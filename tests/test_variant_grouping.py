#!/usr/bin/env python3
"""回归: 变体归并（DMC5 / MHWS 共用一套规则）。

背景
----
记录名 = `[标志前缀] + 技术名`，前缀来自 mdf2 的 alpha-test / two-sided：
`A` / `TS` / `Direct` 及其组合（`ADirect` / `TSDirect` / `ATSDirect`）。
DMC5 的老实现只认 5 个前缀，MHWS 又多了 `Direct` / `TSDirect`，所以统一到
`mmtr_scan.VARIANT_PREFIXES`（长前缀优先），两边共用。

`group_variants()` 再去掉前缀后按 `(ps,vs,hs,ds,gs,cs)` 程序元组归并，
并记录每组用到的前缀与“行号”（row = 记录下标 // cols）。

实测基准（2026-11-08）：
  * base_otomo_equip_gold(240906211)：914 个名字 = 206 个技术；
    前缀 A=188 / TS=188 / ATS=188 / Direct=36 / ADirect=36 /
    TSDirect=36 / ATSDirect=36 / 无=206；
  * speedtree_uber(250206176)：486 个名字，全部无前缀；
  * 行 0 的 DXIL = 1.2，其余行 = 1.6（“行”是编译档，不是渲染目标）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mmtr_blobs as MB  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

MHWS = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
OTOMO = os.path.join(MHWS, "materialshader", "variation",
                     "base_otomo_equip_gold.mmtr.240906211")
SPEEDTREE = os.path.join(MHWS, "materialshader", "master",
                         "speedtree_uber.mmtr.250206176")
DMC5 = os.path.join(r"E:\\Project\\Other", "REEngine-mmtrReverse", "assets",
                    "original", "character_default.mmtr.1808168797")


def _rec(i, name, ps=0, vs=0, cs=0, hs=0, ds=0, gs=0):
    return {"index": i, "name": name, "has_program": bool(ps or vs or cs or hs or ds or gs),
            "stages": {"sig": (0, 0), "vs": (vs, 0), "hs": (hs, 0), "ds": (ds, 0),
                       "gs": (gs, 0), "ps": (ps, 0), "cs": (cs, 0)}}


def _case_split():
    cases = {
        "ATSShadowStatic": ("ATS", "ShadowStatic"),
        "ATSDirectForwardStatic": ("ATSDirect", "ForwardStatic"),
        "TSDirectForwardStatic": ("TSDirect", "ForwardStatic"),
        "ADirectForwardStatic": ("ADirect", "ForwardStatic"),
        "DirectForwardStatic": ("Direct", "ForwardStatic"),
        "TSDepthOutputDepthWriteStatic": ("TS", "DepthOutputDepthWriteStatic"),
        "AOutdoorForwardPreTransform": ("A", "OutdoorForwardPreTransform"),
        "DeferredStatic": ("", "DeferredStatic"),
        "SpeedTreeV8AGBufferMeshletBillboardMS": ("", "SpeedTreeV8AGBufferMeshletBillboardMS"),
    }
    for name, want in cases.items():
        got = SC.split_prefix(name)
        assert got == want, (name, got, want)
    print("split_prefix: %d cases ok (longest-first)" % len(cases))


def _case_group_synthetic():
    # 4 列 × 2 行；同一技术、同一程序 ⇒ 一组，前缀与行都并起来
    recs = [
        _rec(0, "DeferredStatic", ps=10, vs=11),
        _rec(1, "ADeferredStatic", ps=10, vs=11),
        _rec(2, "ShadowStatic", ps=20, vs=21),
        _rec(3, "SpeedTreeV8AGBuffer", ps=30, vs=31),   # 不该被当成 A 前缀
        _rec(4, "DeferredStatic", ps=10, vs=11),
        _rec(5, "ADeferredStatic", ps=10, vs=11),
        _rec(6, "ShadowStatic", ps=22, vs=23),          # 行 1：同技术不同程序
        _rec(7, "SpeedTreeV8AGBuffer", ps=30, vs=31),
    ]
    g = SC.group_variants(recs, cols=4)
    assert set(g) == {"DeferredStatic", "ShadowStatic", "SpeedTreeV8AGBuffer"}, list(g)
    d = g["DeferredStatic"]
    assert len(d) == 1, d
    assert d[0]["prefixes"] == ["", "A"], d[0]
    assert d[0]["rows"] == [0, 1], d[0]
    assert d[0]["programs"] == (10, 11, 0, 0, 0, 0), d[0]
    s = g["ShadowStatic"]
    assert len(s) == 2 and [x["rows"] for x in s] == [[0], [1]], s
    st = g["SpeedTreeV8AGBuffer"]
    assert len(st) == 1 and st[0]["rows"] == [0, 1], st
    print("group_variants synthetic: merge across rows/prefixes ok")


def _case_mhws_otomo():
    if not os.path.exists(OTOMO):
        print("otomo: skipped (unpack missing)")
        return
    data = open(OTOMO, "rb").read()
    view = GP.resolve(data, 240906211)
    es = SC.record_entries(data, view)
    used = [e for e in es if e["has_program"]]
    names = {e["name"] for e in used}
    groups = SC.group_variants(es, view.shape.cols)
    assert len(names) == 914, len(names)
    assert len(groups) == 206, len(groups)
    # 每个技术名都不再带前缀
    for tech in groups:
        pre, rest = SC.split_prefix(tech)
        assert not pre and rest == tech, tech
    # 前缀分布（按**唯一名字**统计，不是按程序组：组会把多行/多前缀并起来）
    from collections import Counter
    base_names = {n for n in names if SC.split_prefix(n)[0] == ""}
    cnt = Counter()
    for n in names:
        pre, tech = SC.split_prefix(n)
        if pre and tech not in base_names:
            pre = ""
        cnt[pre] += 1
    assert cnt[""] == 206 and cnt["A"] == 188 and cnt["TS"] == 188, cnt
    assert cnt["ATS"] == 188 and cnt["Direct"] == 36, cnt
    assert cnt["ADirect"] == 36 and cnt["TSDirect"] == 36, cnt
    assert cnt["ATSDirect"] == 36, cnt
    # 所有槽都被覆盖，且行号不越界
    covered = sum(len(g["indices"]) for items in groups.values() for g in items)
    assert covered == len(used), (covered, len(used))
    for items in groups.values():
        for g in items:
            assert g["rows"] and max(g["rows"]) < view.shape.rows, g
    print("otomo: 914 names -> 206 techs, prefixes ok, %d slots covered" % covered)


def _case_mhws_speedtree():
    if not os.path.exists(SPEEDTREE):
        print("speedtree: skipped (unpack missing)")
        return
    data = open(SPEEDTREE, "rb").read()
    view = GP.resolve(data, 250206176)
    es = SC.record_entries(data, view)
    groups = SC.group_variants(es, view.shape.cols)
    assert len(groups) == 486, len(groups)
    for items in groups.values():
        for g in items:
            assert g["prefixes"] == [""], g
    print("speedtree: 486 names, no prefixes")


def _case_dmc5():
    if not os.path.exists(DMC5):
        print("dmc5: skipped (sample missing)")
        return
    from material_toolkit.lib.mmtr_model import split_variant_name
    assert split_variant_name("ATSShadowStatic") == ("ATS", "ShadowStatic")
    data = open(DMC5, "rb").read()
    view = GP.resolve(data, 1808168797)
    es = SC.record_entries(data, view)
    groups = SC.group_variants(es, view.shape.cols)
    # DMC5 的 4 行字节相同 ⇒ 同一 (前缀) 的程序组只有一组，但行会覆盖 0..3
    multi = [g for items in groups.values() for g in items if len(g["rows"]) > 1]
    assert multi, "DMC5 4 rows identical -> groups should span rows"
    assert all(set(g["rows"]) <= set(range(view.shape.rows)) for g in multi)
    print("dmc5: %d techs, rows collapse as expected (sample group rows=%s)"
          % (len(groups), multi[0]["rows"]))


def _case_dxil_rows():
    if not os.path.exists(OTOMO):
        print("dxil rows: skipped (unpack missing)")
        return
    data = open(OTOMO, "rb").read()
    view = GP.resolve(data, 240906211)
    es = SC.record_entries(data, view)
    rows = {}
    for e in es:
        if not e["has_program"]:
            continue
        r = e["index"] // view.shape.cols
        if r in rows:
            continue
        for key in ("ps", "vs", "cs"):
            off, size = e["stages"][key]
            if off and size:
                ver = MB.dxil_version(data[off:off + size])
                if ver:
                    rows[r] = ver
                    break
    assert rows.get(0) == "1.2", rows
    assert all(v == "1.6" for r, v in rows.items() if r > 0), rows
    print("dxil rows: %s" % rows)


def main():
    _case_split()
    _case_group_synthetic()
    _case_mhws_otomo()
    _case_mhws_speedtree()
    _case_dmc5()
    _case_dxil_rows()
    print("ALL OK")


if __name__ == "__main__":
    main()
