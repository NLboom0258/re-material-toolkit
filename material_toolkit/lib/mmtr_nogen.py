#!/usr/bin/env python3
"""无 donor 构建: 预设(骨架/记录模式/标准银行/desc/PT) + 我们的 PS -> mmtr(v0x01100004)。

不依赖任何 master 文件(版本级常量见 presets/v01100004/, 由 scripts/gen_presets.py 抽取)。
- 程序: 标准槽从"银行"取, 目标主 pass 的材质 PS 用我们编译的(zero-declaration 模板);
- 尾段: pool/cbuffer/param/string **由 RDEF 派生**; desc 用预设**原样**(整段移植; 靠计数取 VS 前缀);
- 记录: 程序指针/大小/计数(binding)/名字/各 desc·表指针 按预设相对偏移写。
已知 v1 简化: GAP(0x540/0x548=槽0 VS, 已写); PT 已按预设重定位。
程序字节码大小字段: PS=+0x9C, VS=+0x88/+0x8C, CS=**+0xA0**(HS/DS/GS=+0x90/+0x94/+0x98, DMC5 标准 master 全 0)。
"""
import hashlib
import json
import os
import re
import struct

try:
    from . import mmtr_blobs as B
    from . import mmtr_presets as P
    from . import material_pass as MP
    from . import material_gen as MG
    from . import rdef as R
    from . import mmtr_tail as T
    from . import vs_gen as VG
    from .mmtr_build import (SKELETON_HI, REC_LO, REC_N, REC_SIZE,
                             PT_LO, PT_N, PT_SIZE)
    from .hashes import ascii_hash
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_blobs as B
    import mmtr_presets as P
    import material_pass as MP
    import material_gen as MG
    import rdef as R
    import mmtr_tail as T
    import vs_gen as VG
    from mmtr_build import (SKELETON_HI, REC_LO, REC_N, REC_SIZE,
                            PT_LO, PT_N, PT_SIZE)
    from hashes import ascii_hash

_UAV_TYPES = (4, 6, 8, 9, 10, 11)   # 不含 7(BYTEADDRESS=SRV)

# 标准 VS 自生结果缓存: 银行键 -> (kind, dxbc); kind ∈ {"gen","bank","none"}。
# 自生需编译 HLSL(较慢) ⇒ 按键全局缓存, 同一键在多槽/多次构建间复用。
_vs_cache = {}

# 引擎 post 表区: InputLayout 元素表等(PT 指针指向此处); 记录栅格尾部与其交叠。
POST_LO = 0x46210


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _uav_order(blob):
    """RDEF 里 UAV(type∈UAV 集) 的名字, 按数组序。"""
    if not blob:
        return []
    info = R.rdef_bind_info(blob) or []
    return [nm for nm, t, _bp, _d, _r in info if t in _UAV_TYPES]


def _variant_name(r):
    return (r.get("prefix") or "") + r["tech"]


def _iface_from_decls(material_src):
    """按材质源码里的 `//! param/tex/engine` 声明生成接口(**规范发射**)。

    委托 `material_inputs.build_iface_and_keepalive`(基础 = **默认基座**(固有输入));
    无声明时返回 None。保活由 build 单独取(此处忽略)。
    """
    from . import material_inputs as INP
    iface, _ka, _rep = INP.build_iface_and_keepalive(material_src, INP.default_iface())
    return iface


# 极简"深度/阴影态 PS"(对齐原版 env_sea#5: `ps_5_0/dcl_globalFlags/ret`, 仅 SV_POSITION
# 输入, RDEF 零绑定)。用途: depth/cutout 族槽(`ps_kind==cutout_ps`)不再用银行的"带
# alpha/dissolve"深度 PS, 而用此极简 PS ⇒ 记录/池/计数/desc 由 `derive_group(VS, 此 PS)`
# 自动派生成"VS-only"干净形态(ATOS/UserMaterial/环境参数不再出现)。
# 依据: 自编译产物与 env_sea#5 逐字节同构(408B, 同反汇编/同签名); 实测产物渲染/穿模不崩。
_MIN_DEPTH_PS_SRC = "void main(in float4 p : SV_POSITION) {}\n"
_min_depth_ps = None


def minimal_depth_ps():
    """返回极简深度 PS 的 DXBC 字节(首次编译后缓存)。"""
    global _min_depth_ps
    if _min_depth_ps is None:
        ps, err = B.compile_hlsl(_MIN_DEPTH_PS_SRC, "main", "ps_5_0",
                                 name="mindepth.hlsl")
        if err:
            raise ValueError("极简深度 PS 编译失败:\n%s" % err)
        _min_depth_ps = ps
    return _min_depth_ps


# 深度族"带效果"自生: 材质源含 `MaterialDepth` 钩子时, 用 deferred_depth 模板 + 钩子编译
# (RDEF 绑定由钩子引用决定 ⇒ 记录绑定自动回归 ATOS/UserMaterial 等)。缺省(无钩子)走极简 PS。
_DEPTH_HOOK_RE = re.compile(r"\bvoid\s+MaterialDepth\s*\(")


