// deferred_std(默认光照 / PBR 语义) 的缺省材质: **引擎默认值式**(PBR 中性)。
// BaseColor 白 / Metallic 0 / Roughness 0.5 / 法线(0,0,1) / Emissive 0 / Occlusion 1 / 半透明 0。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    m.BaseColor    = float3(1.0, 1.0, 1.0);
    m.Metallic     = 0.0;
    m.Roughness    = 0.5;
    m.NormalTS     = float3(0.0, 0.0, 1.0);
    m.Emissive     = float3(0.0, 0.0, 0.0);
    m.Occlusion    = 1.0;
    m.Translucency = 0.0;
}

// 深度族 pass(默认实现, **必需**): 不丢弃任何像素 —— 与"极简深度 PS"效果一致,
// 且**不引用任何输入**(零额外绑定)。深度/阴影族无颜色输出, 只靠 discard 决定是否写深度。
void MaterialDepth(DepthInput di, inout bool discardPixel)
{
    discardPixel = false;
}

// 顶点钩子(必需, VS 级): 缺省不偏移 ⇒ 等价“无钩子”的标准 VS(逐字节一致)。
float3 MaterialVertex(VertexInput vi)
{
    return float3(0.0, 0.0, 0.0);
}
