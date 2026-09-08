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