def has_depth_hook(material_src):
    """材质源是否定义了深度族钩子 `MaterialDepth`。"""
    return bool(_DEPTH_HOOK_RE.search(material_src or ""))


def depth_hook_ps(material_src, iface, minput):
    """编译深度族 PS(deferred_depth 模板 + MaterialDepth 函数)。

    效果的资源绑定由钩子自身的引用决定; 插值声明由 `material_pass.build_source`
    按 passes.json 注入(uv0 条件化)。寄存器由编译器自动紧凑(见 `material_pass` 文件头)。
    """
    ps, err = MP.compile_shading(material_src, "deferred_depth", iface=iface,
                                 keepalive="", minput=minput)
    if err:
        raise ValueError("深度钩子 PS 编译失败:\n%s" % err)
    return ps


def _hlsl_fn_span(src, name):
    """定位 `TYPE NAME(...) {...}` 的 [start, end); 无则 None(简单大括号计数)。

    TYPE 不限 `void`(如 VS 钩子 `float3 MaterialVertex(...)`); 限定在**行首**(定义),
    避免误匹配调用(`= NAME(` / `return NAME(`)。
    """
    m = re.search(r"^[ \t]*[A-Za-z_]\w*\s+%s\s*\(" % re.escape(name), src or "",
                  re.MULTILINE)
    if not m:
        return None
    i = src.index("{", m.start())
    d = 0
    for j in range(i, len(src)):
        c = src[j]
        if c == "{":
            d += 1
        elif c == "}":
            d -= 1
            if d == 0:
                return (m.start(), j + 1)
    return None


def _strip_hlsl_fn(src, name):
    """删除 src 中的某函数定义(保留其余: 辅助函数/声明)。未含该名时原样返回。"""
    sp = _hlsl_fn_span(src, name)
    return src if not sp else (src[:sp[0]] + src[sp[1]:])


def require_fn(material_src, name):
    """确保材质源定义了必需函数 `void name(...)`; 缺失即报错(每个 pass 都必须提供)。

    默认材质源已自带各 pass 的默认实现; 用户手删时在此报错(而非静默回退/默认填充)。
    """
    if _hlsl_fn_span(material_src or "", name) is None:
        raise ValueError("材质源缺少必需函数 `void %s(...)`: 每个 pass 都必须提供其函数"
                         % name)


# 材质源**只允许**这三个函数(MaterialMain/MaterialDepth/MaterialVertex)。
# 额外的用户函数一律拒绝 —— 因为编译某一 pass 时会保留其它函数, 它们可能引用别的 pass 的
# 类型/资源 ⇒ 造成 pass 间“串味”。暂不支持用户自定义辅助函数(之后若需要, 须带“按 pass 归属”
# 另行设计)。匹配形如 `TYPE NAME(...) {` 的函数定义(行首; 行内 `//` 注释天然不匹配)。
_ALLOWED_FUNCS = ("MaterialMain", "MaterialDepth", "MaterialVertex")
_FUNC_DEF_RE = re.compile(r"(?m)^\s*[A-Za-z_][\w<>]*\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{")


def enforce_functions(material_src, allowed=_ALLOWED_FUNCS):
    """材质源不得定义除 `allowed` 外的函数; 否则报错(保证三 pass 完全独立)。"""
    extra = []
    for m in _FUNC_DEF_RE.finditer(material_src or ""):
        nm = m.group(1)
        if nm not in allowed and nm not in extra:
            extra.append(nm)
    if extra:
        raise ValueError(
            "材质源出现不允许的函数: %s\n  仅允许 %s(用户自定义辅助函数暂不支持, "
            "以免某 pass 编译时保留其它函数导致串味)。" % (", ".join(extra), "/".join(allowed)))


def strip_line_map(src, name):
    """删除 `void name(...) {...}` 并返回 (新源, 行映射函数)。

    行映射: 新源第 L 行(1基) -> 原源行号(被删段之后的行整体上移, 映射时加回)。
    未含该函数时返回 (src, 恒等)。供 GUI 把编译错误行号映射回编辑器源码。
    """
    sp = _hlsl_fn_span(src, name)
    if not sp:
        return src, (lambda ln: ln)
    start_line = src.count("\n", 0, sp[0]) + 1
    end_line = src.count("\n", 0, sp[1]) + 1
    deleted = end_line - start_line
    stripped = src[:sp[0]] + src[sp[1]:]

    def _m(ln):
        return ln + deleted if ln >= start_line else ln

    return stripped, _m


def strip_line_map_multi(src, names):
    """连续删除多个函数, 并把行映射合成为“新源行 -> 原源行”。

    用于 PS 编译: 须同时剥掉其它 pass 的函数(`MaterialMain`/`MaterialDepth`/`MaterialVertex`)。
    """
    cur, maps = src, []
    for nm in names:
        cur, m = strip_line_map(cur, nm)
        maps.append(m)

    def _m(ln):
        for m in reversed(maps):
            ln = m(ln)
        return ln
    return cur, _m


