// ============================================================================
// RE mmtr pass 模板: Deferred —— 绑定收窄(实验: 去掉 BaseMetalMap) —— 系统部分
// 目的: 只声明 `NormalRoughnessMap(t2)+ATOS(t3)+AutomaticWrap(s0)`(无 BaseMetalMap(t1),
//       也无 WhitePtSrv(t0))。
// 配合 bind_nrm 材质(采样 NormalRoughnessMap):
//   若正常 ⇒ "任意 2 张标准材质贴图"即可(BaseMetalMap 不特殊);
//   若坏   ⇒ 必须包含 BaseMetalMap(t1)。
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

Texture2D<float4> NormalRoughnessMap             : register(t2);
Texture2D<float4> AlphaTranslucentOcclusionSSSMap: register(t3);
SamplerState AutomaticWrap                       : register(s0);
//__IFACE_END__

struct PSIn
{
    float4 svpos : SV_Position;
    float4 v1    : INTERPOLATOR0;
    float4 v2    : INTERPOLATOR1;
    float4 v3    : INTERPOLATOR2;
    float4 v4    : INTERPOLATOR3;
    float  v5    : INTERPOLATOR4;
};

struct PSOut
{
    float4 o0 : SV_Target0;
    float4 o1 : SV_Target1;
    float4 o2 : SV_Target2;
    float4 o3 : SV_Target3;
};

struct MaterialInput
{
    float2 uv0;
    float2 uv1;
    float3 Normal;
    float3 NormalWS;
    float3 Tangent;
    float3 Bitangent;
    float3 positionWS;
};

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

    MaterialInput mi;
    mi.uv0 = uv0;
    mi.uv1 = uv1;
    mi.Normal = N;
    mi.NormalWS = N.xzy;
    mi.Tangent = T;
    mi.Bitangent = B;
    mi.positionWS = posWS;
    MaterialOutput m;
    MaterialMain(mi, m);

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

    // 保活: 让 t2/t3 留在 RDEF
    if (i.v1.w > 1e30) {
        o.o0.rgb += NormalRoughnessMap.Sample(AutomaticWrap, i.v2.xx).rgb;
        o.o0.rgb += AlphaTranslucentOcclusionSSSMap.Sample(AutomaticWrap, i.v2.zz).rgb;
    }
    return o;
}
