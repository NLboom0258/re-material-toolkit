#!/usr/bin/env python3
"""材质"输入体系": 允许清单(固有标准集 / 材质参数 / 引擎候选) + 声明/保活生成。

数据来自预设 `presets/v01100004/inputs.json`(由 `scripts/_gen_inputs.py` 生成;
缺文件时退化为只从 iface.json 取标准集)。

用法:
    INP.std()        # 固有(标准接口): [{name,kind,type,reg,source}]
    INP.params()     # 材质参数(UserMaterial 成员): [{name,type,offset,source}]
    INP.candidates() # 引擎候选(自定义可选; 带 members/size 或 fmt/dim)
    INP.by_kind("texture", include_std=False)   # 按类别过滤
    INP.build_iface_and_keepalive(src, base)    # 声明+保活(供 nogen / GUI 共用)

声明行(材质源码顶部, 与 `//! param` / `//! tex` 同区):
    //! param <type> <name>    材质参数(进 UserMaterial)
    //! tex <name>             材质贴图(接标准贴图之后 t4 起)
    //! engine <Name>          引擎已有资源(cbuffer/texture/sampler; 声明即保活)

⚠ 引擎资源寄存器**自动分配**(避开标准集已占用); 引擎是否真为其填值 = 待实机验证。
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

KINDS = ("cbuffer", "texture", "sampler", "param")

# 引擎资源用途说明(供"引擎资源添加页"人读)。名字 -> 一句话; 不确定的标"(推测)"。
RESOURCE_DESC = {
    "SceneInfo": "场景/相机: 视图投影矩阵、近远平面、屏幕尺寸/倒数、视锥、裁剪面。",
    "GBufferType": "GBuffer 类型标志: 写 o2.w 供下游判定 GBuffer 类型。",
    "Tonemap": "色调映射/曝光: 曝光值、tonemap 范围、自动曝光开关、AA 参数。",
    "UserMaterial": "材质参数(mdf2 传入): VAR_* 成员表(颜色/金属/粗糙/SSS 等)。",
    "RootConstant": "根常量(DX12 root constants, 32bit): 常用于 CS 分发/索引等少量参数。",
    "cbCSSkinning": "计算着色器蒙皮参数: 顶点/UV 数、各属性字节偏移。",
    "ShadowCastInfo": "阴影投射偏差: 深度/斜率偏置(阴影贴图渲染用)。",
    "EnvironmentInfo": "环境/帧信息: 时间(ms)、帧计数、奇偶帧、全局参数、破坏PBR参数。",
    "CheckerBoardInfo": "棋盘格渲染(Checkerboard)参数: 缩放/偏置/开关(交错渲染/上采样)。(推测)",
    "World": "世界矩阵: worldMat/prevWorldMat/关节偏移(蒙皮/实例用)。",
    "OutdoorLightProbeParam": "室外光照探针网格: 网格 offset/scale/depth/min/max。",
    "ShadowSamplingRotation": "阴影采样旋转: ShadowSamplePoints[8](阴影滤波采样点)。",
    "DirectionalLightParameter": "方向光: 方向/颜色/视投影/SDSM/级联(cascade)参数。",
    "PickState": "拾取(鼠标)状态: 写地址、拾取位置、可选标签。",
    "WhitePtSrv": "引擎白点 raw buffer(ByteAddressBuffer): 曝光/白点基准值; 只能 .Load()。",
    "InstanceWorldInfo": "实例世界信息(struct buffer): 每实例世界相关数据。",
    "InputByteBuffer": "输入字节缓冲: 原始字节数据(按偏移解析)。",
    "bDestPosVB": "目标位置顶点缓冲(CS 蒙皮输出)。",
    "SkinningMatrices": "蒙皮矩阵缓冲(骨骼矩阵)。",
    "IndirectIndicesBuffer": "间接索引缓冲(间接绘制/多实例)。",
    "WorldInstances": "世界实例数据(struct buffer): 每实例世界矩阵等。",
    "UserMaterialInstances": "材质参数 per-instance 结构化缓冲(用于 …Instancing2 槽)。",
    "LightCullingVolumeSRV": "光照剔除体(cluster/体素)。",
    "LightCullingListSRV": "光照剔除列表: 每簇内的光源索引。",
    "OutdoorProbesSRV": "室外光照探针数据。",
    "IBLCubemapArrayList2SRV": "IBL 立方图数组列表。",
    "LightParameterSRV": "光源参数数组(位置/颜色/范围等)。",
    "ShadowParameterSRV": "阴影参数数组。",
    "AreaLightParameterSRV": "面光源参数数组。",
    "IBLCubemapBVHSRV": "IBL 立方图 BVH(加速结构, 探针查找)。",
    "IBLCubemap2DArraySRV": "IBL 立方图(以 2D 数组形式, 便于采样)。",
    "CubemapSRV": "立方图(scene cube)。",
    "IESLightTableSRV": "IES 光源分布表(光度曲线)。",
    "ShadowMapSRV": "阴影贴图(2D 数组, 含级联)。",
    "SSAOResult": "SSAO 结果纹理(屏幕空间环境光遮蔽)。",
    "PickAddressListCount": "拾取地址列表计数。",
    "PickAddressList": "拾取地址列表。",
    "AutomaticWrap": "环绕(Wrap)采样器: 引擎自动选寻址。(推测)",
    "BilinearClamp": "双线性 + 钳制(Clamp)。",
    "BilinearMirror": "双线性 + 镜像(Mirror)。",
    "TrilinearMirror": "三线性 + 镜像(Mirror, 含 mip)。",
    "LinearCompare": "线性比较采样器(阴影 PCF 用)。",
}


def desc_of(name):
    """引擎资源的一句话说明(未知返回空串)。"""
    return RESOURCE_DESC.get(name, "")

_ENGINE_RE = re.compile(r"^\s*//!\s*engine\s+(\w+)\s*$")
_REG_PRE = {"cbuffer": "b", "texture": "t", "sampler": "s"}
# GetDimensions 参数个数(保活用)
_DIM_ARITY = {"2d": 2, "2dms": 2, "2darray": 3, "2dmsarray": 3, "1d": 2,
              "1darray": 3, "3d": 3, "cube": 3, "cubearray": 4, "buffer": 1}


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
    return load()["std"]


def params():
    return load()["params"]


def candidates():
    return load()["candidates"]


def by_kind(kind, include_std=True):
    out = []
    if include_std:
        out += [e for e in std() if e["kind"] == kind]
    out += [e for e in candidates() if e["kind"] == kind]
    return out


def find(name):
    """按名找候选(引擎资源)。"""
    for e in candidates():
        if e["name"] == name:
            return e
    return None


def catalog_text():
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
        extra = ""
        if e["kind"] == "cbuffer":
            extra = "%d 成员/size=%d" % (len(e.get("members", [])), e.get("size", 0))
        elif e["kind"] == "texture":
            extra = "%s/%s" % (e.get("fmt"), e.get("dim"))
        lines.append("  [%-7s] %-32s %-14s ref=%-5s %s"
                     % (e["kind"], e["name"], extra, e.get("reg_ref", ""),
                        e.get("source", "")))
    return "\n".join(lines)


# ---------------------------------------------------------------- 寄存器
def _num(reg):
    return int(re.sub(r"\D", "", reg or "") or 0)


def assign_regs(sel, base_iface):
    """给所选引擎项**自动分配**寄存器(避开 base_iface 已占用)。返回 {name: reg}。"""
    used = {"cbuffer": set(), "texture": set(), "sampler": set()}
    for c in (base_iface or {}).get("cbuffers", []):
        used["cbuffer"].add(_num(c.get("reg")))
    for t in (base_iface or {}).get("textures", []):
        used["texture"].add(_num(t.get("reg")))
    for s in (base_iface or {}).get("samplers", []):
        used["sampler"].add(_num(s.get("reg")))
    out = {}
    for e in sel:
        k = e["kind"]
        if k not in used:
            continue
        n = 0
        while n in used[k]:
            n += 1
        used[k].add(n)
        out[e["name"]] = "%s%d" % (_REG_PRE[k], n)
    return out


def _add_engine(iface, e, reg):
    """把引擎候选加成标准接口形状(cbuffer/texture/sampler)。"""
    if e["kind"] == "cbuffer":
        iface["cbuffers"].append({"name": e["name"], "reg": reg,
                                  "members": list(e.get("members", []))})
    elif e["kind"] == "texture":
        iface["textures"].append({"name": e["name"], "reg": reg,
                                  "fmt": e.get("fmt"), "dim": e.get("dim")})
    elif e["kind"] == "sampler":
        iface["samplers"].append({"name": e["name"], "reg": reg,
                                  "cmp": bool(e.get("cmp"))})


# ---------------------------------------------------------------- 保活
def _member_ref(t, name):
    """给一个成员构造"安全标量引用"(用于保活)。"""
    if "[" in name:                       # 数组
        return "%s[0]" % name
    if t in ("float", "int", "uint", "bool", "half"):
        return "%s" % name
    if t in ("float2", "float3", "float4", "int2", "int3", "int4",
             "uint2", "uint3", "uint4"):
        return "%s.x" % name
    if "x" in t:                          # 矩阵
        return "%s[0][0]" % name
    return "%s.x" % name


def keepalive_hlsl(items):
    """生成死分支内的"保活"语句(引用各资源, 防被编译器剔除)。

    items: [{"kind":"texture|cbuffer|sampler","name":..,"reg":..,"entry":catalog,..}]
    sampler 无法单独"使用"⇒ 略过(其存活取决于是否被采样)。
    """
    lines, k = [], 0
    for it in items:
        kd, nm = it["kind"], it["name"]
        if kd == "texture":
            fmt = (it.get("entry") or {}).get("fmt")
            dim = (it.get("entry") or {}).get("dim") or "2d"
            if fmt == "byte":
                lines.append("%s.GetDimensions(_k0); _ka += float(_k0);" % nm)
            else:
                n = _DIM_ARITY.get(dim, 2)
                args = ", ".join("_k%d" % j for j in range(n))
                lines.append("%s.GetDimensions(%s); _ka += float(_k0);" % (nm, args))
            k += 1
        elif kd == "cbuffer":
            mem = ((it.get("entry") or {}).get("members") or [])
            if mem:
                lines.append("_ka += float(%s);" % _member_ref(mem[0]["type"], mem[0]["name"]))
        elif kd == "param":
            lines.append("_ka += float(%s);" % _member_ref(it.get("type", "float"), nm))
    if not lines:
        return ""
    head = "        uint _k0, _k1, _k2, _k3; float _ka = 0.0;"
    body = ["        " + s for s in lines]
    tail = "        o.o0.x += _ka;"
    return "\n".join([head] + body + [tail])


# ---------------------------------------------------------------- 组装
def parse_engine_decls(material_src):
    out = []
    for line in (material_src or "").splitlines():
        m = _ENGINE_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def build_iface_and_keepalive(material_src, base_iface):
    """**统一入口**: 基础接口 + 材质声明(param/tex) + 引擎资源(engine) -> (iface, 保活HLSL, report)。

    无任何声明时返回 (None, "", report)。
    """
    from . import material_gen as MG
    from . import material_iface as MI
    import copy
    params, textures = MG.parse_decls(material_src)
    eng_names = parse_engine_decls(material_src)
    report = {"engine": [], "params": [], "textures": [], "regs": {}}
    if not params and not textures and not eng_names:
        return None, "", report

    if base_iface is None:
        base_iface = {"cbuffers": [{"name": "UserMaterial", "reg": "b3", "members": []}],
                      "textures": [],
                      "samplers": [{"name": "AutomaticWrap", "reg": "s0", "cmp": False}]}
    known_p = {m["name"] for c in base_iface["cbuffers"] for m in c["members"]}
    known_t = {t["name"] for t in base_iface["textures"]}
    params = [(n, t) for (n, t) in params if n not in known_p]
    textures = [n for n in textures if n not in known_t]
    iface = copy.deepcopy(base_iface)
    if params or textures:
        iface, added = MI.extend(iface, "UserMaterial", params, textures)
        report["params"] = [n for n, _ in params]
        report["textures"] = list(textures)

    # 引擎资源(去重: 已在标准集里的跳过)
    eng = []
    for nm in eng_names:
        e = find(nm)
        if e is None or nm in known_p or nm in known_t:
            continue
        if any(x["name"] == nm for x in eng):
            continue
        eng.append(e)
    regs = assign_regs(eng, iface)
    for e in eng:
        _add_engine(iface, e, regs[e["name"]])
    report["engine"] = [e["name"] for e in eng]
    report["regs"] = regs

    # 保活: 引擎资源 + 新材质贴图 + 新材质参数
    items = [{"kind": e["kind"], "name": e["name"], "reg": regs[e["name"]], "entry": e}
             for e in eng if e["kind"] != "sampler"]
    tmap = {t["name"]: t for t in iface["textures"]}
    for nm in textures:
        items.append({"kind": "texture", "name": nm,
                      "entry": tmap.get(nm)})
    for nm, ty in params:
        items.append({"kind": "param", "name": nm, "type": ty})

    # 贴图寄存器布局: 材质贴图 SRV 必须"从 t0 起、连续、无缺口"(2026-09-28 实测;
    #   100% 原版 Deferred PS 如此; 缺口在 DX12 会 GPU 崩 0x887a0006)。⇒ 只要材质用到任一贴图
    #   (标准/自定义/引擎), 就把 t0..最高用到的寄存器 之间的贴图**全部保活**, 令 RDEF 的纹理
    #   寄存器构成"从 t0 起的连续段"(WhitePtSrv@t0 恒保留)。
    have = {it["name"] for it in items}
    used = set(textures)                       # 自定义 `//! tex`
    for t in iface["textures"]:                # 源码直接引用 / 已保活的贴图
        if t["name"] in have or re.search(
                r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(t["name"]),
                material_src or ""):
            used.add(t["name"])
    regs_used = [_num(t["reg"]) for t in iface["textures"] if t["name"] in used]
    tmax = max(regs_used) if regs_used else -1
    for t in sorted(iface["textures"], key=lambda x: _num(x["reg"])):
        if _num(t["reg"]) <= tmax and t["name"] not in have:
            items.append({"kind": "texture", "name": t["name"], "entry": t})
            have.add(t["name"])

    ka = keepalive_hlsl(items)
    return iface, ka, report
