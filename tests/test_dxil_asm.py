#!/usr/bin/env python3
"""回归: DXIL 文本 IR 读写（反汇编 / 汇编 / 改输出 / 只换 DXIL）。

验收口径：
  1. disassemble → assemble 能得到可 validate 的容器
  2. patch_store_output 改某个 SV_Target 分量后：
     - chunk 列表不变（ILDN/STAT/HASH 全部保留）
     - STAT / ILDN 大小不变，DXIL 大小变化
     - IR diff 只有目标那几行
  3. assemble 的**新容器**不能直接用（缺 ILDN），必须走 replace_dxil

缺少 dxcompiler.dll 或 MHWS 样本时跳过。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import dxbc_container as DC  # noqa: E402
from material_toolkit.lib import dxil_asm as A  # noqa: E402
from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mmtr_patch as PT  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

MHWS = (r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
        r"\materialshader\master\speedtree_uber.mmtr.250206176")


def _ps_blob():
    """取一个带 DXIL 且**真的写 RT1** 的 Deferred PS blob；没有样本返回 None。"""
    if not os.path.exists(MHWS):
        return None
    data = open(MHWS, "rb").read()
    view = GP.resolve(data, 250206176)
    for e in SC.record_entries(data, view):
        if "deferred" not in e["name"].lower():
            continue
        if not (e["has_program"] and e["stages"]["ps"][0]):
            continue
        _ptr, _size, blob = PT.record_blob(data, view, e["index"], "ps")
        if b"DXIL" in blob[:4096]:
            return blob
    return None


def _case_roundtrip(blob):
    text = A.disassemble(blob)
    assert "define" in text and text.count("\n") > 100, len(text)
    new = A.assemble(text)
    A.validate(new)
    assert new[:4] == b"DXBC" and DC.parse(new)["total"] == len(new)
    print("roundtrip: %d IR lines, container %d -> %d, validate ok"
          % (text.count("\n"), len(blob), len(new)))


def _case_patch(blob):
    before = DC.parse(blob)
    # 用不易与既有常量碰撞的值（样本的 RT1 某分量恰好就是 1.0）
    new = A.patch_store_output(blob, rt=1, col=0, value=0.123456)
    after = DC.parse(new)
    names0 = [k["name"] for k in before["chunks"]]
    names1 = [k["name"] for k in after["chunks"]]
    assert names0 == names1, (names0, names1)
    size0 = {k["name"]: k["size"] for k in before["chunks"]}
    size1 = {k["name"]: k["size"] for k in after["chunks"]}
    assert size0[b"STAT"] == size1[b"STAT"], (size0[b"STAT"], size1[b"STAT"])
    assert size0[b"ILDN"] == size1[b"ILDN"]
    assert size1[b"DXIL"] != size0[b"DXIL"]
    a = A.disassemble(blob).splitlines()
    b = A.disassemble(new).splitlines()
    assert len(a) == len(b)
    changed = sum(1 for x, y in zip(a, b) if x != y)
    assert 0 < changed <= 8, changed
    print("patch: chunks kept, STAT/ILDN unchanged, DXIL %d -> %d, %d IR lines changed"
          % (size0[b"DXIL"], size1[b"DXIL"], changed))


def _case_naive_container_rejected(blob):
    """assemble 出的新容器缺 ILDN —— 确认这一点，防止有人误用。"""
    text = A.disassemble(blob)
    naive = A.assemble(text)
    names = [k["name"] for k in DC.parse(naive)["chunks"]]
    assert b"ILDN" not in names, names
    merged = A.replace_dxil(blob, A.container_part(naive, "DXIL"))
    assert b"ILDN" in [k["name"] for k in DC.parse(merged)["chunks"]]
    print("guard: assembled container lacks ILDN (as expected), replace_dxil keeps it")


def main():
    if not A.dxc_available():
        print("no dxcompiler.dll, skip")
        return
    blob = _ps_blob()
    if blob is None:
        print("no MHWS sample, skip")
        return
    _case_roundtrip(blob)
    _case_patch(blob)
    _case_naive_container_rejected(blob)
    print("OK")


if __name__ == "__main__":
    main()
