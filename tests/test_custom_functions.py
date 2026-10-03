#!/usr/bin/env python3
"""回归: 自定义函数库 —— 参数类型策略。

- **支持**: 标量/向量/矩阵、资源、sampler、数组(元素为标量/向量)。
- **拒绝**: 结构体/自定义类型(签名里出现即拒) —— 结构体请改用「附加内容」页
  (原因: 结构体难复用, 且会使"合成调用者"无法消费其返回值/out 而被 DCE, 令阶段检查不可靠)。
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

# ---- 支持: 数组 / 矩阵 / out 数组(标量/向量) ----
CF.save("SumArr", "float SumArr(float x[4]) { return x[0] + x[1] + x[2] + x[3]; }\n")
CF.save("MulMat", "float4 MulMat(float4x4 m, float4 v) { return mul(m, v); }\n")
CF.save("OutArr", "void OutArr(float x, out float y[2]) { y[0] = x; y[1] = x * 2; }\n")
for n in ("SumArr", "MulMat", "OutArr"):
    r = CF.check(n)
    print("%-10s ps=%-5s vs=%-5s" % (n, r["ps"]["ok"], r["vs"]["ok"]))
    assert r["ps"]["ok"] and r["vs"]["ok"], (n, r)

# ---- 拒绝: 结构体(参数 / 返回) ----
CF.save("UseStruct",
         "struct L { float3 c; };\nfloat3 UseStruct(L l) { return l.c; }\n")
CF.save("RetStruct",
         "struct L { float3 c; };\nL RetStruct(float3 x) { L l; l.c = x; return l; }\n")
for n in ("UseStruct", "RetStruct"):
    r = CF.check(n)
    _e1 = (r["ps"]["err"] or "").splitlines()[0][:48] if r["ps"]["err"] else ""
    print("%-10s ps=%-5s vs=%-5s  %s" % (n, r["ps"]["ok"], r["vs"]["ok"], _e1))
    assert not r["ps"]["ok"] and not r["vs"]["ok"], (n, r)
    assert "结构体" in (r["ps"]["err"] or ""), (n, r)

# ---- 解析: 数组尺寸/类型 ----
_sig = CF.parse_signature(CF.get("SumArr"))
assert _sig["params"][0]["array"] == 4 and _sig["params"][0]["type"] == "float", _sig

print("PASS test_custom_functions: 数组/矩阵/out-数组 支持; 结构体 拒绝")
