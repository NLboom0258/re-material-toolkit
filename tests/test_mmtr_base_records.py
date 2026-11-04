#!/usr/bin/env python3
"""回归: 前 5 条基础程序记录的 stage 指针/大小必须成对。

生成时 pt.bin 只重定位指针；找不到对应 blob 时指针写 0、大小字段留下，
会产生"有大小、没指针"的记录 —— 游戏能跑，但 REasy 等工具会拒读
（"<name> has incomplete vertex pointer/size"）。
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mmtr_build as MB  # noqa: E402
from material_toolkit.lib import mmtr_nogen as NG  # noqa: E402
from material_toolkit.lib import material_pass as MP  # noqa: E402


def pairs(buf):
    """[(记录下标, 指针偏移, 指针, 大小)]，覆盖 5 条基础记录的全部 stage。"""
    out = []
    for i in range(MB.BASE_REC_N):
        o = MB.BASE_REC_LO + i * MB.BASE_REC_SIZE
        for po, so in MB.BASE_STAGE_FIELDS:
            ptr = struct.unpack_from("<Q", buf, o + po)[0]
            size = struct.unpack_from("<I", buf, o + so)[0]
            out.append((i, po, ptr, size))
    return out


def assert_paired(buf, where):
    for i, po, ptr, size in pairs(buf):
        assert (ptr == 0) == (size == 0), (where, i, hex(po), hex(ptr), hex(size))


def w64(buf, off, v):
    struct.pack_into("<Q", buf, off, v)


def w32(buf, off, v):
    struct.pack_into("<I", buf, off, v)


# ---- 1) 合成缓冲: 只修不成对的，成对的原样保留 ----
buf = bytearray(MB.BASE_REC_LO + MB.BASE_REC_N * MB.BASE_REC_SIZE)
# 记录 0: 顶点成对(应保留)
o0 = MB.BASE_REC_LO + 0 * MB.BASE_REC_SIZE
w64(buf, o0 + 0x08, 0x1000)
w64(buf, o0 + 0x10, 0x1000)
w32(buf, o0 + 0xB8, 0x100)
w32(buf, o0 + 0xBC, 0x100)
# 记录 1: 全 0(应保留)
# 记录 2: 有大小没指针(应清零)
o2 = MB.BASE_REC_LO + 2 * MB.BASE_REC_SIZE
w32(buf, o2 + 0xB8, 0x40)
w32(buf, o2 + 0xBC, 0x40)
# 记录 3: 像素指针有、大小 0(应成对清零)
o3 = MB.BASE_REC_LO + 3 * MB.BASE_REC_SIZE
w64(buf, o3 + 0x30, 0x2000)
# 记录 4: 顶点两份指针只写了一份(应成对清零)
o4 = MB.BASE_REC_LO + 4 * MB.BASE_REC_SIZE
w64(buf, o4 + 0x08, 0x3000)
w32(buf, o4 + 0xB8, 0x50)
w32(buf, o4 + 0xBC, 0x50)

fixed_buf, fixed = MB.normalize_base_records(bytes(buf))
assert_paired(fixed_buf, "synthetic")
assert (0, "vertex") not in fixed, fixed
assert (2, "vertex") in fixed, fixed
assert (3, "stage@0x30") in fixed, fixed
assert (4, "vertex") in fixed, fixed
# 成对的那条没被动
assert struct.unpack_from("<Q", fixed_buf, o0 + 0x08)[0] == 0x1000
assert struct.unpack_from("<I", fixed_buf, o0 + 0xBC)[0] == 0x100
# 记录 2/3/4 都被清成 0
assert struct.unpack_from("<I", fixed_buf, o2 + 0xBC)[0] == 0
assert struct.unpack_from("<Q", fixed_buf, o3 + 0x30)[0] == 0
assert struct.unpack_from("<Q", fixed_buf, o4 + 0x08)[0] == 0
assert struct.unpack_from("<I", fixed_buf, o4 + 0xBC)[0] == 0

# ---- 2) 真实生成: 默认材质的所有基础记录也必须成对 ----
data, rep = NG.build(MP.default_material("deferred_std"), "Deferred", "deferred_std")
assert data[:4] == b"SDF\0", data[:4]
assert isinstance(rep.get("base_fixed"), list), rep.get("base_fixed")
assert_paired(data, "default build")
print("base_fixed (default build):", rep["base_fixed"])

print("PASS test_mmtr_base_records: 指针/大小成对(合成 + 真实生成)")
