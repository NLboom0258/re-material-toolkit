#!/usr/bin/env python3
"""回归: 按游戏/版本识别 mmtr 布局（game_profile）。

实测背景（2026-11-07）：
  * 记录步长跟**资源版本**走，与表形状无关 —— DMC5 1808168797 的 681 个文件
    有 4×272 / 4×228 / 4×104 / 4×36 / 4×33 五种形状，全部 0x108；
    MHWS 240906211 = 0x110（5 行），MHWS 250206176 = 0x110（**4 行**）。
  * 字段偏移在两个游戏里一致，校验器（stage_count_mismatches）在这三种
    版本、所有形状上都应给出 0 不一致。

本测试覆盖：版本映射 / 形状无关 / 未知版本启发式 / 非法文件。
"""
import glob
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mmtr_info as MI  # noqa: E402

ASSETS = r"E:\Project\Other\REEngine-mmtrReverse\assets\original"


def _build(rows, cols, stride, with_layout=True):
    """合成一个最小 mmtr 容器：记录 0 带 1 个 PS SRV，其余记录只写名字。"""
    n = rows * cols
    table_end = 0x10 + n * stride
    desc_off = table_end + 0x10
    name0_off = table_end + 0x40
    blob_start = table_end + 0x200
    buf = bytearray(blob_start)
    buf[0:4] = b"SDF\x00"
    struct.pack_into("<HH", buf, 4, rows, cols)
    struct.pack_into("<I", buf, 8, blob_start)
    struct.pack_into("<I", buf, 0x10, blob_start)   # 字符串指针(占位)
    for i in range(n):
        rec = 0x10 + i * stride
        nm = ("Rec%03d" % i).encode() + b"\x00"
        off = name0_off + i * 8
        buf[off:off + len(nm)] = nm
        struct.pack_into("<Q", buf, rec, off)
    if with_layout:
        rec = 0x10
        # 计数: 5 类 × 6 stage, 只有 SRV(第 3 类) 的 PS(第 5 位) 为 1
        struct.pack_into("<I", buf, rec + 0xD4, 1)
        buf[rec + 0xD8 + 2 * 6 + 4] = 1
        # 描述符表: 1 条, stage 掩码 PS(0x10)
        struct.pack_into("<I", buf, rec + 0x88, desc_off)
        buf[rec + 0xFC] = 1
        buf[desc_off + 6] = 0x10
    return bytes(buf)


def _case_dmc5():
    files = sorted(glob.glob(os.path.join(ASSETS, "*.mmtr.1808168797")))
    assert files, "no DMC5 sample mmtr found"
    for p in files:
        data = open(p, "rb").read()
        view = GP.resolve(data, 1808168797)
        assert view.profile.game == "DMC5", (p, view.profile.game)
        assert view.rec_stride == 0x108, (p, hex(view.rec_stride))
        bad = MI.stage_count_mismatches(data, view)
        assert bad == [], (os.path.basename(p), bad[:3])
        assert view.table_end == 0x10 + view.n_records * 0x108
        assert len(view.record_offsets()) == view.n_records
        assert view.record_offsets()[-1] + view.rec_stride == view.table_end
    print("DMC5 corpus: %d files, all 0x108 / 0 mismatches" % len(files))


def _case_mhws_synthetic():
    for rows, cols in ((5, 479), (5, 10), (4, 463)):
        data = _build(rows, cols, 0x110)
        view = GP.resolve(data, 240906211)
        assert view.profile.game == "MHWS"
        assert view.rec_stride == 0x110
        assert MI.stage_count_mismatches(data, view) == []
        # 250206176 是 4 行版本, 同样是 0x110
        view2 = GP.resolve(data, 250206176)
        assert view2.profile.game == "MHWS" and view2.rec_stride == 0x110
    print("MHWS synthetic: 5x479 / 5x10 / 4x463 all 0x110, 0 mismatches")


def _case_guess():
    for stride in (0x108, 0x110):
        data = _build(4, 2, stride)
        view = GP.resolve(data)          # 无版本号 -> 步长启发式
        assert view.profile.source == "guessed", view.profile
        assert view.rec_stride == stride, (hex(stride), hex(view.rec_stride))
        assert MI.stage_count_mismatches(data, view) == []
    print("guess: 0x108 / 0x110 both resolved without version")


def _case_bad():
    for data in (b"", b"NOTSDF" + b"\x00" * 64, b"SDF\x00" + b"\x00" * 64):
        try:
            GP.resolve(data)
        except ValueError:
            continue
        raise AssertionError("expected ValueError for junk input")
    print("junk inputs rejected")


def _case_mapping():
    assert GP.by_version(1808168797).game == "DMC5"
    assert GP.by_version(240906211).game == "MHWS"
    assert GP.by_version(250206176).game == "MHWS"
    assert GP.by_version(123456) is None
    assert GP.VERSION_STRIDE[1808168797] == 0x108
    assert GP.VERSION_STRIDE[250206176] == 0x110
    print("version map ok")


def _case_real_mhws_optional():
    """本机有荒野解包时额外跑真实文件；没有则跳过。"""
    root = r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
    picks = (
        (("mastermaterial", "master", "base_uber.mmtr.250206176"), 250206176),
        (("materialshader", "master", "speedtree_uber.mmtr.250206176"), 250206176),
        (("mastermaterial", "master", "base_uber.mmtr.240906211"), 240906211),
        (("materialshader", "master", "volumedecal_custom_base.mmtr.250206176"), 250206176),
    )
    done = 0
    for parts, ver in picks:
        p = os.path.join(root, *parts)
        if not os.path.exists(p):
            continue
        data = open(p, "rb").read()
        view = GP.resolve(data, ver)
        assert view.profile.game == "MHWS", parts
        assert view.rec_stride == 0x110, parts
        bad = MI.stage_count_mismatches(data, view)
        assert bad == [], (parts, bad[:3])
        done += 1
    print("real MHWS: %d samples checked" % done)


if __name__ == "__main__":
    _case_mapping()
    _case_dmc5()
    _case_mhws_synthetic()
    _case_guess()
    _case_bad()
    _case_real_mhws_optional()
    print("OK")
