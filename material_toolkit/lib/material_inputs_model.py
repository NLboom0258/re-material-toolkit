#!/usr/bin/env python3
"""结构化材质输入注册 —— 输入的**唯一真源**(`.mmat.json` 的 `inputs` 字段)。

背景(2026-10-01 重构): 旧方案把输入注册写在材质源码顶部的 `//!` 注释里(`//! preset/engine/
param/tex`, 带 `@pass` 标签, 无标签归 main) ⇒ 解析脆弱(多解析器 / 标签推断 / 自动依赖不注册)。
现改为**结构化**注册。

结构:
    inputs = {
        "preset": {"main": [...], "depth": [...], "vertex": [...]},   # 语义(预设)输入名(**按 pass**)
        "engine": {"main": [...], "depth": [...], "vertex": [...]},   # 引擎资源名(**按 pass**)
        "tex":    {"main": [...], "depth": [...], "vertex": [...]},   # 材质贴图槽名(**按 pass**)
        "param":  [{"name","type"}, ...],                             # 材质参数(**全局**, 非 per-pass)
    }

要点:
- **`param` 是全局的**(不是 per-pass): `UserMaterial` 是 mmtr 级**共享同一张成员表** ⇒ 参数天然
  是材质级; 声明一次, 任一 pass 都可用。生成端对每个 pass 都供给 `UserMaterial`(并集成员); 某 pass
  若不引用任何成员, 该 cbuffer 会被编译器**整体剔除** ⇒ 输出等价(无需扫代码判"哪些 pass 真用到")。
- `preset/engine/tex` **按 pass**(它们是真 per-pass 的语义输入/资源)。
- 自动依赖(预设依赖的引擎资源): `normalize()` 写进**同 pass** 的 `engine`(标准注册流程)。
- 版本级/结构性依赖(如 main 的 SceneInfo、vertex 的标准 VS 引擎资源并集)**不在资产里**, 由
  `presets/<ver>/passes.json` 声明(见 material_inputs.pass_dep_names)。
"""

PASSES = ("main", "depth", "vertex")
_PER_PASS = ("preset", "engine", "tex")


def empty():
    """空 inputs(preset/engine/tex 各 pass 为空; param 为空列表)。"""
    out = {k: {p: [] for p in PASSES} for k in _PER_PASS}
    out["param"] = []
    return out


def _param_items(raw):
    """把 `param` 归一为 [{name,type}]。兼容旧格式(per-pass dict: {main:[...],...})。"""
    if isinstance(raw, dict):
        items = []
        for p in PASSES:
            items += (raw.get(p) or [])
    else:
        items = raw or []
    out, seen = [], set()
    for e in items:
        if isinstance(e, dict):
            nm, tp = e.get("name"), e.get("type") or "float"
        else:
            nm, tp = e, "float"
        if nm and nm not in seen:
            seen.add(nm)
            out.append({"name": nm, "type": tp})
    return out


def from_dict(d):
    """规范化: 保证结构完整 + 去重(保序)。非法/缺失项容错为空。"""
    d = d or {}
    out = {k: {p: [] for p in PASSES} for k in _PER_PASS}
    for k in _PER_PASS:
        src = d.get(k) or {}
        for p in PASSES:
            for nm in (src.get(p) or []):
                if nm and nm not in out[k][p]:
                    out[k][p].append(nm)
    out["param"] = _param_items(d.get("param"))
    return out


def to_dict(inputs):
    return from_dict(inputs)


# ---- 访问器(输入假定已规范化; 直接索引, 容错) ----
def presets(inputs, p):
    return list(((inputs or {}).get("preset") or {}).get(p) or [])


def engine(inputs, p):
    return list(((inputs or {}).get("engine") or {}).get(p) or [])


def textures(inputs, p):
    return list(((inputs or {}).get("tex") or {}).get(p) or [])


def params(inputs):
    """材质参数 [(name,type)] —— **全局**(非 per-pass)。"""
    out = []
    for e in ((inputs or {}).get("param") or []):
        if isinstance(e, dict):
            out.append((e.get("name"), e.get("type") or "float"))
        else:
            out.append((e, "float"))
    return out


def all_params(inputs):
    """全部材质参数 [(name,type)](按序去重) —— 与 `params` 同源(UserMaterial 成员表用)。"""
    out, seen = [], set()
    for (n, t) in params(inputs):
        if n and n not in seen:
            seen.add(n)
            out.append((n, t))
    return out


def normalize(inputs):
    """补齐自动依赖: 预设依赖的引擎资源 -> 写进**同 pass** 的 engine(去重保序)。返回新 dict。

    仅**补**, 不删(GUI 侧据当前预设判定锁定; 不再是依赖的项会解锁, 用户可手动删)。
    """
    out = from_dict(inputs)
    from . import semantic_inputs as SI
    for p in PASSES:
        res = SI.resolve(out["preset"][p], set())
        for nm in res["engine"]:
            if nm not in out["engine"][p]:
                out["engine"][p].append(nm)
    return out
