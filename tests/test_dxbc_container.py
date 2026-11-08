#!/usr/bin/env python3
"""回归: DXBC/DXIL 容器的 chunk 级读写（Stage B 第二步的地基）。

验收口径:
  1. identity() 对真实 blob 逐字节还原（MHWS 240906211 / 250206176 + DMC5）
  2. replace() 尺寸可变: STAT +4B ⇒ 长度 +4、total/hash 重算、换回逐字节还原
  3. chunk 顺序重排后仍可解析、内容不丢
  4. 非法输入（magic / 长度 / 越界）报 ContainerError
  5. HASH chunk: MHWS 有（flags=0、摘要未解）；DMC5 没有

MHWS 样本缺失时相应用例跳过。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from material_toolkit.lib import dxbc_container as DC  # noqa: E402
from material_toolkit.lib import game_profile as GP  # noqa: E402
from material_toolkit.lib import mmtr_patch as PT  # noqa: E402
from material_toolkit.lib import mmtr_scan as SC  # noqa: E402

MHWS_DIR = (r"E:\Project\Other\Mod\REEngine\MH\MHWs\Unpack\natives\stm"
            r"\materialshader\master")
MHWS = [os.path.join(MHWS_DIR, n) for n in (
    "speedtree_uber.mmtr.250206176",
    "static_uber.mmtr.240906211",
    "volumedecal_custom_base.mmtr.250206176",
)]
DMC5 = (r"E:\Project\Other\REEngine-mmtrReverse\assets\original"
        r"\character_default.mmtr.1808168797")


def _load(path):
    data = open(path, "rb").read()
    return data, GP.resolve(data, int(path.rsplit(".", 1)[1]))


def _blobs(data, view):
    """所有唯一 blob（按记录/stage 去重）。"""
    seen = set()
    for e in SC.record_entries(data, view):
        if not e["has_program"]:
            continue
        for st in e["stages"]:
            ptr, size, blob = PT.record_blob(data, view, e["index"], st)
            if ptr and ptr not in seen:
                seen.add(ptr)
                yield e["name"], st, blob


def _case_identity():
    total = 0
    for path in MHWS:
        if not os.path.exists(path):
            print("no sample %s, skip" % os.path.basename(path))
            continue
        data, view = _load(path)
        n = 0
        for name, st, blob in _blobs(data, view):
            out = DC.identity(blob)
            assert out == blob, "%s %s/%s identity mismatch (len %d/%d)" % (
                os.path.basename(path), name, st, len(out), len(blob))
            n += 1
        assert n > 30, n
        total += n
        print("%-42s identity %d/%d ok" % (os.path.basename(path), n, n))
    print("MHWS identity total: %d blobs" % total)


def _case_dmc5():
    if not os.path.exists(DMC5):
        print("no DMC5 sample, skip")
        return
    data, view = _load(DMC5)
    n = 0
    for name, st, blob in _blobs(data, view):
        assert DC.identity(blob) == blob, (name, st)
        assert DC.chunk(blob, "HASH") is None, "DMC5 must not have HASH"
        n += 1
    assert n > 20, n
    print("DMC5 identity %d/%d ok (no HASH chunk)" % (n, n))


def _first_ps():
    for path in MHWS:
        if not os.path.exists(path):
            continue
        data, view = _load(path)
        for e in SC.record_entries(data, view):
            if e["has_program"] and e["stages"]["ps"][0]:
                _, _, blob = PT.record_blob(data, view, e["index"], "ps")
                return blob
    return None


def _case_resize():
    blob = _first_ps()
    if blob is None:
        print("no MHWS sample, skip resize")
        return
    c0 = DC.parse(blob)
    assert c0["total"] == len(blob) and c0["unknown"] == 1
    h = DC.chunk(blob, "HASH")
    assert h is not None and len(h) == 20 and h[:4] == bytes(4), "HASH shape changed"

    for name in ("STAT", "ISG1", "DXIL"):
        body = DC.chunk(blob, name)
        assert body is not None, name
        # 加 4 字节（保持 4 对齐，避免误以为是 padding 问题）
        bigger = DC.replace(blob, name, body + bytes(4))
        assert len(bigger) == len(blob) + 4, (name, len(bigger), len(blob))
        c = DC.parse(bigger)
        assert c["total"] == len(bigger)
        assert c["hash"] != c0["hash"], name
        assert DC.chunk(bigger, name) == body + bytes(4)
        # 其他 chunk 原样
        for k in c0["chunks"]:
            if k["name"] == name.encode("latin1"):
                continue
            assert DC.chunk(bigger, k["name"]) == k["data"], (name, k["name"])
        # 换回 ⇒ 逐字节还原
        assert DC.replace(bigger, name, body) == blob, name
        # 缩短也行
        smaller = DC.replace(blob, name, body[:-4])
        assert len(smaller) == len(blob) - 4
        assert DC.replace(smaller, name, body) == blob
    print("resize: STAT/ISG1/DXIL +-4 B all rebuild and roundtrip ok")


def _case_reorder():
    blob = _first_ps()
    if blob is None:
        print("no MHWS sample, skip reorder")
        return
    chunks = [(k["name"], k["data"]) for k in DC.parse(blob)["chunks"][::-1]]
    out = DC.build(chunks)
    assert DC.order(out) == DC.order(blob)[::-1]
    assert DC.parse(out)["total"] == len(blob)
    for name, data in chunks:
        assert DC.chunk(out, name) == data
    print("reorder: reversed chunk order ok (%d chunks)" % len(chunks))


def _case_guards():
    blob = _first_ps()
    if blob is None:
        print("no MHWS sample, skip guards")
        return
    bad = [b"XXXX" + blob[4:], blob[:20], blob[:40] + b"\x00" * 4 + blob[44:]]
    for b in bad:
        try:
            DC.parse(b)
        except DC.ContainerError:
            pass
        else:
            raise AssertionError("must reject malformed container")
    try:
        DC.replace(blob, "NOPE", b"")
    except DC.ContainerError:
        pass
    else:
        raise AssertionError("must reject missing chunk")
    print("guards: magic/short/out-of-range/missing-chunk all rejected")


def main():
    _case_identity()
    _case_dmc5()
    _case_resize()
    _case_reorder()
    _case_guards()
    print("OK")


if __name__ == "__main__":
    main()
