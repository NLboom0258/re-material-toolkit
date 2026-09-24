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
    程序指针: +0x00=PS; +0xe0/+0xe8=VS; +0x08=CS(部分); 三字段并集=全部 83 blob。
    其它指针(u64, 高 32 位通常 0): +0x10/+0x18/+0x20/+0x28 辅助表; +0x30 共用 VM;
        +0x38/+0x40 cbuffer(描述符/名称池); +0x48/+0x50 sampler; +0x58/+0x60 纹理。
    +0x00 blob 偏移; +0x9c blob 大小; +0xd8 变体名指针。
    计数: +0xa4=资源总数+1; +0xac=cbuffer 数; +0xb0=sampler<<16; +0xb8/+0xcc=SRV 数;
          +0xc4=(sampler<<24)|(cbuffer<<16)。
    语义未定(TBD, 见 REC_TBD): +0x88/+0x8c/+0xa8/+0xb4/+0xbc/+0xc8/+0xd0/+0xd4; 辅助表语义。

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
# 记录捆绑的**多个 shader 程序指针**(实测: +0x00=PS, rec-0x20=VS(真), +0x08=CS(部分))
REC_OFF_CS_BLOB = 0x08      # CS 程序(仅部分记录, 如预变换/蒙皮)
REC_OFF_VS_BLOB = -0x20     # 真 VS 指针在 rec-0x20(逆向 DMC5 exe 确证, 全样本 100%)
REC_OFF_VS_LINK = 0xE0      # 实测 == 下一记录的真 VS(跨记录链接, 非本记录字段)
REC_OFF_VS_BLOB2 = 0xE8     # 同上: 该地址即"下一条记录"的 rec-0x20 ⇒ 跨记录, 勿当本记录 VS
# 其它指针(u64, 高 32 位通常为 0)
REC_OFF_AUX_PTRS = (0x10, 0x18, 0x20, 0x28)  # 4 个辅助表指针(→头部小表; 语义 TBD)
REC_OFF_VM_PTR = 0x30                        # 共用 VM/程序指针(如 0x48780)

# 语义已确认的字段偏移(其余视为 TBD)
REC_KNOWN = {
    0x00, 0x08, 0x9C, 0xD8, 0xE0, 0xE8,
    0x10, 0x18, 0x20, 0x28, 0x30,
    0x38, 0x40, 0x48, 0x50, 0x58, 0x60,
    0xA4, 0xAC, 0xB0, 0xB8, 0xC4, 0xCC,
}

