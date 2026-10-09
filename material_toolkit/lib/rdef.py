#!/usr/bin/env python3
"""RDEF 编辑 + blob 放回(供贴图槽新增使用)。

- add_bound_resource(blob, name, slot): 给 blob 的 RDEF 末尾(纹理组、cbuffer 之前)加一条
  bound resource(texture2d), 并重建 blob(RDEF size / chunk 偏移 / DXBC size / 指纹)。
  ⚠ 关键: 必须同步 variable 描述符的 field[4](默认值偏移), 否则 D3DReflect 报 E_INVALIDARG
  → 引擎反射失败 → 材质整片黑。
- replace_blob(data, blob_idx, new_blob): 把第 blob_idx 个 blob 换为新内容(长度可变),
  重映射头部所有 "blob 起点 / blob 大小" 引用。文件头 blob_start 不变(头部未改)。
"""
import struct
try:
    from .dxilhash import dxil_hash
except ImportError:  # 直接运行本文件时
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dxilhash import dxil_hash

TYPE_TEXTURE = 2
DIM_TEXTURE2D = 4
RET_FLOAT4 = 5
FLAGS_TEX = 0xC
NSAMP = 0xFFFFFFFF


def _br_stride(target):
    """RDEF bound-resource 条目步长: SM5.0=32 字节; **SM5.1=40 字节**(每条 entry 额外 8 字节: space/ID)。

    ⚠ 不区分会导致 SM5.1(DX12) blob 的资源名偏移错位(乱码)。cbuffer 定义步长不受影响(恒 24)。
    """
    return 40 if (target & 0xFFFF) == 0x0501 else 32


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def blob_list(data):
    bs = _u32(data, 8)
    out = []
    off = bs
    while off + 4 <= len(data) and data[off:off + 4] == b"DXBC":
        size = _u32(data, off + 24)
        out.append((off, size))
        off += size
    return bs, out


def _cstr(b, o, n=64):
    if not (0 <= o < len(b)):
        return ""
    e = b.find(b"\x00", o)
    if e < 0 or e - o > n:
        e = min(o + n, len(b))
    return b[o:e].decode("latin1", "replace")


def rdef_resources(blob):
    """解析 DXBC 的 RDEF, 返回 (cb, smp, srv) 三个**名字集合**; 无 RDEF/非法返回 None。

    cb/smp/srv 按 bound resource 的 **type** 分桶: 0=cbuffer, 3=sampler, 其余=SRV/其它。
    ⚠ 这是 mmtr 记录计数/打包字段(+0xa8/+0xac/+0xb4/+0xb8/...)的口径(见
      analysis/mmtr_record_fields.md): 须用"bound resource 按 type 分桶", **不是** RDEF 头 n_cb。
    """
    try:
        if len(blob) < 32 or blob[:4] != b"DXBC":
            return None
        n = _u32(blob, 28)
        if n > 16:
            return None
        rdef_ci = None
        for i in range(n):
            co = _u32(blob, 32 + i * 4)
            if blob[co:co + 4] == b"RDEF":
                rdef_ci = co
                break
        if rdef_ci is None:
            return None
        cs = _u32(blob, rdef_ci + 4)
        r = blob[rdef_ci + 8:rdef_ci + 8 + cs]
        _n_cb, _cb_off, n_br, br_off, _t = struct.unpack_from("<IIIII", r, 0)
        st = _br_stride(_t)
        cb, smp, srv = set(), set(), set()
        for k in range(n_br):
            p = br_off + k * st
            t = _u32(r, p + 4)
            nm = _cstr(r, _u32(r, p))
            (cb if t == 0 else smp if t == 3 else srv).add(nm)
        return cb, smp, srv
    except Exception:
        return None


