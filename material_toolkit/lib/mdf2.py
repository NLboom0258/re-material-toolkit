#!/usr/bin/env python3
"""mdf2(RE Engine 材质定义文件)读写库。

结构(version 10 = DMC5;参考 MDF-Manager/RE-Mesh-Editor 交叉验证):
  头部 16B: 'MDF\\0' + u16 version + u16 materialCount + u64 materialFlags
  材质记录 64B/条(version<19):
    MatNameOffset(8) | NameHash(4) | PropBlockSize(4) | PropertyCount(4) |
    TextureCount(4) | ShaderType(4) | Flags(4) |
    PropHeadersOff(8) | TexHeadersOff(8) | PropDataOff(8) | MMTRPathOff(8)
  贴图条目 24B/条(version<13): TypeOff(8)|UTF16Hash(4)|ASCIIHash(4)|PathOff(8)
  参数条目 24B/条(version<13): NameOff(8)|UTF16Hash(4)|ASCIIHash(4)|ParamCount(4)|DataOff(4)
  参数值区: 每材质 PropBlockSize 字节(参数值按 DataOff 定位)
  字符串表: UTF-16LE, 每串以 00 00 结尾
"""
import struct
try:
    from .hashes import ascii_hash, utf16_hash
except ImportError:  # 直接运行本文件时
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hashes import ascii_hash, utf16_hash


def _u16(b, o): return struct.unpack_from("<H", b, o)[0]
def _u32(b, o): return struct.unpack_from("<I", b, o)[0]
def _u64(b, o): return struct.unpack_from("<Q", b, o)[0]
def _i32(b, o): return struct.unpack_from("<i", b, o)[0]
def _f32(b, o): return struct.unpack_from("<f", b, o)[0]


def read_utf16(data: bytes, off: int) -> str:
    end = off
    while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
        end += 2
    return data[off:end].decode("utf-16-le", errors="replace")


def guess_game_version(path: str):
    """从文件名后缀推断游戏版本,如 xxx.mdf2.10 -> 10。"""
    import os, re
    base = os.path.basename(path)
    mt = re.search(r"\.mdf2\.(\d+)$", base)
    return int(mt.group(1)) if mt else None


class TextureBinding:
    def __init__(self):
        self.type_offset = 0
        self.utf16_hash = 0
        self.ascii_hash = 0
        self.path_offset = 0
        self.texture_type = ""
        self.texture_path = ""

    def __repr__(self):
        return f"Texture({self.texture_type!r} -> {self.texture_path!r})"


class Property:
    def __init__(self):
        self.name_offset = 0
        self.utf16_hash = 0
        self.ascii_hash = 0
        self.param_count = 0
        self.data_offset = 0
        self.name = ""
        self.values = []
        self.cb_offset = None   # 若不为 None,值区按此(cbuffer 内偏移)定位,而非顺序累加

    def __repr__(self):
        return f"Prop({self.name!r} n={self.param_count} off=0x{self.data_offset:x} {self.values})"


class Material:
    def __init__(self):
        self.name_offset = 0
        self.name_hash = 0
        self.prop_block_size = 0
        self.property_count = 0
        self.texture_count = 0
        self.shader_type = 0
        self.flags = 0
        self.prop_headers_offset = 0
        self.tex_headers_offset = 0
        self.prop_data_offset = 0
        self.mmtr_path_offset = 0
        self.name = ""
        self.mmtr_path = ""
        self.textures = []
        self.properties = []

    def __repr__(self):
        return (f"Material({self.name!r} mmtr={self.mmtr_path!r} "
                f"tex={len(self.textures)} prop={len(self.properties)} block={self.prop_block_size})")


