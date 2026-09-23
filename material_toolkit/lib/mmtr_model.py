#!/usr/bin/env python3
"""mmtr 完整解析模型(只读视图)。

把 analysis §11 已确认的头部结构固化为可复用解析, 供 CLI/GUI, 以及后续
"方便地改 mmtr / 从 0 构建 mmtr" 使用。**这是只读视图**(编辑仍在 binding/rdef/mmtr)。

布局(version 0x01100004, 跨样本固定骨架; 详见 analysis §11):
  文件头(0x00..0x14):
    +0x00 'SDF\\0'; +0x04 version; +0x08 blob 区起点; +0x0c 0; +0x10 字符串指针(->技术名)
  程序表 [0x14, 0x53C): 5 条 x 264B (基础程序: PreTransform*CS / ShadowStatic / AShadowStatic)
  变体记录 [0x568, 0x46240): 1083 条 x 264B
  记录后区 [0x46240, blob_start): 名称池 / cbuffer 绑定表 / 参数表 / 字符串池
  blob 区 [blob_start, EOF): 83 个标准 DXBC

变体记录(264B)关键字段:
    +0x00 blob 偏移; +0x9c blob 大小; +0xd8 变体名指针
    +0x38/+0x40 cbuffer(描述符/名称池); +0x48/+0x50 sampler; +0x58/+0x60 纹理
    计数: +0xa4=资源总数+1; +0xac=cbuffer 数; +0xb0=sampler<<16; +0xb8/+0xcc=SRV 数;
          +0xc4=(sampler<<24)|(cbuffer<<16)
  其余字段语义未定: 见 REC_TBD。

描述符 8B: (code,0), code=(type<<24)|(stage<<16)|slot;
  type: 0x00=sampler / 0x02=texture2d / 0x80=raw / 0xff=cbuffer;
  stage 位掩码: 0x01=VS / 0x10=PS / 0x11=VS|PS(共享)。
名称池 16B: [name_off(u64)][hash(u32)][0(u32)]。
"""
import re

try:
    from .binding import blob_list, discover_groups, _u32, _u64, _str
    from .mmtr import Mmtr
except ImportError:  # 允许脚本直接 import
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from binding import blob_list, discover_groups, _u32, _u64, _str
    from mmtr import Mmtr

HEADER_MAGIC = b"SDF\0"

# 固定骨架(version 0x01100004)
PRE_LO, PRE_N, PRE_SIZE = 0x14, 5, 264          # 程序表: [0x14, 0x53C)
REC_LO, REC_HI, REC_SIZE = 0x568, 0x46240, 264  # 变体记录: [0x568, 0x46240)

# 变体记录字段偏移
REC_OFF_BLOB = 0x00
REC_OFF_SIZE = 0x9C
REC_OFF_NAME_PTR = 0xD8
REC_OFF_CB_DESC, REC_OFF_CB_POOL = 0x38, 0x40
REC_OFF_SMP_DESC, REC_OFF_SMP_POOL = 0x48, 0x50
REC_OFF_TEX_DESC, REC_OFF_TEX_POOL = 0x58, 0x60
REC_OFF_CNT_TOTAL = 0xA4    # 资源总数 + 1
REC_OFF_CNT_CB = 0xAC       # cbuffer 数
REC_OFF_CNT_SMP_HI = 0xB0   # sampler << 16
REC_OFF_CNT_SRV = 0xB8      # SRV 数(tex+raw+struct)
REC_OFF_CNT_SMP_CB = 0xC4   # (sampler<<24)|(cbuffer<<16)
REC_OFF_CNT_SRV2 = 0xCC     # 亦为 SRV 数(与 +0xb8 同)

# 语义已确认的字段偏移(其余视为 TBD)
REC_KNOWN = {
    0x00, 0x9C, 0xD8,
    0x38, 0x40, 0x48, 0x50, 0x58, 0x60,
    0xA4, 0xAC, 0xB0, 0xB8, 0xC4, 0xCC,
}

DESC_TYPE = {0x00: "sampler", 0x02: "tex2d", 0x80: "raw", 0xFF: "cbuffer"}


def _decode_desc(code):
    return {"type": (code >> 24) & 0xFF, "stage": (code >> 16) & 0xFF,
            "slot": code & 0xFFFF}


def stage_label(mask):
    """描述符 stage 位掩码 -> 标签(0x01=VS, 0x10=PS, 0x11=VS|PS)。"""
    labs = []
    if mask & 0x01:
        labs.append("VS")
    if mask & 0x10:
        labs.append("PS")
    if mask & 0x02:
        labs.append("GS")
    if mask & 0x04:
        labs.append("HS")
    if mask & 0x08:
        labs.append("DS")
    if mask & 0x20:
        labs.append("CS")
    return "|".join(labs) if labs else f"0x{mask:02x}"


_PREFIX_RE = re.compile(r"^(ATSDirect|ADirect|ATS|A|TS)?")


