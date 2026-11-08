#!/usr/bin/env python3
"""回归: mdf2 的 GPBF（ByteAddressBuffer 引用）解析、写回与路径编辑。

背景（2026-11-08）
------------------
mdf2 v19+ 的每个材质带一组 GPBF 表：每对是
  (槽位名条目, 路径条目)，各 16B（name_off q + utf16_hash I + ascii_hash I）。
槽位名是 shader 里绑定 buffer 的名字（如 `FoliageBAB_StaticParam`），
路径指向 `MasterMaterial/ByteAddressBuffer/<名字>.gpbf` 资源。

工具策略：解析 → 原样写回（`_save_v31` 用 set_str 写路径并按名字重算 hash，
路径条目的 hash 固定 0/1）；GUI 只允许改路径，槽位名/条数不改。

验收口径：
  1. 真实语料：所有 GPBF 路径都以 `.gpbf` 结尾、槽位名非空，路径条目 hash 恒为 (0,1)
     —— 解析没有错位。路径本身是**普通资源路径**，实测 168 对里有 161 对在
     `MasterMaterial/ByteAddressBuffer/`，其余在 `systems/rendering`、
     `GameDesign/Enemy/CommonData/Art`、`Art/Model/Character/.../inner/` 等目录，
     所以不能按固定前缀校验；
  2. 改路径 → save → reload：新路径/hash 正确，槽位名不变；
  3. 未改动时 load→save 逐字节一致。
"""
import glob
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import mdf2 as M  # noqa: E402

MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
SAMPLE = os.path.join(MHWS_ROOT, "art", "model", "character",
                      "ch00", "000", "0000", "ch00_000_0000.mdf2.45")


def _case_corpus():
    files = glob.glob(os.path.join(MHWS_ROOT, "**", "*.mdf2.45"), recursive=True)
    random.seed(11)
    sample = random.sample(files, min(20, len(files)))
    pairs = 0
    with_buf = 0
    for p in sample:
        m = M.Mdf2.load(p)
        for mat in m.materials:
            if not mat.gpu_buffers:
                continue
            with_buf += 1
            for nh, dh in mat.gpu_buffers:
                pairs += 1
                assert nh["name"] and dh["name"], (p, mat.name, nh, dh)
                assert dh["name"].endswith(".gpbf"), (p, mat.name, dh["name"])
                assert (dh["utf16_hash"], dh["ascii_hash"]) == (0, 1), (p, dh)
    assert pairs > 50, pairs
    print("corpus: %d files, %d materials with GPBF, %d pairs, all paths well formed"
          % (len(sample), with_buf, pairs))


def _case_roundtrip():
    raw = open(SAMPLE, "rb").read()
    m = M.Mdf2.load(SAMPLE)
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "rt.mdf2.45")
        m.save(out)
        assert open(out, "rb").read() == raw, "unmodified save must be byte-identical"
    print("round-trip: byte-identical")


def _case_edit():
    m = M.Mdf2.load(SAMPLE)
    idx = next(i for i, mt in enumerate(m.materials) if mt.gpu_buffers)
    mat = m.materials[idx]
    nh, dh = mat.gpu_buffers[0]
    slot, old = nh["name"], dh["name"]
    new = old.replace(".gpbf", "_Edited.gpbf")
    dh["name"] = new
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "edit.mdf2.45")
        m.save(out)
        r = M.Mdf2.load(out)
    mm = r.materials[idx]
    nh2, dh2 = mm.gpu_buffers[0]
    assert nh2["name"] == slot, (nh2["name"], slot)
    assert dh2["name"] == new, dh2["name"]
    assert (dh2["utf16_hash"], dh2["ascii_hash"]) == (0, 1), dh2
    assert len(mm.gpu_buffers) == len(mat.gpu_buffers)
    assert len(r.materials) == len(m.materials)
    # 其它材质的 GPBF 不受影响
    for i, mt in enumerate(m.materials):
        if i == idx:
            continue
        assert [d["name"] for _n, d in mt.gpu_buffers] == \
               [d["name"] for _n, d in r.materials[i].gpu_buffers], i
    print("edit: %r -> %r, slot/hash/count intact" % (old, new))


if __name__ == "__main__":
    if not glob.glob(os.path.join(MHWS_ROOT, "**", "*.mdf2.45"), recursive=True):
        print("no mdf2.45 corpus, skip")
        sys.exit(0)
    _case_corpus()
    _case_roundtrip()
    _case_edit()
    print("OK")
