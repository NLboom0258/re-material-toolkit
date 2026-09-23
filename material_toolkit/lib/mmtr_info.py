#!/usr/bin/env python3
"""mmtr/blob 摘要辅助(供 CLI/GUI 展示)。

- blob_stage: 由 RDEF 的 Target 字段映射阶段(实测: 0xfffe0500=VS, 0xffff0500=PS,
  0x43530500=CS; 与已知分布一致: env blob0-3=蒙皮CS, blob33=主GBuffer PS, 其余多为VS)。
- group_mode: 由该组池里的"引擎 raw buffer 名"判断该组的顶点处理模式。
"""
import struct

try:
    from .binding import blob_list
except ImportError:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from binding import blob_list

TARGET_STAGE = {0xFFFE0500: "VS", 0xFFFF0500: "PS", 0x43530500: "CS"}

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
        stage = TARGET_STAGE.get(target, f"0x{target:08x}")
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
