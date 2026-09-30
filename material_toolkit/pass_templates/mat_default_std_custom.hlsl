// deferred_std_custom(自定义光照 / 直控 GBuffer) 的缺省材质:
// **初始即等价"默认光照"模式** —— 中性 PBR 值, 并按默认模板(deferred_std)的相同规则
// 打包进 4 个原始 GBuffer RT; 曝光抵消也在此做。因这些逻辑都在材质源里, 用户可**删改**。
// 依赖的预设输入已声明(下方 `//! preset`); 删掉它们即表示自行接管对应逻辑。
//! preset Normal
//! preset Tangent
//! preset Bitangent
//! preset velocity
//! preset exposureScale
//! engine GBufferType

void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    // ---- PBR 语义(中性默认值; 与"默认光照"模式的 mat_default_std 一致) ----
    float3 BaseColor    = float3(1.0, 1.0, 1.0);
    float  Metallic     = 0.0;
    float  Roughness    = 0.5;
    float3 NormalTS     = float3(0.0, 0.0, 1.0);
    float3 Emissive     = float3(0.0, 0.0, 0.0);
    float  Occlusion    = 1.0;
    float  Translucency = 0.0;

    // ---- 打包(复刻 deferred_std 模板的输出逻辑) ----
    float metallic = saturate(Metallic * 1.02 - 0.02);
    bool opaque = (metallic > 0.0) || (Translucency <= 0.0);
    float o1w, darkFlag;
    if (opaque)
    {
        o1w = max(metallic, 0.04);
        darkFlag = 0.666667;
    }
    else
    {
        o1w = round(Translucency * 15.49 + 0.5) * 0.0627451017 + 0.0313725509;
        darkFlag = 0.0;
    }

    // 法线基 = GBuffer/插值约定基(Normal/Tangent/Bitangent 预设已按该约定)
    float3 nt = normalize(NormalTS);
    float3 nrm = normalize(mi.Normal * nt.z + mi.Tangent * nt.x + mi.Bitangent * nt.y);
    float2 encN = OctEncodeNormal(nrm).xy;

    m.RT0 = float4(Emissive, 0.0);
    m.RT1 = float4(BaseColor, o1w);
    m.RT2 = float4(encN, Roughness, gbufferTypeFlag * 0.333333343 + darkFlag);
    m.RT3 = float4(Occlusion, mi.velocity, 1.0);

    // ---- 曝光(引擎会对 RT0 再乘 <白点*曝光> ⇒ 这里自动抵消; 同默认光照模板) ----
    m.RT0.rgb *= mi.exposureScale;
}

// 深度族 pass(默认实现, **必需**): 不丢弃任何像素 —— 与"极简深度 PS"效果一致,
// 且**不引用任何输入**(零额外绑定)。深度/阴影族无颜色输出, 只靠 discard 决定是否写深度。
void MaterialDepth(DepthInput di, inout bool discardPixel)
{
    discardPixel = false;
}

// 顶点钩子(必需, VS 级): 缺省不偏移 ⇒ 等价“无钩子”的标准 VS(逐字节一致)。
float3 MaterialVertex(VertexInput v)
{
    return float3(0.0, 0.0, 0.0);
}
