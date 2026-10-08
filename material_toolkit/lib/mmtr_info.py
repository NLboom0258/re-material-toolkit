#!/usr/bin/env python3
"""mmtr/blob 摘要辅助(供 CLI/GUI 展示)。

- blob_stage: 由 RDEF 的 Target 字段映射阶段。token 高16位=类型(ASCII 大类, VS/PS 为特殊码),
  低16位=版本(0x0500=SM5.0 / 0x0501=SM5.1) ⇒ 支持 VS/PS/GS/HS/DS/CS 及 SM5.1(带 "(5.1)" 标注)。
- group_mode: 由该组池里的"引擎 raw buffer 名"判断该组的顶点处理模式。
"""
import struct

try:
    from .binding import blob_list
except ImportError:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from binding import blob_list

# 阶段 token: 高16位=类型(ASCII 大类; VS/PS 为特殊码), 低16位=版本(0x0500=SM5.0 / 0x0501=SM5.1)。
_STAGE_TYPE = {0xFFFE: "VS", 0xFFFF: "PS", 0x4753: "GS",
               0x4853: "HS", 0x4453: "DS", 0x4353: "CS"}


def stage_label(target):
    """RDEF target -> 阶段标签(带 SM5.1 标注); 未知码则 0x 十六进制。"""
    if target is None:
        return "?"
    name = _STAGE_TYPE.get((target >> 16) & 0xFFFF)
    if name is None:
        return "0x%08x" % target
    lo = target & 0xFFFF
    if ((lo >> 8) & 0xF) == 5 and (lo & 0xF) == 1:
        return name + " (5.1)"
    return name


# 兼容保留: 具体 token -> 阶段(不带版本标注)
TARGET_STAGE = dict({(t << 16) | 0x0500: n for t, n in _STAGE_TYPE.items()})
TARGET_STAGE.update({(t << 16) | 0x0501: n for t, n in _STAGE_TYPE.items()})

# 组的"顶点处理模式"由这些 raw buffer 名组合决定
_RAW_MODES = [("SkinningMatrices", "Skinning"),
              ("InstanceWorldInfo", "Instance"),
              ("IndirectIndicesBuffer", "Indirect")]


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def blob_info(data, idx):
    """返回 {'idx','off','size','stage','n_cb','n_br','target'}。"""
    bs, bl = blob_list(data)
    if not (0 <= idx < len(bl)):
        raise ValueError(f"blob idx {idx} out of range")
    off, size = bl[idx]
    target = n_cb = n_br = None
    nch = _u32(data, off + 28)
    for k in range(nch):
        co = _u32(data, off + 32 + k * 4) + off
        if data[co:co + 4] == b"RDEF":
            n_cb, _cbo, n_br, _bro, target = struct.unpack_from("<IIIII", data, co + 8)
            break
    if target is None:
        stage = "?"
    else:
        stage = stage_label(target)
    return {"idx": idx, "off": off, "size": size, "stage": stage,
            "n_cb": n_cb, "n_br": n_br, "target": target}


def blob_count(data):
    return len(blob_list(data)[1])


def blob_group_counts(data):
    """单次扫描: {blob_off: 该 blob 的唯一绑定组数}(供列表展示, 避免逐 blob 全头扫描)。"""
    bs, bl = blob_list(data)
    size_of = dict(bl)
    sets = {off: set() for off, _ in bl}
    for p in range(0, bs - 3, 4):
        v = _u32(data, p)
        if v in sets and _u32(data, p + 0x9c) == size_of[v]:
            sets[v].add((_u32(data, p + 0x58), _u32(data, p + 0x60)))
    return {off: len(s) for off, s in sets.items()}


def group_mode(srv_names):
    """由池名列表推该组的顶点处理模式: Static / Instance / Skinning / Indirect 的组合。"""
    tags = [lab for key, lab in _RAW_MODES if any(key in n for n in srv_names)]
    return "+".join(tags) if tags else "Static"


def type_label(nbytes):
    """按字节大小给出类型标签(与 MDF-Manager 的口径一致: 只看大小/个数)。

    4->float, 8->float2, 12->float3, 16->float4, 48->float4x3, 64->float4x4,
    其它 -> "N*float"。
    """
    n = nbytes // 4
    if n >= 4 and nbytes % 16 == 0:
        return "float4" if n == 4 else f"float4x{n // 4}"
    return {1: "float", 2: "float2", 3: "float3"}.get(n, f"{n}*float")
