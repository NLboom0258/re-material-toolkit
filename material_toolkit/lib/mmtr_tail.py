#!/usr/bin/env python3
"""mmtr 尾段(L4)结构化模型: 名称池 / cbuffer 表 / 参数表 / 描述符区 / 字符串池。

用于"从 0 构建 mmtr"(路线 B)的内容层。布局(version 0x01100004, 见
analysis/mmtr_from_scratch_design.md §2 / SKILL):

  [0x46350, PoolStart)  可选前缀(个别文件在 0x46350 有 16B 非池数据, 如 env_massblood_test)
  [PoolStart, CbStart)  名称池(16B/条): [name_off u64][hash u32][0 u32]  (PoolStart 通常=0x46350)
  [CbStart, PStart)     cbuffer 表(32B/条): [name_off u64][hash u32][0 u32][size u32][count u32][members_off u64]
  [PStart, PEnd)        参数表(16B/条): [name_off u32][0 u32][hash u32][(size<<16)|offset]
  [PEnd, StrStart)      描述符区: [可选 4B 头(区len%8)][N×8B 条目]; 条目=[a u32][code u32],
                        code=(type<<24)|(stage<<16)|slot; 每程序 = cb n_cb | smp n_smp | tex n_srv 连续条目段
  [StrStart, blob_start) 字符串池(ASCII, NUL 结尾)

说明:
  - 名称池/cbuffer/参数/字符串 已结构化解码; 描述符区暂以原始字节保留(待细化)。
  - encode(decode(x)) == x 是硬性判据(逐字节往返), 见 scripts/test_mmtr_tail.py。
"""
import struct

try:
    from .hashes import ascii_hash
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hashes import ascii_hash

SKELETON_HI = 0x46350


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def _ascii(b, o, n=128):
    e = o
    while e < len(b) and b[e] != 0 and e - o < n:
        e += 1
    return b[o:e].decode("latin1", "replace")


def _is_param(b, p, blob_start):
    """16B 参数条目判据。"""
    name_off, z, h, meta = struct.unpack_from("<IIII", b, p)
    if z != 0 or not (0x1000 <= name_off < blob_start):
        return False
    nm = _ascii(b, name_off)
    return bool(nm) and ascii_hash(nm) == h and (meta >> 16) != 0


def _is_pool(b, p, blob_start):
    """16B 名称池条目判据。"""
    name_off = _u64(b, p)
    if not (0x1000 <= name_off < blob_start):
        return False
    nm = _ascii(b, name_off)
    return bool(nm) and ascii_hash(nm) == _u32(b, p + 8) and _u32(b, p + 12) == 0


def _is_cbuffer(b, p, blob_start):
    """32B cbuffer 条目判据(含 members_off 指向合法参数条目)。"""
    name_off = _u64(b, p)
    if not (0x1000 <= name_off < blob_start):
        return False
    nm = _ascii(b, name_off)
    if not nm or ascii_hash(nm) != _u32(b, p + 8) or _u32(b, p + 12) != 0:
        return False
    mo = _u64(b, p + 24)
    if not (0x1000 < mo < blob_start) or not _is_param(b, mo, blob_start):
        return False
    return True


