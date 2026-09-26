// ============================================================================
// RE mmtr pass 模板: Forward (character_eyetransparentex)  —— 系统部分, 勿改
// 定位: **自定义输出**模式 —— 本模板**不重写**引擎那套前向光照(光剔除/IBL/级联阴影/
//       面光/IES); 只负责“输入解包 + 调 MaterialMain + 输出最终色”, 光照/风格由材质自写。
// 依据: character_eyetransparentex 原版 Forward PS(blob36, ~1446 指令) 的**接口/签名**。
// 前向 PSIn 约定(由前向 VS 决定, 各标志前缀一致):
//   INTERPOLATOR0 = (法线.xyz, uv0.x)
//   INTERPOLATOR1 = (uv0.y, 切线.xyz)
//   INTERPOLATOR2 = (切线w符号, 世界坐标.xyz)
//   (另有 SV_IsFrontFace; 本 MVP 暂不暴露, 以保持输入签名兼容所有变体)
// 输出: 单张 SV_Target0 = 最终颜色(取 m.Color)。
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
    float2 screenSize;
    float2 screenInverseSize;
    float2 cullingHelper;
    float4 viewFrustum[6];
    float4 clipplane;
};
//__IFACE_END__

// ---- 输入签名(= 前向 VS 的输出) ----
struct PSIn
{
    float4 svpos : SV_Position;    // reg0
    float4 v1    : INTERPOLATOR0;  // reg1  xyz=法线, w=uv0.x
    float4 v2    : INTERPOLATOR1;  // reg2  x=uv0.y, yzw=切线
    float4 v3    : INTERPOLATOR2;  // reg3  x=切线w符号, yzw=世界坐标
};

// ---- 材质输出(前向 = 最终颜色; 由 MaterialMain 赋值) ----
struct MaterialOutput
{
    float3 Color;          // 最终输出色(o0)
};

// ---- 系统预制输入(前向: 已算好, 材质可直接用; 见 GUI「输入」页) ----
struct MaterialInput
{
    float2 uv0;            // 主 UV
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
    float3 N     = normalize(i.v1.xyz);
    float3 T     = normalize(i.v2.yzw);
    float  tsign = i.v3.x;
    float3 posWS = i.v3.yzw;

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

    // ---- 3. 输出(前向 = 最终颜色) ----
    return float4(m.Color, 1.0);
}
