#!/usr/bin/env python3
"""mmtr 合成基座: head / 变体记录 / tail / blob 的分离与重组 + 结构同构 + 记录字段校验。

目标(用户方向): 把"从 donor 克隆整个 master"推进到 **"版本结构 + 标准 pass 集 + 我们的材质 PS"**
的合成 —— 即摆脱离"必须挑一个现成 master"。本模块提供这一步的**底座**:

  1. `split` / `join` / `roundtrip`: 把 mmtr 拆成四段(骨架 / 内容字段 / 尾段 / blob 区)并可无损重组
     —— 证明"结构"与"内容"可完全分离(合成的前提)。
  2. `same_version`: 同版本 master 的**骨架**(内容清零后 [0,0x46350))逐字节相同
     ⇒ 结构可从**任一**同版本 master 取, 不必是"那一个 donor"。
  3. `verify_record_counts`: 按每个槽 PS/VS 的 RDEF **重算**记录计数/打包字段并与原件比对
     —— 运行时验证"记录内容可由程序推出"(B2 公式), 也是合成时必须"算出"的部分。

尚待(下一片): 由 (程序赋值 + RDEF) **计算**头部内容字段 + 由 RDEF **合成 tail**(池/表/串)。
"""
import struct

try:
    from . import mmtr_blobs as B
    from .mmtr_build import (MmtrImage, MmtrTemplate, content_positions, record_counts,
                             REC_LO, REC_N, REC_SIZE, SKELETON_HI)
    from .rdef import rdef_resources, rdef_uav_names
    from .mmtr_model import MmtrModel
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_blobs as B
    from mmtr_build import (MmtrImage, MmtrTemplate, content_positions, record_counts,
                            REC_LO, REC_N, REC_SIZE, SKELETON_HI)
    from rdef import rdef_resources, rdef_uav_names
    from mmtr_model import MmtrModel

# 记录内"计数/打包字段" -> record_counts() 键 (+ 掩码位宽, None=整 u32)
_COUNT_FIELDS = (("a8", 0xA8, None), ("ac_low", 0xAC, 0xFFFF), ("b0", 0xB0, None),
                 ("b4", 0xB4, None), ("b8", 0xB8, None), ("a4", 0xA4, None),
                 ("c4", 0xC4, None))


def split(data):
    """拆成四段: {skeleton, contents, tail, blobs, head}。

    - skeleton: [0,0x46350) 版本骨架(内容字段清零; 同版本应逐字节相同)。
    - contents: {绝对偏移: u32} 头部所有"内容字段"的值(含 blob_start@0x08、串指针@0x10)。
    - tail: [0x46350, blob_start) 尾段字节。
    - blobs: [blob_start, EOF) blob 区字节。
    - head: [0,0x46350) 原始头部字节(便利)。
    """
    img = MmtrImage.from_bytes(data)
    tpl = MmtrTemplate(data)
    contents = {p: struct.unpack_from("<I", img.buf, p)[0] for p in content_positions()}
    return {"skeleton": tpl.skeleton, "contents": contents, "tail": img.tail,
            "blobs": img.blobs, "head": bytes(img.buf)}


def join(parts):
    """四段重组为完整 mmtr(contents 覆盖到骨架上; blob_start 由 contents@0x08 给出)。"""
    head = bytearray(parts["skeleton"])
    for p, v in parts["contents"].items():
        struct.pack_into("<I", head, p, v)
    return bytes(head) + bytes(parts["tail"]) + bytes(parts["blobs"])


def roundtrip(data):
    """split -> join 是否逐字节回到原文件(分离可逆的硬判据)。"""
    return join(split(data)) == bytes(data)


def same_version(a, b):
    """同版本结构同构: 骨架(内容清零)逐字节相同 ⇒ 结构可在两文件间互换。"""
    return MmtrTemplate(a).skeleton == MmtrTemplate(b).skeleton


def _uav_only(ps_blob):
    """PS 的非 cb/smp 资源是否**全是 UAV**(如 Pick PS) —— 此类记录计数口径不同。"""
    r = rdef_resources(ps_blob)
    if r is None:
        return False
    srv = r[2]
    return bool(srv) and srv <= rdef_uav_names(ps_blob)


def verify_record_counts(data):
    """按 RDEF 重算每槽计数并与原件比对。返回 (bad, exceptions)。

    bad: [(slot, 字段, 现值, 期望值)...] 真不匹配。
    exceptions: 已知例外(UAV-only PS, 如 Pick `PickAddressList*`) —— 其间 mmtr 记录
                不计 UAV, 与"非 cb/smp 即 SRV"口径不符。其余槽应 100% 吻合。
    这是"记录内容可由程序推出"(B2)的运行时验证。
    """
    data = bytes(data)
    img = MmtrImage.from_bytes(data)
    bad, exc = [], []
    for slot in range(REC_N):
        ps = img.rec_field(slot, 0x00)
        if not ps:
            continue
        vs = img.rec_field(slot, -0x20)
        pres = rdef_resources(img.blob_at(ps)) if ps else None
        vres = rdef_resources(img.blob_at(vs)) if vs else None
        if pres is None and vres is None:
            continue
        c = record_counts(pres, vres)
        slot_bad = []
        for key, fo, mask in _COUNT_FIELDS:
            cur = img.rec_field(slot, fo)
            if mask is not None:
                cur &= mask
            if cur != c[key]:
                slot_bad.append((slot, key, cur, c[key]))
        cc_byte = img.buf[REC_LO + slot * REC_SIZE + 0xCC]
        if cc_byte != (c["cc"] & 0xFF):
            slot_bad.append((slot, "cc", cc_byte, c["cc"] & 0xFF))
        if slot_bad:
            (exc if _uav_only(img.blob_at(ps)) else bad).extend(slot_bad)
    return bad, exc


