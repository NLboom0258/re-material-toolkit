#!/usr/bin/env python3
"""回归: mdf2 v31+（MHWS v45）的结构写入。

关键点（2026-11-07，布局参考 REasy 的 mdf 实现 + 原版文件往返校验）：
  * 区域顺序：文件头 → 材质记录(100B) → tex 头(32B/条) → param 头(24B/条)
    → GPBF 表(16B/条) → 字符串池(UTF-16) → 各材质参数值区 → shaderLODRedirects；
  * 字符串池**每个占位都写一份**，但偏移指向该串第一次出现的位置
    （原版就是这样：池里有大量重复串，而引用共享偏移）；
  * param 头 v31+ 的 component 字段 = count(低16) | component_locked(高16)；
  * v>=40 空表共享同一偏移。

验收口径：
  1. **未改动的文件 load→save 必须逐字节相同**（这是最强校验）；
  2. 加/删参数、改贴图路径、加贴图槽、改名后重新解析内容正确；
  3. 修改结果再存一次（幂等）应完全一致。
"""
import glob
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mdf2 as M  # noqa: E402

MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
# 解包目录不在时回退到入库的原版样本（主仓 assets/original；不要用 mod 产出的 mdf2 当基准）
ORIGINAL_ASSETS = r"E:\Project\Other\REEngine-mmtrReverse\assets\original"
TMP = os.path.join(os.environ.get("TEMP", "."), "_mdf2_v31_test.mdf2.45")


def _sample_files(limit=8):
    files = glob.glob(os.path.join(MHWS_ROOT, "**", "*.mdf2.45"), recursive=True)
    if not files:
        files = glob.glob(os.path.join(ORIGINAL_ASSETS, "*.mdf2.45"))
    if not files:
        return []
    random.seed(7)
    return random.sample(files, min(limit, len(files)))


def _case_roundtrip():
    files = _sample_files()
    if not files:
        print("no mdf2.45 samples, skip round-trip")
        return
    for p in files:
        m = M.Mdf2.load(p)
        m.save(TMP)
        a, b = open(p, "rb").read(), open(TMP, "rb").read()
        assert a == b, ("round-trip 不一致", os.path.basename(p), len(a), len(b))
    print("round-trip: %d file(s) byte-identical" % len(files))


def _case_structural():
    files = _sample_files(1)
    if not files:
        print("no mdf2.45 samples, skip structural")
        return
    src = files[0]
    mat_i = 0

    # 加参数
    m = M.Mdf2.load(src)
    mat = m.materials[mat_i]
    n0 = len(mat.properties)
    mat.add_parameter("ZZ_TestParam", "float4", [0.25, 0.5, 0.75, 1.0])
    m.save(TMP)
    m2 = M.Mdf2.load(TMP)
    p = m2.materials[mat_i].get_parameter("ZZ_TestParam")
    assert p is not None and [round(v, 4) for v in p.values] == [0.25, 0.5, 0.75, 1.0]
    assert len(m2.materials[mat_i].properties) == n0 + 1
    # 幂等
    m2.save(TMP + ".2")
    assert open(TMP, "rb").read() == open(TMP + ".2", "rb").read()
    os.remove(TMP + ".2")

    # 删参数
    gone = M.Mdf2.load(src).materials[mat_i].properties[1].name
    m = M.Mdf2.load(src)
    assert m.materials[mat_i].delete_parameter(gone)
    m.save(TMP)
    assert M.Mdf2.load(TMP).materials[mat_i].get_parameter(gone) is None

    # 改贴图路径（变长）
    m = M.Mdf2.load(src)
    tb = m.materials[mat_i].textures[0]
    tb.texture_path = "systems/rendering/ZZ_Test_" + tb.texture_path
    want = tb.texture_path
    m.save(TMP)
    got = M.Mdf2.load(TMP).materials[mat_i].textures[0]
    assert got.texture_path == want, got.texture_path

    # 新增贴图槽
    m = M.Mdf2.load(src)
    ntex = len(m.materials[mat_i].textures)
    m.materials[mat_i].add_texture("ZZ_ExtraMap", "systems/rendering/NullBlack.tex")
    m.save(TMP)
    mm = M.Mdf2.load(TMP).materials[mat_i]
    assert len(mm.textures) == ntex + 1
    assert mm.textures[-1].texture_type == "ZZ_ExtraMap"

    # 改名 + 改 mmtr
    m = M.Mdf2.load(src)
    old = m.materials[mat_i].name
    m.rename_material(old, "ZZ_Renamed")
    m.materials[mat_i].mmtr_path = "MaterialShader/Variation/ZZ_Test.mmtr"
    m.save(TMP)
    mm = M.Mdf2.load(TMP).materials[mat_i]
    assert mm.name == "ZZ_Renamed"
    assert mm.mmtr_path == "MaterialShader/Variation/ZZ_Test.mmtr"
    print("structural: add/del param, set path, add tex, rename ok")


def _case_gpbf_preserved():
    """有 GPBF 表的文件：改值后 GPBF 表内容必须原样保留。"""
    files = _sample_files(3)
    checked = 0
    for src in files:
        m = M.Mdf2.load(src)
        if not any(x.gpu_buffers for x in m.materials):
            continue
        before = [(h["name"], h["utf16_hash"], h["ascii_hash"])
                  for mat in m.materials for pair in mat.gpu_buffers for h in pair]
        m.materials[0].properties[0].values[0] += 0.0   # 不改值，只重写
        m.save(TMP)
        m2 = M.Mdf2.load(TMP)
        after = [(h["name"], h["utf16_hash"], h["ascii_hash"])
                 for mat in m2.materials for pair in mat.gpu_buffers for h in pair]
        assert before == after, os.path.basename(src)
        checked += 1
    print("gpbf preserved: %d file(s)" % checked)


if __name__ == "__main__":
    _case_roundtrip()
    _case_structural()
    _case_gpbf_preserved()
    print("OK")
