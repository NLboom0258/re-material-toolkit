#!/usr/bin/env python3
"""DXIL 文本 IR 读写：反汇编 / 汇编 / 校验 / 容器片段 / 输出行替换。

COM 调用方式（IDxcLibrary / IDxcCompiler / IDxcAssembler / IDxcValidator /
IDxcContainerReflection）参考 REasy（MIT）的 ``file_handlers/sdf/dxc.py`` 与
``com.py``，这里只保留 DXIL 汇编编辑需要的部分，并去掉 REasy 的 Python 3.15
``lazy import``，使其可在 3.12 下运行。

**重要**：``assemble()`` 产出的是**新容器**（缺 ILDN、STAT 重算），直接换进 mmtr
会被引擎拒绝（表现为部分场景不渲染 / 纯黑）。写回必须走 :func:`replace_dxil`，
只替换原容器的 ``DXIL`` chunk，其余 chunk 原样保留。

依赖 ``dxcompiler.dll``（DXC 1.x），查找顺序：
  1. 环境变量 ``REASY_DXCOMPILER_PATH``
  2. ``<repo>/libs/dxcompiler.dll``
  3. Windows SDK ``bin/<ver>/x64/dxcompiler.dll``（取最高版本）

用法::

    text = disassemble(blob)                       # DXBC 容器 -> LLVM 文本 IR
    new  = patch_store_output(blob, rt=1, col=1, value=1.0)   # 改 GBuffer 某分量
    new2 = replace_dxil(blob, container_part(assemble(text), "DXIL"))
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
import uuid

from . import dxbc_container as DC

_POINTER_SIZE = ctypes.sizeof(ctypes.c_void_p)
HRESULT = ctypes.c_long
UINT32 = ctypes.c_uint32
LPVOID = ctypes.c_void_p
DXC_CP_UTF8 = 65001


class DxcError(ValueError):
    """DXC 调用或 DXIL 文本处理失败。"""


class DxcNotFound(DxcError):
    """找不到 dxcompiler.dll。"""


class GUID(ctypes.Structure):
    _fields_ = (("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8))

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


CLSID_DXC_LIBRARY = GUID.parse("6245d6af-66e0-48fd-80b4-4d271796748c")
IID_IDXC_LIBRARY = GUID.parse("e5204dc7-d18c-4c3c-bdfb-851673980fe7")
CLSID_DXC_COMPILER = GUID.parse("73e22d93-e6ce-47f3-b5bf-f0664f39c1b0")
IID_IDXC_COMPILER = GUID.parse("8c210bf3-011f-4422-8d70-6f9acb8db617")
CLSID_DXC_ASSEMBLER = GUID.parse("d728db68-f903-4f80-94cd-dccf76ec7151")
IID_IDXC_ASSEMBLER = GUID.parse("091f7a26-1c1f-4948-904b-e6e3a8a771d5")
CLSID_DXC_VALIDATOR = GUID.parse("8ca3e215-f728-4cf3-8cdd-88af917587a1")
IID_IDXC_VALIDATOR = GUID.parse("a6e82bd2-1fd7-4826-9811-2857e797f49a")
CLSID_DXC_CONTAINER_REFLECTION = GUID.parse("b9f54489-55b8-400c-ba3a-1675e4728b91")
IID_IDXC_CONTAINER_REFLECTION = GUID.parse("d2c21b26-8350-4bdc-976a-331ce6f4c54c")


# ------------------------------------------------------------------ COM 辅助
def com_method(interface, index, restype, *argtypes):
    if not interface.value:
        raise DxcError("NULL pointer access")
    vtable = ctypes.c_void_p.from_address(interface.value).value
    if not vtable:
        raise DxcError("NULL pointer access")
    address = ctypes.c_void_p.from_address(vtable + index * _POINTER_SIZE).value
    return _typed_method(address, restype, argtypes)


@lru_cache(maxsize=256)
def _typed_method(address, restype, argtypes):
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(address)


def com_release(interface):
    if interface is not None and interface.value:
        com_method(interface, 2, wintypes.ULONG)(interface)


def blob_bytes(pointer):
    """``pointer`` 是 IDxcBlob / ID3DBlob。"""
    if pointer is None or not pointer.value:
        return b""
    address = com_method(pointer, 3, ctypes.c_void_p)(pointer)
    size = int(com_method(pointer, 4, ctypes.c_size_t)(pointer))
    return ctypes.string_at(address, size) if address and size else b""


def _check(result, operation):
    if int(result) < 0:
        raise DxcError("%s failed with HRESULT 0x%08X" % (operation, int(result) & 0xFFFFFFFF))


def _operation_result(operation, failure_message, *, hresult=False, strip_nul=False):
    result_blob = error_blob = None
    try:
        status = HRESULT()
        _check(com_method(operation, 3, HRESULT, ctypes.POINTER(HRESULT))(
            operation, ctypes.byref(status)), "IDxcOperationResult::GetStatus")
        error_blob = LPVOID()
        _check(com_method(operation, 5, HRESULT, ctypes.POINTER(LPVOID))(
            operation, ctypes.byref(error_blob)), "IDxcOperationResult::GetErrorBuffer")
        errors = blob_bytes(error_blob)
        if strip_nul:
            errors = errors.rstrip(b"\0")
        diagnostics = errors.decode("utf-8", "replace").strip()
        if int(status.value) < 0:
            if hresult:
                failure_message += " with HRESULT 0x%08X" % (int(status.value) & 0xFFFFFFFF)
            raise DxcError(diagnostics or failure_message)
        result_blob = LPVOID()
        _check(com_method(operation, 4, HRESULT, ctypes.POINTER(LPVOID))(
            operation, ctypes.byref(result_blob)), "IDxcOperationResult::GetResult")
        return blob_bytes(result_blob), diagnostics
    finally:
        com_release(error_blob)
        com_release(result_blob)


# ------------------------------------------------------------------ 库定位
def _version_key(path):
    values = tuple(int(item) for item in re.findall(r"\d+", path.name))
    return values or (0,)


@lru_cache(maxsize=1)
def find_dxcompiler():
    """定位 dxcompiler.dll（优先环境变量，其次仓库 libs，最后 Windows SDK）。"""
    if sys.platform != "win32":
        raise DxcNotFound("DXIL assembly is available on Windows only")
    here = Path(__file__).resolve()
    repo = here.parents[2] if len(here.parents) > 2 else here.parent
    candidates = []
    configured = os.environ.get("REASY_DXCOMPILER_PATH", "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.append(repo / "libs" / "dxcompiler.dll")
    program_files = os.environ.get("ProgramFiles(x86)", "").strip()
    if program_files:
        kits = Path(program_files) / "Windows Kits" / "10"
        candidates.append(kits / "Redist" / "D3D" / "x64" / "dxcompiler.dll")
        binary_root = kits / "bin"
        if binary_root.is_dir():
            versions = sorted((p for p in binary_root.iterdir() if p.is_dir()),
                              key=_version_key, reverse=True)
            candidates.extend(p / "x64" / "dxcompiler.dll" for p in versions)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise DxcNotFound("dxcompiler.dll not found (set REASY_DXCOMPILER_PATH or put it in libs/)")


@lru_cache(maxsize=1)
def _library():
    path = find_dxcompiler()
    try:
        return ctypes.WinDLL(str(path))
    except OSError as exc:
        raise DxcError("could not load %s: %s" % (path, exc)) from exc


def dxc_available():
    try:
        find_dxcompiler()
        _library()
    except DxcError:
        return False
    return True


def _create(clsid, iid):
    create = _library().DxcCreateInstance
    create.argtypes = (ctypes.POINTER(GUID), ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))
    create.restype = HRESULT
    result = LPVOID()
    _check(create(ctypes.byref(clsid), ctypes.byref(iid), ctypes.byref(result)),
           "DxcCreateInstance")
    return result


def _source_blob(raw):
    library = _create(CLSID_DXC_LIBRARY, IID_IDXC_LIBRARY)
    pinned = ctypes.create_string_buffer(raw)
    blob = LPVOID()
    try:
        _check(com_method(library, 6, HRESULT, LPVOID, UINT32, UINT32,
                          ctypes.POINTER(LPVOID))(
            library, ctypes.cast(pinned, LPVOID), len(raw), DXC_CP_UTF8,
            ctypes.byref(blob)), "IDxcLibrary::CreateBlobWithEncodingFromPinned")
    except Exception:
        com_release(library)
        raise
    return library, blob, pinned


# ------------------------------------------------------------------ 基本操作
def disassemble(container):
    """DXBC 容器 / LLVM bitcode -> DXIL 文本 IR。"""
    raw = bytes(container)
    if raw[:4] not in {b"DXBC", b"BC\xc0\xde"}:
        raise DxcError("DXIL disassembly needs a DXBC container or LLVM bitcode")
    library = compiler = source = pinned = result = None
    try:
        library, source, pinned = _source_blob(raw)
        compiler = _create(CLSID_DXC_COMPILER, IID_IDXC_COMPILER)
        result = LPVOID()
        _check(com_method(compiler, 5, HRESULT, LPVOID, ctypes.POINTER(LPVOID))(
            compiler, source, ctypes.byref(result)), "IDxcCompiler::Disassemble")
        text = blob_bytes(result)
        if not text:
            raise DxcError("DXC returned an empty DXIL disassembly")
        return text.rstrip(b"\0").decode("utf-8")
    finally:
        _ = pinned
        for value in (result, compiler, source, library):
            com_release(value)


def assemble(text):
    """DXIL 文本 IR -> **新容器**（缺 ILDN，STAT 重算；写回请用 replace_dxil）。"""
    raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if not raw.strip() or (b"\0" in raw and not raw.startswith(b"BC\xc0\xde")):
        raise DxcError("DXIL assembly is empty or contains an embedded NUL")
    library = assembler = source = pinned = operation = None
    try:
        library, source, pinned = _source_blob(raw)
        assembler = _create(CLSID_DXC_ASSEMBLER, IID_IDXC_ASSEMBLER)
        operation = LPVOID()
        _check(com_method(assembler, 3, HRESULT, LPVOID, ctypes.POINTER(LPVOID))(
            assembler, source, ctypes.byref(operation)),
            "IDxcAssembler::AssembleToContainer")
        bytecode, _diagnostics = _operation_result(operation, "DXIL assembly failed",
                                                   hresult=True)
        if bytecode[:4] != b"DXBC":
            raise DxcError("DXC assembler did not return a DXIL container")
        return bytecode
    finally:
        _ = pinned
        for value in (operation, assembler, source, library):
            com_release(value)


def validate(container):
    """用 IDxcValidator 校验容器；失败抛 DxcError。"""
    library = validator = source = pinned = operation = None
    try:
        library, source, pinned = _source_blob(bytes(container))
        validator = _create(CLSID_DXC_VALIDATOR, IID_IDXC_VALIDATOR)
        operation = LPVOID()
        _check(com_method(validator, 3, HRESULT, LPVOID, UINT32,
                          ctypes.POINTER(LPVOID))(
            validator, source, 0, ctypes.byref(operation)), "IDxcValidator::Validate")
        _operation_result(operation, "DXIL runtime validation failed", strip_nul=True)
    finally:
        _ = pinned
        for value in (operation, validator, source, library):
            com_release(value)


def container_part(container, fourcc):
    """取容器内某个 chunk 的内容（如 "DXIL"）。"""
    raw = bytes(container)
    tag = fourcc.encode("ascii") if isinstance(fourcc, str) else bytes(fourcc)
    if raw[:4] != b"DXBC" or len(tag) != 4:
        raise DxcError("DXIL container parts need a DXBC container and four-byte tag")
    library = source = pinned = api = part = None
    try:
        library, source, pinned = _source_blob(raw)
        api = _create(CLSID_DXC_CONTAINER_REFLECTION, IID_IDXC_CONTAINER_REFLECTION)
        _check(com_method(api, 3, HRESULT, LPVOID)(api, source),
               "IDxcContainerReflection::Load")
        index = UINT32()
        _check(com_method(api, 7, HRESULT, UINT32, ctypes.POINTER(UINT32))(
            api, int.from_bytes(tag, "little"), ctypes.byref(index)),
            "IDxcContainerReflection::FindFirstPartKind(%s)" % tag.decode("ascii"))
        part = LPVOID()
        _check(com_method(api, 6, HRESULT, UINT32, ctypes.POINTER(LPVOID))(
            api, index.value, ctypes.byref(part)),
            "IDxcContainerReflection::GetPartContent")
        result = blob_bytes(part)
    finally:
        _ = pinned
        for value in (part, api, source, library):
            com_release(value)
    if not result:
        raise DxcError("DXIL container part %s is empty" % tag.decode("ascii"))
    return result


# ------------------------------------------------------------------ 高层编辑
def replace_dxil(orig_blob, new_dxil):
    """把 ``new_dxil`` 塞进 ``orig_blob``：只换 DXIL，其余 chunk 原样。

    这是写回 mmtr 的**唯一正确方式**（整容器替换会缺 ILDN，引擎拒收）。
    """
    return DC.replace(bytes(orig_blob), "DXIL", bytes(new_dxil))


STORE_OUTPUT_RE = re.compile(
    r"(storeOutput\.f32\(i32 5, i32 (\d+), i32 \d+, i8 (\d+), float )[^)]+(\))")


def _fmt_float(value):
    """LLVM IR 浮点常量。0/1 用十进制（可读），其余用 double 位模式（精度无损）。

    ``%.6e`` 这类十进制写法的精度不够时，DXIL 汇编器会报
    "floating point constant invalid for type"。
    """
    value = float(value)
    if value == 0.0:
        return "0.000000e+00"
    if value == 1.0:
        return "1.000000e+00"
    import struct
    # 先截到 float32，再写成 double 位模式：LLVM 要求 float 常量能被 float32 精确表示，
    # 否则 DXIL 汇编器报 "floating point constant invalid for type"。
    f32 = struct.unpack("<f", struct.pack("<f", value))[0]
    bits = struct.unpack("<Q", struct.pack("<d", f32))[0]
    return "0x%016X" % bits


def patch_store_output(blob, rt, col, value=None, *, expr=None):
    """把 ``SV_Target<rt>`` 的 ``col`` 分量改成常量 ``value`` 或文本 ``expr``。

    ``expr`` 给的是 IR 表达式（如 ``%42`` 或 ``0.5``），优先级高于 ``value``。
    返回新容器（已走 replace_dxil + validate）。
    """
    text = disassemble(blob)
    literal = expr if expr is not None else _fmt_float(value)
    pattern = re.compile(
        r"(storeOutput\.f32\(i32 5, i32 %d, i32 \d+, i8 %d, float )[^)]+(\))" % (rt, col))
    text, count = pattern.subn(lambda m: m.group(1) + literal + m.group(2), text)
    if not count:
        raise DxcError("no storeOutput for SV_Target%d col%d" % (rt, col))
    assembled = assemble(text)
    validate(assembled)
    return replace_dxil(blob, container_part(assembled, "DXIL"))


def patch_ir(blob, edits):
    """通用 IR 文本编辑。``edits`` 为 ``[(pattern, replacement), ...]`` 或 callable。

    pattern 可以是字符串（精确行替换）或编译好的正则；replacement 可以是字符串
    或接受 match 的函数。返回新容器（已走 replace_dxil + validate）。
    """
    text = disassemble(blob)
    if callable(edits):
        text = edits(text)
    else:
        for pattern, replacement in edits:
            if hasattr(pattern, "sub"):
                text = pattern.sub(replacement, text)
            else:
                if pattern not in text:
                    raise DxcError("IR edit target not found: %r" % (pattern[:60],))
                text = text.replace(pattern, replacement)
    assembled = assemble(text)
    validate(assembled)
    return replace_dxil(blob, container_part(assembled, "DXIL"))


__all__ = [
    "DxcError", "DxcNotFound", "dxc_available", "find_dxcompiler",
    "disassemble", "assemble", "validate", "container_part",
    "replace_dxil", "patch_store_output", "patch_ir",
]
