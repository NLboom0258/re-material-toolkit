"""DDS → .tex（Legacy v11）：结构、校验、sRGB 切换、round-trip、导入命名。"""
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from material_toolkit.lib import psession as ps, tex_file as texlib


def mk(w, h, mip_count, fmt, fill=0x5A):
    """构造一个“已解析”的 DDS（mips 用固定字节填充）。"""
    mips = []
    for i in range(mip_count):
        ww = max(1, w >> i)
        hh = max(1, h >> i)
        mips.append(bytes([fill]) * texlib.get_image_size(fmt, ww, hh))
    return {"width": w, "height": h, "mip_count": mip_count,
            "dxgi_format": fmt, "mips": mips,
            "is_srgb": texlib.is_srgb(fmt), "trailing": 0}


def main():
    # DDS round-trip
    d7 = mk(4, 4, 3, 98)
    dds = texlib.parse_dds(texlib.to_dds_bytes(d7))
    assert dds["width"] == 4 and dds["height"] == 4 and dds["mip_count"] == 3
    assert dds["dxgi_format"] == 98 and dds["mips"] == d7["mips"]

    # tex header / mip 表 / 数据
    t = texlib.to_tex_bytes(d7)
    info = texlib.parse_tex(t)
    assert info["version"] == 11 and info["depth"] == 1
    assert info["swizzle"] == -1 and info["cube"] == 0 and info["flags"] == 0
    assert info["dxgi_format"] == 98 and info["mips"] == d7["mips"]
    off, pitch, size = struct.unpack_from("<qii", t, 32)
    assert (off, pitch, size) == (80, 16, 16), (off, pitch, size)

    # 再写回 DDS，mip 数据仍一致
    again = texlib.parse_dds(texlib.to_dds_bytes(info))
    assert again["mips"] == d7["mips"]

    # sRGB 切换（只改 DXGI 数值）
    assert texlib.with_srgb(98, True) == 99
    assert texlib.with_srgb(99, False) == 98
    assert texlib.parse_tex(texlib.to_tex_bytes(d7, srgb=True))["dxgi_format"] == 99
    try:
        texlib.with_srgb(83, True)      # BC5 没有 sRGB 变体
        raise AssertionError("BC5 不应有 sRGB 变体")
    except texlib.TexError:
        pass

    # 校验：尺寸 / mip / sRGB 变体
    errs, _ = texlib.validate_dds(mk(2, 2, 1, 98))
    assert errs, errs
    errs, warns = texlib.validate_dds(mk(8, 8, 1, 98))
    assert not errs and any("mip" in w for w in warns), (errs, warns)
    errs, warns = texlib.validate_dds(mk(8, 8, 4, 98))
    assert not errs and not any("mip" in w for w in warns), (errs, warns)
    errs, _ = texlib.validate_dds(mk(6, 6, 1, 28))     # RGBA8 不要求 4 倍数
    assert not errs, errs

    # cubemap 拒绝（DX10 miscFlag 在 4+124+8）
    raw = bytearray(texlib.to_dds_bytes(mk(4, 4, 1, 98)))
    struct.pack_into("<I", raw, 4 + 124 + 8, 0x4)
    try:
        texlib.parse_dds(bytes(raw))
        raise AssertionError("cubemap 应当拒绝")
    except texlib.TexError:
        pass

    # 截断 mip 数据 → 拒绝
    raw = texlib.to_dds_bytes(mk(4, 4, 3, 98))[:-8]
    try:
        texlib.parse_dds(raw)
        raise AssertionError("截断应当拒绝")
    except texlib.TexError:
        pass

    # import_texture_bytes：命名 / rev / 版本后缀
    root = os.path.join(tempfile.gettempdir(), "rt_tex_file")
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(os.path.join(root, "natives", "x64", "materialTemp"))
    rel1 = ps.import_texture_bytes(root, t, "mh", "dh", "MyTex", suffix=".11")
    assert rel1.endswith("/MyTex_v1.tex"), rel1
    p1 = os.path.join(root, "natives", "x64", rel1.replace("/", os.sep)) + ".11"
    assert os.path.exists(p1) and open(p1, "rb").read() == t
    rel2 = ps.import_texture_bytes(root, t, "mh", "dh", "MyTex", suffix=".11")
    assert rel2.endswith("/MyTex_v2.tex"), rel2
    try:
        ps.import_texture_bytes(root, t, "mh", "dh", "贴图", suffix=".11")
        raise AssertionError("非 ASCII 名应当拒绝")
    except ValueError:
        pass

    shutil.rmtree(root)
    print("test_tex_file: OK")


if __name__ == "__main__":
    main()
