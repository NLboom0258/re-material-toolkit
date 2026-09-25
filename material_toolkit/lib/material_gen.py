"""材质生成器: 材质函数(HLSL) + 模板 mmtr -> 新 mmtr(M3 固化)。

流程: (pass 模板 + 材质函数)编译 ps_5_0 -> 找模板里该 pass 的所有 PS ->
按**兼容性**筛选 -> 逐个替换 -> 自检。

兼容性(而非"签名完全相等"): 同一 pass 的各 PS 变体会**多带**一些输入语义
(如 `SV_IsFrontFace`), 或换用不同资源。判据:
  1) 我们的输入签名 = 目标输入签名的**前缀**(寄存器/语义/索引一致);
  2) 输出签名相等;
  3) 我们的绑定(cbuffer/sampler/纹理寄存器) 是目标的**子集**;
  4) 目标**不是** per-instance 方案(含 `dcl_resource_structured` 或 `NOINTERPOLATOR`
     输入) —— 那套用 `UserMaterialInstances` 结构化缓冲, 需专属模板。
⇒ 多带输入(=不含它们也能跑)可替换; 绑定/per-instance 不同则跳过。
"""
import re

from . import mmtr_blobs as B
from . import material_pass as MP
from . import rdef as R
from .mmtr_material import MaterialModel, parse_technology
from .mmtr_model import MmtrModel


def _sig_entries(block):
    """从 disasm 签名表抽 [(语义名, 索引, 寄存器)](忽略 mask/used)。"""
    out = []
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("//"):
            continue
        s = s[2:].strip()
        if not s or s.startswith("Name") or s.startswith("---"):
            continue
        parts = s.split()
        if len(parts) >= 4 and parts[1].isdigit():
            out.append((parts[0].lower(), parts[1], parts[3]))
    return out


_BIND_RE = [
    ("cb", re.compile(r"dcl_constantbuffer\s+CB(\d+)")),
    ("s", re.compile(r"dcl_sampler\s+s(\d+)")),
    ("t", re.compile(r"dcl_resource_\w+.*?\s+t(\d+)")),
]


def _bindings(txt):
    out = set()
    for line in txt.splitlines():
        s = line.strip()
        for kind, rx in _BIND_RE:
            m = rx.search(s)
            if m:
                out.add((kind, m.group(1)))
                break
    return out


def analyze(dxbc):
    """PS 接口摘要: 输入/输出签名(名/索引/寄存器)、绑定集、是否 per-instance。"""
    txt = B.disassemble_dxbc(dxbc)
    insig = txt.split("Input signature:", 1)[1].split("Output signature:", 1)[0]
    outsig = txt.split("Output signature:", 1)[1].split("ps_5_", 1)[0]
    ein = tuple(_sig_entries(insig))
    return {
        "in": ein,
        "out": tuple(_sig_entries(outsig)),
        "bind": _bindings(txt),
        "per_instance": ("dcl_resource_structured" in txt
                         or any(e[0] == "nointerpolator" for e in ein)),
    }


def is_replaceable(ours, target):
    """我们的 PS 能否替换进目标 PS。"""
    if target["per_instance"]:
        return False
    if ours["in"] != target["in"][:len(ours["in"])]:
        return False
    if ours["out"] != target["out"]:
        return False
    return ours["bind"] <= target["bind"]


def collect_pass_ps(data, pass_name="Deferred"):
    """模板里该 pass 用到的所有 PS blob 序号(去重排序)。"""
    mm = MaterialModel(bytes(data))
    idxs = set()
    for v in mm.variants():
        if parse_technology(v["tech"])["pass"] == pass_name and v["ps"] >= 0:
            idxs.add(v["ps"])
    return sorted(idxs)


def generate(template, material_src=None, template_name="deferred_env",
             pass_name="Deferred", target="ps_5_0"):
    """材质函数 + 模板 mmtr -> (新 mmtr bytes, report dict)。

    material_src 为 None 时用该 pass 模板的默认材质函数。
    """
    ps, err = MP.compile_shading(material_src, template_name, target=target)
    if err:
        raise ValueError("HLSL 编译失败:\n%s" % err)
    ours = analyze(ps)

    out = bytes(template)
    done, skipped = [], []
    for idx in collect_pass_ps(out, pass_name):
        if not is_replaceable(ours, analyze(B.extract_blob(out, idx))):
            skipped.append(idx)
            continue
        out = R.replace_blob(out, idx, ps)
        done.append(idx)

    bad = [i for i in done
           if not B.verify_dxbc(B.extract_blob(out, i))["disasm_ok"]]
    return out, {"replaced": done, "skipped": skipped, "bad": bad,
                 "issues": MmtrModel(out).validate(), "ps_size": len(ps)}
