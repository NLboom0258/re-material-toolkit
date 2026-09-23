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
ROLE_FIELDS = {"PS": (0x00,), "VS": (0xE0, 0xE8), "CS": (0x08,)}
# 记录内 "PS 大小" 字段
REC_OFF_PS_SIZE = 0x9C


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


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

    def set_program(self, slot, role, blob_off, size=None):
        """把某槽的某角色程序指针指向 blob_off; size 给定则同步该槽 PS 大小(+0x9c)。"""
        for fo in ROLE_FIELDS[role]:
            self.set_rec_field(slot, fo, blob_off)
        if size is not None and role == "PS":
            self.set_rec_field(slot, REC_OFF_PS_SIZE, size)

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
