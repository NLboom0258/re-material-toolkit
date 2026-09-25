// ---- 默认材质函数(env_emissive) —— 等价原版材质逻辑; 可被用户替换 ----
void MaterialMain(in PSIn i, in float2 uv0, in float2 uv1, out MaterialOutput m)
{
    float4 base  = BaseMetalMap.Sample(AutomaticWrap, uv0);
    float4 nr    = NormalRoughnessMap.Sample(AutomaticWrap, uv0);
    float4 atosA = AlphaTranslucentOcclusionEmissiveMap.Sample(BilinearWrap, uv0);
    // VAR_UVSelect_OCC_Emissive != 0 时, 遮挡/自发光遮罩改采样 UV1
    float4 atosB = (VAR_UVSelect_OCC_Emissive != 0.0)
                 ? AlphaTranslucentOcclusionEmissiveMap.Sample(BilinearWrap, uv1)
                 : atosA;

    m.BaseColor    = base.rgb * VAR_BaseColor.rgb;
    m.Metallic     = base.a * VAR_Metallic;
    m.Roughness    = nr.a * VAR_Roughness;
    m.NormalTS     = nr.xyz * 2.0 - 1.0;
    m.Emissive     = atosB.w * base.rgb * VAR_EmissiveColor.rgb;
    m.Occlusion    = atosB.z;
    m.Translucency = atosA.y * VAR_Translucency;
}
