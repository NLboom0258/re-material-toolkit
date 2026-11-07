#!/usr/bin/env python3
"""回归: DXBC 签名 chunk（ISG1/OSG1 32B、ISGN/OSGN 24B）的纯 Python 解析。

背景（2026-11-07）
------------------
MHWS 的 shader 是 DXIL(SM6)，**没有 RDEF**，输入/输出接口只能从签名 chunk 读；
DMC5 的 SM5 构建同样带 ISGN/OSGN（24B）。两者共用同一套解析：

  chunk payload = fourcc+size 之后的 8 字节头(count + 8) + 元素数组 + 名字池
  name 偏移**相对 payload**（不是相对 fourcc），chunk 头里的 size 也只是 payload 长度。

实测对照（dxc -dumpbin 的输入/输出表）：
  PS DeferredStatic 输入 = SV_Position / INTERPOLATOR×4 / NOINTERPOLATOR，
  输出 = SV_Target×4（system value 64=TARGET）。本测试把该表逐项断言。

没有荒野解包 / DMC5 样本时相应用例自动跳过。
"""
import glob
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mmtr_blobs as MB  # noqa: E402

MHWS_MMTR = (r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
             r"\materialshader\master\speedtree_uber.mmtr.250206176")
DMC5_DIR = r"E:\Project\Other\REEngine-mmtrReverse\assets\original"


def _container(tag, payload):
    """最小 DXBC 容器：1 个 chunk（只需要 chunk 表能被读到）。"""
    hdr = bytearray(b"DXBC" + b"\x00" * 16 + struct.pack("<I", 1) + b"\x00" * 4
                    + struct.pack("<I", 1) + struct.pack("<I", 36))
    return bytes(hdr) + tag + struct.pack("<I", len(payload)) + payload


def _isg1_payload(name="POSITION", mask=0x0F, used=0x03, sysval=0, reg=0):
    """ISG1: count + 8 + 32B 元素 + 名字（name 偏移相对 payload）。"""
    body = struct.pack("<I", 1) + struct.pack("<I", 8)
    name_off = 8 + 32
    body += struct.pack("<6I", 0, name_off, 0, sysval, 3, reg)
    body += bytes([mask, used]) + b"\x00\x00" + struct.pack("<I", 0)
    body += name.encode("latin1") + b"\x00"
    return body


def _isgn_payload(name="POSITION", mask=0x07, used=0x07, sysval=1, reg=0):
    """ISGN: count + 8 + 24B 元素（无 stream / min precision）。"""
    body = struct.pack("<I", 1) + struct.pack("<I", 8)
    name_off = 8 + 24
    body += struct.pack("<5I", name_off, 0, sysval, 3, reg)
    body += bytes([mask, used]) + b"\x00\x00"
    body += name.encode("latin1") + b"\x00"
    return body


def _case_synthetic():
    blob = _container(b"ISG1", _isg1_payload())
    sg = MB.blob_signatures(blob)
    assert sg["chunks"] == ["ISG1"], sg["chunks"]
    assert len(sg["input"]) == 1
    p = sg["input"][0]
    assert p["name"] == "POSITION", p
    assert p["register"] == 0 and p["system_value_name"] == "NONE"
    assert MB.mask_str(p["mask"]) == "xyzw" and MB.mask_str(p["used"]) == "xy"
    assert p["type_name"] == "float"

    # 24B 布局：名字偏移同样相对 payload；mask 3 位
    blob = _container(b"ISGN", _isgn_payload(name="NORMAL", reg=2))
    sg = MB.blob_signatures(blob)
    q = sg["input"][0]
    assert q["name"] == "NORMAL" and q["register"] == 2, q
    assert MB.mask_str(q["mask"]) == "xyz"
    assert q["system_value_name"] == "POS", q
    print("synthetic ISG1/ISGN: ok")


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
    sg = MB.blob_signatures(d[ps_off:ps_off + ps_size])
    names = [p["name"] for p in sg["input"]]
    assert names == ["SV_Position", "INTERPOLATOR", "INTERPOLATOR", "INTERPOLATOR",
                     "INTERPOLATOR", "NOINTERPOLATOR"], names
    assert [p["register"] for p in sg["input"]] == [0, 1, 2, 3, 4, 5]
    assert MB.mask_str(sg["input"][0]["mask"]) == "xyzw"
    assert MB.mask_str(sg["input"][0]["used"]) == "xy"
    outs = sg["output"]
    assert [p["name"] for p in outs] == ["SV_Target"] * 4, outs
    assert [p["register"] for p in outs] == [0, 1, 2, 3]
    assert all(p["system_value"] == 64 for p in outs)

    vs_off, vs_size = e["stages"]["vs"]
    vs = MB.blob_signatures(d[vs_off:vs_off + vs_size])
    assert [p["name"] for p in vs["input"]] == [
        "POSITION", "NORMAL", "TANGENT", "TEXCOORD", "COLOR", "Texcoord"], vs["input"]
    assert vs["input"][0]["system_value_name"] == "NONE"
    assert vs["output"][0]["name"] == "SV_Position"
    print("real MHWS: PS %d in / %d out, VS %d in / %d out" %
          (len(sg["input"]), len(outs), len(vs["input"]), len(vs["output"])))


