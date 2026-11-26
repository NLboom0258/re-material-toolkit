#!/usr/bin/env python3
"""MHWS：由 PSV0 反推记录的 per-stage 资源计数（DMC5 `recount_slot` 的等价物）。

规则（实测 ToonTest 240906211：PS 1392 + VS 2090 + CS 225 个 stage 样本，
per-stage 四类计数 3482/3482、`counts_total` 2331/2331 全中）：
    counts[type][stage] = 该 stage blob 的 PSV0 里 **space == 0** 的资源条目数
    counts_total        = 各 stage 的 cb+smp+srv+uav 之和

  - `space != 0` 的是**引擎全局绑定**，不进记录：space32 的静态采样器、
    space1/4/6 的 unbound RawBuffer/Texture2D/Texture3D（bindless 全局）。
  - PSV0 rtype -> 类别：2=cb、1=smp、3/4/5=srv、>=6=uav。

已知例外（全语料 962 个中的 41 个，均为 **250206176 的 VFX 材质**）：
PSV0 里 t32..t35 不在材质 SRV 表内（表只到 t31 + t36），计数比 PSV0 space0 少 4。
240906211 全量 100% 吻合；对例外版本/族请保留 donor 计数（不要在缩表后 recount）。

⚠ 不碰 `desc_counts`（记录 `+0xF6/0xF7/0xFC/0xFD/0xFE` 的**表项数**）：
它们是**绑定组（技术族）级**的，不能由单条记录推出（实测 cb 53%、srv 73%、smp 100%）。

⚠ **计数是“取前 N 项”，不是提示**：引擎按阶段从绑定池里取该阶段的条目（按码里的
`slot` 落寄存器）。把计数改小 = 少绑后面的条目。例如 `ATSDeferredSkinning` 的 PS 取
6 个 cb（槽 0..5），`GBufferType` 在槽 3；改成 2 就只剩 SceneInfo/EnvironmentInfo，
b3 拿不到。⇒ `recount()` 只适用于“新 blob 的资源名/槽位与保留的绑定表一致”的场合
（等价于自检），缩表后仍然能跑只是因为我们自己的 shader 不读被丢的资源。

用法：
    from material_toolkit.lib import mmtr_recount as RC
    RC.diff(data, view)                    # [不一致项]，空 = 全部吻合
    new, stats = RC.recount(data, view)    # 写回计数，返回新 bytes
"""
import collections

try:
    from . import mmtr_blobs as B
    from . import mmtr_scan as SC
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_blobs as B
    import mmtr_scan as SC

__all__ = ["STAGE_ORDER", "KINDS", "psv0_counts", "record_expected",
           "diff", "recount"]

STAGE_ORDER = ("vs", "hs", "ds", "gs", "ps", "cs")
KINDS = ("cb", "smp", "srv", "uav", "opt")     # 与记录 +0xD8 起的 5×6 矩阵一致


def _kind_of(rtype):
    if rtype == 2:
        return "cb"
    if rtype == 1:
        return "smp"
    if rtype in (3, 4, 5):
        return "srv"
    if rtype >= 6:
        return "uav"
    return None


def psv0_counts(blob):
    """一个 shader blob -> {"cb","smp","srv","uav"}（只数 space==0）。"""
    c = collections.Counter()
    p = B.parse_psv0(blob)
    for r in (p["resources"] if p else []):
        if r["space"] != 0:
            continue
        k = _kind_of(r["rtype"])
        if k:
            c[k] += 1
    return {k: c[k] for k in ("cb", "smp", "srv", "uav")}


def record_expected(data, view, entry, cache=None):
    """一条记录 -> (matrix[5][6], total)。matrix 按 KINDS × STAGE_ORDER。"""
    cache = cache if cache is not None else {}
    matrix = [[0] * len(STAGE_ORDER) for _ in KINDS]
    for si, st in enumerate(STAGE_ORDER):
        ptr, size = entry["stages"][st]
        if not ptr:
            continue
        key = (ptr, size)
        cnt = cache.get(key)
        if cnt is None:
            cnt = cache[key] = psv0_counts(data[ptr:ptr + size])
        for ki, k in enumerate(("cb", "smp", "srv", "uav")):
            matrix[ki][si] = cnt[k]
    total = sum(sum(row) for row in matrix[:4])
    return matrix, total


def _records(data, view):
    return [e for e in SC.record_entries(data, view) if e["has_program"]]


def diff(data, view):
    """[(rec_index, name, kind, stage, old, new)]；空 = 记录计数与 PSV0 完全一致。"""
    out = []
    cache = {}
    for e in _records(data, view):
        matrix, total = record_expected(data, view, e, cache)
        rec = view.rec_base + e["index"] * view.rec_stride
        for ki, k in enumerate(KINDS):
            base = rec + view.count_matrix_off + ki * len(STAGE_ORDER)
            for si, st in enumerate(STAGE_ORDER):
                old = data[base + si]
                if old != matrix[ki][si]:
                    out.append((e["index"], e["name"], k, st, old, matrix[ki][si]))
        old_total = int.from_bytes(data[rec + view.count_total_off:
                                        rec + view.count_total_off + 4], "little")
        if old_total != total:
            out.append((e["index"], e["name"], "total", "-", old_total, total))
    return out


def recount(data, view, write=True):
    """按 PSV0 重算并写回（write=False 只统计）。返回 (bytes|None, stats)。"""
    items = diff(data, view)
    stats = {"records": len(_records(data, view)), "mismatches": len(items),
             "changed_records": len({i[0] for i in items}), "written": write}
    if not write or not items:
        return (None if not write else bytes(data)), stats
    buf = bytearray(data)
    cache = {}
    for e in _records(data, view):
        matrix, total = record_expected(data, view, e, cache)
        rec = view.rec_base + e["index"] * view.rec_stride
        for ki in range(4):                       # opt 不碰
            base = rec + view.count_matrix_off + ki * len(STAGE_ORDER)
            for si in range(len(STAGE_ORDER)):
                buf[base + si] = matrix[ki][si]
        buf[rec + view.count_total_off:rec + view.count_total_off + 4] = \
            total.to_bytes(4, "little")
    return bytes(buf), stats
