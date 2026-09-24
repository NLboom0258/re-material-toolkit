#!/usr/bin/env python3
"""mmtr 从0构建支持: 版本骨架常量 + 内容字段映射 + 最小装配器。

基于 596 文件实证(见 analysis/mmtr_from_scratch_design.md §1):
  - 区域 [0x00,0x46350) 中 84.4% 字节跨同版本(0x01100004)文件恒定;
  - "内容字段"(跨文件变化, 必须按材质构造) 仅落在下列位置:
      容器头:   +0x08(blob_start), +0x10(字符串指针)
      程序表:   5 条 x 264B, 每条的 25 个 u32 字段
      间隙:     +0x540, +0x548
      变体记录: 1083 条 x 264B, 每条的 28 个 u32 字段
      固定后段: [0x46240,0x46350) 无(全常量)
  - 其余为"版本骨架"(可照抄/可硬编码)。

提供:
  - content_positions(): [0,0x46350) 内内容字节绝对偏移
  - MmtrTemplate: 抽/校验"版本骨架"(纯骨架 = 内容位置清零后的 [0x00,0x46350))
  - MmtrImage: 最小装配器(骨架/内容 + 尾段 + blob 区), 支持记录字段与程序指针读写
"""
import struct

try:
    from .hashes import ascii_hash
    from .rdef import rdef_resources
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hashes import ascii_hash
    from rdef import rdef_resources

SKELETON_HI = 0x46350

HEAD_CONTENT_U32 = (0x08, 0x10)      # blob_start / 字符串指针

GAP_LO, GAP_HI = 0x53C, 0x568
GAP_CONTENT_U32 = (0x540, 0x548)

PT_LO, PT_N, PT_SIZE = 0x14, 5, 264
PT_CONTENT_FIELDS = (0x004, 0x00C, 0x034, 0x05C, 0x064, 0x06C, 0x074, 0x07C,
                     0x084, 0x08C, 0x094, 0x09C, 0x0B4, 0x0B8, 0x0CC, 0x0D0,
                     0x0D4, 0x0D8, 0x0DC, 0x0E0, 0x0E4, 0x0F0, 0x0F4, 0x0F8,
                     0x104)

REC_LO, REC_N, REC_SIZE = 0x568, 1083, 264
REC_CONTENT_FIELDS = (0x000, 0x008, 0x018, 0x030, 0x038, 0x040, 0x048, 0x050,
                      0x058, 0x060, 0x068, 0x070, 0x088, 0x08C, 0x09C, 0x0A0,
                      0x0A4, 0x0A8, 0x0AC, 0x0B0, 0x0B4, 0x0B8, 0x0C4, 0x0C8,
                      0x0CC, 0x0D8, 0x0E0, 0x0E8)

POST_LO, POST_HI = 0x46240, 0x46350

# 程序角色 -> 记录内字段偏移(见 mmtr_model.REC_OFF_*)
# ⚠ VS 指针在 rec-0x20, 且**存两份**(rec-0x28 与 rec-0x20, 100% 相等 —— 对应两个 D3D 调用);
#    长度同样两份(+0x88/+0x8c)。旧 (0xE0,0xE8) 是误判 —— 那其实是"下一条记录"的 VS 指针对。
ROLE_FIELDS = {"PS": (0x00,), "VS": (-0x28, -0x20), "CS": (0x08,)}
# 记录内 "程序字节码大小" 字段(引擎精确校验; 详见 analysis/mmtr_record_fields.md)
REC_OFF_PS_SIZE = 0x9C
REC_OFF_VS_SIZE = (0x88, 0x8C)   # +0x88(inputLayout)/+0x8c(createVertexShader) 共用同一 VS 大小

# 绑定指针(cbuffer/sampler/纹理 的描述符+池)与计数 字段 —— 跨"布局"重指时需从 donor 同步
BINDING_FIELDS = (0x38, 0x40, 0x48, 0x50, 0x58, 0x60)
COUNT_FIELDS = (0xA4, 0xA8, 0xAC, 0xB0, 0xB4, 0xB8, 0xC4, 0xC8, 0xCC)
SYNC_FIELDS = BINDING_FIELDS + COUNT_FIELDS

# 变体记录内"指向 头/尾/blob 区"的指针字段(值 >= 插入点时需重映射)
HEADER_REC_PTR_FIELDS = (0x00, 0x08, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38, 0x40,
                         0x48, 0x50, 0x58, 0x60, 0xD8, 0xE0, 0xE8, -0x28, -0x20)
