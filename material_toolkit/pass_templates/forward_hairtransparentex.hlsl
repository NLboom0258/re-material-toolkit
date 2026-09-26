// ============================================================================
// RE mmtr pass 模板: Forward (character_hair_transparentex)  —— 系统部分, 勿改
// 定位: **自定义输出**模式 —— 不重写引擎光照, 只负责“输入解包 + 调 MaterialMain + 输出 o0”。
// 依据: character_hair_transparentex 原版 Forward PS(blob33-44) 的接口/签名 + 其前向 VS。
// 头发前向 PSIn 约定(由前向 VS 决定):
//   INTERPOLATOR0 = (法线.xyz, uv0.x)
//   INTERPOLATOR1 = (uv0.y, uv1.xy, 切线.x)
//   INTERPOLATOR2 = (切线.y, 切线.z, 切线w符号, 世界坐标.x)
//   INTERPOLATOR3 = (世界坐标.y, 世界坐标.z)          // mask xy
//   (另有 SV_IsFrontFace; 本 MVP 暂不暴露以保签名兼容所有变体)
// 输出: 单张 SV_Target0; 与原件一致: o0 = (Color*Alpha, Alpha)。
// 组装方式: 本文件 + 用户的 MaterialMain(插入到下方标记行处)。
// ============================================================================

//__IFACE_BEGIN__
cbuffer SceneInfo : register(b0)
{
    row_major float4x4 viewProjMat;
    row_major float3x4 transposeViewMat;
    row_major float3x4 transposeViewInvMat;   // [0..2].w = 相机世界坐标; .z 前向; .y 上向
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
//__IFACE_END__

// ---- 输入签名(= 前向 VS 的输出) ----
struct PSIn
{
    float4 svpos : SV_Position;    // reg0
    float4 v1    : INTERPOLATOR0;  // reg1  xyz=法线, w=uv0.x
    float4 v2    : INTERPOLATOR1;  // reg2  x=uv0.y, yz=uv1, w=切线.x
    float4 v3    : INTERPOLATOR2;  // reg3  xy=切线.yz, z=切线w符号, w=世界坐标.x
    float2 v4    : INTERPOLATOR3;  // reg4  xy=世界坐标.yz
};

// ---- 材质输出(前向 = 最终颜色; 由 MaterialMain 赋值) ----
// 与原件一致: o0 = (Color * Alpha, Alpha)(premultiplied; Alpha=1 即不透明)
struct MaterialOutput
{
    float3 Color;          // 输出色
    float  Alpha;          // 不透明度(1=不透明)
};

// ---- 系统预制输入(前向: 已算好, 材质可直接用; 见 GUI「输入」页) ----
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

//__MATERIAL_MAIN__

float4 main(PSIn i) : SV_Target0
{
    // ---- 1. 输入解包 ----
    float2 uv0   = float2(i.v1.w, i.v2.x);
    float2 uv1   = i.v2.yz;
    float3 N     = normalize(i.v1.xyz);
    float3 T     = normalize(float3(i.v2.w, i.v3.x, i.v3.y));
    float  tsign = i.v3.z;
    float3 posWS = float3(i.v3.w, i.v4.x, i.v4.y);

    float3 B = normalize(cross(N, T)) * ((tsign < 0.0) ? -1.0 : 1.0);

    float3 camPos = float3(transposeViewInvMat[0].w, transposeViewInvMat[1].w,
                           transposeViewInvMat[2].w);
    float3 camDir = normalize(float3(transposeViewInvMat[0].z, transposeViewInvMat[1].z,
                                     transposeViewInvMat[2].z));
    float3 camUp  = normalize(float3(transposeViewInvMat[0].y, transposeViewInvMat[1].y,
                                     transposeViewInvMat[2].y));
    float3 V = normalize(camPos - posWS);

    // ---- 2. 材质逻辑 ----
    MaterialInput mi;
    mi.uv0 = uv0;
    mi.uv1 = uv1;
    mi.Normal = N;
    mi.Tangent = T;
    mi.Bitangent = B;
    mi.positionWS = posWS;
    mi.viewDir = V;
    mi.camPos = camPos;
    mi.camDir = camDir;
    mi.camUp  = camUp;
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 输出(前向 = 最终颜色; premultiplied: o0=(Color*Alpha, Alpha)) ----
    return float4(m.Color * m.Alpha, m.Alpha);
}
