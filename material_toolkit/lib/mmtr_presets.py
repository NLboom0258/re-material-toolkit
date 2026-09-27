#!/usr/bin/env python3
"""版本级预设(version 0x01100004): 把"跨文件恒定"的结构固化为常量资产。

设计(见 analysis/mmtr_from_scratch_design.md §4 路线 B / §1 实测):
  - 目标版本 `0x01100004` 下, 区域 `[0x00,0x46350)` 的"骨架"(内容字段清零后)跨 596 文件**逐字节相同**
    ⇒ 可抽取一次、作为**常量**复用(不必在构建时依赖某个 master 文件)。

预设内容(逐项应为版本级常量):
  - `skeleton.bin`        版本骨架 [0,0x46350)(内容字段清零)
  - `records.json`        记录·技术目录(每槽 前缀/技术名/pass/程序种类 + 按槽结构字段 + desc 相对指针)
  - `standard.bin` + `standard.json`  标准程序银行(VS / Pick PS / 深度族 cutout PS; 按种类+前缀+技术键)
  - `desc.bin`            参考描述符区(原样; "整段移植"用)
  - `pt.bin`              程序表区(5×264B; 内容含指针, 构建时重定位)

用法: 首次由 `python scripts/gen_presets.py <ref.mmtr>` 生成, 之后构建器从此处取。
"""
import json
import os
import hashlib
import struct

try:
    from .mmtr_build import MmtrTemplate, SKELETON_HI  # noqa: F401
except ImportError:  # 允许脚本直接 import
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mmtr_build import MmtrTemplate, SKELETON_HI  # noqa: F401

VERSION = 0x01100004
# 引擎 post 表区(InputLayout 元素表等; PT 指针指向此处)。
# ⚠ skeleton 把"记录字段"清零, 而槽 1081/1082 的记录字段正落在该区 ⇒ 必须单独留存原值。
POST_LO = 0x46210
_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "presets", "v%08x" % VERSION)


def dir_path():
    return _DIR


def skeleton_path():
    return os.path.join(_DIR, "skeleton.bin")


def records_path():
    return os.path.join(_DIR, "records.json")


def standard_path():
    return os.path.join(_DIR, "standard.bin")


def standard_index_path():
    return os.path.join(_DIR, "standard.json")


def desc_path():
    return os.path.join(_DIR, "desc.bin")


def pt_path():
    return os.path.join(_DIR, "pt.bin")


def post_path():
    return os.path.join(_DIR, "post.bin")


def iface_path():
    return os.path.join(_DIR, "iface.json")


def info_path():
    return os.path.join(_DIR, "info.json")


def blobmap_path():
    return os.path.join(_DIR, "blobmap.json")


def blobmap():
    with open(blobmap_path(), encoding="utf-8") as f:
        return json.load(f)


def has_preset():
    return os.path.exists(skeleton_path())


def desc():
    with open(desc_path(), "rb") as f:
        return f.read()


def pt():
    with open(pt_path(), "rb") as f:
        return f.read()


def post():
    """post 表区原值 [POST_LO, SKELETON_HI)(skeleton 里该区被记录栅格清零)。"""
    with open(post_path(), "rb") as f:
        return f.read()


def std_iface():
    """引擎/材质“标准接口”(由参考 Deferred PS 抽取; 含 UserMaterial 标准成员)。

    无 donor 构建时作为**基础接口**, 再加材质自己声明的参数/贴图。无文件返回 None。
    """
    p = iface_path()
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def info():
    with open(info_path(), encoding="utf-8") as f:
        return json.load(f)


def skeleton():
    """版本骨架 bytes(需先 extract)。"""
    with open(skeleton_path(), "rb") as f:
        return f.read()


