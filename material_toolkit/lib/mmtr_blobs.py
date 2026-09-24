#!/usr/bin/env python3
"""mmtr blob 来源统一入口。

blob 区 = `[blob_start, EOF)` 的一串**标准 DXBC**(每个 = 'DXBC' 帧)。
本模块把"如何得到一个 blob"从散落的 CLI 工具收敛成统一入口, 供:
  - S5-c「从规格装配 blob 区」(从 0 构建 mmtr);
  - #2 变体相关「把变体重构/重指到某 blob」。
共用。

四组能力:
  1. 枚举/提取:  list_blobs / blob_count / extract_blob
  2. DXBC 规范化: frame_size / set_frame_size / refingerprint / finalize
                 (任何"手改内容"之后统一走 finalize: 修正帧 size + 重算指纹)
  3. 校验(纯 Python): verify_dxbc(D3DDisassemble/D3DStripShader/D3DReflect)
  4. asm 路径(外部 3Dmigoto exe): find_assembler / disassemble_dxbc / assemble_asm

并用薄抽象 `BlobSource`(kind=transport|dxbc|asm) + `resolve()` 声明式描述"某槽的 blob 从哪来"。

DXBC 帧布局(36 字节头 + chunk 偏移):
  [0:4]='DXBC'  [4:20]=16B 指纹  [20:24]=ver  [24:28]=size  [28:32]=chunkCount  [32:]=chunkOffsets[]
  指纹 = 微软 DxilHash retail 变体(基于 MD5), 见 lib/dxilhash.py; D3D 强校验。
"""
import os
import shutil
import struct
import subprocess
import tempfile

try:
    from .dxilhash import dxil_hash
    from .binding import blob_list
except ImportError:  # 允许脚本直接 import
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dxilhash import dxil_hash
    from binding import blob_list

# 工作区根: lib/ -> material_toolkit/ -> tools/ -> 根
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

# 外部汇编器(3Dmigoto, 带 asm2cbo 单步模式)候选位置
_ASSEMBLER_CANDS = (
    os.path.join(_ROOT, "reference", "D3D_Shaders", "bin", "x64", "Release", "D3D_Shaders.exe"),
    os.path.join(_ROOT, "tools", "mmtr_editor", "utils", "bin", "D3D_Shaders.exe"),
)
# exe 枚举约定: ????????????????-??.bin/.txt/.cbo (16 字符 + '-' + 2 字符)
SHADER_NAME = "aaaaaaaaaaaaaaaa-01"

TARGET_STAGE = {0xFFFE0500: "VS", 0xFFFF0500: "PS", 0x43530500: "CS"}


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


# ---------------------------------------------------------------- 1. 枚举/提取
def list_blobs(data):
    """返回 [(offset, size), ...](按文件内顺序)。"""
    return blob_list(data)[1]


def blob_count(data):
    return len(list_blobs(data))


def extract_blob(data, idx):
    """提取第 idx 个 blob 的原始 DXBC 字节。"""
    bl = list_blobs(data)
    if not (0 <= idx < len(bl)):
        raise IndexError(f"blob idx {idx} out of range (0..{len(bl) - 1})")
    off, size = bl[idx]
    return bytes(data[off:off + size])


# ---------------------------------------------------------------- 2. DXBC 规范化
def frame_size(dxbc):
    """读 DXBC 帧内的 size 字段([24:28])。"""
    return _u32(dxbc, 24)


def set_frame_size(dxbc):
    """把帧内 size 字段([24:28])改为实际长度。"""
    b = bytearray(dxbc)
    if len(b) >= 28:
        struct.pack_into("<I", b, 24, len(b))
    return bytes(b)


def refingerprint(dxbc):
    """重算 16 字节指纹([4:20])= DxilHash retail, 覆盖 [20:] 全部内容。"""
    b = bytearray(dxbc)
    if len(b) >= 20:
        b[4:20] = dxil_hash(bytes(b[20:]), "retail")
    return bytes(b)


