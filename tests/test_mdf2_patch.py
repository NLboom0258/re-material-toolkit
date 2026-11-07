#!/usr/bin/env python3
"""回归: mdf2 的“只改值写回”（不改结构）。

背景
----
mdf2.45(MHWS) 等版本还没有结构写入(save() 只支持 <19)，但参数值 / 着色类型 /
material_flags / shaderLODNum 都是**定长字段**，可以原地补丁写回：长度、偏移、
字符串池一律不动，因此不会出现“重排导致引用错位”的风险。

本测试用真实 mdf2.45 校验：
  * 补丁前后长度一致；
  * 只有被改的字段发生变化（其余字节逐字节相同）；
  * 重新解析后值/着色类型/flags 正确，其它材质与未改参数不受影响；
  * 结构类改动（改分量数）必须报 PatchError，不能静默写坏。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mdf2 as M  # noqa: E402

MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm\art\model\character"
MOD_MDF2 = (r"E:\SteamLibrary\steamapps\common\MonsterHunterWilds\modmanager\Games"
            r"\MonsterHunterWilds\Mods\Liino+v1.1+-+CBS\natives\STM\Art\Model"
            r"\Character\ch03\018\001\2\ch03_018_0012.mdf2.45")


def _find_sample():
    if os.path.exists(MOD_MDF2):
        return MOD_MDF2
    for dirpath, _dirs, files in os.walk(MHWS_ROOT):
        for fn in files:
            if fn.endswith(".mdf2.45"):
                return os.path.join(dirpath, fn)
    return None


def _patched_case(path):
    raw = open(path, "rb").read()
    m = M.Mdf2.load(path)
    mat = m.materials[0]
    pr = mat.properties[0]
    assert mat.shader_type_offset and mat.flags_offset and pr.value_offset

    old_vals = list(pr.values)
    old_st, old_fl = int(mat.shader_type), int(mat.flags)
    pr.values[0] = old_vals[0] + 0.25
    mat.shader_type = 0 if old_st else 1
    mat.flags = old_fl ^ 1
    out = m.patch_bytes(raw)

    assert len(out) == len(raw), (len(out), len(raw))
    diff = [i for i in range(len(raw)) if raw[i] != out[i]]
    # 着色类型 4 字节里通常只有 1 字节变；flags 同理；值 4 字节同理。
    allowed = set()
    allowed.update(range(mat.shader_type_offset, mat.shader_type_offset + 4))
    allowed.update(range(mat.flags_offset, mat.flags_offset + mat.flags_width))
    allowed.update(range(pr.value_offset, pr.value_offset + 4))
    outside = [i for i in diff if i not in allowed]
    assert not outside, ("改到了值区以外的字节", [hex(x) for x in outside[:8]])
    assert diff, "补丁没有产生任何改动"

    # 重新解析补丁结果
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "mdf2_patch_regress.mdf2.45")
    open(tmp, "wb").write(out)
    m2 = M.Mdf2.load(tmp)
    a, b = m.materials[0], m2.materials[0]
    assert abs(b.properties[0].values[0] - (old_vals[0] + 0.25)) < 1e-5
    assert b.shader_type == (0 if old_st else 1)
    assert b.flags == old_fl ^ 1
    # 未改参数一致
    assert [list(p.values) for p in a.properties[1:]] == \
           [list(p.values) for p in b.properties[1:]]
    # 其它材质：名字 / mmtr / 贴图 / 参数全一致
    for x, y in zip(m.materials[1:], m2.materials[1:]):
        assert (x.name, x.mmtr_path) == (y.name, y.mmtr_path)
        assert [(t.texture_type, t.texture_path) for t in x.textures] == \
               [(t.texture_type, t.texture_path) for t in y.textures]
        assert [list(p.values) for p in x.properties] == \
               [list(p.values) for p in y.properties]
    os.remove(tmp)
    return m, mat, pr, len(diff)


def _case_real_file():
    p = _find_sample()
    if not p:
        print("no mdf2.45 sample, skip")
        return
    m, mat, pr, n = _patched_case(p)
    print("patch ok: %s, %d material(s), %d byte(s) changed"
          % (os.path.basename(p), len(m.materials), n))


def _case_reject_structural():
    p = _find_sample()
    if not p:
        print("no mdf2.45 sample, skip reject case")
        return
    raw = open(p, "rb").read()
    m = M.Mdf2.load(p)
    pr = m.materials[0].properties[0]
    pr.values = pr.values[:1]          # 改分量数 = 结构改动
    try:
        m.patch_bytes(raw)
    except M.PatchError as e:
        print("shrink rejected:", str(e)[:48])
        return
    raise AssertionError("改分量数竟然被接受")


def _case_reject_bad_signature():
    m = M.Mdf2.new(45)
    try:
        m.patch_bytes(b"NOTMDF" + b"\x00" * 32)
    except M.PatchError:
        print("bad signature rejected")
        return
    raise AssertionError("非 MDF 头竟然被接受")


if __name__ == "__main__":
    _case_reject_bad_signature()
    _case_real_file()
    _case_reject_structural()
    print("OK")