# 尚未定语义的字段偏移(TBD)
REC_TBD = {0x04, 0x0C, 0x88, 0x8C, 0xA8, 0xB4, 0xBC, 0xC0, 0xC8, 0xD0, 0xD4}

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
                 "cs_blob", "vs_blob", "vs_blob2", "aux_ptrs", "vm_ptr",
                 "count_total", "count_cb", "count_smp_hi", "count_srv",
                 "count_smp_cb", "count_srv2", "cb", "smp", "tex")

    def __init__(self, data, off):
        u32 = lambda o: _u32(data, o)
        self.off = off
        self.blob_off = u32(off + REC_OFF_BLOB)      # 主程序(PS)
        self.blob_size = u32(off + REC_OFF_SIZE)
        self.name_ptr = u32(off + REC_OFF_NAME_PTR)
        self.name = _str(data, self.name_ptr, 64) if 0x1000 <= self.name_ptr < len(data) else ""
        self.cs_blob = u32(off + REC_OFF_CS_BLOB)    # 部分记录(CS)
        self.vs_blob = u32(off + REC_OFF_VS_BLOB)    # 真 VS(rec-0x20)
        self.vs_blob2 = u32(off + REC_OFF_VS_BLOB2)  # ⚠ 跨记录(== 下一条记录的真 VS), 非本记录字段
        self.aux_ptrs = tuple(u32(off + fo) for fo in REC_OFF_AUX_PTRS)
        self.vm_ptr = u32(off + REC_OFF_VM_PTR)
        self.count_total = u32(off + REC_OFF_CNT_TOTAL)
        self.count_cb = u32(off + REC_OFF_CNT_CB)
        self.count_smp_hi = u32(off + REC_OFF_CNT_SMP_HI)
        self.count_srv = u32(off + REC_OFF_CNT_SRV)
        self.count_smp_cb = u32(off + REC_OFF_CNT_SMP_CB)
        self.count_srv2 = u32(off + REC_OFF_CNT_SRV2)
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

    def programs(self):
        """该变体记录捆绑的 shader 程序: {'PS': blob_off, 'VS':..., 'CS':...}(无则省略)。"""
        out = {}
        if self.blob_off:
            out["PS"] = self.blob_off
        if self.vs_blob:
            out["VS"] = self.vs_blob
        if self.cs_blob:
            out["CS"] = self.cs_blob
        return out

    def tbd_fields(self, data):
        """返回该记录中语义未定(REC_TBD 且非 0)的字段: [(field_off, value), ...]。"""
        return [(fo, _u32(data, self.off + fo)) for fo in sorted(REC_TBD)
                if _u32(data, self.off + fo) != 0]

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
        """变体数组 **主程序字段(+0x00, 即 PS)** 引用的 blob: [(blob_idx, off, n_records), ...]。

        注: 一条记录还捆绑 真VS(rec-0x20) 与部分 CS(+0x08); 全部字段的并集 = 全部 blob
        (见 all_referenced_blobs / blob_role)。
        """
        from collections import Counter
        cnt = Counter(r.blob_off for r in self.iter_records())
        idx_of = {o: i for i, (o, _s) in enumerate(self.blobs)}
        return [(idx_of.get(o), o, n) for o, n in sorted(cnt.items()) if o in idx_of]

    def unreferenced_blob_indices(self):
        """变体数组 **+0x00(PS)** 字段未引用的 blob 下标(非“全局未引用”)。"""
        used = {o for _, o, _ in self.referenced_blobs()}
        return [i for i, (o, _s) in enumerate(self.blobs) if o not in used]

    def all_referenced_blob_indices(self):
        """被变体记录**任一程序字段**(PS+VS+CS)引用的 blob 下标(应为全部)。

        注: 含“空槽”(PS=0 但有 VS)记录, 故必须遍历全部记录。
        """
        idx_of = {o: i for i, (o, _s) in enumerate(self.blobs)}
        used = set()
        for r in self.parse_records():
            for off in (r.blob_off, r.vs_blob, r.cs_blob):
                if off in idx_of:
                    used.add(idx_of[off])
        return sorted(used)

    def blob_role(self, idx):
        """该 blob 在记录中充当的程序角色: 'PS'/'VS'/'CS'(可多个, 用 '+' 连)/'?'。"""
        off = self.blob_off(idx)
        roles = []
        for r in self.parse_records():
            if r.blob_off == off and "PS" not in roles:
                roles.append("PS")
            if r.vs_blob == off and "VS" not in roles:
                roles.append("VS")
            if r.cs_blob == off and "CS" not in roles:
                roles.append("CS")
        return "+".join(roles) if roles else "?"

    def records_using(self, idx):
        """所有引用该 blob(任一程序字段)的变体记录。"""
        off = self.blob_off(idx)
        return [r for r in self.parse_records()
                if off in (r.blob_off, r.vs_blob, r.cs_blob)]

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
        ps = {r.blob_off for r in recs if r.blob_off}
        vs = {r.vs_blob for r in recs if r.vs_blob}
        cs = {r.cs_blob for r in recs if r.cs_blob}
        return {
            "version": f"0x{self.version:08x}",
            "blob_start": self.blob_start,
            "blobs": len(self.blobs),
            "records": len(recs),
            "empty_records": sum(1 for r in recs if r.is_empty),
            "ps_blobs": len(ps),
            "vs_blobs": len(vs),
            "cs_blobs": len(cs),
            "program_table": [p.name for p in self.parse_program_table()],
            "string_ptr_name": self.string_ptr_name,
        }

    def dump(self):
        s = self.summary()
        print(f"mmtr version={s['version']} blob_start=0x{s['blob_start']:x} "
              f"blobs={s['blobs']} records={s['records']} empty={s['empty_records']}")
        print(f"  program blobs: PS={s['ps_blobs']} VS={s['vs_blobs']} CS={s['cs_blobs']}")
        print(f"  string_ptr(+0x10) -> {s['string_ptr_name']!r}")
        print(f"  program_table: {s['program_table']}")

    # ---- 一致性校验(编辑后自检, 无需进游戏) ----
    def _check_pool(self, pool, count, label, rec_i, issues):
        """校验名称池前 count 条条目的 hash 与名字一致(池坏/指针错会在此暴露)。"""
        if not pool or not (1 <= count <= 64):
            return
        from .hashes import ascii_hash
        data = self.data
        for k in range(count):
            off = pool + k * 16
            if off + 16 > len(data):
                issues.append(f"rec[{rec_i}] {label} 池条目越界 @0x{off:x}")
                return
            name_off = _u64(data, off)
            h = _u32(data, off + 8)
            if not (0x1000 <= name_off < self.blob_start):
                issues.append(f"rec[{rec_i}] {label} 池[{k}] name_off=0x{name_off:x} 越界")
                return
            nm = _str(data, name_off)
            if not nm or ascii_hash(nm) != h:
                issues.append(f"rec[{rec_i}] {label} 池[{k}] hash 与名字不符 "
                              f"({nm!r}/0x{h:08x})")
                return

    def validate(self):
        """头部一致性校验; 返回问题列表(空=自洽)。用于任何编辑后的离线自检。

        覆盖: blob 连续性、记录程序指针有效性、`+0x9c` 与 PS 实际大小一致
        (捕获“按值重映射”类地雷)、名字指针/名称池 hash。
        """
        issues = []
        data = self.data
        size_of = {o: s for o, s in self.blobs}

        # 1) blob 区应连续到期文件尾
        if self.blobs:
            end = self.blobs[-1][0] + self.blobs[-1][1]
            if end != len(data):
                issues.append(f"blob 区未到文件尾: last_end=0x{end:x} 文件大小=0x{len(data):x}")

        # 2) 逐记录: 程序指针有效 + +0x9c 与 PS 实际大小一致 + 名字指针
        for i, r in enumerate(self.parse_records()):
            if r.blob_off and not r.name:
                continue  # 末条哨兵记录(有 blob 无名字), 字段非程序指针, 跳过
            if r.blob_off:
                if r.blob_off not in size_of:
                    issues.append(f"rec[{i}] {r.name!r}: PS blob 0x{r.blob_off:x} 非有效 blob")
                elif r.blob_size != size_of[r.blob_off]:
                    issues.append(f"rec[{i}] {r.name!r}: +0x9c=0x{r.blob_size:x} != PS 实际大小 "
                                  f"0x{size_of[r.blob_off]:x}(误改大小地雷?)")
            for nm, o in (("VS", r.vs_blob), ("CS", r.cs_blob)):
                if o and o not in size_of:
                    issues.append(f"rec[{i}] {r.name!r}: {nm} blob 0x{o:x} 非有效 blob")
            if r.name_ptr and not (0x1000 <= r.name_ptr < self.blob_start):
                issues.append(f"rec[{i}]: 名字指针 0x{r.name_ptr:x} 越界")

            # 3) 名称池 hash(纹理池长度取 +0xcc)
            self._check_pool(r.tex[1], r.count_srv2, "tex", i, issues)

        return issues


if __name__ == "__main__":
    import sys
    MmtrModel.load(sys.argv[1]).dump()
