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
import re
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

# 包根 material_toolkit/ 与仓库根(其上级); 外部二进制统一放 <pkg>/bin/ 或 <repo>/bin/
_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # material_toolkit/
_REPO = os.path.dirname(_PKG)                                        # 仓库根


def _bin(name):
    """外部二进制候选: <pkg>/bin/ 优先, 其次 <repo>/bin/。"""
    return os.path.join(_PKG, "bin", name)


# 外部汇编器(3Dmigoto 改版, 带 asm2cbo 单步模式)候选位置:
#   bin/D3D_Shaders.exe —— 发布包已内置; 从源码运行需自行编译后放入(见 README)。
_ASSEMBLER_CANDS = (
    _bin("D3D_Shaders.exe"),
    os.path.join(_REPO, "bin", "D3D_Shaders.exe"),
)
# exe 枚举约定: ????????????????-??.bin/.txt/.cbo (16 字符 + '-' + 2 字符)
SHADER_NAME = "aaaaaaaaaaaaaaaa-01"

# 阶段 token(与 rdef.TARGET_STAGE 同源): ...0500=SM5.0 / ...0501=SM5.1。
TARGET_STAGE = {
    0xFFFE0500: "VS", 0xFFFF0500: "PS", 0x47530500: "GS",
    0x48530500: "HS", 0x44530500: "DS", 0x43530500: "CS",
    0xFFFE0501: "VS", 0xFFFF0501: "PS", 0x47530501: "GS",
    0x48530501: "HS", 0x44530501: "DS", 0x43530501: "CS",
}


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
    """优先用随包的 bin/d3dcompiler_47.dll, 否则用系统。"""
    global _d3d
    if _d3d is not None:
        return _d3d
    import ctypes
    for cand in (_bin("d3dcompiler_47.dll"),
                 os.path.join(_REPO, "bin", "d3dcompiler_47.dll")):
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


# ---------------------------------------------------------------- 3b. HLSL 编译(D3DCompile)
def _blob_bytes(p):
    """读取 ID3DBlob(p) 内容(经 vtable 的 GetBufferPointer/GetBufferSize)。"""
    import ctypes
    vt = ctypes.cast(p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])
    get_sz = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])
    return ctypes.string_at(get_ptr(p), get_sz(p))


def compile_hlsl(source, entry="main", target="ps_5_0", name="shader.hlsl",
                 flags1=0, flags2=0):
    """用 d3dcompiler_47 把 HLSL 源码编译成 DXBC。

    返回 (dxbc_bytes, err_text); err_text 非 None 表示失败(含编译错误文本)。
    注: 传**单一**源码字符串(不依赖 #include); 输入/输出签名由源码里的结构体决定。
    """
    import ctypes

    d3d = _load_d3d()
    fn = d3d.D3DCompile
    fn.restype = ctypes.c_long
    fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p,
                   ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    src = source.encode("utf-8")
    buf = ctypes.create_string_buffer(src)
    code = ctypes.c_void_p()
    err = ctypes.c_void_p()
    hr = fn(ctypes.cast(buf, ctypes.c_void_p), len(src),
            name.encode("ascii", "replace"), None, None,
            entry.encode("ascii"), target.encode("ascii"),
            flags1, flags2, ctypes.byref(code), ctypes.byref(err))
    if hr != 0:
        msg = _blob_bytes(err.value) if err.value else b""
        return None, msg.decode("utf-8", "replace")
    return finalize(_blob_bytes(code.value)), None


# ---------------------------------------------------------------- 4. asm 路径(外部 exe)
def find_assembler():
    """定位 3Dmigoto D3D_Shaders.exe(带 asm2cbo 单步); 缺失则报清晰错误。"""
    env = os.environ.get("D3D_SHADERS_EXE")
    cands = ([env] if env else []) + list(_ASSEMBLER_CANDS)
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError(
        "未找到 D3D_Shaders.exe(带 asm2cbo)。请把编译好的 exe 放到 "
        "<material_toolkit>/bin/ 或 <repo>/bin/, 或用环境变量 D3D_SHADERS_EXE 指定。"
        "发布包已内置该 exe; 从源码构建见 README「获取 D3D_Shaders.exe」。")


def find_dxc():
    """定位 dxc.exe（Windows SDK 10 自带；用于 **DXIL 反汇编（只读）**）。

    DXIL(SM6) 没有可用的汇编器（无 D3DAssemble 等价物），所以这里只做
    `dxc -dumpbin`：输出 LLVM IR + 签名/资源表文本，供查看分析，不能写回。
    可用环境变量 DXC_EXE 指定。
    """
    env = os.environ.get("DXC_EXE")
    cands = [env] if env else []
    w = shutil.which("dxc")
    if w:
        cands.append(w)
    kits = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    base = os.path.join(kits, "Windows Kits", "10", "bin")
    if os.path.isdir(base):
        for ver in sorted(os.listdir(base), reverse=True):
            p = os.path.join(base, ver, "x64", "dxc.exe")
            if os.path.isfile(p):
                cands.append(p)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "未找到 dxc.exe（Windows SDK 10 自带）。可安装 SDK 或用环境变量 DXC_EXE 指定。")


