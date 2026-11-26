#!/usr/bin/env python3
"""回归：MHWS 记录计数能不能由 PSV0 反推（mmtr_recount）。

口径：
  1. ToonTest 全量：`diff()` 为空（per-stage 四类 + total 全吻合）；
  2. 幂等：`recount(write=True)` 不改变任何字节；
  3. 破坏后恢复：把一条记录的计数矩阵 + total 清零，recount 逐字节还原；
  4. 语料抽样：其它 MHWS mmtr 也全部吻合（样本缺失则跳过）。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP      # noqa: E402
from material_toolkit.lib import mmtr_recount as RC      # noqa: E402
from material_toolkit.lib import mmtr_scan as SC         # noqa: E402

TOON = r"E:\Project\Other\REEngine-mmtrReverse\workbench\mhws_liino\mdf2\ToonTest.mmtr.240906211.orig"
MHWS_ROOT = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"


def _load(path):
    data = open(path, "rb").read()
    ver = SC.version_from_path(path) or 240906211
    return data, GP.resolve(data, ver)


def _records_with_program(data, view):
    return [e for e in SC.record_entries(data, view) if e["has_program"]]


def _case_diff_clean():
    if not os.path.isfile(TOON):
        print("diff: skipped")
        return
    data, view = _load(TOON)
    items = RC.diff(data, view)
    assert not items, items[:5]
    n = len(_records_with_program(data, view))
    print("diff: ToonTest %d 条记录全部吻合" % n)


def _case_idempotent():
    if not os.path.isfile(TOON):
        print("idempotent: skipped")
        return
    data, view = _load(TOON)
    out, stats = RC.recount(data, view)
    assert out == data, "recount 改动了已经正确的文件"
    print("idempotent: %d 条记录，0 字节变化" % stats["records"])


def _case_restore():
    if not os.path.isfile(TOON):
        print("restore: skipped")
        return
    data, view = _load(TOON)
    recs = _records_with_program(data, view)
    assert recs, "no record with program"
    e = recs[len(recs) // 2]
    rec = view.rec_base + e["index"] * view.rec_stride
    buf = bytearray(data)
    buf[rec + view.count_matrix_off:rec + view.count_matrix_off + 30] = b"\x00" * 30
    buf[rec + view.count_total_off:rec + view.count_total_off + 4] = b"\x00" * 4
    broken = bytes(buf)
    fixed, _stats = RC.recount(broken, view)
    assert fixed == data, "recount 未能从清零状态还原"
    print("restore: 记录 %d (%s) 清零后还原成功" % (e["index"], e["name"]))


def _case_corpus(limit=6):
    if not os.path.isdir(MHWS_ROOT):
        print("corpus: skipped")
        return
    files = sorted(glob.glob(os.path.join(MHWS_ROOT, "**", "*.mmtr.*"), recursive=True))
    files = [p for p in files if p.rsplit(".", 1)[-1] in ("240906211", "250206176")]
    if not files:
        print("corpus: skipped (empty)")
        return
    step = max(1, len(files) // limit)
    used = 0
    for p in files[::step][:limit]:
        data, view = _load(p)
        items = RC.diff(data, view)
        assert not items, (os.path.basename(p), items[:3])
        used += 1
    print("corpus: %d 个 mmtr 全部吻合" % used)


if __name__ == "__main__":
    _case_diff_clean()
    _case_idempotent()
    _case_restore()
    _case_corpus()
    print("ALL PASS")
