// ============================================================================
// RE mmtr pass 模板: Deferred —— **std · 默认光照(PBR 语义输出)** —— 系统部分, 勿改
// 定位: 默认光照模式。材质只给 **PBR 语义**(表3a: BaseColor/Metallic/Roughness/Normal/
//       Emissive/Occlusion/Translucency); **引擎光照/后处理外置** —— 模板把语义打包进 GBuffer,
//       之后的曝光/tonemap/光照由引擎做。
// 依据: `deferred_env` 的打包逻辑; 接口/骨架用**我们的标准接口**(无 donor)。
// 与 deferred_std_custom 的区别: 后者直控 4 个原始 GBuffer RT(custom)。
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
    float4 svpos : SV_Position;   // reg0
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线.yz, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz
    float  v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)(VS 仅输出 x ⇒ 声明标量)
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;
    float4 o1 : SV_Target1;
    float4 o2 : SV_Target2;
    float4 o3 : SV_Target3;
};

// ---- 材质输入(供 MaterialMain 使用) ----
struct MaterialInput
{
    float2 uv0;
    float2 uv1;
    float3 Normal;         // ⚠ GBuffer/插值约定(已 .xzy); 光照请用 NormalWS
    float3 NormalWS;       // 世界(光照)空间法线(= Normal.xzy) —— 与光方向同空间, 做 N·L 用它
    float3 Tangent;        // ⚠ 同 Normal 的约定(已 .xzy); 世界空间计算请 .xzy
    float3 Bitangent;      // ⚠ 同 Normal 的约定(已 .xzy); 世界空间计算请 .xzy
    float3 positionWS;
    float3 camPos;
    float3 camDir;
    float3 camUp;
};

// ---- 材质输出(PBR 语义; 表3a; 打包/光照/后处理由模板+引擎做) ----
struct MaterialOutput
{
    float3 BaseColor;
    float  Metallic;
    float  Roughness;
    float3 NormalTS;       // 切线空间法线
    float3 Emissive;
    float  Occlusion;
    float  Translucency;   // >0 且 Metallic<=0 时为半透明
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

    // ---- 2. 材质逻辑(给 PBR 语义) ----
    MaterialInput mi;
    mi.uv0 = uv0;
    mi.uv1 = uv1;
    mi.Normal = N;
    mi.NormalWS = mi.Normal.xzy;   // 世界(光照)空间; 做 N·L 用这个
    mi.Tangent = T;
    mi.Bitangent = B;
    mi.positionWS = posWS;
    mi.camPos = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,
                       transposeViewInvMat[2].w);
    mi.camDir = normalize(float3(transposeViewInvMat[0].z, transposeViewInvMat[1].z,
                                 transposeViewInvMat[2].z));
    mi.camUp  = normalize(float3(transposeViewInvMat[0].y, transposeViewInvMat[1].y,
                                 transposeViewInvMat[2].y));
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 打包进 GBuffer(引擎光照/后处理外置) ----
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
