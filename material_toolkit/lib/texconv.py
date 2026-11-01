# -*- coding: utf-8 -*-
"""texconv.dll（matyalatte/Texconv-Custom-DLL）封装：图片 → DX10 DDS。

DLL 位置：``material_toolkit/bin/texconv.dll``（与 d3dcompiler_47.dll 同目录，
随仓库/发布包分发；MIT，见 ``bin/THIRD_PARTY_LICENSES.txt`` 与 ``THIRD_PARTY.md``）。

C API（与 ReMeshEditor / Modding-Toolkit 用法一致）::

    int texconv(int argc, const wchar_t** argv, bool verbose, bool ignore_errors,
                bool allow_slow_codec, wchar_t* err_buf, size_t err_buf_size)

参数就是 texconv 命令行参数。关键选项（Modding-Toolkit 实测结论）：

- 目标格式带 SRGB 时传 ``-srgbi``：声明输入是 sRGB，避免 texconv 再做一次
  linear→sRGB 编码（否则 BC7 载荷会变亮，与线性版本不一致）；
- ``-sepalpha``：否则 mip 生成会把 alpha 弄坏；
- 不传 ``-m`` = 生成完整 mip 链；``mips=False`` 时传 ``-m 1``；
- ``-if CUBIC``：与 REE Content Editor 一致，低层 mip 细节更好。

输出是 DX10 头 DDS，可直接交给 ``tex_file.parse_dds``。
"""
from __future__ import annotations

import ctypes
import os

from . import tex_file

_DLL = None


def dll_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "bin", "texconv.dll")


def available():
    return os.path.isfile(dll_path())


def _ensure_com():
    """texconv 用 WIC 读图片，需要 COM 初始化；重复调用无副作用。"""
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # APARTMENTTHREADED
    except Exception:  # noqa: BLE001 —— 失败时交给 texconv 自己报错
        pass


def _load():
    global _DLL
    if _DLL is not None:
        return _DLL
    p = dll_path()
    if not os.path.isfile(p):
        raise RuntimeError("texconv.dll 不存在：%s" % p)
    _ensure_com()
    _DLL = ctypes.cdll.LoadLibrary(p)
    return _DLL


def convert_to_dds(src, fmt, out_dir, mips=True, image_filter="CUBIC"):
    """把图片（PNG/TGA/JPG/BMP/TIFF…）转成 DX10 DDS，返回输出路径。

    ``fmt`` 是 DXGI 名称（``BC7_UNORM`` / ``BC7_UNORM_SRGB`` / ``BC1_UNORM`` /
    ``BC3_UNORM`` / ``BC5_UNORM`` / ``BC4_UNORM`` / ``R8G8B8A8_UNORM`` …），
    必须在 ``tex_file`` 的白名单里。
    """
    fmt_value = tex_file.fmt_from_name(fmt)
    name = tex_file.fmt_name(fmt_value)
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    os.makedirs(out_dir, exist_ok=True)
    dll = _load()

    args = ["-f", name, "-dx10", "-sepalpha"]
    if tex_file.is_srgb(fmt_value):
        args += ["-srgbi"]
    if not mips:
        args += ["-m", "1"]
    if image_filter:
        args += ["-if", image_filter]
    if "SNORM" in name or "SF16" in name:
        args += ["-x2bias"]
    args += ["-y", "-o", out_dir, "--", os.path.normpath(src)]

    argv = (ctypes.c_wchar_p * len(args))(*[ctypes.c_wchar_p(a) for a in args])
    err = ctypes.create_unicode_buffer(1024)
    rc = dll.texconv(len(args), argv, False, False, False, err, len(err))
    if rc != 0:
        raise RuntimeError(err.value or ("texconv 返回 %d" % rc))

    out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + ".dds")
    if not os.path.isfile(out):
        raise RuntimeError("texconv 没有生成预期的 DDS：%s" % out)
    return out
