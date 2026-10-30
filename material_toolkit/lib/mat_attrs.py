# -*- coding: utf-8 -*-
"""材质属性应用：换母材质 / 改 flags / 回写参数值 → 产出一份新的 mdf2 文件。

## 为什么需要独立模块

这三件事**必须一起做**，因为都要经过 `Mdf2.save()`（重排偏移 + 重排字符串池）：

- 换母材质：改 `mat.mmtr_path` + 按新 mmtr 的 `UserMaterial` 对齐参数集
- 改 flags：改材质记录里的 32 位渲染开关
- 回写参数：把内存里调好的值写进文件（否则重载后会丢）

分开做会产出多个中间文件，且每次都要重载一次（引擎按路径缓存，见 §13.4）。
合并后是「一次 save + 一次 apply」。

## 已验证的前提（见 analysis/runtime_preview_plan.md）

- §22：`save()` 能安全做「参数集结构性变更」（10 参 → 2 参、block 76 → 8）
- §21.6：改 flags + 换新路径重载 → 引擎认（影子消失、材质正常）
- §21.4：新增参数的默认值保持 0（不引入 default 机制）

## 注意

- **只同步目标材质**：其它槽用的是别的 mmtr，不能被本次目标 mmtr 的参数表重写
  （`sync_mdf2(..., materials=...)`）
- **产出新路径**：引擎按路径缓存资源，同路径改了内容不会重载
- 本模块只做文件操作；`apply` 到 mesh、更新 sessions.json、删旧文件由调用方负责
"""
from __future__ import annotations

import os

from . import mdf2 as mdf2lib
from . import mmatlib
from . import psession as ps
from .sync import sync_mdf2


class AttrError(ValueError):
    """材质属性应用失败（材质不存在 / mmtr 未登记 / 值个数与类型不符）。"""


def find_mmtr_item(game_root, logic_path):
    """按 mmtr **逻辑路径**（mdf2 里存的形式，如 `materialTemp/mmtr/X_p1.mmtr`）找登记项。"""
    low = ps.norm_rel(logic_path or "").lower()
    if not low:
        return None
    doc = mmatlib.load(game_root)
    for it in (doc.get("items") or {}).values():
        if ps.norm_rel(it.get("rel") or "").lower() == low:
            return it
    return None


def list_mmtr_options(game_root):
    """可选的母材质清单（只来自母材质库登记项，不碰游戏原版）。

    返回 [{assetName, id, rel, bytes, createdAt, shading, nParams}...]，按 assetName 分组倒序展平。
    """
    out = []
    for name, lst in sorted(mmatlib.items_of(game_root).items()):
        for it in lst:
            um = it.get("userMaterial") or []
            out.append({
                "assetName": name,
                "id": it.get("id"),
                "rel": ps.norm_rel(it.get("rel") or ""),
                "bytes": it.get("bytes"),
                "createdAt": it.get("createdAt"),
                "shading": it.get("shading"),
                "nParams": len(um),
            })
    return out


def _apply_one(mdf2, mat, ch, game_root, report):
    """对单个材质应用一组改动。返回是否有实际变更。"""
    changed = False
    name = mat.name

    # ① 换母材质：改引用 + 按新 mmtr 对齐参数集
    target = (ch.get("mmtr") or "").strip()
    if target:
        cur = ps.norm_rel(mat.mmtr_path or "")
        if target.lower() != cur.lower():
            item = find_mmtr_item(game_root, target)
            if item is None:
                raise AttrError(
                    "母材质未登记：%s\n\n只能选用「编译到预览目录」产出的 mmtr"
                    "（登记在 materialTemp/materials.json）。" % target)
            data = mmatlib.mmtr_from_item(game_root, item)
            if not data:
                raise AttrError("母材质文件不存在：%s" % target)
            from .mmtr import Mmtr

            before = [p.name for p in mat.properties]
            mat.mmtr_path = target
            # prune=True：新母材质没有的参数丢弃（UE 语义：换母材质后多余参数不保留）
            sync_mdf2(mdf2, Mmtr.from_bytes(data), prune=True, materials=name)
            after = [p.name for p in mat.properties]
            report.append("%s: 母材质 → %s（参数 %d → %d）"
                          % (name, os.path.basename(target), len(before), len(after)))
            changed = True

    # ② 覆盖参数值（当前内存里的用户改动）
    for pname, vals in (ch.get("params") or {}).items():
        pr = mat.get_parameter(pname)
        if pr is None:
            # 新母材质里没有这个参数 —— 说明换母材质后被 prune 掉了，正常丢弃
            report.append("%s: 参数 %s 已不在新母材质中，丢弃" % (name, pname))
            continue
        vals = [float(v) for v in (vals or [])]
        if len(vals) != len(pr.values):
            # 类型（分量数）变了 —— 由母材质决定，不能用旧值硬填
            report.append("%s: 参数 %s 类型由 %s 变为 %s，沿用新默认值"
                          % (name, pname, len(pr.values), len(vals)))
            continue
        if [float(x) for x in pr.values] != vals:
            pr.values = vals
            changed = True

    # ③ flags 位
    fl = ch.get("flags") or {}
    if fl:
        d = mat.flags_dict()
        hit = {k: bool(v) for k, v in fl.items() if k in d}
        bad = [k for k in fl if k not in d]
        if bad:
            raise AttrError("未知的 flag：%s" % ", ".join(sorted(bad)))
        if any(d[k] != v for k, v in hit.items()):
            d.update(hit)
            mat.set_flags_dict(d)
            report.append("%s: flags → 0x%08X" % (name, mat.flags & 0xFFFFFFFF))
            changed = True

    return changed


