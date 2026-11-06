#!/usr/bin/env python3
"""回归: DXIL 反汇编（dxc -dumpbin，只读）。

DXIL(SM6) 没有可用的汇编器，所以只提供查看：
  * find_dxc() 在 PATH / Windows SDK / 环境变量 DXC_EXE 里找 dxc.exe；
  * disassemble_dxil(blob) 用 `dxc -dumpbin` 输出反汇编文本（含 Input/Output signature、
    resource 绑定与 LLVM IR 指令），用于分析；本测试只验证“能出文本”，不涉及写回。

没有 dxc 或荒野解包时自动跳过。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mmtr_blobs as MB  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"


def _case_dxc():
    try:
        dxc = MB.find_dxc()
    except FileNotFoundError:
        print("dxc: skipped (not installed)")
        return False
    assert os.path.isfile(dxc), dxc
    print("dxc:", dxc)
    return True


def _case_real_blob():
    files = glob.glob(os.path.join(MHWS_ROOT, "**", "speedtree_uber.mmtr.250206176"),
                      recursive=True)
    if not files:
        print("real DXIL: skipped (no unpack)")
        return
    data = open(files[0], "rb").read()
    view = GP.resolve(data, 250206176)
    entries = SC.record_entries(data, view)
    blobs = SC.blob_list(data, view, entries)
    off, size = blobs[0]
    text = MB.disassemble_dxil(data[off:off + size])
    assert len(text) > 1000, len(text)
    assert "Input signature" in text, text[:200]
    assert "dx.op" in text or "dxil" in text.lower(), text[:200]
    print("real DXIL: %d chars, signature + dx ops ok" % len(text))


if __name__ == "__main__":
    if _case_dxc():
        _case_real_blob()
    print("OK")
