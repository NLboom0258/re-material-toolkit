#!/usr/bin/env python3
"""DXBC / DXIL 容器的 chunk 级读写。

两个游戏用同一种容器（实测 speedtree.mmtr 与 character_default.mmtr）：

    偏移  内容
    0     "DXBC"
    4     16 字节校验 = dxil_hash(blob[20:], "retail")   ← 实测命中，两游戏同算法
    20    u32 unknown = 1
    24    u32 total_size
    28    u32 chunk_count
    32    u32 offsets[chunk_count]
    ..    chunk: 4 字节名字 + u32 尺寸 + 内容（**无 padding**，实测相邻 gap 全 0）

MHWS 的 chunk 顺序：SFI0 ISG1 OSG1 PSV0 STAT ILDN HASH DXIL
DMC5 的 chunk 顺序：RDEF ISGN OSGN SHEX STAT

`HASH` chunk（20 B = flags 0 + 16 B 摘要）的算法**未解**——不是
md5/dxil_hash/sha1/sha256 对 DXIL 或整体的结果（13 种组合全不中）。
所以换 DXIL 时它只能原样保留（是否被引擎校验需实机）；
`replace_chunk()` 默认不动它。

用法::

    c = parse(blob)
    identity(blob) == blob                      # 恒等重建
    replace(blob, b"ISG1", new_sig)             # 任意 chunk，尺寸可变
"""
import struct

from .dxilhash import dxil_hash

MAGIC = b"DXBC"
HEADER_SIZE = 32
HASH_OFF = 4
HASH_SIZE = 16
BODY_OFF = 20


class ContainerError(ValueError):
    """容器结构非法。"""


def parse(blob):
    """解析容器 -> dict(hash/unknown/total/count/chunks)。"""
    if len(blob) < HEADER_SIZE or blob[:4] != MAGIC:
        raise ContainerError("not a DXBC container")
    h, unknown, total, n = struct.unpack_from("<16sIII", blob, HASH_OFF)
    if HEADER_SIZE + 4 * n > len(blob):
        raise ContainerError("chunk table out of range")
    offs = list(struct.unpack_from("<%dI" % n, blob, HEADER_SIZE))
    chunks = []
    for o in offs:
        if o + 8 > len(blob):
            raise ContainerError("chunk at 0x%X out of range" % o)
        name = blob[o:o + 4]
        size = struct.unpack_from("<I", blob, o + 4)[0]
        if o + 8 + size > len(blob):
            raise ContainerError("chunk %r size out of range" % name)
        chunks.append({"name": name, "off": o, "size": size,
                       "data": blob[o + 8:o + 8 + size]})
    return {"hash": h, "unknown": unknown, "total": total, "count": n,
            "chunks": chunks, "len": len(blob)}


def build(chunks, unknown=1):
    """按给定顺序拼一个新容器（chunk 尺寸任意），重算 total 与头部校验。

    chunks: [(name: bytes|str, data: bytes), ...]
    """
    items = []
    for name, data in chunks:
        if isinstance(name, str):
            name = name.encode("latin1")
        if len(name) != 4:
            raise ContainerError("chunk name must be 4 bytes: %r" % (name,))
        items.append((name, bytes(data)))
    if unknown != 1:
        raise ContainerError("unknown must be 1 (实测两游戏恒 1)")
    n = len(items)
    off = HEADER_SIZE + 4 * n
    offs = []
    body = bytearray()
    for name, data in items:
        offs.append(off)
        body += name + struct.pack("<I", len(data)) + data
        off += 8 + len(data)
    total = off
    out = bytearray()
    out += MAGIC + bytes(HASH_SIZE) + struct.pack("<III", unknown, total, n)
    out += struct.pack("<%dI" % n, *offs)
    out += body
    if len(out) != total:
        raise ContainerError("size mismatch")
    out[HASH_OFF:HASH_OFF + HASH_SIZE] = dxil_hash(bytes(out[BODY_OFF:]), "retail")
    return bytes(out)


def identity(blob):
    """原样重建（顺序/内容不变）——用于验证重建逻辑本身。"""
    c = parse(blob)
    return build([(k["name"], k["data"]) for k in c["chunks"]], c["unknown"])


def order(blob):
    """chunk 名字顺序，如 [b'SFI0', b'ISG1', ...]。"""
    return [k["name"] for k in parse(blob)["chunks"]]


def chunk(blob, name):
    """取某个 chunk 的内容；没有返回 None。"""
    if isinstance(name, str):
        name = name.encode("latin1")
    for k in parse(blob)["chunks"]:
        if k["name"] == name:
            return k["data"]
    return None


def replace(blob, name, data):
    """替换某个 chunk 的内容（**尺寸可变**），其余 chunk 原样保留。

    不校验语义；`HASH` chunk 不会自动重算（算法未解）。
    """
    if isinstance(name, str):
        name = name.encode("latin1")
    c = parse(blob)
    if not any(k["name"] == name for k in c["chunks"]):
        raise ContainerError("no chunk %r" % (name,))
    return build([(k["name"], data if k["name"] == name else k["data"])
                  for k in c["chunks"]], c["unknown"])


def diff_positions(a, b):
    """逐字节比较，返回不同位置（等长时）；长度不同返回 None。"""
    if len(a) != len(b):
        return None
    return [i for i in range(len(a)) if a[i] != b[i]]
