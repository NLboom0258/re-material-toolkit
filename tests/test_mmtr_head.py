#!/usr/bin/env python3
"""回归: MHWS mmtr 头部模型（段划分 / 结构化指针表 / 无损重建）。

验证口径（全部字节级）：
  1. identity rebuild：真实语料抽样 + ToonTest 样本，重建结果逐字节相同；
  2. 段序连续、`条目数 × 条目宽 == 段长`；
  3. 变长：names 段 +16 → 后续段整体平移、读取结果（记录名/绑定/参数）不变、
     blob 区逐字节不变；反向缩短可逐字节还原；
  4. 缩短：params 段 -16 → 往返可精确还原；
  5. 段内编辑：30 项 srv pool 末尾插入一个名字条目 → 所有记录的绑定名不变、
     插入/删除往返逐字节还原（贴图槽加项的雏形）；
  6. 错误路径：DMC5 profile（无 8B 尾部）/ 未知段名 / 长度非条目宽整数倍。

真实语料缺失时对应用例跳过。
"""
import glob
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mmtr_head as HEAD  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

ASSETS = r"E:\Project\Other\REEngine-mmtrReverse\assets\original"
MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
TOON = r"E:\Project\Other\REEngine-mmtrReverse\workbench\mhws_liino\mdf2\ToonTest.mmtr.240906211.orig"