def _case_dmc5_real():
    files = glob.glob(os.path.join(DMC5_DIR, "*.mmtr.1808168797"))
    if not files:
        print("real DMC5: skipped (no samples)")
        return
    from material_toolkit.lib import game_profile as GP
    from material_toolkit.lib import mmtr_scan as SC
    found = 0
    for path in files:
        d = open(path, "rb").read()
        v = GP.resolve(d, 1808168797)
        es = SC.record_entries(d, v)
        for off, size in SC.blob_list(d, v, es)[:8]:
            sg = MB.blob_signatures(d[off:off + size])
            if not sg["input"]:
                continue
            names = [p["name"] for p in sg["input"]]
            assert "POSITION" in names, (path, names)
            assert [p["register"] for p in sg["input"]] == list(range(len(names)))
            found += 1
            break
        if found:
            break
    assert found, "no DMC5 blob with ISGN/ISG1 found"
    print("real DMC5: ISGN parsed (%d in)" % len(names))


def _case_dxc_crosscheck():
    """有 dxc 时把反汇编文本的签名行与解析结果对一遍（名字/掩码/寄存器）。"""
    if not os.path.exists(MHWS_MMTR):
        print("dxc crosscheck: skipped (unpack missing)")
        return
    try:
        dxc = MB.find_dxc()
    except FileNotFoundError:
        print("dxc crosscheck: skipped (dxc missing)")
        return
    d, e = _mhws_blobs()
    ps_off, ps_size = e["stages"]["ps"]
    text = MB.disassemble_dxil(d[ps_off:ps_off + ps_size])
    rows = []
    section = None
    for line in text.splitlines():
        low = line.lower()
        if "input signature:" in low:
            section = "input"
            continue
        if "output signature:" in low:
            section = "output"
            continue
        if line.startswith("; shader") or line.startswith("; Pipeline"):
            section = None
            continue
        if not section or not line.startswith("; "):
            continue
        parts = line[1:].split()
        if len(parts) < 6:
            continue
        # 只收真正的表格行：Index 是数字、Mask 只含 xyzw-、Register 是数字。
        if not (parts[1].isdigit() and parts[3].isdigit()
                and set(parts[2]) <= set("xyzw-")):
            continue
        rows.append((section, parts))
    got = {(kind, p["register"]): (p["name"], MB.mask_str(p["mask"]))
           for kind, p in [("input", x) for x in MB.blob_signatures(
               d[ps_off:ps_off + ps_size])["input"]]
           + [("output", x) for x in MB.blob_signatures(
               d[ps_off:ps_off + ps_size])["output"]]}
    checked = 0
    for kind, parts in rows:
        name, _idx, mask, reg = parts[0], parts[1], parts[2], parts[3]
        key = (kind, int(reg))
        if key not in got:
            continue
        assert got[key] == (name, mask), (key, got[key], name, mask)
        checked += 1
    assert checked >= 10, checked
    print("dxc crosscheck: %d signature rows match (%s)" % (checked, dxc))


def main():
    _case_synthetic()
    _case_mhws_real()
    _case_dmc5_real()
    _case_dxc_crosscheck()
    print("ALL OK")


if __name__ == "__main__":
    main()
