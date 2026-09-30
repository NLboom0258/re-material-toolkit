"""标准 VS 自生: 按 (输入族, 世界来源, instancing, clip, 输出族) 生成 HLSL 并编译为 vs_5_0。

背景(见 PROJECT_SUMMARY「pass 自生」):
  mmtr 里 VS 分两类 —— **标准 VS**(引擎变换/蒙皮, 只绑引擎资源) 与 **材质自定义 VS**
  (`VertexShaderUsed=1`, 读材质贴图/参数做顶点效果)。本模块只做**标准 VS**。
  实机/离线结论: FXC **保留"已声明未使用"的输入** ⇒ 复刻 ISGN 只需声明完整 struct。
  材质族打包(= PS 模板解包约定, 全变体统一):
    i0=(worldN.xyz, uv0.x) i1=(uv0.y, uv1.xy, worldT.x) i2=(worldT.y, worldT.z, tan.w, worldPos.x)
    i3=(worldPos.y, worldPos.z, prevClip.xy) i4=prevClip.z
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
        "o.i4 = %s.z;" % pclip,
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


def compile_vs(src):
    """编译为 vs_5_0 -> (dxbc_bytes, err_text)。"""
    return B.compile_hlsl(src, "main", "vs_5_0", name="vs_gen.hlsl")
