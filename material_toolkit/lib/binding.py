#!/usr/bin/env python3
"""mmtr 资源绑定(纹理槽)编辑库: 发现变体组 + 新增/重命名纹理槽。

关键事实(2026-09-22 实机验证, 详见 PROJECT_SUMMARY §六 / analysis §10):
  - 同一 shader 程序(blob) 在 header 里对应**多组** (描述符,池) 绑定(不同 pass);
    **新增/改名必须对该 blob 的"全部组"都做**, 只改一组(非引擎所用那组)无效。
  - **绑定键 = header 池名**(非 blob 内 RDEF 名)。
  - 池/描述符在 header 里连续堆叠; 插入会移动后续所有内容 ⇒ 必须重映射全部指针。
  - 记录计数: +0xa4=该 blob 资源总数+1、+0xb8/+0xcc=SRV 数(加槽时三者同时 +1);
    改动只针对**目标 blob 的记录**(同组其它 blob 的变体不动)。

结构(实测):
  变体记录 264B: +0x00 blob偏移; +0x9c blob大小; +0x58 描述符指针; +0x60 池指针;
                 +0xa4/+0xac/+0xb0/+0xb8/+0xcc 计数。
  池条目 16B: [name_off(u64)][hash(u32)][0(u32)]。
  描述符 8B: (code,0), code=(type<<24)|(stage<<16)|slot; 记录里 +0x58 指向"首条前 4B 头"。
"""
import struct

try:
    from .hashes import ascii_hash
except ImportError:  # 允许脚本直接 import
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hashes import ascii_hash

TYPE_TEX2D = 0x02
TYPE_RAW = 0x80
STAGE_PS = 0x10


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def _u64(d, o):
    return struct.unpack_from("<Q", d, o)[0]


def _str(d, o, n=64):
    return d[o:o + n].split(b"\x00")[0].decode("latin1")


def blob_list(data):
    """返回 (blob_start, [(offset, size), ...])。"""
    bs = _u32(data, 8)
    out = []
    off = bs
    while off + 4 <= len(data) and data[off:off + 4] == b"DXBC":
        size = _u32(data, off + 24)
        out.append((off, size))
        off += size
    return bs, out


def blob_offsets(data):
    return [o for o, _ in blob_list(data)[1]]


def discover_groups(data, blob_off):
    """返回该 blob 引用的所有唯一 (desc_ptr, pool_ptr) -> [记录偏移, ...]。

    组 = 一个"池+描述符"绑定; 同一 shader 程序可有多个组(不同 pass)。
    """
    bs, bl = blob_list(data)
    size_of = dict(bl)
    groups = {}
    for p in range(0, bs - 3, 4):
        if _u32(data, p) == blob_off and _u32(data, p + 0x9c) == size_of.get(blob_off):
            key = (_u32(data, p + 0x58), _u32(data, p + 0x60))
            groups.setdefault(key, []).append(p)
    return groups


def _pool_entries(data, pool, count, maxn=64):
    """读 count 条池条目(name_off(u64),hash(u32),0), 校验 hash -> [(name, hash), ...]。"""
    bs = _u32(data, 8)
    res = []
    for i in range(min(count, maxn)):
        off = pool + i * 16
        if off + 16 > len(data):
            break
        no = _u64(data, off)
        h = _u32(data, off + 8)
        if not (0x1000 <= no < bs):
            break
        nm = _str(data, no)
        if not nm:
            break
        res.append((nm, h))
    return res


def _srvs(data, desc, pool, count):
    """把池条目与描述符按索引对齐 -> [{idx,type,slot,name,hash}, ...](共 count 条)。"""
    out = []
    for i, (nm, h) in enumerate(_pool_entries(data, pool, count)):
        code = _u32(data, desc + 4 + i * 8)
        out.append({"idx": i, "type": (code >> 24) & 0xff,
                    "slot": code & 0xffff, "name": nm, "hash": h})
    return out


def group_summary(data, blob_idx):
    """列出某 blob 的所有绑定组(含池名/槽位, 供 CLI/dump)。"""
    bs, bl = blob_list(data)
    if not (0 <= blob_idx < len(bl)):
        raise ValueError(f"blob idx {blob_idx} out of range (0..{len(bl) - 1})")
    blob_off = bl[blob_idx][0]
    out = []
    for (desc, pool), recs in sorted(discover_groups(data, blob_off).items(),
                                     key=lambda kv: kv[0][1]):
        r0 = recs[0]
        out.append({
            "desc": desc, "pool": pool, "n_rec": len(recs),
            "a4": _u32(data, r0 + 0xa4), "ac": _u32(data, r0 + 0xac),
            "b8": _u32(data, r0 + 0xb8), "cc": _u32(data, r0 + 0xcc),
            "srvs": _srvs(data, desc, pool, _u32(data, r0 + 0xcc)),
        })
    return out


