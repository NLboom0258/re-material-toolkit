#!/usr/bin/env python3
"""回归: 自定义函数库 —— 数组/结构体/矩阵参数支持(编译检查合成实参)。

合成策略: 标量用字面量; 资源/采样器/结构体/矩阵/数组一律 **全局声明**(HLSL 全局默认零初始化)。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import custom_functions as CF  # noqa: E402

td = tempfile.mkdtemp(prefix="cf_arr_")
CF.FUNCS_DIR = td
CF.clear_cache()

CF.save("UseStruct",
         "struct L { float3 d; float3 c; };\n"
         "float3 UseStruct(L l) { return l.c * dot(l.d, l.d); }\n")
CF.save("SumArr",
         "float SumArr(float x[4]) { return x[0] + x[1] + x[2] + x[3]; }\n")
CF.save("SumStructArr",
         "struct L { float3 d; float3 c; };\n"
         "float3 SumStructArr(L ls[2]) { return ls[0].c + ls[1].c; }\n")
CF.save("MulMat",
         "float4 MulMat(float4x4 m, float4 v) { return mul(m, v); }\n")
CF.save("OutArr",
         "void OutArr(float x, out float y[2]) { y[0] = x; y[1] = x * 2; }\n")

for n in ("UseStruct", "SumArr", "SumStructArr", "MulMat", "OutArr"):
    r = CF.check(n)
    print("%-14s ps=%-5s vs=%-5s unsupported=%-5s"
          % (n, r["ps"]["ok"], r["vs"]["ok"], r["unsupported"]))
    assert r["ps"]["ok"] and r["vs"]["ok"], (n, r)

# 数组尺寸/类型解析
_sig = CF.parse_signature(CF.get("SumArr"))
assert _sig["params"][0]["array"] == 4 and _sig["params"][0]["type"] == "float", _sig
assert CF.parse_signature(CF.get("UseStruct"))["params"][0]["type"] == "L"

print("PASS test_custom_functions: 数组/结构体/矩阵/out-数组 参数 编译检查 OK")
