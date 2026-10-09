#!/usr/bin/env python3
"""mmtr/SDF 容器「RDEF 驱动重建」(编辑写回)。

思路(用户方向 = 运行时语义级 + 导出时组装; 见 analysis/sdf_container_structure.md):
  - **没有运行时偏移拼接**: 解析出语义(每组 = RDEF 派生的资源声明) -> 改语义(编辑 RDEF)
    -> 导出时**按语义整体重组**容器(组/池/cbuffer/参数/描述符/字符串 全部重算)。
  - 与 mmtr_tail.rebuild_canonical 的区别: 组内容/描述符来自 **RDEF**(而非原池切片)
    => "改 RDEF -> 重建" 可让新增资源生效; 且允许尾段变长(自动重映射 blob 偏移)。

依赖主版本 0x01100004 布局; 非主版本返回 None/抛错(由调用方处理)。
"""
from __future__ import annotations

import struct

try:
    from .hashes import ascii_hash
    from .mmtr_tail import TailModel, _TEX_DIM_TYPE, _dedup
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hashes import ascii_hash
    from mmtr_tail import TailModel, _TEX_DIM_TYPE, _dedup

# 计数/打包字段: (record_counts 键, 记录内偏移, 位掩码 None=整 u32)
_CNT_FIELDS = (("a8", 0xA8, None), ("ac_low", 0xAC, 0xFFFF), ("b0", 0xB0, None),
               ("b4", 0xB4, None), ("b8", 0xB8, None), ("a4", 0xA4, None),
               ("c4", 0xC4, None))