def _splice_and_remap(data, inserts):
    """在 header 内插入(offset, bytes), 并重映射 header 内所有指针。

    inserts: [(offset, bytes), ...](可乱序); offset 需在 [0, blob_start)。
    返回新的完整文件 bytes。规则: 老值 v -> v + sum(len(b) for o,b in inserts if o<=v);
    blob 绝对偏移(>=blob_start 的已知起点) -> +total。
    """
    bs, bl = blob_list(data)
    old_starts = set(o for o, _ in bl)
    inserts = sorted(inserts, key=lambda t: t[0])
    total = sum(len(b) for _, b in inserts)
    first = inserts[0][0]

    def remap(v):
        if v in old_starts:
            return v + total
        if first <= v < bs:
            return v + sum(len(b) for o, b in inserts if o <= v)
        return v

    hdr = bytearray(data[:bs])
    for i in range(0, len(hdr) - 3, 4):
        v = _u32(hdr, i)
        nv = remap(v)
        if nv != v:
            struct.pack_into("<I", hdr, i, nv)
    # 文件头 blob_start 字段(offset 8) 若因数据被覆盖/或未命中集合, 显式修正
    struct.pack_into("<I", hdr, 8, bs + total)

    out = bytearray()
    prev = 0
    for o, b in inserts:
        out += hdr[prev:o]
        out += b
        prev = o
    out += hdr[prev:]
    out += data[bs:]
    return bytes(out)


def _inc_count(buf, blob_off, fields=(0xa4, 0xb8, 0xcc), recs_only=None):
    """对 u32(p)==blob_off 的记录计数 +1(就地, 在 splice 之前调用)。

    recs_only: 若非 None, 只对其中包含的记录偏移 +1(逐组实验用)。
    """
    bs = _u32(buf, 8)
    n = 0
    for p in range(0, bs - 3, 4):
        if _u32(buf, p) == blob_off and (recs_only is None or p in recs_only):
            for fo in fields:
                struct.pack_into("<I", buf, p + fo, _u32(buf, p + fo) + 1)
            n += 1
    return n


def _tex_indices(data, desc, type_=TYPE_TEX2D, window=12):
    """描述符数组中同类型纹理条目的索引(从 desc+4 起, 遇到 cbuffer(0xff) 停)。"""
    idxs = []
    for i in range(window):
        code = _u32(data, desc + 4 + i * 8)
        t = (code >> 24) & 0xff
        if t == 0xff:
            break
        if t == type_:
            idxs.append(i)
    return idxs