def _mhws_samples(limit=24):
    if not os.path.isdir(MHWS_ROOT):
        return []
    files = sorted(glob.glob(os.path.join(MHWS_ROOT, "**", "*.mmtr.*"), recursive=True))
    files = [p for p in files
             if p.rsplit(".", 1)[-1] in ("240906211", "250206176")]
    step = max(1, len(files) // limit)
    return files[::step][:limit]


def _case_identity():
    paths = list(_mhws_samples())
    if os.path.isfile(TOON):
        paths.insert(0, TOON)
    if not paths:
        print("identity: skipped (no MHWS corpus)")
        return
    for p in paths:
        ver = SC.version_from_path(p) or 240906211
        data = open(p, "rb").read()
        ok, info = HEAD.verify(data, version=ver)
        assert ok, (os.path.basename(p), info)
        m = HEAD.parse(data, version=ver)
        prev = None
        for s in m.sections:
            if prev is not None:
                assert s.start == prev, (os.path.basename(p), s.name, s.start - prev)
            if s.item:
                assert s.count * s.item == s.size, (os.path.basename(p), s.name)
            prev = s.end
        assert prev == len(data) or m.sections[-1].name == "blobs"
    print("identity rebuild: %d MHWS files byte-identical" % len(paths))


def _case_grow():
    if not os.path.isfile(TOON):
        print("grow: skipped (no ToonTest sample)")
        return
    data = open(TOON, "rb").read()
    m = HEAD.parse(data, version=240906211)
    anchor = struct.unpack_from("<Q", data, m.sec("names").start)[0]
    grown = m.rebuild({"names": m.span("names") + struct.pack("<QII", anchor, 0, 0)})
    assert len(grown) == len(data) + 16
    m2 = HEAD.parse(grown, version=240906211)
    n1, n2 = m.sec("names"), m2.sec("names")
    assert n2.count == n1.count + 1
    assert m2.sec("blobs").start == m.sec("blobs").start + 16
    # blob 区只平移，内容逐字节不变
    assert grown[m2.sec("blobs").start:] == data[m.sec("blobs").start:]
    # 读回一致（指针重映射正确的强证据）
    v1, v2 = GP.resolve(data, 240906211), GP.resolve(grown, 240906211)
    r1, r2 = SC.record_entries(data, v1), SC.record_entries(grown, v2)
    assert [e["name"] for e in r1] == [e["name"] for e in r2]
    for e1, e2 in list(zip(r1, r2))[:20]:
        b1 = [(x["slot"], x["name"]) for x in (SC.bindings(data, v1, e1["off"], "srv") or [])]
        b2 = [(x["slot"], x["name"]) for x in (SC.bindings(grown, v2, e2["off"], "srv") or [])]
        assert b1 == b2
    # 反向缩短 -> 逐字节还原
    assert m2.rebuild({"names": m2.span("names")[:-16]}) == data
    print("grow +16: shifted, read-back identical, round-trip exact")


def _case_shrink():
    if not os.path.isfile(TOON):
        print("shrink: skipped (no ToonTest sample)")
        return
    data = open(TOON, "rb").read()
    m = HEAD.parse(data, version=240906211)
    pm = m.sec("params")
    assert pm.count > 1
    shrunk = m.rebuild({"params": m.span("params")[:-16]})
    assert len(shrunk) == len(data) - 16
    m2 = HEAD.parse(shrunk, version=240906211)
    assert m2.sec("params").count == pm.count - 1
    assert m2.sec("blobs").start == m.sec("blobs").start - 16
    assert shrunk[m2.sec("blobs").start:] == data[m.sec("blobs").start:]
    assert m2.rebuild({"params": m2.span("params") + data[pm.end - 16:pm.end]}) == data
    print("shrink -16: shifted, round-trip exact")


def _case_pool_edit():
    """段内插入/删除（贴图槽加项的雏形）。"""
    if not os.path.isfile(TOON):
        print("pool edit: skipped (no ToonTest sample)")
        return
    data = open(TOON, "rb").read()
    m = HEAD.parse(data, version=240906211)
    v = GP.resolve(data, 240906211)
    # 找 30 项的 srv pool（DeferredPreTransformVfx 系列，句柄块来源）
    pool = cnt = None
    for e in SC.record_entries(data, v):
        c = data[e["off"] + 0xFC]
        if c == 30:
            pool = struct.unpack_from("<Q", data, e["off"] + 0x90)[0]
            cnt = c
            break
    assert pool is not None
    at = pool + cnt * 16
    anchor = struct.unpack_from("<Q", data, pool)[0]
    grown = m.rebuild(edits={"names": [(at, 0, struct.pack("<QII", anchor, 0, 0), [(0, 8)])]})
    assert len(grown) == len(data) + 16
    m2 = HEAD.parse(grown, version=240906211)
    assert m2.sec("names").count == m.sec("names").count + 1

    def binds(buf):
        vv = GP.resolve(buf, 240906211)
        out = []
        for e in SC.record_entries(buf, vv):
            row = []
            for kind in ("cb", "smp", "srv", "uav", "opt"):
                row.extend((kind, x["slot"], x["name"])
                           for x in (SC.bindings(buf, vv, e["off"], kind) or []))
            out.append(tuple(row))
        return out

    # 编辑点之后的 pool 指针全部平移 -> 所有记录的绑定名必须一致
    assert binds(data) == binds(grown), "pool pointer remap wrong"
    assert grown[m2.sec("blobs").start:] == data[m.sec("blobs").start:]
    # 删除同一区间 -> 逐字节还原
    back = m2.rebuild(edits={"names": [(at, 16, b"", [])]})
    assert back == data, "insert/delete round-trip mismatch"
    print("pool edit: bindings stable, insert/delete round-trip exact")


def _case_errors():
    dmc5 = sorted(glob.glob(os.path.join(ASSETS, "*.mmtr.1808168797")))
    if dmc5:
        data = open(dmc5[0], "rb").read()
        try:
            HEAD.parse(data, version=1808168797)
        except HEAD.HeadError as e:
            assert "trailer" in str(e) or "MHWS" in str(e)
        else:
            raise AssertionError("DMC5 should be rejected")
    if not os.path.isfile(TOON):
        print("errors: partial (no ToonTest sample)")
        return
    data = open(TOON, "rb").read()
    m = HEAD.parse(data, version=240906211)
    for bad, want in (
        ({"nosuch": b""}, "未知段"),
        ({"names": m.span("names") + b"\x00" * 15}, "整数倍"),
    ):
        try:
            m.rebuild(bad)
        except HEAD.HeadError as e:
            assert want in str(e), (want, str(e))
        else:
            raise AssertionError("should raise for %r" % (want,))
    print("errors: DMC5 rejected, bad overrides rejected")


if __name__ == "__main__":
    _case_identity()
    _case_grow()
    _case_shrink()
    _case_pool_edit()
    _case_errors()
    print("OK")
