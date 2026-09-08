#!/usr/bin/env python3
"""mmtr(RE Engine 主材质 / SDF 容器)头部解析 + 参数定义表读取。

头部布局(env_emissive 实测):
  文件头(0x14) -> 变体目录 -> 资源绑定列表 -> cbuffer 绑定列表 -> 参数定义表 -> 字符串池 -> blob 区

参数定义条目(16B/条, cbuffer 成员):
  [name_offset u32][0 u32][hash u32][type=字节大小 u16 | cbuffer内偏移 u16]
  hash = murmur3(name, 0xffffffff)

cbuffer 绑定条目(32B/条)含成员表偏移(指向该 cbuffer 的成员定义段)。
"""
import struct
try:
    from .hashes import ascii_hash
except ImportError:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hashes import ascii_hash


def _u16(b, o): return struct.unpack_from("<H", b, o)[0]
def _u32(b, o): return struct.unpack_from("<I", b, o)[0]


def read_ascii(data: bytes, off: int) -> str:
    end = off
    while end < len(data) and data[end] != 0:
        end += 1
    return data[off:end].decode("latin1", errors="replace")


class Param:
    """cbuffer 成员定义条目。"""
    def __init__(self, entry_off, name_off, h, size, offset):
        self.entry_off = entry_off
        self.name_off = name_off
        self.hash = h
        self.size = size          # 字节大小(4=float, 16=float4, ...)
        self.offset = offset      # cbuffer 内字节偏移
        self.name = ""

    def __repr__(self):
        return f"Param({self.name!r} size={self.size} off=0x{self.offset:x} @0x{self.entry_off:x})"


