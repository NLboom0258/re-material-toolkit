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


def find_mmtr_item(game_root, target):
    """按材质名 / mmtr 逻辑路径找登记项。

    路径命中不了时再按“产物名解析”找：旧 rev 路径（不在登记簿里）也能映射到
    该材质当前那一份（`mmatlib.resolve_material`）。
    """
    if not target:
        return None
    low = ps.norm_rel(target or "").lower()
    doc = mmatlib.load(game_root)
    for it in (doc.get("materials") or {}).values():
        if ps.norm_rel(it.get("rel") or "").lower() == low:
            return it
        if (it.get("name") or "").lower() == low:
            return it
    _name, it = mmatlib.resolve_material(game_root, target)
    return it


def list_mmtr_options(game_root):
    """可选的母材质清单（工具编译的材质，每个名字一项）。

    返回 [{name, rel, bytes, createdAt, rev, shading, nParams}...]，按名字排序。
    """
    out = []
    for it in mmatlib.list_materials(game_root):
        um = it.get("userMaterial") or []
        out.append({
            "name": it.get("name") or "?",
            "rel": ps.norm_rel(it.get("rel") or ""),
            "bytes": it.get("bytes"),
            "createdAt": it.get("createdAt"),
            "rev": it.get("rev"),
            "shading": it.get("shading") or {},
            "nParams": len(um),
        })
    return out


def _original_mdf2_path(game_root, mh, dh):
    """原始 mdf2 的可用磁盘路径（orig 副本优先，其次 originalMdf2/baseMdf2）。"""
    doc = ps.load_index(game_root)
    e = ps.get_mdf2(doc, mh, dh) or {}
    rel = ps.norm_rel(e.get("orig") or "")
    if rel:
        p = ps.rel_to_abs(game_root, rel)
        if os.path.exists(p):
            return p
    base = ps.norm_rel(e.get("originalMdf2") or e.get("baseMdf2") or "")
    if not base:
        return None
    try:
        return ps.ensure_mdf2(game_root, base)
    except Exception:  # noqa: BLE001 —— pak 里也没有时只能放弃
        return None


def original_material(game_root, mh, dh, mat_name):
    """原始 mdf2 里的同名材质（读不到返回 None）。"""
    p = _original_mdf2_path(game_root, mh, dh)
    if not p:
        return None
    try:
        m = mdf2lib.Mdf2.load(p)
    except Exception:  # noqa: BLE001
        return None
    return m.get_material(mat_name)


def original_mmtr(game_root, mh, dh, mat_name):
    """原始 mdf2 里该材质指向的母材质逻辑路径（原始母材质）。"""
    mat = original_material(game_root, mh, dh, mat_name)
    return ps.norm_rel(mat.mmtr_path or "") if mat is not None else ""


def original_textures(game_root, mh, dh):
    """原始 mdf2 **所有材质**用到的贴图路径（去重、排序）。

    mdf2 自带贴图路径，所以不需要 pak 文件表就能列出“原本可用的贴图”；
    读不到原 mdf2（磁盘/pak 都没有）时返回空列表。
    """
    p = _original_mdf2_path(game_root, mh, dh)
    if not p:
        return []
    try:
        doc = mdf2lib.Mdf2.load(p)
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for mat in doc.materials:
        for tb in mat.textures:
            s = ps.norm_rel(tb.texture_path or "")
            if not s:
                continue
            k = s.lower()
            if k not in seen:
                seen.add(k)
                out.append(s)
    return sorted(out, key=str.lower)


def _clone_props(props):
    """深拷贝参数表（只带名字/值；偏移与 hash 由 save 重算）。"""
    out = []
    for p in props:
        q = mdf2lib.Property()
        q.name = p.name
        q.values = list(p.values)
        q.param_count = len(q.values)
        out.append(q)
    return out


def _apply_one(mdf2, mat, ch, game_root, report, mh=None, dh=None):
    """对单个材质应用一组改动。返回是否有实际变更。"""
    changed = False
    name = mat.name

    # ① 换母材质
    target = (ch.get("mmtr") or "").strip()
    if target:
        cur = ps.norm_rel(mat.mmtr_path or "")
        if target == "__orig__":
            # 恢复原始母材质 + 原始参数集（原始 mdf2 是权威，不需要读游戏原版 mmtr）
            src = original_material(game_root, mh, dh, name)
            if src is None:
                raise AttrError("读不到原始 mdf2 里的材质 %s（原文件可能已不在）" % name)
            src_path = ps.norm_rel(src.mmtr_path or "")
            if (src_path.lower() != cur.lower()
                    or [p.name for p in src.properties] != [p.name for p in mat.properties]):
                mat.mmtr_path = src.mmtr_path
                mat.properties = _clone_props(src.properties)
                report.append("%s: 母材质 → 原始（%s，%d 参数）"
                              % (name, src_path or "(空)", len(mat.properties)))
                changed = True
        elif target.lower() != cur.lower():
            item = find_mmtr_item(game_root, target)
            if item is None:
                raise AttrError(
                    "母材质未登记：%s\n\n只能选用「编译到预览目录」产出的材质"
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
                          % (name, item.get("name") or os.path.basename(target),
                             len(before), len(after)))
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

    # ④ 贴图槽路径（只改 path，不改 type；type 需与 mmtr 的 SRV 名一致）
    for ttype, tpath in (ch.get("textures") or {}).items():
        tb = mat.get_texture(ttype)
        if tb is None:
            report.append("%s: 贴图槽 %s 不存在，丢弃" % (name, ttype))
            continue
        tpath = (tpath or "").strip()
        if tpath and tpath != tb.texture_path:
            tb.texture_path = tpath
            changed = True
            report.append("%s: 贴图 %s → %s" % (name, ttype, tpath))

    return changed


