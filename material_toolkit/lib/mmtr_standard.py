#!/usr/bin/env python3
"""标准 pass 集: 把 mmtr 的 blob 显式分为「材质主 PS」/「cutout PS」/「标准程序」。

背景(2026-09-27; 详见 analysis/pass_matrix.md §6/§7/§7b):
  - 跨正常 master 逐字节比对证实: **VS + Pick PS 逐字节相同**(引擎标准, 可整段复用);
    **材质主 pass(Deferred/Forward)的彩色 PS 随材质全不同**(我们写);
    **深度族 cutout PS 结构标准、仅 RDEF 里 alpha 贴图名随材质**。
  - ⇒ "纯凭空生成" = **标准程序(复用) + 我们的材质 PS(写)**。本模块把这层拆分显式化:
      分类 / 银行 / 指纹(跨 master 一致性) / 骨架化生成(只动材质 PS, 并校验标准集不变)。

三类 blob:
  - ``material_ps``: Deferred/Forward 里**有彩色输出**的 PS —— 材质特定(我们写)。
  - ``cutout_ps``  : 深度族(Shadow/DepthWrite/ZPrePass/PreTransform)及主 pass 里**无输出**
                     的 PS —— 结构标准(采样 alpha 贴图 + 抖动 + discard), 绑定随材质。
  - ``standard_vs`` / ``standard_ps``: 其余 VS 与 Pick PS —— 引擎标准, 跨 master 相同。
  - ``other``: CS 等未归类 blob。

"标准集"(standard_vs ∪ standard_ps)是跨 master 一致的部分, 用于 donor-independent 复用。
"""
import hashlib

try:
    from . import mmtr_blobs as B
    from .mmtr_material import MaterialModel, parse_technology
    from .mmtr_model import MmtrModel
    from . import rdef as R
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_blobs as B
    from mmtr_material import MaterialModel, parse_technology
    from mmtr_model import MmtrModel
    import rdef as R

# 材质主 pass = 写"最终材质外观"的 pass(其彩色 PS 材质特定)
MATERIAL_PASSES = ("Deferred", "Forward")
# 深度族 pass: 其 PS 为 cutout(采样 alpha 贴图 + 抖动 + discard)或无
DEPTH_PASSES = ("Shadow", "DepthWrite", "ZPrePass", "PreTransform")

# kind 优先级(同一 blob 被多技术引用时取"更材质特定"者)
_PRIO = {"standard_vs": 0, "standard_ps": 1, "cutout_ps": 2, "material_ps": 3}
_STD_KINDS = ("standard_vs", "standard_ps")


def _md5(b):
    return hashlib.md5(bytes(b)).hexdigest()


def has_color_output(dxbc):
    """PS 是否有彩色输出(输出签名含 `SV_Target`)。

    直接扫 DXBC 字节(OSGN/OSG1 chunk 里语义名以明文出现), 无需反汇编: 快且确定。
    """
    b = bytes(dxbc)
    return b"SV_Target" in b or b"SV_TARGET" in b


def blob_kinds(data):
    """{blob 下标: kind}, kind ∈ material_ps/cutout_ps/standard_vs/standard_ps/other。"""
    data = bytes(data)
    mm = MaterialModel(data)
    n = B.blob_count(data)
    kind = {}

    def put(i, k):
        if i < 0:
            return
        if i not in kind or _PRIO[k] >= _PRIO[kind[i]]:
            kind[i] = k

    for v in mm.variants():
        d = parse_technology(v["tech"])
        p = d["pass"]
        if v["vs"] >= 0:
            put(v["vs"], "standard_vs")
        if v["ps"] >= 0:
            if p in MATERIAL_PASSES:
                put(v["ps"], "material_ps" if has_color_output(B.extract_blob(data, v["ps"]))
                    else "cutout_ps")
            elif p in DEPTH_PASSES:
                put(v["ps"], "cutout_ps")
            else:                                    # Pick 等
                put(v["ps"], "standard_ps")
    for i in range(n):
        kind.setdefault(i, "other")
    return kind


def classify(data, pass_name=None):
    """分类报告 dict: 各 kind 的 blob 下标 + 材质 PS 按 pass 分组。"""
    data = bytes(data)
    mm = MaterialModel(data)
    kinds = blob_kinds(data)
    by_kind = {}
    for i, k in kinds.items():
        by_kind.setdefault(k, []).append(i)
    for k in by_kind:
        by_kind[k].sort()
    mat_by_pass = {}
    for v in mm.variants():
        if v["ps"] >= 0 and kinds.get(v["ps"]) == "material_ps":
            p = parse_technology(v["tech"])["pass"]
            mat_by_pass.setdefault(p, set()).add(v["ps"])
    return {
        "n_blobs": B.blob_count(data),
        "by_kind": by_kind,
        "material_ps": by_kind.get("material_ps", []),
        "cutout_ps": by_kind.get("cutout_ps", []),
        "standard_vs": by_kind.get("standard_vs", []),
        "standard_ps": by_kind.get("standard_ps", []),
        "standard": by_kind.get("standard_vs", []) + by_kind.get("standard_ps", []),
        "material_ps_by_pass": {k: sorted(x) for k, x in sorted(mat_by_pass.items())},
    }