def add_texture_slot(data: bytes, blob_idx: int, name: str, slot=None,
                     type_=TYPE_TEX2D, stage=STAGE_PS, reuse_name=True, rdef=True,
                     only_indices=None):
    """给指定 blob 的绑定组新增一个纹理槽, 返回新文件 bytes。

    name: 池名(绑定键)。若 mmtr 内已存在则原地复用; 否则追加到字符串区末尾。
    slot: t 序号; None=取各组"最后一个 texture2d"之后的下一个。
    rdef: True(默认)同时对**目标 blob 的 RDEF** 加同名 bound resource
          (缺它则 SHEX/RDEF 不一致 → D3DReflect 非法 → 材质黑)。
    only_indices: None=所有组(默认, 面向引擎"多组须一致"); 或指定"按池偏移排序后的
          组下标"子集(逐组实验用, 只改某几组; 计数也只对所选组的记录 +1)。
    """
    data = bytes(data)
    bs, bl = blob_list(data)
    if not (0 <= blob_idx < len(bl)):
        raise ValueError("blob idx out of range")
    blob_off = bl[blob_idx][0]
    groups = discover_groups(data, blob_off)
    if not groups:
        raise ValueError(f"no binding group found for blob {blob_idx}")
    sel = None
    if only_indices is not None:
        sel = set(only_indices)
        ordered = sorted(groups.items(), key=lambda kv: kv[0][1])
        groups = dict(ordered[i] for i in range(len(ordered)) if i in sel)
        if not groups:
            raise ValueError(f"only_indices {sorted(sel)} select no group (have {len(ordered)})")

    # 新槽 slot: 取各组"已有 texture2d 最大 slot + 1"的最大值(各组应一致)
    if slot is None:
        slot = max(max((_u32(data, desc + 4 + i * 8) & 0xffff)
                       for i in _tex_indices(data, desc)) + 1
                   for (desc, pool) in groups)

    # 先改 RDEF(目标 blob), 再放回(blob 变长 -> 重映射头部引用)
    if rdef:
        from .rdef import add_bound_resource, replace_blob
        blob_size = bl[blob_idx][1]
        new_blob = add_bound_resource(data[blob_off:blob_off + blob_size], name, slot, type_=type_)
        data = replace_blob(data, blob_idx, new_blob)
        bs, bl = blob_list(data)
        blob_off = bl[blob_idx][0]
        groups = discover_groups(data, blob_off)
        if sel is not None:
            ordered = sorted(groups.items(), key=lambda kv: kv[0][1])
            groups = dict(ordered[i] for i in range(len(ordered)) if i in sel)

    found = data.find(name.encode("ascii") + b"\x00", 0, bs)
    append = (found < 0) or (not reuse_name)
    hash_ = ascii_hash(name)
    name_bytes = name.encode("ascii") + b"\x00" if append else b""

    # 各组插入点(池/描述符同索引); 同一数组可能被多组共用 -> 按偏移去重
    pool_ins, desc_ins = {}, {}
    for (desc, pool), recs in groups.items():
        idxs = _tex_indices(data, desc)
        if not idxs:
            raise ValueError(f"no texture2d desc@0x{desc:x}")
        idx = idxs[-1] + 1
        pool_ins.setdefault(pool + idx * 16, slot)
        desc_ins.setdefault(desc + 4 + idx * 8, slot)

    # 新名字落位(考虑插入造成的位移)
    sizes = [(o, 16) for o in pool_ins] + [(o, 8) for o in desc_ins]
    if append:
        sizes_all = sizes + [(bs, len(name_bytes))]
        new_name_off = bs + sum(sz for o, sz in sizes_all if o < bs)
    else:
        new_name_off = found + sum(sz for o, sz in sizes if o <= found)

    pool_bytes = struct.pack("<QII", new_name_off, hash_, 0)
    inserts = []
    for off in pool_ins:
        inserts.append((off, pool_bytes))
    for off, sv in desc_ins.items():
        inserts.append((off, struct.pack("<II", (type_ << 24) | (stage << 16) | sv, 0)))
    if append:
        inserts.append((bs, name_bytes))

    # 先落计数(在 splice 之前, 此时记录还在原偏移); 只对"所选组"的记录 +1
    target_recs = set(p for recs in groups.values() for p in recs)
    counted = bytearray(data)
    _inc_count(counted, blob_off, recs_only=target_recs)
    out = _splice_and_remap(bytes(counted), inserts)
    return bytes(out)


def rename_slot(data: bytes, blob_idx: int, slot: int, new_name: str, reuse_name=True):
    """把指定 blob 的**所有组**中 slot==slot 的纹理槽改名(池名+hash)。

    绑定键=池名 ⇒ 改名即改绑定的贴图类型; 不改 RDEF。返回新文件 bytes。
    """
    data = bytes(data)
    bs, bl = blob_list(data)
    if not (0 <= blob_idx < len(bl)):
        raise ValueError("blob idx out of range")
    blob_off = bl[blob_idx][0]
    groups = discover_groups(data, blob_off)
    if not groups:
        raise ValueError("no binding group found")

    found = data.find(new_name.encode("ascii") + b"\x00", 0, bs)
    append = (found < 0) or (not reuse_name)
    hash_ = ascii_hash(new_name)
    name_bytes = new_name.encode("ascii") + b"\x00" if append else b""

    edits = []   # 池条目偏移(name_off 字段位置)
    for (desc, pool), recs in groups.items():
        for i in _tex_indices(data, desc):
            code = _u32(data, desc + 4 + i * 8)
            if (code & 0xffff) == slot:
                edits.append(pool + i * 16)
                break
    if not edits:
        raise ValueError(f"slot t{slot} not found in any group of blob {blob_idx}")

    if append:
        inserts = [(bs, name_bytes)]
        new_name_off = bs
        out = bytearray(_splice_and_remap(data, inserts))
    else:
        new_name_off = found
        out = bytearray(data)

    for eo in edits:
        struct.pack_into("<Q", out, eo, new_name_off)
        struct.pack_into("<I", out, eo + 8, hash_)
    return bytes(out)