def disassemble_dxil(blob, timeout=60):
    """用一个 DXBC/DXIL 容器（或裸 DXIL bitcode）调 `dxc -dumpbin` -> 反汇编文本。

    仅用于查看/分析（DXIL 不可汇编回去）；失败时抛 RuntimeError(带 dxc 的输出)。
    """
    dxc = find_dxc()
    fd, path = tempfile.mkstemp(prefix="mmtr_dxil_", suffix=".dxil")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bytes(blob))
        p = subprocess.run([dxc, "-dumpbin", path], capture_output=True, timeout=timeout)
        if p.returncode != 0:
            err = (p.stderr or p.stdout or b"").decode("utf-8", "replace")
            raise RuntimeError(err.strip() or "dxc -dumpbin 失败")
        return p.stdout.decode("utf-8", "replace")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- 签名 chunk（ISG1/OSG1/…）
# DXBC 签名 chunk 里的 name 偏移**相对 chunk 数据起点**（fourcc+size 之后的 payload），
# 元素布局（实测 MHWS DXIL，与 `dxc -dumpbin` 的输入/输出表逐项一致）：
#   ISG1 / OSG1 / PSG1  32B：stream u32, name u32, sem_idx u32, sysval u32,
#                            comptype u32, register u32, mask u8, used u8,
#                            pad u16, min_precision u32
#   ISGN / OSGN / PCSG  24B：name/sem_idx/sysval/comptype/register (5×u32),
#                            mask u8, used u8, pad u16
#   OSG5                28B：stream u32 + 同 24B（无 min precision）
# 说明：DMC5(SM5.0/5.1) 与 MHWS(SM6/DXIL) 都能用这一套解析，只是 tag 不同。
SIGNATURE_KINDS = {
    "ISGN": "input", "ISG1": "input", "ISG5": "input",
    "OSGN": "output", "OSG1": "output", "OSG5": "output",
    "PCSG": "patch", "PSG1": "patch",
}

# D3D_NAME（system value）；64+ 是 D3D_NAME_TARGET 一族（输出签名用）。
SYSTEM_VALUE_NAMES = {
    0: "NONE", 1: "POS", 2: "CLIP_DISTANCE", 3: "CULL_DISTANCE",
    4: "RENDER_TARGET_ARRAY_INDEX", 5: "VIEWPORT_ARRAY_INDEX", 6: "VERTEX_ID",
    7: "PRIMITIVE_ID", 8: "INSTANCE_ID", 9: "IS_FRONT_FACE", 10: "SAMPLE_INDEX",
    11: "FINAL_QUAD_EDGE_TESSFACTOR", 12: "FINAL_QUAD_INSIDE_TESSFACTOR",
    13: "FINAL_TRI_EDGE_TESSFACTOR", 14: "FINAL_TRI_INSIDE_TESSFACTOR",
    15: "FINAL_LINE_DETAIL_TESSFACTOR", 16: "FINAL_LINE_DENSITY_TESSFACTOR",
    17: "BARYCENTRICS", 18: "SHADINGRATE", 19: "CULL_PRIMITIVE",
    64: "TARGET", 65: "DEPTH", 66: "COVERAGE", 67: "DEPTH_GREATER_EQUAL",
    68: "DEPTH_LESS_EQUAL", 69: "STENCIL_REF",
}

# D3D_REGISTER_COMPONENT_TYPE
COMPONENT_TYPE_NAMES = {
    0: "unknown", 1: "uint", 2: "int", 3: "float", 4: "uint16", 5: "int16",
    6: "float16", 7: "uint64", 8: "int64", 9: "float64",
}


def mask_str(mask):
    """分量掩码 -> "xyzw" 子集（0 -> "-"）。"""
    s = "".join(c for i, c in enumerate("xyzw") if (mask >> i) & 1)
    return s or "-"


def dxbc_chunks(blob):
    """独立 DXBC blob 的 chunk 列表 -> [(fourcc, 相对偏移, 大小)]（读不出返回 []）。"""
    if len(blob) < 32 or blob[:4] != b"DXBC":
        return []
    n = struct.unpack_from("<I", blob, 28)[0]
    if n <= 0 or n > 64:
        return []
    out = []
    for i in range(n):
        off = struct.unpack_from("<I", blob, 32 + i * 4)[0]
        if off + 8 > len(blob):
            break
        out.append((blob[off:off + 4].decode("latin1"), off,
                    struct.unpack_from("<I", blob, off + 4)[0]))
    return out


