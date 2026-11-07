#!/usr/bin/env python3
"""mmtr 只读扫描（真实记录表；DMC5 / MHWS 通用）。

和 `binding.py` 的旧 264B 窗口无关：布局全部来自 `game_profile.MmtrView`，
因此同一套代码能读 DMC5(0x108) 与 MHWS(0x110)，也自然支持每个文件不同的
rows×cols（4×272 / 5×479 / 4×463 …）。

供 GUI 的「只读 MMTR 页」、CLI 与校验工具使用；本模块不写文件。

主要入口：
    view = game_profile.resolve(data, version)   # version 来自文件名后缀
    rs   = records(data, view)                   # 每条记录的阶段/计数/名字
    us   = blob_usage(data, view)                # blob -> [(记录, 阶段)]
    bs   = bindings(data, view, rec_off, "srv")   # 某条记录的绑定表条目
"""
import struct

try:
    from . import game_profile as GP
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import game_profile as GP

_MAGIC = b"SDF\x00"


def version_from_path(path):
    """从文件名取资源版本：`xxx.mmtr.240906211` -> 240906211；取不到返回 None。"""
    if not path:
        return None
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    parts = name.split(".")
    if len(parts) >= 2 and parts[-1].isdigit():
        return int(parts[-1])
    return None


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def _u64(d, o):
    return struct.unpack_from("<Q", d, o)[0]


def cstring(data, off, blob_start, limit=64):
    """读 NUL 结尾的 ASCII 名（越界/非可打印返回 ""）。"""
    if not (0x40 <= off < blob_start):
        return ""
    end = data.find(b"\x00", off, min(off + limit, blob_start))
    if end < 0:
        return ""
    raw = data[off:end]
    if not raw or not all(0x20 <= c < 0x7F for c in raw):
        return ""
    return raw.decode("ascii", "replace")


# ---------------------------------------------------------------- blob 区
def blob_list(data, view=None, entries=None):
    """blob 列表 -> [(off, size)]。

    有 view 时**以记录里的 (ptr,size) 为准**（权威、且自动容忍每个 blob 后的
    尾部字节：MHWS 实测每个 DXBC 容器后跟 8 字节，DMC5 无）；没有 view 时按
    容器头 +24 的总大小连续走（trailer=0）。
    """
    if len(data) < 16 or data[:4] != _MAGIC:
        return []
    limit = _u32(data, 8)
    if view is not None:
        seen = {}
        for e in (entries if entries is not None else record_entries(data, view)):
            for ptr, size in e["stages"].values():
                if ptr and size and ptr + size <= len(data) and data[ptr:ptr + 4] == b"DXBC":
                    seen[(ptr, size)] = True
        return sorted(seen)
    out = []
    off = limit
    while off + 32 <= len(data) and data[off:off + 4] == b"DXBC":
        size = _u32(data, off + 24)
        if size < 32 or off + size > len(data):
            break
        out.append((off, size))
        nxt = off + size
        # MHWS: 容器后跟 8 字节尾部（auto-detect，无需 profile）
        if data[nxt:nxt + 4] != b"DXBC" and data[nxt + 8:nxt + 12] == b"DXBC":
            nxt += 8
        off = nxt
    return out


def blob_chunks(data, off, size):
    """容器内的 chunk 列表 -> [(fourcc, 相对偏移, 大小)]（读不出返回 []）。"""
    if not (0 <= off <= len(data) - 32) or data[off:off + 4] != b"DXBC":
        return []
    n = _u32(data, off + 28)
    if n <= 0 or n > 64:
        return []
    out = []
    for i in range(n):
        co = off + _u32(data, off + 32 + i * 4)
        if co + 8 > off + size:
            break
        out.append((data[co:co + 4].decode("latin1"), co - off, _u32(data, co + 4)))
    return out


# ---------------------------------------------------------------- 记录
def record_entries(data, view):
    """每条真实记录 -> dict(index, off, name, stages, counts, desc_counts)。

    stages: {stage_key: (ptr, size)}（7 个槽，含输入签名 sig）
    counts: {kind: [6]}（记录自报的 per-stage 计数）
    desc_counts: {kind: int}（各类描述符表条数）
    """
    bs = view.blob_start
    out = []
    name_off = view.profile.name_off
    for i, rec in enumerate(view.record_offsets()):
        stages = {}
        has_prog = False
        for s in view.stages:
            ptr = _u64(data, rec + s.ptr_off)
            size = _u32(data, rec + s.size_off)
            stages[s.key] = (ptr, size)
            if s.key not in ("sig",) and ptr:
                has_prog = True
        counts = {}
        for k, d in enumerate(view.desc_tables):
            base = rec + view.count_matrix_off + k * 6
            counts[d.key] = list(data[base:base + 6])
        out.append({
            "index": i,
            "off": rec,
            "name": cstring(data, _u64(data, rec + name_off), bs),
            "stages": stages,
            "has_program": has_prog,
            "counts_total": _u32(data, rec + view.count_total_off),
            "counts": counts,
            "desc_counts": {d.key: data[rec + d.count_off] for d in view.desc_tables},
        })
    return out