class Mdf2:
    """mdf2 只读解析(version 10 优先,其他版本部分支持)。"""

    def __init__(self):
        self.file_version = 0   # 文件头 u16(恒为 1)
        self.game_version = 0   # 游戏/资产版本(来自文件名后缀,决定记录尺寸)
        self.material_flags = 0
        self.materials = []

    @classmethod
    def load(cls, path: str, game_version: int = None) -> "Mdf2":
        data = open(path, "rb").read()
        if data[:4] != b"MDF\0":
            raise ValueError("not an MDF file")
        m = cls()
        m.file_version = _u16(data, 4)
        count = _u16(data, 6)
        m.material_flags = _u64(data, 8)
        if game_version is None:
            game_version = guess_game_version(path)
        if game_version is None:
            raise ValueError("无法确定游戏版本,请显式传入 game_version")
        m.game_version = game_version
        ver = game_version

        # 材质记录连续排列,先记录每条记录的字段,再按偏移读取字符串/子表
        pos = 16
        for _ in range(count):
            mat = Material()
            mat.name_offset = _u64(data, pos); pos += 8
            mat.name_hash = _u32(data, pos); pos += 4
            mat.prop_block_size = _i32(data, pos); pos += 4
            mat.property_count = _i32(data, pos); pos += 4
            mat.texture_count = _i32(data, pos); pos += 4
            if ver >= 19:
                pos += 8  # GPBFBufferNameCount + GPBFBufferPathCount
            if ver >= 31:
                pos += 4  # bakeTextureArraySize
            mat.shader_type = _i32(data, pos); pos += 4
            if ver >= 31:
                pos += 12  # flags + flagsB + shaderLODNum
            else:
                mat.flags = _i32(data, pos); pos += 4
            if ver >= 51:
                pos += 8  # ver51UnknOffset
            mat.prop_headers_offset = _u64(data, pos); pos += 8
            mat.tex_headers_offset = _u64(data, pos); pos += 8
            if ver >= 19:
                pos += 8  # GPUBufferOffset
            mat.prop_data_offset = _u64(data, pos); pos += 8
            mat.mmtr_path_offset = _u64(data, pos); pos += 8
            if ver >= 31:
                pos += 8  # mmtrsDataOffset
            m.materials.append(mat)

        # 逐材质读取名字/mmtr/贴图/参数
        for mat in m.materials:
            mat.name = read_utf16(data, mat.name_offset)
            mat.mmtr_path = read_utf16(data, mat.mmtr_path_offset)
            # 贴图
            p = mat.tex_headers_offset
            for _ in range(mat.texture_count):
                tb = TextureBinding()
                tb.type_offset = _u64(data, p); p += 8
                tb.utf16_hash = _u32(data, p); p += 4
                tb.ascii_hash = _u32(data, p); p += 4
                tb.path_offset = _u64(data, p); p += 8
                if ver >= 13:
                    p += 8
                tb.texture_type = read_utf16(data, tb.type_offset)
                tb.texture_path = read_utf16(data, tb.path_offset)
                mat.textures.append(tb)
            # 参数
            p = mat.prop_headers_offset
            for _ in range(mat.property_count):
                pr = Property()
                pr.name_offset = _u64(data, p); p += 8
                pr.utf16_hash = _u32(data, p); p += 4
                pr.ascii_hash = _u32(data, p); p += 4
                if ver >= 13:
                    pr.data_offset = _i32(data, p); p += 4
                    pr.param_count = _u16(data, p); p += 2
                    p += 2  # unknFlag
                else:
                    pr.param_count = _i32(data, p); p += 4
                    pr.data_offset = _i32(data, p); p += 4
                pr.name = read_utf16(data, pr.name_offset)
                vbase = mat.prop_data_offset + pr.data_offset
                pr.values = [_f32(data, vbase + i * 4) for i in range(pr.param_count)]
                mat.properties.append(pr)
        return m

    def _collect_strings(self):
        """收集所有字符串(去重,保持首次出现顺序)并回填各对象的偏移索引。"""
        strings, index = [], {}

        def intern(s):
            if s not in index:
                index[s] = len(strings)
                strings.append(s)
            return index[s]

        for mat in self.materials:
            mat.name_offset = intern(mat.name)
            mat.mmtr_path_offset = intern(mat.mmtr_path)
            for tb in mat.textures:
                tb.type_offset = intern(tb.texture_type)
                tb.path_offset = intern(tb.texture_path)
            for pr in mat.properties:
                pr.name_offset = intern(pr.name)
        return strings

    @staticmethod
    def _align16(x):
        return (x + 15) & ~15

    def add_property(self, material_name, prop_name, values, cb_offset=None):
        """给指定材质新增一个参数条目(值)。cb_offset 为该参数在 cbuffer 内的字节偏移。"""
        for mat in self.materials:
            if mat.name == material_name:
                pr = Property()
                pr.name = prop_name
                pr.values = list(values)
                pr.param_count = len(pr.values)
                pr.cb_offset = cb_offset
                mat.properties.append(pr)
                return pr
        raise ValueError(f"material {material_name!r} not found")

    def save(self, path: str):
        """写入 mdf2(重排所有偏移 + 重算 hash)。version<19 的布局。"""
        ver = self.game_version
        if ver >= 19:
            raise NotImplementedError("当前仅支持 version<19(DMC5/RE2/RE3)写入")
        strings = self._collect_strings()

        # 字符串表(UTF-16LE + 00 00)
        str_table = bytearray()
        str_off = []
        for s in strings:
            str_off.append(len(str_table))
            str_table += s.encode("utf-16-le") + b"\x00\x00"
        for mat in self.materials:
            mat.name_offset = str_off[mat.name_offset]
            mat.mmtr_path_offset = str_off[mat.mmtr_path_offset]
            for tb in mat.textures:
                tb.type_offset = str_off[tb.type_offset]
                tb.path_offset = str_off[tb.path_offset]
            for pr in mat.properties:
                pr.name_offset = str_off[pr.name_offset]

        # 分区尺寸与偏移
        n_mat = len(self.materials)
        mat_rec_size = 64
        tex_rec_size = 24
        prop_rec_size = 24
        mat_off = 16
        tex_off = self._align16(mat_off + n_mat * mat_rec_size)
        n_tex = sum(len(m.textures) for m in self.materials)
        prop_off = self._align16(tex_off + n_tex * tex_rec_size)
        n_prop = sum(len(m.properties) for m in self.materials)
        str_off_base = self._align16(prop_off + n_prop * prop_rec_size)
        data_off_base = self._align16(str_off_base + len(str_table))

        # 字符串偏移 = 字符串表文件偏移 + 表内偏移
        for mat in self.materials:
            mat.name_offset = str_off_base + mat.name_offset
            mat.mmtr_path_offset = str_off_base + mat.mmtr_path_offset
            for tb in mat.textures:
                tb.type_offset = str_off_base + tb.type_offset
                tb.path_offset = str_off_base + tb.path_offset
            for pr in mat.properties:
                pr.name_offset = str_off_base + pr.name_offset

        # 逐材质计算 propData 与各子表偏移
        cur_prop_data = 0
        for mat in self.materials:
            mat.prop_data_offset = data_off_base + cur_prop_data
            off = 0
            for pr in mat.properties:
                if pr.cb_offset is not None:
                    pr.data_offset = pr.cb_offset
                    off = max(off, pr.cb_offset + pr.param_count * 4)
                else:
                    pr.data_offset = off
                    off += pr.param_count * 4
            mat.prop_block_size = off
            cur_prop_data += off

        # 组装
        out = bytearray()
        out += b"MDF\0" + struct.pack("<HHQ", 1, n_mat, self.material_flags)
        # 材质记录
        mat_recs = bytearray()
        cur_tex_off, cur_prop_off = tex_off, prop_off
        for mat in self.materials:
            mat.name_hash = utf16_hash(mat.name)
            mat.texture_count = len(mat.textures)
            mat.property_count = len(mat.properties)
            mat.tex_headers_offset = cur_tex_off
            mat.prop_headers_offset = cur_prop_off
            cur_tex_off += mat.texture_count * tex_rec_size
            cur_prop_off += mat.property_count * prop_rec_size
            mat_recs += struct.pack(
                "<QIiiiiIQQQQ",
                mat.name_offset, mat.name_hash & 0xFFFFFFFF, mat.prop_block_size,
                mat.property_count, mat.texture_count, mat.shader_type,
                mat.flags & 0xFFFFFFFF,
                mat.prop_headers_offset, mat.tex_headers_offset,
                mat.prop_data_offset, mat.mmtr_path_offset)
        out += mat_recs
        # 贴图条目
        out += b"\x00" * (tex_off - len(out))
        for mat in self.materials:
            for tb in mat.textures:
                tb.utf16_hash = utf16_hash(tb.texture_type)
                tb.ascii_hash = ascii_hash(tb.texture_type)
                out += struct.pack("<QIIQ", tb.type_offset, tb.utf16_hash,
                                   tb.ascii_hash, tb.path_offset)
        # 参数条目
        out += b"\x00" * (prop_off - len(out))
        for mat in self.materials:
            for pr in mat.properties:
                pr.utf16_hash = utf16_hash(pr.name)
                pr.ascii_hash = ascii_hash(pr.name)
                pr.param_count = len(pr.values)
                out += struct.pack("<QIIii", pr.name_offset, pr.utf16_hash,
                                   pr.ascii_hash, pr.param_count, pr.data_offset)
        # 字符串表
        out += b"\x00" * (str_off_base - len(out))
        out += str_table
        # 参数值区(按 data_offset 定位写入,支持 cb_offset 对齐)
        out += b"\x00" * (data_off_base - len(out))
        prop_data = bytearray(cur_prop_data)
        for mat in self.materials:
            base = mat.prop_data_offset - data_off_base
            for pr in mat.properties:
                off = base + pr.data_offset
                for i, v in enumerate(pr.values):
                    struct.pack_into("<f", prop_data, off + i * 4, v)
        out += prop_data
        open(path, "wb").write(out)
        return len(out)

    def dump(self):
        print(f"Mdf2 game_version={self.game_version} file_version={self.file_version} "
              f"materials={len(self.materials)}")
        for mat in self.materials:
            print(f"  {mat.name}  mmtr={mat.mmtr_path}  shaderType={mat.shader_type} "
                  f"flags=0x{mat.flags & 0xFFFFFFFF:08x}")
            for tb in mat.textures:
                print(f"      tex {tb.texture_type} -> {tb.texture_path}")
            for pr in mat.properties:
                print(f"      prop {pr.name} = {pr.values}")


if __name__ == "__main__":
    import sys
    Mdf2.load(sys.argv[1]).dump()