def extract(ref_data, outdir=None):
    """从参考 master 抽取预设(骨架 + 记录目录 + 标准程序银行)并写入 outdir。返回目录。"""
    outdir = outdir or _DIR
    os.makedirs(outdir, exist_ok=True)
    ref_data = bytes(ref_data)
    with open(os.path.join(outdir, "skeleton.bin"), "wb") as f:
        f.write(MmtrTemplate(ref_data).skeleton)

    from .mmtr_standard import blob_kinds
    from .mmtr_blobs import extract_blob
    from .mmtr_material import MaterialModel, parse_technology
    from .mmtr_tail import TailModel

    tail = TailModel.decode(ref_data)
    desc = bytes(ref_data[tail.boundaries["desc"]:tail.boundaries["string"]])
    with open(os.path.join(outdir, "desc.bin"), "wb") as f:
        f.write(desc)
    # 程序表(PT) 区(5×264B; 内容含指针, 构建时需重定位)
    with open(os.path.join(outdir, "pt.bin"), "wb") as f:
        f.write(ref_data[0x14:0x14 + 5 * 264])
    # post 表区原值(skeleton 会把它当作"记录字段"清零 ⇒ 单独留存)
    with open(os.path.join(outdir, "post.bin"), "wb") as f:
        f.write(ref_data[POST_LO:SKELETON_HI])
    # 标准材质接口(来自参考 Deferred PS): 无 donor 构建的基础接口
    from .material_gen import pick_iface_ps
    from .material_iface import iface_from_dxbc
    _bi = pick_iface_ps(ref_data, "Deferred")
    if _bi is not None:
        with open(os.path.join(outdir, "iface.json"), "w", encoding="utf-8") as f:
            json.dump(iface_from_dxbc(extract_blob(ref_data, _bi)), f,
                      ensure_ascii=False, separators=(",", ":"))
    # 参考 blob 起点 -> md5(供按内容重定位 PT 里的 blob 指针)
    bmap, o = {}, int(tail.boundaries["blob_start"])
    while o + 28 <= len(ref_data) and ref_data[o:o + 4] == b"DXBC":
        sz = struct.unpack_from("<I", ref_data, o + 24)[0]
        bmap[str(o)] = hashlib.md5(ref_data[o:o + sz]).hexdigest()
        o += sz
    with open(os.path.join(outdir, "blobmap.json"), "w", encoding="utf-8") as f:
        json.dump(bmap, f, separators=(",", ":"))
    import struct as _s
    desc_lo = tail.boundaries["desc"]
    desc_hi = tail.boundaries["string"]

    def _ascii(o, n=256):
        e = o
        while e < len(ref_data) and ref_data[e] != 0 and e - o < n:
            e += 1
        return ref_data[o:e].decode("latin1", "replace")

    # 参考分层基址 + head+0x10 串 + PT 名(构建时把 PT/head 的绝对指针转相对)
    ptnames = []
    for _k in range(5):
        _nv = _s.unpack_from("<I", ref_data, 0x14 + _k * 264 + 0x104)[0]
        ptnames.append(_ascii(_nv) if 0x1000 <= _nv < len(ref_data) else "")
    info = {"bases": {k: int(v) for k, v in tail.boundaries.items()},
            "str10": _ascii(_s.unpack_from("<I", ref_data, 0x10)[0]),
            "ptnames": ptnames}
    with open(os.path.join(outdir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, separators=(",", ":"))

    def _u32(p):
        return _s.unpack_from("<I", ref_data, p)[0]

    def drel(p, fo):
        """记录字段 +fo 相对 desc 的偏移(0 则返回 0; 不在 desc 内返回 None)。"""
        v = _u32(p + fo)
        if v == 0:
            return 0
        return v - desc_lo if desc_lo <= v < desc_hi else None

    kinds = blob_kinds(ref_data)
    recs = []
    for v in MaterialModel(ref_data).variants():
        pt = parse_technology(v["tech"])
        base = 0x568 + v["slot"] * 264
        recs.append({
            "slot": v["slot"], "prefix": v["prefix"] or "", "tech": v["tech"],
            "pass": (pt["pass"] if pt else None),
            "ps_kind": (kinds.get(v["ps"]) if v["ps"] >= 0 else None),
            "vs_kind": (kinds.get(v["vs"]) if v["vs"] >= 0 else None),
            "cs_kind": (kinds.get(v["cs"]) if v["cs"] >= 0 else None),
            # 按槽结构字段(版本级): +0x18 常量; +0xC8 小整数
            "x18": _u32(base + 0x18), "c8": _u32(base + 0xC8),
            # 指向 desc 区的字段的相对偏移(整段移植时按此重定位)
            "d30": drel(base, 0x30), "d38": drel(base, 0x38),
            "d48": drel(base, 0x48), "d58": drel(base, 0x58),
            "d68": drel(base, 0x68), "d70": drel(base, 0x70),
        })
    with open(os.path.join(outdir, "records.json"), "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, separators=(",", ":"))

    # 标准程序银行: 按 (kind, 前缀, 技术) 键; 内容**去重**(同字节只存一份)
    bank, blob, uniq = {}, bytearray(), {}
    for v in MaterialModel(ref_data).variants():
        key = (v["prefix"] or "", v["tech"])
        for role, idx in (("vs", v["vs"]), ("ps", v["ps"]), ("cs", v["cs"])):
            if idx < 0:
                continue
            kd = kinds.get(idx)
            if kd not in ("standard_vs", "standard_ps", "cutout_ps", "other"):
                continue
            bk = "%s|%s|%s" % (kd, key[0], key[1])
            if bk in bank:
                continue
            b = extract_blob(ref_data, idx)
            h = hashlib.md5(b).hexdigest()
            if h not in uniq:
                uniq[h] = [len(blob), len(b)]
                blob += b
            bank[bk] = uniq[h]
            bank["md5|" + h] = uniq[h]
    with open(os.path.join(outdir, "standard.bin"), "wb") as f:
        f.write(bytes(blob))
    with open(os.path.join(outdir, "standard.json"), "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, separators=(",", ":"))
    return outdir


def check(data):
    """某 mmtr 的骨架是否 == 预设骨架(版本一致性判据)。"""
    return MmtrTemplate(data).skeleton == skeleton()