def split_variant_name(name):
    """变体名 -> (标志前缀, 技术名)。如 'ATSShadowStatic' -> ('ATS', 'ShadowStatic')。"""
    if not name:
        return "", ""
    m = _PREFIX_RE.match(name)
    pre = m.group(1) or ""
    return pre, name[len(pre):]


class VariantRecord:
    """264B 变体记录。"""

    __slots__ = ("off", "blob_off", "blob_size", "name_ptr", "name",
                 "count_total", "count_cb", "count_smp_hi", "count_srv",
                 "count_smp_cb", "cb", "smp", "tex")

    def __init__(self, data, off):
        u32 = lambda o: _u32(data, o)
        self.off = off
        self.blob_off = u32(off + REC_OFF_BLOB)
        self.blob_size = u32(off + REC_OFF_SIZE)
        self.name_ptr = u32(off + REC_OFF_NAME_PTR)
        self.name = _str(data, self.name_ptr, 64) if 0x1000 <= self.name_ptr < len(data) else ""
        self.count_total = u32(off + REC_OFF_CNT_TOTAL)
        self.count_cb = u32(off + REC_OFF_CNT_CB)
        self.count_smp_hi = u32(off + REC_OFF_CNT_SMP_HI)
        self.count_srv = u32(off + REC_OFF_CNT_SRV)
        self.count_smp_cb = u32(off + REC_OFF_CNT_SMP_CB)
        self.cb = (u32(off + REC_OFF_CB_DESC), u32(off + REC_OFF_CB_POOL))
        self.smp = (u32(off + REC_OFF_SMP_DESC), u32(off + REC_OFF_SMP_POOL))
        self.tex = (u32(off + REC_OFF_TEX_DESC), u32(off + REC_OFF_TEX_POOL))

    @property
    def is_empty(self):
        return self.blob_off == 0

    @property
    def prefix(self):
        return split_variant_name(self.name)[0]

    @property
    def tech(self):
        return split_variant_name(self.name)[1]

    def __repr__(self):
        return (f"VariantRecord(#{self.name or '<unnamed>'} blob=0x{self.blob_off:x} "
                f"sz={self.blob_size} cb={self.count_cb} srv={self.count_srv})")


class ProgramEntry:
    """程序表条目(264B): +0x34=blob 偏移, +0x104=技术名指针。"""

    def __init__(self, data, off):
        self.off = off
        self.blob_off = _u32(data, off + 0x34)
        name_ptr = _u32(data, off + 0x104)
        self.name = _str(data, name_ptr, 64) if 0x1000 <= name_ptr < len(data) else ""

    def __repr__(self):
        return f"ProgramEntry({self.name!r} blob=0x{self.blob_off:x})"


class BindingGroup:
    """一个 (描述符, 池) 绑定组 = 该 blob 的一种绑定列表(对应一种顶点模式/pass)。"""

    def __init__(self, data, desc, pool, records):
        self.desc = desc
        self.pool = pool
        self.records = records
        self.srvs = []          # [{idx,type,slot,name,hash,stage}]
        self._parse(data)

    def _parse(self, data):
        n = _u32(data, self.records[0] + REC_OFF_CNT_SRV2) or _u32(data, self.records[0] + REC_OFF_CNT_SRV)
        for i in range(min(n, 64)):
            eoff = self.pool + i * 16
            if eoff + 16 > len(data):
                break
            name_off = _u64(data, eoff)
            h = _u32(data, eoff + 8)
            if not (0x1000 <= name_off < len(data)):
                break
            nm = _str(data, name_off)
            if not nm:
                break
            d = _decode_desc(_u32(data, self.desc + 4 + i * 8))
            self.srvs.append({"idx": i, "type": d["type"], "slot": d["slot"],
                              "stage": d["stage"], "name": nm, "hash": h})

    @property
    def stage_mask(self):
        m = 0
        for s in self.srvs:
            m |= s["stage"]
        return m

    def srv_names(self):
        return [s["name"] for s in self.srvs]

    def __repr__(self):
        return (f"BindingGroup(desc=0x{self.desc:x} pool=0x{self.pool:x} "
                f"n_srv={len(self.srvs)} n_rec={len(self.records)})")


