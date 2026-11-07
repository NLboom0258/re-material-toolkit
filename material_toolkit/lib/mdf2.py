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
def _i64(b, o): return struct.unpack_from("<q", b, o)[0]


# 参数类型表:(类型名, 字节大小)。mdf2 本身不存"类型", 只存"值的个数";
# 类型 = 个数推导(与 MDF-Manager / RE-Mesh-Editor 口径一致)。
PARAM_TYPES = [("float", 4), ("float2", 8), ("float3", 12), ("float4", 16),
               ("float4x3", 48), ("float4x4", 64)]
PARAM_TYPE_BYTES = dict(PARAM_TYPES)


def param_type_name(nbytes: int) -> str:
    """字节大小 -> 类型名(与 mmtr_info.type_label 口径一致)。"""
    n = nbytes // 4
    if n >= 4 and nbytes % 16 == 0:
        return "float4" if n == 4 else f"float4x{n // 4}"
    return {1: "float", 2: "float2", 3: "float3"}.get(n, f"{n}*float")


def param_type_float_count(type_name: str) -> int:
    """类型名 -> float 个数(float 个数 x4 = 字节大小)。未知名抛错。"""
    if type_name not in PARAM_TYPE_BYTES:
        raise ValueError(f"unknown param type {type_name!r} "
                         f"(可选: {', '.join(n for n, _ in PARAM_TYPES)})")
    return PARAM_TYPE_BYTES[type_name] // 4


def d3d_cbuffer_next(cur_off: int, size: int) -> int:
    """d3dcompiler 的 cbuffer 打包: 从 cur_off 起放一个 size 字节成员。

    规则: 若该成员会跨 16 字节边界(即 cur_off%16 + size > 16), 则对齐到下一个 16 边界。
    (与 material_iface.next_offset 同口径 => mdf2 值区 == shader 的 cbuffer 布局。)
    """
    if (cur_off % 16) + size > 16:
        cur_off = (cur_off + 15) & ~15
    return cur_off


def layout_prop_offsets(properties) -> int:
    """按 d3dcompiler cbuffer 打包规则计算各属性 data_offset(原地写回), 返回块大小。

    - `cb_offset` 非 None: 用其值(权威偏移, 通常来自 mmtr/shader, 已是对齐布局);
    - `cb_offset` 为 None: 从上一成员末尾按 d3dcompiler 规则续排(不跨 16 字节行)。

    引擎按 mmtr/shader 的 cbuffer 偏移去 mdf2 值区取值, 故两者必须一致;
    若按"顺序紧排"(不补对齐), float3/矩阵等会被读错位。
    """
    off = 0
    for pr in properties:
        pr.param_count = len(pr.values)
        size = pr.param_count * 4
        if pr.cb_offset is not None:
            pr.data_offset = pr.cb_offset
            off = max(off, pr.cb_offset + size)
        else:
            off = d3d_cbuffer_next(off, size)
            pr.data_offset = off
            off += size
    return off


class ParamWriteError(ValueError):
    """参数值写回失败（参数名/值个数与 mdf2 不匹配）。"""


class PatchError(ValueError):
    """只改值写回失败（结构不匹配 / 偏移越界 / 模型里出现了结构改动）。"""


def patch_param_floats(data: bytearray, mat, changes: dict, strict=True):
    """把 {参数名: [float...]} **原地**写进 data 里 mat 的值区。

    只覆盖 float 字节，不动参数头 / 字符串池 / 其他材质 —— 因为引擎只在加载时
    读 mdf2 的结构，值区改了多少字节都不影响布局。

    值的个数必须与 mdf2 里 ``param_count`` 一致：mdf2 不存类型，个数变了等于
    改类型，会打乱后面所有参数的 data_offset（需要完整 ``save()`` 重排）。

    返回 [(参数名, 绝对偏移, 旧值, 新值)]。
    """
    out = []
    for name, vals in (changes or {}).items():
        pr = mat.get_parameter(name)
        if pr is None:
            if strict:
                raise ParamWriteError(
                    "材质 %r 没有参数 %r（现有: %s）"
                    % (mat.name, name, ", ".join(p.name for p in mat.properties)))
            continue
        vals = [float(v) for v in vals]
        if len(vals) != pr.param_count:
            raise ParamWriteError(
                "参数 %r 在 mdf2 里是 %d 个 float（%s），传入 %d 个。"
                "改类型必须走完整 save() 重排，不能原地覆盖"
                % (name, pr.param_count, pr.type, len(vals)))
        base = mat.prop_data_offset + pr.data_offset
        old = list(pr.values)
        for i, v in enumerate(vals):
            struct.pack_into("<f", data, base + i * 4, v)
        pr.values = vals
        out.append((name, base, old, vals))
    return out