def apply_attrs(game_root, edit_rel, changes, log=None):
    """把一组材质属性改动应用到 edit 副本，产出**新路径**的文件。

    changes 形如::

        {"Leg": {"mmtr": "materialTemp/mmtr/Toon_V5_p20261031_010203.mmtr",
                 "params": {"AlphaValue": [1.0], "MatCapColor": [1.0, 1.0, 1.0]},
                 "textures": {"BaseColorMap": "materialTemp/tex/.../x.tex"},
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
        if _apply_one(d, mat, ch, game_root, report,
                      mh=parsed.get("mh"), dh=parsed.get("dh")):
            touched.append(name)

    if not touched:
        log("没有任何实际变更（值都没变）")
        return {"rel": old_rel, "old": old_rel, "bytes": 0, "report": report, "changed": False}

    # 新路径：同一副本 rev+1（引擎按路径缓存，必须换）
    new = ps.bump_copy_rel(game_root, old_rel)
    new_rel = new["rel"]
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
        if [(t.texture_type, t.texture_path) for t in a.textures] != \
           [(t.texture_type, t.texture_path) for t in b.textures]:
            os.remove(dst)
            raise AttrError("产出文件 %s 的贴图槽写入不一致，已删除" % name)

    log("产出 %s（%s v%d，%d B，改动材质：%s）"
        % (new_rel, new["name"], new["rev"], n, ", ".join(touched)))
    return {"rel": new_rel, "old": old_rel, "bytes": n, "report": report,
            "changed": True, "touched": touched,
            "name": new["name"], "rev": new["rev"],
            "mh": new["mh"], "dh": new["dh"]}


def repoint_edits(game_root, old_mmtr, new_mmtr, new_bytes, log=None,
                  material_name="", extra_by_rel=None):
    """把所有副本里引用旧母材质的材质改到 new_mmtr（编译后自动更新）。

    匹配旧引用的两种方式：
      - 路径 == ``old_mmtr``（上一版登记项）
      - 给了 ``material_name`` 时：路径在缓存目录且按产物名解析出同一个名字
        （覆盖更旧的 rev）

    遍历的是 ``sessions.json`` 里登记的**全部具名副本**（不只是最近使用的）。
    每个受影响的副本会：
      1. 把该材质的 ``mmtr_path`` 改成新路径
      2. 按新 mmtr 的 UserMaterial 重建参数集（同名保留值、新增 0、旧删）
      3. 存成**同一副本的 rev+1 路径**（引擎按路径缓存）

    ``extra_by_rel`` = ``{副本 rel(lower): {材质: {params, textures}}}``：
    副本上正在预览的**未保存**参数/贴图改动，按名字合并到新文件（同贴图联动）。

    返回 ``[{mh, dh, name, old, new, rev, materials}]``。
    只改文件；apply 到游戏、更新 sessions.json、删旧文件由调用方负责。
    """
    log = log or (lambda *_a, **_k: None)
    from .mmtr import Mmtr

    old_low = ps.norm_rel(old_mmtr or "").lower()
    name_low = (material_name or "").lower()
    prefix = (ps.TEMP_DIR + "/" + mmatlib.MMTR_DIR + "/").lower()
    if not old_low and not name_low:
        return []
    extra_by_rel = extra_by_rel or {}
    doc = ps.load_index(game_root)
    out = []
    seen = set()
    for mh, o in (doc.get("objects") or {}).items():
        for dh in (o.get("mdfs") or {}):
            for c in ps.list_copies(doc, mh, dh):
                rel = c["rel"]
                if rel.lower() in seen:      # 同一文件被多条登记引用时只处理一次
                    continue
                seen.add(rel.lower())
                src = ps.rel_to_abs(game_root, rel)
                if not os.path.exists(src):
                    continue
                try:
                    d = mdf2lib.Mdf2.load(src)
                except Exception:  # noqa: BLE001
                    continue
                names = []
                for m in d.materials:
                    mp = ps.norm_rel(m.mmtr_path or "").lower()
                    if old_low and mp == old_low:
                        names.append(m.name)
                    elif name_low and mp.startswith(prefix) \
                            and mmatlib.parse_artifact_name(mp).lower() == name_low:
                        names.append(m.name)
                if not names:
                    continue
                mm = Mmtr.from_bytes(new_bytes)
                for name in names:
                    d.get_material(name).mmtr_path = new_mmtr
                    sync_mdf2(d, mm, prune=True, materials=name)
                # 合并该副本上未保存的参数/贴图改动（同名同类型才保留；
                # 新母材质裁掉的参数/改类型的参数直接丢弃）。
                for ename, ech in (extra_by_rel.get(rel.lower()) or {}).items():
                    em = d.get_material(ename)
                    if em is None:
                        continue
                    for pname, vals in (ech.get("params") or {}).items():
                        pr = em.get_parameter(pname)
                        if pr is None:
                            continue
                        vals = [float(x) for x in (vals or [])]
                        if len(vals) != len(pr.values):
                            continue
                        pr.values = vals
                    for ttype, tpath in (ech.get("textures") or {}).items():
                        tb = em.get_texture(ttype)
                        if tb is not None and tpath:
                            tb.texture_path = tpath
                new = ps.bump_copy_rel(game_root, rel)
                dst = ps.rel_to_abs(game_root, new["rel"])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                d.save(dst)
                out.append({"mh": mh, "dh": dh, "name": c["name"],
                            "old": rel, "new": new["rel"], "rev": new["rev"],
                            "materials": names})
                log("自动更新副本: %s → %s（%s）"
                    % (rel, new["rel"], ", ".join(names)))
    return out