class MmtrModel:
    """mmtr 完整解析(只读)。"""

    def __init__(self, data):
        if data[:4] != HEADER_MAGIC:
            raise ValueError("not an SDF/mmtr file")
        self.data = data
        self.magic = data[:4]
        self.version = _u32(data, 4)
        self.blob_start = _u32(data, 8)
        self.header_field_0x0c = _u32(data, 0x0C)
        self.string_ptr = _u32(data, 0x10)
        self.string_ptr_name = (_str(data, self.string_ptr, 64)
                                if 0x1000 <= self.string_ptr < len(data) else "")
        _, self.blobs = blob_list(data)          # [(off, size), ...]
        self._nmtr = None                        # 惰性: 参数表(复用 Mmtr)
        self.program_table = None
        self.records = None

    @classmethod
    def load(cls, path):
        return cls(open(path, "rb").read())

    # ---- 程序表 / 变体记录 ----
    def parse_program_table(self):
        if self.program_table is None:
            self.program_table = [ProgramEntry(self.data, PRE_LO + i * PRE_SIZE)
                                  for i in range(PRE_N)]
        return self.program_table

    def parse_records(self):
        if self.records is None:
            n = (REC_HI - REC_LO) // REC_SIZE
            self.records = [VariantRecord(self.data, REC_LO + i * REC_SIZE)
                            for i in range(n)]
        return self.records

    def iter_records(self, include_empty=False):
        for r in self.parse_records():
            if include_empty or not r.is_empty:
                yield r

    def record_count(self):
        return (REC_HI - REC_LO) // REC_SIZE

    def empty_record_count(self):
        return sum(1 for r in self.parse_records() if r.is_empty)

    # ---- blob ----
    def blob_count(self):
        return len(self.blobs)

    def blob_off(self, idx):
        return self.blobs[idx][0]

    def blob_size(self, idx):
        return self.blobs[idx][1]

    def blob_index_of(self, blob_off):
        for i, (o, _s) in enumerate(self.blobs):
            if o == blob_off:
                return i
        return None

    def blob_variants(self, idx):
        """返回指向该 blob 的所有变体名(去重、保序)。"""
        off = self.blob_off(idx)
        out, seen = [], set()
        for r in self.iter_records():
            if r.blob_off == off and r.name and r.name not in seen:
                seen.add(r.name)
                out.append(r.name)
        return out

    def referenced_blobs(self):
        """变体数组实际引用的 blob: [(blob_idx, off, n_records), ...](按 blob 顺序)。

        注意: 变体数组只引用**部分** blob(env=17 / 共 83); 其余由程序表/记录其它指针引用。
        """
        from collections import Counter
        cnt = Counter(r.blob_off for r in self.iter_records())
        idx_of = {o: i for i, (o, _s) in enumerate(self.blobs)}
        return [(idx_of.get(o), o, n) for o, n in sorted(cnt.items()) if o in idx_of]

    def unreferenced_blob_indices(self):
        """变体数组**未**引用的 blob 下标(见 referenced_blobs 说明)。"""
        used = {o for _, o, _ in self.referenced_blobs()}
        return [i for i, (o, _s) in enumerate(self.blobs) if o not in used]

    def record_for(self, idx):
        """该 blob 的第一条(非空)变体记录。"""
        off = self.blob_off(idx)
        for r in self.iter_records():
            if r.blob_off == off:
                return r
        return None

    # ---- 绑定组 ----
    def groups(self, idx):
        """该 blob 的所有唯一 (描述符,池) 绑定组(按池偏移排序)。"""
        off = self.blob_off(idx)
        grp = discover_groups(self.data, off)
        return [BindingGroup(self.data, desc, pool, recs)
                for (desc, pool), recs in sorted(grp.items(), key=lambda kv: kv[0][1])]

    # ---- 参数表(复用 Mmtr) ----
    def _mmtr(self):
        if self._nmtr is None:
            self._nmtr = Mmtr.from_bytes(self.data)
        return self._nmtr

    @property
    def params(self):
        return self._mmtr().params

    def cbuffer_members(self, cbuffer_name):
        return self._mmtr().cbuffer_members(cbuffer_name)

    def string_pool_range(self):
        m = self._mmtr()
        return (m.string_pool_lo, m.string_pool_hi)

    # ---- 统计 / 摘要 ----
    def variant_name_stats(self):
        """变体名前缀直方图 + 基础技术名集合。

        口径 = **全部“有名字”的记录**(含空 blob 记录; 与 analysis §11 一致: 前缀合计 1082)。
        """
        from collections import Counter
        pref = Counter()
        techs = set()
        for r in self.parse_records():
            if not r.name:
                continue
            p, t = split_variant_name(r.name)
            pref[p or "(none)"] += 1
            techs.add(t)
        return {"prefix": dict(pref), "tech_count": len(techs),
                "techs": sorted(techs)}

    def summary(self):
        recs = self.parse_records()
        blob_to_names = {}
        for r in recs:
            if not r.is_empty:
                blob_to_names.setdefault(r.blob_off, []).append(r.name)
        return {
            "version": f"0x{self.version:08x}",
            "blob_start": self.blob_start,
            "blobs": len(self.blobs),
            "records": len(recs),
            "empty_records": sum(1 for r in recs if r.is_empty),
            "distinct_blobs_referenced": len(blob_to_names),
            "program_table": [p.name for p in self.parse_program_table()],
            "string_ptr_name": self.string_ptr_name,
        }

    def dump(self):
        s = self.summary()
        print(f"mmtr version={s['version']} blob_start=0x{s['blob_start']:x} "
              f"blobs={s['blobs']} records={s['records']} empty={s['empty_records']} "
              f"distinct_blobs={s['distinct_blobs_referenced']}")
        print(f"  string_ptr(+0x10) -> {s['string_ptr_name']!r}")
        print(f"  program_table: {s['program_table']}")


if __name__ == "__main__":
    import sys
    MmtrModel.load(sys.argv[1]).dump()