# 材质着色类型(MaterialShadingType; 权威 = 逆向 DMC5 exe 的 via.render.MaterialShadingType)。
# 值取自 exe 的枚举注册代码(RVA 0x2C5EEC0..0x2C5F07A: 每个成员 = `mov r8d, <值>` +
# `lea rdx, <名字>`) —— 共 12 项, GUI 在 8。
# ⚠ 早期版本漏了 GUI: 名字池 0x460AE80 里没有独立的 "GUI" 字面量(被链接器合并到
#   0x45F2B3C), 只按池内顺序推断就会从 8 起整体错位一格(旧表: 8=GUIMesh /
#   9=ExpensiveTransparent / 10=Forward) ⇒ DMC5 的 `*TransparentEX` master(眼睛/头发)
#   在 mdf2 里写的 10 是 **ExpensiveTransparent**; 11(Forward) 没有任何 mdf2 使用。
# 社区工具(MDF-Manager)的 `ShadingType` 是跨游戏并集(Water=8/SpeedTree=9/GUI=10/
#   GUIMesh=11/GUIMeshTransparent=12/ExpensiveTransparent=13/Forward=14), 不能用于 DMC5。
SHADING_TYPES = [
    ("Standard", 0), ("Decal", 1), ("DecalWithMetallic", 2), ("DecalNRMR", 3),
    ("Transparent", 4), ("Distortion", 5), ("PrimitiveMesh", 6), ("Water", 7),
    ("GUI", 8), ("GUIMesh", 9), ("ExpensiveTransparent", 10), ("Forward", 11),
]
SHADING_TYPE_VALUES = dict(SHADING_TYPES)

# MHWS(1808168797 对应的 mdf2.45)：41 项。值取自 REasy 的 mhws_enums.json
# (resources/data/enums/via.render.MaterialShadingType)，并用解包语料的分布核对过
# 常见值：0 Standard / 1 Decal / 6 Transparent / 16 ExpensiveTransparent /
# 17 Forward / 25 ShellFurMaterial（角色的毛皮材质）。未逐项逆向 exe。
_MHWS_SHADING_TYPES = [
    ("Standard", 0), ("Decal", 1), ("DecalStencil", 2), ("SeparateAlphaDecal", 3),
    ("DecalWithMetallic", 4), ("DecalNRMR", 5), ("Transparent", 6),
    ("TransparentStencil", 7), ("Distortion", 8), ("PrimitiveMesh", 9),
    ("PrimitiveSolidMesh", 10), ("Water", 11), ("SpeedTree", 12), ("GUI", 13),
    ("GUIMesh", 14), ("GUIMeshTransparent", 15), ("ExpensiveTransparent", 16),
    ("Forward", 17), ("RenderTarget", 18), ("PostProcess", 19),
    ("PrimitiveMaterial", 20), ("PrimitiveSolidMaterial", 21),
    ("PrimitiveSolidMaterialExpensive", 22), ("SpineMaterial", 23),
    ("VolumetricFog", 24), ("ShellFurMaterial", 25), ("VolumeDecal", 26),
    ("AlembicMesh", 27), ("AlembicMeshForward", 28), ("AlembicMeshTransparent", 29),
    ("MarchingCubes", 30), ("MarchingCubesForward", 31),
    ("MarchingCubesTransparent", 32), ("Strands", 33), ("Eyeball", 34),
    ("EyeballPostProcess", 35), ("NFXTransparent", 36), ("Cloudscape2", 37),
    ("CloudscapeReserved", 38), ("VolumeSolidMaterial", 39),
    ("VolumeDecalMetallic", 40),
]

SHADING_TYPE_TABLES = {"DMC5": SHADING_TYPES, "MHWS": _MHWS_SHADING_TYPES}
# mdf2 的 game_version(文件名后缀) -> 游戏名；未登记返回 ""（不猜）。
MDF_GAME_BY_VERSION = {10: "DMC5", 45: "MHWS"}


def game_for_mdf_version(version):
    return MDF_GAME_BY_VERSION.get(int(version), "")


def shading_types(game="DMC5"):
    return SHADING_TYPE_TABLES.get(game, [])


def shading_type_name(v: int, game="DMC5"):
    for n, val in shading_types(game):
        if val == v:
            return n
    return f"0x{v:x}"


def shading_type_value(name: str, game="DMC5"):
    return dict(shading_types(game)).get(name)


# 材质 flags 位布局**按 MDF 版本分**（2026-11-07 用 DMC5 语料自证）：
#   v10(DMC5) bits0-7 基础布尔 / bits8-15 TessFactor(8 位) / 16-23 PhongFactor /
#              24 ForcedAlphaTestEnable / 25 AlphaTestEnable
#   v13       TessFactor 8 位@8，bits24-26 Rough/ForcedAlpha/Alpha
#   v19       插入 Forced/TwoSide(bits8-9)，TessFactor 移到 bit10(6 位)
#   v21+      再加 bit10 TransparentZPostPassEnable，TessFactor 移到 bit11(5 位)
#   v31+      material_flags 为 8 字节，高 32 位见 FLAG_B_FIELDS
# 证据：DMC5 解包 2054 个 mdf2 / 10013 个材质中，bits8-15 非零的 82 个材质该字节恒为
# 0x2F（= 47）；按 v21 读会变成 ForcedTwoSide+TwoSide+ZPost 三个布尔 + TessFactor=5。
# 每项 (名字, 位宽): 位宽=1 为布尔；TessFactor/PhongFactor 为整数。
_BASE_FLAG_NAMES = (
    "BaseTwoSideEnable", "BaseAlphaTestEnable", "ShadowCastDisable", "VertexShaderUsed",
    "EmissiveUsed", "TessellationEnable", "EnableIgnoreDepth", "AlphaMaskUsed",
)

