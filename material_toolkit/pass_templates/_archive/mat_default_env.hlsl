// ---- 默认材质函数(env_emissive) —— 等价原版材质逻辑; 可被用户替换 ----
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    float4 base  = BaseMetalMap.Sample(AutomaticWrap, mi.uv0);
    float4 nr    = NormalRoughnessMap.Sample(AutomaticWrap, mi.uv0);
    float4 atosA = AlphaTranslucentOcclusionEmissiveMap.Sample(BilinearWrap, mi.uv0);
    // VAR_UVSelect_OCC_Emissive != 0 时, 遮挡/自发光遮罩改采样 UV1
    float4 atosB = (VAR_UVSelect_OCC_Emissive != 0.0)
                 ? AlphaTranslucentOcclusionEmissiveMap.Sample(BilinearWrap, mi.uv1)
                 : atosA;

    m.BaseColor    = base.rgb * VAR_BaseColor.rgb;
    m.Metallic     = base.a * VAR_Metallic;
    m.Roughness    = nr.a * VAR_Roughness;
    m.NormalTS     = nr.xyz * 2.0 - 1.0;
    m.Emissive     = atosB.w * base.rgb * VAR_EmissiveColor.rgb;
    m.Occlusion    = atosB.z;
    m.Translucency = atosA.y * VAR_Translucency;
}