def rebuild_from_rdef(data, blob_patch=None):
    """由 RDEF 驱动重建容器。blob_patch: {blob_idx: new_bytes} 先替换这些 blob。

    返回新文件 bytes; 非主版本/结构异常返回 None。
    """
    data = bytes(data)
    try:
        from .mmtr_build import (PT_LO, PT_N, PT_SIZE, REC_LO, REC_N, REC_SIZE,
                                 SKELETON_HI, record_counts)
        from .rdef import (rdef_bind_order, rdef_bind_info, rdef_cbuffers,
                           rdef_resources, blob_list)
    except ImportError:
        from mmtr_build import (PT_LO, PT_N, PT_SIZE, REC_LO, REC_N, REC_SIZE,
                                SKELETON_HI, record_counts)
        from rdef import (rdef_bind_order, rdef_bind_info, rdef_cbuffers,
                          rdef_resources, blob_list)

    _bs, bl = blob_list(data)
    if not bl:
        return None
    try:
        t = TailModel.decode(data)
    except ValueError:
        return None
    bnd = t.boundaries
    old_bs, str_start = bnd["blob_start"], bnd["string"]
    blob_bytes = [bytes(data[o:o + s]) for o, s in bl]
    if blob_patch:
        for idx, nb in blob_patch.items():
            if 0 <= idx < len(blob_bytes):
                blob_bytes[idx] = bytes(nb)
    off_index = {o: i for i, (o, _s) in enumerate(bl)}

    def u32(o):
        return struct.unpack_from("<I", data, o)[0]

    def name_at(v):
        if str_start <= v < old_bs:
            return _ascii(data, v)
        return None

    _bi, _cb, _bo = {}, {}, {}

    def bind_info(i):
        if i not in _bi:
            _bi[i] = {nm: (tt, bp, dim) for nm, tt, bp, dim, _r
                      in (rdef_bind_info(blob_bytes[i]) or [])}
        return _bi[i]

    def cbuffer_defs(i):
        if i not in _cb:
            d = {}
            for (nm, size, mem) in (rdef_cbuffers(blob_bytes[i]) or []):
                d.setdefault(nm, (size, len(mem),
                                  tuple((m[0], m[2], m[1]) for m in mem)))
            _cb[i] = d
        return _cb[i]

    def bind_order(i):
        if i not in _bo:
            o = rdef_bind_order(blob_bytes[i]) or {"cb": [], "smp": [], "tex": []}
            _bo[i] = (tuple(o["cb"]), tuple(o["smp"]), tuple(o["tex"]))
        return _bo[i]

    pa_by_off = {pe[0]: pe for pe in t.param_entries}
    stored_cb = {}
    for _e in t.cbuffer_entries:
        _mem = []
        for _j in range(_e["count"]):
            _pe = pa_by_off.get(_e["members_off"] + 16 * _j)
            if _pe is None:
                break
            _mem.append((_pe[5], _pe[3], _pe[4]))
        stored_cb.setdefault(_e["name"], (_e["size"], _e["count"], tuple(_mem)))

    def derived(vs_idx, ps_idx):
        """由 VS/PS RDEF(blob 下标, 可 None) 派生一个绑定组: cb 定义 + smp/tex 名 + 描述符条目。

        cb **定义**优先用容器存储的(稳定、decode 友好; P1 不改 cbuffer); 缺则退回 RDEF 解析。
        """
        vi = bind_info(vs_idx) if vs_idx is not None else {}
        pi = bind_info(ps_idx) if ps_idx is not None else {}
        vo = bind_order(vs_idx) if vs_idx is not None else ((), (), ())
        po = bind_order(ps_idx) if ps_idx is not None else ((), (), ())
        grp = {k: _dedup(list(vo[j]) + list(po[j]))
               for j, k in enumerate(("cb", "smp", "tex"))}
        cbd = dict(stored_cb)
        for i in (ps_idx, vs_idx):
            if i is None:
                continue
            for nm, v in cbuffer_defs(i).items():
                cbd.setdefault(nm, v)
        cbdefs = [(nm,) + cbd.get(nm, (0, 0, ())) for nm in grp["cb"]]
        desc = {}
        for kind in ("cb", "smp", "tex"):
            ent = []
            for nm in grp[kind]:
                v, p = vi.get(nm), pi.get(nm)
                stg = 0x11 if (v and p) else (0x01 if v else 0x10)
                u0 = v[1] if v else 0
                low = p[1] if p else 0
                if kind == "cb":
                    ty = 0xFF
                elif kind == "smp":
                    ty = 0x00
                else:
                    ty = _TEX_DIM_TYPE.get((v or p)[2], 0x02)
                ent.append((u0, (ty << 24) | (stg << 16) | low))
            desc[kind] = ent
        return {"cb": cbdefs, "smp": grp["smp"], "tex": grp["tex"], "desc": desc}

    slots = [(PT_LO + i * PT_SIZE, "pt", i) for i in range(PT_N)] + \
            [(REC_LO + i * REC_SIZE, "rec", i) for i in range(REC_N)]

    names, nset, groups, gorder, slot_gk = [], set(), {}, [], {}

    def use(nm):
        if nm and nm not in nset:
            nset.add(nm)
            names.append(nm)

    for base, kind, i in slots:
        for fo in (0xD8, 0x104):
            use(name_at(u32(base + fo)))
        if kind != "rec":            # PT=程序/InputLayout 表, 无绑定组
            continue
        vs_idx, ps_idx = off_index.get(u32(base - 0x20)), off_index.get(u32(base + 0x00))
        if vs_idx is None and ps_idx is None:
            continue
        g = derived(vs_idx, ps_idx)
        key = (tuple(g["cb"]), tuple(g["smp"]), tuple(g["tex"]))
        if key not in groups:
            groups[key] = g
            gorder.append(key)
            for d in g["cb"]:
                use(d[0])
                for (mn, _ms, _mo) in d[3]:
                    use(mn)
            for nm in g["smp"] + g["tex"]:
                use(nm)
        slot_gk[(kind, i)] = key
    use(name_at(u32(0x10)))

    smp_uniq, tex_uniq, cb_uniq, param_rel = {}, {}, {}, {}
    smp_order, tex_order, cb_order = [], [], []
    for gk in gorder:
        g = groups[gk]
        for key, uniq, order in ((tuple(g["smp"]), smp_uniq, smp_order),
                                 (tuple(g["tex"]), tex_uniq, tex_order),
                                 (tuple(g["cb"]), cb_uniq, cb_order)):
            if key not in uniq:
                uniq[key] = None
                order.append(key)

    param, param_name_at = bytearray(), []
    for ck in cb_order:
        for d in ck:
            if d in param_rel:
                continue
            param_rel[d] = len(param)
            for (mn, ms, mo) in d[3]:
                param_name_at.append((len(param), mn))
                param += struct.pack("<IIII", 0, 0, ascii_hash(mn), (ms << 16) | mo)

    cb, cb_name_at, cb_mem_at = bytearray(), [], []
    for ck in cb_order:
        cb_uniq[ck] = len(cb)
        for d in ck:
            cb_name_at.append((len(cb), d[0]))
            cb_mem_at.append((len(cb), d))
            cb += struct.pack("<QIIIIQ", 0, ascii_hash(d[0]), 0, d[1], d[2], 0)

    pool, pool_name_at = bytearray(), []
    for sk in smp_order:
        smp_uniq[sk] = len(pool)
        for nm in sk:
            pool_name_at.append((len(pool), nm))
            pool += struct.pack("<QII", 0, ascii_hash(nm), 0)
    for tk in tex_order:
        tex_uniq[tk] = len(pool)
        for nm in tk:
            pool_name_at.append((len(pool), nm))
            pool += struct.pack("<QII", 0, ascii_hash(nm), 0)

    # 描述符区: 4B 头 + 各**唯一段**([cb]/[smp]/[tex] 的条目序列按内容去重, 跨组共享);
    #   记录 +0x38/+0x48/+0x58 = 对应段起点。
    desc, seg_off = bytearray(b"\x00\x00\x00\x00"), {}

    def _seg(entries):
        key = tuple(entries)
        if key not in seg_off:
            seg_off[key] = len(desc)
            for (u0, code) in entries:
                desc.extend(struct.pack("<II", u0, code))
        return seg_off[key]

    desc_rel = {}
    for gk in gorder:
        desc_rel[gk] = {kind: _seg(groups[gk]["desc"][kind]) for kind in ("cb", "smp", "tex")}

    str_pool, str_rel = bytearray(), {}
    for nm in names:
        str_rel[nm] = len(str_pool)
        str_pool += nm.encode("latin1", "replace") + b"\x00"

    cb_rel = {gk: cb_uniq[tuple(groups[gk]["cb"])] for gk in gorder}
    smp_rel = {gk: smp_uniq[tuple(groups[gk]["smp"])] for gk in gorder}
    tex_rel = {gk: tex_uniq[tuple(groups[gk]["tex"])] for gk in gorder}

    pool_base = SKELETON_HI
    cb_base = pool_base + len(pool)
    param_base = cb_base + len(cb)
    desc_base = param_base + len(param)
    str_base = desc_base + len(desc)
    for off, nm in pool_name_at:
        struct.pack_into("<Q", pool, off, str_base + str_rel[nm])
    for off, nm in cb_name_at:
        struct.pack_into("<Q", cb, off, str_base + str_rel[nm])
    for off, d in cb_mem_at:
        struct.pack_into("<Q", cb, off + 24, param_base + param_rel[d])
    for off, nm in param_name_at:
        struct.pack_into("<I", param, off, str_base + str_rel[nm])

    new_tail = bytes(pool) + bytes(cb) + bytes(param) + bytes(desc) + bytes(str_pool)
    new_bs = SKELETON_HI + len(new_tail)

    new_off, cur = [], new_bs
    for b in blob_bytes:
        new_off.append(cur)
        cur += len(b)
    old_to_new = {bl[i][0]: new_off[i] for i in range(len(bl))}
    old_size = {bl[i][0]: bl[i][1] for i in range(len(bl))}
    new_size = {new_off[i]: len(blob_bytes[i]) for i in range(len(bl))}
    patched_old = {bl[i][0] for i in (blob_patch or {}) if 0 <= i < len(bl)}

    head = bytearray(data[:SKELETON_HI])
    struct.pack_into("<I", head, 0x08, new_bs)
    hnm = name_at(u32(0x10))
    if hnm in str_rel:
        struct.pack_into("<I", head, 0x10, str_base + str_rel[hnm])

    # blob 指针/大小 重映射(通用扫描; 大小字段按记录口径 +0x9C/+0xA8/+0xAC)
    for p in range(0, len(head) - 3, 4):
        v = u32(p)
        nv = old_to_new.get(v)
        if nv is None:
            continue
        struct.pack_into("<I", head, p, nv)
        osz, nsz = old_size.get(v), new_size.get(nv)
        if osz is None or nsz is None or osz == nsz:
            continue
        for so in (0x9C, 0xA8, 0xAC):
            sp = p + so
            if sp + 4 <= len(head) and u32(sp) == osz:
                struct.pack_into("<I", head, sp, nsz)

    # 尾段指针 + 记录计数(仅对"用到被改 blob"的槽重算)
    for base, kind, i in slots:
        gk = slot_gk.get((kind, i))
        if gk is not None:
            for fo, drel in ((0x38, "cb"), (0x48, "smp"), (0x58, "tex")):
                struct.pack_into("<I", head, base + fo, desc_base + desc_rel[gk][drel])
            struct.pack_into("<I", head, base + 0x40, cb_base + cb_rel[gk])
            struct.pack_into("<I", head, base + 0x50, pool_base + smp_rel[gk])
            struct.pack_into("<I", head, base + 0x60, pool_base + tex_rel[gk])
        for fo in (0xD8, 0x104):
            v = u32(base + fo)
            nm = name_at(v)
            if nm in str_rel:
                struct.pack_into("<I", head, base + fo, str_base + str_rel[nm])
        if kind != "rec":
            continue
        vs_idx, ps_idx = off_index.get(u32(base - 0x20)), off_index.get(u32(base + 0x00))
        vs_off = bl[vs_idx][0] if vs_idx is not None else 0
        ps_off = bl[ps_idx][0] if ps_idx is not None else 0
        if not (vs_off in patched_old or ps_off in patched_old):
            continue
        pres = rdef_resources(blob_bytes[ps_idx]) if ps_idx is not None else None
        vres = rdef_resources(blob_bytes[vs_idx]) if vs_idx is not None else None
        if pres is None and vres is None:
            continue
        c = record_counts(pres, vres)
        for key, fo, mask in _CNT_FIELDS:
            if mask is not None:
                cur2 = u32(base + fo)
                struct.pack_into("<I", head, base + fo,
                                 (cur2 & ~mask & 0xFFFFFFFF) | (c[key] & mask))
            else:
                struct.pack_into("<I", head, base + fo, c[key])
        head[base + 0xCC] = c["cc"] & 0xFF
        head[base + 0xC6] = c["c6"] & 0xFF
        head[base + 0xC7] = c["c7"] & 0xFF

    return bytes(head) + new_tail + b"".join(blob_bytes)


