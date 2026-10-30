# -*- coding: utf-8 -*-
"""母材质库：登记「工具编译出来的 mmtr 产物」。

## 为什么需要登记

换母材质时，工具**只允许使用自己编译过的 mmtr** —— 因为需要它的
``UserMaterial`` 成员表（名字 / offset / 大小）来决定 mdf2 该有哪些参数、
值区怎么摆。游戏原版 mmtr 在 pak 里，不列入可用范围。

## 目录与命名

全部在 ``<gameRoot>/natives/x64/materialTemp`` 下（目录由服务 ``/paths`` 给出）::

    materials.json                                  ← 登记簿（本模块）
    mmtr/<name>_p<YYYYmmdd_HHMMSS>.mmtr.1808168797  ← 编译产物

**文件名带时间戳**：引擎按路径缓存资源，同路径改了内容不会重载
（mdf2 那边已经踩过，mmtr 同理）。所以每次编译都产出新路径，旧的在被替换后清理。

写进 mdf2 的 ``mmtr_path`` 是**逻辑路径**（不带 ``natives/x64/`` 与 ``.1808168797``）::

    materialTemp/mmtr/Toon_V5_p20261030_072500.mmtr

## 登记项

```json
{
  "version": 1,
  "mmtrVer": ".1808168797",
  "items": {
    "a1b2c3d4": {
      "id": "a1b2c3d4",
      "assetName": "Toon_V5",
      "assetPath": "assets/ModMaterial/Toon_V5.mmat.json",
      "rel": "materialTemp/mmtr/Toon_V5_p20261030_072500.mmtr",
      "bytes": 654211,
      "createdAt": "2026-10-30T07:25:00",
      "sourceHash": "9f2c...",
      "shading": {"lighting_mode": "default", "shading_type": "deferred"},
      "userMaterial": [{"name": "AlphaValue", "offset": 0, "size": 4}, ...],
      "textureSlots": ["AlbedoMap", ...]
    }
  },
  "active": {"Toon_V5": "a1b2c3d4"}
}
```

``userMaterial`` 是**编译时从 mmtr 里解析出来的快照**，用于 UI 显示和
「登记 vs 实际」一致性检查。真正做参数同步时仍然以解析 mmtr 为准
（见 ``mmtr_from_item``），因为那才是引擎读的那份。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from . import mdf2 as mdf2lib
from . import psession as ps

MMTR_VER = ".1808168797"
LIB = "materials.json"
MMTR_DIR = "mmtr"                     # 位于 materialTemp 下
LIB_VERSION = 1

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 产物 stem 的尾部：`_p<YYYYmmdd_HHMMSS>` 可选跟 `_<序号>`
_RE_STEM = re.compile(r"_p(\d{8}_\d{6})(?:_(\d+))?$")


def h8(text):
    """8 位 hex（sha1 前 4 字节）。与 psession 的 hash 口径一致。"""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]


def safe_name(name, fallback="Material"):
    """把材质名规范成可用作文件名的形式（去掉路径分隔符与非法字符）。"""
    s = (name or "").strip() or fallback
    s = _BAD_CHARS.sub("_", s).strip(" .")
    return s or fallback


def now_iso():
    """登记项的 createdAt（仅用于显示；排序看文件 stem，不看它）。"""
    t = time.time()
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
            + ".%03d" % int((t % 1) * 1000))


def _stem_of(rel):
    """``materialTemp/mmtr/X_p20261030_074342_1.mmtr`` → ``X_p20261030_074342_1``"""
    s = ps.norm_rel(rel or "")
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    if s.lower().endswith(".mmtr"):
        s = s[:-5]
    return s


def _seq_of(rel):
    """stem → ``(时间戳, 序号)``，用于排序与判定“最后一版”。

    序号按**整数**比，所以 ``_10 > _9``；没有序号时当 0。
    """
    m = _RE_STEM.search(_stem_of(rel))
    if not m:
        return ("", 0)
    return (m.group(1), int(m.group(2) or 0))


def _used_stems(game_root, doc):
    """已占用的 stem（磁盘文件名 + 登记簿，小写）。"""
    out = set()
    d = mmtr_dir_abs(game_root)
    if os.path.isdir(d):
        for fn in os.listdir(d):
            low = fn.lower()
            if ".mmtr" in low:
                out.add(low.split(".mmtr")[0])
    for it in (doc.get("items") or {}).values():
        st = _stem_of(it.get("rel"))
        if st:
            out.add(st.lower())
    return out


def unique_stem(game_root, doc, asset_name):
    """生成唯一且**单调递增**的产物 stem（文件名不带扩展名）。

    为什么要索引而不是只有时间戳：引擎按路径缓存，每次编译必须换新路径；
    但同一秒内连续编译两次会撞同一个时间戳，需要靠 ``_1`` / ``_2`` 区分。
    序号用整数比较，所以 ``_10`` 大于 ``_9``（不是字符串比较）。

    **时钟回拨**：若当前时间早于该资产最后一版（NTP 修正 / 手动改时间），
    仍以“当前”为基准会产生一个排在旧版**之前**的名字 —— 那样“最新”就不再是最大值，
    prune / active 会取错。所以此时退回用最后一版的时间戳作基准，再靠序号递增。
    """
    name = safe_name(asset_name)
    used = _used_stems(game_root, doc)
    now = time.strftime("%Y%m%d_%H%M%S")

    last_ts, _last_seq = "", -1
    for it in (doc.get("items") or {}).values():
        if (it.get("assetName") or "") != name:
            continue
        ts, seq = _seq_of(it.get("rel"))
        if (ts, seq) > (last_ts, _last_seq):
            last_ts, _last_seq = ts, seq
    for s in used:                       # 磁盘上的孤儿也要算进去
        if s.startswith(name.lower() + "_p"):
            ts, seq = _seq_of(s + ".mmtr")
            if (ts, seq) > (last_ts, _last_seq):
                last_ts, _last_seq = ts, seq

    ts = max(now, last_ts or now)         # 时钟回拨 → 以最后一版为基准
    base = "%s_p%s" % (name, ts)
    if base.lower() not in used:
        return base
    n = 1
    while ("%s_%d" % (base, n)).lower() in used:
        n += 1
    return "%s_%d" % (base, n)


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
    p = os.path.join(ps.temp_root(game_root), s.replace("/", os.sep))
    return p


# ---------------------------------------------------------------- 读写登记簿

def load(game_root):
    p = lib_path(game_root)
    if not os.path.exists(p):
        return {"version": LIB_VERSION, "mmtrVer": MMTR_VER, "items": {}, "active": {}}
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {"version": LIB_VERSION, "mmtrVer": MMTR_VER, "items": {}, "active": {}}
    doc.setdefault("items", {})
    doc.setdefault("active", {})
    doc.setdefault("mmtrVer", MMTR_VER)
    doc["version"] = LIB_VERSION
    return doc


def save(game_root, doc, log=None):
    p = lib_path(game_root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    if log:
        log("登记簿已更新: %s（%d 项）" % (p, len(doc.get("items") or {})))


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
    """把一份编译好的 mmtr 字节落盘并登记。返回登记项 dict。"""
    if not data:
        raise ValueError("mmtr 字节为空")
    name = safe_name(asset_name)
    d = mmtr_dir_abs(game_root)
    os.makedirs(d, exist_ok=True)
    doc = load(game_root)
    stem = unique_stem(game_root, doc, name)
    fn = stem + ".mmtr" + MMTR_VER
    p = os.path.join(d, fn)
    with open(p, "wb") as f:
        f.write(data)

    rel = ps.norm_rel("%s/%s/%s.mmtr" % (ps.TEMP_DIR, MMTR_DIR, stem))

    prof, slots = mmtr_profile(data)
    item = {
        "id": h8(rel),
        "assetName": name,
        "assetPath": asset_path or "",
        "rel": rel,
        "bytes": len(data),
        "createdAt": now_iso(),
        "sourceHash": source_hash or "",
        "shading": dict(shading or {}),
        "userMaterial": prof,
        "textureSlots": slots,
    }
    doc = load(game_root)
    doc["items"][item["id"]] = item
    doc["active"][name] = item["id"]
    save(game_root, doc, log=log)
    if log:
        log("已产出 %s（%d B，UserMaterial=%d，贴图槽=%d）"
            % (rel, len(data), len(prof), len(slots)))
    return item


def items_of(game_root, doc=None):
    """按 assetName 分组，每组按生成时间倒序。返回 {name: [item, ...]}。"""
    doc = doc or load(game_root)
    out = {}
    for it in (doc.get("items") or {}).values():
        out.setdefault(it.get("assetName") or "?", []).append(it)
    for v in out.values():
        # 按 stem 的 (时间戳, 整数序号) 倒序：_10 要排到 _9 前面（不能字符串比）
        v.sort(key=lambda x: _seq_of(x.get("rel")), reverse=True)
    return out


def active_item(game_root, asset_name, doc=None):
    doc = doc or load(game_root)
    iid = (doc.get("active") or {}).get(asset_name)
    it = (doc.get("items") or {}).get(iid) if iid else None
    if it and os.path.exists(mmtr_abs(game_root, it["rel"])):
        return it
    lst = items_of(game_root, doc).get(asset_name) or []
    for it in lst:                       # 登记项丢了就退到最新一份
        if os.path.exists(mmtr_abs(game_root, it["rel"])):
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
    """扫 sessions.json 里所有 edit 的 mdf2，收集它们引用的 mmtr 逻辑路径。"""
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


def prune(game_root, doc=None, keep_per_asset=1, dry_run=True, log=None):
    """删掉没被引用的旧 mmtr 产物。

    保留：每个 ``assetName`` 最近 ``keep_per_asset`` 份、``active`` 指向的、
    以及**被现有 edit mdf2 引用**的（后者绝不能删 —— 删了引擎下次重载就读不到，
    材质会变默认方格甚至闪退，和 mdf2 那次踩过的坑一样）。

    返回被删（或 dry-run 下将被删）的绝对路径列表。
    """
    doc = doc or load(game_root)
    keep_ids = set()
    live = referenced_mmtrs(game_root)
    groups = items_of(game_root, doc)
    for name, lst in groups.items():
        for it in lst[:max(1, keep_per_asset)]:
            keep_ids.add(it["id"])
    for iid in (doc.get("active") or {}).values():
        if iid:
            keep_ids.add(iid)

    removed = []
    # 遍历副本：下面会 pop，直接遍历 values() 会 “dictionary changed size”
    for it in list((doc.get("items") or {}).values()):
        rel = ps.norm_rel(it.get("rel") or "")
        if not rel:
            continue
        if it["id"] in keep_ids or rel.lower() in live:
            continue
        p = mmtr_abs(game_root, rel)
        if not os.path.exists(p):
            doc["items"].pop(it["id"], None)
            continue
        if dry_run:
            removed.append(p)
            continue
        try:
            os.remove(p)
            removed.append(p)
            doc["items"].pop(it["id"], None)
        except OSError as e:
            if log:
                log("删除失败 %s: %s" % (os.path.basename(p), e))

    # 孤儿文件：目录里有、登记簿里没有（上次进程崩在“写完未登记”之间）。
    # 未登记就无法被选用，删掉安全；但同样跳过在用的。
    known = {mmtr_abs(game_root, ps.norm_rel(it.get("rel") or ""))
             for it in (doc.get("items") or {}).values() if it.get("rel")}
    d = mmtr_dir_abs(game_root)
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if ".mmtr" not in fn.lower():
                continue
            p = os.path.join(d, fn)
            if p in known:
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
            except OSError:
                pass
    if not dry_run and removed:
        save(game_root, doc, log=None)
    if log:
        log("%s %d 个旧 mmtr 产物%s" % ("将清理" if dry_run else "已清理",
                                       len(removed), "（dry-run）" if dry_run else ""))
    return removed