def vs_hook_source(material_src):
    """材质源 -> VS 钩子源(剥掉两个 PS 函数 MaterialMain/MaterialDepth)。"""
    return _strip_hlsl_fn(_strip_hlsl_fn(material_src, "MaterialMain"), "MaterialDepth")


def _load_bank():
    data = open(P.standard_path(), "rb").read()
    idx = json.load(open(P.standard_index_path(), encoding="utf-8"))
    return data, idx


def standard_variants():
    """distinct 标准 VS 变体 [(label, bank_key)]; 按 blob 内容去重(保序)。供 GUI 预览选择。"""
    data, idx = _load_bank()
    seen, out = set(), []
    for k in idx:
        if not k.startswith("standard_vs|"):
            continue
        e = idx[k]
        h = hashlib.md5(bytes(data[e[0]:e[0] + e[1]])).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        _, pre, tech = k.split("|", 2)
        out.append(("%s · %s" % (pre, tech) if pre else tech, k))
    return out


_d30_map_cache = None


def d30_map():
    """(prefix, family, world) -> 记录 d30(相对 desc 偏移)。由版本目录(records+银行 VS)派生。

    d30 = 记录 `+0x30` = **输入布局码**指针; 其码块随 (family, world) 变。
    "补输入"换族后, 该槽 d30 须重指到"目标 family+world"的码块 ⇒ 需本映射(取自同族 donor 槽)。
    """
    global _d30_map_cache
    if _d30_map_cache is None:
        data, idx = _load_bank()
        recs = json.load(open(P.records_path(), encoding="utf-8"))
        m = {}
        for r in recs:
            if not r.get("vs_kind") or not r.get("d30"):
                continue
            pre, tech = r.get("prefix") or "", r["tech"]
            e = idx.get("standard_vs|%s|%s" % (pre, tech))
            if not e:
                continue
            sp = VG.spec_from_blob(bytes(data[e[0]:e[0] + e[1]]))
            if not sp or not sp.get("family"):
                continue
            m.setdefault((pre, sp["family"], sp["world"]), r["d30"])
        _d30_map_cache = m
    return _d30_map_cache


def build_standard_vs(material_src, bank_key):
    """组装+编译一个标准 VS 变体(注入 material_src 的顶点钩子)。-> (src, dxbc, err)。"""
    data, idx = _load_bank()
    e = idx.get(bank_key)
    if not e:
        return "", None, "无此变体: %s" % bank_key
    ref = bytes(data[e[0]:e[0] + e[1]])
    spec = VG.spec_from_blob(ref)
    if not spec or not spec.get("family"):
        return "", None, "无法从银行 blob 反推 spec"
    from . import semantic_inputs as SI
    vs_in = SI.resolve(SI.parse_preset_decls(material_src), set(), stage="vs",
                       struct_name="VertexInput", recv="v")
    src = VG.build_from_spec(spec, hook=vs_hook_source(material_src) or None, vs_in=vs_in)
    dxbc, err = VG.compile_vs(src)
    return src, dxbc, err


