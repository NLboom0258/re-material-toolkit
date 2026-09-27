// ============================================================================
// RE mmtr pass 模板: Deferred —— **std · 自定义光照(直控 GBuffer)** —— 系统部分, 勿改
// 定位: 自定义光照模式。材质**直控** 4 个原始 GBuffer RT(原始值), 不经“PBR 语义 → 打包”;
//       最终输出前的后处理(曝光等)也由材质经 `mi.exposureScale` 控制。
// 接口/骨架用**我们的标准接口**(无 donor)。
// 与 deferred_std 的区别: 后者给 PBR 语义(默认光照, 打包+光照/后处理外置)。
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
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线.yz, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz, zw=速度项
    float4 v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;   // 原始 GBuffer RT0
    float4 o1 : SV_Target1;   // 原始 GBuffer RT1
    float4 o2 : SV_Target2;   // 原始 GBuffer RT2
    float4 o3 : SV_Target3;   // 原始 GBuffer RT3
};

// ---- 材质输入(供 MaterialMain 使用) ----
struct MaterialInput
{
    float2 uv0;
    float2 uv1;
    float3 positionWS;
    float3 viewDir;
    float3 Normal;
    float3 Tangent;
    float3 Bitangent;
    float3 camPos;
    float3 camDir;
    float3 camUp;
    float2 velocity;       // 系统: 屏幕空间速度(RT3.yz 默认)
    float  exposureScale;  // 系统: 1/(白点*曝光系数)(RT0 曝光用)
};

// ---- 材质输出(直写原始 GBuffer; 由 MaterialMain 赋值) ----
struct MaterialOutput
{
    float4 RT0;
    float4 RT1;
    float4 RT2;
    float4 RT3;
};

// 曝光补偿常量(延迟 RT0 会被引擎再乘曝光+tonemap; 供材质直写 RT0 时抵消)
#define BARE_RT0_EXPOSURE 100.0

// 法线八面体编码(等价原版; 供材质调用)
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
    MaterialInput mi;
    mi.uv0 = float2(i.v1.w, i.v2.x);
    mi.uv1 = i.v2.yz;
    mi.positionWS = float3(i.v3.w, i.v4.x, i.v4.y);
    mi.Normal = normalize(i.v1.xyz).xzy;
    mi.Tangent = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    mi.Bitangent = cross(mi.Normal, mi.Tangent);
    mi.Bitangent = (i.v3.z < 0.0) ? -mi.Bitangent : mi.Bitangent;
    mi.Bitangent = normalize(mi.Bitangent);
    mi.camPos = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,
                       transposeViewInvMat[2].w);
    mi.camDir = normalize(float3(transposeViewInvMat[0].z, transposeViewInvMat[1].z,
                                 transposeViewInvMat[2].z));
    mi.camUp  = normalize(float3(transposeViewInvMat[0].y, transposeViewInvMat[1].y,
                                 transposeViewInvMat[2].y));
    mi.viewDir = normalize(mi.positionWS - mi.camPos);

    // ---- 2. 系统量(速度 / 曝光) ----
    float2 ndc = i.svpos.xy * screenInverseSize * float2(2.0, -2.0) + float2(-1.0, 1.0);
    mi.velocity = (i.v4.zw / i.v5.x) - ndc;

    float wp = asfloat(WhitePtSrv.Load(0));
    wp = useAutoExposure ? wp : 1.0;
    wp = wp * exposureAdjustment;
    mi.exposureScale = 1.0 / max(wp, 0.0001);

    // ---- 3. 材质逻辑(直写 4 个 GBuffer RT) ----
    MaterialOutput m;
    MaterialMain(mi, m);

    PSOut o;
    o.o0 = m.RT0;
    o.o1 = m.RT1;
    o.o2 = m.RT2;
    o.o3 = m.RT3;

    // ---- 4. 保活(死分支): 让标准/自定义接口资源留在 RDEF ----
    if (i.v1.w > 1e30) {
        o.o0.rgb += NormalRoughnessMap.Sample(AutomaticWrap, i.v2.xx).rgb;
        o.o0.rgb += AlphaTranslucentOcclusionSSSMap.Sample(AutomaticWrap, i.v2.yy).rgb;
        o.o0.rgb += BaseMetalMap.Sample(AutomaticWrap, i.v2.zz).rgb;
        o.o0.rgb += float(WhitePtSrv.Load(0)).xxx;
        o.o0.rgb += viewProjMat[0][0].xxx + gbufferTypeFlag.xxx
                  + gbufferTypeReserve0.xxx + gbufferTypeReserve1.xxx
                  + gbufferTypeReserve2.xxx
                  + exposureAdjustment.xxx + VAR_BaseColor.rgb;
        //__KEEPALIVE__
    }
    return o;
}
