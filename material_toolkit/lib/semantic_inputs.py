#!/usr/bin/env python3
"""预设(语义)输入目录: 每个条目 = 一个可**单独添加**的语义输入项。

数据来自 `presets/<ver>/semantic_inputs.json`(人工维护的"内容"文件, 非从 mmtr 抽取)。
每个条目: `{name, group?, desc?, depends[], field{name,type}, impl[]}`。
`group` 仅用于界面分组显示 —— **添加单位是单个条目**(如 `camPos`), 不是一整类。

设计(2026-10-01 v3, 结构化真源):
- "已添加的语义输入项"由 `.mmat.json` 的 `inputs.preset[pass]` 给出(见 material_inputs_model);
- 组装时**动态生成** `struct MaterialInput`(字段 = 所有已添加项提供的字段)与 main 内的
  构造代码(每项的 impl); **依赖的引擎资源**由 `resolve()` 返回, 调用方并入该 pass 的接口;
- 本模块**不 import material_inputs**(避免循环); 只收"名字列表", 不做源码解析。

用法:
    SI.catalog()                         # 全部条目 [{name,group,desc,depends,field,impl}]
    SI.groups()                          # 按 group 聚合(界面用): [{group, items:[...]}]
    SI.find(name)                        # 单个(无则 None)
    SI.resolve(names, existing_names)    # 解析 -> deps/engine/fields/impl/def/build
"""

import json
import os
import re

try:
    from . import mmtr_presets as P
except ImportError:  # 允许脚本直接 import
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_presets as P

_GROUP_NONE = "(未分组)"

# ---- 值层: 每个"阶段值"给 ps/vs/depth 各自的"供值表达式"(真·世界空间/屏幕语义) ----
# 预设 impl 用 `G.<值>` 引用; 解析时按 stage 替换。None = 该 stage 拿不到此值。
#   ps   = 主 PS(读插值 i.v1..)
#   vs   = 顶点着色器(读生成的局部量 wp/wN/wT; _vs_svpos/_vs_pclip 由 vs_gen 计算)
#   depth= 深度 PS —— Phase 3 后深度族 VS 也输出**材质族插值**(pack=mat) ⇒ depth 供值 == ps 供值。
STAGE_VALUES = {
    "worldPos":     {"ps": "float3(i.v3.w, i.v4.x, i.v4.y)", "vs": "wp",
                     "depth": "float3(i.v3.w, i.v4.x, i.v4.y)"},
    "worldNormal":  {"ps": "i.v1.xyz", "vs": "wN", "depth": "i.v1.xyz"},
    "worldTangent": {"ps": "float3(i.v2.w, i.v3.y, i.v3.x)", "vs": "wT",
                     "depth": "float3(i.v2.w, i.v3.y, i.v3.x)"},
    "tangentSign":  {"ps": "(i.v3.z < 0.0 ? -1.0 : 1.0)", "vs": "sign(i.tan.w)",
                     "depth": "(i.v3.z < 0.0 ? -1.0 : 1.0)"},
    "uv0":          {"ps": "float2(i.v1.w, i.v2.x)", "vs": "i.uv0",
                     "depth": "float2(i.v1.w, i.v2.x)"},
    "uv1":          {"ps": "i.v2.yz", "vs": "i.uv1", "depth": "i.v2.yz"},
    "svpos":        {"ps": "i.svpos", "vs": "_vs_svpos", "depth": "i.svpos"},
    "prevClip":     {"ps": "float4(i.v4.zw, 0.0, i.v5.x)", "vs": "_vs_pclip",
                     "depth": "float4(i.v4.zw, 0.0, i.v5.x)"},
    # 仅顶点阶段有意义的量: PS/深度阶段拿不到(骨骼矩阵只给 VS) ⇒ 填 None 报“不支持”。
    # 它们的具体值由 vs_gen 按 world 硬编码(见 build_from_spec), 这里的 0u 只是占位。
    "variantKind":  {"ps": None, "vs": "0u", "depth": None},
    "jointOffset":  {"ps": None, "vs": "0u", "depth": None},
}

# 需要**插值**(INTERPOLATOR)才能供值的值(深度族启用这些时, 深度 VS 须改为输出材质族插值)。
INTERP_VALUES = frozenset(("worldPos", "worldNormal", "worldTangent", "tangentSign",
                           "uv0", "uv1", "prevClip"))

# 值 -> 该值在 VS 需要的顶点属性(供"补输入"判定); 未列/空 = 无需额外属性。
VALUE_VS_ATTR = {
    "worldNormal": ["NORMAL"], "worldTangent": ["TANGENT"], "tangentSign": ["TANGENT"],
    "uv0": ["TEXCOORD0"], "uv1": ["TEXCOORD1"],
}