def parse_signature(blob, tag, off, size):
    """解析一个签名 chunk -> [{name,index,register,mask,used,stream,...}]。

    注意 chunk 头(8B) 里的 size 是 **payload 长度**（不含 fourcc/size 本身），
    name 偏移也相对 payload —— 两处都以 payload 为基准，否则会截掉最后一个名字。
    """
    payload = off + 8
    end = min(payload + size, len(blob))
    if payload + 8 > end:
        return []
    count = struct.unpack_from("<I", blob, payload)[0]
    if count > 256:
        return []
    if tag == "OSG5":
        stride, stream_prefix, has_min_precision = 28, True, False
    elif tag.endswith("1") or tag.endswith("5"):
        stride, stream_prefix, has_min_precision = 32, True, True
    else:
        stride, stream_prefix, has_min_precision = 24, False, False
    entries = payload + 8
    out = []
    for i in range(count):
        e = entries + i * stride
        if e + stride > end:
            break
        if stream_prefix:
            stream = struct.unpack_from("<I", blob, e)[0]
            base = e + 4
        else:
            stream, base = 0, e
        name_off, sem_idx, sysval, comptype, register = struct.unpack_from("<5I", blob, base)
        mask, used = blob[base + 20], blob[base + 21]
        min_prec = 0
        if has_min_precision and base + 28 <= e + stride:
            min_prec = struct.unpack_from("<I", blob, base + 24)[0]
        name = ""
        np = payload + name_off
        if payload <= np < end:
            z = blob.find(b"\x00", np, end)
            name = blob[np:z].decode("latin1", "replace") if z >= 0 else ""
        out.append({
            "name": name, "index": sem_idx, "register": register,
            "system_value": sysval,
            "system_value_name": SYSTEM_VALUE_NAMES.get(sysval, "0x%X" % sysval),
            "component_type": comptype,
            "type_name": COMPONENT_TYPE_NAMES.get(comptype, "type%d" % comptype),
            "mask": mask, "used": used, "stream": stream,
            "min_precision": min_prec,
        })
    return out


def blob_signatures(blob):
    """一个 DXBC/DXIL blob 的签名 -> {input, output, patch, chunks}（缺失给空列表）。

    `chunks` 是容器里的 fourcc 列表（便于在界面上说明为何没有签名）。
    """
    out = {"input": [], "output": [], "patch": [], "chunks": []}
    for tag, off, size in dxbc_chunks(blob):
        out["chunks"].append(tag)
        kind = SIGNATURE_KINDS.get(tag)
        if not kind or out[kind]:
            continue
        out[kind] = parse_signature(blob, tag, off, size)
    return out


# ---------------------------------------------------------------- PSV0（DXIL 资源声明）
# DXIL 没有 RDEF，资源声明在 PSV0 chunk 里（实测 MHWS，与 `dxc -dumpbin` 的
# Resource Bindings 表逐项对应）：
#   payload + 0x00..0x40   头部（+0x20 = signature vectors 打包, +0x38 = 资源数,
#                          +0x3C = 条目步长）
#   payload + 0x40         资源表：每条 6×u32 =
#                          [f0(未定), f1(未定/首条 RawBuffer=1), id/register,
#                           lower_bound, kind, space]
# lower_bound = 0xFFFFFFFF 表示无界（bindless 的 RawBuffer）。
# 名字不在 PSV0 里（dxc 的名字来自 DXIL 元数据），需要 dxc/RDAT 兜底。
PSV0_HEADER = 0x40

# DXIL::ResourceKind
RESOURCE_KIND_NAMES = {
    1: "Texture1D", 2: "Texture2D", 3: "Texture2DMS", 4: "Texture3D",
    5: "TextureCube", 6: "Texture1DArray", 7: "Texture2DArray",
    8: "Texture2DMSArray", 9: "TextureCubeArray", 10: "TypedBuffer",
    11: "RawBuffer", 12: "StructuredBuffer", 13: "CBuffer", 14: "Sampler",
    15: "TBuffer", 16: "RTAccelerationStructure", 17: "FeedbackTexture2D",
    18: "FeedbackTexture2DArray",
}


