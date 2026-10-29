# -*- coding: utf-8 -*-
"""从 RE Engine pak 里按需提取单个资源（DMC5 实测）。

## 为什么需要

只有**部分**模型被解包到游戏根的 ``natives/x64``，其余（pl0800_01 头、pl0200 等）
还在 ``re_chunk_000.pak`` 里。预览需要一份**真实的 mdf2 文件**做副本，所以要么用户
自己解包，要么工具自己从 pak 取 —— 这里实现后者。

## 格式（依据 REE-Content-Editor 的 PakFile.cs / MurMur3HashUtils.cs）

pak 只存路径 hash、不存原始路径：

    pathHash = (murmur3_32(UTF16LE(路径大写)) << 32) | murmur3_32(UTF16LE(路径小写))

两者都是标准 murmur3 x86_32、seed = 0xFFFFFFFF。所以只要知道资源路径
（游戏内 ``/identity`` 就能给），就能反查 entry —— **不需要 listfile**。

v4 结构::

    header 16B : magic("KPKA") major(1) minor(1) flags(2) fileCount(4) fingerprint(4)
    entry  48B : pathHash(8) offset(8) csize(8) dsize(8) attributes(8) checksum(8)
                 attributes: compression = &0xf (0=None 1=Deflate 2=Zstd)
                             encryption  = >>16 & 0xff
                             offsetType  = >>24 & 0xf  (0=直接用 offset 1=chunk 表)

DMC5 实测 ``flags = 0``：entry 表紧跟 header、不加密、无 chunk 表、无额外字段。

## pak 链

``re_chunk_000.pak`` 是主体，``re_chunk_000.pak.patch_001.pak`` … 依次**覆盖**，
序号大的赢（实测 ``pl0800_01.mdf2.10`` 只存在于 patch_005）。
"""
from __future__ import annotations

import os
import re
import struct
import zlib

from .hashes import MURMUR3_SEED, murmur3_32

MAGIC = 0x414B504B          # "KPKA"
_HEADER = struct.Struct("<IBBhII")
_ENTRY = struct.Struct("<QqqqQq")

COMP_NONE, COMP_DEFLATE, COMP_ZSTD = 0, 1, 2


def pak_path_hash(path: str) -> int:
    """文件路径 -> pak 里的 64 位 hash（低 32 = 小写，高 32 = 大写）。"""
    low = murmur3_32(path.lower().encode("utf-16-le"), MURMUR3_SEED)
    high = murmur3_32(path.upper().encode("utf-16-le"), MURMUR3_SEED)
    return (high << 32) | low


class PakEntry:
    __slots__ = ("offset", "csize", "dsize", "compression", "encryption", "offset_type")

    def __init__(self, offset, csize, dsize, attributes):
        self.offset = offset
        self.csize = csize
        self.dsize = dsize
        self.compression = attributes & 0xF
        self.encryption = (attributes >> 16) & 0xFF
        self.offset_type = (attributes >> 24) & 0xF

    def __repr__(self):
        return ("Entry(off=%d c=%d/%d comp=%d enc=%d ot=%d)"
                % (self.offset, self.csize, self.dsize,
                   self.compression, self.encryption, self.offset_type))


class PakArchive:
    def __init__(self, path):
        self.path = path
        self.entries = {}
        self.error = None
        try:
            self._load()
        except Exception as e:                      # 损坏 / 不认识的 pak 直接跳过
            self.error = "%s: %s" % (type(e).__name__, e)

    def _load(self):
        size = os.path.getsize(self.path)
        with open(self.path, "rb") as f:
            head = f.read(16)
            if len(head) < 16:
                raise ValueError("文件太小")
            magic, major, minor, flags, count, _fp = _HEADER.unpack(head)
            if magic != MAGIC:
                raise ValueError("不是 pak（magic=%08X）" % magic)
            if major != 4:
                raise ValueError("只支持 v4（实际 v%d.%d）" % (major, minor))
            if flags != 0:
                # 有额外字段 / entry 表加密 / chunk 表时需要另写解析，先明确报错
                raise ValueError("pak feature flags=%d 暂不支持" % flags)
            if count <= 0:
                return
            need = count * _ENTRY.size
            blob = f.read(need)
            if len(blob) < need:
                raise ValueError("entry 表不完整（%d/%d）" % (len(blob), need))
        for i in range(count):
            ph, off, csz, dsz, attr, _chk = _ENTRY.unpack_from(blob, i * _ENTRY.size)
            if ph:
                self.entries[ph] = PakEntry(off, csz, dsz, attr)

    def read(self, entry: PakEntry) -> bytes:
        if entry.encryption != 0:
            raise RuntimeError("pak entry 加密（type=%d），暂不支持" % entry.encryption)
        if entry.offset_type != 0:
            raise RuntimeError("pak entry 使用 chunk 表，暂不支持")
        with open(self.path, "rb") as f:
            f.seek(entry.offset)
            raw = f.read(entry.csize)
        if len(raw) < entry.csize:
            raise RuntimeError("读取不完整（%d/%d）" % (len(raw), entry.csize))
        return _decompress(raw, entry)


