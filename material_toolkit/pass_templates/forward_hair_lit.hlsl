// ============================================================================
// RE mmtr pass 模板: Forward —— **引擎式光照(默认光照 / default)** —— 系统部分, 勿改
// 定位: 材质只给 PBR(表3a 子集); 光照由本模板按引擎逻辑算(方向光 + 级联阴影)。
// 依据: character_hair_transparentex 原版前向 PS 的接口(光照资源) + 参考延迟全局光照
//       shader(M2全局光照…)的方向光/阴影写法。
// 说明: MVP 只做【方向光 NoL + 级联阴影】; 局部光/IBL/雾/高光 待后续。
// 前向 PSIn 约定(同 forward_hairtransparentex): INTERP0=(N.xyz,uv0.x)、
//   INTERP1=(uv0.y,uv1.xy,T.x)、INTERP2=(T.yz,Tsign,posWS.x)、INTERP3=(posWS.yz)。
// 输出: 单张 SV_Target0 = 最终颜色。
// 组装方式: 本文件 + 用户的 MaterialMain(插入到下方标记行处)。
// ============================================================================

//__IFACE_BEGIN__
cbuffer SceneInfo : register(b0)
{
    row_major float4x4 viewProjMat;
    row_major float3x4 transposeViewMat;
    row_major float3x4 transposeViewInvMat;   // [0..2].w = 相机世界坐标
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

cbuffer DirectionalLightParameter : register(b3)
{
    float3 DL_Direction;             // 光传播方向(光->场景)
    uint   DL_Enable;
    float3 DL_Color;
    float  DL_MinAlpha;
    row_major float4x4 DL_ViewProjection;
    float  DL_Variance;
    uint   DL_ArrayIndex;
    uint   DL_MipIndex;
    float  DL_Bias;
    float3 Cascade_Translate1;
    float  Cascade_Bias1;
    float3 Cascade_Translate2;
    float  Cascade_Bias2;
    float3 Cascade_Translate3;
    float  Cascade_Bias3;
    float2 Cascade_Scale1;
    float2 Cascade_Scale2;
    float2 Cascade_Scale3;
    uint   SDSMEnable;
    uint   SDSMDebugDraw;
    float4 CascadeDistance;
};

cbuffer Tonemap : register(b5)
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

cbuffer UserMaterial : register(b6)
{
    float4 VAR_LimLight_Color;
    float4 VAR_PrimalySpecularColor;
    float4 VAR_SecondarySpecularColor;
    float4 VAR_LightDirection;
    float4 VAR_EmissiveColor_Low;
    float4 VAR_EmissiveColor_High;
    float4 VAR_ExtraEmissive_Color;
    float4 VAR_BaseColor;
    float4 VAR_OcclusionColor;
    float4 VAR_SlowEffectSphere_Position;
    float  VAR_LimLight_Intensity;
    float  VAR_LimLight_Pow;
    float  VAR_LimLight_UseMeshNormal;
    float  VAR_LimLight_FakeNormalCheck;
    float  VAR_SpecularMaskIntensity;
    float  VAR_SpecularIntensity;
    float  VAR_Primaly_Anisotropy;
    float  VAR_Secondary_AnisoOffset;
    float  VAR_PrimalySpec_Sharpness;
    float  VAR_SecondSpec_Sharpness;
    float  VAR_SpecularShiftIntensity;
    float  VAR_EnableEmissive;
    float  VAR_Emissive_Intensity;
    float  VAR_DetoneEmissive_Intensity;
    float  VAR_DetoneEmissive_Rate;
    float  VAR_ExtraEmissive_Intensity;
    float  VAR_ExtraEmissive_Range;
    float  VAR_Roughness;
    float  VAR_Translucency;
    float  VAR_AlphaTestRef;
    float  VAR_UseDissolveTest;
    float  VAR_DissolveTest_Min;
    float  VAR_DissolveTest_Max;
    float  VAR_OccDark;
    float  VAR_AlphaValue;
    float  VAR_TimeLock_EmissiveIntensity;
    float  VAR_SlowEffectSphere_Radius;
    float  VAR_BackfaceNormalReverse;
    float  VAR_Transparent_Max;
    float  VAR_Transparent_Min;
    float  VAR_DissolveOffset;
    float  CAPCOM_MATERIAL_RESERVE;
};

Texture2DArray<float> ShadowMapSRV           : register(t13);
Texture2D<float4>     NormalRoughnessMap     : register(t17);
Texture2D<float4>     AlphaTranslucentOcclusionSSSMap : register(t18);
SamplerState          AutomaticWrap          : register(s4);
SamplerComparisonState LinearCompare         : register(s5);
//__IFACE_END__

// ---- 输入签名(同 forward_hairtransparentex) ----
struct PSIn
{
    float4 svpos : SV_Position;    // reg0
    float4 v1    : INTERPOLATOR0;  // reg1  xyz=法线, w=uv0.x
    float4 v2    : INTERPOLATOR1;  // reg2  x=uv0.y, yz=uv1, w=切线.x
    float4 v3    : INTERPOLATOR2;  // reg3  xy=切线.yz, z=切线w符号, w=世界坐标.x
    float2 v4    : INTERPOLATOR3;  // reg4  xy=世界坐标.yz
};

// ---- 材质输入(供 MaterialMain 使用) ----
struct MaterialInput
{
    float2 uv0;            // 主 UV
    float2 uv1;            // 副 UV
    float3 Normal;         // 世界法线(已归一)
    float3 Tangent;        // 世界切线(已归一)
    float3 Bitangent;      // 世界副切线(已归一)
    float3 positionWS;     // 世界坐标
    float3 viewDir;        // 相机 -> 像素(单位向量)
    float3 camPos;         // 相机世界坐标
    float3 camDir;         // 相机前向(已归一)
    float3 camUp;          // 相机上向(已归一)
};

// ---- 材质输出(默认光照: 材质只给 PBR; 由 MaterialMain 赋值) ----
struct MaterialOutput
{
    float3 BaseColor;
    float  Roughness;
    float  Metallic;
    float3 NormalTS;       // 切线空间法线
    float3 Emissive;
};

//__MATERIAL_MAIN__

// ---- 方向光级联阴影(UV 选级联; 对应原版前向 PS 的 uv-based 分支) ----
// 注: 原版另有“距离选级联(SDSM)”分支(if SDSMEnable); 本函数为 uv-based, 待实机确认用哪条。
// 返回 [0,1](1=受光)。
float RE_DirShadow(float3 posWS)
{
    float4 ls = mul(float4(posWS, 1.0), DL_ViewProjection);   // primary 投影 xy∈[0,1]
    float2 u1 = float2(ls.x * Cascade_Translate1.z + Cascade_Translate1.x,
                       ls.y * Cascade_Translate1.z + Cascade_Translate1.y);
    float2 u2 = float2(ls.x * Cascade_Translate2.z + Cascade_Translate2.x,
                       ls.y * Cascade_Translate2.z + Cascade_Translate2.y);
    float2 u3 = float2(ls.x * Cascade_Translate3.z + Cascade_Translate3.x,
                       ls.y * Cascade_Translate3.z + Cascade_Translate3.y);
    float2 uv;
    float  arr;
    // 逐一测试：cascade1 用原始 ls; 之后用上一级 remap 后的 uv(同原版)
    if (max(abs(ls.x - 0.5), abs(ls.y - 0.5)) < 0.5)
        { uv = u1; arr = (float)DL_ArrayIndex; }
    else if (max(abs(u1.x - 0.5), abs(u1.y - 0.5)) < 0.5)
        { uv = u2; arr = (float)DL_ArrayIndex + 1.0; }
    else if (max(abs(u2.x - 0.5), abs(u2.y - 0.5)) < 0.5)
        { uv = u3; arr = (float)DL_ArrayIndex + 2.0; }
    else if (max(abs(u3.x - 0.5), abs(u3.y - 0.5)) < 0.5)
        { uv = u3; arr = (float)DL_ArrayIndex + 3.0; }
    else
        return 1.0;   // 不在任何级联内 -> 不投影
    return ShadowMapSRV.SampleCmpLevelZero(LinearCompare, float3(uv, arr), ls.z + DL_Bias);
}

float4 main(PSIn i) : SV_Target0
{
    // ---- 1. 输入解包 ----
    float3 N     = normalize(i.v1.xyz);
    float3 T     = normalize(float3(i.v2.w, i.v3.x, i.v3.y));
    float  tsign = i.v3.z;
    float3 posWS = float3(i.v3.w, i.v4.x, i.v4.y);
    float3 B     = normalize(cross(N, T)) * ((tsign < 0.0) ? -1.0 : 1.0);

    float3 camPos = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,
                           transposeViewInvMat[2].w);
    float3 camDir = normalize(float3(transposeViewInvMat[0].z, transposeViewInvMat[1].z,
                                     transposeViewInvMat[2].z));
    float3 camUp  = normalize(float3(transposeViewInvMat[0].y, transposeViewInvMat[1].y,
                                     transposeViewInvMat[2].y));

    MaterialInput mi;
    mi.uv0 = float2(i.v1.w, i.v2.x);
    mi.uv1 = i.v2.yz;
    mi.Normal = N;
    mi.Tangent = T;
    mi.Bitangent = B;
    mi.positionWS = posWS;
    mi.viewDir = normalize(camPos - posWS);
    mi.camPos = camPos;
    mi.camDir = camDir;
    mi.camUp  = camUp;

    // ---- 2. 材质逻辑(PBR) ----
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 光照(引擎式: 方向光 NoL; 级联阴影本轮暂关, 先验证光照) ----
    float3 mt = normalize(m.NormalTS);
    float3 nWS = normalize(mi.Tangent * mt.x + mi.Bitangent * mt.y + mi.Normal * mt.z);

    float3 light = float3(0.0, 0.0, 0.0);
    if (DL_Enable != 0)
    {
        // 原版前向 PS: dp3_sat r0.y, r2.xyzx, cb3[0].xyzx  => dot(N, +DL_Direction)
        float ndl = saturate(dot(nWS, DL_Direction));
        light = DL_Color * ndl;
    }

    float3 color = m.BaseColor * (light + 0.05) + m.Emissive;   // +0.05 环境光(便于观察)
    return float4(color, 1.0);
}