def finalize(dxbc):
    """规范化: 先修正帧 size, 再重算指纹。任何手改内容后统一走它。

    注: 对"未改动"的合法 DXBC(如从 mmtr 搬运出的 blob)是**恒等**——size 与指纹本就正确。
    """
    return refingerprint(set_frame_size(dxbc))


# ---------------------------------------------------------------- 3. 校验(D3D + RDEF)
def rdef_signature(dxbc):
    """解析 RDEF 头, 返回 {'n_cb','n_br','target','stage'} (无 RDEF 时前两项/ target 为 None)。"""
    nch = _u32(dxbc, 28)
    for k in range(nch):
        co = _u32(dxbc, 32 + k * 4)
        if dxbc[co:co + 4] == b"RDEF":
            n_cb, _cbo, n_br, _bro, target = struct.unpack_from("<IIIII", dxbc, co + 8)
            return {"n_cb": n_cb, "n_br": n_br, "target": target,
                    "stage": TARGET_STAGE.get(target, f"0x{target:08x}")}
    return {"n_cb": None, "n_br": None, "target": None, "stage": "?"}


class _GUID(object):
    """ctypes GUID(用于 D3DReflect 的 IID)。"""

    def __new__(cls, s):
        import ctypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong),
                        ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(s)
        return GUID(u.time_low, u.time_mid, u.time_hi_version,
                    (ctypes.c_ubyte * 8)(*u.bytes[8:]))


# ID3D11ShaderReflection
_IID_SHADER_REFLECTION = "{8d536ca1-0cca-4956-a837-786963755584}"

_d3d = None


def _load_d3d():
    """优先用工作区自带 libs/d3dcompiler_47.dll, 否则用系统。"""
    global _d3d
    if _d3d is not None:
        return _d3d
    import ctypes
    for cand in (os.path.join(_ROOT, "libs", "d3dcompiler_47.dll"),
                 os.path.join(_ROOT, "tools", "mmtr_editor", "utils", "bin", "d3dcompiler_47.dll")):
        if os.path.exists(cand):
            try:
                _d3d = ctypes.WinDLL(cand)
                return _d3d
            except OSError:
                pass
    _d3d = ctypes.WinDLL("d3dcompiler_47.dll")
    return _d3d


def verify_dxbc(dxbc):
    """对**独立 DXBC blob** 做 D3D 三校验。返回 dict:

      disasm_ok  D3DDisassemble==0 (微软亲裁: 字节码合法)
      strip_ok   D3DStripShader==0  (比 disassemble 更严, 防 chunk 不一致)
      reflect_ok D3DReflect==0      (RDEF 合法; !=0 时引擎会材质整黑)
      n_cb/n_br/target/stage  RDEF 摘要
    """
    import ctypes

    d3d = _load_d3d()
    blob = bytes(dxbc)
    size = len(blob)
    buf = ctypes.create_string_buffer(blob)

    dis = d3d.D3DDisassemble
    dis.restype = ctypes.c_long
    dis.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint,
                    ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    strip = d3d.D3DStripShader
    strip.restype = ctypes.c_long
    strip.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint,
                      ctypes.POINTER(ctypes.c_void_p)]
    reflect = d3d.D3DReflect
    reflect.restype = ctypes.c_long
    reflect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p)]

    p = ctypes.c_void_p()
    hr_dis = dis(ctypes.cast(buf, ctypes.c_void_p), size, 0, None, ctypes.byref(p))
    p2 = ctypes.c_void_p()
    hr_strip = strip(ctypes.cast(buf, ctypes.c_void_p), size, 0, ctypes.byref(p2))
    iid = _GUID(_IID_SHADER_REFLECTION)
    p3 = ctypes.c_void_p()
    hr_reflect = reflect(ctypes.cast(buf, ctypes.c_void_p), size,
                         ctypes.byref(iid), ctypes.byref(p3))

    out = {"disasm_ok": hr_dis == 0, "strip_ok": hr_strip == 0,
           "reflect_ok": hr_reflect == 0}
    out.update(rdef_signature(blob))
    return out


