// ---- 默认材质函数(前向/custom) —— 可被用户替换 ----
// 前向: MaterialMain 直接决定输出色(o0 = m.Emissive); mi 提供 uv/法线基/世界坐标/相机基。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    m.BaseColor    = float3(0.8, 0.8, 0.8);
    m.Metallic     = 0.0;
    m.Roughness    = 0.5;
    m.NormalTS     = float3(0.0, 0.0, 1.0);
    m.Emissive     = float3(0.5, 0.5, 0.5);
    m.Occlusion    = 1.0;
    m.Translucency = 0.0;
}
