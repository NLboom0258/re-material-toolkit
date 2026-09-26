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

    def rebuild_string(self):
        return b"".join(s.encode("latin1") + b"\x00" for _, s in self.strings)

    def lossless(self):
        """各段“由解析条目重建”是否与原始字节一致(证明结构化解析无损)。"""
        return (self.rebuild_pool() == self.sections["pool"] and
                self.rebuild_cbuffer() == self.sections["cbuffer"] and
                self.rebuild_param() == self.sections["param"] and
                self.rebuild_desc() == self.sections["desc"] and
                self.rebuild_string() == self.sections["string"])

    def encode(self) -> bytes:
        """尾段字节 = 各段拼接; pool/cbuffer/param/desc/string 均由解析条目重建。"""
        return (self.sections["prefix"] + self.rebuild_pool() +
                self.rebuild_cbuffer() + self.rebuild_param() +
                self.rebuild_desc() + self.rebuild_string())

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


# --------------------------------------------------------------- 绑定组: 由 RDEF 派生(验证)
# 实测规则(analysis/pass_matrix.md §10): 记录的"绑定组"(池/表名列表) = 
#   VS 资源(RDEF 数组序) ++ PS 资源(RDEF 数组序), 按类(cb/smp/tex)去重。
# 这使尾段的"绑定层"可由程序(blob)的 RDEF 完全派生。

def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def derive_group(vs_blob, ps_blob):
    """由 VS/PS 的 RDEF 派生绑定组: {kind: [name...]}(VS序 ++ PS序 按类去重)。

    注: 尾段池只收 **SRV**(含 STRUCTURED/BYTEADDRESS), **不含 UAV**(如 Pick 的
    `PickAddressList*`); 去 UAV 已由 `rdef.rdef_bind_order` 按 type 处理。
    无 RDEF 的程序当空。返回 {"cb": [...], "smp": [...], "tex": [...]}。
    """
    try:
        from .rdef import rdef_bind_order
    except ImportError:
        from rdef import rdef_bind_order
    empty = {"cb": [], "smp": [], "tex": []}
    vb = (rdef_bind_order(vs_blob) if vs_blob else None) or empty
    pb = (rdef_bind_order(ps_blob) if ps_blob else None) or empty
    return {k: _dedup(vb[k] + pb[k]) for k in ("cb", "smp", "tex")}


