#!/usr/bin/env python3
"""回归: DXIL 的 PSV0 资源声明解析（DXIL 没有 RDEF，PSV0 是唯一二进制来源）。

结构（实测 MHWS，与 `dxc -dumpbin` 的 Resource Bindings 表逐项对应）：

    payload + 0x00..0x40   头部（+0x20 = signature vectors, +0x38 = 资源数,
                          +0x3C = 条目步长）
    payload + 0x40         资源表：每条 6×u32 =
                          [f0, f1, id/register, lower_bound, kind, space]
    lower_bound = 0xFFFFFFFF 表示无界（bindless 的 RawBuffer）

名字不在 PSV0 里（dxc 的名字来自 DXIL 元数据），`psv0_names_from_dxc()` 只做显示兜底。
没有荒野解包 / dxc 时相应用例自动跳过。
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mmtr_blobs as MB  # noqa: E402

MHWS_MMTR = (r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
             r"\materialshader\master\speedtree_uber.mmtr.250206176")


def _container(tag, payload):
    hdr = bytearray(b"DXBC" + b"\x00" * 16 + struct.pack("<I", 1) + b"\x00" * 4
                    + struct.pack("<I", 1) + struct.pack("<I", 36))
    return bytes(hdr) + tag + struct.pack("<I", len(payload)) + payload


def _psv0_blob(entries, sig=0x06000406):
    header = bytearray(64)
    struct.pack_into("<I", header, 0, 52)
    struct.pack_into("<I", header, 0x20, sig)
    struct.pack_into("<I", header, 0x38, len(entries))
    struct.pack_into("<I", header, 0x3C, 24)
    body = b"".join(struct.pack("<6I", *e) for e in entries)
    return _container(b"PSV0", bytes(header) + body)


def _case_synthetic():
    blob = _psv0_blob([
        (2, 0, 0, 0, 13, 0),                 # CBuffer id0
        (4, 1, 1, 0xFFFFFFFF, 11, 0),        # 无界 RawBuffer id1
    ])
    psv = MB.parse_psv0(blob)
    assert psv and psv["count"] == 2 and psv["stride"] == 24, psv
    assert [r["kind_name"] for r in psv["resources"]] == ["CBuffer", "RawBuffer"]
    assert [r["id"] for r in psv["resources"]] == [0, 1]
    assert psv["resources"][1]["lower"] == 0xFFFFFFFF
    assert all(r["space"] == 0 for r in psv["resources"])
    print("synthetic PSV0: ok")


def _mhws_blobs():
    from material_toolkit.lib import game_profile as GP
    from material_toolkit.lib import mmtr_scan as SC
    d = open(MHWS_MMTR, "rb").read()
    v = GP.resolve(d, 250206176)
    es = SC.record_entries(d, v)
    e = next(x for x in es if x["name"] == "DeferredStatic" and x["stages"]["ps"][0])
    return d, e


def _case_mhws_real():
    if not os.path.exists(MHWS_MMTR):
        print("real MHWS: skipped (unpack missing)")
        return
    d, e = _mhws_blobs()
    ps_off, ps_size = e["stages"]["ps"]
    psv = MB.parse_psv0(d[ps_off:ps_off + ps_size])
    assert psv and psv["count"] == 4, psv
    assert [r["kind"] for r in psv["resources"]] == [13, 13, 13, 7]
    assert [r["id"] for r in psv["resources"]] == [0, 1, 2, 0]
    assert all(r["space"] == 0 for r in psv["resources"])

    vs_off, vs_size = e["stages"]["vs"]
    vs = MB.parse_psv0(d[vs_off:vs_off + vs_size])
    assert vs and vs["count"] == 8, vs
    assert [r["kind"] for r in vs["resources"]] == [13, 13, 13, 12, 12, 11, 11, 11]
    # bindless 的 RedirectTbl 是无界的
    assert vs["resources"][5]["lower"] == 0xFFFFFFFF
    print("real MHWS: PS %d resources, VS %d resources" % (psv["count"], vs["count"]))


def _case_dxc_names():
    if not os.path.exists(MHWS_MMTR):
        print("dxc names: skipped (unpack missing)")
        return
    try:
        MB.find_dxc()
    except FileNotFoundError:
        print("dxc names: skipped (dxc missing)")
        return
    d, e = _mhws_blobs()
    off, size = e["stages"]["ps"]
    names = MB.psv0_names_from_dxc(d[off:off + size])
    assert [x["name"] for x in names] == [
        "SceneInfo", "EnvironmentInfo", "GBufferType", "BlueNoise16"], names
    psv = MB.parse_psv0(d[off:off + size])
    assert len(names) == psv["count"]
    print("dxc names: %s" % [x["name"] for x in names])


def main():
    _case_synthetic()
    _case_mhws_real()
    _case_dxc_names()
    print("ALL OK")


if __name__ == "__main__":
    main()
