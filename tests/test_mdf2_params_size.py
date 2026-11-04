#!/usr/bin/env python3
"""回归: mdf2 的参数字节块必须按 16 字节对齐(与 shader 的 UserMaterial cbuffer 一致)。

vanilla mdf2 的 prop_block_size 总是 16 的倍数(例如 12 个参数用 72 字节、
块大小写 80)。旧 writer 写紧凑值(76), 于是 REasy 按 ABI 校验时报:

    The selected MDF allocates 76 bytes, but the native material buffer declares 80.

游戏能跑(多出的 4 字节是 cbuffer 尾部 padding), 但第三方工具会拒绝绑定,
并把 "找到但绑定失败" 误报成 "找不到使用此 MMTR 的 MDF"。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib.mdf2 import Material, Mdf2  # noqa: E402


def _material():
    mat = Material()
    mat.name = "ParamSize"
    mat.mmtr_path = "shaders/ParamSize.mmtr"
    mat.shader_type = 0
    mat.flags = 0
    # 布局: 0,4,8,16(float3),32(float4),48,52,56,60,64(float3)
    # 结束于 76 ⇒ 16 字节对齐后应为 80。
    for name, values in (
        ("A", [1.0]), ("B", [2.0]), ("C", [3.0]),
        ("D", [1.0, 2.0, 3.0]),
        ("E", [1.0, 2.0, 3.0, 4.0]),
        ("F", [5.0]), ("G", [6.0]), ("H", [7.0]), ("I", [8.0]),
        ("J", [9.0, 10.0, 11.0]),
    ):
        type_name = "float" if len(values) == 1 else "float%d" % len(values)
        mat.add_parameter(name, type_name, values)
    return mat


def _already_aligned():
    mat = Material()
    mat.name = "Aligned"
    mat.mmtr_path = "shaders/Aligned.mmtr"
    mat.shader_type = 0
    mat.flags = 0
    for name in ("A", "B", "C", "D"):
        mat.add_parameter(name, "float", [1.0])
    return mat


mdf = Mdf2()
mdf.file_version = 1
mdf.game_version = 10
mdf.materials.extend((_material(), _already_aligned()))

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "params.mdf2.10")
    mdf.save(path)
    loaded = Mdf2.load(path)
    sized, aligned = loaded.materials
    assert sized.prop_block_size == 80, sized.prop_block_size
    assert [p.data_offset for p in sized.properties] == [0, 4, 8, 16, 32, 48, 52, 56, 60, 64], [
        p.data_offset for p in sized.properties
    ]
    assert [list(p.values) for p in sized.properties][-1] == [9.0, 10.0, 11.0]
    assert aligned.prop_block_size == 16, aligned.prop_block_size
    for mat in loaded.materials:
        assert mat.prop_block_size % 16 == 0, (mat.name, mat.prop_block_size)

print("PASS test_mdf2_params_size: 参数字节块按 16 字节对齐 (76 -> 80)")
