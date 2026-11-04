#!/usr/bin/env python3
"""回归: DMC5 的 MaterialShadingType 必须是 exe 里的 12 项(含 GUI=8)。

历史: DMC5.exe 的名字池(0x460AE80)里没有独立的 "GUI" 字面量 —— 它被链接器合并到了
别处(0x45F2B3C)。早期版本只按池内顺序推断, 于是 8 起整体错位一格:
旧表 8=GUIMesh / 9=ExpensiveTransparent / 10=Forward。

exe 的枚举注册代码(RVA 0x2C5EEC0..0x2C5F07A, 每个成员 = `mov r8d, <值>` +
`lea rdx, <名字>`)给出的真值:

    0 Standard   1 Decal   2 DecalWithMetallic   3 DecalNRMR   4 Transparent
    5 Distortion 6 PrimitiveMesh 7 Water 8 GUI 9 GUIMesh 10 ExpensiveTransparent
    11 Forward

⇒ DMC5 的 `*TransparentEX` master(眼睛/头发)在 mdf2 里用的 10 是 ExpensiveTransparent,
不是 Forward; 11 在解包语料里零使用。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib.mdf2 import (  # noqa: E402
    SHADING_TYPES, Mdf2, shading_type_name, shading_type_value,
)

EXPECTED = [
    ("Standard", 0), ("Decal", 1), ("DecalWithMetallic", 2), ("DecalNRMR", 3),
    ("Transparent", 4), ("Distortion", 5), ("PrimitiveMesh", 6), ("Water", 7),
    ("GUI", 8), ("GUIMesh", 9), ("ExpensiveTransparent", 10), ("Forward", 11),
]

assert list(SHADING_TYPES) == EXPECTED, list(SHADING_TYPES)
assert shading_type_name(8) == "GUI"
assert shading_type_name(9) == "GUIMesh"
assert shading_type_name(10) == "ExpensiveTransparent"
assert shading_type_name(11) == "Forward"
assert shading_type_value("ExpensiveTransparent") == 10
assert shading_type_value("Forward") == 11
assert shading_type_name(12).startswith("0x")   # 越界值仍回退到十六进制

# 写盘 -> 重读: 10 必须仍是 ExpensiveTransparent(值本身不变, 变的是名字)
mdf = Mdf2()
mdf.file_version = 1
mdf.game_version = 10
mdf.add_material("EyeExpensive", "MasterMaterial/Master/Character_EyeTransparentEX.mmtr",
                 shader_type=shading_type_value("ExpensiveTransparent"), flags=0)

import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "enum.mdf2.10")
    mdf.save(path)
    loaded = Mdf2.load(path)
    mat = loaded.materials[0]
    assert mat.shader_type == 10, mat.shader_type
    assert mat.shading_type_name() == "ExpensiveTransparent", mat.shading_type_name()

print("PASS test_mdf2_shading_types: MaterialShadingType 12 项, GUI=8 / ExpensiveTransparent=10")