def material_ps_indices(data, pass_name=None):
    """材质主 pass 的 PS blob 下标(pass_name 给定则只取该 pass)。"""
    info = classify(data)
    if pass_name is None:
        return sorted(info["material_ps"])
    return sorted(info["material_ps_by_pass"].get(pass_name, []))


def standard_fingerprint(data):
    """标准集(VS + Pick PS)的内容指纹: {md5: 条数}(跨 master 应一致)。"""
    data = bytes(data)
    kinds = blob_kinds(data)
    fp = {}
    for i, k in kinds.items():
        if k in _STD_KINDS:
            h = _md5(B.extract_blob(data, i))
            fp[h] = fp.get(h, 0) + 1
    return fp


def diff_standard(a, b):
    """比对两个 mmtr 的**标准集**指纹; 返回 {equal, only_in_a, only_in_b}。"""
    fa, fb = standard_fingerprint(a), standard_fingerprint(b)
    only_a = {h: fa[h] - fb.get(h, 0) for h in fa if fa[h] > fb.get(h, 0)}
    only_b = {h: fb[h] - fa.get(h, 0) for h in fb if fb[h] > fa.get(h, 0)}
    return {"equal": not only_a and not only_b, "only_in_a": only_a, "only_in_b": only_b}


def generate(donor, material_ps, pass_name="Deferred"):
    """骨架化生成: donor 提供**结构 + 标准 pass 集**; 仅把目标 pass 的**材质 PS** 换成新程序。

    donor:       结构载体(提供头部/变体记录/标准 pass 集)。
    material_ps: 单个 DXBC(bytes) 或列表 —— 按"可替换性"(输入前缀/输出相等/per-instance)
                 匹配到该 pass 的材质 PS 槽。
    pass_name:   目标主 pass(Deferred/Forward)。

    返回 (新 mmtr bytes, report dict)。report 含 ``standard_preserved`` —— 校验标准集未被改动。
    """
    from .material_gen import analyze, is_replaceable

    data = bytes(donor)
    targets = material_ps_indices(data, pass_name)

    ps_list = list(material_ps) if isinstance(material_ps, (list, tuple)) else [material_ps]
    an_list = [analyze(p) for p in ps_list]

    out = data
    replaced, skipped, bad = [], [], []
    for idx in sorted(targets):
        a = analyze(B.extract_blob(out, idx))
        hit = None
        for ps, ao in zip(ps_list, an_list):
            if is_replaceable(ao, a, check_bind=False):
                hit = ps
                break
        if hit is None:
            skipped.append(idx)
            continue
        out = R.replace_blob(out, idx, hit)
        if B.verify_dxbc(B.extract_blob(out, idx))["disasm_ok"]:
            replaced.append(idx)
        else:
            bad.append(idx)

    report = {
        "pass_name": pass_name,
        "targets": sorted(targets),
        "replaced": replaced,
        "skipped": skipped,
        "bad": bad,
        "ps_size": len(ps_list[0]),
        "issues": MmtrModel(out).validate(),
        "standard_preserved": diff_standard(data, out)["equal"],
    }
    return out, report


def report(data, other=None):
    """打印分类报告(供 CLI)。"""
    info = classify(data)
    print("blobs=%d" % info["n_blobs"])
    for k in ("material_ps", "cutout_ps", "standard_vs", "standard_ps", "other"):
        idxs = info["by_kind"].get(k, [])
        print("  %-12s %2d 个: %s" % (k, len(idxs), idxs))
    if info["material_ps_by_pass"]:
        parts = ", ".join("%s=%s" % (p, v) for p, v in info["material_ps_by_pass"].items())
        print("  材质 PS 按 pass: %s" % parts)
    if other is not None:
        d = diff_standard(data, other)
        print("标准集 vs 另一文件: %s" % ("一致" if d["equal"] else "不同"))
        if not d["equal"]:
            print("  only_in_self: %d   only_in_other: %d"
                  % (len(d["only_in_a"]), len(d["only_in_b"])))


def main():
    import sys
    if len(sys.argv) < 2:
        print("用法: python mmtr_standard.py <mmtr> [other.mmtr]"); return
    data = open(sys.argv[1], "rb").read()
    other = open(sys.argv[2], "rb").read() if len(sys.argv) > 2 else None
    report(data, other)


if __name__ == "__main__":
    main()
