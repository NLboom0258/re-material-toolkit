#!/usr/bin/env python3
"""re-material-toolkit — 一键启动 GUI(Material Studio)。

用法:
    python run.py [<file.mmtr.*> | <file.mdf2.*>]

说明: GUI 是本工具的主入口; 底层可编程库与 CLI 见 material_toolkit/。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from material_studio.app import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(sys.argv))
