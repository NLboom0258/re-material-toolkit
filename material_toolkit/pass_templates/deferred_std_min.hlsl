// ============================================================================
// RE mmtr pass 模板: Deferred —— **最小空材质版(实验用)** —— 系统部分
// 目的: 抹掉「模板体系 / 系统预制输入 / 保活」, 只留空材质**真正必须**的东西:
//   - 输入签名(SV_Position + 插值 v1..v5) / 输出签名(4 张 GBuffer)
//   - SceneInfo(velocity 用 screenInverseSize)
//   - GBufferType(gbufferTypeFlag -> o2.w)
// 已去掉: Tonemap / WhitePtSrv(t0) / BaseMetalMap(t1) / NormalRoughnessMap(t2) /
//         AlphaTranslucentOcclusionSSSMap(t3) / AutomaticWrap(s0) / UserMaterial(b3),
//         以及全部保活死分支; 相机预制输入(camPos/camDir/camUp)也去掉。
// 组装方式: 本文件 + 用户的 MaterialMain(插入到下方标记行处)。
// ============================================================================

//__IFACE_BEGIN__
cbuffer SceneInfo : register(b0)
{
    row_major float4x4 viewProjMat;
    row_major float3x4 transposeViewMat;
    row_major float3x4 transposeViewInvMat;
    float4 projElement[2];
    float4 projInvElements[2];
    row_major float4x4 viewProjInvMat;
    row_major float4x4 prevViewProjMat;
    float3 ZToLinear;
    float  subdivisionLevel;
    float2 screenSize;
    float2 screenInverseSize;
    float2 cullingHelper;
    float  cameraNearPlane;
    float  cameraFarPlane;
    float4 viewFrustum[6];
    float4 clipplane;
};

cbuffer GBufferType : register(b1)
{
    float  gbufferTypeFlag;
    float  gbufferTypeReserve0;
    float  gbufferTypeReserve1;
    float  gbufferTypeReserve2;
};
//__IFACE_END__

// ---- 输入签名(寄存器 0..5) ----
struct PSIn
{
    float4 svpos : SV_Position;   // reg0
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线.yz, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz
    float  v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;
    float4 o1 : SV_Target1;
    float4 o2 : SV_Target2;
    float4 o3 : SV_Target3;
};

// ---- 材质输入 ----
struct MaterialInput
{
    float2 uv0;
    float2 uv1;
    float3 Normal;         // ⚠ GBuffer/插值约定(已 .xzy); 光照请用 NormalWS
    float3 NormalWS;       // 世界(光照)空间法线(= Normal.xzy)
    float3 Tangent;        // ⚠ 同 Normal 的约定(已 .xzy)
    float3 Bitangent;      // ⚠ 同 Normal 的约定(已 .xzy)
    float3 positionWS;
};

// ---- 材质输出(PBR 语义) ----
struct MaterialOutput
{
    float3 BaseColor;
    float  Metallic;
    float  Roughness;
    float3 NormalTS;
    float3 Emissive;
    float  Occlusion;
    float  Translucency;
};

// 法线八面体编码(等价原版)
float3 OctEncodeNormal(float3 n)
{
    float l1 = abs(n.x) + abs(n.y) + abs(n.z);
    float2 p = n.xy / l1;
    bool back = (n.z <= 0.0);
    float2 f;
    f.x = (p.x >= 0.0) ? (1.0 - abs(p.y)) : -(1.0 - abs(p.y));
    f.y = (p.y >= 0.0) ? (1.0 - abs(p.x)) : -(1.0 - abs(p.x));
    float2 r = back ? f : p;
    return float3(r * 0.5 + 0.5, 0.0);
}

//__MATERIAL_MAIN__

PSOut main(PSIn i)
{
    // ---- 1. 输入解包 ----
    float2 uv0 = float2(i.v1.w, i.v2.x);
    float2 uv1 = i.v2.yz;

    float3 N = normalize(i.v1.xyz).xzy;
    float3 T = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    float3 B = cross(N, T);
    B = (i.v3.z < 0.0) ? -B : B;
    B = normalize(B);
    float3 posWS = float3(i.v3.w, i.v4.x, i.v4.y);

    float2 ndc = i.svpos.xy * screenInverseSize * float2(2.0, -2.0) + float2(-1.0, 1.0);
    float2 vel = (i.v4.zw / i.v5.x) - ndc;

    // ---- 2. 材质逻辑 ----
    MaterialInput mi;
    mi.uv0 = uv0;
    mi.uv1 = uv1;
    mi.Normal = N;
    mi.NormalWS = mi.Normal.xzy;
    mi.Tangent = T;
    mi.Bitangent = B;
    mi.positionWS = posWS;
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 打包进 GBuffer ----
    float metallic = saturate(m.Metallic * 1.02 - 0.02);
    float translucency = m.Translucency;
    bool opaque = (metallic > 0.0) || (translucency <= 0.0);
    float o1w, darkFlag;
    if (opaque)
    {
        o1w = max(metallic, 0.04);
        darkFlag = 0.666667;
    }
    else
    {
        o1w = round(translucency * 15.49 + 0.5) * 0.0627451017 + 0.0313725509;
        darkFlag = 0.0;
    }

    float3 nt = normalize(m.NormalTS);
    float3 nrm = normalize(N * nt.z + T * nt.x + B * nt.y);
    float2 encN = OctEncodeNormal(nrm).xy;

    PSOut o;
    o.o0 = float4(m.Emissive, 0.0);
    o.o1 = float4(m.BaseColor, o1w);
    o.o2 = float4(encN, m.Roughness, gbufferTypeFlag * 0.333333343 + darkFlag);
    o.o3 = float4(m.Occlusion, vel, 1.0);
    return o;
}
