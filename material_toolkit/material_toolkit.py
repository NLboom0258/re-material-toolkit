#!/usr/bin/env python3
"""material_toolkit CLI — mdf2 / mmtr 材质接口工具。

用法:
  python material_toolkit.py mdf2-dump    <file.mdf2>
  python material_toolkit.py mmtr-dump    <file.mmtr>
  python material_toolkit.py mmtr-add-param <in.mmtr> <cbuffer> <name> <size> <offset> -o <out.mmtr>

说明:
  - mdf2 结构/读写见 lib/mdf2.py; mmtr 头部/参数表见 lib/mmtr.py;
  - hash = murmur3(名字, 0xFFFFFFFF),见 lib/hashes.py。
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib.mdf2 import Mdf2      # noqa: E402
from lib.mmtr import Mmtr      # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    a = sys.argv[2:]

    if cmd == "mdf2-dump":
        Mdf2.load(a[0]).dump()
    elif cmd == "mmtr-dump":
        Mmtr.load(a[0]).dump()
    elif cmd == "mmtr-add-param":
        src, cbuffer, name, size, offset = a[0], a[1], a[2], int(a[3]), int(a[4])
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr"
        m = Mmtr.load(src)
        data = m.add_cbuffer_param(cbuffer, name, size, offset)
        open(out, "wb").write(data)
        print(f"OK: {src} + param {name}(size={size},off={offset}) -> {out} "
              f"({len(m.data)} -> {len(data)} bytes)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