class Mmtr:
    def __init__(self):
        self.data = b""
        self.blob_start = 0
        self.params = []          # 所有参数定义条目(按文件顺序)
        self.string_pool_lo = 0
        self.string_pool_hi = 0

    @classmethod
    def load(cls, path: str) -> "Mmtr":
        m = cls()
        m.data = open(path, "rb").read()
        if m.data[:4] != b"SDF\0":
            raise ValueError("not an SDF/mmtr file")
        m.blob_start = _u32(m.data, 8)
        # 字符串池范围: blob 区之前的可打印 ASCII 串
        import re
        lo = hi = None
        for mt in re.finditer(rb"[\x20-\x7e]{3,}", m.data[:m.blob_start]):
            if lo is None:
                lo = mt.start()
            hi = mt.end()
        m.string_pool_lo, m.string_pool_hi = (lo or 0), (hi or 0)

        # 扫描参数定义条目: name 可解析 + 第2 u32==0 + hash==murmur3(name) + type(高16)合理
        data = m.data
        for p in range(0, m.blob_start - 16, 4):
            name_off, z, h, meta = struct.unpack_from("<IIII", data, p)
            if z != 0 or not (0x1000 <= name_off < m.blob_start):
                continue
            name = read_ascii(data, name_off)
            if not name or ascii_hash(name) != h:
                continue
            size = meta >> 16
            offset = meta & 0xFFFF
            if size == 0:          # 资源绑定条目(type=0),跳过
                continue
            pr = Param(p, name_off, h, size, offset)
            pr.name = name
            m.params.append(pr)
        return m

    def _scan_cbuffer_entries(self, cbuffer_name):
        """扫描 cbuffer 绑定条目(32B/条: name_off(8) hash(4) 0(4) size(4) count(4) members_off(8))。

        返回 [[pos, members_off, count, size], ...](同一 cbuffer 每变体一条,内容相同)。
        """
        data = self.data
        blob_start = self.blob_start
        pool_lo, pool_hi = self.string_pool_lo, self.string_pool_hi
        cb_entries = []
        for p in range(0, blob_start - 32, 4):
            name_off = struct.unpack_from("<Q", data, p)[0]
            if not (pool_lo <= name_off < pool_hi):
                continue
            nm = read_ascii(data, name_off)
            if nm != cbuffer_name:
                continue
            if ascii_hash(nm) != struct.unpack_from("<I", data, p + 8)[0]:
                continue
            size_v = struct.unpack_from("<I", data, p + 16)[0]
            count = struct.unpack_from("<I", data, p + 20)[0]
            members_off = struct.unpack_from("<Q", data, p + 24)[0]
            # 校验 members_off 确实指向参数表(该处应是合法参数条目:name 为文件内偏移)
            if not (0x1000 < members_off < blob_start):
                continue
            m_name = struct.unpack_from("<I", data, members_off)[0]
            if not (0x1000 < m_name < blob_start):
                continue
            cb_entries.append([p, members_off, count, size_v])
        return cb_entries

    def cbuffer_members(self, cbuffer_name):
        """返回该 cbuffer 的成员参数列表(按成员表顺序)。"""
        entries = self._scan_cbuffer_entries(cbuffer_name)
        if not entries:
            return []
        _, members_off, count, _ = entries[0]
        lo, hi = members_off, members_off + count * 16
        return [pr for pr in self.params if lo <= pr.entry_off < hi]

    def add_cbuffer_param(self, cbuffer_name, param_name, size, offset):
        """在指定 cbuffer 成员表末尾插入新参数,返回新的完整文件 bytes。

        头部变长插入会同步重映射三类绝对偏移:
          - 字符串池引用(pool_lo..blob_start): +16(字符串池后移)
          - blob 起点引用(>=blob_start): +16+len(name_bytes)(字符串池又变长)
          - 参数表后半偏移(insert_pos..pool_lo): +16
        并更新 cbuffer 绑定条目的 count/size 与文件头 blob_start。
        """
        data = self.data
        blob_start = self.blob_start
        pool_lo, pool_hi = self.string_pool_lo, self.string_pool_hi

        # 1. 定位 cbuffer 绑定条目
        cb_entries = self._scan_cbuffer_entries(cbuffer_name)
        if not cb_entries:
            raise ValueError(f"cbuffer {cbuffer_name!r} not found")
        members_off = cb_entries[0][1]
        count = cb_entries[0][2]
        insert_pos = members_off + count * 16

        name_bytes = param_name.encode("ascii") + b"\x00"
        name_delta = len(name_bytes)
        head_delta = 16  # 参数条目插入长度

        # 字符串池起点 = 参数表末尾(最后一条参数条目之后)
        pool_start = max(pr.entry_off for pr in self.params) + 16
        # 收集所有字符串起点(旧值)与所有 blob 起点(旧值)
        str_starts = set()
        q = pool_start
        while q < blob_start:
            str_starts.add(q)
            while q < blob_start and data[q] != 0:
                q += 1
            q += 1
        blob_starts = set()
        off = blob_start
        while off + 4 <= len(data) and data[off:off + 4] == b"DXBC":
            blob_starts.add(off)
            off += struct.unpack_from("<I", data, off + 24)[0]
        # 参数段起点: 扫描"所有" cbuffer 绑定条目的 members_off(插入点之后的需 +16)
        seg_starts = set()
        for p in range(0, blob_start - 32, 4):
            mo = struct.unpack_from("<Q", data, p + 24)[0]
            if not (0x1000 < mo < pool_start):
                continue
            mn = struct.unpack_from("<I", data, mo)[0]
            if 0x1000 < mn < blob_start:
                seg_starts.add(mo)

        entry = struct.pack("<IIII", 0, 0, ascii_hash(param_name), (size << 16) | offset)

        # 2. 重映射函数:
        #   - blob 起点(精确集合) -> +16+名字长度
        #   - 头部内偏移 [insert_pos, blob_start) -> +16(含字符串池/参数表后半/各表引用)
        #   注:hash 值通常远大于 blob_start,不会落入头部偏移区间,不会误伤
        def remap(v):
            if v in blob_starts:
                return v + head_delta + name_delta
            if insert_pos <= v < blob_start:
                return v + head_delta
            return v

        out = bytearray()
        # A: 插入点之前(含旧 blob_start 字段暂留,最后改); 从 0x0c 开始(跳过魔数/版本/blob_start)
        head_a = bytearray(data[:insert_pos])
        for i in range(0x0C, len(head_a) - 3, 4):
            v = struct.unpack_from("<I", head_a, i)[0]
            nv = remap(v)
            if nv != v:
                struct.pack_into("<I", head_a, i, nv)
        out += head_a
        # B: 新参数条目(name_off 稍后回填)
        entry_off_in_out = len(out)
        out += entry
        # C: 插入点到字符串池末尾(重映射)
        head_c = bytearray(data[insert_pos:blob_start])
        for i in range(0, len(head_c) - 3, 4):
            v = struct.unpack_from("<I", head_c, i)[0]
            nv = remap(v)
            if nv != v:
                struct.pack_into("<I", head_c, i, nv)
        out += head_c
        # D: 新名字
        new_name_off = len(out)
        out += name_bytes
        # E: blob 区(内容不变,偏移已由头部的 remap 处理)
        out += data[blob_start:]

        # 3. 回填新条目的 name_off
        struct.pack_into("<I", out, entry_off_in_out, new_name_off)
        # 4. 更新 cbuffer 绑定条目 count/size(cbuffer 大小必须对齐 16 字节,否则 D3D CreateBuffer 失败)
        for (p, mo, cnt, sz) in cb_entries:
            new_sz = (sz + size + 15) & ~15
            struct.pack_into("<I", out, p + 16, new_sz)
            struct.pack_into("<I", out, p + 20, cnt + 1)
        # 5. 更新文件头 blob_start
        struct.pack_into("<I", out, 8, blob_start + head_delta + name_delta)
        return bytes(out)

    def dump(self):
        print(f"mmtr blob_start=0x{self.blob_start:x} "
              f"string_pool=0x{self.string_pool_lo:x}..0x{self.string_pool_hi:x} "
              f"params={len(self.params)}")
        for pr in self.params:
            print(f"  {pr.name:36s} size={pr.size:3d} off=0x{pr.offset:04x} @0x{pr.entry_off:x}")


if __name__ == "__main__":
    import sys
    Mmtr.load(sys.argv[1]).dump()