def rdef_bind_info(blob):
    """RDEF bound resources 按数组序 -> [(name, type, bind_point, dim, ret), ...]; 无 RDEF 返回 None。

    用于由 RDEF 生成 mmtr 尾段描述符条目(见 analysis/pass_matrix.md §10)。
    """
    r = _rdef_of(blob)
    if r is None:
        return None
    try:
        _n_cb, _cb_off, n_br, br_off, _t = struct.unpack_from("<IIIII", r, 0)
    except Exception:
        return None
    st = _br_stride(_t)
    out = []
    for k in range(n_br):
        p = br_off + k * st
        out.append((_cstr(r, _u32(r, p)), _u32(r, p + 4), _u32(r, p + 20),
                    _u32(r, p + 12), _u32(r, p + 8)))
    return out


def rdef_bind_raw(blob):
    """RDEF bound resources 原始字段 -> [(name,type,ret,dim,nsamp,bind,count,flags), ...]。

    供"复制资源全部信息(跨文件粘贴)"用; SM5.1(40B/条)布局不同, 返回 None。
    """
    r = _rdef_of(blob)
    if r is None:
        return None
    try:
        _n_cb, _cb_off, n_br, br_off, _t = struct.unpack_from("<IIIII", r, 0)
    except Exception:
        return None
    st = _br_stride(_t)
    if st != 32:
        return None
    out = []
    for k in range(n_br):
        p = br_off + k * st
        out.append((_cstr(r, _u32(r, p)), _u32(r, p + 4), _u32(r, p + 8),
                    _u32(r, p + 12), _u32(r, p + 16), _u32(r, p + 20),
                    _u32(r, p + 24), _u32(r, p + 28)))
    return out


def rdef_bind_order(blob):
    """RDEF bound resources 按**数组顺序**分桶 -> {"cb":[name...], "smp":[...], "tex":[...]} 保序。

    分桶按 D3D11_SIT type: cb=0, smp=3, tex=SRV 类 {1(tbuffer),2(texture),5(structured),
    7(byteaddress)}; **UAV 类 {4,6,8,9,10,11} 丢弃**(尾段池只收 SRV, 不收 UAV)。
    用途: mmtr 尾段"绑定组"(池名列表)可由 PS/VS 的 RDEF 派生 —— 规则 = VS序 ++ PS序 按类去重
    (见 analysis/pass_matrix.md §10)。无 RDEF 返回 None。
    """
    r = _rdef_of(blob)
    if r is None:
        return None
    try:
        _n_cb, _cb_off, n_br, br_off, _t = struct.unpack_from("<IIIII", r, 0)
    except Exception:
        return None
    out = {"cb": [], "smp": [], "tex": []}
    st = _br_stride(_t)
    for k in range(n_br):
        p = br_off + k * st
        nm = _cstr(r, _u32(r, p))
        t = _u32(r, p + 4)
        if t == 0:
            out["cb"].append(nm)
        elif t == 3:
            out["smp"].append(nm)
        elif t in (1, 2, 5, 7):          # SRV 类(tbuffer/texture/structured/byteaddress)
            out["tex"].append(nm)
        # 其余(UAV 4/6/8/9/10/11)不入池
    return out


def rdef_uav_names(blob):
    """RDEF 里 **UAV** 的名字集合 (bound resource type ∈ {4,6,7,8,9,10} = RWTYPED/
    RWSTRUCTURED/RWBYTEADDRESS/APPEND/CONSUME/RWSTRUCTURED_WITH_COUNTER)。

    用途: 判别"UAV-only"着色器(如 Pick PS: 除 cb 外只有 UAV) —— 其 mmtr 记录计数与
    "非 cb/smp 即 SRV"的口径不一致(见 analysis/mmtr_record_fields.md)。无 RDEF 返回空集。
    """
    _UAV = (4, 6, 7, 8, 9, 10)
    r = _rdef_of(blob)
    if r is None:
        return set()
    try:
        _n_cb, _cb_off, n_br, br_off, _t = struct.unpack_from("<IIIII", r, 0)
    except Exception:
        return set()
    out = set()
    st = _br_stride(_t)
    for k in range(n_br):
        p = br_off + k * st
        if _u32(r, p + 4) in _UAV:
            out.add(_cstr(r, _u32(r, p)))
    return out