# 名称池字段(16B 条目: [name_off(u64)][hash(u32)][0])
POOL_FIELDS = (0x40, 0x50, 0x60)


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def _ascii(b, off, n=64):
    if not (0 <= off < len(b)):
        return None
    e = off
    while e < len(b) and b[e] != 0 and e - off < n:
        e += 1
    return b[off:e].decode("latin1", "replace")


def content_positions():
    """产出 [0, SKELETON_HI) 内所有"内容字节"的绝对偏移(生成器)。"""
    for base in HEAD_CONTENT_U32:
        for t in range(4):
            yield base + t
    for base in GAP_CONTENT_U32:
        for t in range(4):
            yield base + t
    for i in range(PT_N):
        ent = PT_LO + i * PT_SIZE
        for fo in PT_CONTENT_FIELDS:
            for t in range(4):
                yield ent + fo + t
    for i in range(REC_N):
        ent = REC_LO + i * REC_SIZE
        for fo in REC_CONTENT_FIELDS:
            for t in range(4):
                yield ent + fo + t


def content_count():
    return 4 * len(HEAD_CONTENT_U32) + 4 * len(GAP_CONTENT_U32) \
        + PT_N * 4 * len(PT_CONTENT_FIELDS) + REC_N * 4 * len(REC_CONTENT_FIELDS)


def record_counts(ps_res, vs_res):
    """由 (PS, VS) 的 RDEF 资源桶计算记录的计数/打包字段(2026-09-24 逆向确证, 全样本 100%)。

    ps_res/vs_res: `rdef.rdef_resources()` 的返回值 (cb,smp,srv) 集合 或 None。
    口径: 按 RDEF **bound-resource 的 type** 分桶(0=cb/3=smp/其余=SRV), 名字**并集**(VS∪PS)。
    详见 analysis/mmtr_record_fields.md。返回各字段应填值(键见实现)。
    """
    pcb, psmp, psrv = ps_res or (set(), set(), set())
    vcb, vsmp, vsrv = vs_res or (set(), set(), set())
    n_cb_ps = len(pcb)
    n_smp = len(psmp | vsmp)
    b4, b8 = len(vsrv), len(psrv)
    a8 = len(vcb)
    c6 = len(vcb | pcb)
    return {"a8": a8, "ac_low": n_cb_ps, "b0": n_smp << 16, "b4": b4, "b8": b8,
            "a4": a8 + n_cb_ps + n_smp + b4 + b8, "cc": len(vsrv | psrv),
            "c6": c6, "c7": n_smp, "c4": (n_smp << 24) | (c6 << 16)}


class MmtrTemplate:
    """版本骨架: 内容位置清零后的 [0x00,0x46350)。同一版本应逐字节一致。"""

    def __init__(self, data):
        if data[:4] != b"SDF\x00":
            raise ValueError("not an SDF/mmtr file")
        if len(data) < SKELETON_HI:
            raise ValueError(f"file too small for skeleton (<0x{SKELETON_HI:x})")
        self.raw = bytes(data[:SKELETON_HI])
        sk = bytearray(self.raw)
        for p in content_positions():
            sk[p] = 0
        self.skeleton = bytes(sk)

    @classmethod
    def load(cls, path):
        return cls(open(path, "rb").read())


def extract_skeleton(data):
    """导出版本骨架(纯骨架 bytes)。"""
    return MmtrTemplate(data).skeleton


def new_from_template(template: bytes) -> bytes:
    """从模板构造新 mmtr 的 bytes(统一入口)。

    现状: 返回 template 的逐字节副本(克隆) —— 即“基于该模板的新文件”。
    说明: 因 B2(记录内容字段全推导)受阻, “完全凭空合成头部内容”暂不可行 ⇒ 采用
          “模板 + 移植 + 编辑”。本函数是“从模板构建”的**统一入口**: 将来把可重构的部分
          (骨架 / 尾段 L4 / 已建模的记录字段)改为按规格生成、未摸清的字段照抄模板,
          调用方(GUI/CLI)无需改动。
    """
    return bytes(template)


