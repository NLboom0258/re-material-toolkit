#!/usr/bin/env python3
"""回归: mdf2 ↔ mmtr 参数同步的顺序 / 类型变化。

覆盖：
  1. 新参数插入到 mmtr 中间位置（不能追加到末尾）
  2. 同名参数类型（float 个数）变化 → 按新类型重设
  3. prune=True 删除多余参数；prune=False 保留在末尾
  4. 未变的参数保留原值
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib.mdf2 import Mdf2            # noqa: E402
from material_toolkit.lib.sync import sync_mdf2       # noqa: E402


class Member:
    def __init__(self, name, offset, size):
        self.name, self.offset, self.size = name, offset, size


class StubMmtr:
    def __init__(self, members):
        self._members = members

    def cbuffer_members(self, cbuffer):
        if cbuffer != "UserMaterial":
            raise ValueError(cbuffer)
        return self._members


doc = Mdf2()
mat = doc.add_material("Leg", "x.mmtr")
mat.add_parameter("A", "float", [1.0])
mat.add_parameter("Test4", "float", [4.0])

# 1) 中间插入 Test3 → 顺序必须跟 mmtr
added, pruned, _ = sync_mdf2(
    doc, StubMmtr([Member("A", 0, 4), Member("Test3", 4, 4), Member("Test4", 8, 4)]),
    prune=True)
assert added == 1 and pruned == 0, (added, pruned)
assert [p.name for p in mat.properties] == ["A", "Test3", "Test4"]
assert mat.get_parameter("A").values == [1.0]
assert mat.get_parameter("Test3").values == [0.0]

# 2) 同名参数类型变化 float → float3 → 重设为 3 个 0
added, pruned, _ = sync_mdf2(
    doc, StubMmtr([Member("A", 0, 4), Member("Test3", 4, 12), Member("Test4", 16, 4)]),
    prune=True)
p = mat.get_parameter("Test3")
assert added == 0 and pruned == 0
assert len(p.values) == 3 and p.type == "float3", (p.values, p.type)
assert p.cb_offset == 4

# 3) prune=False 保留 mmtr 里没有的参数（接在末尾）
mat.add_parameter("Extra", "float", [7.0])
sync_mdf2(doc, StubMmtr([Member("A", 0, 4), Member("Test3", 4, 12),
                        Member("Test4", 16, 4)]), prune=False)
assert [p.name for p in mat.properties] == ["A", "Test3", "Test4", "Extra"]
assert mat.get_parameter("Extra").values == [7.0]

# 4) prune=True 删除它
added, pruned, _ = sync_mdf2(
    doc, StubMmtr([Member("A", 0, 4), Member("Test3", 4, 12),
                   Member("Test4", 16, 4)]), prune=True)
assert pruned == 1
assert [p.name for p in mat.properties] == ["A", "Test3", "Test4"]

print("test_sync_order: OK")
