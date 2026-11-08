#!/usr/bin/env python3
"""回归: 从 mmtr 直接读 UserMaterial 成员表（DMC5 / MHWS 同一格式）。

背景（2026-11-08）
------------------
MHWS 没有 RDEF，但材质参数并没有变成“bindless 独有模型”：参数布局仍写在
mmtr 的 cbuffer 名称池里。池条目 32B：

    name u64 | hash u32 | 0 | size u32 | nvar u32 | members u64

成员 16B：

    name u64 | hash u32 | packed(offset u16 | size u16)

实测对应关系：
  * EF_Skin_NPR（社区自定义母材质）：70 项 / 688B，与 mdf2 的 70 个参数
    名称+顺序+偏移完全一致；
  * Base_eye_Emit（原版）：44 项 / 224B，mdf2 只有 43 个参数 ——
    第 44 项是 CAPCOM_MATERIAL_RESERVE（引擎保留，mdf2 不写）；
  * DMC5 character_default：22 项 / 112B，首项 LimLight_Color。

没有对应样本时相应用例自动跳过。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mdf2 as M  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
MODS = r"E:\SteamLibrary\steamapps\common\MonsterHunterWilds\modmanager\Games\MonsterHunterWilds\Mods"
DMC5 = r"E:\Project\Other\REEngine-mmtrReverse\assets\original\character_default.mmtr.1808168797"


def _load(path, ver):
    data = open(path, "rb").read()
    view = GP.resolve(data, ver)
    return data, view, SC.record_entries(data, view)


def _case_format_invariants():
    p = os.path.join(MHWS_ROOT, "materialshader", "variation", "base_eye_emit.mmtr.240906211")
    if not os.path.exists(p):
        print("skip: base_eye_emit")
        return
    data, view, entries = _load(p, 240906211)
    tables = SC.user_material_tables(data, view, entries)
    assert len(tables) == 1, len(tables)
    t = tables[0]
    assert t["size"] == 224 and t["count"] == 44, (t["size"], t["count"])
    assert t["members"][0]["name"] == "ColorParam", t["members"][0]
    assert t["members"][0]["offset"] == 0
    # 偏移单调递增、大小是 4 的倍数
    last = -1
    for m in t["members"]:
        assert m["offset"] > last, m
        last = m["offset"]
        assert m["size"] % 4 == 0 and m["size"] > 0, m
    # 最后一个成员是引擎保留项
    assert t["members"][-1]["name"] == "CAPCOM_MATERIAL_RESERVE", t["members"][-1]
    print("format invariants ok (%d members, %d B)" % (t["count"], t["size"]))


def _case_matches_mdf2():
    mdf = os.path.join(MHWS_ROOT, "art", "model", "character", "ch00", "000", "0000",
                       "ch00_000_0000.mdf2.45")
    mm = os.path.join(MHWS_ROOT, "materialshader", "variation", "base_eye_emit.mmtr.240906211")
    if not (os.path.exists(mdf) and os.path.exists(mm)):
        print("skip: mdf2 cross-check")
        return
    data, view, entries = _load(mm, 240906211)
    members = SC.user_material_tables(data, view, entries)[0]["members"]
    members = [m for m in members if not m["name"].startswith("CAPCOM_")]
    mat = next(x for x in M.Mdf2.load(mdf).materials if x.name == "eye_L")
    assert mat.property_count == len(members), (mat.property_count, len(members))
    for mm_, pr in zip(members, mat.properties):
        assert mm_["name"] == pr.name, (mm_, pr.name)
        assert mm_["offset"] == pr.data_offset, (mm_, pr.data_offset)
        assert mm_["size"] == pr.param_count * 4, (mm_, pr.param_count)
    print("mdf2 cross-check ok (%d params)" % len(members))


def _case_custom_master():
    hits = glob.glob(os.path.join(MODS, "**", "EF_Skin_NPR.mmtr.240906211"), recursive=True)
    if not hits:
        print("skip: EF_Skin_NPR")
        return
    data, view, entries = _load(hits[0], 240906211)
    t = SC.user_material_tables(data, view, entries)[0]
    assert (t["size"], t["count"]) == (688, 70), (t["size"], t["count"])
    assert [m["name"] for m in t["members"]][:3] == ["ColorParam", "ObjectOffset", "RayTrace_BaseColor"]
    print("custom master ok (70 params / 688 B)")


def _case_dmc5():
    if not os.path.exists(DMC5):
        print("skip: DMC5 sample")
        return
    data, view, entries = _load(DMC5, 1808168797)
    t = SC.user_material_tables(data, view, entries)[0]
    assert t["count"] == 22 and t["size"] == 112, (t["count"], t["size"])
    assert t["members"][0]["name"] == "LimLight_Color", t["members"][0]
    print("DMC5 ok (22 params / 112 B)")


def _case_type_labels():
    assert SC.member_type_label(4) == "float"
    assert SC.member_type_label(16) == "float4"
    assert SC.member_type_label(64) == "float4x4"
    assert SC.member_type_label(20) == "20 B"
    print("type labels ok")


if __name__ == "__main__":
    _case_type_labels()
    _case_format_invariants()
    _case_matches_mdf2()
    _case_custom_master()
    _case_dmc5()
    print("OK")
