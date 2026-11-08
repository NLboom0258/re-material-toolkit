#!/usr/bin/env python3
"""RE Engine 字符串哈希(参数名/资源名/材质名)。

实测结论(DMC5/RE Engine):
  - 参数名/贴图名用 UTF-8(ASCII) 字节的 murmur3(seed=0xFFFFFFFF);
  - 材质名用 UTF-16LE 字节的 murmur3(同名不同结果)。

mmtr 参数表与 mdf2 的 ASCIIHash 字段一致,均用 ascii_hash。
"""
import struct

MURMUR3_SEED = 0xFFFFFFFF


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 x86_32(标准实现)。"""
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & 0xFFFFFFFF
    n = len(data) // 4
    for i in range(n):
        k = struct.unpack_from("<I", data, i * 4)[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    k = 0
    tail = data[n * 4:]
    if len(tail) == 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def ascii_hash(name: str) -> int:
    """参数名/资源名哈希(UTF-8 字节,seed=0xFFFFFFFF)。"""
    return murmur3_32(name.encode("utf-8"), MURMUR3_SEED)


def utf16_hash(name: str) -> int:
    """材质名哈希(UTF-16LE 字节,seed=0xFFFFFFFF)。"""
    return murmur3_32(name.encode("utf-16-le"), MURMUR3_SEED)


# ---------------------------------------------------------------- xxh64
# MHWS(mmtr 0x110)把每个 shader blob 的缓存键写成 8 字节小端 u64，
# 值 = XXH64(blob, seed=0xFFFFFFFF)。实测 600/600 个 blob 全中。
# 这里给纯 Python 实现，避免给工具链加 xxhash 依赖（写入侧要用它重算键）。
_XXH64_M = 0xFFFFFFFFFFFFFFFF
_XXH64_P1 = 0x9E3779B185EBCA87
_XXH64_P2 = 0xC2B2AE3D27D4EB4F
_XXH64_P3 = 0x165667B19E3779F9
_XXH64_P4 = 0x85EBCA77C2B2AE63
_XXH64_P5 = 0x27D4EB2F165667C5
XXH64_SEED = 0xFFFFFFFF


def _rotl64(x, r):
    return ((x << r) | (x >> (64 - r))) & _XXH64_M


def _xxh64_round(acc, inp):
    acc = (acc + inp * _XXH64_P2) & _XXH64_M
    acc = _rotl64(acc, 31)
    return (acc * _XXH64_P1) & _XXH64_M


def _xxh64_merge(acc, val):
    val = _xxh64_round(0, val)
    acc ^= val
    return (acc * _XXH64_P1 + _XXH64_P4) & _XXH64_M


def xxh64(data: bytes, seed: int = 0) -> int:
    """XXH64（纯 Python）。与 xxhash.xxh64_intdigest 逐位一致（已对拍）。"""
    d = memoryview(data)
    n = len(d)
    i = 0
    if n >= 32:
        v1 = (seed + _XXH64_P1 + _XXH64_P2) & _XXH64_M
        v2 = (seed + _XXH64_P2) & _XXH64_M
        v3 = seed & _XXH64_M
        v4 = (seed - _XXH64_P1) & _XXH64_M
        while i <= n - 32:
            v1 = _xxh64_round(v1, int.from_bytes(d[i:i + 8], "little"))
            v2 = _xxh64_round(v2, int.from_bytes(d[i + 8:i + 16], "little"))
            v3 = _xxh64_round(v3, int.from_bytes(d[i + 16:i + 24], "little"))
            v4 = _xxh64_round(v4, int.from_bytes(d[i + 24:i + 32], "little"))
            i += 32
        h = (_rotl64(v1, 1) + _rotl64(v2, 7) + _rotl64(v3, 12) + _rotl64(v4, 18)) & _XXH64_M
        h = _xxh64_merge(h, v1)
        h = _xxh64_merge(h, v2)
        h = _xxh64_merge(h, v3)
        h = _xxh64_merge(h, v4)
    else:
        h = (seed + _XXH64_P5) & _XXH64_M
    h = (h + n) & _XXH64_M
    while i <= n - 8:
        h ^= _xxh64_round(0, int.from_bytes(d[i:i + 8], "little"))
        h = (_rotl64(h, 27) * _XXH64_P1 + _XXH64_P4) & _XXH64_M
        i += 8
    if i <= n - 4:
        h ^= (int.from_bytes(d[i:i + 4], "little") * _XXH64_P1) & _XXH64_M
        h = (_rotl64(h, 23) * _XXH64_P2 + _XXH64_P3) & _XXH64_M
        i += 4
    while i < n:
        h ^= (d[i] * _XXH64_P5) & _XXH64_M
        h = (_rotl64(h, 11) * _XXH64_P1) & _XXH64_M
        i += 1
    h ^= h >> 33
    h = (h * _XXH64_P2) & _XXH64_M
    h ^= h >> 29
    h = (h * _XXH64_P3) & _XXH64_M
    h ^= h >> 32
    return h


def blob_cache_key(blob: bytes) -> int:
    """mmtr 里 blob 后的 8 字节缓存键（MHWS 用；seed 固定 0xFFFFFFFF）。"""
    return xxh64(blob, XXH64_SEED)


if __name__ == "__main__":
    # 自检:已知样本
    cases = {
        "EmissiveColor": 0x48E05A6F,
        "BaseColor": 0xC4395B1A,
        "Metallic": 0xF9078A81,
        "BaseMetalMap": 0xD85E0958,
        "WhitePtSrv": 0xB894677B,
    }
    ok = True
    for name, expect in cases.items():
        got = ascii_hash(name)
        flag = "OK " if got == expect else "BAD"
        if got != expect:
            ok = False
        print(f"[{flag}] ascii_hash({name!r}) = 0x{got:08X} (expect 0x{expect:08X})")
    print("self-check:", "PASS" if ok else "FAIL")
