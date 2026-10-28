# -*- coding: utf-8 -*-
"""材质运行时预览部署（对应 analysis/runtime_preview_plan.md §6）。

实测结论：
- 同路径 `sdk.create_resource` 恒返回**缓存对象**，改磁盘内容无效
- **全新路径**可以加载（0.x 秒同步卡顿 + 一瞬"缺材质默认方格"）
⇒ 每次预览都生成一对**全新文件名**的 mmtr + mdf2，由 REF 脚本按索引文件加载。

产物全部写到**游戏根**的 natives（不要写 mod 目录 —— Fluffy 的 mod 目录与根是
硬链接，覆盖写会断链，游戏仍读根目录那份），并且集中在**一个专用目录**里，
方便清理、也不会和游戏/其它 mod 的文件混在一起：

    <root>/natives/x64/materialTemp/index.txt           ← REF 脚本读它
    <root>/natives/x64/materialTemp/<name>_p<ts>.mmtr.1808168797
    <root>/natives/x64/materialTemp/<base>_p<ts>.mdf2.10

索引为极简 `key=value` 文本（Lua 侧逐行 split，不依赖 JSON 库）：

    ver=20261029_154530
    mdf=materialTemp/pl0800_p20261029_154530.mdf2
    match=character/player/pl0800_vergilpl/pl0800_body/pl0800
    mmtr=materialTemp/Toon_V5_p20261029_154530.mmtr
"""
from __future__ import annotations

import os
import posixpath
import re
import time

from . import mdf2 as MDF
from . import material_instance as minst
from . import mmtr_nogen as nogen

TEMP_DIR = "materialTemp"       # natives/x64 下的专用目录
IDX_NAME = "index.txt"
MMTR_EXT = ".1808168797"
MDF2_EXT = ".10"
_TS_RE = re.compile(r"_p(\d{8}_\d{6})")


def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def _pass_of_template(tmpl):
    return "Forward" if (tmpl or "").lower().startswith("forward") else "Deferred"


def _vo_kind(mode):
    return "full" if (mode or "").lower() == "custom" else "offset"


def norm_rel(p):
    """mdf2 路径 → 相对 natives/x64、正斜杠、不带版本后缀。"""
    p = (p or "").replace("\\", "/").strip("/")
    if p.lower().endswith(MDF2_EXT):
        p = p[: -len(MDF2_EXT)]
    return p


def pick_materials(mdf2, wanted=None):
    """决定重指向哪些材质槽。

    wanted 为空时取 mdf2 里 **mmtr_path 出现次数最多** 的那一组 —— 主体材质通常
    占多数（如 pl0800.mdf2 里 Acc/Body/Brow/Brow1/Cloth 共用 ToonTest，而眼睛那
    组共用 BaseUnlitTest）。
    """
    if wanted:
        return list(wanted)
    from collections import Counter
    c = Counter((getattr(m, "mmtr_path", "") or "") for m in mdf2.materials)
    if not c:
        return []
    target, _ = c.most_common(1)[0]
    return [m.name for m in mdf2.materials
            if (getattr(m, "mmtr_path", "") or "") == target]


def _prune(tmp, keep):
    """只保留最近 keep 组预览文件。

    按文件名里的 `_p<YYYYMMDD_HHMMSS>` 分组，而不是根据 mdf2 去反推 mmtr ——
    后者依赖 glob 拼模式，实测会漏删 mmtr。
    """
    if keep <= 0:
        return 0
    groups = {}
    for name in os.listdir(tmp):
        m = _TS_RE.search(name)
        if m:
            groups.setdefault(m.group(1), []).append(name)
    remove = []
    for ts in sorted(groups)[:-keep]:
        remove.extend(groups[ts])
    removed = 0
    for name in remove:
        try:
            os.remove(os.path.join(tmp, name))
            removed += 1
        except OSError:
            pass
    return removed