def boundary_issues(data):
    """输入边界校验: 每槽 PS 的输入签名(ISGN) 必须 ⊆ 其 VS 的输出签名(OSGN)。

    D3D12 建 PSO 会校验"PS 输入 ⊆ VS 输出"; 超标则整个材质 pass **被静默跳过**(不崩不报错) ——
    见 PROJECT_SUMMARY "DX12"。名称忽略大小写、跳过系统值语义(详见 dxbc_sig.check_input_supported)。

    返回 [(slot, name, sem_index, ps_mask, vs_mask)] (vs_mask=None = VS 无此槽); 空 = 合规。
    """
    data = bytes(data)
    img = MmtrImage.from_bytes(data)
    from . import dxbc_sig as SG
    out = []
    for slot in range(REC_N):
        ps = img.rec_field(slot, 0x00)
        vs = img.rec_field(slot, -0x20)
        if not ps or not vs:
            continue
        for (n, sem, pm, vm) in SG.check_input_supported(
                SG.input_signature(img.blob_at(ps)), SG.output_signature(img.blob_at(vs))):
            out.append((slot, n, sem, pm, vm))
    return out


# --------------------------------------------------------------- 语义 spec 合成(头部内容可计算)
# 每槽的"语义字段"(位置/大小/程序指针/绑定指针/计数) 可计算; 其余(TBD/gap/程序表) 归"残差"照抄。
_SLOT_SEMANTIC_FIELDS = (0x00, 0x08, -0x28, -0x20, 0x9C, 0x88, 0x8C, 0xD8,
                         0x38, 0x40, 0x48, 0x50, 0x58, 0x60,
                         0xA4, 0xA8, 0xAC, 0xB0, 0xB4, 0xB8, 0xC4)


def _blob_at(spec, off):
    """从 spec[blobs] 里取绝对偏移 off 处的 blob; 非 blob 起点返回 None。"""
    if not off:
        return None
    bs = SKELETON_HI + len(spec["tail"])
    i = off - bs
    b = spec["blobs"]
    if i < 0 or i + 28 > len(b) or b[i:i + 4] != b"DXBC":
        return None
    return bytes(b[i:i + struct.unpack_from("<I", b, i + 24)[0]])


def _counts_for(ps_blob, vs_blob):
    """(record_counts dict, uav_only) ; 取不到 RDEF 返回 (None, False)。"""
    pres = rdef_resources(ps_blob) if ps_blob else None
    vres = rdef_resources(vs_blob) if vs_blob else None
    if pres is None and vres is None:
        return None, False
    c = record_counts(pres, vres)
    uav = bool(pres) and bool(pres[2]) and pres[2] <= rdef_uav_names(ps_blob)
    return c, uav


def slot_specs(data):
    """每槽的语义 spec(非空槽): 程序/大小/名字指针/绑定指针/计数。"""
    data = bytes(data)
    img = MmtrImage.from_bytes(data)
    out = []
    for i, r in enumerate(MmtrModel(data).parse_records()):
        if r.is_empty and not r.vs_blob and not r.cs_blob:
            continue
        base = REC_LO + i * REC_SIZE
        out.append({
            "slot": i, "ps": r.blob_off, "vs": r.vs_blob, "cs": r.cs_blob,
            "ps_size": r.blob_size, "vs_size": img.rec_field(i, 0x88),
            "name_ptr": r.name_ptr,
            "bind": (r.cb[0], r.cb[1], r.smp[0], r.smp[1], r.tex[0], r.tex[1]),
            "counts": {k: img.rec_field(i, fo) for k, fo, _m in _COUNT_FIELDS},
            "cc": img.buf[base + 0xCC],
        })
    return out


def _semantic_positions(slots):
    """语义字段覆盖的绝对字节位(用于从全量内容里扣除出"残差")。"""
    pos = set()
    for s in slots:
        base = REC_LO + s["slot"] * REC_SIZE
        for fo in _SLOT_SEMANTIC_FIELDS:
            for t in range(4):
                pos.add(base + fo + t)
        pos.add(base + 0xCC)
    return pos


