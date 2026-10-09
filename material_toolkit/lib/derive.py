# -*- coding: utf-8 -*-
"""由 RDEF 派生 SDF/mmtr 容器的绑定结构(组 / 资源名池 / 全局字符串池)。

设计依据(见 analysis/sdf_container_structure.md, 全语料主版本已证):
  - 变体记录(264B) = 交叉点: 挂 6 个程序槽(VS/HS/DS/GS/PS/CS) 与 绑定表(desc/pool)。
  - **组的池内容 = 其记录各程序槽 RDEF 声明的并集**(引擎序); **组 ⟺ 资源声明签名**。
  - **资源名池** = 被变体记录引用的 blob 的 RDEF 资源并集(按池名去重)。
  - **全局字符串池**(头区) ⊇ 所有 blob(含未引用)的 RDEF 资源名 ∪ 变体名等元数据。

本模块**只算不改**(供 GUI 显示; 将来"由编辑后的 RDEF 重建容器"也复用)。
注意: 记录解析依赖主版本(0x01100004)骨架; 非主版本(旧格式 SDF)不保证。
"""
from __future__ import annotations

import struct

from . import rdef as _rdef
from .mmtr_model import MmtrModel

# 池类别 <-> D3D bound-resource type
#   cb=0, smp=3, SRV={1(tbuffer),2(texture),5(structured),7(byteaddress)},
#   UAV={4,6,8,9,10,11}。池里只有 cb/smp/SRV(表3)与 SRV/UAV(表4/5)。
_CAT_SRV = (1, 2, 5, 7)
_CAT_UAV = (4, 6, 8, 9, 10, 11)


def category(bind_type):
    """D3D bound-resource type -> 'cb'/'smp'/'tex'/'uav'/'?'。"""
    if bind_type == 0:
        return "cb"
    if bind_type == 3:
        return "smp"
    if bind_type in _CAT_SRV:
        return "tex"
    if bind_type in _CAT_UAV:
        return "uav"
    return "?"


# 阶段 desc-"stage 字节" = 位掩码(实测: VS|PS=0x11, CS=0x20, DS=0x04)
STAGE_BIT = {"VS": 0x01, "HS": 0x02, "DS": 0x04, "GS": 0x08, "PS": 0x10, "CS": 0x20}
_STAGE_NAMES = ((0x01, "VS"), (0x02, "HS"), (0x04, "DS"),
                (0x08, "GS"), (0x10, "PS"), (0x20, "CS"))
# 记录的 6 个程序槽(相对记录基址; 按 stage 位升序): (阶段, 指针偏移)
_PROG = (("VS", -0x20), ("HS", -0x18), ("DS", -0x10),
         ("GS", -0x08), ("PS", 0x00), ("CS", 0x08))


def stage_text(mask):
    """stage 位掩码 -> 'VS'/'VS|PS'/'CS'…。"""
    return "|".join(n for b, n in _STAGE_NAMES if mask & b) or "?"


def _u32(data, o):
    return struct.unpack_from("<I", data, o)[0]


def slots(data, rec):
    """记录的非空程序槽: [(stage, blob_off), ...](按 stage 位升序)。

    ⚠ **不要用 `rec.is_empty`(只查 P0)**: 纯 compute 记录 P0/VS 皆空、CS 在 rec+0x08;
      蒙皮/细分等还可能只有 HS/DS/GS。只看 P0 会漏整条记录的资源(实测 lighting.sdf 324 条)。
    """
    out = []
    for st, po in _PROG:
        off = _u32(data, rec.off + po)
        if off:
            out.append((st, off))
    return out


def _entries(data, off, size_of):
    """某 blob 的 RDEF bound resources(数组序) -> [(name,type,bind_point,dim,ret)]。"""
    if not off or off not in size_of:
        return []
    return _rdef.rdef_bind_info(data[off:off + size_of[off]]) or []


def _signature(data, rec, size_of):
    """该记录的“资源声明签名”= frozenset((stage, name, cat, bind_point))。组 ⟺ 签名。"""
    sig = set()
    for st, off in slots(data, rec):
        for (nm, t, bp, _dim, _r) in _entries(data, off, size_of):
            sig.add((st, nm, category(t), bp))
    return frozenset(sig)


