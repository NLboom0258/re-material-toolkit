#!/usr/bin/env python3
"""材质"输入体系": 允许清单(固有标准集 / 材质参数 / 引擎候选) + 声明/保活生成。

数据来自预设 `presets/v01100004/inputs.json`(由 `scripts/gen_inputs.py` 生成;
缺文件时退化为只从 iface.json 取标准集)。

用法:
    INP.std()        # 固有(标准接口): [{name,kind,type,reg,source}]
    INP.params()     # 材质参数(UserMaterial 成员; 预设默认空, 每材质自由): [{name,type,offset,source}]
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

# 引擎资源用途说明(供"引擎资源添加页"/输入页人读)。名 -> **一句话(总体定位)**。
# cbuffer 这里只写"整体定位"; 其**成员**各自的说明见 MEMBER_DESC。不确定的标"(推测)"。
RESOURCE_DESC = {
    "SceneInfo": "场景/相机信息(矩阵、屏幕尺寸、近远平面、视锥等)。",
    "GBufferType": "GBuffer 类型标志(写 o2.w 供下游判定 GBuffer 类型)。",
    "Tonemap": "色调映射/曝光/AA 参数(屏幕后处理用)。",
    "UserMaterial": "材质参数(mdf2 传入): VAR_* 成员表。",
    "RootConstant": "通用根常量(DX12 root constants, 32bit)。",
    "cbCSSkinning": "计算着色器蒙皮(CS skinning)参数。",
    "ShadowCastInfo": "阴影投射偏差(阴影贴图渲染)。",
    "EnvironmentInfo": "环境/帧信息(时间、帧号、全局参数、破坏PBR参数)。",
    "CheckerBoardInfo": "棋盘格(Checkerboard)渲染参数。(推测)",
    "World": "世界矩阵(当前/上一帧)与关节偏移。",
    "OutdoorLightProbeParam": "室外光照探针网格参数。",
    "ShadowSamplingRotation": "阴影采样旋转(滤波采样点)。",
    "DirectionalLightParameter": "方向光参数(方向/颜色/级联/阴影)。",
    "PickState": "拾取(鼠标)状态。",
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
    # ---- 2026-09-30 语料扫描新增(定义取自原版 blob; 说明含推测) ----
    "FogParam": "雾参数(内散射色/密度/高度衰减/最大不透明)。",
    "WindInfo": "风信息(风数量)。",
    "PrimitiveMeshConstant": "图元网格常量(程序化网格)。",
    "cbHeightField": "高度场(地形/水面高度)。(推测)",
    "cbHeightFieldPartial": "高度场(局部)。(推测)",
    "cbWaterSurfaceDeferred": "水面延迟着色参数(混合色/法线/自发光)。",
    "LightInfo": "光源计数(点光/面光、前向/总数)。",
    "BSPTree": "BSP 树缓冲(透明排序/加速结构)。(推测)",
    "TetraCoordinate": "四面体坐标缓冲(光照探针插值坐标)。",
    "IndirectProbe": "间接光照探针缓冲。",
    "ReadonlyDepth": "只读深度缓冲(场景深度)。",
    "PivotBuffer": "枢轴缓冲(风/摆动形变)。(推测)",
    "WindParams": "风参数缓冲。",
    "PrimitiveMeshCBInstances": "图元网格实例缓冲。",
    "BilinearWrap": "双线性 + 环绕(Wrap)采样器。",
    "PointWrap": "点采样 + 环绕(Wrap)。",
    "BilinearBorder": "双线性 + Border。",
    "TrilinearBorder": "三线性 + Border(含 mip)。",
    "TrilinearWrap": "三线性 + 环绕(Wrap)。",
    "PointClamp": "点采样 + 钳制(Clamp)。",
}


def desc_of(name):
    """引擎资源的一句话说明(总体定位; 未知返回空串)。"""
    return RESOURCE_DESC.get(name, "")


# cbuffer 成员说明: cbuffer名 -> {成员名: 一句话}。供输入页/引擎资源选择器逐成员展示。
MEMBER_DESC = {
    "SceneInfo": {
        "viewProjMat": "视图投影矩阵(世界→裁剪)",
        "transposeViewMat": "视图矩阵(转置 3x4)",
        "transposeViewInvMat": "视图逆矩阵(转置 3x4; 可提相机世界位置/朝向)",
        "projElement[2]": "投影矩阵元素(斜切/近平面处理)",
        "projInvElements[2]": "投影逆矩阵元素",
        "viewProjInvMat": "视图投影逆矩阵(裁剪→世界)",
        "prevViewProjMat": "上一帧视图投影(速度/运动矢量)",
        "ZToLinear": "深度(Z)→线性深度参数",
        "subdivisionLevel": "曲面细分级别",
        "screenSize": "屏幕尺寸(像素)",
        "screenInverseSize": "屏幕尺寸倒数(NDC 反算)",
        "cullingHelper": "剔除辅助参数",
        "cameraNearPlane": "相机近平面",
        "cameraFarPlane": "相机远平面",
        "viewFrustum[6]": "视锥 6 平面",
        "clipplane": "裁剪平面",
    },
    "GBufferType": {
        "gbufferTypeFlag": "GBuffer 类型标志(写 o2.w)",
        "gbufferTypeReserve0": "保留",
        "gbufferTypeReserve1": "保留",
        "gbufferTypeReserve2": "保留",
    },
    "Tonemap": {
        "exposureAdjustment": "曝光调整",
        "tonemapRange": "色调映射范围",
        "sharpness": "锐化强度",
        "preTonemapRange": "前置 tonemap 范围",
        "useAutoExposure": "是否自动曝光",
        "echoBlend": "残影/回声混合",
        "AABlend": "AA 混合",
        "AASubPixel": "AA 子像素",
        "ResponsiveAARate": "响应式 AA 速率",
    },
    "RootConstant": {"constant32Bits": "通用 32bit 常量"},
    "cbCSSkinning": {
        "cVertexCount": "顶点数",
        "cUVCount": "UV 套数",
        "reserved": "保留",
        "cVertexPositionByteOffset": "源-位置 字节偏移",
        "cVertexNormalByteOffset": "源-法线 字节偏移",
        "cVertexTexcoordByteOffset": "源-UV0 字节偏移",
        "cVertexTexcoord2ByteOffset": "源-UV1 字节偏移",
        "cVertexSkinWeightByteOffset": "源-蒙皮权重 字节偏移",
        "cDstVertexPositionByteOffset": "目标-位置 字节偏移",
        "cDstVertexPrevPositionByteOffset": "目标-上一帧位置 字节偏移",
        "cDstVertexNormalByteOffset": "目标-法线 字节偏移",
    },
    "ShadowCastInfo": {
        "shadowCastDepthBias": "阴影深度偏置",
        "shadowCastSlopeBias": "阴影斜率偏置",
        "shadowCastReserve": "保留",
    },
    "EnvironmentInfo": {
        "timeMillisecond": "时间(毫秒)",
        "frameCount": "帧计数",
        "isOddFrame": "奇偶帧标志",
        "reserveEnvironmentInfo": "保留",
        "userGlobalParams": "用户全局参数(4x4)",
        "breakingPBRSpecularIntensity": "破坏PBR-镜面强度",
        "breakingPBRIBLReflectanceBias": "破坏PBR-IBL反射偏移",
        "breakingPBRIBLIntensity": "破坏PBR-IBL强度",
        "breakingPBR_Reserved": "保留",
    },
    "CheckerBoardInfo": {
        "cbr": "棋盘缩放/偏置",
        "cbr_bias": "棋盘偏置",
        "cbr_using": "是否启用棋盘渲染",
    },
    "World": {
        "worldMat": "世界矩阵",
        "prevWorldMat": "上一帧世界矩阵",
        "jointOffset": "关节偏移",
        "prevJoinOffset": "上一帧关节偏移",
        "reserveWorld": "保留",
    },
    "OutdoorLightProbeParam": {
        "GridOffset": "探针网格偏移",
        "GridScale": "探针网格缩放",
        "OutdoorGridOffset": "室外网格偏移",
        "GridDepth": "网格深度",
        "OutdoorGridScale": "室外网格缩放",
        "_OutdoorLightProbeParam3": "保留",
        "OutdoorGridMin": "室外网格最小",
        "OutdoorGridMax": "室外网格最大",
    },
    "ShadowSamplingRotation": {"ShadowSamplePoints[8]": "阴影滤波采样点(8)"},
    "DirectionalLightParameter": {
        "DL_Direction": "方向光方向",
        "DL_Enable": "方向光启用",
        "DL_Color": "方向光颜色",
        "DL_MinAlpha": "最小 alpha",
        "DL_ViewProjection": "方向光视投影矩阵",
        "DL_Variance": "阴影方差",
        "DL_ArrayIndex": "阴影数组索引",
        "DL_MipIndex": "阴影 mip 索引",
        "DL_Bias": "阴影偏置",
        "Cascade_Translate1": "级联1-平移",
        "Cascade_Bias1": "级联1-偏置",
        "Cascade_Translate2": "级联2-平移",
        "Cascade_Bias2": "级联2-偏置",
        "Cascade_Translate3": "级联3-平移",
        "Cascade_Bias3": "级联3-偏置",
        "Cascade_Scale1": "级联1-缩放",
        "Cascade_Scale2": "级联2-缩放",
        "Cascade_Scale3": "级联3-缩放",
        "SDSMEnable": "SDSM 启用",
        "SDSMDebugDraw": "SDSM 调试绘制",
        "CascadeDistance": "级联距离",
    },
    "PickState": {
        "WriteAddress": "写地址",
        "PickPosition": "拾取位置",
        "OptionalTag": "可选标签",
    },
}


def member_desc(cbuffer, member):
    """cbuffer 某成员的一句话说明(未知返回空串)。"""
    return (MEMBER_DESC.get(cbuffer) or {}).get(member, "")

_ENGINE_RE = re.compile(r"^\s*//!\s*engine\s+(\w+)\s*$")
_REG_PRE = {"cbuffer": "b", "texture": "t", "sampler": "s"}


def path():
    return os.path.join(P.dir_path(), "inputs.json")


def _fallback():
    """无 inputs.json: 退化为**空清单**(宁可少展示, 也不展示过时/错误的项)。

    真实清单以 `inputs.json` 为准(由 `scripts/gen_inputs.py` 生成)。以前这里会照搬
    iface.json 的"整份标准接口"(含 UserMaterial 的固化成员等), 与最小化后的设计不符, 故改为空。
    """
    return {"std": [], "params": [], "candidates": []}


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


def _pass_deps(pass_name, template=None):
    """该 pass 的**引擎资源依赖**名列表。

    优先用 passes.json 里该模板的 `depends_by_template[template]` 覆盖(同一 pass 的
    不同**模板**结构性需求不同: 薄模板(如自定义光照)不直接读引擎资源 ⇒ 依赖为空,
    其依赖只从**材质的预设**来); 未提供覆盖时回退到 pass 级 `depends`。
    """
    p = (P.passes().get("passes", {}).get(pass_name, {}) or {})
    by = p.get("depends_by_template") or {}
    if template is not None and template in by:
        return list(by[template] or [])
    return list(p.get("depends") or [])


def base_iface_for_pass(pass_name="main", template=None):
    """该 pass 的基座接口 = **它声明的引擎资源依赖**(自动分配寄存器)。

    依赖来自 `presets/<ver>/passes.json`(可按 **模板** 覆盖 —— `depends_by_template`);
    依赖的资源 -> 被系统自动添加(引擎资源被依赖 ⇒ 锁定/不可删)。
    **固有输入已取消** —— 不再写死基座, SceneInfo 等已归引擎资源。
    GUI 与生成器**都必须**用它, 避免"两条路径基座不一致"。
    """
    deps = _pass_deps(pass_name, template)
    iface = {"cbuffers": [], "textures": [], "samplers": []}
    sel, seen = [], set()
    for nm in deps:
        e = find(nm)
        if e and nm not in seen:
            seen.add(nm)
            sel.append(e)
    regs = assign_regs(sel, iface)
    for e in sel:
        _add_engine(iface, e, regs[e["name"]])
    return iface


def default_iface():
    """(兼容别名) 主 pass 的基座接口。"""
    return base_iface_for_pass("main")


def catalog_text():
    d = load()
    lines = ["固有输入(标准接口, 只读) %d 项:" % len(d["std"])]
    for e in d["std"]:
        lines.append("  [%-7s] %-32s %-8s %-6s %s"
                     % (e["kind"], e["name"], e["type"], e["reg"], e["source"]))
    lines.append("材质参数(每材质自由声明; 预设不固化) %d 项:" % len(d["params"]))
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


def _iface_names(iface):
    """接口里已声明的资源名集合(cbuffer/texture/sampler)。供预设依赖判定。"""
    out = set()
    for c in (iface or {}).get("cbuffers", []):
        out.add(c["name"])
    for t in (iface or {}).get("textures", []):
        out.add(t["name"])
    for s in (iface or {}).get("samplers", []):
        out.add(s["name"])
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


# ---------------------------------------------------------------- 保活(已退役)
# 2026-09-30: “显式 register + 保活”退役 —— 寄存器改由 d3dcompiler **自动紧凑**分配
#   (寄存器号 ≡ RDEF 位置, 结构上不可能有空洞), 不再需要死分支保活。
#   `build_iface_and_keepalive` 仍返回一个第二元素, 但**恒为空串**(仅兼容调用方签名)。


# ---------------------------------------------------------------- 组装

# 插值输入的用途说明(供界面展示; 非资源)。
INTERP_DESC = {
    "INTERPOLATOR0": "法线 xyz / UV0.x / 深度: UV0 等(见模板)",
    "INTERPOLATOR1": "UV0.y / UV1 / 切线.x",
    "INTERPOLATOR2": "切线.yz / bitangent 符号 / 世界坐标.x",
    "INTERPOLATOR3": "世界坐标.yz",
    "INTERPOLATOR4": "速度项",
}


def _interp_specs_for(pass_name, presets):
    """该 pass **实际**的插值声明规格。

    主 pass -> passes.json 全量(材质族); 深度 -> 仅当该 pass 预设需要插值时用材质族(Phase 3);
    顶点 -> 无(顶点输入由预设生成, 非插值)。
    """
    if pass_name in ("main", None):
        return interp_specs("main")
    if pass_name == "depth":
        from . import semantic_inputs as SI
        return interp_specs("main") if SI.presets_need_interp(presets or []) else []
    return []


def interp_inputs(pass_name="main", presets=()):
    """该 pass 声明依赖的插值输入 [(名, 用途)]。非资源(只读展示)。"""
    return [(s["name"], INTERP_DESC.get(s["name"], ""))
            for s in _interp_specs_for(pass_name, presets)]


# 插值掩码 -> HLSL 类型(4->float4 ... 1->float)
_MASK_TYPE = {1: "float", 2: "float2", 3: "float3", 4: "float4"}


def interp_specs(pass_name="main"):
    """该 pass 的插值声明规格 [{name,var,mask}](来自 passes.json; 主 pass)。"""
    p = (P.passes().get("passes", {}).get(pass_name, {}) or {})
    out = []
    for it in (p.get("interp") or []):
        out.append(it if isinstance(it, dict) else {"name": it, "var": None, "mask": 4})
    return out


def interp_decls(pass_name="main", presets=()):
    """生成该 pass 的 `PSIn` 插值声明行(缩进 4)。供模板注入(真驱动)。"""
    lines = []
    for s in _interp_specs_for(pass_name, presets):
        lines.append("    %s %s : %s;" % (_MASK_TYPE.get(s.get("mask", 4), "float4"),
                                          s.get("var"), s.get("name")))
    return "\n".join(lines)


def interp_has(pass_name, name, presets=()):
    """该 pass 是否(按当前预设)声明某插值输入。"""
    return any(s.get("name") == name
               for s in _interp_specs_for(pass_name, presets))


def pass_dep_names(pass_name="main", template=None):
    """该 pass **声明的引擎资源依赖**名列表(passes.json; 可按模板覆盖)。系统自动添加 + 锁定。"""
    return _pass_deps(pass_name, template)


def _canonical_dep_order(pass_name):
    """该 pass 的引擎资源**规范声明顺序**(取 pass 级 `depends`, 与模板覆盖无关)。"""
    if not pass_name:
        return []
    p = (P.passes().get("passes", {}).get(pass_name, {}) or {})
    return list(p.get("depends") or [])


def _apply_canonical_order(iface, pass_name):
    """把接口里的资源按**引擎期望的固定顺序**重排。

    引擎**按 RDEF 顺序连续绑定材质资源**(顺序敏感) ⇒ 声明顺序必须固定(与 passes.json 的
    `depends` 一致), 否则同一种资源在“默认/自定义”两路径下会落不同寄存器 ⇒ 绑定错位。
    未在规范表内的(如 UserMaterial / 额外声明)按原相对顺序排在其后。寄存器号随重排重标
    (仅展示用; 实际由编译器按声明序自动紧凑)。
    """
    order = _canonical_dep_order(pass_name)
    if not order:
        return
    idx = {n: i for i, n in enumerate(order)}
    for kind, pre in (("cbuffers", "b"), ("textures", "t"), ("samplers", "s")):
        lst = iface.get(kind) or []
        if len(lst) < 2:
            continue
        reordered = sorted(lst, key=lambda e: idx.get(e["name"], len(order)))
        if [e["name"] for e in reordered] == [e["name"] for e in lst]:
            continue
        for i, e in enumerate(reordered):
            e["reg"] = "%s%d" % (pre, i)
        iface[kind] = reordered


def build_iface_and_keepalive(base_iface, engine_names=(), params=(), textures=(),
                              presets=(), stage="ps", struct_name="MaterialInput",
                              recv="mi", all_params=None, pass_name=None):
    """**统一入口**: 基座 + 该 pass 的声明(engine/param/tex/preset) -> (iface, "", report)。

    第 2 个返回值(保活文本)**已退役**, 恒为 ""(寄存器改由编译器自动紧凑)。

    engine_names/params/textures/presets: 该 pass 的**显式清单**(来自结构化 inputs)。
    stage: "ps" / "vs" / "depth" —— 决定预设 `G.<值>` 的供值来源与生成的结构体(名/接收者)。
    all_params: 全部材质参数 (name,type) —— UserMaterial 成员表用**并集**(全局共享一套)。
    pass_name: 用于 `_apply_canonical_order` 的规范声明序(main/depth/vertex)。
    **始终**返回接口: 无声明时接口 = 基座(该 pass 的引擎依赖)。
    """
    from . import material_iface as MI
    import copy
    report = {"engine": [], "params": [], "textures": [], "regs": {},
              "presets": [], "preset_unknown": [], "preset_unsupported": [],
              "lock": {}, "minput": None}

    if base_iface is None:
        base_iface = {"cbuffers": [{"name": "UserMaterial", "reg": "b3", "members": []}],
                      "textures": [],
                      "samplers": [{"name": "AutomaticWrap", "reg": "s0", "cmp": False}]}

    # 预设(语义)输入: 目录**全局**; 启用集**按 pass 各自**(由调用方给出本 pass 名单)。
    #   各 pass 从同一目录生成**各自**的结构体(MaterialInput/DepthInput/VertexInput)。
    from . import semantic_inputs as SI
    _stage = stage or ("depth" if pass_name == "depth" else "ps")
    _sn, _rc = struct_name, recv
    if _stage == "depth" and struct_name == "MaterialInput":
        _sn, _rc = "DepthInput", "di"
    _presets = list(presets or [])
    _bad = SI.unsupported_in_stage(_presets, _stage)   # 该 stage 拿不到的值所依赖的预设
    if _bad:
        _presets = [n for n in _presets if n not in _bad]
    _si = SI.resolve(_presets, _iface_names(base_iface), stage=_stage,
                     struct_name=_sn, recv=_rc)
    eng_names = list(engine_names or []) + list(_si["engine"])
    report["presets"] = _si["presets"]
    report["preset_unknown"] = _si["unknown"]
    report["preset_unsupported"] = _bad
    report["lock"] = _si["lock"]
    report["minput"] = {"def": _si["def"], "build": _si["build"]}

    # 材质参数/贴图(该 pass): UserMaterial 成员表用**并集**(mmtr 级共享一套)。
    params = list(params or [])
    textures = list(textures or [])
    known_p = {m["name"] for c in base_iface["cbuffers"] for m in c["members"]}
    known_t = {t["name"] for t in base_iface["textures"]}
    params = [(n, t) for (n, t) in params if n not in known_p]
    textures = [n for n in textures if n not in known_t]
    iface = copy.deepcopy(base_iface)
    if params or textures:
        _cbp = [p for p in (all_params if all_params is not None else params)
                if p[0] not in known_p]
        iface, added = MI.extend(iface, "UserMaterial", _cbp, textures)
        report["params"] = [n for n, _ in params]
        report["textures"] = list(textures)

    # 引擎资源(去重: 已在该接口里的, **按 名字+种类** 跳过 —— cbuffer/texture/sampler 都要查;
    #   只查 param 名/纹理名会漏掉 sampler/cbuffer ⇒ 同一名字被追加两次 ⇒ "redefinition")
    known_of = {
        "cbuffer": {c["name"] for c in iface["cbuffers"]},
        "texture": {t["name"] for t in iface["textures"]},
        "sampler": {s["name"] for s in iface["samplers"]},
    }
    eng = []
    for nm in eng_names:
        e = find(nm)
        if e is None or nm in known_of.get(e["kind"], set()):
            continue
        if any(x["name"] == nm for x in eng):
            continue
        eng.append(e)
    regs = assign_regs(eng, iface)
    for e in eng:
        _add_engine(iface, e, regs[e["name"]])
    report["engine"] = [e["name"] for e in eng]
    report["regs"] = regs

    # 规范声明顺序: 引擎按 RDEF 顺序绑定材质资源(顺序敏感) ⇒ 重排为与 passes.json
    #   `depends` 一致的固定顺序(对默认模式无影响; 把“按预设声明序”漂移的自定义拉回规范)。
    _apply_canonical_order(iface, pass_name)
    # 保活已退役(2026-09-30): 寄存器改由 d3dcompiler“自动紧凑”分配(寄存器号 ≡ RDEF 位置,
    #   结构上无空洞), 无需再靠死分支引用维持 RDEF。
    return iface, "", report