def extract(data):
    """提取合成 spec: skeleton + tail + blobs + string_ptr + slots(语义) + residual(其余内容字段)。"""
    data = bytes(data)
    img = MmtrImage.from_bytes(data)
    full = {p: struct.unpack_from("<I", img.buf, p)[0] for p in content_positions()}
    slots = slot_specs(data)
    sem = _semantic_positions(slots)
    return {"skeleton": MmtrTemplate(data).skeleton, "tail": img.tail, "blobs": img.blobs,
            "string_ptr": struct.unpack_from("<I", img.buf, 0x10)[0],
            "slots": slots, "residual": {p: v for p, v in full.items() if p not in sem}}


def instantiate(spec, program_of=None, recompute_counts=False):
    """由合成 spec 组装 mmtr。

    program_of: {slot: {"ps"/"vs"/"cs": blob 绝对偏移}} —— 覆盖该槽程序(合成时接入我们的 PS)。
    recompute_counts: True 时按 RDEF **重算**计数(UAV-only PS 如 Pick 除外, 退回 spec)。
    """
    head = bytearray(spec["skeleton"])
    struct.pack_into("<I", head, 0x08, SKELETON_HI + len(spec["tail"]))
    struct.pack_into("<I", head, 0x10, spec["string_ptr"])
    for p, v in spec["residual"].items():
        struct.pack_into("<I", head, p, v)
    prog_of = program_of or {}
    for s in spec["slots"]:
        slot = s["slot"]
        base = REC_LO + slot * REC_SIZE
        ps = prog_of.get(slot, {}).get("ps", s["ps"])
        vs = prog_of.get(slot, {}).get("vs", s["vs"])
        cs = prog_of.get(slot, {}).get("cs", s["cs"])
        # 指派新程序时, 自动同步其大小(否则 +0x9c/+0x88 与实际不符)
        ps_size = s["ps_size"]
        vs_size = s["vs_size"]
        if "ps" in prog_of.get(slot, {}):
            b = _blob_at(spec, ps)
            if b is not None:
                ps_size = len(b)
        if "vs" in prog_of.get(slot, {}):
            b = _blob_at(spec, vs)
            if b is not None:
                vs_size = len(b)
        struct.pack_into("<I", head, base + 0x00, ps)
        struct.pack_into("<I", head, base - 0x28, vs)
        struct.pack_into("<I", head, base - 0x20, vs)
        struct.pack_into("<I", head, base + 0x08, cs)
        struct.pack_into("<I", head, base + 0x9C, ps_size)
        struct.pack_into("<I", head, base + 0x88, vs_size)
        struct.pack_into("<I", head, base + 0x8C, vs_size)
        struct.pack_into("<I", head, base + 0xD8, s["name_ptr"])
        for fo, v in zip((0x38, 0x40, 0x48, 0x50, 0x58, 0x60), s["bind"]):
            struct.pack_into("<I", head, base + fo, v)
        counts, cc = s["counts"], s["cc"]
        if recompute_counts:
            c, uav = _counts_for(_blob_at(spec, ps), _blob_at(spec, vs))
            if c is not None and not uav:
                counts = {k: c[k] for k, _fo, _m in _COUNT_FIELDS}
                cc = c["cc"] & 0xFF
        for k, fo, mask in _COUNT_FIELDS:
            v = counts[k]
            if mask is not None:
                cur = struct.unpack_from("<I", head, base + fo)[0]
                v = (cur & ~mask & 0xFFFFFFFF) | (v & mask)
            struct.pack_into("<I", head, base + fo, v)
        head[base + 0xCC] = cc & 0xFF
    return bytes(head) + bytes(spec["tail"]) + bytes(spec["blobs"])


def roundtrip_spec(data):
    """extract -> instantiate 是否逐字节回到原文件(头部内容可从语义 spec 完整重构)。"""
    return instantiate(extract(data)) == bytes(data)


def report(data):
    """打印合成基座自检: 分离可逆 / 版本指纹 / 记录计数校验。"""
    import hashlib

    data = bytes(data)
    print("file size=%d blobs=%d" % (len(data), B.blob_count(data)))
    print("  分离可逆(split->join==原文件): %s" % roundtrip(data))
    print("  版本骨架 md5: %s" % hashlib.md5(MmtrTemplate(data).skeleton).hexdigest())
    print("  语义 spec 往返(extract->instantiate): %s" % roundtrip_spec(data))
    bad, exc = verify_record_counts(data)
    print("  记录计数按 RDEF 重算: 真不匹配 %d 处; 已知例外(Pick) %d 处" % (len(bad), len(exc)))
    for it in bad[:10]:
        print("     [BAD] slot=%d %s 现值=%d 期望=%d" % it)
    issues = MmtrModel(data).validate()
    print("  头部自检: %s" % ("通过" if not issues else "%d 处问题" % len(issues)))


def main():
    import sys
    if len(sys.argv) < 2:
        print("用法: python mmtr_synth.py <mmtr> [other.mmtr]"); return
    data = open(sys.argv[1], "rb").read()
    report(data)
    if len(sys.argv) > 2:
        other = open(sys.argv[2], "rb").read()
        print("  同版本结构 vs %s: %s" % (sys.argv[2], same_version(data, other)))


if __name__ == "__main__":
    main()