def _decompress(raw: bytes, entry: PakEntry) -> bytes:
    if entry.compression == COMP_NONE:
        return raw
    if entry.compression == COMP_DEFLATE:
        return zlib.decompress(raw, -15)            # 裸 deflate
    if entry.compression == COMP_ZSTD:
        try:
            import zstandard
        except ImportError:
            raise RuntimeError(
                "该资源是 zstd 压缩的，需要 zstandard 包：\n"
                "    python -m pip install zstandard")
        return zstandard.ZstdDecompressor().decompress(raw, max_output_size=entry.dsize)
    raise RuntimeError("未知压缩类型 %d" % entry.compression)


# ---------------------------------------------------------------- 资源路径

_ARCH_CACHE = {}


def _pak_files(game_root):
    """按**优先级升序**返回 pak 文件（主 pak 在前，patch 序号大的在后）。"""
    root = os.path.abspath(game_root)
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        low = n.lower()
        if not low.endswith(".pak"):
            continue
        if not low.startswith("re_chunk_"):
            continue
        out.append(os.path.join(root, n))
    def _key(p):
        m = re.search(r"patch_(\d+)", os.path.basename(p).lower())
        return int(m.group(1)) if m else -1
    out.sort(key=_key)
    return out


def archives(game_root):
    """返回 [{archive}]，按优先级升序；带缓存（pak 集合/mtime 变了才重读）。"""
    root = os.path.abspath(game_root)
    files = _pak_files(root)
    sig = []
    for p in files:
        try:
            st = os.stat(p)
            sig.append((os.path.basename(p), st.st_size, int(st.st_mtime)))
        except OSError:
            sig.append((os.path.basename(p), 0, 0))
    sig = tuple(sig)
    cached = _ARCH_CACHE.get(root)
    if cached and cached[0] == sig:
        return cached[1]
    archs = [PakArchive(p) for p in files]
    _ARCH_CACHE[root] = (sig, archs)
    return archs


# 各类资源在 pak 里的版本后缀（实测：mdf2 用 ``.10``，mmtr 用 ``.1808168797``）。
# 猜错就查不到 —— 所以按类型给候选，逐个试（见 ``to_pak_paths``）。
_VERSION_SUFFIX = {
    ".mdf2": (".10",),
    ".mmtr": (".1808168797", ".10"),
    ".tex": (".10",),
    ".mesh": (".10",),
}
# 已经带版本后缀：不再补
_HAS_VERSION = (".10", ".1808168797")


def _strip_natives(rel: str) -> str:
    s = (rel or "").replace("\\", "/").strip()
    low = s.lower()
    for pre in ("natives/x64/", "natives/stm/", "natives/"):
        if low.startswith(pre):
            s = s[len(pre):]
            break
    return s.lstrip("/")


def to_pak_paths(rel: str):
    """资源路径 → pak 路径**候选列表**（按可能性排序）。

    只猜一种后缀不够：``.mdf2`` 是 ``X.mdf2.10``，``.mmtr`` 却是
    ``X.mmtr.1808168797``（实测 DMC5）。猜错就静默查不到，所以给候选逐个试。
    """
    s = _strip_natives(rel)
    if not s:
        return []
    low = s.lower()
    out = []
    if low.endswith(_HAS_VERSION):
        out.append(s)
    else:
        vers = None
        for ext, cand in _VERSION_SUFFIX.items():
            if low.endswith(ext):
                vers = cand
                break
        if vers:
            out.extend(s + v for v in vers)
        else:
            # 没带已知扩展名：按 mdf2 处理（向后兼容旧调用）
            out.extend([s + ".mdf2.10", s + ".10"])
    return ["natives/x64/" + x for x in out]


def to_pak_path(rel: str) -> str:
    """主候选路径（仅用于显示 / 兼容旧调用）。查文件请用 ``find_entry``。"""
    cands = to_pak_paths(rel)
    return cands[0] if cands else ""


def find_entry(game_root, rel):
    """按资源路径查 entry。返回 ``(archive, entry)`` 或 ``None``。

    多个候选后缀逐个试；同一个 hash 可能同时出现在主 pak 和 patch 里 ——
    取**优先级最高**（序号最大）的。
    """
    archs = archives(game_root)
    for p in to_pak_paths(rel):
        h = pak_path_hash(p)
        found = None
        for arch in archs:
            e = arch.entries.get(h)
            if e is not None:
                found = (arch, e)                 # 后面的覆盖前面的
        if found is not None:
            return found
    return None


def read_resource(game_root, rel) -> bytes | None:
    """读一个资源的原始字节（自动解压）。找不到返回 None。"""
    hit = find_entry(game_root, rel)
    if hit is None:
        return None
    arch, entry = hit
    return arch.read(entry)


def archive_summary(game_root):
    """诊断用：每个 pak 的版本 / entry 数 / 错误。"""
    out = []
    for a in archives(game_root):
        out.append({
            "path": os.path.basename(a.path),
            "entries": len(a.entries),
            "error": a.error,
        })
    return out
