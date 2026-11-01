# -*- coding: utf-8 -*-
"""DDS → RE Engine .tex（Legacy，DMC5 v11）转换 / .tex 解析。

字段布局与关键坑来自两处公开实现 + 本机实测（2026-11-01）：
- kagenocookie/RE-Engine-Lib ``TexFile.cs``：Legacy header 32B，depth=1、
  swizzleControl=-1；mip 表 16B（offset/pitch/size）。
- Modding-Toolkit ``core/tex_file.py``：Legacy **不做** 256 行 padding，
  ``format`` 直接存 DDS 的 DXGI 数值（不经格式名→内部表，避免 sRGB 丢失）；
  注释记录 depth=0 会让 DX12 崩溃（D3D12 DepthOrArraySize 不能为 0）。

DMC5 vanilla 实测（``texture/*.tex.11``，2814 个样本中的代表）：

    magic=00584554 version=11 depth=1 imageCount=1 mipCount=N
    swizzleControl=-1 cubemap=0 flags=0 format=<DXGI 数值>
    header 32B + mip 表 16B*N + mip 数据（无 padding；offset = 32 + 16*N）
    2048x2048 BC7：pitch=8192, size=4194304, mip0 offset=32+12*16=224
    4x4 BC7：pitch=16, size=16, offset=80/96/112（同样无 padding）

只做 2D 单面纹理（DMC5 的贴图槽用不到 cubemap/array；遇到直接拒绝）。
legacy FourCC DDS（DXT1/3/5 等）暂不支持，提示用户用 texconv 导出 DX10 DDS。
"""
from __future__ import annotations

import struct

TEX_MAGIC = 0x00584554
LEGACY_MAX_VERSION = 27
DMC5_TEX_VERSION = 11

_HEADER = struct.Struct("<IIHHHBBIiII")   # 32B（Legacy）
_MIP = struct.Struct("<qii")              # 16B: offset(int64), pitch(int32), size(int32)

DDS_MAGIC = 0x20534444
DX10_FOURCC = 0x30315844
_DDS_HEADER = struct.Struct("<7I 44s 32s 4I I")   # 124B（magic 之后）
_DX10 = struct.Struct("<5I")

_DDSD_FLAGS = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000   # CAPS|HEIGHT|WIDTH|PIXELFORMAT|MIPMAPCOUNT
_DDPF_FOURCC = 0x4
_DDSCAPS_TEXTURE = 0x1000
_DDSCAPS_COMPLEX = 0x8
_DDSCAPS_MIPMAP = 0x400000
_DDS_DIMENSION_TEXTURE2D = 3
_DDS_MISC_TEXTURECUBE = 0x4


class TexError(ValueError):
    """DDS/.tex 结构不合法（给 UI 直接展示的文案）。"""


# DXGI 格式表：fmt -> (名称, sRGB 变体（没有则 None）, 每块/像素描述)
# 只收录 2D 纹理常用格式；typeless 故意不收（引擎应按具体类型创建资源）。
# (kind, n): kind="bc" → n = 每 4x4 块字节数；kind="linear" → n = 每像素字节数
_FMT_INFO = {
    28: ("R8G8B8A8_UNORM", 29, ("linear", 4)),
    29: ("R8G8B8A8_UNORM_SRGB", 28, ("linear", 4)),
    49: ("R8G8_UNORM", None, ("linear", 2)),
    61: ("R8_UNORM", None, ("linear", 1)),
    65: ("A8_UNORM", None, ("linear", 1)),
    71: ("BC1_UNORM", 72, ("bc", 8)),
    72: ("BC1_UNORM_SRGB", 71, ("bc", 8)),
    74: ("BC2_UNORM", 75, ("bc", 16)),
    75: ("BC2_UNORM_SRGB", 74, ("bc", 16)),
    77: ("BC3_UNORM", 78, ("bc", 16)),
    78: ("BC3_UNORM_SRGB", 77, ("bc", 16)),
    80: ("BC4_UNORM", None, ("bc", 8)),
    81: ("BC4_SNORM", None, ("bc", 8)),
    83: ("BC5_UNORM", None, ("bc", 16)),
    84: ("BC5_SNORM", None, ("bc", 16)),
    95: ("BC6H_UF16", None, ("bc", 16)),
    96: ("BC6H_SF16", None, ("bc", 16)),
    98: ("BC7_UNORM", 99, ("bc", 16)),
    99: ("BC7_UNORM_SRGB", 98, ("bc", 16)),
}

# 未在 DMC5 vanilla 里实测过的格式：只提示，不拒绝。
_UNVERIFIED_DMC5 = {74, 75, 95, 96}


def fmt_name(fmt):
    info = _FMT_INFO.get(fmt)
    return info[0] if info else ("DXGI_%d" % fmt)


def fmt_label(fmt):
    """``BC7_UNORM_SRGB (99)`` 形式，方便 UI/日志。"""
    return "%s (%d)" % (fmt_name(fmt), fmt)


def is_srgb(fmt):
    return "SRGB" in fmt_name(fmt)