def record_group(data, slot, tail=None):
    """读**本题(donor)**中某记录的绑定组(从尾段池/表切片取名字)。

    tail 传已经 decode 的 TailModel(批量时复用, 避免重复解析)。
    返回 {"cb": [...], "smp": [...], "tex": [...]}; 空槽返回全空。用于与 `derive_group` 比对。
    """
    try:
        from .mmtr_build import MmtrImage, REC_LO, REC_SIZE
    except ImportError:
        from mmtr_build import MmtrImage, REC_LO, REC_SIZE
    t = tail if tail is not None else TailModel.decode(data)
    img = MmtrImage.from_bytes(data)
    bs = t.boundaries

    def names(off, n, is_cb):
        base = bs["cbuffer"] if is_cb else bs["pool"]
        step = 32 if is_cb else 16
        k = off - base
        if not off or k < 0 or k % step:
            return []
        if is_cb:
            return [t.cbuffer_entries[(k // 32) + q]["name"] for q in range(n)
                    if (k // 32) + q < len(t.cbuffer_entries)]
        return [t.pool_entries[(k // 16) + q][3] for q in range(n)
                if (k // 16) + q < len(t.pool_entries)]

    c6 = (img.rec_field(slot, 0xC4) >> 16) & 0xFF
    nsmp = img.rec_field(slot, 0xB0) >> 16
    ntex = img.buf[REC_LO + slot * REC_SIZE + 0xCC]
    return {"cb": names(img.rec_field(slot, 0x40), c6, True),
            "smp": names(img.rec_field(slot, 0x50), nsmp, False),
            "tex": names(img.rec_field(slot, 0x60), ntex, False)}


# tex 描述符 code 的高字节(type)由 RDEF dimension 决定(观测自 env; 见 §10)
_TEX_DIM_TYPE = {1: 0x80, 3: 0x05, 4: 0x02, 5: 0x06, 8: 0x03, 9: 0x04}


def _bind_info_map(blob):
    """RDEF bound resources -> {name: (type, bind_point, dim)}。"""
    try:
        from .rdef import rdef_bind_info
    except ImportError:
        from rdef import rdef_bind_info
    info = rdef_bind_info(blob) if blob else None
    if not info:
        return {}
    return {nm: (t, bp, dim) for nm, t, bp, dim, _ret in info}


def check_desc(data):
    """校验每条记录的**绑定描述符条目** (u0,code) 是否 = RDEF 派生值。

    期望: `u0`=VS 绑定寄存器; `code`=`(type<<24)|(stage<<16)|PS绑定寄存器`;
    `stage` 掩码 `0x01`=VS/`0x10`=PS/`0x11`=共享; type: cb=0xff / smp=0x00 /
    tex=由 RDEF dimension 映射(`_TEX_DIM_TYPE`; 未知维只比 bp/stage)。
    返回 (n_checked, mismatches); mismatches = [(slot,kind,name,u0,exp_u0,code,exp_stage,exp_low,got_type,exp_type)]。
    """
    try:
        from .mmtr_build import MmtrImage, REC_LO, REC_SIZE
        from .mmtr_model import MmtrModel
    except ImportError:
        from mmtr_build import MmtrImage, REC_LO, REC_SIZE
        from mmtr_model import MmtrModel
    img = MmtrImage.from_bytes(data)
    model = MmtrModel(data)
    n, bad = 0, []
    for slot, r in enumerate(model.parse_records()):
        if r.is_empty and not r.vs_blob:
            continue
        vs, ps = img.blob_at(r.vs_blob), img.blob_at(r.blob_off)
        vm, pm = _bind_info_map(vs), _bind_info_map(ps)
        grp = derive_group(vs, ps)
        c6 = (img.rec_field(slot, 0xC4) >> 16) & 0xFF
        nsmp = img.rec_field(slot, 0xB0) >> 16
        ntex = img.buf[REC_LO + slot * REC_SIZE + 0xCC]
        for kind, dptr, cnt in (("cb", img.rec_field(slot, 0x38), c6),
                                ("smp", img.rec_field(slot, 0x48), nsmp),
                                ("tex", img.rec_field(slot, 0x58), ntex)):
            if not dptr or cnt == 0:
                continue
            names = grp[kind]
            if len(names) < cnt:
                continue
            for k in range(cnt):
                off = dptr + k * 8
                u0, code = _u32(data, off), _u32(data, off + 4)
                nm = names[k]
                vi, pi = vm.get(nm), pm.get(nm)
                exp_stage = 0x11 if (vi and pi) else (0x01 if vi else 0x10)
                exp_u0 = vi[1] if vi else 0
                exp_low = pi[1] if pi else 0
                if kind == "cb":
                    exp_type = 0xFF
                elif kind == "smp":
                    exp_type = 0x00
                else:
                    exp_type = _TEX_DIM_TYPE.get((vi or pi)[2]) if (vi or pi) else None
                got_type = (code >> 24) & 0xFF
                n += 1
                if not (u0 == exp_u0 and (code >> 16) & 0xFF == exp_stage
                        and code & 0xFFFF == exp_low
                        and (exp_type is None or got_type == exp_type)):
                    bad.append((slot, kind, nm, u0, exp_u0, code, exp_stage,
                                exp_low, got_type, exp_type))
    return n, bad


def check_groups(data):
    """校验: 每条非空记录的【池切片】 == 由 PS/VS RDEF 派生的绑定组。

    返回 (n_checked, mismatches); mismatches = [(slot, kind, donor_list, derived_list), ...]。
    这是"尾段绑定层可由 RDEF 派生"的硬验证。
    """
    try:
        from .mmtr_model import MmtrModel
        from .mmtr_build import MmtrImage
    except ImportError:
        from mmtr_model import MmtrModel
        from mmtr_build import MmtrImage
    img = MmtrImage.from_bytes(data)
    tail = TailModel.decode(data)
    n, bad = 0, []
    for slot, r in enumerate(MmtrModel(data).parse_records()):
        if r.is_empty and not r.vs_blob:
            continue
        n += 1
        got = record_group(data, slot, tail)
        want = derive_group(img.blob_at(r.vs_blob), img.blob_at(r.blob_off))
        for kind in ("cb", "smp", "tex"):
            if got[kind] != want[kind]:
                bad.append((slot, kind, got[kind], want[kind]))
    return n, bad


# --------------------------------------------------------------- 规范合成(①-b)
def rebuild_canonical(data):
    """规范重建尾段: pool/cbuffer/param/string 按(组)去重重建 + desc 原样平移, 并重指
    记录/程序表 的尾部指针。**头部尺寸不变**(尾段 padding 到原长, 避开 blob 指针重映射)。

    仅处理 version `0x01100004` 布局; 无法安全重建时返回 None。
    组 = 一个"绑定 slice"(cb 表切片 + smp 池切片 + tex 池切片), 按指针三元组归并;
    同一组只发一套。strings 按引用去重。
    """
    data = bytes(data)
    try:
        t = TailModel.decode(data)
    except ValueError:
        return None
    try:
        from .mmtr_build import PT_LO, PT_N, PT_SIZE, REC_LO, REC_N, REC_SIZE
    except ImportError:
        from mmtr_build import PT_LO, PT_N, PT_SIZE, REC_LO, REC_N, REC_SIZE
    bs = t.boundaries
    old_bs = bs["blob_start"]
    old_tail_len = old_bs - SKELETON_HI
    desc = bytes(data[bs["desc"]:bs["string"]])
    old_desc = bs["desc"]

    pool_by_off = {e[0]: e for e in t.pool_entries}
    cb_by_off = {e["off"]: e for e in t.cbuffer_entries}
    pa_by_off = {e[0]: e for e in t.param_entries}

    def u32(o):
        return struct.unpack_from("<I", data, o)[0]

    def cb_defs(ptr, n):
        out = []
        for k in range(n):
            e = cb_by_off.get(ptr + 32 * k)
            if e is None:
                return None
            mem, mo = [], e["members_off"]
            for j in range(e["count"]):
                pe = pa_by_off.get(mo + 16 * j)
                if pe is None:
                    break
                mem.append((pe[5], pe[3], pe[4]))
            out.append((e["name"], e["size"], e["count"], tuple(mem)))
        return out

    def pool_names(ptr, n):
        out = []
        for k in range(n):
            e = pool_by_off.get(ptr + 16 * k)
            if e is None:
                return None
            out.append(e[3])
        return out

    names, nset = [], set()

    def use(nm):
        if nm and nm not in nset:
            nset.add(nm)
            names.append(nm)

    def name_at(v):
        if bs["string"] <= v < old_bs:
            return _ascii(data, v)
        return None

    slots = [(PT_LO + i * PT_SIZE, "pt", i) for i in range(PT_N)] + \
            [(REC_LO + i * REC_SIZE, "rec", i) for i in range(REC_N)]
    groups, gorder, slot_gk = {}, [], {}
    for base, kind, i in slots:
        for fo in (0xD8, 0x104):
            use(name_at(u32(base + fo)))
        cbt, smpt, text = u32(base + 0x40), u32(base + 0x50), u32(base + 0x60)
        if not (cbt or smpt or text):
            continue
        c6 = (u32(base + 0xC4) >> 16) & 0xFF
        nsmp = u32(base + 0xB0) >> 16
        ntex = data[base + 0xCC]
        key = (cbt, smpt, text, nsmp, ntex, c6)
        if key not in groups:
            g = {"cb": cb_defs(cbt, c6) if cbt else [],
                 "smp": pool_names(smpt, nsmp) if smpt else [],
                 "tex": pool_names(text, ntex) if text else []}
            if g["cb"] is None or g["smp"] is None or g["tex"] is None:
                return None
            groups[key] = g
            gorder.append(key)
            for d in g["cb"]:
                use(d[0])
                for (mn, _ms, _mo) in d[3]:
                    use(mn)
            for nm in g["smp"] + g["tex"]:
                use(nm)
        slot_gk[(kind, i)] = key
    use(name_at(u32(0x10)))

    smp_uniq, tex_uniq, cb_uniq, param_rel = {}, {}, {}, {}
    smp_order, tex_order, cb_order = [], [], []
    for gk in gorder:
        g = groups[gk]
        for key, uniq, order in ((tuple(g["smp"]), smp_uniq, smp_order),
                                 (tuple(g["tex"]), tex_uniq, tex_order),
                                 (tuple(g["cb"]), cb_uniq, cb_order)):
            if key not in uniq:
                uniq[key] = None
                order.append(key)

    param, param_name_at = bytearray(), []
    for ck in cb_order:
        for d in ck:
            if d in param_rel:
                continue
            param_rel[d] = len(param)
            for (mn, ms, mo) in d[3]:
                param_name_at.append((len(param), mn))
                param += struct.pack("<IIII", 0, 0, ascii_hash(mn), (ms << 16) | mo)

    cb, cb_name_at, cb_mem_at = bytearray(), [], []
    for ck in cb_order:
        cb_uniq[ck] = len(cb)
        for d in ck:
            cb_name_at.append((len(cb), d[0]))
            cb_mem_at.append((len(cb), d))
            cb += struct.pack("<QIIIIQ", 0, ascii_hash(d[0]), 0, d[1], d[2], 0)

    pool, pool_name_at = bytearray(), []
    for sk in smp_order:
        smp_uniq[sk] = len(pool)
        for nm in sk:
            pool_name_at.append((len(pool), nm))
            pool += struct.pack("<QII", 0, ascii_hash(nm), 0)
    for tk in tex_order:
        tex_uniq[tk] = len(pool)
        for nm in tk:
            pool_name_at.append((len(pool), nm))
            pool += struct.pack("<QII", 0, ascii_hash(nm), 0)

    cb_rel = {gk: cb_uniq[tuple(groups[gk]["cb"])] for gk in gorder}
    smp_rel = {gk: smp_uniq[tuple(groups[gk]["smp"])] for gk in gorder}
    tex_rel = {gk: tex_uniq[tuple(groups[gk]["tex"])] for gk in gorder}

    str_pool, str_rel = bytearray(), {}
    for nm in names:
        str_rel[nm] = len(str_pool)
        str_pool += nm.encode("latin1", "replace") + b"\x00"

    pool_base = SKELETON_HI
    cb_base = pool_base + len(pool)
    param_base = cb_base + len(cb)
    desc_base = param_base + len(param)
    pad = old_tail_len - (len(pool) + len(cb) + len(param) + len(desc) + len(str_pool))
    if pad < 0:
        return None
    str_base = desc_base + len(desc) + pad

    for off, nm in pool_name_at:
        struct.pack_into("<Q", pool, off, str_base + str_rel[nm])
    for off, nm in cb_name_at:
        struct.pack_into("<Q", cb, off, str_base + str_rel[nm])
    for off, d in cb_mem_at:
        struct.pack_into("<Q", cb, off + 24, param_base + param_rel[d])
    for off, nm in param_name_at:
        struct.pack_into("<I", param, off, str_base + str_rel[nm])

    new_tail = bytes(pool) + bytes(cb) + bytes(param) + desc + b"\x00" * pad + bytes(str_pool)

    head = bytearray(data[:SKELETON_HI])
    hnm = name_at(u32(0x10))
    if hnm in str_rel:
        struct.pack_into("<I", head, 0x10, str_base + str_rel[hnm])
    for base, kind, i in slots:
        for fo in (0x38, 0x48, 0x58):
            v = u32(base + fo)
            if v:
                struct.pack_into("<I", head, base + fo, desc_base + (v - old_desc))
        gk = slot_gk.get((kind, i))
        if gk is not None:
            struct.pack_into("<I", head, base + 0x40, cb_base + cb_rel[gk])
            struct.pack_into("<I", head, base + 0x50, pool_base + smp_rel[gk])
            struct.pack_into("<I", head, base + 0x60, pool_base + tex_rel[gk])
        for fo in (0xD8, 0x104):
            v = u32(base + fo)
            nm = name_at(v)
            if nm in str_rel:
                struct.pack_into("<I", head, base + fo, str_base + str_rel[nm])
    return bytes(head) + new_tail + data[old_bs:]


def verify_rebuild(data, out):
    """重建前后 逐槽比对绑定组名字(环境无关的正确性检查)。返回 (n, mismatches)。"""
    try:
        from .mmtr_model import MmtrModel
    except ImportError:
        from mmtr_model import MmtrModel
    n, bad = 0, []
    src, dst = MmtrModel(data).parse_records(), MmtrModel(out).parse_records()
    for slot in range(min(len(src), len(dst))):
        a, b = record_group(data, slot), record_group(out, slot)
        n += 1
        if a != b:
            bad.append((slot, a, b))
    return n, bad


if __name__ == "__main__":
    import sys
    d = open(sys.argv[1], "rb").read()
    TailModel.decode(d).dump()
