#!/usr/bin/env python3
"""RDEF 编辑 + blob 放回(供贴图槽新增使用)。

- add_bound_resource(blob, name, slot): 给 blob 的 RDEF 末尾(纹理组、cbuffer 之前)加一条
  bound resource(texture2d), 并重建 blob(RDEF size / chunk 偏移 / DXBC size / 指纹)。
  ⚠ 关键: 必须同步 variable 描述符的 field[4](默认值偏移), 否则 D3DReflect 报 E_INVALIDARG
  → 引擎反射失败 → 材质整片黑。
- replace_blob(data, blob_idx, new_blob): 把第 blob_idx 个 blob 换为新内容(长度可变),
  重映射头部所有 "blob 起点 / blob 大小" 引用。文件头 blob_start 不变(头部未改)。
"""
import struct
try:
    from .dxilhash import dxil_hash
except ImportError:  # 直接运行本文件时
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dxilhash import dxil_hash

TYPE_TEXTURE = 2
DIM_TEXTURE2D = 4
RET_FLOAT4 = 5
FLAGS_TEX = 0xC
NSAMP = 0xFFFFFFFF


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def blob_list(data):
    bs = _u32(data, 8)
    out = []
    off = bs
    while off + 4 <= len(data) and data[off:off + 4] == b"DXBC":
        size = _u32(data, off + 24)
        out.append((off, size))
        off += size
    return bs, out


def add_bound_resource(blob: bytes, name: str, slot: int, type_=TYPE_TEXTURE,
                       dim=DIM_TEXTURE2D, ret=RET_FLOAT4, flags=FLAGS_TEX,
                       nsamp=NSAMP):
    """给 RDEF bound resource 数组插入一条(texture2d, slot), 返回新 blob bytes。"""
    blob = bytearray(blob)
    n = _u32(blob, 28)
    ofs = [_u32(blob, 32 + i * 4) for i in range(n)]
    rdef_ci = next((co for co in ofs if blob[co:co + 4] == b"RDEF"), None)
    if rdef_ci is None:
        raise ValueError("blob has no RDEF")
    cs = _u32(blob, rdef_ci + 4)
    rdef = bytearray(blob[rdef_ci + 8:rdef_ci + 8 + cs])

    n_cb, cb_off, n_br, br_off, target = struct.unpack_from("<IIIII", rdef, 0)
    # 插入点: 第一个 cbuffer(名 type=0)之前, 保持 sampler->texture->cbuffer 顺序
    first_cb = n_br
    for k in range(n_br):
        if _u32(rdef, br_off + k * 32 + 4) == 0:
            first_cb = k
            break
    ins = br_off + first_cb * 32
    name_pos = _u32(rdef, br_off + first_cb * 32) if first_cb < n_br else br_off + n_br * 32
    nb = name.encode("ascii") + b"\x00"
    LD = len(nb)
    new_name_off = name_pos + 32
    entry = struct.pack("<IIIIIIII", new_name_off, type_, ret, dim, nsamp, slot, 1, flags)

    out = bytearray()
    out += rdef[:ins]
    out += entry
    out += rdef[ins:name_pos]
    out += nb
    out += rdef[name_pos:]

    def delta(v):
        if v >= name_pos:
            return 32 + LD
        if v >= ins:
            return 32
        return 0

    def shift(fo):
        v = _u32(out, fo)
        d = delta(v)
        if d:
            struct.pack_into("<I", out, fo, v + d)

    shift(4)     # ConstantBufferOffset
    shift(24)    # CreatorOffset
    for k in range(n_br):
        op = br_off + k * 32
        shift(op if k < first_cb else op + 32)      # bound resource 名字
    cb_off2 = cb_off + 32 + LD                       # cbuffer 定义整体后移
    for k in range(n_cb):
        p = cb_off2 + k * 24
        shift(p)                                     # cbuffer 名字
        vc = _u32(out, p + 4)
        vo = _u32(out, p + 8)
        vo2 = vo + delta(vo)                         # varOff 也指向插入点之后
        struct.pack_into("<I", out, p + 8, vo2)
        for vv in range(vc):
            vp = vo2 + vv * 40
            shift(vp)            # 变量名
            shift(vp + 16)       # variable 描述符 field[4](默认值偏移)
    struct.pack_into("<I", out, 8, n_br + 1)         # bound resource 数量 +1

    # 重建 blob
    d = len(out) - cs
    new_blob = bytearray()
    new_blob += blob[:32]
    new_blob += b"\x00" * (n * 4)
    new_ofs = [co + d if co > rdef_ci else co for co in ofs]
    struct.pack_into("<" + "I" * n, new_blob, 32, *new_ofs)
    for co, sz in sorted((co, _u32(blob, co + 4)) for co in ofs):
        if co == rdef_ci:
            new_blob += b"RDEF" + struct.pack("<I", len(out)) + out
        else:
            new_blob += blob[co:co + 8 + sz]
    struct.pack_into("<I", new_blob, 24, len(new_blob))
    new_blob[4:20] = dxil_hash(bytes(new_blob[20:]), "retail")
    return bytes(new_blob)


def replace_blob(data: bytes, blob_idx: int, new_blob: bytes) -> bytes:
    """把第 blob_idx 个 blob 换为 new_blob(长度可变), 重映射头部引用。"""
    bs, bl = blob_list(data)
    if not (0 <= blob_idx < len(bl)):
        raise ValueError("blob idx out of range")
    head = bytearray(data[:bs])
    new_blobs = []
    old_to_new, old_sz_to_new = {}, {}
    cursor = bs
    for i, (start, size) in enumerate(bl):
        raw = new_blob if i == blob_idx else data[start:start + size]
        sz = len(raw)
        new_blobs.append(raw)
        old_to_new[start] = cursor
        if sz != size:
            old_sz_to_new[size] = sz
        cursor += sz
    old_starts = set(old_to_new)
    old_sizes = set(old_sz_to_new)
    for i in range(0, len(head) - 3, 4):
        w = _u32(head, i)
        if w in old_starts:
            struct.pack_into("<I", head, i, old_to_new[w])
        elif w in old_sizes:
            struct.pack_into("<I", head, i, old_sz_to_new[w])
    out = bytearray(head)
    for raw in new_blobs:
        out += raw
    return bytes(out)
