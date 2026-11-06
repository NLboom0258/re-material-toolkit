#!/usr/bin/env python3
"""回归: 真实记录表的只读扫描（mmtr_scan）。

覆盖：记录字段、绑定表（描述符 + 名称池）、blob 枚举（含 MHWS 每 blob 后
8 字节尾部）、以及 DMC5 真实语料的一致性。

实测背景：
  * 记录表字段见 `game_profile`；绑定表 desc 指向「首条前 4B 头」，
    code = (type<<24)|(stage<<16)|slot；cbuffer 池条目 32B，其它 16B。
  * MHWS 的每个 DXBC 容器后跟 8 字节（DMC5 为 0），所以 blob 枚举必须
    以记录里的 (ptr,size) 为准（或 auto-detect 跳过尾部）。
"""
import glob
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

ASSETS = r"E:\Project\Other\REEngine-mmtrReverse\assets\original"
MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"


def _build_mhws():
    rows, cols = 5, 4
    n = rows * cols
    stride = 0x110
    table_end = 0x10 + n * stride
    desc_off = table_end
    pool_off = table_end + 8
    name_off = table_end + 0x20
    blob_off = table_end + 0x80
    blob = bytearray(64)
    blob[0:4] = b"DXBC"
    struct.pack_into("<I", blob, 24, 64)   # total size
    struct.pack_into("<I", blob, 28, 0)    # chunk count
    total = blob_off + 64 + 8
    buf = bytearray(total)
    buf[0:4] = b"SDF\x00"
    struct.pack_into("<HH", buf, 4, rows, cols)
    struct.pack_into("<I", buf, 8, blob_off)
    struct.pack_into("<I", buf, 0x10, table_end)
    buf[name_off:name_off + 9] = b"TestTech\x00"
    buf[blob_off:blob_off + 64] = blob
    buf[blob_off + 64:blob_off + 72] = b"\x11\x22\x33\x44\x55\x66\x77\x88"
    rec = 0x10
    struct.pack_into("<Q", buf, rec, name_off)
    struct.pack_into("<Q", buf, rec + 0x30, blob_off)   # PS
    struct.pack_into("<I", buf, rec + 0xCC, 64)
    struct.pack_into("<I", buf, rec + 0xD4, 1)          # total bindings
    buf[rec + 0xD8 + 2 * 6 + 4] = 1                     # SRV / PS
    struct.pack_into("<I", buf, rec + 0x88, desc_off)   # SRV desc
    struct.pack_into("<I", buf, rec + 0x90, pool_off)   # SRV pool
    buf[rec + 0xFC] = 1
    struct.pack_into("<I", buf, desc_off + 4, (0x02 << 24) | (0x10 << 16) | 0)
    struct.pack_into("<Q", buf, pool_off, name_off + 9)  # 第二个名字
    buf[name_off + 9:name_off + 17] = b"TestMap\x00"
    return bytes(buf)


def _case_synthetic():
    data = _build_mhws()
    view = GP.resolve(data, 250206176)
    assert view.profile.game == "MHWS"
    es = SC.record_entries(data, view)
    assert len(es) == 20 and es[0]["name"] == "TestTech"
    assert es[0]["stages"]["ps"] == (view.blob_start, 64)
    assert es[0]["desc_counts"]["srv"] == 1
    blobs = SC.blob_list(data, view, es)
    assert blobs == [(view.blob_start, 64)], blobs
    assert SC.blob_list(data) == blobs, "walk must skip the 8-byte trailer"
    assert SC.blob_usage(data, view, es) == {view.blob_start: [(0, "ps")]}
    b = SC.bindings(data, view, es[0]["off"], "srv")
    assert len(b) == 1 and b[0]["name"] == "TestMap", b
    assert SC.stage_label(b[0]["stage"]) == "PS"
    assert SC.type_label(b[0]["type"]) == "tex2d"
    print("synthetic MHWS: records / blob trailer / bindings ok")


def _case_dmc5():
    files = sorted(glob.glob(os.path.join(ASSETS, "*.mmtr.1808168797")))
    assert files
    for p in files:
        data = open(p, "rb").read()
        view = GP.resolve(data, SC.version_from_path(p))
        es = SC.record_entries(data, view)
        blobs = SC.blob_list(data, view, es)
        assert 60 <= len(blobs) <= 200, (os.path.basename(p), len(blobs))
        used = SC.blob_usage(data, view, es)
        assert set(used) == {o for o, _ in blobs}, os.path.basename(p)
        # 眼睛/头发 TransparentEX 是纯前向 master，没有 DeferredStatic：退到首个有程序的记录
        rec = next((e for e in es if e["name"] == "DeferredStatic"),
                   next(e for e in es if e["has_program"]))
        rb = SC.record_bindings(data, view, rec["off"])
        names = [x["name"] for x in rb["cb"] + rb["smp"] + rb["srv"]]
        assert names and all(names), (os.path.basename(p), rec["name"])
        if rec["name"] == "DeferredStatic":
            assert "SceneInfo" in names, (os.path.basename(p), names[:8])
        assert SC.blob_chunks(data, *blobs[0])[0][0] == "RDEF"
    print("DMC5 corpus: %d files, all blobs referenced, bindings/chunks ok" % len(files))


def _case_real_mhws_optional():
    if not os.path.isdir(MHWS_ROOT):
        print("real MHWS: skipped (no unpack)")
        return
    hits = glob.glob(os.path.join(MHWS_ROOT, "**", "speedtree_uber.mmtr.250206176"),
                     recursive=True)
    if not hits:
        print("real MHWS: skipped (sample missing)")
        return
    data = open(hits[0], "rb").read()
    view = GP.resolve(data, 250206176)
    es = SC.record_entries(data, view)
    blobs = SC.blob_list(data, view, es)
    assert len(blobs) == 585, len(blobs)
    assert SC.blob_list(data) == blobs, "auto-trailer walk disagrees"
    rec = next(e for e in es if e["name"] == "DeferredStatic")
    rb = SC.record_bindings(data, view, rec["off"])
    names = [x["name"] for x in rb["cb"] + rb["srv"]]
    assert "BindlessBuffer" in names, names[:8]
    chunks = [c[0] for c in SC.blob_chunks(data, *blobs[0])]
    assert "DXIL" in chunks and "RDEF" not in chunks, chunks
    print("real MHWS: 585 blobs, DXIL chunks, bindings ok")


if __name__ == "__main__":
    _case_synthetic()
    _case_dmc5()
    _case_real_mhws_optional()
    print("OK")
