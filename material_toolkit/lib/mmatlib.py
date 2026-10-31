# -*- coding: utf-8 -*-
"""母材质库：按**材质名**管理工具编译出来的 mmtr 产物。

## 为什么按名字管理（而不是按产物文件）

工具里的处理单元是**材质**（用户在材质系统页编辑的那个资产），mmtr 只是它的
编译产物。同一个材质改一版再编译，应该**更新这个材质**，而不是多出一条新记录；
否则「换母材质」的下拉里会出现同一个材质的好几份。

所以登记簿的 key 是材质名，每个材质只保留**当前一份**产物：

    materials.json
    {
      "version": 2,
      "mmtrVer": ".1808168797",
      "materials": {
        "Toon_V5": {
          "name": "Toon_V5",
          "rev": 3,                                  ← 第几次编译
          "rel": "materialTemp/mmtr/Toon_V5_r3.mmtr", ← 当前产物（逻辑路径）
          "assetPath": "...", "bytes": ..., "createdAt": ...,
          "sourceHash": "...", "shading": {...},
          "userMaterial": [...], "textureSlots": [...]
        }
      }
    }

## 文件名仍然带 rev

引擎按路径缓存资源：同路径换了内容不会重载（mdf2 已踩过，mmtr 同理）。
所以每次编译都写成新路径 ``<name>_r<rev>.mmtr.1808168797``，rev 递增；
旧文件如果**没有被任何工作副本引用**，由 ``prune`` 删掉。

写进 mdf2 的 ``mmtr_path`` 是逻辑路径（不带 ``natives/x64/`` 与版本后缀）。
"""
from __future__ import annotations

import json
import os
import re

from . import mdf2 as mdf2lib
from . import psession as ps

MMTR_VER = ".1808168797"
LIB = "materials.json"
MMTR_DIR = "mmtr"                     # 位于 materialTemp 下
LIB_VERSION = 2

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 旧版的产物 stem：`<name>_p<YYYYmmdd_HHMMSS>` 可选跟 `_<序号>`（仅迁移时用）
_RE_OLD_STEM = re.compile(r"_p(\d{8}_\d{6})(?:_(\d+))?$")


def safe_name(name, fallback="Material"):
    """把材质名规范成可用作文件名的形式（去掉路径分隔符与非法字符）。"""
    s = (name or "").strip() or fallback
    s = _BAD_CHARS.sub("_", s).strip(" .")
    return s or fallback


def now_iso():
    """登记项的 createdAt（仅用于显示）。"""
    import time
    t = time.time()
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
            + ".%03d" % int((t % 1) * 1000))


def lib_path(game_root):
    return os.path.join(ps.temp_root(game_root), LIB)


def mmtr_dir_abs(game_root):
    return os.path.join(ps.temp_root(game_root), MMTR_DIR)


def mmtr_abs(game_root, rel):
    """逻辑路径 ``materialTemp/mmtr/X.mmtr`` → 磁盘绝对路径（补版本后缀）。"""
    s = ps.norm_rel(rel)
    if not s.lower().endswith(MMTR_VER):
        s += MMTR_VER
    if s.lower().startswith(ps.TEMP_DIR.lower() + "/"):
        s = s[len(ps.TEMP_DIR) + 1:]
    return os.path.join(ps.temp_root(game_root), s.replace("/", os.sep))


def is_artifact_path(rel):
    """是不是我们缓存目录里的 mmtr 产物（``materialTemp/mmtr/``）。"""
    s = ps.norm_rel(rel or "")
    return s.lower().startswith((ps.TEMP_DIR + "/" + MMTR_DIR + "/").lower())


def artifact_stem(rel):
    """``materialTemp/mmtr/Toon_V5_r3.mmtr`` → ``Toon_V5_r3``。"""
    name = ps.norm_rel(rel or "").rsplit("/", 1)[-1]
    low = name.lower()
    if low.endswith(MMTR_VER):
        name = name[:-len(MMTR_VER)]
        low = name.lower()
    if low.endswith(".mmtr"):
        name = name[:-5]
    return name


_SUFFIX_RES = (
    re.compile(r"_r\d+$"),                       # 新：<name>_r<rev>
    re.compile(r"_p\d{8}_\d{6}(?:_\d+)?$"),      # 旧：<name>_p<ts>[_N]
)


def parse_artifact_name(rel):
    """从产物路径解析材质名（只解析字符串，不查登记）。

    后缀格式固定且**一定在末尾**，所以从后往前按顺序剥：
      ``<name>_r<rev>`` / ``<name>_p<YYYYmmdd_HHMMSS>[_N]``

    不符合任何已知后缀 → 返回 ``""``（不是我们标准流程的产物；使用它本身有风险，
    调用方应按未登记处理）。材质名本身可能以这些形式结尾 —— 所以调用方应
    **先用完整 stem 查一次登记**，再退回这个解析结果（见 ``resolve_material``）。
    """
    stem = artifact_stem(rel)
    for rx in _SUFFIX_RES:
        m = rx.search(stem)
        if m:
            return stem[:m.start()]
    return ""


