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


def report(data):
    """打印合成基座自检: 分离可逆 / 版本指纹 / 记录计数校验。"""
    import hashlib

    data = bytes(data)
    print("file size=%d blobs=%d" % (len(data), B.blob_count(data)))
    print("  分离可逆(split->join==原文件): %s" % roundtrip(data))
    print("  版本骨架 md5: %s" % hashlib.md5(MmtrTemplate(data).skeleton).hexdigest())
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