_G_RE = re.compile(r"G\.(\w+)")


def _stage_expr(value, stage):
    """某值在某 stage 的供值表达式(不可用返回 None)。"""
    return (STAGE_VALUES.get(value) or {}).get(stage)


def path():
    return os.path.join(P.dir_path(), "semantic_inputs.json")


def catalog():
    """全部条目(列表)。无文件返回 []。"""
    p = path()
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("entries", d.get("presets", []))


def find(name):
    for e in catalog():
        if e["name"] == name:
            return e
    return None


def groups():
    """按 `group` 聚合(保持首次出现顺序); 无 group 的归入 "(未分组)"。

    返回 [{group, items:[条目...]}]。仅供界面分组显示, 不改变"单项添加"的语义。
    """
    out, idx = [], {}
    for e in catalog():
        g = e.get("group") or _GROUP_NONE
        if g not in idx:
            idx[g] = len(out)
            out.append({"group": g, "items": []})
        out[idx[g]]["items"].append(e)
    return out


def unsupported_in_stage(names, stage):
    """这些预设中, 依赖了该 stage 无法供值(如深度拿不到几何值)的项名列表。"""
    out = []
    for n in names:
        e = find(n)
        if e and any(not _stage_expr(v, stage) for v in (e.get("values") or [])):
            out.append(n)
    return out


def available(name, stage="ps"):
    """该语义输入项在给定 stage 是否**可选**。

    优先看条目里的显式 `stages`(缺省 = 三个 pass 都可用); 老数据没有 `stages` 时
    回退到“全部 `values` 在该 stage 都能供值”的推导。
    """
    e = find(name)
    if not e:
        return False
    st = e.get("stages")
    if st:
        return stage in st
    vals = e.get("values") or []
    if not vals:
        return True
    return all(_stage_expr(v, stage) is not None for v in vals)


def available_names(stage="ps"):
    """该 stage 可添加的全部语义输入项名集合。"""
    out = set()
    for g in groups():
        for e in g.get("items") or ():
            if available(e["name"], stage):
                out.add(e["name"])
    return frozenset(out)


def presets_need_interp(names):
    """这些预设是否依赖**需要插值**的值(深度族启用时, 深度 VS 须改为输出材质族插值)。"""
    for n in names:
        e = find(n)
        if e and any(v in INTERP_VALUES for v in (e.get("values") or [])):
            return True
    return False


# reduced 族(深度/阴影/拾取)缺失的顶点属性 -> 依赖它们的预设必须"补输入"(换完整族)。
_UPGRADE_ATTRS = frozenset(("NORMAL", "TANGENT", "TEXCOORD0"))


def presets_need_upgrade(names):
    """这些预设是否依赖 reduced 族缺失的顶点属性(NORMAL/TANGENT/TEXCOORD0)。

    为真 ⇒ 应自动启用"强制完整输入"且**锁死**(不可取消), 直到不再有预设依赖它们。
    """
    for n in names:
        e = find(n)
        if not e:
            continue
        for v in (e.get("values") or []):
            if any(a in _UPGRADE_ATTRS for a in VALUE_VS_ATTR.get(v, [])):
                return True
    return False


def _is_interp(name):
    """是否插值输入(INTERPOLATORn): 非引擎资源, 仅作依赖/锁定。"""
    return (name or "").startswith("INTERPOLATOR")


def _deps_of(e, stage="ps"):
    """该预设项在指定 stage 的依赖。

    同一项在三个 pass 的**取值实现不同** ⇒ 依赖也可能不同(如 `uv0` 在 PS 来自
    INTERPOLATOR, 在 VS 直接是顶点属性 `i.uv0`)。条目里可用 `depends_by_stage`
    按 stage 覆盖, 未列出时回退到通用 `depends`。
    """
    by = e.get("depends_by_stage") or {}
    if stage in by:
        return list(by[stage] or [])
    return list(e.get("depends") or [])


def deps_of(name, stage="ps"):
    """该预设项在指定 stage 的依赖名列表(供 GUI 展示/校验)。"""
    e = find(name)
    return _deps_of(e, stage) if e else []