# RDEF target(profile token) -> stage (逆向 DMC5 exe; 见 mmtr_blobs.TARGET_STAGE)
# 低16位=版本(major<<8|minor): ...0500=SM5.0(DX11) / ...0501=SM5.1(DX12)。
# 高16位=类型 token(ASCII 大类; VS/PS 为特殊码): GS='GS' HS='HS' DS='DS' CS='CS'。
TARGET_STAGE = {
    0xFFFE0500: "VS", 0xFFFF0500: "PS", 0x47530500: "GS",
    0x48530500: "HS", 0x44530500: "DS", 0x43530500: "CS",
    0xFFFE0501: "VS", 0xFFFF0501: "PS", 0x47530501: "GS",
    0x48530501: "HS", 0x44530501: "DS", 0x43530501: "CS",
}


def _rdef_of(blob):
    """返回 RDEF 区 bytes(不含 'RDEF'+size 头), 无则 None。"""
    if len(blob) < 32 or blob[:4] != b"DXBC":
        return None
    n = _u32(blob, 28)
    for i in range(n):
        co = _u32(blob, 32 + i * 4)
        if blob[co:co + 4] == b"RDEF":
            return blob[co + 8:co + 8 + _u32(blob, co + 4)]
    return None


def rdef_stage(blob):
    """从 RDEF 头 target 解析 shader stage('VS'/'PS'/'CS'…) 或 None。"""
    r = _rdef_of(blob)
    if r is None or len(r) < 20:
        return None
    target = _u32(r, 16)
    return TARGET_STAGE.get(target)


def rdef_cbuffers(blob):
    """解析 RDEF 的 cbuffer 定义 -> [(name, size, [(member, start, size), ...]), ...]; 无 RDEF 则 None。

    cbuffer 定义 24B/条(name_off/var_count/var_off/size/flags/type); 成员 40B/条(name_off/start/size/…)。
    ⚠ 仅供“输入边界”枚举/比对(引擎固定 cbuffer 的成员布局跨文件恒定)。
    """
    r = _rdef_of(blob)
    if r is None:
        return None
    n_cb, cb_off, _n_br, _br_off, _t = struct.unpack_from("<IIIII", r, 0)
    out = []
    for k in range(n_cb):
        p = cb_off + k * 24
        name = _cstr(r, _u32(r, p))
        vc = _u32(r, p + 4)
        vo = _u32(r, p + 8)
        size = _u32(r, p + 12)
        mem = [( _cstr(r, _u32(r, vo + v * 40)), _u32(r, vo + v * 40 + 4),
                _u32(r, vo + v * 40 + 8)) for v in range(vc)]
        out.append((name, size, mem))
    return out


