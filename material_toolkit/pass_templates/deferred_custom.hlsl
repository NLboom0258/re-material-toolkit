// ============================================================================
// RE mmtr pass 模板: Deferred —— **自定义光照 / 直写 GBuffer**(custom) —— 系统部分, 勿改
// 定位: 材质**直控**输出 —— MaterialMain 直接给出 4 个 GBuffer RT(原始值),
//       不经过“PBR 语义 → 自动打包”。模板只负责: 输入解包 + 系统量(速度/曝光)
//       + 调用 MaterialMain + 写 o0..o3。
// 依据: character_default 原版 Deferred PS(blob33) 的接口/打包; 默认材质函数复刻其
//       “语义 + 打包”, 故默认版 ≈ 原版。
// 组装方式: 本文件 + 用户的 MaterialMain(插入到下方标记行处)。
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
    float  v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)(VS 仅输出 x ⇒ 声明标量)
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
    float2 uv0;            // 主 UV
    float2 uv1;            // 副 UV
    float3 positionWS;     // 世界坐标
    float3 viewDir;        // 相机 -> 像素(单位向量)
    float3 Normal;         // ⚠ GBuffer/插值约定(已 .xzy); 光照请用 NormalWS
    float3 NormalWS;       // 世界(光照)空间法线(= Normal.xzy) —— 与光方向同空间, 做 N·L 用它
    float3 Tangent;        // ⚠ 同 Normal 的约定(已 .xzy); 世界空间计算请 .xzy
    float3 Bitangent;      // ⚠ 同 Normal 的约定(已 .xzy); 世界空间计算请 .xzy
    float3 camPos;         // 相机世界坐标
    float3 camDir;         // 相机前向(已归一)
    float3 camUp;          // 相机上向(已归一)
    float2 velocity;       // 系统: 屏幕空间速度(RT3.yz 默认)
    float  exposureScale;  // 系统: 1/(白点*曝光系数)(RT0 曝光用)
};

// ---- 材质输出(直写原始 GBuffer; 由 MaterialMain 赋值) ----
struct MaterialOutput
{
    float4 RT0;   // 原始 GBuffer RT0(自发光/旁路光照)
    float4 RT1;   // 原始 GBuffer RT1(BaseColor.rgb + Metallic/半透明.w)
    float4 RT2;   // 原始 GBuffer RT2(法线八面体.xy + Roughness.z + Misc.w)
    float4 RT3;   // 原始 GBuffer RT3(Occlusion.x + Velocity.yz + SubSurface.w)
};

// 法线八面体编码(等价原版; 供默认材质/用户调用)
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
    mi.camPos = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,
                       transposeViewInvMat[2].w);
    mi.camDir = normalize(float3(transposeViewInvMat[0].z, transposeViewInvMat[1].z,
                                 transposeViewInvMat[2].z));
    mi.camUp  = normalize(float3(transposeViewInvMat[0].y, transposeViewInvMat[1].y,
                                 transposeViewInvMat[2].y));
    mi.Normal = normalize(i.v1.xyz).xzy;
    mi.NormalWS = mi.Normal.xzy;   // 世界(光照)空间; 做 N·L 用这个
    mi.Tangent = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    mi.Bitangent = cross(mi.Normal, mi.Tangent);
    mi.Bitangent = (i.v3.z < 0.0) ? -mi.Bitangent : mi.Bitangent;
    mi.Bitangent = normalize(mi.Bitangent);
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
    return o;
}
