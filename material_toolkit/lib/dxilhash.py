#!/usr/bin/env python3
"""Microsoft DxilHash(retail/debug 变体, 基于 MD5) —— 与 tools/mmtr_dxilhash.py 同源。
blob 内容变化后必须用它重算 16 字节指纹(否则 D3D CreatePixelShader E_INVALIDARG)。
"""
import struct


def _rol(x, n):
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


_S = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
_K = [0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
      0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
      0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
      0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
      0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
      0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
      0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
      0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391]


def _md5_block(state, x):
    a, b, c, d = state
    for i in range(64):
        if i < 16:
            f = (b & c) | ((~b) & d); g = i
        elif i < 32:
            f = (b & d) | (c & (~d)); g = (5 * i + 1) % 16
        elif i < 48:
            f = b ^ c ^ d; g = (3 * i + 5) % 16
        else:
            f = c ^ (b | (~d)); g = (7 * i) % 16
        f = (f + a + _K[i] + x[g]) & 0xFFFFFFFF
        a, d, c = d, c, b
        b = (b + _rol(f, _S[i])) & 0xFFFFFFFF
    return ((state[0] + a) & 0xFFFFFFFF, (state[1] + b) & 0xFFFFFFFF,
            (state[2] + c) & 0xFFFFFFFF, (state[3] + d) & 0xFFFFFFFF)


_PAD = bytes([0x80]) + bytes(63)


def dxil_hash(data, mode="retail"):
    """Microsoft DxilHash retail/debug 变体(MD5-based), 返回 16 字节摘要。"""
    n = len(data)
    leftOver = n & 0x3F
    if leftOver < 56:
        padAmt = 56 - leftOver; two = False
    else:
        padAmt = 120 - leftOver; two = True
    state = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476]
    numBlocks = (n + padAmt + 8) >> 6
    offset = 0
    nextEnd = numBlocks - 2 if two else numBlocks - 1
    for i in range(numBlocks):
        if i == nextEnd:
            if not two:
                rem = n - offset
                xb = bytearray(64)
                if mode == "retail":
                    struct.pack_into("<I", xb, 0, (n << 3) & 0xFFFFFFFF)
                    xb[4:4 + rem] = data[offset:offset + rem]
                    xb[4 + rem:4 + rem + padAmt] = _PAD[:padAmt]
                    struct.pack_into("<I", xb, 60, (1 | (n << 1)) & 0xFFFFFFFF)
                else:
                    struct.pack_into("<I", xb, 0, ((n << 4) | 0xF) & 0xFFFFFFFF)
                    xb[4:4 + rem] = data[offset:offset + rem]
                    xb[4 + rem:4 + rem + padAmt] = _PAD[:padAmt]
                    struct.pack_into("<I", xb, 60, ((n << 2) | 0x10000000) & 0xFFFFFFFF)
            else:
                if i == numBlocks - 2:
                    rem = n - offset
                    xb = bytearray(64)
                    xb[:rem] = data[offset:offset + rem]
                    xb[rem:rem + padAmt - 56] = _PAD[:padAmt - 56]
                    nextEnd = numBlocks - 1
                else:
                    xb = bytearray(64)
                    if mode == "retail":
                        struct.pack_into("<I", xb, 0, (n << 3) & 0xFFFFFFFF)
                        xb[4:60] = _PAD[padAmt - 56:padAmt]
                        struct.pack_into("<I", xb, 60, (1 | (n << 1)) & 0xFFFFFFFF)
                    else:
                        struct.pack_into("<I", xb, 0, ((n << 4) | 0xF) & 0xFFFFFFFF)
                        xb[4:60] = _PAD[padAmt - 56:padAmt]
                        struct.pack_into("<I", xb, 60, ((n << 2) | 0x10000000) & 0xFFFFFFFF)
            x = list(struct.unpack_from("<16I", xb, 0))
        else:
            x = list(struct.unpack_from("<16I", data, offset))
        state = list(_md5_block(state, x))
        offset += 64
    return struct.pack("<4I", *state)
