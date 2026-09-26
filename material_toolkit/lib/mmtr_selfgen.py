#!/usr/bin/env python3
"""自建 mmtr(延迟): 我们的**零声明 PS** + 按 RDEF **收缩**的绑定/尾段。

与 `mat-gen` 的区别(用户方向: 尽量不依赖现有 mmtr 的"材质接口"):
  - PS 用 `deferred_bare` 模板 —— **只用插值输入, 零 cbuffer/贴图/sampler 声明**;
  - 替换目标主 pass 的材质 PS 后, 对相关记录 `recount_slot`(按 RDEF 重算计数)
    ⇒ 记录绑定组自动收缩为 **VS 侧资源**(不含 UserMaterial / 材质贴图);
  - 再 `rebuild_canonical` 规范化尾段 ⇒ **自动丢弃**不再被任何记录引用的池/表/参数/字符串。

结果: 产物**不依赖任何现成 master 的材质参数/贴图声明** ⇒ 任何 mdf2 指过来都能跑
(材质声明只有"我们真正用到的"), 且与原文件等长(避开 blob 指针重映射)。
仅处理 version `0x01100004`。
"""
try:
    from . import mmtr_blobs as B
    from . import material_pass as MP
    from . import material_gen as G
    from . import rdef as R
    from . import mmtr_tail as T
    from .mmtr_build import MmtrImage, REC_N
    from .mmtr_model import MmtrModel
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_blobs as B
    import material_pass as MP
    import material_gen as G
    import rdef as R
    import mmtr_tail as T
    from mmtr_build import MmtrImage, REC_N
    from mmtr_model import MmtrModel


def compile_bare(material_src, template="deferred_bare"):
    """编译零声明模板 + 材质源 -> PS DXBC。"""
    ps, err = MP.compile_shading(material_src, template)
    if err:
        raise ValueError("HLSL 编译失败:\n%s" % err)
    return ps


def build(base, material_src, pass_name="Deferred", template="deferred_bare",
          canonical=True):
    """base(master) + 我们的材质源 -> (自建 mmtr bytes, report)。

    步骤: 编译零声明 PS -> 替换该 pass 全部可替换的材质 PS -> 对相关记录 recount
    -> rebuild_canonical(收缩尾段)。report 含 replaced/skipped/bad/recounted/canonical/issues。
    """
    data = bytes(base)
    ps = compile_bare(material_src, template)
    ours = G.analyze(ps)

    targets = G.collect_pass_ps(data, pass_name)
    out, replaced, skipped, bad = data, [], [], []
    for idx in targets:
        a = G.analyze(B.extract_blob(out, idx))
        if not G.is_replaceable(ours, a, check_bind=False):
            skipped.append(idx)
            continue
        out = R.replace_blob(out, idx, ps)
        (replaced if B.verify_dxbc(B.extract_blob(out, idx))["disasm_ok"]
         else bad).append(idx)

    # 按 RDEF 重算被替换槽的计数 ⇒ 绑定组收缩为 VS 侧
    m = MmtrModel(out)
    repl = {m.blob_off(i) for i in replaced}
    img = MmtrImage.from_bytes(out)
    recounted = 0
    for slot in range(REC_N):
        if img.rec_field(slot, 0x00) in repl:
            if img.recount_slot(slot) is not None:
                recounted += 1
    out = img.to_bytes()

    canon = None
    if canonical:
        norm = T.rebuild_canonical(out)
        canon = norm is not None
        if norm is not None:
            out = norm

    return out, {
        "pass_name": pass_name, "template": template, "ps_size": len(ps),
        "targets": targets, "replaced": replaced, "skipped": skipped, "bad": bad,
        "recounted": recounted, "canonical": canon,
        "issues": MmtrModel(out).validate(),
    }