# ---------------------------------------------------------------- 4. asm 路径(外部 exe)
def find_assembler():
    """定位 3Dmigoto D3D_Shaders.exe(带 asm2cbo 单步); 缺失则报清晰错误。"""
    env = os.environ.get("D3D_SHADERS_EXE")
    cands = ([env] if env else []) + list(_ASSEMBLER_CANDS)
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError(
        "未找到 D3D_Shaders.exe(带 asm2cbo)。请用 MSBuild 编译 "
        "reference/D3D_Shaders/src (Release|x64), 或设置环境变量 D3D_SHADERS_EXE。")


def _run_exe(exe, args, workdir):
    p = subprocess.run([exe] + args, cwd=workdir, capture_output=True,
                       text=True, timeout=600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def disassemble_dxbc(dxbc, workdir=None):
    """把独立 DXBC blob 反汇编成**标准 asm**(D3D_Shaders 默认流程 bin->asm)。"""
    exe = find_assembler()
    own = workdir is None
    if own:
        workdir = tempfile.mkdtemp(prefix="mmtr_blobs_disasm_")
    try:
        cache = os.path.join(workdir, "ShaderCache")
        shutil.rmtree(cache, ignore_errors=True)
        os.makedirs(cache, exist_ok=True)
        with open(os.path.join(cache, SHADER_NAME + ".bin"), "wb") as f:
            f.write(bytes(dxbc))
        _rc, log = _run_exe(exe, [], workdir)
        out = os.path.join(cache, SHADER_NAME + ".txt")
        if not os.path.exists(out):
            raise RuntimeError(f"反汇编失败(未生成 asm):\n{log}")
        with open(out, encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        if own:
            shutil.rmtree(workdir, ignore_errors=True)


def assemble_asm(asm_text, ref_dxbc=None, workdir=None):
    """把 asm 文本汇编成**带指纹 DXBC**(D3D_Shaders asm2cbo; 自动处理 SHEX size/
    dwordCount/chunk 偏移/指纹)。ref_dxbc 给定时作为参考 bin(用于 exe 的 shader 枚举)。
    """
    exe = find_assembler()
    own = workdir is None
    if own:
        workdir = tempfile.mkdtemp(prefix="mmtr_blobs_asm_")
    try:
        cache = os.path.join(workdir, "ShaderCache")
        shutil.rmtree(cache, ignore_errors=True)
        os.makedirs(cache, exist_ok=True)
        if ref_dxbc is not None:
            with open(os.path.join(cache, SHADER_NAME + ".bin"), "wb") as f:
                f.write(bytes(ref_dxbc))
        with open(os.path.join(cache, SHADER_NAME + ".txt"), "w",
                  encoding="utf-8") as f:
            f.write(asm_text)
        _rc, log = _run_exe(exe, ["asm2cbo", workdir], workdir)
        cbo = os.path.join(cache, SHADER_NAME + ".cbo")
        if not os.path.exists(cbo):
            raise RuntimeError(f"汇编失败(未生成 DXBC):\n{log}")
        with open(cbo, "rb") as f:
            return f.read()
    finally:
        if own:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- 可选: HLSL 混合翻译器预处理
# hlsl_blend_dxbc_translator: 把“DXBC asm + HLSL 标记”混合文本翻回纯 asm(非标记行透传)。
# 纯 asm 是它的子集 ⇒ 无需“切换逻辑”: 有 exe 就把编辑文本过一遍再汇编, 没有则直接用原 asm。
_TRANSLATOR_CANDS = (
    os.path.normpath(os.path.join(_ROOT, "..", "..", "Cpp",
                                  "hlsl_blend_dxbc_translator", "x64", "Release",
                                  "hlsl_blend_dxbc_translator.exe")),
)


def find_translator():
    """定位 hlsl_blend_dxbc_translator.exe; 缺失返回 None(纯 asm 仍可用)。

    查找: 环境变量 HLSL_BLEND_TRANSLATOR_EXE -> 约定相对路径(<工作区>/../Cpp/...)。
    """
    env = os.environ.get("HLSL_BLEND_TRANSLATOR_EXE")
    for c in ([env] if env else []) + list(_TRANSLATOR_CANDS):
        if c and os.path.exists(c):
            return c
    return None


def run_translator(asm_text, exe=None, workdir=None):
    """把混合 asm 文本过一遍翻译器 -> 纯 asm 文本。exe 缺失则报错。"""
    exe = exe or find_translator()
    if not exe:
        raise FileNotFoundError("未找到 hlsl_blend_dxbc_translator.exe")
    own = workdir is None
    if own:
        workdir = tempfile.mkdtemp(prefix="mmtr_translator_")
    try:
        data_dir = os.path.dirname(os.path.dirname(os.path.dirname(exe)))  # <proj>/data
        data_dir = os.path.join(data_dir, "data")
        inp = os.path.join(workdir, "in.asm")
        outp = os.path.join(workdir, "out.asm")
        with open(inp, "w", encoding="utf-8") as f:
            f.write(asm_text)
        args = [exe, "-input", inp, "-output", outp]
        if os.path.isdir(data_dir):
            args += ["-data", data_dir]
        p = subprocess.run(args, capture_output=True, text=True, timeout=600)
        if not os.path.exists(outp):
            raise RuntimeError(f"翻译器失败:\n{p.stdout}\n{p.stderr}")
        with open(outp, encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        if own:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- BlobSource 抽象
class BlobSource(object):
    """描述"一个 blob 从哪来", resolve() -> 规范化后的 DXBC bytes。

    三种来源:
      transport: 既有 blob 搬运(从一个 mmtr 里提取第 idx 个 blob);
      dxbc     : 现成 DXBC 字节(已在别处汇编好);
      asm      : asm 文本(本库调 D3D_Shaders 汇编成带指纹 DXBC)。
    三者 resolve() 后都经 finalize 规范化(size + 指纹), 保证后续可安全放入 mmtr。
    """

    def __init__(self, kind, *, mmtr=None, idx=None, dxbc=None, asm=None,
                 ref_dxbc=None, workdir=None):
        self.kind = kind
        self.mmtr = mmtr
        self.idx = idx
        self.dxbc = dxbc
        self.asm = asm
        self.ref_dxbc = ref_dxbc
        self.workdir = workdir

    @classmethod
    def transport(cls, mmtr: bytes, idx: int):
        """从既有 mmtr 搬运第 idx 个 blob。"""
        return cls("transport", mmtr=mmtr, idx=idx)

    @classmethod
    def from_dxbc(cls, dxbc: bytes):
        """直接用现成 DXBC 字节。"""
        return cls("dxbc", dxbc=dxbc)

    @classmethod
    def from_asm(cls, asm: str, ref_dxbc=None, workdir=None):
        """汇编 asm 文本 -> 带指纹 DXBC。"""
        return cls("asm", asm=asm, ref_dxbc=ref_dxbc, workdir=workdir)

    def resolve(self) -> bytes:
        if self.kind == "transport":
            return finalize(extract_blob(self.mmtr, self.idx))
        if self.kind == "dxbc":
            return finalize(self.dxbc)
        if self.kind == "asm":
            return finalize(assemble_asm(self.asm, ref_dxbc=self.ref_dxbc,
                                         workdir=self.workdir))
        raise ValueError(f"未知 BlobSource kind: {self.kind!r}")

    def __repr__(self):
        tag = {"transport": f"idx={self.idx}", "dxbc": f"{len(self.dxbc or b'')}B",
               "asm": "asm"}.get(self.kind, self.kind)
        return f"BlobSource({self.kind}, {tag})"
