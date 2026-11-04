#!/usr/bin/env python3
"""回归: per-stage 绑定计数必须与描述符 stage 掩码一致。

DMC5 记录 +0xD4 = 总 binding 数, +0xD8..+0xF5 = 5 类 × 6 stage 的计数;
第三方工具(REasy)会按描述符 stage 掩码逐位校验。旧生成器把 sampler 一律
记到 PS、总数又漏算 UAV, 于是出现:

    DeferredSkinning sampler masks count as (1, 0, 0, 0, 3, 0),
    expected (0, 0, 0, 0, 3, 0)

游戏能跑(引擎宽容), 但文件自相矛盾。这里用"VS 采样贴图"的自定义材质
真实生成(同时覆盖 Pick 族的 UAV 总数), 防止回归。
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mmtr_info as MI  # noqa: E402
from material_toolkit.lib import mmtr_nogen as NG  # noqa: E402

_TDIR = os.path.join(ROOT, "material_toolkit", "pass_templates")
with open(os.path.join(_TDIR, "mat_default_std_custom.mmat.json"), encoding="utf-8") as f:
    _asset = json.load(f)

inputs = _asset["inputs"]
inputs["engine"]["vertex"] = ["AutomaticWrap"]   # VS 侧声明 sampler
inputs["tex"]["vertex"] = ["NormalAOMap"]        # VS 侧采样贴图
source = _asset["shading"]["source"].replace(
    "    vo.UV1           = vi.uv1;\n",
    "    vo.UV1           = vi.uv1;\n"
    "    float sdfMask = NormalAOMap.SampleLevel(AutomaticWrap, vi.uv0, 0).w;\n"
    "    // 必须让结果真正参与输出, 否则编译器 DCE 掉采样, sampler 不会进 RDEF\n"
    "    vo.NormalWS = lerp(vi.NormalWS, -vi.NormalWS, sdfMask);\n", 1)

data, rep = NG.build(source, "Deferred", "deferred_std_custom", inputs=inputs)
assert data[:4] == b"SDF\0", data[:4]

bad = MI.stage_count_mismatches(data)
assert not bad, bad[:8]

n = struct.unpack_from("<H", data, 4)[0] * struct.unpack_from("<H", data, 6)[0]
vs_sampler = sum(data[MI.REC_LO_REAL + i * MI.REC_SIZE_REAL + 0xD8 + 6] for i in range(n))
assert vs_sampler > 0, "构建未覆盖 VS sampler 情形, 回归价值不足"

print("records:", n, "VS sampler bindings:", vs_sampler, "mismatches:", len(bad))
print("PASS test_mmtr_stage_counts: per-stage 计数与描述符掩码一致")
