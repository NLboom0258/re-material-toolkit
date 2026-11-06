#!/usr/bin/env python3
"""mmtr/blob 摘要辅助(供 CLI/GUI 展示)。

- blob_stage: 由 RDEF 的 Target 字段映射阶段。token 高16位=类型(ASCII 大类, VS/PS 为特殊码),
  低16位=版本(0x0500=SM5.0 / 0x0501=SM5.1) ⇒ 支持 VS/PS/GS/HS/DS/CS 及 SM5.1(带 "(5.1)" 标注)。
- group_mode: 由该组池里的"引擎 raw buffer 名"判断该组的顶点处理模式。
"""
import struct

try:
    from .binding import blob_list
    from . import game_profile as GP
except ImportError:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from binding import blob_list
    import game_profile as GP

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
    """单次扫描: {blob_off: 该 blob 的唯一绑定组数}(供列表展示, 避免逐 blob 全头扫描)。

    兼顾记录的 3 个程序槽: P0(+0x00) / VS(-0x20) / CS(+0x08) ——
    纯 compute 记录 P0 为空、CS 在 +0x08, 只扫 P0 会让 CS blob 的组数漏成 0。
    每槽 = (大小字段相对扫描点偏移, 组键 desc/pool 相对扫描点偏移)。
    """
    bs, bl = blob_list(data)
    size_of = dict(bl)
    sets = {off: set() for off, _ in bl}
    slots = ((0x9C, 0x58), (0xAC, 0x78), (0x98, 0x50))   # P0 / VS / CS
    for p in range(0x20, bs - 3, 4):
        v = _u32(data, p)
        if v not in sets:
            continue
        sz = size_of[v]
        for so, ko in slots:
            if _u32(data, p + so) == sz:
                sets[v].add((_u32(data, p + ko), _u32(data, p + ko + 8)))
                break
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


# ---- 程序记录自洽性校验 ----
# 真实表布局见 game_profile: 基址/步长/条数按游戏取(DMC5 0x10/0x108/1088,
# MHWS 0x10/0x110/2395), 记录内字段偏移两者一致:
#   +0xD4 = 总 stage 绑定数; +0xD8..0xF5 = 5 类(CB/SMP/SRV/UAV/OPT)×6 stage 计数;
#   +0x68/0x78/0x88/0x98/0xA8 = 各描述符表, +0xF6/0xF7/0xFC/0xFD/0xFE = 各类条数。
# 下面两个常量保留为 DMC5 默认(兼容旧调用), 实际以 profile 为准。
REC_LO_REAL, REC_SIZE_REAL = GP.DMC5.rec_base, GP.DMC5.rec_stride
DESC_PTR_FIELDS = tuple(d.desc_off for d in GP.DMC5.desc_tables)
DESC_COUNT_FIELDS = tuple(d.count_off for d in GP.DMC5.desc_tables)
_RESOURCE_KIND_NAMES = tuple(d.label for d in GP.DMC5.desc_tables)


def _u64(d, o):
    return struct.unpack_from("<Q", d, o)[0]


def _record_name(d, rec, blob_start):
    off = _u64(d, rec)
    if not (0 < off < blob_start):
        return ""
    end = d.find(b"\x00", off, blob_start)
    return d[off:end].decode("latin1", "replace") if end >= 0 else ""


def stage_count_mismatches(data, view=None):
    """校验 per-stage 计数是否与描述符 stage 掩码一致(第三方工具/引擎的硬约束)。

    view: `game_profile.MmtrView`; None = 按版本后缀/表形状自动识别
          (DMC5 / MHWS 已登记, 新版本走步长启发式)。
    返回 [(记录下标, 类别, 程序名, 实际计数, 记录声明)]; 空列表 = 自洽。
    类别 "*" 表示总数(+0xD4)与 30 个计数字节之和不符。
    """
    if len(data) < 16 or data[:4] != b"SDF\x00":
        raise ValueError("not an SDF/mmtr file")
    view = view or GP.resolve(data)
    blob_start = _u32(data, 8)   # 写入侧是 <I(高 4 字节保留为 0)
    table_end = view.table_end
    hi = view.count_matrix_off + view.count_matrix_len
    bad = []
    for i in range(view.n_records):
        rec = view.rec_base + i * view.rec_stride
        if rec + view.rec_stride > blob_start:
            break
        counts = data[rec + view.count_matrix_off:rec + hi]
        name = _record_name(data, rec, blob_start)
        total = _u32(data, rec + view.count_total_off)
        if total != sum(counts):
            bad.append((i, "*", name, (), (total, sum(counts))))
        for kind, d in enumerate(view.desc_tables):
            # 描述符表指针是 u32(与写入侧/rec_field 一致); 低 4 字节之外不能当指针高低位拼。
            ptr, cnt = _u32(data, rec + d.desc_off), data[rec + d.count_off]
            if not ptr or not cnt:
                continue
            if not (table_end <= ptr < blob_start) or ptr + cnt * 8 > blob_start:
                continue
            actual = [0] * 6
            for j in range(cnt):
                mask = data[ptr + j * 8 + 6]
                for st in range(6):
                    if mask & (1 << st):
                        actual[st] += 1
            want = list(counts[kind * 6:(kind + 1) * 6])
            if actual != want:
                bad.append((i, d.label, name, tuple(actual), tuple(want)))
    return bad
