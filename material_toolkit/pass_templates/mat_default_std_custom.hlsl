// deferred_std_custom(自定义光照 / 直控 GBuffer) 的缺省材质: 中性默认值(直写 4 个 RT)。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    m.RT0 = float4(0.0, 0.0, 0.0, 0.0);              // 自发光(RT0)
    m.RT1 = float4(1.0, 1.0, 1.0, 0.04);             // BaseColor(白) + Metallic(0.04 下限)
    m.RT2 = float4(0.5, 0.5, 0.5, 0.666667);         // 法线(0.5,0.5≈(0,0,1)) + Roughness(0.5) + Misc
    m.RT3 = float4(1.0, 0.0, 0.0, 1.0);              // Occlusion(1) + Velocity(0) + SubSurface(1)
}