def apply_attrs(game_root, edit_rel, changes, log=None):
    """把一组材质属性改动应用到 edit 副本，产出**新路径**的文件。

    changes 形如::

        {"Leg": {"mmtr": "materialTemp/mmtr/Toon_V5_p20261031_010203.mmtr",
                 "params": {"AlphaValue": [1.0], "MatCapColor": [1.0, 1.0, 1.0]},
                 "flags":  {"ShadowCastDisable": True}},
         "Body": {"flags": {"TwoSideEnable": False}}}

    返回值::

        {"rel": 新路径(不带 .10), "old": 旧路径, "bytes": n, "report": [文案...]}

    失败抛 `AttrError`；此时**不会**写出任何文件（save 前全部校验完）。
    """
    log = log or (lambda *_a, **_k: None)
    old_rel = ps.norm_rel(edit_rel)
    src = ps.rel_to_abs(game_root, old_rel)
    if not os.path.exists(src):
        raise AttrError("工作副本不存在：%s" % old_rel)

    parsed = ps.parse_temp_rel(old_rel)
    if not parsed:
        raise AttrError("无法从路径反推身份（不是本模块生成的 edit 副本）：%s" % old_rel)

    d = mdf2lib.Mdf2.load(src)
    report = []
    touched = []
    for name, ch in (changes or {}).items():
        mat = d.get_material(name)
        if mat is None:
            raise AttrError("材质不存在：%s" % name)
        if _apply_one(d, mat, ch, game_root, report):
            touched.append(name)

    if not touched:
        log("没有任何实际变更（值都没变）")
        return {"rel": old_rel, "old": old_rel, "bytes": 0, "report": report, "changed": False}

    # 新路径：同一 (mesh, mdf2) 链换新时间戳（引擎按路径缓存，必须换）
    # game_root 用于避重：同一秒内连续产出时会自动追加 _1 / _2…
    new_rel = ps.new_edit_rel(parsed["mh"], parsed["dh"], game_root=game_root)
    dst = ps.rel_to_abs(game_root, new_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    n = d.save(dst)

    # 校验：重新 load，确认材质数 / 目标材质参数 / flags 都写对了
    try:
        v = mdf2lib.Mdf2.load(dst)
    except Exception as e:  # noqa: BLE001
        os.remove(dst)
        raise AttrError("产出文件无法重新解析，已删除：%s" % e)
    if len(v.materials) != len(d.materials):
        os.remove(dst)
        raise AttrError("产出文件材质数不一致（%d vs %d），已删除"
                        % (len(v.materials), len(d.materials)))
    for name in touched:
        a, b = d.get_material(name), v.get_material(name)
        if [p.name for p in a.properties] != [p.name for p in b.properties]:
            os.remove(dst)
            raise AttrError("产出文件 %s 的参数表与内存不一致，已删除" % name)
        if [round(float(x), 6) for p in a.properties for x in p.values] != \
           [round(float(x), 6) for p in b.properties for x in p.values]:
            os.remove(dst)
            raise AttrError("产出文件 %s 的参数值写入不一致，已删除" % name)
        if (a.flags & 0xFFFFFFFF) != (b.flags & 0xFFFFFFFF):
            os.remove(dst)
            raise AttrError("产出文件 %s 的 flags 写入不一致，已删除" % name)

    log("产出 %s（%d B，改动材质：%s）" % (new_rel, n, ", ".join(touched)))
    return {"rel": new_rel, "old": old_rel, "bytes": n, "report": report,
            "changed": True, "touched": touched}


def write_params(game_root, edit_rel, changes, log=None):
    """只把参数值写回（不换母材质、不动 flags），产出新路径。

    changes = {"Leg": {"MatCapColor": [5.0, 0.0, 0.0]}, ...}
    等价于 `apply_attrs` 只传 params，单独开一个入口是为了让调用方语义清楚。
    """
    wrapped = {m: {"params": p} for m, p in (changes or {}).items()}
    return apply_attrs(game_root, edit_rel, wrapped, log=log)
