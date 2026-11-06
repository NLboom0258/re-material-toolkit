#!/usr/bin/env python3
"""回归: mdf2.45（MHWS）的版本 / 着色类型 / flags 解析。

要点（2026-11-07，参考 REasy 的 mdf 实现并用解包文件核对）：
  * mdf2 >= 31 的 material_flags 是 **8 字节**：低 32 位仍是 v21 布局，
    高 32 位是 flagsB（TransparentDistortionEnable … DitheredLodTransitionEnable，
    TransparentPriorityBias 在 bit40 起 8 位有符号）；
  * 着色类型名 **按游戏分表**：DMC5 12 项、MHWS 41 项，值编号不同，不能混用；
  * 写入仍只支持 version < 19：save() 对 mdf2.45 必须明确拒绝，不能静默写坏。
"""
import glob
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mdf2 as M  # noqa: E402

MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"


def _case_tables():
    assert len(M.shading_types("DMC5")) == 12
    assert len(M.shading_types("MHWS")) == 41
    assert M.shading_type_name(10, "DMC5") == "ExpensiveTransparent"
    assert M.shading_type_name(16, "MHWS") == "ExpensiveTransparent"
    assert M.shading_type_name(17, "MHWS") == "Forward"
    assert M.shading_type_name(6, "MHWS") == "Transparent"
    assert M.shading_type_name(25, "MHWS") == "ShellFurMaterial"
    assert M.shading_type_value("Forward", "MHWS") == 17
    assert M.game_for_mdf_version(45) == "MHWS"
    assert M.game_for_mdf_version(10) == "DMC5"
    print("shading tables: DMC5 12 / MHWS 41, names ok")


def _case_flags():
    v = (1 << 35) | (1 << 31) | 0x9
    d = M.decode_flags(v, 45)
    assert d["ForwardPrepassEnabled"] is True, d
    assert d["NoRayTracing"] is True, d
    assert M.encode_flags(d, 45) == v
    assert "ForwardPrepassEnabled" not in M.decode_flags(v, 10)
    assert M.decode_flags(0xFF << 40, 45)["TransparentPriorityBias"] == -1
    print("flags: 8-byte layout / signed bias / round-trip ok")


def _case_real_optional():
    files = sorted(glob.glob(os.path.join(MHWS_ROOT, "**", "*.mdf2.45"), recursive=True))
    if not files:
        print("real MHWS: skipped (no unpack)")
        return
    names = {n for n, _ in M.shading_types("MHWS")}
    for p in files[:3]:
        m = M.Mdf2.load(p)
        assert m.game_version == 45 and m.game == "MHWS", p
        assert m.materials, p
        for mat in m.materials[:20]:
            assert mat.flags_width == 8, (p, mat.name)
            assert mat.shading_type_name() in names, (p, mat.name, mat.shader_type)
            assert mat.mmtr_path, (p, mat.name)
            for pr in mat.properties[:5]:
                assert all(abs(x) < 1e7 for x in pr.values), (p, mat.name, pr.name)
        with tempfile.TemporaryDirectory() as td:
            try:
                m.save(os.path.join(td, "x.mdf2.45"))
            except NotImplementedError:
                pass
            else:
                raise AssertionError("save() must reject mdf2.45")
    print("real MHWS: %d files parsed, names/flags ok, save rejected" % min(3, len(files)))


if __name__ == "__main__":
    _case_tables()
    _case_flags()
    _case_real_optional()
    print("OK")