V10_FLAG_FIELDS = [
    *[(n, 1) for n in _BASE_FLAG_NAMES], ("TessFactor", 8), ("PhongFactor", 8),
    ("ForcedAlphaTestEnable", 1), ("AlphaTestEnable", 1),
]
V13_FLAG_FIELDS = [
    *[(n, 1) for n in _BASE_FLAG_NAMES], ("TessFactor", 8), ("PhongFactor", 8),
    ("RoughTransparentEnable", 1), ("ForcedAlphaTestEnable", 1), ("AlphaTestEnable", 1),
]
V19_FLAG_FIELDS = [
    *[(n, 1) for n in _BASE_FLAG_NAMES], ("ForcedTwoSideEnable", 1),
    ("TwoSideEnable", 1), ("TessFactor", 6), ("PhongFactor", 8),
    ("RoughTransparentEnable", 1), ("ForcedAlphaTestEnable", 1),
    ("AlphaTestEnable", 1), ("SSSProfileUsed", 1), ("EnableStencilPriority", 1),
    ("RequireDualQuaternion", 1), ("PixelDepthOffsetUsed", 1), ("NoRayTracing", 1),
]
MATERIAL_FLAG_FIELDS = [
    ("BaseTwoSideEnable", 1), ("BaseAlphaTestEnable", 1), ("ShadowCastDisable", 1),
    ("VertexShaderUsed", 1), ("EmissiveUsed", 1), ("TessellationEnable", 1),
    ("EnableIgnoreDepth", 1), ("AlphaMaskUsed", 1), ("ForcedTwoSideEnable", 1),
    ("TwoSideEnable", 1), ("TransparentZPostPassEnable", 1),
    ("TessFactor", 5), ("PhongFactor", 8),
    ("RoughTransparentEnable", 1), ("ForcedAlphaTestEnable", 1),
    ("AlphaTestEnable", 1), ("SSSProfileUsed", 1), ("EnableStencilPriority", 1),
    ("RequireDualQuaternion", 1), ("PixelDepthOffsetUsed", 1), ("NoRayTracing", 1),
]


def _flag_offsets(fields):
    out, bit = [], 0
    for name, width in fields:
        out.append((name, width, bit))
        bit += width
    return out


FLAG_BIT_OFFSETS = _flag_offsets(MATERIAL_FLAG_FIELDS)   # v21+（兼容旧调用）


def flag_fields(version=10):
    """按 mdf2 版本返回 [(名字, 位宽, 位偏移)]（未知的位不列）。"""
    if version >= 21:
        return FLAG_BIT_OFFSETS
    if version >= 19:
        return _flag_offsets(V19_FLAG_FIELDS)
    if version >= 13:
        return _flag_offsets(V13_FLAG_FIELDS)
    return _flag_offsets(V10_FLAG_FIELDS)


def decode_material_flags(v: int, version=None) -> dict:
    """u32 -> {名字: True/False 或 整数}；version=None 用 v21+ 布局（兼容旧调用）。"""
    fields = FLAG_BIT_OFFSETS if version is None else flag_fields(version)
    out = {}
    for name, width, bit in fields:
        val = (v >> bit) & ((1 << width) - 1)
        out[name] = bool(val) if width == 1 else val
    return out


def encode_material_flags(d: dict, version=None) -> int:
    """{名字: 值} -> u32（未提供的位视作 0；不在该版本布局里的名字忽略）。"""
    fields = FLAG_BIT_OFFSETS if version is None else flag_fields(version)
    out = 0
    for name, width, bit in fields:
        val = int(d.get(name, 0)) & ((1 << width) - 1)
        out |= val << bit
    return out


# mdf2 >= 31 的 material_flags 是 **8 字节**(低 32 位仍是上面的 v21 布局，高 32 位是
# 新命名的位)。名字/位取自 REasy mdf_file.py 的 _FLAG_LAYOUTS(31, ...)，并在
# MHWS mdf2.45 语料上核对过。TransparentPriorityBias 为有符号 8 位。
FLAG_B_FIELDS = [
    ("TransparentDistortionEnable", 32, 1, False),
    ("AlphaUsed", 33, 1, False),
    ("BakeTextureUseSecondaryUV", 34, 1, False),
    ("ForwardPrepassEnabled", 35, 1, False),
    ("ForcedAlphaTestEnableShadow", 36, 1, False),
    ("TessellationZPrepassDisable", 37, 1, False),
    ("DitheredLodTransitionEnable", 38, 1, False),
    ("TransparentPriorityBias", 40, 8, True),
]


def decode_flags(v: int, version: int = 10) -> dict:
    """按 mdf2 版本解 flags：<31 为 u32；>=31 为 u64(低 32 位同名，高 32 位见 FLAG_B_FIELDS)。"""
    out = decode_material_flags(v & 0xFFFFFFFF, version)
    if version >= 31:
        for name, bit, width, signed in FLAG_B_FIELDS:
            val = (int(v) >> bit) & ((1 << width) - 1)
            if signed and val >= (1 << (width - 1)):
                val -= 1 << width
            out[name] = bool(val) if width == 1 else val
    return out


def encode_flags(d: dict, version: int = 10) -> int:
    out = encode_material_flags(d, version)
    if version >= 31:
        for name, bit, width, signed in FLAG_B_FIELDS:
            val = int(d.get(name, 0)) & ((1 << width) - 1)
            out |= val << bit
    return out