def add_bound_resource(blob: bytes, name: str, slot: int, type_=TYPE_TEXTURE,
                       dim=DIM_TEXTURE2D, ret=RET_FLOAT4, flags=FLAGS_TEX,
                       nsamp=NSAMP):
    """给 RDEF bound resource 数组插入一条(texture2d, slot), 返回新 blob bytes。"""
    blob = bytearray(blob)
    n = _u32(blob, 28)
    ofs = [_u32(blob, 32 + i * 4) for i in range(n)]
    rdef_ci = next((co for co in ofs if blob[co:co + 4] == b"RDEF"), None)
    if rdef_ci is None:
        raise ValueError("blob has no RDEF")
    cs = _u32(blob, rdef_ci + 4)
    rdef = bytearray(blob[rdef_ci + 8:rdef_ci + 8 + cs])

    n_cb, cb_off, n_br, br_off, target = struct.unpack_from("<IIIII", rdef, 0)
    # 插入点: 第一个 cbuffer(名 type=0)之前, 保持 sampler->texture->cbuffer 顺序
    first_cb = n_br
    for k in range(n_br):
        if _u32(rdef, br_off + k * 32 + 4) == 0:
            first_cb = k
            break
    ins = br_off + first_cb * 32
    name_pos = _u32(rdef, br_off + first_cb * 32) if first_cb < n_br else br_off + n_br * 32
    nb = name.encode("ascii") + b"\x00"
    LD = len(nb)
    new_name_off = name_pos + 32
    entry = struct.pack("<IIIIIIII", new_name_off, type_, ret, dim, nsamp, slot, 1, flags)

    out = bytearray()
    out += rdef[:ins]
    out += entry
    out += rdef[ins:name_pos]
    out += nb
    out += rdef[name_pos:]

    def delta(v):
        if v >= name_pos:
            return 32 + LD
        if v >= ins:
            return 32
        return 0

    def shift(fo):
        v = _u32(out, fo)
        d = delta(v)
        if d:
            struct.pack_into("<I", out, fo, v + d)

    shift(4)     # ConstantBufferOffset
    shift(24)    # CreatorOffset
    for k in range(n_br):
        op = br_off + k * 32
        shift(op if k < first_cb else op + 32)      # bound resource 名字
    cb_off2 = cb_off + 32 + LD                       # cbuffer 定义整体后移
    for k in range(n_cb):
        p = cb_off2 + k * 24
        shift(p)                                     # cbuffer 名字
        vc = _u32(out, p + 4)
        vo = _u32(out, p + 8)
        vo2 = vo + delta(vo)                         # varOff 也指向插入点之后
        struct.pack_into("<I", out, p + 8, vo2)
        for vv in range(vc):
            vp = vo2 + vv * 40
            shift(vp)            # 变量名
            shift(vp + 16)       # variable 描述符 field[4](默认值偏移)
    struct.pack_into("<I", out, 8, n_br + 1)         # bound resource 数量 +1

    # 重建 blob
    d = len(out) - cs
    new_blob = bytearray()
    new_blob += blob[:32]
    new_blob += b"\x00" * (n * 4)
    new_ofs = [co + d if co > rdef_ci else co for co in ofs]
    struct.pack_into("<" + "I" * n, new_blob, 32, *new_ofs)
    for co, sz in sorted((co, _u32(blob, co + 4)) for co in ofs):
        if co == rdef_ci:
            new_blob += b"RDEF" + struct.pack("<I", len(out)) + out
        else:
            new_blob += blob[co:co + 8 + sz]
    struct.pack_into("<I", new_blob, 24, len(new_blob))
    new_blob[4:20] = dxil_hash(bytes(new_blob[20:]), "retail")
    return bytes(new_blob)


def remove_bound_resource(blob, name):
    """从 RDEF 删除名为 name 的 bound resource(仅删 32B 条目; 名字串留作孤儿), 返回新 blob。

    镜象 add_bound_resource: 删条目后后续指针偏移 -32; n_br -1。仅支持 SM5.0(32B/条)。
    """
    blob = bytearray(blob)
    n = _u32(blob, 28)
    ofs = [_u32(blob, 32 + i * 4) for i in range(n)]
    rdef_ci = next((co for co in ofs if blob[co:co + 4] == b"RDEF"), None)
    if rdef_ci is None:
        raise ValueError("blob has no RDEF")
    cs = _u32(blob, rdef_ci + 4)
    rdef = bytearray(blob[rdef_ci + 8:rdef_ci + 8 + cs])
    n_cb, cb_off, n_br, br_off, target = struct.unpack_from("<IIIII", rdef, 0)
    if _br_stride(target) != 32:
        raise ValueError("仅支持 SM5.0 RDEF(32B/条)")
    idx = next((k for k in range(n_br)
                if _cstr(rdef, _u32(rdef, br_off + k * 32)) == name), None)
    if idx is None:
        raise ValueError("RDEF 未声明资源: %s" % name)
    rem = br_off + idx * 32
    out = bytearray()
    out += rdef[:rem]
    out += rdef[rem + 32:]

    def nd(v):
        return -32 if v >= rem + 32 else 0

    def nshift(fo):
        v = _u32(out, fo)
        d = nd(v)
        if d:
            struct.pack_into("<I", out, fo, v + d)

    nshift(4)      # ConstantBufferOffset
    nshift(24)     # CreatorOffset
    for k in range(n_br - 1):        # 其余 bound resource 名字
        nshift(br_off + k * 32)
    cb_off2 = _u32(out, 4)
    for k in range(n_cb):
        p = cb_off2 + k * 24
        nshift(p)                    # cbuffer 名字
        vc = _u32(out, p + 4)
        vo = _u32(out, p + 8)
        vo2 = vo + nd(vo)
        struct.pack_into("<I", out, p + 8, vo2)
        for vv in range(vc):
            vp = vo2 + vv * 40
            nshift(vp)               # 变量名
            nshift(vp + 16)          # variable 描述符 field[4]
    struct.pack_into("<I", out, 8, n_br - 1)   # bound resource 数量 -1

    # 重建 blob
    d = len(out) - cs
    new_blob = bytearray()
    new_blob += blob[:32]
    new_blob += b"\x00" * (n * 4)
    new_ofs = [co + d if co > rdef_ci else co for co in ofs]
    struct.pack_into("<" + "I" * n, new_blob, 32, *new_ofs)
    for co, sz in sorted((co, _u32(blob, co + 4)) for co in ofs):
        if co == rdef_ci:
            new_blob += b"RDEF" + struct.pack("<I", len(out)) + out
        else:
            new_blob += blob[co:co + 8 + sz]
    struct.pack_into("<I", new_blob, 24, len(new_blob))
    new_blob[4:20] = dxil_hash(bytes(new_blob[20:]), "retail")
    return bytes(new_blob)