class TailModel:
    """尾段结构化模型。sections 保序; encode() = 各段原样拼接(往返逐字节一致)。"""

    def __init__(self):
        self.blob_start = 0
        self.boundaries = {}     # name -> off
        self.sections = {}       # name -> bytes
        self.pool_entries = []       # [(off, name_off, hash, name)]
        self.cbuffer_entries = []    # [dict(off,name_off,hash,name,size,count,members_off)]
        self.param_entries = []      # [(off, name_off, hash, size, offset, name)]
        self.strings = []            # [(off, value)]
        self.desc_head = b""         # 描述符区 4B 头
        self.desc_entries = []       # [(a u32, code u32)]

    @classmethod
    def decode(cls, data: bytes) -> "TailModel":
        t = cls()
        blob_start = _u32(data, 8)
        t.blob_start = blob_start
        rec_lo, rec_n, rec_size = 0x568, (0x46240 - 0x568) // 264, 264

        # 锚点(记录里的绝对指针): +0x40=cbuffer 表条目; +0x38/+0x48/+0x58=描述符数组
        cb_anchor, desc_anchor = [], []
        for i in range(rec_n):
            off = rec_lo + i * rec_size
            v = _u32(data, off + 0x40)
            if 0x1000 <= v < blob_start:
                cb_anchor.append(v)
            for fo in (0x38, 0x48, 0x58):
                v = _u32(data, off + fo)
                if 0x1000 <= v < blob_start:
                    desc_anchor.append(v)

        cb_start = min(cb_anchor) if cb_anchor else \
            min((p for p in range(SKELETON_HI, blob_start - 32, 4)
                 if _is_cbuffer(data, p, blob_start)), default=None)
        if cb_start is None:
            raise ValueError("无法定位 cbuffer 表起点")

        # 参数表起点: cbuffer 条目的 members_off ∪ 扫描到的首个参数条目
        mem = [_u64(data, p + 24) for p in range(cb_start, blob_start - 32, 32)
               if _is_cbuffer(data, p, blob_start)]
        cands = list(mem)
        first_param = next((p for p in range(cb_start, blob_start - 16, 4)
                            if _is_param(data, p, blob_start)), None)
        if first_param is not None:
            cands.append(first_param)
        param_start = min(cands) if cands else None
        if param_start is None:
            raise ValueError("无法定位参数表起点")

        # 描述符区起点 = 最早 desc 锚点(回退: 参数表末尾)
        if desc_anchor:
            desc_start = min(desc_anchor)
        else:
            desc_start = param_start
            while desc_start + 16 <= blob_start and _is_param(data, desc_start, blob_start):
                desc_start += 16

        # 字符串池起点: 所有名字引用的最小值
        refs = set()
        refs.add(_u32(data, 0x10))
        for i in range(5):
            refs.add(_u32(data, 0x14 + i * 264 + 0x104))
        for p in range(SKELETON_HI, cb_start, 4):
            if _is_pool(data, p, blob_start):
                refs.add(_u64(data, p))
        for p in range(cb_start, param_start, 4):
            if _is_cbuffer(data, p, blob_start):
                refs.add(_u64(data, p))
        for p in range(param_start, desc_start, 16):
            refs.add(_u32(data, p))
        for i in range(rec_n):
            refs.add(_u32(data, rec_lo + i * rec_size + 0xD8))
        refs = {r for r in refs if 0x1000 <= r < blob_start}
        str_start = min(refs)

        # 名称池起点: [0x46350, cb_start) 内第一个"后续槽位全为合法 pool 条目"的 16B 边界
        # (部分文件在 0x46350 处有 16B 非池前缀, 如 env_massblood_test)
        pool_start = None
        for p in range(SKELETON_HI, cb_start, 16):
            if (cb_start - p) % 16 == 0 and all(
                    _is_pool(data, q, blob_start) for q in range(p, cb_start, 16)):
                pool_start = p
                break
        if pool_start is None:
            pool_start = SKELETON_HI

        t.boundaries = {"tail_hi": SKELETON_HI, "pool": pool_start, "cbuffer": cb_start,
                        "param": param_start, "desc": desc_start,
                        "string": str_start, "blob_start": blob_start}
        t.sections = {
            "prefix": bytes(data[SKELETON_HI:pool_start]),
            "pool": bytes(data[pool_start:cb_start]),
            "cbuffer": bytes(data[cb_start:param_start]),
            "param": bytes(data[param_start:desc_start]),
            "desc": bytes(data[desc_start:str_start]),
            "string": bytes(data[str_start:blob_start]),
        }
        t._parse(data, blob_start, pool_start, cb_start, param_start, desc_start, str_start)
        return t

    def _parse(self, data, bs, pool_start, cb_start, p_start, p_end, str_start):
        for p in range(pool_start, cb_start, 16):
            if _is_pool(data, p, bs):
                t = (p, _u64(data, p), _u32(data, p + 8), _ascii(data, _u64(data, p)))
                self.pool_entries.append(t)
        for p in range(cb_start, p_start, 32):
            if _is_cbuffer(data, p, bs):
                self.cbuffer_entries.append({
                    "off": p, "name_off": _u64(data, p), "hash": _u32(data, p + 8),
                    "name": _ascii(data, _u64(data, p)),
                    "size": _u32(data, p + 16), "count": _u32(data, p + 20),
                    "members_off": _u64(data, p + 24)})
        for p in range(p_start, p_end, 16):
            if _is_param(data, p, bs):
                name_off, _z, h, meta = struct.unpack_from("<IIII", data, p)
                self.param_entries.append((p, name_off, h, meta >> 16, meta & 0xFFFF,
                                           _ascii(data, name_off)))
        q = str_start
        while q < bs:
            s = _ascii(data, q)
            self.strings.append((q, s))
            q += len(s) + 1
        # 描述符区: [可选 4B 头][N×8B 条目 [a,code]]; 头长 = 区 len % 8 (0 或 4)
        d = self.sections["desc"]
        head_len = len(d) % 8
        self.desc_head = d[:head_len]
        for i in range((len(d) - head_len) // 8):
            self.desc_entries.append(struct.unpack_from("<II", d, head_len + 8 * i))

    # ---- 由解析条目无损重建各段(用于证明模型可构造) ----
    def rebuild_pool(self):
        return b"".join(struct.pack("<QII", no, h, 0) for _, no, h, _ in self.pool_entries)

    def rebuild_cbuffer(self):
        return b"".join(struct.pack("<QIIIIQ", e["name_off"], e["hash"], 0,
                                    e["size"], e["count"], e["members_off"])
                        for e in self.cbuffer_entries)

    def rebuild_param(self):
        return b"".join(struct.pack("<IIII", no, 0, h, (sz << 16) | off)
                        for _, no, h, sz, off, _ in self.param_entries)

    def rebuild_desc(self):
        return self.desc_head + b"".join(struct.pack("<II", a, c)
                                         for a, c in self.desc_entries)

    def lossless(self):
        """各段“由解析条目重建”是否与原始字节一致(证明结构化解析无损)。"""
        return (self.rebuild_pool() == self.sections["pool"] and
                self.rebuild_cbuffer() == self.sections["cbuffer"] and
                self.rebuild_param() == self.sections["param"] and
                self.rebuild_desc() == self.sections["desc"])

    def encode(self) -> bytes:
        """尾段字节 = 各段拼接(保序)。"""
        return (self.sections["prefix"] + self.sections["pool"] +
                self.sections["cbuffer"] + self.sections["param"] +
                self.sections["desc"] + self.sections["string"])

    def dump(self):
        b = self.boundaries
        print(f"  tail sections: pool=0x{b['pool']:x} cbuffer=0x{b['cbuffer']:x} "
              f"param=0x{b['param']:x} desc=0x{b['desc']:x} string=0x{b['string']:x} "
              f"blob_start=0x{b['blob_start']:x}")
        print(f"    sizes: " + " ".join(
            f"{k}=0x{len(v):x}" for k, v in self.sections.items()))
        print(f"    pools={len(self.pool_entries)} cbuffers={len(self.cbuffer_entries)} "
              f"params={len(self.param_entries)} desc={len(self.desc_entries)} "
              f"strings={len(self.strings)}")


def build_desc(programs, head=bytes(4)):
    """从“程序列表”合成描述符区(供从 0 构建用)。

    programs: [{"cb": [code, ...], "smp": [code, ...], "tex": [code, ...]}, ...]
      其中 code = (type<<24) | (stage<<16) | slot。
    布局(我们自定的自洽排列, 不模仿原文件的 dedup/对齐): 
      [head(4B)] + 逐程序顺序拼接 [cb n_cb][smp n_smp][tex n_srv] 的 8B 条目(每条=[a=0][code])。
    返回 (bytes, placement): placement[i] = {"cb":off, "smp":off, "tex":off} (相对区起点)。
    记录只需把 +0x38/+0x48/+0x58 设成对应 off(加上区起点绝对偏移)、
    +0xac/+0xb0/+0xcc 设成对应计数即可。
    """
    out = bytearray(head)
    placement = []
    for p in programs:
        pos = {}
        for kind in ("cb", "smp", "tex"):
            pos[kind] = len(out)
            for code in p.get(kind, ()):
                out += struct.pack("<II", 0, code & 0xFFFFFFFF)
        placement.append(pos)
    return bytes(out), placement


if __name__ == "__main__":
    import sys
    d = open(sys.argv[1], "rb").read()
    TailModel.decode(d).dump()
