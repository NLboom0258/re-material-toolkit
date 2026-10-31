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
              prune: bool = False, materials=None):
    """让 mdf2 的指定材质与 mmtr 的指定 cbuffer 成员一致(原地修改 mdf2)。

    materials：None = 全部材质（旧行为）；str = 单个材质名；list/tuple/set = 这几个。
    为什么需要它：换母材质时通常只换**一个槽**，如果拿该 mmtr 去对齐全部材质，
    其它槽的参数集会被错误重写（它们用的是另一个 mmtr）。

    返回 (added, pruned, report):
      added  = 新增参数条目总数;
      pruned = 删除参数条目总数;
      report = [(材质名, [新增参数名...]), ...]
    """
    members = mmtr.cbuffer_members(cbuffer)
    if not members:
        raise ValueError(f"cbuffer {cbuffer!r} not found in mmtr")
    names_in_mmtr = {m.name for m in members}

    targets = mdf2.materials
    if materials is not None:
        want = {materials} if isinstance(materials, str) else set(materials)
        targets = [m for m in mdf2.materials if m.name in want]
        missing = want - {m.name for m in mdf2.materials}
        if missing:
            # 早失败：静默什么都不做最容易让人误判“同步成功”
            raise ValueError("material(s) not found in mdf2: %s" % ", ".join(sorted(missing)))

    added = pruned = 0
    report = []
    for mat in targets:
        old = {pr.name: pr for pr in mat.properties}
        # 1. 可选: 删除 mmtr 中不存在的参数
        if prune:
            for name in list(old):
                if name not in names_in_mmtr:
                    del old[name]
                    pruned += 1
        # 2/3. 按 **mmtr 成员顺序** 重建参数表：
        #   - 已有同名参数保留对象（值不变），但 cb_offset 对齐 mmtr；
        #   - 同名但 float 个数变了（类型变了）→ 按 mmtr 重设，默认 0；
        #   - 新参数插入到 mmtr 对应位置（不能一律 append，否则顺序会错）。
        props = []
        added_here = []
        for m in members:
            n = max(1, m.size // 4)
            pr = old.get(m.name)
            if pr is None:
                pr = Property()
                pr.name = m.name
                pr.values = [0.0] * n
                pr.param_count = n
                added += 1
                added_here.append(m.name)
            elif len(pr.values) != n:
                # 类型变化：旧值对新类型没有意义，按新类型重置
                pr.values = [0.0] * n
                pr.param_count = n
            pr.param_count = len(pr.values)
            pr.cb_offset = m.offset
            props.append(pr)
        if not prune:
            # 保留 mdf2 里多出来、mmtr 没有的参数（接在末尾，内部相对顺序不变）
            for pr in mat.properties:
                if pr.name not in names_in_mmtr:
                    props.append(pr)
        mat.properties = props
        report.append((mat.name, added_here))
    return added, pruned, report
