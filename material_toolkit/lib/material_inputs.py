#!/usr/bin/env python3
"""材质"输入体系": 允许清单(固有标准集 / 材质参数 / 引擎候选)。

数据来自预设 `presets/v01100004/inputs.json`(由 `scripts/_gen_inputs.py` 生成;
缺文件时退化为只从 iface.json 取标准集)。

用法:
    from ..lib import material_inputs as INP
    INP.std()        # 固有(标准接口): [{name,kind,type,reg,source}]
    INP.params()     # 材质参数(UserMaterial 成员): [{name,type,offset,source}]
    INP.candidates() # 引擎候选(自定义可选): [{name,kind,type,reg,source}]
    INP.by_kind("texture")            # 按类别过滤(允许清单)
    INP.decl_hlsl(sel)                # 把所选输入拼成 HLSL 声明(供接口/保活)
"""
import json
import os

try:
    from . import mmtr_presets as P
except ImportError:  # 允许脚本直接 import
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_presets as P

KINDS = ("cbuffer", "texture", "sampler", "param")


def path():
    return os.path.join(P.dir_path(), "inputs.json")


def _fallback():
    """无 inputs.json: 只用 iface.json 的标准集(候选为空)。"""
    iface = P.std_iface()
    if not iface:
        return {"std": [], "params": [], "candidates": []}
    std, params = [], []
    for c in iface["cbuffers"]:
        is_mat = c["name"] == "UserMaterial"
        std.append({"name": c["name"], "kind": "cbuffer", "type": "cbuffer",
                    "reg": c["reg"], "source": "material" if is_mat else "engine"})
        if is_mat:
            params += [{"name": m["name"], "type": m["type"],
                        "offset": m["offset"], "source": "material"}
                       for m in c["members"]]
    for t in iface["textures"]:
        std.append({"name": t["name"], "kind": "texture",
                    "type": t.get("fmt") or "float4", "reg": t["reg"],
                    "source": "engine" if t["name"] == "WhitePtSrv" else "material"})
    for s in iface["samplers"]:
        std.append({"name": s["name"], "kind": "sampler", "type": "sampler",
                    "reg": s["reg"], "source": "engine"})
    return {"std": std, "params": params, "candidates": []}


def load():
    if os.path.exists(path()):
        with open(path(), encoding="utf-8") as f:
            return json.load(f)
    return _fallback()


def std():
    """固有输入(标准接口; 只读)。"""
    return load()["std"]


def params():
    """材质参数(UserMaterial 标准成员)。"""
    return load()["params"]


def candidates():
    """引擎候选(自定义可选用)。"""
    return load()["candidates"]


def by_kind(kind, include_std=True):
    """允许清单里的某项类别: 标准 + 候选。kind ∈ KINDS(除 param)。"""
    out = []
    if include_std:
        out += [e for e in std() if e["kind"] == kind]
    out += [e for e in candidates() if e["kind"] == kind]
    return out


def catalog_text():
    """人读清单(供 CLI 打印)。"""
    d = load()
    lines = ["固有输入(标准接口, 只读) %d 项:" % len(d["std"])]
    for e in d["std"]:
        lines.append("  [%-7s] %-32s %-8s %-6s %s"
                     % (e["kind"], e["name"], e["type"], e["reg"], e["source"]))
    lines.append("材质参数(UserMaterial 标准成员) %d 项:" % len(d["params"]))
    lines.append("  " + ", ".join("%s(%s)" % (p["name"], p["type"])
                                  for p in d["params"]))
    lines.append("引擎候选(自定义可选) %d 项:" % len(d["candidates"]))
    for e in d["candidates"]:
        lines.append("  [%-7s] %-32s %-10s %-6s %s"
                     % (e["kind"], e["name"], e["type"], e["reg"], e["source"]))
    return "\n".join(lines)


def decl_hlsl(sel):
    """把所选输入拼成 HLSL 声明(sel: [entry]); 供接口注入/保活。

    - cbuffer: 同名不重复声明(模板已带的跳过由调用方决定);
    - texture: `Texture2D<float4> <name> : register(tN);`(fmt=byte 时 ByteAddressBuffer);
    - sampler: `SamplerState <name> : register(sN);`
    """
    out = []
    for e in sel:
        k, nm, reg = e["kind"], e["name"], e.get("reg", "")
        if k == "cbuffer":
            out.append("// cbuffer %s : register(%s) 需模板/预设提供完整成员声明" % (nm, reg))
        elif k == "texture":
            if e.get("type") == "byte":
                out.append("ByteAddressBuffer %s : register(%s);" % (nm, reg))
            else:
                out.append("Texture2D<float4> %s : register(%s);" % (nm, reg))
        elif k == "sampler":
            out.append("SamplerState %s : register(%s);" % (nm, reg))
    return "\n".join(out)
