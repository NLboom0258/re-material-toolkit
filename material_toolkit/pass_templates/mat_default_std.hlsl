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
