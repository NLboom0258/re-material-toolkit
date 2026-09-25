#!/usr/bin/env python3
"""DXBC 输入/输出签名解析(ISGN/OSGN; SM5.1 的 ISG1/OSG1)。

签名 chunk 体 = `[u32 count][u32 8]` + count × 24B 元素 + 名字串;
元素 6×u32 = `name_off, semantic_index, system_value_type, component_type, register, mask_and_rwmask`;
`name_off` 相对**体首**(即元素数组之后紧跟名字)。

用途: 枚举各 pass 的 VS 顶点输入(ISGN) 与各 pass 输出(OSGN) —— 属“输入/输出边界”。
"""
import struct

# 仅用 SM5.0 的 ISGN/OSGN(元素 24B, name_off 相对体首)。
# ⚠ SM5.1 的 ISG1/OSG1 元素更大(体 228/count 6 ⇒ 需 32B/元素), 本项目暂不解析(避免乱码)。
_IN_TAGS = ("ISGN",)
_OUT_TAGS = ("OSGN",)
_COMP = {0: "unknown", 1: "u32", 2: "s32", 3: "f32"}


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def chunks(blob):
    """[(tag, data_off), ...] (data_off = chunk 体开始处)。"""
    n = _u32(blob, 28)
    return [(blob[o:o + 4].decode("latin1"), o) for o in
            (_u32(blob, 32 + i * 4) for i in range(n))]


def _chunk_body(blob, tag):
    for t, o in chunks(blob):
        if t == tag:
            return blob[o + 8:o + 8 + _u32(blob, o + 4)]
    return None


def _cstr(b, o, n=64):
    if not (0 <= o < len(b)):
        return ""
    e = b.find(b"\x00", o)
    if e < 0 or e - o > n:
        e = min(o + n, len(b))
    return b[o:e].decode("latin1", "replace")


def parse_signature(blob, tags=_IN_TAGS):
    """返回 [(name, sem_index, sysval, comp_type_str, register, mask, rwmask), ...]; 无签名则 []。"""
    body = None
    for t in tags:
        body = _chunk_body(blob, t)
        if body is not None:
            break
    if body is None or len(body) < 8:
        return []
    cnt = _u32(body, 0)
    out = []
    for i in range(cnt):
        p = 8 + i * 24
        if p + 24 > len(body):
            break
        name = _cstr(body, _u32(body, p))
        sem = _u32(body, p + 4)
        sysval = _u32(body, p + 8)
        comp = _u32(body, p + 12)
        reg = _u32(body, p + 16)
        maskrw = _u32(body, p + 20)
        out.append((name, sem, sysval, _COMP.get(comp, "?"), reg,
                    maskrw & 0xFF, (maskrw >> 8) & 0xFF))
    return out


def input_signature(blob):
    return parse_signature(blob, _IN_TAGS)


def output_signature(blob):
    return parse_signature(blob, _OUT_TAGS)


def sig_key(sig):
    """把签名压成可分组键: ((name, sem, reg, mask), ...)。"""
    return tuple((n, s, r, m) for (n, s, _sv, _c, r, m, _rw) in sig)


def sig_str(sig):
    """人类可读: `NORMAL#0:r1.f TEXCOORD#0:r4.3`。"""
    return " ".join("%s#%d:r%d.%x" % (n or "<unnamed>", s, r, m)
                    for (n, s, _sv, _c, r, m, _rw) in sig)
