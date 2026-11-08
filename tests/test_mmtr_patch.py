#!/usr/bin/env python3
"""回归: mmtr 同尺寸 blob 替换（Stage B 第一步）。

只做「原地换 DXBC 容器 + 重算 8 字节缓存键」，所以验收口径是字节级的：

  1. 输出长度不变；
  2. 差异只出现在「被改的字节」+「blob 后的 8 字节键」两处，其他字节逐字节相同；
  3. 写回的键 == xxh64(新 blob, seed=0xFFFFFFFF)，且全文件所有 blob 的键校验通过；
  4. 重新解析后，该记录的 blob 内容 == 新 blob；
  5. 幂等：换回原 blob ⇒ 与原文逐字节相同；
  6. 尺寸不符 / 非 DXBC / 记录没有该 stage ⇒ PatchError；
  7. DMC5（无缓存键）：差异只有被改的那 1 个字节。

MHWS 文件缺失时相应用例跳过。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import hashes as H  # noqa: E402
from material_toolkit.lib import mmtr_patch as PT  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

MHWS = (r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
        r"\materialshader\master\speedtree_uber.mmtr.250206176")
DMC5 = r"E:\Project\Other\REEngine-mmtrReverse\assets\original\character_default.mmtr.1808168797"


def _load(path):
    data = open(path, "rb").read()
    ver = int(path.rsplit(".", 1)[1])
    return data, GP.resolve(data, ver)


def _diff_positions(a, b):
    assert len(a) == len(b)
    return [i for i in range(len(a)) if a[i] != b[i]]


def _pick_rec_with_ps(data, view):
    for e in SC.record_entries(data, view):
        if e["has_program"] and e["stages"]["ps"][0]:
            return e
    raise AssertionError("没有带 PS 的记录")


def _case_mhws():
    if not os.path.exists(MHWS):
        print("no MHWS sample, skip")
        return
    data, view = _load(MHWS)
    e = _pick_rec_with_ps(data, view)
    ptr, size, old = PT.record_blob(data, view, e["index"], "ps")
    assert len(old) == size and old[:4] == b"DXBC"

    # 改最后一个字节（容器尾，不影响头部结构）
    new = bytearray(old)
    new[-1] ^= 0xFF
    new = bytes(new)
    out, info = PT.replace_blob(data, view, e["index"], "ps", new)

    assert len(out) == len(data), (len(out), len(data))
    assert info["trailer"] == 8 and info["cache_key"] == H.blob_cache_key(new)

    diff = _diff_positions(data, out)
    expect = {ptr + size - 1} | set(range(ptr + size, ptr + size + 8))
    assert set(diff) == expect, (len(diff), sorted(set(diff) - expect)[:5])

    # 重新解析：blob 内容已更新
    out_view = GP.resolve(out, int(MHWS.rsplit(".", 1)[1]))
    p2, s2, blob2 = PT.record_blob(out, out_view, e["index"], "ps")
    assert (p2, s2) == (ptr, size) and blob2 == new

    # 全文件缓存键自校验
    ok, bad, details = PT.verify_trailers(out, out_view)
    assert bad == 0, details
    assert ok >= 500, ok

    # 幂等：换回原 blob ⇒ 原文
    back, _ = PT.replace_blob(out, out_view, e["index"], "ps", old)
    assert back == data

    # 共享范围：这个 blob 被多少记录引用（写进输出，便于看清影响面）
    refs = PT.blob_refs(data, view, ptr)
    print("MHWS: rec#%d %s ptr=0x%X size=%d diff=%d trailer_ok=%d shared_refs=%d"
          % (e["index"], e["name"], ptr, size, len(diff), ok, len(refs)))


def _case_guards():
    if not os.path.exists(MHWS):
        print("no MHWS sample, skip guards")
        return
    data, view = _load(MHWS)
    e = _pick_rec_with_ps(data, view)
    ptr, size, old = PT.record_blob(data, view, e["index"], "ps")

    for bad, why in ((old[:-1], "short"), (old + b"\x00", "long"),
                     (b"XXXX" + old[4:], "magic")):
        try:
            PT.replace_blob(data, view, e["index"], "ps", bad)
        except PT.PatchError:
            pass
        else:
            raise AssertionError("must reject " + why)
    # 记录没有该 stage
    empty = next(e2 for e2 in SC.record_entries(data, view) if not e2["stages"]["cs"][0])
    try:
        PT.replace_blob(data, view, empty["index"], "cs", old)
    except PT.PatchError:
        pass
    else:
        raise AssertionError("must reject missing stage")
    print("guards: size/magic/missing-stage all rejected")


def _case_dmc5():
    if not os.path.exists(DMC5):
        print("no DMC5 sample, skip")
        return
    data, view = _load(DMC5)
    e = _pick_rec_with_ps(data, view)
    ptr, size, old = PT.record_blob(data, view, e["index"], "ps")
    new = bytearray(old)
    new[-1] ^= 0xFF
    new = bytes(new)
    out, info = PT.replace_blob(data, view, e["index"], "ps", new)
    assert len(out) == len(data) and info["trailer"] == 0 and info["cache_key"] is None
    diff = _diff_positions(data, out)
    assert diff == [ptr + size - 1], (len(diff), diff[:5])
    assert PT.verify_trailers(out, GP.resolve(out, 1808168797)) == (0, 0, [])
    print("DMC5: rec#%d %s diff=%d (no trailer) ok" % (e["index"], e["name"], len(diff)))


def main():
    _case_mhws()
    _case_guards()
    _case_dmc5()
    print("OK")


if __name__ == "__main__":
    main()