def resolve_material(game_root, mmtr_path):
    """缓存目录里的 mmtr 路径 → ``(材质名, 登记项 | None)``。

    命中规则：
      1. 只处理 ``materialTemp/mmtr/`` 下的路径；游戏原版路径返回 ``("", None)``
      2. 先用**完整 stem** 查（材质名本身可能带 ``_r123``）
      3. 再按 ``_r<rev>`` / ``_p<时间戳>`` 剥后缀查
      4. 都查不到 → 返回解析出的名字 + None（调用方降级成“未登记”/提示）
    """
    s = ps.norm_rel(mmtr_path or "")
    if not is_artifact_path(s):
        return "", None
    doc = load(game_root)
    mats = doc.get("materials") or {}
    for cand in (artifact_stem(s), parse_artifact_name(s)):
        it = mats.get(cand)
        if it and os.path.exists(mmtr_abs(game_root, it.get("rel") or "")):
            return cand, it
    return parse_artifact_name(s), None


def _old_seq(rel):
    """旧版 ``name_p20261030_081053`` → ``(时间戳, 序号)``（迁移排序用）。"""
    stem = ps.norm_rel(rel or "").rsplit("/", 1)[-1]
    if stem.lower().endswith(".mmtr"):
        stem = stem[:-5]
    m = _RE_OLD_STEM.search(stem)
    return (m.group(1), int(m.group(2) or 0)) if m else ("", 0)


def _migrate_v1(doc):
    """旧版 ``{items, active}`` → 新版 ``{materials}``。

    每个材质名只保留一份：优先 ``active``，否则取文件名时间戳最新的一份。
    旧文件不在这里删；下次 ``prune`` 时按“未登记且未被引用”清掉。
    """
    groups = {}
    for it in (doc.get("items") or {}).values():
        groups.setdefault(it.get("assetName") or "?", []).append(it)
    mats = {}
    active = doc.get("active") or {}
    for name, lst in groups.items():
        pick = None
        aid = active.get(name)
        if aid:
            pick = next((x for x in lst if x.get("id") == aid), None)
        if pick is None:
            lst.sort(key=lambda x: _old_seq(x.get("rel")), reverse=True)
            pick = lst[0]
        mats[name] = {
            "name": name,
            "rev": 0,                       # 下一次编译从 1 开始
            "rel": ps.norm_rel(pick.get("rel") or ""),
            "assetPath": pick.get("assetPath") or "",
            "bytes": pick.get("bytes"),
            "createdAt": pick.get("createdAt") or now_iso(),
            "sourceHash": pick.get("sourceHash") or "",
            "shading": dict(pick.get("shading") or {}),
            "userMaterial": list(pick.get("userMaterial") or []),
            "textureSlots": list(pick.get("textureSlots") or []),
        }
    return {"version": LIB_VERSION, "mmtrVer": doc.get("mmtrVer") or MMTR_VER,
            "materials": mats}


# ---------------------------------------------------------------- 读写登记簿

def load(game_root):
    p = lib_path(game_root)
    doc = None
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = None
    if not isinstance(doc, dict):
        doc = {}
    if (doc.get("version") or 1) < 2 or "materials" not in doc:
        doc = _migrate_v1(doc)
    doc.setdefault("materials", {})
    doc["version"] = LIB_VERSION
    doc.setdefault("mmtrVer", MMTR_VER)
    return doc