def parse_psv0(blob):
    """解析 PSV0 资源表 -> {count, stride, sig, resources}；没有 PSV0 返回 None。

    每条资源：{index, id, lower, kind, kind_name, space, f0, f1}
    （id = HLSL register；lower = 绑定区间起点，0xFFFFFFFF 表示无界）。
    """
    for tag, off, size in dxbc_chunks(blob):
        if tag != "PSV0":
            continue
        payload = off + 8
        end = min(payload + size, len(blob))
        if payload + PSV0_HEADER > end:
            return None
        sig = struct.unpack_from("<I", blob, payload + 0x20)[0]
        count = struct.unpack_from("<I", blob, payload + 0x38)[0]
        stride = struct.unpack_from("<I", blob, payload + 0x3C)[0]
        if not (0 < stride <= 64) or count > 256:
            return None
        if payload + PSV0_HEADER + count * stride > end:
            return None
        out = []
        for i in range(count):
            base = payload + PSV0_HEADER + i * stride
            f0, f1, rid, lower, kind, space = struct.unpack_from("<6I", blob, base)
            out.append({
                "index": i, "f0": f0, "f1": f1, "id": rid, "lower": lower,
                "kind": kind,
                "kind_name": RESOURCE_KIND_NAMES.get(kind, "kind%d" % kind),
                "space": space,
            })
        return {"count": count, "stride": stride, "sig": sig, "resources": out}
    return None


def psv0_names_from_dxc(blob):
    """用 dxc -dumpbin 的 Resource Bindings 表取资源名（PSV0 本身不含名字）。

    返回按 dxc 资源顺序的 [{name,type,dim,bind,count}]；解析失败返回 []。
    仅用于显示，不参与二进制解析。
    """
    try:
        text = disassemble_dxil(blob)
    except Exception:  # noqa: BLE001
        return []
    rows, keep = [], False
    for line in text.splitlines():
        if "Resource Bindings:" in line:
            keep = True
            continue
        if not keep:
            continue
        s = line.strip()
        if s == ";":
            continue
        if not line.startswith("; "):
            keep = False
            continue
        parts = line[1:].split()
        if len(parts) < 7 or parts[0] == "Name" or parts[1].startswith("-"):
            continue
        if not parts[-1].isdigit():
            continue
        rows.append({"name": parts[0], "type": parts[1], "dim": parts[3],
                     "bind": parts[5], "count": int(parts[-1])})
    return rows


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
            dxbc = f.read()
        # 3Dmigoto asm2cbo 的指纹**不可全信**: 实测某些长度算出错误指纹
        # (如 len=6838, leftOver=34 单行 padding 分支) -> D3D 三项校验全拒。
        # 一律用已验证的 dxil_hash 重算(与 mmtr_rebuild 同做法)。
        return finalize(dxbc)
    finally:
        if own:
            shutil.rmtree(workdir, ignore_errors=True)


# 3Dmigoto asm2cbo 的报错形如:
#   ... : Assembly parse error on line 6, Unrecognised instruction:
#   "xyzzy r0, r1"
_ASM_ERR_RE = re.compile(r"on line (\d+),\s*([^\n:]*)")


def parse_asm_errors(log):
    """从 3Dmigoto 汇编日志里抽取 [(行号(1-based), 说明), ...](行号缺失则不含)。"""
    out = []
    for m in _ASM_ERR_RE.finditer(log or ""):
        out.append((int(m.group(1)), m.group(2).strip()))
    return out


def check_asm(asm_text, ref_dxbc=None):
    """试汇编以做"编译即检查"。不抛异常, 返回 dict:

      ok     bool           能否汇编出合法 DXBC
      errors [(line, msg)]  汇编器报错(行号 1-based; 供编辑器行级标注)
      log    str            原始日志(失败时便于排查)
    """
    try:
        assemble_asm(asm_text, ref_dxbc=ref_dxbc)
        return {"ok": True, "errors": [], "log": ""}
    except Exception as e:  # noqa: BLE001
        log = str(e)
        return {"ok": False, "errors": parse_asm_errors(log), "log": log}


# ---------------------------------------------------------------- 可选: HLSL 混合翻译器预处理
# hlsl_blend_dxbc_translator: 把“DXBC asm + HLSL 标记”混合文本翻回纯 asm(非标记行透传)。
# 纯 asm 是它的子集 ⇒ 无需“切换逻辑”: 有 exe 就把编辑文本过一遍再汇编, 没有则直接用原 asm。
_TRANSLATOR_CANDS = (
    _bin("hlsl_blend_dxbc_translator.exe"),
    os.path.join(_REPO, "bin", "hlsl_blend_dxbc_translator.exe"),
)


def find_translator():
    """定位 hlsl_blend_dxbc_translator.exe; 缺失返回 None(纯 asm 仍可用)。

    可选功能。exe 不随仓(见 README 链接自行构建), 放入 bin/ 或用
    环境变量 HLSL_BLEND_TRANSLATOR_EXE 指定。
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
        inp = os.path.join(workdir, "in.asm")
        outp = os.path.join(workdir, "out.asm")
        with open(inp, "w", encoding="utf-8") as f:
            f.write(asm_text)
        args = [exe, "-input", inp, "-output", outp]
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
