"""texconv.dll 封装：PNG → DX10 DDS → .tex（需要 material_toolkit/bin/texconv.dll）。"""
import os
import shutil
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from material_toolkit.lib import tex_file as texlib, texconv


def make_png(path, w, h, rgba=(128, 64, 200, 255)):
    """写一个 RGBA8 PNG（无滤波/无交错），不依赖 PIL。"""
    raw = b""
    row = bytes(rgba) * w
    for _ in range(h):
        raw += b"\x00" + row

    def chunk(t, d):
        return (struct.pack(">I", len(d)) + t + d +
                struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 9)) +
           chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main():
    if not texconv.available():
        print("test_texconv: SKIP（bin/texconv.dll 不存在）")
        return
    root = tempfile.mkdtemp(prefix="rt_texconv_")
    try:
        p4 = os.path.join(root, "src4.png")
        make_png(p4, 4, 4)

        dds_path = texconv.convert_to_dds(p4, "BC7_UNORM", root, mips=True)
        info = texlib.read_dds(dds_path)
        print("BC7:", info["width"], info["height"], "mip", info["mip_count"],
              "fmt", info["dxgi_format"])
        assert info["width"] == 4 and info["height"] == 4
        assert info["mip_count"] == 3, info["mip_count"]
        assert info["dxgi_format"] == 98
        t = texlib.to_tex_bytes(info)
        assert len(t) == 128, len(t)          # 与 vanilla DefaultNormalAOMap 一致
        ti = texlib.parse_tex(t)
        assert ti["version"] == 11 and ti["depth"] == 1
        assert ti["swizzle"] == -1 and ti["flags"] == 0 and ti["cube"] == 0

        # sRGB：只改 DXGI 数值，目标 99
        dds_s = texconv.convert_to_dds(p4, "BC7_UNORM_SRGB", root)
        assert texlib.read_dds(dds_s)["dxgi_format"] == 99

        # 不生成 mip
        dds_1 = texconv.convert_to_dds(p4, "BC7_UNORM", root, mips=False)
        assert texlib.read_dds(dds_1)["mip_count"] == 1

        # 非 4 倍数：texconv 能生成，但我们的校验要拒绝
        p6 = os.path.join(root, "src6.png")
        make_png(p6, 6, 6)
        dds_6 = texconv.convert_to_dds(p6, "BC7_UNORM", root)
        errs, _ = texlib.validate_dds(texlib.read_dds(dds_6))
        assert errs, errs

        # BC1（带完整 mip）
        p16 = os.path.join(root, "src16.png")
        make_png(p16, 16, 16)
        dds_1n = texconv.convert_to_dds(p16, "BC1_UNORM", root)
        i16 = texlib.read_dds(dds_1n)
        assert i16["dxgi_format"] == 71 and i16["mip_count"] == 5
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("test_texconv: OK")


if __name__ == "__main__":
    main()