def deploy(asset, game_root, base_mdf2, materials=None, keep=20, sync=False,
           log=print):
    """生成一对新的 mmtr + mdf2 写进游戏根 natives，并更新索引。

    asset     : MaterialAsset（材质资产）
    game_root : 游戏根目录（含 natives 的那层）
    base_mdf2 : 参照的 mdf2，相对 natives/x64（如 character/.../pl0800.mdf2）
    materials : 要重指向的材质槽名；None = 自动取占比最多的那组
    keep      : 保留最近多少组预览文件（0 = 不清理）
    sync      : 是否按新 mmtr 重新同步 mdf2 的参数定义（默认关，保留 base 的参数值）
    """
    root = os.path.abspath(game_root)
    nat = os.path.join(root, "natives", "x64")
    if not os.path.isdir(nat):
        raise RuntimeError("找不到 natives/x64：%s" % nat)

    rel = norm_rel(base_mdf2)
    base_path = os.path.join(nat, rel + MDF2_EXT)
    if not os.path.exists(base_path):
        raise RuntimeError("base mdf2 不存在：%s" % base_path)

    d, stem = posixpath.split(rel)
    base_stem = stem[:-5] if stem.lower().endswith(".mdf2") else stem

    tmp = os.path.join(nat, TEMP_DIR)
    os.makedirs(tmp, exist_ok=True)

    ts = _stamp()
    name = (getattr(asset, "name", "") or "material").replace(" ", "_")
    mmtr_rel = "%s/%s_p%s.mmtr" % (TEMP_DIR, name, ts)
    mdf2_rel = "%s/%s_p%s.mdf2" % (TEMP_DIR, base_stem, ts)

    # 1) mmtr
    tmpl = asset.template.get("pass_template") or "deferred_std"
    data, rep = nogen.build(
        asset.shading_source or "", _pass_of_template(tmpl), tmpl,
        inputs=asset.inputs, extra=asset.shading_extra,
        vo_kind=_vo_kind(asset.lighting_mode))
    failed = rep.get("vs_gen_failed") or 0
    if failed:
        log("  [warn] VS 生成失败 %d 个（已回退银行）" % failed)
    mmtr_file = os.path.join(tmp, "%s_p%s.mmtr%s" % (name, ts, MMTR_EXT))
    with open(mmtr_file, "wb") as f:
        f.write(data)

    # 2) mdf2：克隆参照，把目标材质重指向新 mmtr
    mf = MDF.Mdf2.load(base_path)
    mats = pick_materials(mf, materials)
    if not mats:
        raise RuntimeError("base mdf2 里没有可重指向的材质")
    for mn in mats:
        minst.repoint(mf, mn, mmtr_rel, data if sync else None, sync=sync)
    mdf2_file = os.path.join(tmp, "%s_p%s.mdf2%s" % (base_stem, ts, MDF2_EXT))
    nbytes = mf.save(mdf2_file)

    # 3) 索引（Lua 侧读它决定加载哪组）
    #    REF 的 Lua io 是沙盒的（基准是 reframework/data，natives 要用 $natives/ 前缀，
    #    且禁止 '..'），所以同时写两份索引，脚本会逐个探测。
    #
    #    match 必须覆盖**两种状态**：
    #      - 还没预览过：mesh 的材质是原 mdf2（character/.../pl0800.mdf2）
    #      - 已经套上预览：材质变成 materialTemp/pl0800_pXXX.mdf2
    #    否则第二次应用就会 hits=0（实测踩过）。多个前缀用 '|' 分隔。
    match = "%s|%s/%s" % (((d + "/") if d else "") + base_stem, TEMP_DIR, base_stem)
    body = "ver=%s\nmdf=%s\nmatch=%s\nmmtr=%s\n" % (ts, mdf2_rel, match, mmtr_rel)
    idx_files = [os.path.join(tmp, IDX_NAME)]
    data_dir = os.path.join(root, "reframework", "data", TEMP_DIR)
    try:
        os.makedirs(data_dir, exist_ok=True)
        idx_files.append(os.path.join(data_dir, IDX_NAME))
    except OSError:
        pass
    for p in idx_files:
        try:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
        except OSError as e:
            log("  [warn] 索引写入失败 %s: %s" % (p, e))
    idx_file = idx_files[0]

    # 4) 清理旧预览
    removed = _prune(tmp, keep)

    log("OK  preview  ver=%s" % ts)
    log("    mmtr : %s  (%d B)" % (mmtr_rel, len(data)))
    log("    mdf2 : %s  (%d B)" % (mdf2_rel, nbytes))
    log("    材质 : %s" % ", ".join(mats))
    for p in idx_files:
        log("    索引 : %s" % p)
    if removed:
        log("    清理 : 删除 %d 个旧预览文件（保留最近 %d 组）" % (removed, keep))
    return {"ver": ts, "mmtr": mmtr_rel, "mdf2": mdf2_rel,
            "materials": mats, "index": idx_file}
