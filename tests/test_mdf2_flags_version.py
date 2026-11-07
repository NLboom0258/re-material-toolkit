#!/usr/bin/env python3
"""回归: mdf2 材质 flags 的位布局**按版本**区分（DMC5 v10 vs v19/v21+）。

背景（2026-11-07）
------------------
早期 flags 表照抄了 RE-Mesh-Editor 的“最新版本”布局（v21：bits8/9=Forced/TwoSide、
bit10=ZPost、TessFactor 5 位@11），但 DMC5 的 mdf2 是 **v10**：
  bits0-7 基础布尔 / **bits8-15 TessFactor(8 位)** / 16-23 PhongFactor /
  24 ForcedAlphaTestEnable / 25 AlphaTestEnable。

证据：DMC5 解包 2054 个 mdf2 / 10013 个材质里，bits8-15 非零的 82 个材质该字节**恒为
0x2F（47）**；若按 v21 解，就会变成 ForcedTwoSide+TwoSide+ZPost 三个布尔 + TessFactor=5
（三个布尔同时为真 82/82，且 47 正好是 8 位数值 —— 这些位就是一个数，不是三个开关）。

本测试覆盖：v10 / v19 / v21 / v45 的整数字段位置、布尔位位置、编码往返，以及真实
DMC5 样本（em5400）的 TessFactor=47。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mdf2 as M  # noqa: E402

DMC5_ROOT = r"E:\Project\Other\Mod\REEngine\DMC\DMC5\Unpack\natives\x64"


def _offsets(version):
    return {n: (w, b) for n, w, b in M.flag_fields(version)}


def _case_layouts():
    v10 = _offsets(10)
    assert v10["TessFactor"] == (8, 8), v10["TessFactor"]
    assert v10["PhongFactor"] == (8, 16)
    assert "TwoSideEnable" not in v10 and "TransparentZPostPassEnable" not in v10

    v19 = _offsets(19)
    assert v19["ForcedTwoSideEnable"] == (1, 8)
    assert v19["TwoSideEnable"] == (1, 9)
    assert v19["TessFactor"] == (6, 10)

    v21 = _offsets(21)
    assert v21["TransparentZPostPassEnable"] == (1, 10)
    assert v21["TessFactor"] == (5, 11)

    v45 = _offsets(45)
    assert v45 == v21, ">=31 的低 32 位仍是 v21 布局"

    print("layouts: v10/v19/v21/v45 ok")


def _case_decode():
    x = 0x2F << 8          # bits 8,9,10,11,13 -> 0x2F

    d10 = M.decode_flags(x, 10)
    assert d10["TessFactor"] == 0x2F
    assert "TwoSideEnable" not in d10
    assert M.encode_flags(d10, 10) == x

    d21 = M.decode_flags(x, 21)
    assert d21["ForcedTwoSideEnable"] and d21["TwoSideEnable"] and \
           d21["TransparentZPostPassEnable"]
    assert d21["TessFactor"] == 5
    assert M.encode_flags(d21, 21) == x

    # 未定义的位名在编码时忽略，不会踩到别的字段
    assert M.encode_flags({"TwoSideEnable": True}, 10) == 0

    # v10 没有 flagsB；v45 有
    assert "ForwardPrepassEnabled" not in M.decode_flags(x, 10)
    assert M.decode_flags(1 << 35, 45)["ForwardPrepassEnabled"] is True
    print("decode/encode: v10 47 vs v21 5 ok")


def _case_real_dmc5():
    pat = os.path.join(DMC5_ROOT, "**", "em5400_25.mdf2.10")
    hits = glob.glob(pat, recursive=True)
    if not hits:
        print("no em5400 sample, skip")
        return
    m = M.Mdf2.load(hits[0])
    seen = 0
    for mat in m.materials:
        d = mat.flags_dict()
        if d.get("TessFactor") == 0x2F:
            assert d["PhongFactor"] == 127
            assert "TwoSideEnable" not in d
            seen += 1
    assert seen, "em5400 样本里没有 TessFactor=47 的材质"
    print("real DMC5: %d material(s) with TessFactor=47, PhongFactor=127" % seen)


if __name__ == "__main__":
    _case_layouts()
    _case_decode()
    _case_real_dmc5()
    print("OK")
