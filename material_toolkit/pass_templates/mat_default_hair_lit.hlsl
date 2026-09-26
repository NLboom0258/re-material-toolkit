// ---- 默认材质函数(前向 / 默认光照) —— 只给 PBR(表3a 子集); 光照由模板算 ----
// 验证用: 固定 albedo + 平法线, 便于观察方向光/阴影。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    m.BaseColor = float3(0.8, 0.8, 0.8);
    m.Roughness = 0.5;
    m.Metallic  = 0.0;
    m.NormalTS  = float3(0.0, 0.0, 1.0);
    m.Emissive  = float3(0.0, 0.0, 0.0);
}
