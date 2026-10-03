#!/usr/bin/env python3
"""回归: 材质“附加内容”(per-pass) —— 结构体/helper 拼接 + 报错 + 序列化 + GUI 页。

附加内容 = 拼在材质源之前的 HLSL(struct/typedef/helper 函数); 模板**不主动使用**,
由用户在入口函数里调用; 可引用接口作用域内的引擎资源/参数/贴图。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mmtr_nogen as NG  # noqa: E402
from material_toolkit.lib import material_pass as MP  # noqa: E402
from material_toolkit.lib import material_asset as MA  # noqa: E402

# 1) 默认材质不变
src0 = MP.default_material("deferred_std")
d0, _r0 = NG.build(src0, "Deferred", "deferred_std")
assert len(d0) == 592987, ("默认材质大小变了", len(d0))

EXTRA = (
    "struct MyLight { float3 dir; float3 color; };\n"
    "float3 MyLighting(MyLight lt, float3 n) { return lt.color * saturate(dot(n, lt.dir)); }\n"
)
SRC_USE = (
    "void MaterialMain(in MaterialInput mi, out MaterialOutput m)\n{\n"
    "    MyLight lt; lt.dir = float3(0,1,0); lt.color = float3(1,0.5,0.2);\n"
    "    m.BaseColor = MyLighting(lt, float3(0,1,0));\n"
    "    m.Metallic = 0; m.Roughness = 0.5; m.NormalTS = float3(0,0,1);\n"
    "    m.Emissive = 0; m.Occlusion = 1; m.Translucency = 0;\n}\n"
    "void MaterialDepth(DepthInput di, inout bool discardPixel) { discardPixel = false; }\n"
    "float3 MaterialVertex(VertexInput vi) { return float3(0,0,0); }\n"
)

# 2) 有附加内容 -> 可编译(用到 struct + helper)
d1, _r1 = NG.build(SRC_USE, "Deferred", "deferred_std", extra={"main": EXTRA})
assert d1, "带附加内容应生成成功"

# 3) 无附加内容 -> 报错(证明附加内容确实被拼接进编译)
try:
    NG.build(SRC_USE, "Deferred", "deferred_std")
    raise AssertionError("不注入附加内容时应编译失败")
except ValueError:
    pass

# 4) 附加内容自身语法错 -> 报错
try:
    NG.build(src0, "Deferred", "deferred_std", extra={"main": "struct Bad { float3 x\n"})
    raise AssertionError("附加内容语法错时应编译失败")
except ValueError:
    pass

# 5) material_asset 序列化 round-trip
a = MA.MaterialAsset(shading_source="x", shading_extra={"main": EXTRA})
assert a.extra_for("main") == EXTRA and a.extra_for("depth") == ""
b = MA.MaterialAsset.from_dict(a.to_dict())
assert b.shading_extra["main"] == EXTRA
assert set(b.shading_extra) == {"main", "depth", "vertex"}

# 6) GUI: 「附加内容」页存在(main/depth/vertex 三个编辑器)
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])
from material_studio import app as A  # noqa: E402
p = A.MaterialSystemPanel()
assert "附加内容" in [p.tabs.tabText(i) for i in range(p.tabs.count())]
assert set(p.ed_extra) == {"main", "depth", "vertex"}

print("PASS test_material_extra: 附加内容 拼接/报错/序列化/GUI 页 均 OK")