def compose(template, tail_model, blobs: bytes) -> bytes:
    """B1 容器重装: 骨架(head) + 结构化 L4(tail_model.encode) + blob 区 -> 完整 mmtr bytes。

    template: MmtrTemplate(提供 head [0,0x46350)); tail_model: mmtr_tail.TailModel;
    blobs: blob 区原始字节。返回完整文件(头部 blob_start 已写)。
    注: head 的**内容字段**仍来自 template(真正“从0”需 B2 按规格重建); 复现原文件时 template=原文件。
    """
    head = bytearray(template.raw)
    tail = tail_model.encode()
    struct.pack_into("<I", head, 0x08, SKELETON_HI + len(tail))
    return bytes(head) + tail + bytes(blobs)


class MmtrImage:
    """最小装配器: 以某模板的 [0,0x46350) 为底, 携带尾段与 blob 区。

    from_bytes(x).to_bytes() == x  (round-trip 闸门)。
    """

    def __init__(self, template=None, buf=None):
        if buf is not None:
            self.buf = bytearray(buf)
        elif template is not None:
            self.buf = bytearray(template.raw)
        else:
            raise ValueError("need template or buf")
        self.tail = b""      # [0x46350, blob_start)
        self.blobs = b""     # [blob_start, EOF)

    @classmethod
    def from_bytes(cls, data):
        img = cls(buf=data[:SKELETON_HI])
        bs = _u32(data, 8)
        img.tail = bytes(data[SKELETON_HI:bs])
        img.blobs = bytes(data[bs:])
        return img

    def to_bytes(self):
        head = bytearray(self.buf)
        struct.pack_into("<I", head, 0x08, SKELETON_HI + len(self.tail))
        return bytes(head) + bytes(self.tail) + bytes(self.blobs)

    @property
    def blob_start(self):
        return SKELETON_HI + len(self.tail)

    # ---- 记录字段 ----
    def rec_field(self, slot, fo):
        return _u32(self.buf, REC_LO + slot * REC_SIZE + fo)

    def set_rec_field(self, slot, fo, v):
        struct.pack_into("<I", self.buf, REC_LO + slot * REC_SIZE + fo, v)

    def set_program(self, slot, role, blob_off, size=None, recount=False):
        """把某槽的某角色程序指针指向 blob_off; size 给定则同步该角色的大小字段
        (PS-> +0x9c; VS-> +0x88 与 +0x8c)。recount=True 时另按 RDEF 重算计数/打包字段。"""
        for fo in ROLE_FIELDS[role]:
            self.set_rec_field(slot, fo, blob_off)
        if size is not None:
            if role == "PS":
                self.set_rec_field(slot, REC_OFF_PS_SIZE, size)
            elif role == "VS":
                for fo in REC_OFF_VS_SIZE:
                    self.set_rec_field(slot, fo, size)
        if recount:
            self.recount_slot(slot)

    def blob_sizes(self):
        """{blob 偏移: 大小}(扫描 blob 区)。"""
        out = {}
        b = self.blobs
        off = self.blob_start
        i = 0
        while i + 4 <= len(b) and b[i:i + 4] == b"DXBC":
            sz = _u32(b, i + 4 * 6)  # +0x18=size
            out[off] = sz
            off += sz
            i += sz
        return out

    def blob_at(self, off):
        """取 blob 区里绝对偏移 off 处的 blob bytes; 非 blob 起点返回 None。"""
        if off < self.blob_start:
            return None
        i = off - self.blob_start
        b = self.blobs
        if i + 28 > len(b) or b[i:i + 4] != b"DXBC":
            return None
        return bytes(b[i:i + _u32(b, i + 24)])

    def recount_slot(self, slot, tables=True):
        """按该槽 PS/VS 的 RDEF 重算并写入"计数/打包字段"(2026-09-24 逆向确证)。

        写 `+0xa8`/`+0xac`(仅低16)/`+0xb0`/`+0xb4`/`+0xb8`/`+0xa4`/`+0xc4`(含 `+0xc6`/`+0xc7`)。
        tables=True 时另写"表条数字节" `+0xcc`(仅当表3 布局与新程序一致时正确)。
        返回计算结果 dict; 两程序都取不到(无 RDEF)时返回 None(不写)。
        注: 这是"从0合成/跨布局"的写入口; 同布局装配时结果应与原件一致, 可用于自检。
        """
        ps = self.rec_field(slot, 0x00)
        vs = self.rec_field(slot, -0x20)
        pres = rdef_resources(self.blob_at(ps)) if ps else None
        vres = rdef_resources(self.blob_at(vs)) if vs else None
        if pres is None and vres is None:
            return None
        c = record_counts(pres, vres)
        self.set_rec_field(slot, 0xA8, c["a8"])
        cur = self.rec_field(slot, 0xAC)
        self.set_rec_field(slot, 0xAC, (cur & 0xFFFF0000) | (c["ac_low"] & 0xFFFF))
        self.set_rec_field(slot, 0xB0, c["b0"])
        self.set_rec_field(slot, 0xB4, c["b4"])
        self.set_rec_field(slot, 0xB8, c["b8"])
        self.set_rec_field(slot, 0xA4, c["a4"])
        self.set_rec_field(slot, 0xC4, c["c4"])
        if tables:
            self.buf[REC_LO + slot * REC_SIZE + 0xCC] = c["cc"] & 0xFF
        return c

    # ---- 程序指针批量编辑 ----
    def slots_using(self, blob_off, role="PS"):
        """返回程序指针指向 blob_off 的槽下标列表。"""
        return [s for s in range(REC_N)
                if any(self.rec_field(s, fo) == blob_off for fo in ROLE_FIELDS[role])]

    def repoint_program(self, src_off, dst_off, role="PS", size=None):
        """把所有该角色指向 src_off 的槽改为指向 dst_off; 返回改动槽数。

        注: 目标须与源"资源布局同构"(否则槽的绑定组会对不上)。
        """
        n = 0
        for s in self.slots_using(src_off, role):
            self.set_program(s, role, dst_off, size=size)
            n += 1
        return n

    # ---- 绑定组/计数 装配 ----
    def sync_binding_from(self, dst_slot, src_slot, role="PS", blob_off=None, size=None,
                          recount=False):
        """从 src_slot 复制 绑定指针 + 计数 到 dst_slot(可选同时重指程序/同步大小)。

        用途: 把某槽改成使用"另一个槽所用的程序"时, 其绑定组与计数须跟随该程序
        (跨"资源布局"替换的关键)。src_slot 通常是"原生使用目标程序"的槽(donor)。
        """
        for fo in SYNC_FIELDS:
            self.set_rec_field(dst_slot, fo, self.rec_field(src_slot, fo))
        if blob_off is not None:
            self.set_program(dst_slot, role, blob_off, size=size, recount=recount)
        elif recount:
            self.recount_slot(dst_slot)

    def repoint_program_with_binding(self, src_off, dst_idx, model, role="PS"):
        """整族(角色指向 src_off 的槽)重指到 blob[dst_idx], 并从"同组的原生 donor 槽"
        同步 绑定指针 + 计数 + 大小。返回 (改动槽数, 跳过槽数)。

        组(donor)按 (desc,pool) 匹配 —— 同一技术族各前缀共用同组, 故 donor 通常存在。
        """
        dst_off = model.blob_off(dst_idx)
        dst_size = model.blob_size(dst_idx)
        donors = {}
        for s in range(REC_N):
            if any(self.rec_field(s, fo) == dst_off for fo in ROLE_FIELDS[role]):
                donors.setdefault((self.rec_field(s, 0x58), self.rec_field(s, 0x60)), s)
        n = skip = 0
        for s in self.slots_using(src_off, role):
            key = (self.rec_field(s, 0x58), self.rec_field(s, 0x60))
            d = donors.get(key)
            if d is None:
                skip += 1
                continue
            self.sync_binding_from(s, d, role=role, blob_off=dst_off, size=dst_size)
            n += 1
        return n, skip

    # ---- 尾部插入 + 绝对偏移重映射 ----
    def _remap_header(self, orig_head, pos, old_len, delta):
        """重映射 header 里"已知结构"的指针字段(旧值>=pos 的 +=delta)。

        old_len: 旧文件长度 (>= blob_start) —— 上界必须覆盖 blob 区(blob 偏移 >= blob_start)。
        """
        for slot in range(REC_N):
            base = REC_LO + slot * REC_SIZE
            for fo in HEADER_REC_PTR_FIELDS:
                v = _u32(orig_head, base + fo)
                if pos <= v < old_len:
                    struct.pack_into("<I", self.buf, base + fo, v + delta)
        for i in range(PT_N):
            base = PT_LO + i * PT_SIZE
            for fo in range(0, PT_SIZE, 4):
                v = _u32(orig_head, base + fo)
                if pos <= v < old_len:
                    struct.pack_into("<I", self.buf, base + fo, v + delta)
        v = _u32(orig_head, 0x10)
        if pos <= v < old_len:
            struct.pack_into("<I", self.buf, 0x10, v + delta)

    def _remap_pools(self, orig_head, orig_tail, new_tail, orig_all, pos, old_bs, delta):
        """重映射所有可识别名称池(16B 条目)的 name_off(旧值>=pos 的 +=delta)。"""
        pools = set()
        for slot in range(REC_N):
            base = REC_LO + slot * REC_SIZE
            for fo in POOL_FIELDS:
                p = _u32(orig_head, base + fo)
                if p:
                    pools.add(p)
        for P in pools:
            k = 0
            while True:
                oe = P + k * 16
                if oe + 16 > old_bs or oe < SKELETON_HI:
                    break
                i = oe - SKELETON_HI
                no = _u64(orig_tail, i)
                h = _u32(orig_tail, i + 8)
                if not (SKELETON_HI <= no < old_bs):
                    break
                nm = _ascii(orig_all, no)
                if not nm or ascii_hash(nm) != h:
                    break
                if no >= pos:
                    ne = oe + (delta if oe >= pos else 0)
                    struct.pack_into("<Q", new_tail, ne - SKELETON_HI, no + delta)
                k += 1

    def _remap_tables(self, orig_tail, new_tail, orig_all, pos, old_bs, delta):
        """重映射 参数表(16B: name_off@0) 与 cbuffer 绑定表(32B: name_off@0, members_off@24)。"""
        def newpos(oe):
            return oe + (delta if oe >= pos else 0)

        # 参数定义条目(16B): [name_off u32][0 u32][hash u32][size|offset u32]
        for off in range(SKELETON_HI, old_bs - 16, 4):
            i = off - SKELETON_HI
            name_off = _u32(orig_tail, i)
            if not (0x1000 <= name_off < old_bs) or _u32(orig_tail, i + 4) != 0:
                continue
            h = _u32(orig_tail, i + 8)
            nm = _ascii(orig_all, name_off)
            if not nm or ascii_hash(nm) != h or (_u32(orig_tail, i + 12) >> 16) == 0:
                continue
            if name_off >= pos:
                struct.pack_into("<I", new_tail, newpos(off) - SKELETON_HI, name_off + delta)

        # cbuffer 绑定条目(32B): name_off(u64)@0, members_off(u64)@24
        for off in range(SKELETON_HI, old_bs - 32, 4):
            i = off - SKELETON_HI
            name_off = _u64(orig_tail, i)
            if not (0x1000 <= name_off < old_bs):
                continue
            h = _u32(orig_tail, i + 8)
            nm = _ascii(orig_all, name_off)
            if not nm or ascii_hash(nm) != h:
                continue
            mo = _u64(orig_tail, i + 24)
            if not (0x1000 < mo < old_bs) or _u32(orig_tail, mo - SKELETON_HI) < 0x1000:
                continue
            ne = newpos(off) - SKELETON_HI
            if name_off >= pos:
                struct.pack_into("<Q", new_tail, ne, name_off + delta)
            if mo >= pos:
                struct.pack_into("<Q", new_tail, ne + 24, mo + delta)

    def insert_tail(self, pos, blob):
        """在尾段绝对位置 pos 处插入 blob, 并重映射所有"已建模"的绝对偏移。

        pos: 绝对偏移, 位于 [SKELETON_HI, blob_start]。
        Note: 仅重映射"已知结构"的指针(记录指针字段 / 程序表 / 头串 / 名称池 name_off);
             若尾段存在未建模的内部指针, 插入会破坏它(用"语义等价"验证自查)。
        """
        delta = len(blob)
        orig_head = bytes(self.buf)
        orig_tail = bytes(self.tail)
        orig_all = orig_head + orig_tail
        old_bs = len(orig_all)
        if not (SKELETON_HI <= pos <= old_bs):
            raise ValueError(f"pos 0x{pos:x} 不在尾段 [0x{SKELETON_HI:x},0x{old_bs:x}]")
        t = pos - SKELETON_HI
        new_tail = bytearray(orig_tail[:t] + bytes(blob) + orig_tail[t:])
        old_len = old_bs + len(self.blobs)
        self._remap_header(orig_head, pos, old_len, delta)
        self._remap_pools(orig_head, orig_tail, new_tail, orig_all, pos, old_bs, delta)
        self._remap_tables(orig_tail, new_tail, orig_all, pos, old_bs, delta)
        self.tail = bytes(new_tail)
