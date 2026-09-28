#!/usr/bin/env python3
"""预设(语义)输入目录: 一个语义名 -> 依赖的引擎资源 + MaterialInput 字段 + 构造实现。

数据来自 `presets/<ver>/semantic_inputs.json`(人工维护的"内容"文件, 非从 mmtr 抽取)。

设计(2026-09-29):
- 材质源码顶部用 `//! preset <Name>` 声明"已添加的预设输入";
- 组装时**动态生成** `struct MaterialInput`(字段 = 所有预设提供的字段)与 main 内的
  构造代码(每预设的 impl); **依赖的引擎资源**按 `//! engine` 同样路径加入接口(自动分配
  寄存器 + 保活);
- 本模块**不 import material_inputs**(避免循环); 依赖判定由调用方传入 `existing_names`。

用法:
    SI.catalog()                         # 全部预设 [{name,desc,depends,fields,impl}]
    SI.find(name)                        # 单个(无则 None)
    SI.parse_preset_decls(src)           # 源码里的 `//! preset` 名(按序去重)
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

_PRESET_RE = re.compile(r"^\s*//!\s*preset\s+(\w+)\s*$")


def path():
    return os.path.join(P.dir_path(), "semantic_inputs.json")


def catalog():
    """全部预设(列表)。无文件返回 []。"""
    p = path()
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("presets", [])


def find(name):
    for e in catalog():
        if e["name"] == name:
            return e
    return None


def parse_preset_decls(material_src):
    """材质源码里的 `//! preset <Name>` 声明(按序去重)。"""
    out = []
    for line in (material_src or "").splitlines():
        m = _PRESET_RE.match(line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _dedup(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def resolve(preset_names, existing_names=frozenset()):
    """按"已添加的预设名"解析出: 依赖资源 + MaterialInput 字段 + 构造实现 + 文本。

    参数:
      preset_names   : `//! preset` 名列表(可含未知名)。
      existing_names : 基础接口里**已有**的资源名集合(这些依赖不再重复添加)。

    返回 dict:
      presets : 有效预设名(按序去重)
      unknown : 不在目录里的预设名
      deps    : 全部依赖资源名(按序去重)
      engine  : 需要新增的依赖(不在 existing_names)
      lock    : {依赖名: [引入它的预设名, ...]}
      fields  : [{"name","type"}] (按序去重, 同名取首个)
      impl    : ["mi.xxx = ...;", ...] (按序)
      def     : struct MaterialInput 定义文本(空则含占位成员)
      build   : main 内构造代码(已缩进 4 空格; 空则含占位赋值)
    """
    names, unknown = [], []
    for n in _dedup(list(preset_names)):
        (names if find(n) else unknown).append(n)

    deps, lock = [], {}
    for n in names:
        for d in (find(n).get("depends") or []):
            if d not in deps:
                deps.append(d)
            lock.setdefault(d, [])
            if n not in lock[d]:
                lock[d].append(n)
    existing = set(existing_names or ())
    engine = [d for d in deps if d not in existing]

    fields, seen_f = [], set()
    impl = []
    for n in names:
        e = find(n)
        for f in (e.get("fields") or []):
            if f["name"] not in seen_f:
                seen_f.add(f["name"])
                fields.append({"name": f["name"], "type": f.get("type", "float")})
        impl.extend(e.get("impl") or [])

    return {"presets": names, "unknown": unknown, "deps": deps, "engine": engine,
            "lock": lock, "fields": fields, "impl": impl,
            "def": struct_text(fields), "build": build_text(fields, impl)}


def struct_text(fields):
    """`struct MaterialInput { ... };` —— 无字段时放一个占位成员(空 struct 在 HLSL 非法)。"""
    lines = ["struct MaterialInput", "{"]
    if fields:
        for f in fields:
            lines.append("    %s %s;" % (f["type"], f["name"]))
    else:
        lines.append("    float _reserved;   // 空: 未添加任何预设输入")
    lines.append("};")
    return "\n".join(lines)


def build_text(fields, impl):
    """main 内的构造代码(缩进 4 空格)。无字段时给占位赋初值, 避免未初始化。"""
    body = list(impl) if impl else ["mi._reserved = 0.0;"]
    return "\n".join(["    MaterialInput mi;"] + ["    " + s for s in body])


def minput_from_src(material_src, existing_names=frozenset()):
    """便捷入口: 解析源码 `//! preset` -> resolve(...)。"""
    return resolve(parse_preset_decls(material_src), existing_names)


def empty_texts():
    """没有任何预设时的 def/build(供 build_source 的兜底默认)。"""
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
