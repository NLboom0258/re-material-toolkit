// ============================================================================
// RE mmtr pass 模板: Deferred (env_emissive) —— per-instance 路径  —— 系统部分, 勿改
// 依据: env_emissive 原版 Deferred PS(blob41) 反汇编逐条等价改写。
// 与 deferred_env 的差异: 材质参数走 `UserMaterialInstances` 结构化缓冲(按实例索引),
//                        无 cb3 UserMaterial; 纹理整体后移 +1; 多一个实例索引输入(v6.x)。
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

// ---- 材质参数(表2; per-instance 结构化缓冲; 成员=env UserMaterial) ----
struct UMParams
{
    float4 VAR_EmissiveColor;
    float4 VAR_BaseColor;
    float  VAR_EmitIntensity;
    float  VAR_DetoneEmitIntensity;
    float  VAR_DetoneEmitRate;
    float  VAR_Metallic;
    float  VAR_Roughness;
    float  VAR_Translucency;
    float  VAR_AlphaTestRef;
    float  VAR_UVSelect_OCC_Emissive;
    float  VAR_AlphaValue;
    float  VAR_DissolveOffset;
    float  CAPCOM_MATERIAL_RESERVE0;
    float  CAPCOM_MATERIAL_RESERVE1;
};

StructuredBuffer<UMParams> UserMaterialInstances : register(t1);

// 裸名 static 全局(供 MaterialMain 直接用 VAR_*)
static float4 VAR_EmissiveColor;
static float4 VAR_BaseColor;
static float  VAR_EmitIntensity;
static float  VAR_DetoneEmitIntensity;
static float  VAR_DetoneEmitRate;
static float  VAR_Metallic;
static float  VAR_Roughness;
static float  VAR_Translucency;
static float  VAR_AlphaTestRef;
static float  VAR_UVSelect_OCC_Emissive;
static float  VAR_AlphaValue;
static float  VAR_DissolveOffset;
static float  CAPCOM_MATERIAL_RESERVE0;
static float  CAPCOM_MATERIAL_RESERVE1;

void LoadMaterialParams(UMParams _mu)
{
    VAR_EmissiveColor = _mu.VAR_EmissiveColor;
    VAR_BaseColor = _mu.VAR_BaseColor;
    VAR_EmitIntensity = _mu.VAR_EmitIntensity;
    VAR_DetoneEmitIntensity = _mu.VAR_DetoneEmitIntensity;
    VAR_DetoneEmitRate = _mu.VAR_DetoneEmitRate;
    VAR_Metallic = _mu.VAR_Metallic;
    VAR_Roughness = _mu.VAR_Roughness;
    VAR_Translucency = _mu.VAR_Translucency;
    VAR_AlphaTestRef = _mu.VAR_AlphaTestRef;
    VAR_UVSelect_OCC_Emissive = _mu.VAR_UVSelect_OCC_Emissive;
    VAR_AlphaValue = _mu.VAR_AlphaValue;
    VAR_DissolveOffset = _mu.VAR_DissolveOffset;
    CAPCOM_MATERIAL_RESERVE0 = _mu.CAPCOM_MATERIAL_RESERVE0;
    CAPCOM_MATERIAL_RESERVE1 = _mu.CAPCOM_MATERIAL_RESERVE1;
}

// ---- 材质贴图/sampler(表2; 纹理后移 +1) ----
ByteAddressBuffer WhitePtSrv                        : register(t0);
Texture2D<float4> BaseMetalMap                      : register(t2);
Texture2D<float4> NormalRoughnessMap                : register(t3);
Texture2D<float4> AlphaTranslucentOcclusionEmissiveMap : register(t4);
SamplerState BilinearWrap                           : register(s0);
SamplerState AutomaticWrap                          : register(s1);
//__IFACE_END__

// ---- 输入签名(= 模板 VS 的输出; 寄存器顺序 0..6) ----
struct PSIn
{
    float4 svpos : SV_Position;   // reg0  屏幕像素坐标
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线, z=bitangent 符号
    float4 v4    : INTERPOLATOR3; // reg4  zw=速度项(当前)
    float4 v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)
    nointerpolation float idx : NOINTERPOLATOR0; // reg6  材质实例索引
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;   // Emissive(旁路光照)
    float4 o1 : SV_Target1;   // BaseColor.rgb + Metallic/Translucency.w
    float4 o2 : SV_Target2;   // 法线八面体编码.xy + Roughness.z + Misc.w
    float4 o3 : SV_Target3;   // Occlusion.x + Velocity.yz + SubSurface.w
};

// ---- 材质语义输出(表3a; 由 MaterialMain 赋值) ----
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

// ---- 系统预制输入(已算好, 材质可直接用; 见 GUI「输入」页) ----
struct MaterialInput
{
    float2 uv0;            // 主 UV
    float2 uv1;            // 副 UV
    float3 Normal;         // 几何法线(已归一)
    float3 Tangent;        // 切线(已归一)
    float3 Bitangent;      // 副切线(已归一)
    float3 camPos;         // 相机世界坐标
    float3 camDir;         // 相机前向(已归一)
    float3 camUp;          // 相机上向(已归一)
};

//__MATERIAL_MAIN__

// ---- 八面体法线编码(等价原件 octahedral 折叠) ----
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
    // ---- 0. 载入 per-instance 材质参数(写入裸名全局) ----
    LoadMaterialParams(UserMaterialInstances[(uint)i.idx]);

    // ---- 1. 输入解包 ----
    float2 uv0 = float2(i.v1.w, i.v2.x);
    float2 uv1 = i.v2.yz;

    float3 N = normalize(i.v1.xyz).xzy;
    float3 T = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    float3 B = cross(N, T);
    B = (i.v3.z < 0.0) ? -B : B;
    B = normalize(B);

    float2 ndc = i.svpos.xy * screenInverseSize * float2(2.0, -2.0) + float2(-1.0, 1.0);
    float2 vel = (i.v4.zw / i.v5.x) - ndc;

    // ---- 2. 材质逻辑 ----
    MaterialInput mi;
    mi.uv0 = uv0;
    mi.uv1 = uv1;
    mi.Normal = N;
    mi.Tangent = T;
    mi.Bitangent = B;
    mi.camPos = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,
                       transposeViewInvMat[2].w);
    mi.camDir = normalize(float3(transposeViewInvMat[0].z, transposeViewInvMat[1].z,
                                 transposeViewInvMat[2].z));
    mi.camUp  = normalize(float3(transposeViewInvMat[0].y, transposeViewInvMat[1].y,
                                 transposeViewInvMat[2].y));
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 打包 ----
    float wp = asfloat(WhitePtSrv.Load(0));
    wp = useAutoExposure ? wp : 1.0;
    wp = wp * exposureAdjustment;
    float detone = VAR_DetoneEmitIntensity / max(1.0 - tonemapRange, 0.0001);
    detone = max(detone / wp, 0.0);
    float emitScale = VAR_DetoneEmitRate * (detone - VAR_EmitIntensity) + VAR_EmitIntensity;

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
    o.o0 = float4(m.Emissive * emitScale, 0.0);
    o.o1 = float4(m.BaseColor, o1w);
    o.o2 = float4(encN, m.Roughness, gbufferTypeFlag * 0.333333343 + darkFlag);
    o.o3 = float4(m.Occlusion, vel, 1.0);
    return o;
}
