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
    "UserMaterial": {
        "VAR_LimLight_Color": "边缘光颜色",
        "VAR_BaseColor": "基础色",
        "VAR_LimLight_Intensity": "边缘光强度",
        "VAR_LimLight_Pow": "边缘光幂",
        "VAR_LimLight_Invert": "边缘光反转",
        "VAR_OcclusionMap_UseSecondaryUV": "遮蔽图用第二套 UV",
        "VAR_Metallic": "金属度",
        "VAR_Roughness": "粗糙度",
        "VAR_TranslucencyIntensity": "半透明强度",
        "VAR_AlbedoOffsetIntensity": "反照率偏移强度",
        "VAR_OcclusionIntensity": "遮蔽强度",
        "VAR_SpecularReflectance": "镜面反射率",
        "VAR_Use_SpecularReflectanceMap": "是否用镜面反射率贴图",
        "VAR_SSS_Channel": "次表面散射通道",
        "VAR_UseAlphaMap": "是否用 alpha 贴图",
        "VAR_AlphaTestRef": "Alpha 测试阈值",
        "VAR_DissolveControl": "消融控制",
        "VAR_AlphaValue": "Alpha 值",
        "VAR_DissolveOffset": "消融偏移",
        "CAPCOM_MATERIAL_RESERVE0": "引擎保留",
        "CAPCOM_MATERIAL_RESERVE1": "引擎保留",
        "CAPCOM_MATERIAL_RESERVE2": "引擎保留",
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
# GetDimensions 输出参数个数(保活用)。必须匹配 HLSL 内建的**无 mipLevel 重载**:
#   1d=1, 1darray=2, 2d=2, 2darray=3, 3d=3, cube=2, cubearray=3, 2dms=3, 2dmsarray=4。
_DIM_ARITY = {"1d": 1, "1darray": 2, "2d": 2, "2darray": 3, "3d": 3,
              "cube": 2, "cubearray": 3, "2dms": 3, "2dmsarray": 4, "buffer": 1}


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


def default_iface():
    """默认(空材质)接口基座 = 固有输入(**单一真源**): 名字取自 `inputs.json` 的 `std`,
    定义/成员取自标准接口 `iface.json`。

    GUI 与生成器**都必须**用它, 避免"两条路径基座不一致"导致的重复声明 / 寄存器错配。
    """
    full = P.std_iface() or {"cbuffers": [], "textures": [], "samplers": []}
    s = load()["std"]
    kb = {e["name"] for e in s if e["kind"] == "cbuffer"}
    kt = {e["name"] for e in s if e["kind"] == "texture"}
    ks = {e["name"] for e in s if e["kind"] == "sampler"}
    return {
        "cbuffers": [c for c in full.get("cbuffers", []) if c["name"] in kb],
        "textures": [t for t in full.get("textures", []) if t["name"] in kt],
        "samplers": [x for x in full.get("samplers", []) if x["name"] in ks],
    }


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
    """给一个成员构造"安全标量引用"(用于保活 `_ka += float(ref)`)。"""
    if "[" in name:                       # 数组: 取首元素(剥掉声明里的长度)
        name = "%s[0]" % name.split("[", 1)[0]
    if t in ("float", "int", "uint", "bool", "half"):
        return name
    if "[" not in name and "x" in t:      # 矩阵(如 float4x4)
        return "%s[0][0]" % name
    return "%s.x" % name


def keepalive_hlsl(items):
    """生成死分支内的"保活"语句(引用各资源, 防被编译器剔除)。

    items: [{"kind":"texture|cbuffer|sampler","name":..,"reg":..,"entry":catalog,..}]
    sampler 无法单独"使用"⇒ 略过(其存活取决于是否被采样)。
    """
    from . import material_iface as MI
    lines, k = [], 0
    for it in items:
        kd, nm = it["kind"], it["name"]
        if kd == "texture":
            entry = it.get("entry") or {}
            if MI.decl_skipped(entry):     # struct/未知 fmt/dim 无法声明 ⇒ 跳过(否则引用未定义名)
                continue
            fmt = entry.get("fmt")
            dim = entry.get("dim") or "2d"
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
    head = ("if (i.v1.w > 1e30) {\n"
            "        uint _k0, _k1, _k2, _k3; float _ka = 0.0;")
    body = ["        " + s for s in lines]
    tail = "        o.o0.x += _ka;\n    }"
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
    # 无 `//!` 声明: 接口仍返回 None(让上层回退到模板 IFACE), 但**仍要**按
    # "源码引用了哪些标准资源"生成 t0 前缀保活(否则单张/缺口会出错)。
    bare = not params and not textures and not eng_names

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
    return (None if bare else iface), ka, report
