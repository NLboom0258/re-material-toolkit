#!/usr/bin/env python3
"""回归: mmtr 技术名 -> pass 家族（变体页分组）。

背景（2026-11-07）
------------------
旧规则只看名字开头，MHWS 的技术名大量带 A/TS/ATS 前缀（alpha-test / two-sided），
于是 `ADeferredStatic`、`ADepthOutputDepthWritePreTransform` 之类全掉进“其它”；
SpeedTree/Meshlet 还会把用途写在中间（`SpeedTreeV8ATreeFoliageO0Forward`）。

现在按用途词判定（规则在 `lib/mmtr_scan.pass_family`，纯逻辑、无 GUI 依赖）：
SpeedTree / Meshlet 先细分，再按 GBuffer(Deferred) → Forward → Shadow → Pick →
Stencil → Depth/PrePass → 其它用途；实测 MHWS 语料“其它”为 0。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"

CASES = [
    ("DeferredStatic", "GBuffer"),
    ("ADeferredStatic", "GBuffer"),
    ("ATSSkinningDeferred", "GBuffer"),
    ("GBufferVisibility", "GBuffer"),
    ("FowardVisibility", "Forward"),
    ("ForwardVisibility", "Forward"),
    ("ADepthOutputDepthWritePreTransform", "Depth/PrePass"),
    ("ADepthOutputPrePassStatic", "Depth/PrePass"),
    ("ZPrePass", "Depth/PrePass"),
    ("ADepthOutputPickStatic", "Pick"),
    ("ADepthOutputStencilWriteStatic", "Stencil"),
    ("Shadow", "Shadow"),
    ("SpeedTreeV8ATreeFoliageO0Forward", "SpeedTree · Forward"),
    ("SpeedTreeV8AShadowMeshletVS", "SpeedTree · Shadow"),
    ("SpeedTreeV8AGBufferMeshletVS", "SpeedTree · GBuffer"),
    ("SpeedTreeV8TreeO0DepthWrite", "SpeedTree · Depth"),
    ("SpeedTreeV8TreeO0", "SpeedTree"),
    ("AGBufferMeshletVS", "Meshlet · GBuffer"),
    ("AShadowMeshletMS", "Meshlet · Shadow"),
    ("APreTransformStaticCS", "Compute"),
    ("AEmissiveOnly", "Emissive"),
    ("WaterFoam", "Water"),
    ("GUIVertex", "GUI"),
    ("SomethingUnknown", "其它"),
]


def _case_names():
    for name, want in CASES:
        got = SC.pass_family(name)
        assert got == want, (name, got, want)
    print("names: %d cases ok" % len(CASES))


def _mhws_file(pattern):
    hits = glob.glob(os.path.join(MHWS_ROOT, pattern), recursive=True)
    return hits[0] if hits else None


def _case_real(pattern, must_have):
    path = _mhws_file(pattern)
    if not path:
        print("real %s: skipped (unpack missing)" % pattern)
        return
    from material_toolkit.lib import game_profile as GP
    d = open(path, "rb").read()
    v = GP.resolve(d, SC.version_from_path(path))
    es = SC.record_entries(d, v)
    names = sorted({e["name"] for e in es if e["has_program"]})
    fams = {SC.pass_family(n) for n in names}
    others = [n for n in names if SC.pass_family(n) == "其它"]
    assert not others, (os.path.basename(path), others[:5])
    for fam in must_have:
        assert fam in fams, (os.path.basename(path), fam, sorted(fams))
    print("real %s: %d names, %d families, 0 others" %
          (os.path.basename(path), len(names), len(fams)))


def main():
    _case_names()
    _case_real("**/speedtree_uber.mmtr.250206176",
               ("SpeedTree", "SpeedTree · Shadow", "SpeedTree · Forward", "GBuffer"))
    _case_real("**/base_otomo_equip_gold.mmtr.240906211",
               ("Depth/PrePass", "Forward", "Stencil", "Shadow", "Pick", "Compute"))
    _case_real("**/base_atos_multiblend_vfx.mmtr.240906211",
               ("GBuffer", "Depth/PrePass", "Meshlet · Shadow"))
    print("ALL OK")


if __name__ == "__main__":
    main()
