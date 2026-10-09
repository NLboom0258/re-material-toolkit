# -*- coding: utf-8 -*-
"""由 RDEF 派生 SDF/mmtr 容器的绑定结构(组 / 资源名池 / 全局字符串池)。

设计依据(见 analysis/sdf_container_structure.md, 全语料主版本已证):
  - 变体记录(264B) = 交叉点: 挂 主程序 P0@+0x00 / VS@-0x20 / CS@+0x08 与 绑定表(desc/pool)。
  - **组的池内容 = 其记录的 [VS∪P0] RDEF 声明的并集**(引擎序 [VS块][PS块]); **组 ⟺ 资源声明签名**。
  - **资源名池** = 被变体记录引用的 blob 的 RDEF 资源并集(按池名去重)。
  - **全局字符串池**(头区) ⊇ 所有 blob(含未引用)的 RDEF 资源名 ∪ 变体名/成员名等元数据。

本模块**只算不改**(供 GUI 显示; 将来"由编辑后的 RDEF 重建容器"也复用)。
注意: 记录解析依赖主版本(0x01100004)骨架; 非主版本(旧格式 SDF)不保证。
"""
from __future__ import annotations

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


# 引擎序里 stage 标记(desc code 的 stage 位)
STAGE_VS, STAGE_PS, STAGE_BOTH, STAGE_CS = 0x01, 0x10, 0x11, 0x20


def _has_program(rec):
    """该记录是否捆绑了任一 shader 程序(P0/VS/CS)。

    ⚠ 不可用 `rec.is_empty`(只查 P0) —— 纯 compute 记录 P0/VS 皆空、CS 在 rec+0x08,
      用 is_empty 会把它们当作空记录跳过 ⇒ CS shader 的资源不进名池/组(实测 lighting.sdf 324 条)。
    """
    return bool(rec.blob_off or rec.vs_blob or rec.cs_blob)


def _entries(data, off, size_of):
    """某 blob 的 RDEF bound resources(数组序) -> [(name,type,bind_point,dim,ret)]。"""
    if not off or off not in size_of:
        return []
    return _rdef.rdef_bind_info(data[off:off + size_of[off]]) or []


def _signature(data, rec, size_of):
    """该记录的“资源声明签名”= frozenset((stage, name, cat, bind_point))。组 ⟺ 签名。"""
    sig = set()
    for stage, off in (("VS", rec.vs_blob), ("PS", rec.blob_off), ("CS", rec.cs_blob)):
        for (nm, t, bp, _dim, _r) in _entries(data, off, size_of):
            sig.add((stage, nm, category(t), bp))
    return frozenset(sig)


def record_entries(data, rec, size_of):
    """该记录绑定池条目(引擎序), 由 RDEF 派生。

    图形记录(VS/P0): [VS块][PS块](按名去重); stage=0x01(VS)/0x10(PS)/0x11(both);
        slot=含 PS→PS 的 bind point、纯 VS→0。
    纯 compute 记录(CS-only, P0/VS 皆空): 由 CS RDEF 派生; stage=0x20(CS); slot=RDEF bind point。
    """
    vs = _entries(data, rec.vs_blob, size_of)
    ps = _entries(data, rec.blob_off, size_of)
    if not vs and not ps:                       # CS-only
        out, seen = [], set()
        for (nm, t, bp, dim, _r) in _entries(data, rec.cs_blob, size_of):
            if nm in seen:
                continue
            seen.add(nm)
            out.append({"name": nm, "cat": category(t), "stage": STAGE_CS,
                        "slot": bp, "dim": dim})
        return out
    ps_map = {}                       # name -> (slot, cat, dim)   (PS 侧为准)
    for (nm, t, bp, dim, _r) in ps:
        ps_map.setdefault(nm, (bp, category(t), dim))
    out, seen = [], set()
    for (nm, t, _bp, dim, _r) in vs:
        if nm in seen:
            continue
        seen.add(nm)
        if nm in ps_map:              # VS 与 PS 都有 -> stage=both, slot=PS 的
            slot, cat, d2 = ps_map[nm]
            out.append({"name": nm, "cat": cat, "stage": STAGE_BOTH, "slot": slot, "dim": d2})
        else:                         # 纯 VS -> slot=0(引擎不写 VS 的 bind point)
            out.append({"name": nm, "cat": category(t), "stage": STAGE_VS, "slot": 0, "dim": dim})
    for (nm, t, bp, dim, _r) in ps:
        if nm in seen:
            continue
        seen.add(nm)
        out.append({"name": nm, "cat": category(t), "stage": STAGE_PS, "slot": bp, "dim": dim})
    return out


def derive_groups(data):
    """程序化绑定组: **按资源声明签名**分组(组 ⟺ 签名)。

    -> {sig: {"entries": [...], "recs": [rec_off...], "blobs": set(blob_off), "n_rec"}}。
    组内容(entries)对同签名的记录必然一致; 直接取首条记录派生。至少被 2 个不同来源
    记录共用的组, 与容器里“共享池”一一对应(仅“包含关系”签名是原版的合并优化)。
    """
    model = MmtrModel(data)
    size_of = dict(model.blobs)
    out = {}
    for r in model.parse_records():
        if not _has_program(r):
            continue
        sig = _signature(data, r, size_of)
        g = out.get(sig)
        if g is None:
            g = {"sig": sig, "recs": [], "blobs": set(),
                 "entries": record_entries(data, r, size_of)}
            out[sig] = g
        g["recs"].append(r.off)
        for off in (r.vs_blob, r.blob_off, r.cs_blob):
            if off:
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
        if not _has_program(r):
            continue
        for off in (r.vs_blob, r.blob_off, r.cs_blob):
            if off:
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