def replace_blob(data: bytes, blob_idx: int, new_blob: bytes) -> bytes:
    """把第 blob_idx 个 blob 换为 new_blob(长度可变), 重映射头部引用。

    ⚠ 大小字段必须"按记录"更新, 不能按"值"全局替换(否则同尺寸 blob 互污, 实测 createPixelShader 崩)。
    记录的 "程序指针 + 字节码大小" 字段(逆向确证, 见 analysis/mmtr_record_fields.md):
      - PS: 指针 @ rec+0x00, 大小 @ rec+0x9c
      - VS: 指针 @ rec-0x20, 大小 @ rec+0x88 与 rec+0x8c(引擎对 VS 大小精确校验)
    故: 仅当某处 u32 == 某 blob 起点、且对应的大小字段恰等于该 blob 原大小时, 才更新之。
    """
    bs, bl = blob_list(data)
    if not (0 <= blob_idx < len(bl)):
        raise ValueError("blob idx out of range")
    orig_head = data[:bs]
    head = bytearray(orig_head)
    new_blobs = []
    old_to_new = {}          # old_start -> new_start
    changed = {}             # old_start -> (old_size, new_size) 仅变长/变短的 blob
    cursor = bs
    for i, (start, size) in enumerate(bl):
        raw = new_blob if i == blob_idx else data[start:start + size]
        sz = len(raw)
        new_blobs.append(raw)
        old_to_new[start] = cursor
        if sz != size:
            changed[start] = (size, sz)
        cursor += sz

    # 先按"原始头"检测所有写入位置, 再统一落盘(避免读改写互相干扰)
    # 大小字段按记录更新, 且区分 PS/VS:
    #   p 为 PS 指针(rec+0x00) ⇒ 大小在 p+0x9C
    #   p 为 VS 指针(rec-0x20) ⇒ 长度在 p+0xAC(=rec+0x8c), 另有 +0x88 p+0xA8(=rec+0x88)
    writes = []
    for p in range(0, len(orig_head) - 3, 4):
        w = _u32(orig_head, p)
        if w in old_to_new:
            writes.append((p, old_to_new[w]))                 # blob 起点引用
            if w in changed:
                old_size, new_size = changed[w]
                if p + 0x9C + 4 <= len(orig_head) and \
                        _u32(orig_head, p + 0x9C) == old_size:      # PS 大小
                    writes.append((p + 0x9C, new_size))
                if p + 0xAC + 4 <= len(orig_head) and \
                        _u32(orig_head, p + 0xAC) == old_size:      # VS 长度(+0x8c)
                    writes.append((p + 0xAC, new_size))
                    if _u32(orig_head, p + 0xA8) == old_size:       # +0x88(同值)
                        writes.append((p + 0xA8, new_size))
    for pos, val in writes:
        struct.pack_into("<I", head, pos, val)

    out = bytearray(head)
    for raw in new_blobs:
        out += raw
    return bytes(out)