def _all_groups(data, blobs=None):
    """单遍枚举所有“有效绑定组”: yield (blob_idx, desc, pool, first_rec_off)。

    仅取“程序指针指向真实 blob 且 +0x9c 与 blob 大小一致”的记录(与 discover_groups 同口径);
    按 (blob_idx, desc, pool) 去重(取首见记录, 与 group_summary 的 recs[0] 一致)。
    blobs=None 全部; 传下标集合则只看这些 blob。
    O(header) 单遍, 比“逐 blob 调 discover_groups”快得多(后者是 GUI 卡顿的根源之一)。
    """
    bs, bl = blob_list(data)
    size_of = dict(bl)
    at = {o: i for i, (o, _s) in enumerate(bl)}
    want = None if blobs is None else set(blobs)
    seen = set()
    for p in range(0, bs - 3, 4):
        v = _u32(data, p)
        idx = at.get(v)
        if idx is None or (want is not None and idx not in want):
            continue
        if _u32(data, p + 0x9C) != size_of[v]:
            continue
        key = (idx, _u32(data, p + 0x58), _u32(data, p + 0x60))
        if key in seen:
            continue
        seen.add(key)
        yield idx, key[1], key[2], p


def name_vocabulary(data):
    """文件内所有绑定组用到的 SRV 名字 -> {name: {"groups": 组数, "blobs": set(blob_idx)}}。

    绑定键=池名 ⇒ 这个名字表就是“该材质用到的所有贴图类型”。单遍枚举(见 _all_groups)。
    """
    vocab = {}
    for idx, _desc, pool, rec0 in _all_groups(data):
        n = _u32(data, rec0 + 0xCC)
        for nm, _h in _pool_entries(data, pool, n):
            d = vocab.setdefault(nm, {"groups": 0, "blobs": set()})
            d["groups"] += 1
            d["blobs"].add(idx)
    return vocab


def rename_name_global(data, old_name, new_name, reuse_name=True, blobs=None):
    """把**指定范围内**绑定组里名字==old_name 的池条目改名为 new_name(按名字, 与类型无关)。

    绑定键=池名 ⇒ 按名改名。blobs=None 时全文件; 传 [blob_idx,...] 时只改这些 blob 的组
    (raw/tex2d 都能改, 不像 rename_slot 只认 texture2d 且 slot 不唯一)。
    新名若文件内已存在(且 reuse_name)则复用其串; 否则追加到字符串区末尾(带全量重映射, 文件变长)。
    返回新文件 bytes(未命中任何条目则原样返回)。
    """
    data = bytes(data)
    _bs, _bl = blob_list(data)
    found = data.find(new_name.encode("ascii") + b"\x00", 0, _bs)
    append = (found < 0) or (not reuse_name)

    # 收集所有需改的池条目偏移(以 (pool, idx) 定位, 去重); 单遍枚举
    edits = set()
    for _idx, _desc, pool, rec0 in _all_groups(data, blobs):
        n = _u32(data, rec0 + 0xCC)
        for i, (nm, _h) in enumerate(_pool_entries(data, pool, n)):
            if nm == old_name:
                edits.add(pool + i * 16)
    if not edits:
        return data

    hash_ = ascii_hash(new_name)
    if append:
        name_bytes = new_name.encode("ascii") + b"\x00"
        out = bytearray(_splice_and_remap(data, [(_bs, name_bytes)]))
        new_name_off = _bs
    else:
        out = bytearray(data)
        new_name_off = found

    for eo in edits:
        struct.pack_into("<Q", out, eo, new_name_off)
        struct.pack_into("<I", out, eo + 8, hash_)
    return bytes(out)


if __name__ == "__main__":
    import sys
    d = open(sys.argv[1], "rb").read()
    idx = int(sys.argv[2])
    print(f"blob {idx} groups:")
    for g in group_summary(d, idx):
        print(f"  desc@0x{g['desc']:x} pool@0x{g['pool']:x} n_rec={g['n_rec']} "
              f"a4={g['a4']} ac={g['ac']} b8={g['b8']} cc={g['cc']}")