def blob_usage(data, view, entries=None):
    """blob 偏移 -> [(记录下标, 阶段key)]；只统计非 sig 槽。"""
    use = {}
    for e in (entries if entries is not None else record_entries(data, view)):
        for key, (ptr, _size) in e["stages"].items():
            if key == "sig" or not ptr:
                continue
            use.setdefault(ptr, []).append((e["index"], key))
    return use


# ---------------------------------------------------------------- 绑定表
def bindings(data, view, rec_off, kind):
    """读某条记录的一类绑定表 -> [{idx,name,hash,type,stage,slot}]。

    desc 指向「首条前 4B 头」：第 i 条 code 在 desc+4+i*8；
    code = (type<<24)|(stage_mask<<16)|slot。
    名称池条目：cbuffer 32B（含 size/count/members），其它 16B。
    """
    d = view.profile.desc(kind)
    desc = _u32(data, rec_off + d.desc_off)
    pool = _u32(data, rec_off + d.pool_off)
    count = data[rec_off + d.count_off]
    bs = view.blob_start
    stride = 32 if kind == "cb" else 16
    out = []
    if not desc or not pool or not count or desc + 4 + count * 8 > bs:
        return out
    for i in range(count):
        code = _u32(data, desc + 4 + i * 8)
        po = pool + i * stride
        if po + 16 > bs:
            break
        out.append({
            "idx": i,
            "name": cstring(data, _u64(data, po), bs),
            "hash": _u32(data, po + 8),
            "type": (code >> 24) & 0xFF,
            "stage": (code >> 16) & 0xFF,
            "slot": code & 0xFFFF,
        })
    return out


def record_bindings(data, view, rec_off):
    """{kind: [条目]}（5 类，空表给空列表）。"""
    return {d.key: bindings(data, view, rec_off, d.key) for d in view.desc_tables}


def name_vocabulary(data, view, entries=None):
    """SRV 名称池里出现的名字 -> 引用记录数（按真实绑定表统计）。"""
    vocab = {}
    for e in (entries if entries is not None else record_entries(data, view)):
        for b in bindings(data, view, e["off"], "srv"):
            nm = b["name"]
            if nm:
                vocab[nm] = vocab.get(nm, 0) + 1
    return vocab


# ---------------------------------------------------------------- 展示辅助
STAGE_MASK_LABELS = (("VS", 0x01), ("HS", 0x02), ("DS", 0x04),
                     ("GS", 0x08), ("PS", 0x10), ("CS", 0x20))
TYPE_LABELS = {0x00: "sampler", 0x02: "tex2d", 0x03: "tex3d", 0x04: "cube",
               0x06: "tex2d-array", 0x80: "buffer", 0xFF: "cbuffer"}


def stage_label(mask):
    return "|".join(lab for lab, bit in STAGE_MASK_LABELS if mask & bit) or "-"


def type_label(code):
    return TYPE_LABELS.get(code, "0x%02x" % code)


def pass_family(name):
    """记录名 -> pass 家族（MHWS / DMC5 通用，按用途词判定）。

    不能只看开头：MHWS 的名字普遍带 A/TS/ATS 前缀（alpha-test / two-sided），
    如 `ADeferredStatic`、`ADepthOutputDepthWritePreTransform`；SpeedTree 与
    Meshlet 还会把用途写在中间（`SpeedTreeV8ATreeFoliageO0Forward`）。
    优先级：SpeedTree/Meshlet 细分 → GBuffer（Deferred）→ Forward → Shadow →
    Pick → Stencil → Depth/PrePass → 其它用途。实测 MHWS 语料 0 个“其它”。
    """
    low = (name or "").casefold()
    if "speedtree" in low:
        if "gbuffer" in low:
            return "SpeedTree · GBuffer"
        if "shadow" in low:
            return "SpeedTree · Shadow"
        if "depth" in low:
            return "SpeedTree · Depth"
        if "forward" in low or "foward" in low:
            return "SpeedTree · Forward"
        return "SpeedTree"
    if "meshlet" in low:
        if "gbuffer" in low:
            return "Meshlet · GBuffer"
        if "shadow" in low:
            return "Meshlet · Shadow"
        return "Meshlet"
    if "deferred" in low or "gbuffer" in low:
        return "GBuffer"
    if "forward" in low or "foward" in low:
        return "Forward"
    if "shadow" in low:
        return "Shadow"
    if "pick" in low:
        return "Pick"
    if "stencil" in low:
        return "Stencil"
    if any(t in low for t in ("depthwrite", "depthoutput", "prepass", "zprepass")):
        return "Depth/PrePass"
    if "velocity" in low:
        return "Velocity"
    if "water" in low:
        return "Water"
    if "gui" in low:
        return "GUI"
    if "emissiveonly" in low:
        return "Emissive"
    if low.endswith("cs") or "compute" in low:
        return "Compute"
    return "其它"
