// deferred_std 的默认材质: 基础色贴图 × BaseColor -> 自发光(RT0)。
// 与 assets/mod/_self_mat/std_texparm.hlsl 同; 供 mat-nogen 不显式传材质源时使用。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    float3 albedo = BaseMetalMap.Sample(AutomaticWrap, mi.uv0).rgb * VAR_BaseColor.rgb;
    m.RT0 = float4(albedo, 0.0) * BARE_RT0_EXPOSURE;
    m.RT1 = float4(albedo, 0.0);
    m.RT2 = float4(OctEncodeNormal(mi.Normal).xy, 0.5, 0.666667);
    m.RT3 = float4(1.0, 0.0, 0.0, 0.0);
}
