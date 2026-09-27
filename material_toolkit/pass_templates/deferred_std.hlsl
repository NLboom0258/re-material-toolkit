// ============================================================================
// RE mmtr pass 模板: Deferred —— **标准接口版(自建用)** —— 系统部分, 勿改
// 定位: "自建 mmtr"用。与 deferred_bare 的区别: 声明**完整标准资源集**
//       (SceneInfo/GBufferType/Tonemap/UserMaterial + WhitePtSrv/BaseMetalMap/
//        NormalRoughnessMap/AlphaTranslucentOcclusionSSSMap + AutomaticWrap)。
// 依据(skill §三十四z): PS 只声明部分资源时, 引擎**不绑材质贴图/参数**(采样恒 0);
//       必须声明"完整资源集"并**被使用**(否则被编译器剔除 ⇒ 等于没声明)。
//       资源名/寄存器取自参考材质 Deferred PS 的标准接口。
// 输出: o0=RT0(Emissive) / o1=RT1(BaseColor) / o2=RT2(Normal/Roughness) / o3=RT3.
// 组装方式: 本文件 + 用户的 MaterialMain(插入到下方标记行处)。
// ⚠ 用本模板时**不要**再传 iface(会替换掉 IFACE 块里的标准声明)。
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
    float subdivisionLevel;
    float2 screenSize;
    float2 screenInverseSize;
    float2 cullingHelper;
    float cameraNearPlane;
    float cameraFarPlane;
    float4 viewFrustum[6];
    float4 clipplane;
};

cbuffer GBufferType : register(b1)
{
    float gbufferTypeFlag;
    float gbufferTypeReserve0;
    float gbufferTypeReserve1;
    float gbufferTypeReserve2;
};

cbuffer Tonemap : register(b2)
{
    float exposureAdjustment;
    float tonemapRange;
    float sharpness;
    float preTonemapRange;
    int useAutoExposure;
    float echoBlend;
    float AABlend;
    float AASubPixel;
    float ResponsiveAARate;
};

cbuffer UserMaterial : register(b3)
{
    float4 VAR_LimLight_Color;
    float4 VAR_BaseColor;
    float VAR_LimLight_Intensity;
    float VAR_LimLight_Pow;
    float VAR_LimLight_Invert;
    float VAR_OcclusionMap_UseSecondaryUV;
    float VAR_Metallic;
    float VAR_Roughness;
    float VAR_TranslucencyIntensity;
    float VAR_AlbedoOffsetIntensity;
    float VAR_OcclusionIntensity;
    float VAR_SpecularReflectance;
    float VAR_Use_SpecularReflectanceMap;
    float VAR_SSS_Channel;
    float VAR_UseAlphaMap;
    float VAR_AlphaTestRef;
    float VAR_DissolveControl;
    float VAR_AlphaValue;
    float VAR_DissolveOffset;
    float CAPCOM_MATERIAL_RESERVE0;
    float CAPCOM_MATERIAL_RESERVE1;
    float CAPCOM_MATERIAL_RESERVE2;
};

ByteAddressBuffer WhitePtSrv : register(t0);
Texture2D<float4> BaseMetalMap : register(t1);
Texture2D<float4> NormalRoughnessMap : register(t2);
Texture2D<float4> AlphaTranslucentOcclusionSSSMap : register(t3);
SamplerState AutomaticWrap : register(s0);
//__IFACE_END__

// ---- 输入签名(寄存器 0..5; = Deferred VS 的输出) ----
struct PSIn
{
    float4 svpos : SV_Position;   // reg0
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线.yz, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz
    float4 v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;   // 原始 GBuffer RT0(Emissive)
    float4 o1 : SV_Target1;   // 原始 GBuffer RT1(BaseColor.rgb + Metallic/半透明.w)
    float4 o2 : SV_Target2;   // 原始 GBuffer RT2(法线八面体.xy + Roughness.z + Misc.w)
    float4 o3 : SV_Target3;   // 原始 GBuffer RT3(Occlusion.x + Velocity.yz + SubSurface.w)
};

// ---- 材质输入(供 MaterialMain 使用; 全部来自插值) ----
struct MaterialInput
{
    float2 uv0;            // 主 UV
    float2 uv1;            // 副 UV
    float3 Normal;         // 世界法线(已归一)
    float3 Tangent;        // 世界切线(已归一)
    float3 Bitangent;      // 世界副切线(已归一)
    float3 positionWS;     // 世界坐标
};

// ---- 材质输出(直写原始 GBuffer; 由 MaterialMain 赋值) ----
struct MaterialOutput
{
    float4 RT0;   // 自发光(旁路光照; 测试色写这里最稳)
    float4 RT1;   // BaseColor.rgb + Metallic(.w)
    float4 RT2;   // 法线八面体(.xy) + Roughness(.z) + Misc(.w)
    float4 RT3;   // Occlusion(.x) + Velocity(.yz) + SubSurface(.w)
};

// 曝光补偿常量: 延迟 RT0(自发光) 输出后会被引擎再乘"曝光"+tonemap, 值太小会发黑。
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
    // ---- 1. 输入解包(全部来自插值) ----
    MaterialInput mi;
    mi.uv0 = float2(i.v1.w, i.v2.x);
    mi.uv1 = i.v2.yz;
    mi.Normal = normalize(i.v1.xyz).xzy;
    mi.Tangent = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    mi.Bitangent = normalize(cross(mi.Normal, mi.Tangent));
    mi.positionWS = float3(i.v3.w, i.v4.x, i.v4.y);

    // ---- 2. 材质逻辑 ----
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 写 4 张 GBuffer ----
    PSOut o;
    o.o0 = m.RT0;
    o.o1 = m.RT1;
    o.o2 = m.RT2;
    o.o3 = m.RT3;

    // ---- 4. 保活(死分支): 让标准接口资源留在 RDEF ----
    // 引擎要求记录声明"完整资源集"才会绑定材质贴图/参数; 未使用的声明会被编译器剔除。
    if (i.v1.w > 1e30) {
        o.o0.rgb += NormalRoughnessMap.Sample(AutomaticWrap, i.v2.xx).rgb;
        o.o0.rgb += AlphaTranslucentOcclusionSSSMap.Sample(AutomaticWrap, i.v2.yy).rgb;
        o.o0.rgb += float(WhitePtSrv.Load(0)).xxx;
        o.o0.rgb += viewProjMat[0][0].xxx + gbufferTypeFlag.xxx
                  + exposureAdjustment.xxx + VAR_LimLight_Color.rgb;
    }
    return o;
}
