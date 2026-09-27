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


def check_input_supported(ps_sig, vs_sig, ignore_sysval=True):
    """校验 "PS 输入 ⊆ VS 输出": 每个 PS 输入槽(语义名+索引)读的分量必须在 VS 输出里。

    D3D12 建 PSO 时会校验此项: 超标则整个材质 pass **被静默跳过**(不崩不报错); DX11 宽容。
    规则: 对每个 PS 输入元素, 其 mask 必须是 VS 同槽(同名+同语义索引)mask 的**子集**。
    系统值语义(SV_*, sysval!=0)恒可用, 默认跳过。名称比较**忽略大小写**
    (参考 PS 写 `SV_Position`, 参考 VS 写 `SV_POSITION`)。

    返回 [(name, sem_index, ps_mask, vs_mask)] (vs_mask=None = VS 无此槽); 空 = 合规。
    """
    vs = {}
    for (n, s, _sv, _c, _r, m, _rw) in vs_sig:
        vs[(n.upper(), s)] = m
    bad = []
    for (n, s, sv, _c, _r, m, _rw) in ps_sig:
        if ignore_sysval and sv:
            continue
        vm = vs.get((n.upper(), s))
        if m & ~(vm or 0):
            bad.append((n, s, m, vm))
    return bad