def save(game_root, doc, log=None):
    p = lib_path(game_root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    if log:
        log("登记簿已更新: %s（%d 个材质）" % (p, len(doc.get("materials") or {})))


# ---------------------------------------------------------------- 解析 mmtr

def mmtr_profile(data: bytes):
    """从 mmtr 字节里取 UserMaterial 成员 + 材质贴图槽（登记用快照）。"""
    from .mmtr import Mmtr
    from .material_instance import material_texture_slots
    m = Mmtr.from_bytes(data)
    members = m.cbuffer_members("UserMaterial") or []
    prof = [{"name": x.name, "offset": x.offset, "size": x.size} for x in members]
    try:
        slots = material_texture_slots(data)
    except Exception:
        slots = []
    return prof, slots


# ---------------------------------------------------------------- 登记 / 查询

def register(game_root, asset_name, data, asset_path=None, shading=None,
             source_hash=None, log=None):
    """落盘一份 mmtr 并把**这个材质**更新到它。返回登记项 dict。

    - 同名材质再次编译 → ``rev`` 递增，登记簿仍只有一项
    - 旧产物不在这里删（可能还被工作副本引用），交给 ``prune``
    """
    if not data:
        raise ValueError("mmtr 字节为空")
    name = safe_name(asset_name)
    d = mmtr_dir_abs(game_root)
    os.makedirs(d, exist_ok=True)
    doc = load(game_root)
    old = (doc.get("materials") or {}).get(name) or {}
    rev = int(old.get("rev") or 0) + 1
    while True:
        stem = "%s_r%d" % (name, rev)
        if not os.path.exists(os.path.join(d, stem + ".mmtr" + MMTR_VER)):
            break
        rev += 1

    p = os.path.join(d, stem + ".mmtr" + MMTR_VER)
    with open(p, "wb") as f:
        f.write(data)
    rel = ps.norm_rel("%s/%s/%s.mmtr" % (ps.TEMP_DIR, MMTR_DIR, stem))

    prof, slots = mmtr_profile(data)
    item = {
        "name": name,
        "rev": rev,
        "assetPath": asset_path or "",
        "rel": rel,
        "bytes": len(data),
        "createdAt": now_iso(),
        "sourceHash": source_hash or "",
        "shading": dict(shading or {}),
        "userMaterial": prof,
        "textureSlots": slots,
    }
    doc["materials"][name] = item
    save(game_root, doc, log=log)
    if log:
        log("已产出材质 %s（第 %d 版）%s（%d B，参数 %d，贴图槽 %d）"
            % (name, rev, rel, len(data), len(prof), len(slots)))
    return item


def list_materials(game_root, doc=None):
    """所有已登记材质（每个名字一项），按名字排序。"""
    doc = doc or load(game_root)
    return [it for _n, it in sorted((doc.get("materials") or {}).items())]


def get_material(game_root, asset_name, doc=None):
    """按材质名取登记项；文件丢了返回 None（不猜别的版本）。"""
    doc = doc or load(game_root)
    it = (doc.get("materials") or {}).get(safe_name(asset_name))
    if it and os.path.exists(mmtr_abs(game_root, it.get("rel") or "")):
        return it
    return None


def item_by_rel(game_root, logic_path, doc=None):
    """按 mmtr 逻辑路径反查登记项（mdf2 里存的是路径）。"""
    low = ps.norm_rel(logic_path or "").lower()
    if not low:
        return None
    doc = doc or load(game_root)
    for it in (doc.get("materials") or {}).values():
        if ps.norm_rel(it.get("rel") or "").lower() == low:
            return it
    return None


def mmtr_from_item(game_root, item):
    """读登记项对应的 mmtr 字节（不存在返回 None）。"""
    if not item:
        return None
    p = mmtr_abs(game_root, item["rel"])
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- 在用检测 / 清理

def referenced_mmtrs(game_root):
    """扫 sessions.json 里所有 edit/orig 的 mdf2，收集它们引用的 mmtr 逻辑路径。"""
    out = set()
    doc = ps.load_index(game_root)
    for o in (doc.get("objects") or {}).values():
        for e in (o.get("mdfs") or {}).values():
            for kind in ("edit", "orig"):
                rel = ps.norm_rel(e.get(kind) or "")
                if not rel:
                    continue
                p = ps.rel_to_abs(game_root, rel)
                if not os.path.exists(p):
                    continue
                try:
                    m = mdf2lib.Mdf2.load(p)
                except Exception:
                    continue
                for mat in m.materials:
                    if mat.mmtr_path:
                        out.add(ps.norm_rel(mat.mmtr_path).lower())
    return out


def prune(game_root, doc=None, dry_run=True, log=None):
    """删掉**既没登记、也没被任何工作副本引用**的 mmtr 文件。

    登记过的（每个材质当前那一份）一律保留；被 edit/orig mdf2 引用的也保留
    （删了引擎下次重载读不到，材质会变默认方格）。旧版本的产物如果还被引用，
    会一直留着，直到对应的 mdf2 换掉为止。
    """
    doc = doc or load(game_root)
    keep = {mmtr_abs(game_root, ps.norm_rel(it.get("rel") or ""))
            for it in (doc.get("materials") or {}).values() if it.get("rel")}
    live = referenced_mmtrs(game_root)

    removed = []
    d = mmtr_dir_abs(game_root)
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if ".mmtr" not in fn.lower():
                continue
            p = os.path.join(d, fn)
            if p in keep:
                continue
            logical = "%s/%s/%s" % (ps.TEMP_DIR, MMTR_DIR, fn.split(".mmtr")[0] + ".mmtr")
            if logical.lower() in live:
                continue
            if dry_run:
                removed.append(p)
                continue
            try:
                os.remove(p)
                removed.append(p)
            except OSError as e:
                if log:
                    log("删除失败 %s: %s" % (os.path.basename(p), e))
    if log:
        log("%s %d 个旧 mmtr 产物%s" % ("将清理" if dry_run else "已清理",
                                       len(removed), "（dry-run）" if dry_run else ""))
    return removed