def has_srgb_variant(fmt):
    info = _FMT_INFO.get(fmt)
    return bool(info and info[1])


def with_srgb(fmt, on):
    """返回同族线性/sRGB 变体的 DXGI 值；没有变体时抛 TexError。"""
    info = _FMT_INFO.get(fmt)
    if not info:
        raise TexError("不支持的 DXGI 格式：%d" % fmt)
    if is_srgb(fmt) == bool(on):
        return fmt
    other = info[1]
    if not other:
        raise TexError("%s 没有 %s 变体" % (fmt_name(fmt), "sRGB" if on else "线性"))
    return other


def _block_info(fmt):
    info = _FMT_INFO.get(fmt)
    if not info:
        raise TexError("不支持的 DXGI 格式：%d（只支持 BC / RGBA8 / R8 / R8G8 / A8）" % fmt)
    return info[2]


def get_pitch(fmt, width):
    kind, n = _block_info(fmt)
    if kind == "bc":
        return max(1, (width + 3) // 4) * n
    return width * n


def get_image_size(fmt, width, height):
    kind, n = _block_info(fmt)
    if kind == "bc":
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * n
    return width * height * n


def full_mip_count(width, height):
    n = 1
    while width > 1 or height > 1:
        width = max(1, width >> 1)
        height = max(1, height >> 1)
        n += 1
    return n


# ---------------------------------------------------------------- DDS 读取

def parse_dds(data):
    """解析 DX10-header 的 2D DDS → dict（不依赖路径，方便测试）。

    返回 ``{width, height, mip_count, dxgi_format, mips, is_srgb, trailing}``。
    """
    if len(data) < 4 + _DDS_HEADER.size + _DX10.size:
        raise TexError("DDS 文件太小，不是合法 DDS")
    if struct.unpack_from("<I", data, 0)[0] != DDS_MAGIC:
        raise TexError("不是 DDS 文件（缺少 'DDS '）")
    (size, _flags, height, width, _pitch_or_linear, depth, mip_map_count,
     _res1, pixel_format, _caps1, _caps2, _caps3, _caps4, _res2) = \
        _DDS_HEADER.unpack_from(data, 4)
    pf_size, _pf_flags, pf_fourcc, _pf_rgb, _r, _g, _b, _a = struct.unpack("<8I", pixel_format)
    if pf_fourcc != DX10_FOURCC:
        raise TexError("只支持带 DX10 头的 DDS（请用 texconv / 新版导出工具；"
                       "旧 DXT1/DXT5 格式请先转成 DX10）")
    off = 4 + _DDS_HEADER.size
    dxgi_fmt, dim, misc, array_size, _misc2 = _DX10.unpack_from(data, off)
    off += _DX10.size

    if dim != _DDS_DIMENSION_TEXTURE2D:
        raise TexError("只支持 2D 纹理（resourceDimension=%d）" % dim)
    if array_size != 1:
        raise TexError("不支持纹理数组（arraySize=%d）" % array_size)
    if misc & _DDS_MISC_TEXTURECUBE:
        raise TexError("不支持立方体贴图")
    if depth not in (0, 1):
        raise TexError("不支持 3D 纹理（depth=%d）" % depth)
    if width < 1 or height < 1:
        raise TexError("DDS 宽高非法：%dx%d" % (width, height))
    if dxgi_fmt not in _FMT_INFO:
        raise TexError("不支持的 DXGI 格式：%s" % fmt_label(dxgi_fmt))

    mip_count = max(1, mip_map_count)
    full = full_mip_count(width, height)
    if mip_count > full:
        raise TexError("mip 数量超过完整链（%d > %d）" % (mip_count, full))

    mips = []
    for i in range(mip_count):
        w = max(1, width >> i)
        h = max(1, height >> i)
        n = get_image_size(dxgi_fmt, w, h)
        if off + n > len(data):
            raise TexError("DDS 数据不完整（mip %d 需要 %d 字节）" % (i, n))
        mips.append(data[off:off + n])
        off += n
    return {
        "width": width,
        "height": height,
        "mip_count": mip_count,
        "dxgi_format": dxgi_fmt,
        "mips": mips,
        "is_srgb": is_srgb(dxgi_fmt),
        "trailing": len(data) - off,
    }


def read_dds(path):
    with open(path, "rb") as f:
        return parse_dds(f.read())


def validate_dds(dds):
    """返回 ``(errors, warnings)``；errors 非空时不应继续导入。"""
    errs, warns = [], []
    fmt = dds["dxgi_format"]
    w, h = dds["width"], dds["height"]
    kind, _n = _block_info(fmt)
    if kind == "bc":
        if w < 4 or h < 4:
            errs.append("BC 压缩贴图宽高必须 ≥4（当前 %dx%d；1x1 之类会让 DX12 建资源失败）"
                        % (w, h))
        elif w % 4 or h % 4:
            errs.append("BC 压缩贴图宽高必须是 4 的倍数（当前 %dx%d）" % (w, h))
    full = full_mip_count(w, h)
    if dds["mip_count"] < full:
        warns.append("mip 链不完整（当前 %d 级，完整需要 %d 级）；远处采样可能模糊"
                     % (dds["mip_count"], full))
    if (w & (w - 1)) or (h & (h - 1)):
        warns.append("宽高不是 2 的幂（DMC5 vanilla 基本都是 POT，mip/压缩质量可能受影响）")
    if fmt in _UNVERIFIED_DMC5:
        warns.append("%s 未在 DMC5 vanilla 中实测，引擎可能不支持" % fmt_name(fmt))
    return errs, warns


# ---------------------------------------------------------------- .tex 写出

def to_tex_bytes(dds, version=DMC5_TEX_VERSION, srgb=None):
    """把 ``parse_dds`` 的结果打包成 Legacy .tex 字节。

    ``srgb``：None = 保持源格式；True/False = 切到同族 sRGB/线性变体
    （只改 DXGI 数值，不动像素数据）。
    """
    fmt = dds["dxgi_format"]
    if srgb is not None:
        fmt = with_srgb(fmt, srgb)
    width, height = dds["width"], dds["height"]
    mips = dds["mips"]
    mip_count = len(mips)
    data_start = _HEADER.size + mip_count * _MIP.size

    header = _HEADER.pack(
        TEX_MAGIC, int(version), width, height, 1,      # depth = 1（DX12 不能为 0）
        mip_count, 1, fmt,                              # mipCount, imageCount = 1
        -1, 0, 0,                                       # swizzle=-1, cubemap=0, flags=0
    )
    body = bytearray()
    records = []
    for i, mip in enumerate(mips):
        w = max(1, width >> i)
        pitch = get_pitch(fmt, w)
        need = get_image_size(fmt, w, max(1, height >> i))
        if len(mip) != need:
            raise TexError("mip %d 大小不对（%d ≠ %d）" % (i, len(mip), need))
        records.append((data_start + len(body), pitch, len(mip)))
        body += mip

    table = b"".join(_MIP.pack(off, pitch, size) for off, pitch, size in records)
    return header + table + bytes(body)


def write_tex_from_dds(dds_path, out_path, version=DMC5_TEX_VERSION, srgb=None):
    data = to_tex_bytes(read_dds(dds_path), version=version, srgb=srgb)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


# ---------------------------------------------------------------- .tex 读取（测试/以后导出）

def parse_tex(data):
    """解析 Legacy .tex → 与 ``parse_dds`` 同形（只支持单 image、2D）。"""
    if len(data) < _HEADER.size:
        raise TexError(".tex 文件太小")
    (magic, version, width, height, depth, mip_count, image_count, fmt,
     swizzle, cube, flags) = _HEADER.unpack_from(data, 0)
    if magic != TEX_MAGIC:
        raise TexError("不是 .tex 文件（magic=%08X）" % magic)
    if version > LEGACY_MAX_VERSION:
        raise TexError("暂不支持 Modern .tex（version=%d）" % version)
    if image_count != 1 or cube:
        raise TexError("只支持单 image、非立方体的 .tex（imageCount=%d cube=%d）"
                       % (image_count, cube))
    if fmt not in _FMT_INFO:
        raise TexError("不支持的 .tex 格式：%s" % fmt_label(fmt))
    mips = []
    for i in range(mip_count):
        off, _pitch, size = _MIP.unpack_from(data, _HEADER.size + i * _MIP.size)
        if off + size > len(data):
            raise TexError(".tex mip %d 越界" % i)
        mips.append(data[off:off + size])
    return {
        "width": width,
        "height": height,
        "mip_count": mip_count,
        "dxgi_format": fmt,
        "mips": mips,
        "is_srgb": is_srgb(fmt),
        "version": version,
        "depth": depth,
        "swizzle": swizzle,
        "cube": cube,
        "flags": flags,
    }


def read_tex(path):
    with open(path, "rb") as f:
        return parse_tex(f.read())


# ---------------------------------------------------------------- DDS 写出（测试/以后导出）

def to_dds_bytes(dds):
    """把 ``parse_dds`` / ``parse_tex`` 的结果写成 DX10 DDS 字节（单 image 2D）。"""
    width, height = dds["width"], dds["height"]
    mips = dds["mips"]
    fmt = dds["dxgi_format"]
    caps = _DDSCAPS_TEXTURE
    if len(mips) > 1:
        caps |= _DDSCAPS_COMPLEX | _DDSCAPS_MIPMAP
    pixel_format = struct.pack("<8I", 32, _DDPF_FOURCC, DX10_FOURCC, 0, 0, 0, 0, 0)
    header = _DDS_HEADER.pack(
        124, _DDSD_FLAGS, height, width, 0, 0, len(mips),
        b"\x00" * 44, pixel_format, caps, 0, 0, 0, 0,
    )
    dx10 = _DX10.pack(fmt, _DDS_DIMENSION_TEXTURE2D, 0, 1, 0)
    return (struct.pack("<I", DDS_MAGIC) + header + dx10 + b"".join(mips))


def write_dds(path, dds):
    with open(path, "wb") as f:
        f.write(to_dds_bytes(dds))
    return path