def build(material_src, pass_name="Deferred", template="deferred_bare", iface=None,
          force_full_inputs=False):
    """-> (mmtr bytes, report)。

    iface: 接口(来自 material_iface); None 时按材质源码的 `//! param/tex` 声明自动生成
           (无声明则保持零声明)。
    force_full_inputs: 为 True 时**强制**把 reduced 族(depth/shadow/pick)补输入到完整族
           (`full`/`skin_full`); 默认 False(仅当顶点钩子引用了缺属性字段时自动补)。
    寄存器由 d3dcompiler **自动紧凑**分配(不写 `register`; 见 `material_pass` 文件头)。
    """
    if not P.has_preset():
        raise RuntimeError("缺少预设: 先跑 scripts/gen_presets.py <ref.mmtr>")
    skeleton = P.skeleton()
    recs = json.load(open(P.records_path(), encoding="utf-8"))
    desc = P.desc()
    info = json.load(open(P.info_path(), encoding="utf-8"))
    bank_data = open(P.standard_path(), "rb").read()
    bank = json.load(open(P.standard_index_path(), encoding="utf-8"))

    def bank_blob(key):
        e = bank.get(key)
        return bytes(bank_data[e[0]:e[0] + e[1]]) if e else None

    # 标准 VS: **优先自生**(vs_gen 反推 spec→生成→编译 vs_5_0), 失败/无键回退银行字节。
    # 引擎只看签名+绑定组(离线 62/62 已证自生与银行等价) ⇒ 属行为等价的内部替换。
    vs_stat = {"gen": 0, "bank": 0, "none": 0, "upgraded": 0}

    def vs_for(key):
        """键 -> (kind, dxbc, spec_used, orig_family); kind ∈ {"gen","bank","none"}。

        结果全局缓存(键含钩子签名 + 补输入标志)。_upg 时对 reduced 族做"补输入"(换完整族)。
        """
        ck = (key, _hook_h, _upg)
        c = _vs_cache.get(ck)
        if c is None:
            ref = bank_blob(key)
            if not ref:
                c = ("none", None, None, None)
            else:
                gen, spec_used, orig_fam = None, None, None
                try:
                    spec = VG.spec_from_blob(ref)
                    if spec and spec.get("family"):
                        orig_fam = spec["family"]
                        spec_used = spec
                        if _upg and orig_fam in VG.FULLMAP:
                            spec_used = dict(spec)
                            spec_used["family"] = VG.FULLMAP[orig_fam]
                        gen, _err = VG.compile_vs(
                            VG.build_from_spec(spec_used, hook=vs_hook_src or None,
                                               vs_in=vs_in))
                except Exception:
                    gen = None
                c = (("gen", gen, spec_used, orig_fam) if gen
                     else ("bank", ref, None, None))
            _vs_cache[ck] = c
        return c

    # Pick 族的 PS 全语料唯一(与材质/前缀无关, 恒 3148B) => 预取银行里任意一条 Pick 槽的 key
    pick_key = next((k for k in bank if k.startswith("cutout_ps|") and "|Pick" in k), None)

    # 1) 我们的 PS (接口 + 保活: 材质声明 -> 规范发射; 引擎资源声明 -> 声明即保活)
    from . import material_inputs as INP
    # 每个 pass 函数都必须存在(MaterialMain/MaterialDepth/MaterialVertex; 之后新增 pass 同理)
    require_fn(material_src, "MaterialMain")
    require_fn(material_src, "MaterialDepth")
    require_fn(material_src, "MaterialVertex")
    # 只允许这三个函数(多余函数 -> 报错): 保证三 pass 组装完全独立、不串。
    enforce_functions(material_src)
    # 材质参数命名校验: RDEF 名(声明名)唯一 + 裸名(mdf2/参数表)唯一。
    _confl = MG.param_name_conflicts(MG.parse_decls(material_src)[0])
    if _confl:
        raise ValueError("材质参数命名冲突:\n  " + "\n  ".join(_confl))
    # 每 pass 一份源(剥掉别的 pass 的函数: 未调用函数里的资源引用既不被 DCE、也会影响保活)。
    #   PS 编译须再剥 `MaterialVertex`(VS 钩子用到 VertexInput, PS 侧无此类型)。
    mp_src = _strip_hlsl_fn(_strip_hlsl_fn(material_src, "MaterialDepth"), "MaterialVertex")
    d_src = _strip_hlsl_fn(_strip_hlsl_fn(material_src, "MaterialMain"), "MaterialVertex")
    # VS 钩子源 = 材质源剥掉两个 PS 函数(`MaterialVertex` + 辅助函数; `//!` 是注释, 无害)。
    vs_hook_src = _strip_hlsl_fn(_strip_hlsl_fn(material_src, "MaterialMain"),
                                 "MaterialDepth")
    _hook_h = hashlib.md5(vs_hook_src.encode("utf-8")).hexdigest()
    # 顶点预设(stage="vs"): 全局启用集 -> 生成 VertexInput(字段=已启用预设) + 构造行(v.xxx=...);
    #   同时给出 vs_attrs(需用到的顶点属性) 供"补输入"依赖判定。
    from . import semantic_inputs as SI
    vs_in = SI.resolve(SI.parse_preset_decls(material_src), set(), stage="vs",
                       struct_name="VertexInput", recv="v")
    if vs_in.get("unsupported"):
        raise ValueError("顶点预设依赖的值无法在 VS 供值: %s" % vs_in["unsupported"])
    # "补输入": reduced 族(深度/阴影/拾取)换到完整族。
    # 触发 = 手动开关(force_full_inputs) / 顶点预设依赖缺属性(自动) / 源码指令。
    _auto_upg = bool(vs_in.get("vs_attrs"))
    _upg = bool(force_full_inputs) or _auto_upg or bool(
        re.search(r"(?m)^\s*//!\s*vertex\s+force_full_inputs\b", material_src or ""))
    # 主 pass: 基座 = 固有输入(默认模式); 声明/保活**按 main 过滤**(不再夹带别的 pass 的资源)。
    base = iface if iface is not None else INP.base_iface_for_pass("main", template)
    iface, ka, _rep = INP.build_iface_and_keepalive(
        mp_src, base, pass_name="main", other_code=INP._code_only(d_src),
        all_params=INP.all_params(material_src))
    if iface is None:
        iface = base
    minput = _rep.get("minput")
    ps_blob, err = MP.compile_shading(mp_src, template, iface=iface,
                                      keepalive=ka, minput=minput)
    if err:
        raise ValueError("HLSL 编译失败:\n%s" % err)

    # 1b) 逐实例材质("…Instancing2")槽: 引擎绑 UserMaterialInstances(结构化), 与 cbuffer 风格
    #     主 PS 不自洽 ⇒ 用 **instance 风格 PS**(读 UserMaterialInstances, 纹理 +1,
    #     实例索引 NOINTERPOLATOR0)。判定按**预设里该槽 PS 是否用 UMI**(records.ps_inst)。
    #     instance PS 的意义就是读 UMI(=UserMaterial) ⇒ **仅当材质确有 UserMaterial(声明了参数)
    #     且模板有 `<template>_instance` 对应件时才另编**; 否则该槽复用主 PS。
    inst_tpl = template + "_instance"
    inst_slots = [r for r in recs if r.get("ps_inst")]
    has_um = any(c["name"] == "UserMaterial" for c in (iface or {}).get("cbuffers", []))
    ps_inst = None
    if (inst_slots and has_um
            and os.path.exists(os.path.join(MP._TDIR, inst_tpl + ".hlsl"))):
        ps_inst, err_inst = MP.compile_shading(mp_src, inst_tpl, iface=iface,
                                               style="instance", keepalive=ka,
                                               minput=minput)
        if err_inst:
            raise ValueError("instance PS 编译失败:\n%s" % err_inst)

    # 1c) 深度族 PS: **恒组装** —— MaterialDepth(必需) -> deferred_depth 模板(剥掉 MaterialMain)。
    # 基座 = **空**(depth 未声明引擎资源依赖); 声明/接口按 depth 过滤; 保活为空
    #     (深度无颜色输出, 用不了主 pass 的 o0 保活语句)。
    iface_d, _ka_d, _rep_d = INP.build_iface_and_keepalive(
        d_src, INP.base_iface_for_pass("depth"), pass_name="depth",
        all_params=INP.all_params(material_src))
    ps_depth = depth_hook_ps(d_src, iface_d, None)

    # 2) 逐槽解析程序
    slots = []
    for r in recs:
        pre, tech = r.get("prefix") or "", r["tech"]
        vs = None
        _src = None
        rel30 = None
        if r.get("vs_kind"):
            _src, vs, _vsp, _vof = vs_for("standard_vs|%s|%s" % (pre, tech))
            if _src == "gen":
                vs_stat["gen"] += 1
            elif _src == "bank":
                vs_stat["bank"] += 1
            elif _src == "none":
                vs_stat["none"] += 1
            # 补输入: 该槽被升级(reduced -> 完整族) ⇒ 记录 d30 须重指到目标族码块。
            if _src == "gen" and _vof in VG.FULLMAP and _vsp.get("family") != _vof:
                _m = d30_map()
                rel30 = (_m.get((pre, _vsp["family"], _vsp["world"]))
                         or _m.get((pre, _vsp["family"])))
                vs_stat["upgraded"] += 1
        cs = bank_blob("%s|%s|%s" % (r["cs_kind"], pre, tech)) if r.get("cs_kind") else None
        if r.get("ps_kind") == "material_ps":
            ps = ps_inst if (ps_inst is not None and r.get("ps_inst")) else ps_blob
        elif r.get("ps_kind") == "cutout_ps":
            if tech.startswith("Pick"):
                # Pick 族 = 引擎固定的“屏幕拾取”PS(与材质无关; 全语料唯一一条 3148B)
                # => 银行任意一条 Pick 槽即可。待办: 之后换自生的固定 pick PS。
                ps = bank_blob(pick_key) if pick_key else ps_depth
            else:
                # 深度/阴影族: 恒用 MaterialDepth 自生(默认实现 = 不丢弃, 效果同极简 PS)
                ps = ps_depth
        elif r.get("ps_kind"):
            ps = bank_blob("%s|%s|%s" % (r["ps_kind"], pre, tech))
        else:
            ps = None
        slots.append({"slot": r["slot"], "rec": r, "vs": vs, "ps": ps, "cs": cs,
                      "rel30": rel30})

    # 3) 绑定组(RDEF 派生) + 去重
    cbs_of = {}       # (vs,ps) -> {name: (size, count, members)}

    def cb_defs_for(vs, ps, names):
        def cbs(bl):
            out = {}
            for (nm, sz, mem) in (R.rdef_cbuffers(bl) or []):
                # UserMaterial 成员: 写进 mmtr 参数表用**裸名**(去 `VAR_`), 对齐原版
                # (RDEF 成员带 `VAR_`, 表/mdf2 裸名); 其它引擎 cbuffer 成员保持原名。
                _mb = MG.param_base_name if nm == "UserMaterial" else (lambda x: x)
                out.setdefault(nm, (sz, len(mem),
                                    tuple((_mb(m[0]), m[2], m[1]) for m in mem)))
            return out
        if (vs, ps) not in cbs_of:
            d = {}
            d.update(cbs(ps) if ps else {})
            for k, v in (cbs(vs) if vs else {}).items():
                d.setdefault(k, v)
            cbs_of[(vs, ps)] = d
        d = cbs_of[(vs, ps)]
        return [(nm,) + d[nm] for nm in names if nm in d]

    groups, order, gkey = {}, [], {}
    for i, s in enumerate(slots):
        vs, ps = s["vs"], s["ps"]
        if not vs and not ps:
            continue
        bo = T.derive_group(vs, ps)
        cbd = cb_defs_for(vs, ps, bo["cb"])
        t4 = _uav_order(ps) or _uav_order(vs) or []
        key = (tuple(cbd), tuple(bo["smp"]), tuple(bo["tex"]), tuple(t4))
        if key not in groups:
            groups[key] = {"cb": cbd, "smp": bo["smp"], "tex": bo["tex"], "t4": t4,
                           "vs": vs, "ps": ps}
            order.append(key)
        gkey[i] = key

    # 3b) 描述符区: 预设原样(供未建模指针 +0x30/+0x68) + 我们**按组派生**的 8B 条目。
    #     ⚠ 不能整体照搬预设: 我们的 PS 资源集与参考不同 ⇒ 名称与条目会错位(绑不上)。
    def _grp_names(g, kind):
        return [d[0] for d in g["cb"]] if kind == "cb" else list(g[kind])

    pre = P.desc()
    pad = b"\x00" * ((8 - (len(pre) % 8)) % 8)
    gen, gplace = bytearray(), {}
    for k in order:
        g = groups[k]
        vm, pm = T._bind_info_map(g.get("vs")), T._bind_info_map(g.get("ps"))
        pos = {}
        for kind in ("cb", "smp", "tex"):
            pos[kind] = len(pre) + len(pad) + len(gen)
            for nm in _grp_names(g, kind):
                vi, pi = vm.get(nm), pm.get(nm)
                if kind == "cb":
                    typ = 0xFF
                elif kind == "smp":
                    typ = 0x00
                else:
                    typ = T._TEX_DIM_TYPE.get((vi or pi or (0, 0, "2d"))[2], 0x02)
                stage = 0x11 if (vi and pi) else (0x01 if vi else 0x10)
                code = (typ << 24) | (stage << 16) | (pi[1] if pi else 0)
                # 第一条 u32 = VS 绑定寄存器(仅 VS 侧/共享才有)
                gen += struct.pack("<II", (vi[1] if vi else 0), code)
        gplace[k] = pos
    desc = pre + pad + bytes(gen)

    # 4) 尾段各段(pool/cb/param/desc/string)
    smp_u, tex_u, cb_u, t4_u = {}, {}, {}, {}
    smp_o, tex_o, cb_o, t4_o = [], [], [], []
    for k in order:
        g = groups[k]
        for key, uniq, od in ((tuple(g["smp"]), smp_u, smp_o),
                              (tuple(g["tex"]), tex_u, tex_o),
                              (tuple(g["cb"]), cb_u, cb_o),
                              (tuple(g["t4"]), t4_u, t4_o)):
            if key not in uniq:
                uniq[key] = None
                od.append(key)

    names, nset = [], set()

    def use(nm):
        if nm and nm not in nset:
            nset.add(nm)
            names.append(nm)

    for k in order:
        g = groups[k]
        for d in g["cb"]:
            use(d[0])
            for (mn, _ms, _mo) in d[3]:
                use(mn)
        for nm in g["smp"] + g["tex"] + g["t4"]:
            use(nm)
    for r in recs:
        use(_variant_name(r))
    for nm in (info.get("ptnames") or []):
        use(nm)
    use(info.get("str10") or "")
    str_pool, str_rel = bytearray(), {}
    for nm in names:
        str_rel[nm] = len(str_pool)
        str_pool += nm.encode("latin1", "replace") + b"\x00"

    param, param_name_at, pdef = bytearray(), [], {}
    for ck in cb_o:
        for d in ck:
            if d in pdef:
                continue
            pdef[d] = len(param)
            for (mn, ms, mo) in d[3]:
                param_name_at.append((len(param), mn))
                param += struct.pack("<IIII", 0, 0, ascii_hash(mn), (ms << 16) | mo)

    cb, cb_name_at, cb_mem_at = bytearray(), [], []
    for ck in cb_o:
        cb_u[ck] = len(cb)
        for d in ck:
            cb_name_at.append((len(cb), d[0]))
            cb_mem_at.append((len(cb), d))
            cb += struct.pack("<QIIIIQ", 0, ascii_hash(d[0]), 0, d[1], d[2], 0)

    pool, pool_name_at = bytearray(), []
    for sk in smp_o:
        smp_u[sk] = len(pool)
        for nm in sk:
            pool_name_at.append((len(pool), nm))
            pool += struct.pack("<QII", 0, ascii_hash(nm), 0)
    for tk in tex_o:
        tex_u[tk] = len(pool)
        for nm in tk:
            pool_name_at.append((len(pool), nm))
            pool += struct.pack("<QII", 0, ascii_hash(nm), 0)
    for qk in t4_o:
        t4_u[qk] = len(pool)
        for nm in qk:
            pool_name_at.append((len(pool), nm))
            pool += struct.pack("<QII", 0, ascii_hash(nm), 0)

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
        struct.pack_into("<Q", cb, off + 24, param_base + pdef[d])
    for off, nm in param_name_at:
        struct.pack_into("<I", param, off, str_base + str_rel[nm])
    tail = bytes(pool) + bytes(cb) + bytes(param) + bytes(desc) + bytes(str_pool)
    blob_start = SKELETON_HI + len(tail)

    # 5) blob 区(去重)
    blob, off_of = bytearray(), {}

    def put(b):
        nonlocal blob
        h = hashlib.md5(b).hexdigest()
        if h not in off_of:
            off_of[h] = blob_start + len(blob)
            blob += b
        return off_of[h]

    cb_rel = {k: cb_u[tuple(groups[k]["cb"])] for k in order}
    smp_rel = {k: smp_u[tuple(groups[k]["smp"])] for k in order}
    tex_rel = {k: tex_u[tuple(groups[k]["tex"])] for k in order}
    t4_rel = {k: t4_u[tuple(groups[k]["t4"])] for k in order}

    # 6) 写记录
    head = bytearray(skeleton)
    for i, s in enumerate(slots):
        if not (s["vs"] or s["ps"] or s["cs"]):
            continue
        r = s["rec"]
        base = REC_LO + i * REC_SIZE
        vs_off = put(s["vs"]) if s["vs"] else 0
        ps_off = put(s["ps"]) if s["ps"] else 0
        cs_off = put(s["cs"]) if s["cs"] else 0
        struct.pack_into("<I", head, base + 0x00, ps_off)
        struct.pack_into("<I", head, base + 0x08, cs_off)
        struct.pack_into("<I", head, base - 0x28, vs_off)
        struct.pack_into("<I", head, base - 0x20, vs_off)
        struct.pack_into("<I", head, base + 0x9C, len(s["ps"]) if s["ps"] else 0)
        struct.pack_into("<I", head, base + 0x88, len(s["vs"]) if s["vs"] else 0)
        struct.pack_into("<I", head, base + 0x8C, len(s["vs"]) if s["vs"] else 0)
        struct.pack_into("<I", head, base + 0x18, int(r.get("x18") or 0))
        struct.pack_into("<I", head, base + 0xC8, int(r.get("c8") or 0))
        struct.pack_into("<I", head, base + 0xA0, len(s["cs"]) if s["cs"] else 0)
        for fo, rk in ((0x30, "d30"), (0x68, "d68")):
            rel = (s["rel30"] if (fo == 0x30 and s.get("rel30") is not None)
                   else r.get(rk))
            struct.pack_into("<I", head, base + fo, desc_base + rel if rel else 0)
        k = gkey.get(i)
        if k is not None:
            g = groups[k]
            gp = gplace[k]
            struct.pack_into("<I", head, base + 0x38,
                             desc_base + gp["cb"] if g["cb"] else 0)
            struct.pack_into("<I", head, base + 0x48,
                             desc_base + gp["smp"] if g["smp"] else 0)
            struct.pack_into("<I", head, base + 0x58,
                             desc_base + gp["tex"] if g["tex"] else 0)
            struct.pack_into("<I", head, base + 0x40, cb_base + cb_rel[k] if g["cb"] else 0)
            struct.pack_into("<I", head, base + 0x50, pool_base + smp_rel[k] if g["smp"] else 0)
            struct.pack_into("<I", head, base + 0x60, pool_base + tex_rel[k] if g["tex"] else 0)
            struct.pack_into("<I", head, base + 0x70, pool_base + t4_rel[k] if g["t4"] else 0)
        struct.pack_into("<I", head, base + 0x80, 0)
        nrel = str_rel.get(_variant_name(r))
        struct.pack_into("<I", head, base + 0xD8, (str_base + nrel) if nrel is not None else 0)
        # 计数(binding 口径)
        vbo = T.derive_group(s["vs"], None) if s["vs"] else {"cb": [], "smp": [], "tex": []}
        pbo = T.derive_group(None, s["ps"]) if s["ps"] else {"cb": [], "smp": [], "tex": []}
        g = groups[k] if k is not None else {"cb": [], "smp": [], "tex": [], "t4": []}
        a8, nb_ps = len(vbo["cb"]), len(pbo["cb"])
        nsmp = len(g["smp"])
        b4, b8 = len(vbo["tex"]), len(pbo["tex"])
        c6 = len(g["cb"])
        struct.pack_into("<I", head, base + 0xA8, a8)
        cur = _u32(head, base + 0xAC)
        struct.pack_into("<I", head, base + 0xAC, (cur & 0xFFFF0000) | (nb_ps & 0xFFFF))
        struct.pack_into("<I", head, base + 0xB0, nsmp << 16)
        struct.pack_into("<I", head, base + 0xB4, b4)
        struct.pack_into("<I", head, base + 0xB8, b8)
        struct.pack_into("<I", head, base + 0xA4, a8 + nb_ps + nsmp + b4 + b8)
        struct.pack_into("<I", head, base + 0xC4, (nsmp << 24) | (c6 << 16))
        head[base + 0xCC] = len(g["tex"]) & 0xFF
        head[base + 0xCD] = len(g["t4"]) & 0xFF
        head[base + 0xCE] = 0

    # 7) 程序表(PT): 复制预设 + 重定位指针(blob 按内容映射; desc/池/表 按相对偏移)
    pt = bytearray(P.pt())
    bm = {int(k): v for k, v in P.blobmap().items()}
    rb = info["bases"]
    ptn = info.get("ptnames") or []
    pt_miss, pt_nomap = [], []
    for off in range(0, len(pt) - 3, 4):
        v = _u32(pt, off)
        if not v:
            continue
        if v in bm:
            if bm[v] in off_of:
                nv = off_of[bm[v]]
            else:
                nv = 0
                pt_miss.append((off, v, bm[v]))
        elif rb["blob_start"] <= v < (rb["blob_start"] + 0x200000):
            nv = v
            pt_nomap.append((off, v))
        elif rb["desc"] <= v < rb["string"]:
            nv = desc_base + (v - rb["desc"])
        elif rb["string"] <= v < rb["blob_start"]:
            # PT 名: 按对应条目的名字重定位
            k = (off - 0x104) // 264 if (off - 0x104) % 264 == 0 else -1
            nm = ptn[k] if 0 <= k < len(ptn) else ""
            nv = str_base + str_rel[nm] if nm in str_rel else 0
        elif rb["pool"] <= v < rb["cbuffer"]:
            k = v - rb["pool"]
            nv = pool_base + k if k < len(pool) else 0
        elif rb["cbuffer"] <= v < rb["param"]:
            k = v - rb["cbuffer"]
            nv = cb_base + k if k < len(cb) else 0
        elif rb["param"] <= v < rb["desc"]:
            k = v - rb["param"]
            nv = param_base + k if k < len(param) else 0
        else:
            nv = v
        struct.pack_into("<I", pt, off, nv)
    head[0x14:0x14 + len(pt)] = pt

    # 7b) 保护尾部 post 表: 记录栅格(槽 1082 的 base-0x28)会与引擎的 InputLayout
    #     元素表区 [0x46210,0x46350) 交叠 —— 必须从骨架原样恢复, 否则元素数被覆写为 0。
    head[POST_LO:SKELETON_HI] = P.post()

    # 8) 容器头
    struct.pack_into("<I", head, 0x08, blob_start)
    struct.pack_into("<I", head, 0x10, str_base + str_rel[info.get("str10") or ""])
    out = bytes(head) + tail + bytes(blob)

    # 8b) 输入边界校验: 我们的 PS 的 ISGN 必须 ⊆ 各材质槽对应 VS 的 OSGN
    #     (DX12 建 PSO 会校验; 超标则整个材质 pass 被静默跳过 —— 见 PROJECT_SUMMARY "DX12")
    from . import dxbc_sig as _SG
    boundary, _seen = [], set()
    for s in slots:
        if not (s["ps"] and s["vs"]) or s["rec"].get("ps_kind") != "material_ps":
            continue
        _k = (hashlib.md5(s["ps"]).hexdigest(), hashlib.md5(s["vs"]).hexdigest())
        if _k in _seen:
            continue
        _seen.add(_k)
        for _n, _sem, _pm, _vm in _SG.check_input_supported(
                _SG.input_signature(s["ps"]), _SG.output_signature(s["vs"])):
            boundary.append((s["slot"], _n, _sem, _pm, _vm))

    # 8c) 纹理寄存器布局校验: 材质贴图 SRV 应"从 t0 起、连续、无缺口"
    #     (DX12 有缺口会 GPU 崩 0x887a0006; 100% 原版 Deferred PS 如此)
    _srv_skip = (0, 3) + _UAV_TYPES        # cbuffer/sampler/UAV 不算 SRV
    tex_regs = sorted({bp for (_n, _t, bp, _d, _r)
                       in (R.rdef_bind_info(ps_blob) or []) if _t not in _srv_skip})
    tex_gap = [i for i in range(len(tex_regs)) if tex_regs[i] != i]

    return out, {
        "size": len(out), "blob_start": blob_start, "n_blobs": len(off_of),
        "ps_size": len(ps_blob), "records": len(slots),
        "groups": len(order), "str10": info.get("str10"),
        "pt_miss": pt_miss, "pt_nomap": pt_nomap,
        "boundary": boundary, "boundary_pairs": len(_seen),
        "tex_regs": tex_regs, "tex_gap": tex_gap,
        "vs_gen": vs_stat["gen"], "vs_bank": vs_stat["bank"],
        "vs_missing": vs_stat["none"], "vs_cache": len(_vs_cache),
        "vs_upgrade": _upg, "vs_upgraded": vs_stat["upgraded"],
        "vs_attrs": list(vs_in.get("vs_attrs") or []),
        "vs_full_inputs_auto": _auto_upg,
        "iface_tex": [t["name"] for t in (iface or {}).get("textures", [])],
        "depth_ps": "material",
        "presets": _rep.get("presets") or [],
        "preset_unknown": _rep.get("preset_unknown") or [],
        "preset_unsupported": _rep.get("preset_unsupported") or [],
        "lock": _rep.get("lock") or {},
    }
