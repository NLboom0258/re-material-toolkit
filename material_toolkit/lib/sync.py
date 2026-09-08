#!/usr/bin/env python3
"""mdf2 ↔ mmtr 同步层: 让 mdf2 的参数集合与 mmtr 的参数定义表保持一致。

核心思想: **mmtr 是接口的唯一权威**, mdf2 只负责给值。对 mdf2 每个材质:
  - mmtr 的 cbuffer 有、mdf2 无的参数 -> 新增条目(默认值 0), 值区按 mmtr offset 定位;
  - 已有参数 -> 若 mmtr 有同名成员, 用其 offset 对齐(cb_offset), 保证值区位置正确;
  - (可选 prune=True) mdf2 有、mmtr 无的参数 -> 删除。

为何值区必须按 mmtr offset 定位: 引擎按 mmtr 参数表的 offset 去 mdf2 值区取值,
若值区按"参数顺序"累加, 新增参数的位置就会与 mmtr offset 错位 -> 读到 0(黑)。
"""
try:
    from .mmtr import Mmtr
    from .mdf2 import Mdf2, Property
except ImportError:  # 直接运行本文件时
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mmtr import Mmtr
    from mdf2 import Mdf2, Property


def sync_mdf2(mdf2: Mdf2, mmtr: Mmtr, cbuffer: str = "UserMaterial",
              prune: bool = False):
    """让 mdf2 每个材质的参数集合与 mmtr 的指定 cbuffer 成员一致(原地修改 mdf2)。

    返回 (added, pruned, report):
      added  = 新增参数条目总数;
      pruned = 删除参数条目总数;
      report = [(材质名, [新增参数名...]), ...]
    """
    members = mmtr.cbuffer_members(cbuffer)
    if not members:
        raise ValueError(f"cbuffer {cbuffer!r} not found in mmtr")
    off_by_name = {p.name: p for p in members}
    names_in_mmtr = set(off_by_name)

    added = pruned = 0
    report = []
    for mat in mdf2.materials:
        # 1. 可选: 删除 mmtr 中不存在的参数
        if prune:
            keep = []
            for pr in mat.properties:
                if pr.name in names_in_mmtr:
                    keep.append(pr)
                else:
                    pruned += 1
            mat.properties = keep
        # 2. 对齐已有参数的 cb_offset(值区按 mmtr offset 定位)
        for pr in mat.properties:
            m = off_by_name.get(pr.name)
            if m is not None:
                pr.cb_offset = m.offset
        # 3. 新增 mmtr 有、mdf2 无的参数(默认值 0)
        existing = {pr.name for pr in mat.properties}
        added_here = []
        for m in members:
            if m.name in existing:
                continue
            n = max(1, m.size // 4)   # 元素数(按 float 计)
            pr = Property()
            pr.name = m.name
            pr.values = [0.0] * n
            pr.param_count = n
            pr.cb_offset = m.offset
            mat.properties.append(pr)
            added += 1
            added_here.append(m.name)
        report.append((mat.name, added_here))
    return added, pruned, report
