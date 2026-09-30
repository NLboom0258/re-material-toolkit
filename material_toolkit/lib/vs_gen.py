"""标准 VS 自生: 按 (输入族, 世界来源, instancing, clip, 输出族) 生成 HLSL 并编译为 vs_5_0。

背景(见 PROJECT_SUMMARY「pass 自生」):
  mmtr 里 VS 分两类 —— **标准 VS**(引擎变换/蒙皮, 只绑引擎资源) 与 **材质自定义 VS**
  (`VertexShaderUsed=1`, 读材质贴图/参数做顶点效果)。本模块只做**标准 VS**。
  实机/离线结论: FXC **保留"已声明未使用"的输入** ⇒ 复刻 ISGN 只需声明完整 struct。
  材质族打包(= PS 模板解包约定, 全变体统一):
    i0=(worldN.xyz, uv0.x) i1=(uv0.y, uv1.xy, worldT.x) i2=(worldT.y, worldT.z, tan.w, worldPos.x)
    i3=(worldPos.y, worldPos.z, prevClip.xy) i4=prevClip.**w**(齐次项, 非 .z)
"""
from . import mmtr_blobs as B

# 输入族 -> [(hlsl_type, var, semantic), ...](声明顺序 == 寄存器序, 必须与 ISGN 一致)
INPUT_FAMILIES = {
    "Static": [
        ("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
        ("float4", "tan", "TANGENT0"), ("float2", "uv0", "TEXCOORD0"),
        ("float2", "uv1", "Texcoord1"),
    ],
    "Static2": [
        ("float3", "p0", "POSITION0"), ("float2", "uv1", "Texcoord1"),
    ],
    "Skinning": [
        ("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
        ("float4", "tan", "TANGENT0"), ("float2", "uv0", "TEXCOORD0"),
        ("uint4", "bi", "INDEX0"), ("float4", "bw", "WEIGHT0"),
        ("float2", "uv1", "Texcoord1"),
    ],
    "SkinningMin": [
        ("float3", "p0", "POSITION0"), ("uint4", "bi", "INDEX0"),
        ("float4", "bw", "WEIGHT0"), ("float2", "uv1", "Texcoord1"),
    ],
    "PreTransform": [
        ("float3", "p0", "POSITION0"), ("float3", "p1", "POSITION1"),
        ("float4", "nrm", "NORMAL0"), ("float4", "tan", "TANGENT0"),
        ("float2", "uv0", "TEXCOORD0"), ("float2", "uv1", "Texcoord1"),
    ],
    "StaticInst": [
        ("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
        ("float4", "tan", "TANGENT0"), ("float2", "uv0", "TEXCOORD0"),
        ("float2", "uv1", "Texcoord1"), ("uint", "svid", "SV_InstanceID"),
    ],
    "SkinningInst": [
        ("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
        ("float4", "tan", "TANGENT0"), ("float2", "uv0", "TEXCOORD0"),
        ("uint4", "bi", "INDEX0"), ("float4", "bw", "WEIGHT0"),
        ("float2", "uv1", "Texcoord1"), ("uint", "svid", "SV_InstanceID"),
    ],
    "NrmUV1": [
        ("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
        ("float2", "uv1", "Texcoord1"),
    ],
    "PosUV1": [
        ("float3", "p0", "POSITION0"), ("float2", "uv1", "Texcoord1"),
    ],
    "PreTransformMin": [
        ("float3", "p0", "POSITION0"), ("float2", "uv1", "Texcoord1"),
    ],
}

# 引擎结构化缓冲(引擎直供) -> (struct名, 成员文本, stride)
SRV_STRUCTS = {
    "InstanceWorldInfo": (
        "InstanceWorld",
        "row_major float3x4 worldMat; row_major float3x4 prevWorldMat;"
        " uint jointOffset; uint prevJointOffset; uint2 rev;", 112),
    "SkinningMatrices": ("JointMatrix", "float4 row0; float4 row1; float4 row2;", 48),
    "WorldInstances": (
        "WorldInstance",
        "row_major float4x4 worldMat; row_major float4x4 prevWorldMat;"
        " int jointOffset; int prevJoinOffset; int2 reserveWorld;", 144),
    "IndirectIndicesBuffer": ("_", "uint value;", 4),
}


def _iface_for(cbuffer_names):
    from . import material_inputs as INP
    sel = [e for e in (INP.find(n) for n in cbuffer_names) if e]
    iface = {"cbuffers": [], "textures": [], "samplers": []}
    regs = INP.assign_regs(sel, iface)
    for e in sel:
        INP._add_engine(iface, e, regs[e["name"]])
    return iface


def _srv_decl(name):
    """引擎结构化缓冲声明(PS 的 _texture_decl 会跳过 struct, 故这里显式声明)。"""
    sn, mem, _st = SRV_STRUCTS[name]
    if name == "IndirectIndicesBuffer":
        return "StructuredBuffer<uint> %s;" % name
    return ("struct %s { %s };\nStructuredBuffer<%s> %s;"
            % (sn, mem, sn, name))


def build_source(input_family, cbuffers, out_fields, body, srvs=()):
    """组装 VS 源。srvs: 需声明的引擎结构化缓冲名(如 ["InstanceWorldInfo"])。"""
    from . import material_iface as MI
    iface = _iface_for(cbuffers)
    L = [MI.hlsl_of(iface)]
    for n in srvs:
        L.append(_srv_decl(n))
    L.append("struct VSIn {")
    for t, n, sem in INPUT_FAMILIES[input_family]:
        L.append("    %s %s : %s;" % (t, n, sem))
    L.append("};")
    L.append("struct VSOut {")
    L.append("    float4 pos : SV_Position;")
    for t, n, sem in out_fields:
        L.append("    %s %s : %s;" % (t, n, sem))
    L.append("};")
    L.append("VSOut main(VSIn i)")
    L.append("{")
    L.append("    VSOut o;")
    L.extend("    " + ln for ln in body.splitlines())
    L.append("    return o;")
    L.append("}")
    return "\n".join(L) + "\n"


def mat_pack(pos="wp", n="wN", t="wT", pclip="pclip", clip=False):
    """材质族统一打包(pos/n/t = float3 世界量; pclip = float4 上一帧裁剪坐标)。

    clip=True 时额外输出 SV_ClipDistance = dot(clipplane, float4(pos,1))。
    """
    L = [
        "o.pos = mul(float4(%s, 1.0), viewProjMat);" % pos,
        "o.i0 = float4(%s.xyz, i.uv0.x);" % n,
        "o.i1 = float4(i.uv0.y, i.uv1.xy, %s.x);" % t,
        "o.i2 = float4(%s.y, %s.z, i.tan.w, %s.x);" % (t, t, pos),
        "o.i3 = float4(%s.y, %s.z, %s.xy);" % (pos, pos, pclip),
        # i4 = **prevClip.w**(齐次除法项) —— 千万别写成 .z! 材质 PS 里 `vel = i.v4.zw/i.v5.x - ndc`
        # 把 i.v5.x 当上一帧裁剪坐标的 **W** 用; 写 .z 会让运动矢量全错(TAA 把抖动累积成条状/残影)。
        "o.i4 = %s.w;" % pclip,
    ]
    if clip:
        L.append("o.cd = dot(clipplane, float4(%s, 1.0));" % pos)
    return "\n".join(L)


def depth_pack(pos="wp", interp=False, clip=False):
    """深度族打包: o.pos = pos·viewProj; interp=True 时输出 uv0+pos(与 deferred_depth 解包一致);
    clip=True 时输出 SV_ClipDistance。"""
    L = ["o.pos = mul(float4(%s, 1.0), viewProjMat);" % pos]
    if interp:
        L.append("o.j0 = float4(i.uv0, %s.xy);" % pos)
        L.append("o.j1 = float2(%s.z, 0.0);" % pos)
    if clip:
        L.append("o.cd = dot(clipplane, float4(%s, 1.0));" % pos)
    return "\n".join(L)


def shadow_offset(wp="wp", n="wN"):
    """Shadow 族: 沿法线偏移世界位置(抗阴影痤疮), 逐条对应原版 asm(blob4/b8)。

    依赖 ShadowCastInfo(shadowCastDepthBias/SlopeBias) + SceneInfo(transposeViewInvMat/
    projElement[1].y/screenInverseSize.x)。开启条件 = 两个 bias 均非 0。
    """
    return "\n".join([
        "float3 _cam = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,"
        " transposeViewInvMat[2].w);",
        "if (shadowCastDepthBias != 0.0 && shadowCastSlopeBias != 0.0) {",
        "    if (0.5 < abs(projElement[1].y)) {",
        "        float3 _vd = normalize(_cam - %s);" % wp,
        "        float _b = ((1.0 - saturate(dot(%s, -_vd))) * screenInverseSize.x)"
        " * shadowCastSlopeBias + shadowCastDepthBias;" % n,
        "        %s = %s - _vd * _b;" % (wp, wp),
        "    } else {",
        "        float3 _vd = -float3(transposeViewInvMat[0].z, transposeViewInvMat[1].z,"
        " transposeViewInvMat[2].z);",
        "        float _b = ((1.0 - saturate(dot(%s, _vd))) * screenInverseSize.x)"
        " * shadowCastSlopeBias + shadowCastDepthBias;" % n,
        "        %s = %s + _vd * _b;" % (wp, wp),
        "    }",
        "}",
    ])


# ============================================================ 数据驱动(从银行 blob 自动推导)
# 基础输入族(不含 SV_InstanceID; 需要时追加)
_BASE_IN = {
    "full": [("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
             ("float4", "tan", "TANGENT0"), ("float2", "uv0", "TEXCOORD0"),
             ("float2", "uv1", "Texcoord1")],
    "nrm_uv1": [("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
                ("float2", "uv1", "Texcoord1")],
    "pos_uv1": [("float3", "p0", "POSITION0"), ("float2", "uv1", "Texcoord1")],
    "pretr": [("float3", "p0", "POSITION0"), ("float3", "p1", "POSITION1"),
              ("float4", "nrm", "NORMAL0"), ("float4", "tan", "TANGENT0"),
              ("float2", "uv0", "TEXCOORD0"), ("float2", "uv1", "Texcoord1")],
    "skin_full": [("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
                  ("float4", "tan", "TANGENT0"), ("float2", "uv0", "TEXCOORD0"),
                  ("uint4", "bi", "INDEX0"), ("float4", "bw", "WEIGHT0"),
                  ("float2", "uv1", "Texcoord1")],
    "skin_min": [("float3", "p0", "POSITION0"), ("uint4", "bi", "INDEX0"),
                 ("float4", "bw", "WEIGHT0"), ("float2", "uv1", "Texcoord1")],
    "skin_min_nrm": [("float3", "p0", "POSITION0"), ("float4", "nrm", "NORMAL0"),
                     ("uint4", "bi", "INDEX0"), ("float4", "bw", "WEIGHT0"),
                     ("float2", "uv1", "Texcoord1")],
}
_IN_KEY = {
    (("POSITION", 0), ("NORMAL", 0), ("TANGENT", 0), ("TEXCOORD", 0), ("TEXCOORD", 1)): "full",
    (("POSITION", 0), ("NORMAL", 0), ("TEXCOORD", 1)): "nrm_uv1",
    (("POSITION", 0), ("TEXCOORD", 1)): "pos_uv1",
    (("POSITION", 0), ("POSITION", 1), ("NORMAL", 0), ("TANGENT", 0),
     ("TEXCOORD", 0), ("TEXCOORD", 1)): "pretr",
    (("POSITION", 0), ("NORMAL", 0), ("TANGENT", 0), ("TEXCOORD", 0),
     ("INDEX", 0), ("WEIGHT", 0), ("TEXCOORD", 1)): "skin_full",
    (("POSITION", 0), ("INDEX", 0), ("WEIGHT", 0), ("TEXCOORD", 1)): "skin_min",
    (("POSITION", 0), ("NORMAL", 0), ("INDEX", 0), ("WEIGHT", 0),
     ("TEXCOORD", 1)): "skin_min_nrm",
}


def spec_from_blob(blob):
    """从银行标准 VS blob 反推生成 spec(输入族/世界来源/输出/Clip/Shadow)。"""
    from . import dxbc_sig as SG
    from . import rdef as R
    ins = SG.input_signature(blob)
    key = tuple((n.upper(), s) for (n, s, sv, _c, _r, _m, _rw) in ins if not sv)
    svid = any(n.upper().startswith("SV_INSTANCE") for (n, *_r) in ins)
    outs = SG.output_signature(blob)
    n_interp = sum(1 for (n, *_r) in outs if n.upper().startswith("INTERPOLATOR"))
    clip = any(n.upper() == "SV_CLIPDISTANCE" for (n, *_r) in outs)
    nointerp0 = any(n.upper().startswith("NOINTERPOLATOR") for (n, *_r) in outs)
    # 顺序必须**保留银行 RDEF 声明序**(不可排序): 引擎按 shader 声明序给引擎资源分槽 ——
    # 排序会把 SkinningMatrices/InstanceWorldInfo、SceneInfo/RootConstant 对调,
    # 致蒙皮读到错误的矩阵/实例信息 ⇒ 顶点炸开成条状(实机 2026-09-30)。
    _bo = R.rdef_bind_order(blob) or {"cb": [], "smp": [], "tex": []}
    cbn = list(_bo["cb"])
    srv = list(_bo["tex"])
    if "SkinningMatrices" in srv:
        world = "skin_indirect" if "IndirectIndicesBuffer" in srv else "skin"
    elif "IndirectIndicesBuffer" in srv:
        world = "indirect"
    elif "WorldInstances" in srv:
        world = "worldinst"
    elif "InstanceWorldInfo" in srv:
        world = "inst"
    elif "World" in cbn:
        world = "world"
    else:
        world = "pre"
    return {"family": _IN_KEY.get(key), "svid": svid, "world": world,
            "pack": "mat" if n_interp >= 5 else "depth",
            "interp": n_interp >= 1, "clip": clip, "nointerp0": nointerp0,
            "shadow": "ShadowCastInfo" in cbn, "cbuffers": cbn, "srvs": srv}


def _emit_world(world, need_n, need_t, need_prev):
    """生成变换代码, 产出名量 wp/(wN)/(wT)/(pp); 返回 (lines, prevw_expr)。"""
    L, prevw = [], None
    if world == "pre":
        L.append("float3 wp = i.p0;")
        if need_n:
            L.append("float3 wN = i.nrm.xyz;")
        if need_t:
            L.append("float3 wT = i.tan.xyz;")
        prevw = "float4(i.p1, 1.0)"
        return L, prevw
    if world in ("inst", "indirect", "skin", "skin_indirect"):
        if world == "inst":
            ie = "InstanceWorldInfo[constant32Bits]"
        elif world == "indirect":
            ie = "InstanceWorldInfo[IndirectIndicesBuffer[i.svid + constant32Bits]]"
        elif world == "skin":
            ie = "InstanceWorldInfo[constant32Bits]"
        else:
            ie = "InstanceWorldInfo[IndirectIndicesBuffer[i.svid + constant32Bits]]"
        L.append("InstanceWorld iw = %s;" % ie)
        L.append("float4 p4 = float4(i.p0, 1.0);")
        if world.startswith("skin"):
            L.append("float4 w = i.bw / (i.bw.x + i.bw.y + i.bw.z + i.bw.w);")
            for k in range(4):
                L.append("JointMatrix j%d = SkinningMatrices[iw.jointOffset + i.bi.%s];"
                         % (k, "xyzw"[k]))
            L.append("float4 r0 = j0.row0*w.x + j1.row0*w.y + j2.row0*w.z + j3.row0*w.w;")
            L.append("float4 r1 = j0.row1*w.x + j1.row1*w.y + j2.row1*w.z + j3.row1*w.w;")
            L.append("float4 r2 = j0.row2*w.x + j1.row2*w.y + j2.row2*w.z + j3.row2*w.w;")
            L.append("float3 wp = float3(dot(r0,p4), dot(r1,p4), dot(r2,p4));")
            if need_n:
                L.append("float4 bn = float4(i.nrm.xyz, 0.0);")
                L.append("float3 wN = float3(dot(r0,bn), dot(r1,bn), dot(r2,bn));")
            if need_t:
                L.append("float4 bt = float4(i.tan.xyz, 0.0);")
                L.append("float3 wT = float3(dot(r0,bt), dot(r1,bt), dot(r2,bt));")
            if need_prev:
                for k in range(4):
                    L.append("JointMatrix q%d = SkinningMatrices[iw.prevJointOffset + i.bi.%s];"
                             % (k, "xyzw"[k]))
                L.append("float4 s0 = q0.row0*w.x + q1.row0*w.y + q2.row0*w.z + q3.row0*w.w;")
                L.append("float4 s1 = q0.row1*w.x + q1.row1*w.y + q2.row1*w.z + q3.row1*w.w;")
                L.append("float4 s2 = q0.row2*w.x + q1.row2*w.y + q2.row2*w.z + q3.row2*w.w;")
                L.append("float3 pp = float3(dot(s0,p4), dot(s1,p4), dot(s2,p4));")
                prevw = "float4(pp, 1.0)"
        else:
            L.append("float3 wp = mul(iw.worldMat, p4);")
            if need_n:
                L.append("float3 wN = mul(iw.worldMat, float4(i.nrm.xyz, 0.0));")
            if need_t:
                L.append("float3 wT = mul(iw.worldMat, float4(i.tan.xyz, 0.0));")
            if need_prev:
                L.append("float3 pp = mul(iw.prevWorldMat, p4);")
                prevw = "float4(pp, 1.0)"
        return L, prevw
    if world == "world":
        L.append("float4 p4 = float4(i.p0, 1.0);")
        L.append("float3 wp = mul(p4, worldMat).xyz;")
        if need_n:
            L.append("float3 wN = mul(i.nrm.xyz, (float3x3)worldMat);")
        if need_t:
            L.append("float3 wT = mul(i.tan.xyz, (float3x3)worldMat);")
        if need_prev:
            L.append("float3 pp = mul(p4, prevWorldMat).xyz;")
            prevw = "float4(pp, 1.0)"
        return L, prevw
    if world == "worldinst":
        L.append("WorldInstance wi = WorldInstances[i.svid];")
        L.append("float4 p4 = float4(i.p0, 1.0);")
        L.append("float3 wp = mul(p4, wi.worldMat).xyz;")
        if need_n:
            L.append("float3 wN = mul(i.nrm.xyz, (float3x3)wi.worldMat);")
        if need_t:
            L.append("float3 wT = mul(i.tan.xyz, (float3x3)wi.worldMat);")
        if need_prev:
            L.append("float3 pp = mul(p4, wi.prevWorldMat).xyz;")
            prevw = "float4(pp, 1.0)"
        return L, prevw
    raise ValueError("unknown world %r" % world)


def _assemble(infields, outs, cbs, srvs, body_lines):
    from . import material_iface as MI
    L = [MI.hlsl_of(_iface_for(cbs))]
    for n in srvs:
        L.append(_srv_decl(n))
    L.append("struct VSIn {")
    for t, n, sem in infields:
        L.append("    %s %s : %s;" % (t, n, sem))
    L.append("};")
    L.append("struct VSOut {")
    L.append("    float4 pos : SV_Position;")
    for t, n, sem in outs:
        L.append("    %s %s : %s;" % (t, n, sem))
    L.append("};")
    L.append("VSOut main(VSIn i)")
    L.append("{")
    L.append("    VSOut o;")
    L.extend("    " + ln for ln in body_lines)
    L.append("    return o;")
    L.append("}")
    return "\n".join(L) + "\n"


def build_from_spec(spec):
    """按 spec(来自 spec_from_blob)生成 VS 源。"""
    world = spec["world"]
    pack, interp = spec["pack"], spec["interp"]
    clip, shadow = spec["clip"], spec["shadow"]
    nointerp0 = spec.get("nointerp0", False)
    need_n = (pack == "mat") or shadow
    need_t = (pack == "mat")
    need_prev = (pack == "mat")
    L, prevw = _emit_world(world, need_n, need_t, need_prev)
    if shadow:
        L.extend(shadow_offset(wp="wp", n="wN").splitlines())
    if pack == "mat":
        L.append("float4 pclip = mul(%s, prevViewProjMat);" % prevw)
        L.extend(mat_pack(pos="wp", n="wN", t="wT", pclip="pclip", clip=clip).splitlines())
        outs = [("float4", "i0", "INTERPOLATOR0"), ("float4", "i1", "INTERPOLATOR1"),
                ("float4", "i2", "INTERPOLATOR2"), ("float4", "i3", "INTERPOLATOR3"),
                ("float", "i4", "INTERPOLATOR4")]
        if nointerp0:
            # 必须声明为 `nointerpolation`: 否则 FXC 会把相邻的同类型标量输出
            # (float INTERPOLATOR4 + 本项)打包进同一寄存器(本项只占 .y) ⇒ mask 错位。
            # 用 `nointerpolation` 让 FXC 分到**独立寄存器**(mask=1), 精确复刻银行;
            # 绝不可用 float4 撑开 —— 虽能独占寄存器但 mask=15 ≠ 银行 1, 引擎会异常
            # (实机: 近距渐隐抖动图案异常/拖影)。
            L.append("o.n0 = (float)i.svid;")
            outs.append(("nointerpolation float", "n0", "NOINTERPOLATOR0"))
        if clip:
            outs.append(("float", "cd", "SV_ClipDistance"))
    else:
        L.extend(depth_pack(pos="wp", interp=interp, clip=clip).splitlines())
        outs = []
        if interp:
            outs += [("float4", "j0", "INTERPOLATOR0"), ("float2", "j1", "INTERPOLATOR1")]
        if clip:
            outs.append(("float", "cd", "SV_ClipDistance"))
    infields = list(_BASE_IN[spec["family"]])
    if spec["svid"]:
        infields.append(("uint", "svid", "SV_InstanceID"))
    return _assemble(infields, outs, spec["cbuffers"], spec["srvs"], L)


def compile_vs(src):
    """编译为 vs_5_0 -> (dxbc_bytes, err_text)。"""
    return B.compile_hlsl(src, "main", "vs_5_0", name="vs_gen.hlsl")