def record_entries(data, rec, size_of):
    """该记录绑定池条目(引擎序), 由各程序槽 RDEF 派生。

    按 stage 位升序(VS→HS→DS→GS→PS→CS)遍历各槽的 RDEF, 名字去重(先见者定位);
    stage = 声明它的各槽位之**位掩码 OR**; slot = 含 PS 取 PS bind point、纯 CS 取 bind point、
    其余(VS/HS/DS/GS-only)取 0(引擎不写这些槽的 bind point)。
    """
    prog = [(st, _entries(data, off, size_of)) for st, off in slots(data, rec)]
    ps_bp = {}
    for st, ents in prog:
        if st == "PS":
            for (nm, t, bp, dim, _r) in ents:
                ps_bp.setdefault(nm, (bp, category(t), dim))
    has_gfx = any(st != "CS" for st, _ in prog)
    out, seen = [], {}
    for st, ents in prog:
        for (nm, t, bp, dim, _r) in ents:
            if nm in seen:
                seen[nm]["stage"] |= STAGE_BIT[st]
                continue
            if nm in ps_bp:
                slot, cat, d2 = ps_bp[nm]
            elif has_gfx:
                slot, cat, d2 = 0, category(t), dim
            else:                                   # 纯 CS
                slot, cat, d2 = bp, category(t), dim
            e = {"name": nm, "cat": cat, "stage": STAGE_BIT[st], "slot": slot, "dim": d2}
            seen[nm] = e
            out.append(e)
    return out


def derive_groups(data):
    """程序化绑定组: **按资源声明签名**分组(组 ⟺ 签名)。

    -> {sig: {"entries": [...], "recs": [rec_off...], "blobs": set(blob_off), "n_rec"}}。
    组内容(entries)对同签名的记录必然一致; 直接取首条记录派生。
    """
    model = MmtrModel(data)
    size_of = dict(model.blobs)
    out = {}
    for r in model.parse_records():
        sl = slots(data, r)
        if not sl:
            continue
        sig = _signature(data, r, size_of)
        g = out.get(sig)
        if g is None:
            g = {"sig": sig, "recs": [], "blobs": set(),
                 "entries": record_entries(data, r, size_of)}
            out[sig] = g
        g["recs"].append(r.off)
        for _st, off in sl:
            g["blobs"].add(off)
    for g in out.values():
        g["n_rec"] = len(g["recs"])
    return out


def derive_namepool(data, cats=("cb", "smp", "tex", "uav")):
    """程序化资源名池: 被变体记录**引用**的 blob 的 RDEF 资源。

    -> {name: {"cats": set(...), "blobs": set(blob_off)}}。未被引用的 blob 的资源不入池。
    """
    model = MmtrModel(data)
    size_of = dict(model.blobs)
    used = set()
    for r in model.parse_records():
        for _st, off in slots(data, r):
            used.add(off)
    vocab = {}
    for off in used:
        for (nm, t, _bp, _dim, _r) in _entries(data, off, size_of):
            c = category(t)
            if c == "?" or c not in cats:
                continue
            d = vocab.setdefault(nm, {"cats": set(), "blobs": set()})
            d["cats"].add(c)
            d["blobs"].add(off)
    return vocab


def derive_stringpool(data):
    """程序化全局字符串池名集: 所有 blob(含未引用)的 RDEF **绑定资源名** ∪ 变体名。

    -> set[str]。验证: 该集合应为容器头区(字符串池)的子集。
    注: cbuffer **成员名**(如 VAR_*) **不在**头区(实测), 故不纳入。
    """
    model = MmtrModel(data)
    size_of = dict(model.blobs)
    names = set()
    for off, sz in model.blobs:
        b = data[off:off + sz]
        for (nm, _t, _bp, _dim, _r) in (_rdef.rdef_bind_info(b) or []):
            names.add(nm)
    for r in model.parse_records():
        if r.name:
            names.add(r.name)
    return names