def read_utf16(data: bytes, off: int) -> str:
    end = off
    while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
        end += 2
    return data[off:end].decode("utf-16-le", errors="replace")


def _read_gpbf(data: bytes, off: int) -> dict:
    """GPBF 条目（16B）：name_off q + utf16_hash I + ascii_hash I。"""
    return {"name": read_utf16(data, _u64(data, off)),
            "utf16_hash": _u32(data, off + 8),
            "ascii_hash": _u32(data, off + 12)}


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
        self.locked = 0          # mdf2 >= 13（原样保留）
        self.reserved = 0        # mdf2 >= 13 的 4 字节保留位
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
        self.orig_param_count = 0  # 加载时的分量数（补丁写回只允许改值，不允许改个数）
        self.component_locked = 0 # mdf2 >= 13：条目头高 16 位（原样保留）
        self.data_offset = 0
        self.value_offset = 0     # 值区在文件中的绝对偏移（补丁写回用，load() 填充）
        self.name = ""
        self.values = []
        self.cb_offset = None   # 若不为 None,值区按此(cbuffer 内偏移)定位,而非顺序累加

    @property
    def type(self):
        """参数类型名(由值个数推导; mdf2 不存类型)。"""
        return param_type_name(len(self.values) * 4)

    def set_type(self, type_name):
        """改类型 = 改值的个数(不足补 0、超出截断), 返回 self。"""
        n = param_type_float_count(type_name)
        if n > len(self.values):
            self.values = list(self.values) + [0.0] * (n - len(self.values))
        else:
            self.values = list(self.values[:n])
        self.param_count = len(self.values)
        return self

    def __repr__(self):
        return f"Prop({self.name!r} type={self.type} off=0x{self.data_offset:x} {self.values})"


class Material:
    def __init__(self):
        self.name_offset = 0
        self.name_hash = 0
        self.prop_block_size = 0
        self.property_count = 0
        self.texture_count = 0
        self.shader_type = 0
        self.flags = 0
        self.flags_width = 4          # mdf2 >= 31 为 8 字节
        self.shader_lod_num = 0       # mdf2 >= 31
        # ---- mdf2 >= 19 / >= 31 的额外数据块（结构写回时原样搬运） ----
        self.gpbf_name_count = 0
        self.gpbf_data_count = 0
        self.gpbf_offset = 0
        self.bake_texture_array_size = 0
        self.unk51 = 0                  # v51 的未知 8 字节（原样保留）
        self.shader_lod_redirects_offset = 0
        self.gpu_buffers = []           # [(name_hdr, data_hdr)]，每条 16B 的不透明条目
        self.shader_lod_redirects = []  # v31+ 且 shaderLODNum>0：[(counts, elems)]
        self.mdf_version = 10
        self.game = "DMC5"
        # 字段在文件中的绝对偏移（只改值写回用；load() 填充）
        self.header_offset = 0
        self.shader_type_offset = 0
        self.flags_offset = 0
        self.shader_lod_num_offset = 0
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

    # ---- 域模型: 参数 - 名字/类型/值 (offset/hash 只在编解码内部处理) ----
    def get_parameter(self, name):
        for pr in self.properties:
            if pr.name == name:
                return pr
        return None

    def add_parameter(self, name, type_name="float", values=None):
        """新增参数。values=None 时按 type_name 生成全 0 默认值。返回 Property。"""
        pr = Property()
        pr.name = name
        if values is not None:
            pr.values = [float(v) for v in values]
        else:
            pr.values = [0.0] * param_type_float_count(type_name)
        pr.param_count = len(pr.values)
        self.properties.append(pr)
        return pr

    def delete_parameter(self, name):
        """按名字删除参数, 返回是否删除。"""
        for i, pr in enumerate(self.properties):
            if pr.name == name:
                del self.properties[i]
                return True
        return False

    def rename_parameter(self, old, new):
        pr = self.get_parameter(old)
        if pr is None:
            raise ValueError(f"parameter {old!r} not found")
        pr.name = new
        return pr

    def unique_parameter_name(self, base="NewParam"):
        """生成一个不重名的占位参数名(base / base_1 / base_2 ...)。"""
        names = {pr.name for pr in self.properties}
        if base not in names:
            return base
        i = 1
        while f"{base}_{i}" in names:
            i += 1
        return f"{base}_{i}"

    # ---- 域模型: 贴图 - 名字/路径 ----
    def get_texture(self, texture_type):
        for tb in self.textures:
            if tb.texture_type == texture_type:
                return tb
        return None

    def add_texture(self, texture_type, texture_path):
        """新增贴图槽(type 名 + 路径)。返回 TextureBinding。"""
        tb = TextureBinding()
        tb.texture_type = texture_type
        tb.texture_path = texture_path
        self.textures.append(tb)
        return tb

    def rename_texture(self, old, new):
        """把贴图槽的 type 名由 old 改为 new(绑定键变化), 返回 TextureBinding。"""
        tb = self.get_texture(old)
        if tb is None:
            raise ValueError(f"texture {old!r} not found")
        tb.texture_type = new
        return tb

    def delete_texture(self, texture_type):
        """按 type 名删除贴图槽, 返回是否删除。"""
        for i, tb in enumerate(self.textures):
            if tb.texture_type == texture_type:
                del self.textures[i]
                return True
        return False

    # ---- 域模型: 材质级属性(着色类型 / flags) ----
    def shading_type_name(self, game=None):
        return shading_type_name(self.shader_type, game or self.game or "")

    def set_shading_type(self, name):
        v = shading_type_value(name, self.game or "")
        if v is None:
            raise ValueError(f"unknown shading type {name!r}")
        self.shader_type = v
        return self

    def flags_dict(self, version=None):
        """按 mdf2 版本解 flags（>=31 为 8 字节，含 flagsB 新增位）。"""
        return decode_flags(self.flags, self.mdf_version if version is None else version)

    def set_flags_dict(self, d):
        """按 {flag 名: 值} 回写 flags 位（按本材质的 mdf2 版本）。"""
        self.flags = encode_flags(d, self.mdf_version)
        return self