def _dedup(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def resolve(item_names, existing_names=frozenset(), stage="ps",
            struct_name="MaterialInput", recv="mi"):
    """按"已添加的语义输入项名"解析出: 依赖资源 + 输入结构体字段 + 构造实现 + 文本。

    stage: "ps"(主 PS, 默认) / "vs"(顶点) / "depth"(深度 PS)。决定 `G.<值>` 的替换来源。
           depth 下拿不到的几何值记入 `unsupported`(调用方据以禁用/报错)。
    struct_name/recv: 生成的输入结构体名与变量名(PS=MaterialInput mi / VS=VertexInput v /
           深度=DepthInput di)。

    参数:
      item_names     : `//! preset` 名列表(可含未知名); 每名 = 一个单独项。
      existing_names : 基础接口里**已有**的资源名集合(这些依赖不再重复添加)。

    返回 dict:
      presets : 有效项名(按序去重)
      unknown : 不在目录里的项名
      deps    : 全部依赖资源名(按序去重)
      engine  : 需要新增的依赖(不在 existing_names)
      lock    : {依赖名: [引入它的项名, ...]}
      fields  : [{"name","type"}] (按序去重, 同名取首个)
      impl    : ["mi.xxx = ...;", ...] (按序)
      def     : struct MaterialInput 定义文本(空则含占位成员)
      build   : main 内构造代码(已缩进 4 空格; 空则含占位赋值)
    """
    names, unknown = [], []
    for n in _dedup(list(item_names)):
        (names if find(n) else unknown).append(n)

    deps, lock, interp = [], {}, []
    for n in names:
        for d in _deps_of(find(n), stage):
            if d not in deps:
                deps.append(d)
            lock.setdefault(d, [])
            if n not in lock[d]:
                lock[d].append(n)
            if _is_interp(d) and d not in interp:
                interp.append(d)
    existing = set(existing_names or ())
    # 插值输入不是引擎资源 ⇒ **不进 engine**(不写 `//! engine`), 仅作依赖/锁定信息
    engine = [d for d in deps if d not in existing and not _is_interp(d)]

    fields, seen_f, impl_raw, values, unsupported = [], set(), [], [], []
    for n in names:
        e = find(n)
        f = e.get("field")
        if f and f["name"] not in seen_f:
            seen_f.add(f["name"])
            fields.append({"name": f["name"], "type": f.get("type", "float")})
        impl_raw.extend(e.get("impl") or [])
        for v in (e.get("values") or []):
            if v not in values:
                values.append(v)
            if not _stage_expr(v, stage) and v not in unsupported:
                unsupported.append(v)

    # 阶段值替换: `G.<值>` -> 该 stage 的供值表达式(不可用 -> 保留占位, 由 unsupported 揭示)
    def _sub(m):
        return _stage_expr(m.group(1), stage) or ("/*unsupported:%s*/0.0f" % m.group(1))
    impl = [_G_RE.sub(_sub, ln) for ln in impl_raw]
    # 接收者重写: 目录 impl 一律写 `mi.xxx = ...`; 非 PS stage 换成该 stage 的变量名(v./di.)。
    if recv != "mi":
        impl = [re.sub(r"\bmi\.", recv + ".", ln) for ln in impl]

    vs_attrs = []
    for v in values:
        for a in VALUE_VS_ATTR.get(v, []):
            if a not in vs_attrs:
                vs_attrs.append(a)

    return {"presets": names, "unknown": unknown, "deps": deps, "engine": engine,
            "interp": interp, "lock": lock, "fields": fields, "impl": impl,
            "values": values, "unsupported": unsupported, "vs_attrs": vs_attrs,
            "def": struct_text(fields, struct_name),
            "build": build_text(fields, impl, struct_name, recv)}


def struct_text(fields, name="MaterialInput"):
    """`struct <name> { ... };` —— 无字段时放一个占位成员(空 struct 在 HLSL 非法)。"""
    lines = ["struct %s" % name, "{"]
    if fields:
        for f in fields:
            lines.append("    %s %s;" % (f["type"], f["name"]))
    else:
        lines.append("    float _reserved;   // 空: 未添加任何语义输入")
    lines.append("};")
    return "\n".join(lines)


def build_text(fields, impl, name="MaterialInput", recv="mi"):
    """该 stage main 内的构造代码(缩进 4 空格)。无字段时给占位赋初值, 避免未初始化。"""
    body = list(impl) if impl else ["%s._reserved = 0.0;" % recv]
    return "\n".join(["    %s %s;" % (name, recv)] + ["    " + s for s in body])


def minput_from_src(material_src, existing_names=frozenset()):
    """(已废弃) 旧"从源码 `//! preset` 解析"入口 —— 结构化真源后不再解析源码, 恒空。

    保留仅为兼容旧 donor 路径(其材质源不含声明 ⇒ 结果与空解析一致)。
    """
    return resolve([], existing_names)


def empty_texts():
    """没有任何语义输入时的 def/build(供 build_source 的兜底默认)。"""
    return {"def": struct_text([]), "build": build_text([], [])}


def names_in(iface):
    """接口里已声明的资源名集合(cbuffer/texture/sampler)。供依赖判定。"""
    out = set()
    for c in (iface or {}).get("cbuffers", []):
        out.add(c["name"])
    for t in (iface or {}).get("textures", []):
        out.add(t["name"])
    for s in (iface or {}).get("samplers", []):
        out.add(s["name"])
    return out
