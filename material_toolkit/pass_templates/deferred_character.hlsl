// ============================================================================
// RE mmtr pass 模板: Deferred (character_default)  —— 系统部分, 勿改
// 组装方式: 本文件 + 用户的 MaterialMain(插入到下方标记行处)
// 依据: character_default 原版 Deferred PS(blob33) 反汇编逐条等价改写。
// 与 env 模板的差异: 只 1 个 sampler(s0); t3 = ...OcclusionSSSMap; UserMaterial 成员不同;
//                   有世界坐标/视线方向与“限界光(LimLight)”。
// ============================================================================

//__IFACE_BEGIN__
cbuffer SceneInfo : register(b0)
{
    row_major float4x4 viewProjMat;
    row_major float3x4 transposeViewMat;
    row_major float3x4 transposeViewInvMat;   // [7].w/[8].w/[9].w = 相机世界坐标
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
    float3 gbufferTypeReserve;
};

cbuffer Tonemap : register(b2)
{
    float exposureAdjustment;
    float tonemapRange;
    float sharpness;
    float preTonemapRange;
    int   useAutoExposure;
    float echoBlend;
    float AABlend;
    float AASubPixel;
    float ResponsiveAARate;
};

// ---- 材质 cbuffer(表2; = character_default 的 UserMaterial) ----
cbuffer UserMaterial : register(b3)
{
    float4 VAR_LimLight_Color;
    float4 VAR_BaseColor;
    float  VAR_LimLight_Intensity;
    float  VAR_LimLight_Pow;
    float  VAR_LimLight_Invert;
    float  VAR_OcclusionMap_UseSecondaryUV;
    float  VAR_Metallic;
    float  VAR_Roughness;
    float  VAR_TranslucencyIntensity;
    float  VAR_AlbedoOffsetIntensity;
    float  VAR_OcclusionIntensity;
    float  VAR_SpecularReflectance;
    float  VAR_Use_SpecularReflectanceMap;
    float  VAR_SSS_Channel;
    float  VAR_UseAlphaMap;
    float  VAR_AlphaTestRef;
    float  VAR_DissolveControl;
    float  VAR_AlphaValue;
    float  VAR_DissolveOffset;
    float  CAPCOM_MATERIAL_RESERVE0;
    float  CAPCOM_MATERIAL_RESERVE1;
    float  CAPCOM_MATERIAL_RESERVE2;
};

// ---- 材质贴图/sampler(表2) ----
ByteAddressBuffer WhitePtSrv                     : register(t0);
Texture2D<float4> BaseMetalMap                   : register(t1);
Texture2D<float4> NormalRoughnessMap             : register(t2);
Texture2D<float4> AlphaTranslucentOcclusionSSSMap: register(t3);
SamplerState AutomaticWrap                       : register(s0);
//__IFACE_END__

// ---- 输入签名(寄存器 0..5) ----
struct PSIn
{
    float4 svpos : SV_Position;   // reg0  屏幕像素坐标
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz, zw=速度项
    float4 v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)
};

struct PSOut
{
    float4 o0 : SV_Target0;   // 限界光/自发光(旁路光照)
    float4 o1 : SV_Target1;   // Albedo.rgb + Metallic/Translucency.w
    float4 o2 : SV_Target2;   // 法线八面体.xy + Roughness.z + Misc.w
    float4 o3 : SV_Target3;   // Occlusion.x + Velocity.yz + SubSurface.w
};

// ---- 材质语义输出(表3a) ----
struct MaterialOutput
{
    float3 BaseColor;
    float  Metallic;
    float  Roughness;
    float3 NormalTS;       // 切线空间法线
    float3 Emissive;
    float  Occlusion;
    float  SubSurface;
    float  Translucency;
    float  Reflectance;
};

// ---- 材质输入(供 MaterialMain 使用) ----
struct MaterialInput
{
    float2 uv0;
    float2 uv1;
    float3 positionWS;     // 世界坐标
    float3 viewDir;        // 相机 -> 像素(单位向量)
    float3 N;              // 切线基: 法线
    float3 T;              // 切线基: 切线
    float3 B;              // 切线基: 副切线
};

//__MATERIAL_MAIN__

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

PSOut main(PSIn i)
{
    // ---- 1. 输入解包 ----
    MaterialInput mi;
    mi.uv0 = float2(i.v1.w, i.v2.x);
    mi.uv1 = i.v2.yz;
    mi.positionWS = float3(i.v3.w, i.v4.x, i.v4.y);
    float3 camPos = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,
                           transposeViewInvMat[2].w);
    mi.N = normalize(i.v1.xyz).xzy;
    mi.T = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    mi.B = cross(mi.N, mi.T);
    mi.B = (i.v3.z < 0.0) ? -mi.B : mi.B;
    mi.B = normalize(mi.B);
    mi.viewDir = normalize(mi.positionWS - camPos);

    float2 ndc = i.svpos.xy * screenInverseSize * float2(2.0, -2.0) + float2(-1.0, 1.0);
    float2 vel = (i.v4.zw / i.v5.x) - ndc;

    // ---- 2. 材质逻辑 ----
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 打包 ----
    float wp = asfloat(WhitePtSrv.Load(0));
    wp = useAutoExposure ? wp : 1.0;
    wp = wp * exposureAdjustment;
    float inten = VAR_LimLight_Intensity / max(1.0 - tonemapRange, 0.0001);
    inten = max(inten / wp, 0.0);

    float metallic = saturate(m.Metallic * 1.02 - 0.02);
    float translucency = m.Translucency;
    bool opaque = (metallic > 0.0) || (translucency <= 0.0);
    float o1w, darkFlag;
    if (opaque)
    {
        o1w = max(metallic, min(m.Reflectance * 0.04, 0.08));
        darkFlag = 0.666667;
    }
    else
    {
        float t4 = round(translucency * 15.49 + 0.5);
        float r4 = round(saturate(m.Reflectance * 0.5) * 15.49 + 0.5);
        o1w = t4 * 0.0627451017 + r4 * 0.00392156886;
        darkFlag = 0.0;
    }

    float3 nt = normalize(m.NormalTS);
    float3 nrm = normalize(mi.N * nt.z + mi.T * nt.x + mi.B * nt.y);
    float2 encN = OctEncodeNormal(nrm).xy;

    PSOut o;
    o.o0 = float4(m.Emissive * inten, 0.0);
    o.o1 = float4(m.BaseColor, o1w);
    o.o2 = float4(encN, m.Roughness, gbufferTypeFlag * 0.333333343 + darkFlag);
    o.o3 = float4(m.Occlusion, vel, m.SubSurface);
    return o;
}