class Mdf2:
    """mdf2 只读解析(version 10 优先,其他版本部分支持)。"""

    def __init__(self):
        self.file_version = 0   # 文件头 u16(恒为 1)
        self.game_version = 0   # 游戏/资产版本(来自文件名后缀,决定记录尺寸)
        self.material_flags = 0
        self.materials = []

    @property
    def game(self):
        """按 game_version 推断游戏名（未登记返回 ""）。"""
        return game_for_mdf_version(self.game_version)

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
            mat.header_offset = pos
            mat.mdf_version = ver
            mat.game = game_for_mdf_version(ver)
            mat.name_offset = _u64(data, pos); pos += 8
            mat.name_hash = _u32(data, pos); pos += 4
            mat.prop_block_size = _i32(data, pos); pos += 4
            mat.property_count = _i32(data, pos); pos += 4
            mat.texture_count = _i32(data, pos); pos += 4
            if ver >= 19:
                mat.gpbf_name_count = _i32(data, pos); pos += 4
                mat.gpbf_data_count = _i32(data, pos); pos += 4
            if ver >= 31:
                mat.bake_texture_array_size = _u32(data, pos); pos += 4
            mat.shader_type = _i32(data, pos); pos += 4
            mat.shader_type_offset = pos - 4
            if ver >= 31:
                mat.flags = _u64(data, pos); mat.flags_offset = pos; pos += 8      # v31+: 8 字节(低 32 位同 v21)
                mat.flags_width = 8
                if ver >= 51:
                    mat.unk51 = _u64(data, pos); pos += 8
                mat.shader_lod_num = _u32(data, pos)
                mat.shader_lod_num_offset = pos; pos += 4
            else:
                mat.flags = _i32(data, pos); mat.flags_offset = pos; pos += 4
            mat.prop_headers_offset = _u64(data, pos); pos += 8
            mat.tex_headers_offset = _u64(data, pos); pos += 8
            if ver >= 19:
                mat.gpbf_offset = _u64(data, pos); pos += 8
            mat.prop_data_offset = _u64(data, pos); pos += 8
            mat.mmtr_path_offset = _u64(data, pos); pos += 8
            if ver >= 31:
                mat.shader_lod_redirects_offset = _u64(data, pos); pos += 8
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
                    tb.locked = _i32(data, p); p += 4
                    tb.reserved = _i32(data, p); p += 4
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
                    pr.component_locked = _u16(data, p); p += 2
                else:
                    pr.param_count = _i32(data, p); p += 4
                    pr.data_offset = _i32(data, p); p += 4
                pr.name = read_utf16(data, pr.name_offset)
                pr.orig_param_count = pr.param_count
                vbase = mat.prop_data_offset + pr.data_offset
                pr.value_offset = vbase
                pr.values = [_f32(data, vbase + i * 4) for i in range(pr.param_count)]
                mat.properties.append(pr)
            # GPBF 表（v19+）：每对 (name, data) 各 16B，名字/哈希原样保留
            if ver >= 19 and mat.gpbf_offset and mat.gpbf_name_count > 0:
                p = mat.gpbf_offset
                for _ in range(mat.gpbf_name_count):
                    nh = _read_gpbf(data, p); p += 16
                    dh = _read_gpbf(data, p); p += 16
                    mat.gpu_buffers.append((nh, dh))
            # shaderLODRedirects（v31+）：2*N 个 int64 偏移，再各自 [count][items]
            if ver >= 31 and mat.shader_lod_redirects_offset and mat.shader_lod_num > 0:
                p = mat.shader_lod_redirects_offset
                tbl = [_i64(data, p + i * 8) for i in range(mat.shader_lod_num * 2)]
                for i in range(mat.shader_lod_num):
                    counts, elems = [], []
                    co, eo = tbl[i * 2], tbl[i * 2 + 1]
                    if co:
                        c = _i32(data, co)
                        counts = [_i32(data, co + 4 + k * 4) for k in range(max(0, c))]
                    if eo:
                        c = _i32(data, eo)
                        elems = [_i32(data, eo + 4 + k * 4) for k in range(max(0, c))]
                    mat.shader_lod_redirects.append((counts, elems))
        return m

    @classmethod
    def new(cls, game_version=10, file_version=1):
        """从 0 新建一个空 mdf2(仅结构; 材料类型/flags 等默认值尚未完善)。"""
        m = cls()
        m.file_version = file_version
        m.game_version = game_version
        m.material_flags = 0
        m.materials = []
        return m

    def get_material(self, name):
        for mat in self.materials:
            if mat.name == name:
                return mat
        return None

    def add_material(self, name, mmtr_path="", shader_type=0, flags=0):
        """新增材质(名字 + mmtr路径 + shaderType/flags 原样保存)。返回 Material。"""
        mat = Material()
        mat.name = name
        mat.mmtr_path = mmtr_path
        mat.shader_type = shader_type
        mat.flags = flags
        self.materials.append(mat)
        return mat

    def delete_material(self, name):
        """按名字删除材质, 返回是否删除。"""
        for i, mat in enumerate(self.materials):
            if mat.name == name:
                del self.materials[i]
                return True
        return False

    def rename_material(self, old, new):
        mat = self.get_material(old)
        if mat is None:
            raise ValueError(f"material {old!r} not found")
        mat.name = new
        return mat

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

    def add_texture(self, material_name, texture_type, texture_path):
        """给指定材质新增一个贴图条目(类型名 + 路径)。类型名需与 shader RDEF 资源名一致。"""
        for mat in self.materials:
            if mat.name == material_name:
                tb = TextureBinding()
                tb.texture_type = texture_type
                tb.texture_path = texture_path
                mat.textures.append(tb)
                return tb
        raise ValueError(f"material {material_name!r} not found")

    def set_texture(self, material_name, texture_type, texture_path=None,
                    default_path="Null.tex"):
        """新增或修改某材质的贴图槽(按 type 名匹配; 已存在则改路径, 否则新增)。

        绑定键 = type 名(需与 mmtr header 的池名一致); path 为空则用 default_path 占位。
        占位路径无需真实存在(只为让槽位成立)。
        """
        path = texture_path or default_path
        for mat in self.materials:
            if mat.name == material_name:
                for tb in mat.textures:
                    if tb.texture_type == texture_type:
                        tb.texture_path = path
                        return tb
                tb = TextureBinding()
                tb.texture_type = texture_type
                tb.texture_path = path
                mat.textures.append(tb)
                return tb
        raise ValueError(f"material {material_name!r} not found")

    def save(self, path: str):
        """写入 mdf2（重排所有偏移 + 重算 hash）。

        version 10（DMC5）走旧布局；version>=31（MHWS 等）走 `_save_v31`。
        """
        ver = self.game_version
        if ver >= 31:
            return self._save_v31(path)
        if ver >= 19:
            raise NotImplementedError("version 19-30 的写入尚未适配（v10 与 v31+ 已支持）")
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

        # 逐材质计算 propData 与各子表偏移(值区按 d3dcompiler cbuffer 布局)
        cur_prop_data = 0
        for mat in self.materials:
            mat.prop_data_offset = data_off_base + cur_prop_data
            # 参数块大小按 16 字节对齐 —— 必须与 shader 的 UserMaterial cbuffer 大小一致
            # (cbuffer 大小总是 16 的倍数; vanilla mdf2 也是如此)。留成紧凑值(如 76)
            # 会让 REasy 等按 ABI 校验的工具判定 "MDF allocates 76, MMTR declares 80"。
            mat.prop_block_size = (layout_prop_offsets(mat.properties) + 15) & ~15
            cur_prop_data += mat.prop_block_size

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

    # ---- version>=31 的结构写入（布局参考 REasy 的 mdf 实现，并用原版文件往返校验） ----
    @staticmethod
    def _v31_record_offsets(ver):
        """v31+ 材质记录的字段偏移（相对记录起点；v51 在 flags 后多一个 u64）。"""
        o, p = {}, 0

        def take(key, size):
            nonlocal p
            o[key] = p
            p += size

        take("name", 8); take("name_hash", 4)
        take("params_size", 4); take("param_count", 4); take("tex_count", 4)
        if ver >= 19:
            take("gpbf_name_count", 4); take("gpbf_data_count", 4)
        if ver >= 31:
            take("bake", 4)
        take("shader_type", 4)
        if ver >= 31:
            take("flags", 8)
            if ver >= 51:
                take("unk51", 8)
            take("lod", 4)
        else:
            take("flags", 4)
        take("prop_hdr", 8); take("tex_hdr", 8)
        if ver >= 19:
            take("gpbf", 8)
        take("params", 8); take("mmtr", 8)
        if ver >= 31:
            take("lod_redir", 8)
        o["_size"] = p
        return o

    def _save_v31(self, path):
        """写 version>=31 的 mdf2（结构写回）。

        区域顺序（与 REasy 实现、原版文件实测一致）：
          文件头 → 材质记录 → tex 头(按材质) → param 头(按材质) → GPBF 表(按材质)
          → 字符串池 → 各材质参数值区 → shaderLODRedirects。
        v>=40 时空表共享同一偏移（原版做法）。
        """
        ver = self.game_version
        n = len(self.materials)
        fo = self._v31_record_offsets(ver)
        out = bytearray(b"MDF\0" + struct.pack("<HHQ", 1, n, self.material_flags))

        rec = []
        for _ in self.materials:
            rec.append(len(out))
            out += b"\x00" * fo["_size"]

        shared = {} if ver >= 40 else None

        def table(kind, items):
            if shared is None or items:
                return len(out)
            return shared.setdefault(kind, len(out))

        tex_pos = []
        for mat in self.materials:
            mat.tex_headers_offset = table("tex", mat.textures)
            for tb in mat.textures:
                tex_pos.append((tb, len(out)))
                out += b"\x00" * 32          # v>=13 的 tex 条目
        prop_pos = []
        for mat in self.materials:
            mat.prop_headers_offset = table("prop", mat.properties)
            for pr in mat.properties:
                prop_pos.append((pr, len(out)))
                out += b"\x00" * 24
        gpbf_pos = []
        for mat in self.materials:
            mat.gpbf_offset = table("gpbf", mat.gpu_buffers)
            mat.gpbf_name_count = mat.gpbf_data_count = len(mat.gpu_buffers)
            for nh, dh in mat.gpu_buffers:
                gpbf_pos.append((nh, len(out), False)); out += b"\x00" * 16
                gpbf_pos.append((dh, len(out), True)); out += b"\x00" * 16

        # 参数值区：先按原 rel 偏移分配偏移（保留原文件的对齐/空洞），再算块大小
        for mat in self.materials:
            cursor = 0
            for pr in mat.properties:
                pr.param_count = len(pr.values)
                off = pr.data_offset if pr.data_offset >= cursor else cursor
                pr.data_offset = off
                cursor = off + pr.param_count * 4
            orig = int(mat.prop_block_size or 0)
            mat.prop_block_size = orig if orig >= cursor else self._align16(cursor)

        # 字符串池：每个占位都写一份（原版**不删重复串**，否则总长对不上），
        # 但偏移指向该串**首次出现**的位置（同一串共享偏移）。
        str_base = len(out)
        pool = bytearray()
        first = {}

        def set_str(field_pos, text):
            text = text or ""
            cur = str_base + len(pool)
            off = first.setdefault(text, cur)
            pool.extend(text.encode("utf-16-le") + b"\x00\x00")
            struct.pack_into("<Q", out, field_pos, off)

        for mat, rp in zip(self.materials, rec):
            set_str(rp + fo["name"], mat.name)
            set_str(rp + fo["mmtr"], mat.mmtr_path)
        for tb, tp in tex_pos:
            if tb.texture_type:
                set_str(tp, tb.texture_type)
            if tb.texture_path:
                set_str(tp + 16, tb.texture_path)
        for pr, pp in prop_pos:
            set_str(pp, pr.name)
        for h, hp, _is_val in gpbf_pos:
            set_str(hp, h["name"])
        out += pool

        # 参数值区
        for mat in self.materials:
            base = len(out)
            mat.prop_data_offset = base
            for pr in mat.properties:
                target = base + pr.data_offset
                if target > len(out):
                    out += b"\x00" * (target - len(out))
                pr.value_offset = len(out)
                if pr.param_count:
                    out += struct.pack("<%df" % pr.param_count,
                                       *[float(v) for v in pr.values[:pr.param_count]])
            want = base + mat.prop_block_size
            if want > len(out):
                out += b"\x00" * (want - len(out))

        # shaderLODRedirects（v31+ 且 shaderLODNum>0）
        for mat in self.materials:
            if ver >= 31 and mat.shader_lod_num > 0:
                mat.shader_lod_redirects_offset = len(out)
                table_pos = len(out)
                out += b"\x00" * (mat.shader_lod_num * 16)
                offsets = []
                for i in range(mat.shader_lod_num):
                    counts, elems = [], []
                    if i < len(mat.shader_lod_redirects):
                        counts, elems = mat.shader_lod_redirects[i]
                    offsets.append(len(out))
                    out += struct.pack("<i", len(counts))
                    out += b"".join(struct.pack("<i", int(v)) for v in counts)
                    offsets.append(len(out))
                    out += struct.pack("<i", len(elems))
                    out += b"".join(struct.pack("<i", int(v)) for v in elems)
                for j, ofs in enumerate(offsets):
                    struct.pack_into("<q", out, table_pos + j * 8, ofs)
            else:
                mat.shader_lod_redirects_offset = 0

        # 回填材质记录
        for mat, rp in zip(self.materials, rec):
            mat.name_hash = utf16_hash(mat.name)
            mat.texture_count = len(mat.textures)
            mat.property_count = len(mat.properties)
            struct.pack_into("<I", out, rp + fo["name_hash"], mat.name_hash & 0xFFFFFFFF)
            struct.pack_into("<i", out, rp + fo["params_size"], int(mat.prop_block_size))
            struct.pack_into("<i", out, rp + fo["param_count"], mat.property_count)
            struct.pack_into("<i", out, rp + fo["tex_count"], mat.texture_count)
            if ver >= 19:
                struct.pack_into("<i", out, rp + fo["gpbf_name_count"], mat.gpbf_name_count)
                struct.pack_into("<i", out, rp + fo["gpbf_data_count"], mat.gpbf_data_count)
                struct.pack_into("<q", out, rp + fo["gpbf"], mat.gpbf_offset)
            if ver >= 31:
                struct.pack_into("<I", out, rp + fo["bake"],
                                 mat.bake_texture_array_size & 0xFFFFFFFF)
                struct.pack_into("<Q", out, rp + fo["flags"],
                                 int(mat.flags) & 0xFFFFFFFFFFFFFFFF)
                if ver >= 51:
                    struct.pack_into("<Q", out, rp + fo["unk51"],
                                     int(mat.unk51) & 0xFFFFFFFFFFFFFFFF)
                struct.pack_into("<I", out, rp + fo["lod"],
                                 int(mat.shader_lod_num) & 0xFFFFFFFF)
            else:
                struct.pack_into("<I", out, rp + fo["flags"], int(mat.flags) & 0xFFFFFFFF)
            struct.pack_into("<i", out, rp + fo["shader_type"], int(mat.shader_type))
            struct.pack_into("<q", out, rp + fo["prop_hdr"], mat.prop_headers_offset)
            struct.pack_into("<q", out, rp + fo["tex_hdr"], mat.tex_headers_offset)
            struct.pack_into("<q", out, rp + fo["params"], mat.prop_data_offset)
            if ver >= 31:
                struct.pack_into("<q", out, rp + fo["lod_redir"],
                                 mat.shader_lod_redirects_offset)

        # 贴图头（type/path 为空写 0；hashes 重算）
        for tb, tp in tex_pos:
            if not tb.texture_type:
                struct.pack_into("<Q", out, tp, 0)
            else:
                struct.pack_into("<II", out, tp + 8,
                                 utf16_hash(tb.texture_type) & 0xFFFFFFFF,
                                 ascii_hash(tb.texture_type) & 0xFFFFFFFF)
            if not tb.texture_path:
                struct.pack_into("<Q", out, tp + 16, 0)
            struct.pack_into("<ii", out, tp + 24, int(tb.locked), int(tb.reserved))
        # 参数头
        for pr, pp in prop_pos:
            struct.pack_into("<II", out, pp + 8,
                             utf16_hash(pr.name) & 0xFFFFFFFF,
                             ascii_hash(pr.name) & 0xFFFFFFFF)
            struct.pack_into("<i", out, pp + 16, int(pr.data_offset))
            comp = ((int(pr.component_locked) & 0xFFFF) << 16) | (int(pr.param_count) & 0xFFFF)
            struct.pack_into("<I", out, pp + 20, comp)
        # GPBF 条目（data 条目的 hashes 固定 0/1）
        for h, hp, is_val in gpbf_pos:
            if is_val:
                uh, ah = 0, 1
            else:
                uh = utf16_hash(h["name"]) & 0xFFFFFFFF
                ah = ascii_hash(h["name"]) & 0xFFFFFFFF
            struct.pack_into("<II", out, hp + 8, uh, ah)

        open(path, "wb").write(out)
        return len(out)

    # ---- 只改值写回（不改结构；mdf2.45 等尚未适配结构写入的版本用） ----
    def patch_bytes(self, original: bytes) -> bytes:
        """把**允许改值的字段**写回原始字节：长度、偏移、字符串池一律不动。

        可写：参数浮点值（分量数必须与原来一致）、着色类型、material_flags、
        shaderLODNum。名称 / mmtr 路径 / 贴图路径 / 增删项 / 类型变更都不会写
        （GUI 已对这些禁改；模型里若出现结构改动，这里会报 PatchError）。
        """
        if len(original) < 16 or original[:4] != b"MDF\0":
            raise PatchError("not an MDF file")
        out = bytearray(original)
        for mat in self.materials:
            if not mat.shader_type_offset or not mat.flags_offset:
                raise PatchError("未记录字段偏移，无法补丁写回：%r" % (mat.name,))
            if mat.shader_type_offset + 4 > len(out):
                raise PatchError("着色类型偏移越界：%r" % (mat.name,))
            struct.pack_into("<i", out, mat.shader_type_offset, int(mat.shader_type))
            if mat.flags_offset + mat.flags_width > len(out):
                raise PatchError("flags 偏移越界：%r" % (mat.name,))
            if mat.flags_width == 8:
                struct.pack_into("<Q", out, mat.flags_offset,
                                 int(mat.flags) & 0xFFFFFFFFFFFFFFFF)
            else:
                struct.pack_into("<I", out, mat.flags_offset,
                                 int(mat.flags) & 0xFFFFFFFF)
            if mat.shader_lod_num_offset:
                if mat.shader_lod_num_offset + 4 > len(out):
                    raise PatchError("shaderLODNum 偏移越界：%r" % (mat.name,))
                struct.pack_into("<I", out, mat.shader_lod_num_offset,
                                 int(mat.shader_lod_num) & 0xFFFFFFFF)
            for pr in mat.properties:
                want = pr.orig_param_count or pr.param_count
                if len(pr.values) != want:
                    raise PatchError(
                        "参数 %r 的分量数 %d != 原 %d（改类型属于结构改动，不能用补丁写回）"
                        % (pr.name, len(pr.values), want))
                if pr.orig_param_count and pr.param_count != pr.orig_param_count:
                    raise PatchError(
                        "参数 %r 的 param_count 被改动（结构类改动）" % (pr.name,))
                if not pr.value_offset:
                    raise PatchError("参数 %r 缺少值区偏移" % (pr.name,))
                if pr.value_offset + want * 4 > len(out):
                    raise PatchError("参数 %r 的值区越界" % (pr.name,))
                if want:
                    struct.pack_into("<%df" % want, out, pr.value_offset,
                                     *[float(v) for v in pr.values])
        return bytes(out)

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
