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


def pick_iface_ps(data, pass_name="Deferred"):
    """选一个“基础”PS 当接口来源(非 per-instance、有输出、额外输入最少者)。"""
    best = None
    for idx in collect_pass_ps(data, pass_name):
        a = analyze(B.extract_blob(data, idx))
        if a["per_instance"] or not a["out"]:
            continue
        if best is None or len(a["in"]) < best[1]:
            best = (idx, len(a["in"]))
    return best[0] if best else None


_DECL_RE = re.compile(r"^\s*//!\s*(\w+)\s+(.+?)\s*$")


def parse_decls(material_src):
    """解析材质源码里的声明行: `//! param <type> <name>` / `//! tex <name>`。

    返回 (params=[(name,type)], textures=[name])。
    """
    from . import material_iface as MI
    params, textures = [], []
    for line in (material_src or "").splitlines():
        m = _DECL_RE.match(line)
        if not m:
            continue
        kind, rest = m.group(1).lower(), m.group(2).split()
        if kind == "param" and len(rest) == 2:
            if rest[0].lower() in MI.TYPE_SIZE:
                typ, name = rest[0], rest[1]
            else:
                name, typ = rest[0], rest[1]
            params.append((name, typ))
        elif kind in ("tex", "texture") and rest:
            textures.append(rest[0])
    return params, textures


def generate(template, material_src=None, template_name="deferred_env",
             pass_name="Deferred", target="ps_5_0", iface_from_template=True,
             add_inputs=True):
    """材质函数 + 模板 mmtr -> (新 mmtr bytes, report dict)。

    - 接口声明默认由模板 mmtr 的 Deferred PS **自动生成**;
    - 材质源码里的 `//! param <type> <name>` / `//! tex <name>` 声明会自动写进 mmtr
      (参数表 / 绑定)并加入接口声明(⑤b)。
    """
    from . import material_iface as MI
    from .mmtr import Mmtr
    from .binding import add_texture_slot

    out = bytes(template)
    base_idx = pick_iface_ps(out, pass_name) if iface_from_template else None
    iface = (MI.iface_from_dxbc(B.extract_blob(out, base_idx))
             if base_idx is not None else None)

    # 目标/跳过: 以“基础 PS”作参考(它等价于我们即将编译的 PS 的签名/绑定)
    targets, skipped = [], []
    if base_idx is not None:
        base_an = analyze(B.extract_blob(out, base_idx))
        for idx in collect_pass_ps(out, pass_name):
            (targets if is_replaceable(base_an, analyze(B.extract_blob(out, idx)))
             else skipped).append(idx)

    # 材质声明的自定义参数/贴图
    params, textures = parse_decls(material_src) if (add_inputs and material_src) \
        else ([], [])
    added_params = []
    if iface is not None and (params or textures):
        iface, added_params = MI.extend(iface, "UserMaterial", params, textures)

    # 写进 mmtr: 参数 -> 参数表; 贴图 -> 各目标 blob 的绑定组
    if added_params:
        mm = Mmtr.from_bytes(out)
        for name, size, off in added_params:
            out = mm.add_cbuffer_param("UserMaterial", name, size, off)
            mm = Mmtr.from_bytes(out)
    for tname in textures:
        for idx in targets:
            out = add_texture_slot(out, idx, tname, rdef=False)

    ps, err = MP.compile_shading(material_src, template_name, target=target,
                                 iface=iface)
    if err:
        raise ValueError("HLSL 编译失败:\n%s" % err)
    bad = []
    for idx in targets:
        out = R.replace_blob(out, idx, ps)
        if not B.verify_dxbc(B.extract_blob(out, idx))["disasm_ok"]:
            bad.append(idx)

    return out, {"replaced": targets, "skipped": skipped, "bad": bad,
                 "issues": MmtrModel(out).validate(), "ps_size": len(ps),
                 "added_params": added_params, "added_textures": list(textures)}