# 按类别加"绑定资源"的 RDEF 字段预设(实测自原版 bound resource 原始字段)。
#   ⚠ cbuffer(type=0) **不支持**: 它除绑定项外还需 RDEF 里的 cbuffer **定义表**(成员), add_bound_resource 不建它
#   (与设计一致: cbuffer 引用需改 shader 源); UAV 因种类多(type 4/6/8/…) 未预置。
RESOURCE_KINDS = {
    "tex2d": dict(type_=2, ret=5, dim=4, nsamp=0xFFFFFFFF, flags=0xC),  # Texture2D (SRV)
    "buf":   dict(type_=7, ret=6, dim=1, nsamp=0, flags=0),            # ByteAddress Buffer (SRV)
    "smp":   dict(type_=3, ret=0, dim=0, nsamp=0, flags=0),            # Sampler
}
_KIND_TYPES = {"tex2d": (2,), "buf": (7,), "smp": (3,)}


def add_resource(data, blob_idx, cat, name, slot=None):
    """给 blob 的 RDEF 加一条绑定资源(cat ∈ RESOURCE_KINDS), 再按 RDEF 重建整个容器。

    slot=None 取该类别现有 bind point 之后的下一个。**同 blob 已声明同名则报错**(去重)。
    返回新容器 bytes。
    """
    try:
        from .rdef import add_bound_resource, rdef_bind_info, blob_list
    except ImportError:
        from rdef import add_bound_resource, rdef_bind_info, blob_list
    if cat not in RESOURCE_KINDS:
        raise ValueError("未知资源类别: %s" % cat)
    _bs, bl = blob_list(data)
    if not (0 <= blob_idx < len(bl)):
        raise ValueError("blob idx out of range")
    o, s = bl[blob_idx]
    blob = data[o:o + s]
    info = rdef_bind_info(blob) or []
    if any(nm == name for (nm, _t, _bp, _d, _r) in info):
        raise ValueError("该 blob 已声明资源: %s" % name)
    if slot is None:
        types = _KIND_TYPES[cat]
        used = [bp for (nm, tt, bp, _d, _r) in info if tt in types]
        slot = (max(used) + 1) if used else 0
    new_blob = add_bound_resource(blob, name, slot, **RESOURCE_KINDS[cat])
    out = rebuild_from_rdef(data, {blob_idx: new_blob})
    if out is None:
        raise ValueError("非主版本(0x01100004)容器, 暂不支持 RDEF 重建")
    return out


def add_texture(data, blob_idx, name, slot=None):
    """[兼容包装] 给 blob 的 RDEF 加一条 Texture2D 并重建。"""
    return add_resource(data, blob_idx, "tex2d", name, slot)


def _ascii(b, o, n=128):
    e = o
    while e < len(b) and b[e] != 0 and e - o < n:
        e += 1
    return b[o:e].decode("latin1", "replace")


if __name__ == "__main__":
    import sys
    d = open(sys.argv[1], "rb").read()
    out = rebuild_from_rdef(d)
    print("rebuild_from_rdef:", "None" if out is None else "%d -> %d" % (len(d), len(out)))
