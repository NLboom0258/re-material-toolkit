// ---- 默认材质函数(character_default) —— 等价原版材质逻辑; 可被用户替换 ----
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    float4 base  = BaseMetalMap.Sample(AutomaticWrap, mi.uv0);
    float4 nr    = NormalRoughnessMap.Sample(AutomaticWrap, mi.uv0);
    float4 atos  = AlphaTranslucentOcclusionSSSMap.Sample(AutomaticWrap, mi.uv0);

    // 切线空间法线
    float3 nt = nr.xyz * 2.0 - 1.0;
    m.NormalTS = nt;
    float3 nWS = normalize(mi.Tangent * nt.x + mi.Bitangent * nt.y + mi.Normal * nt.z);

    // 限界光(LimLight): 基于法线.视线夹角的幂 → 写进 RT0(旁路光照)
    float ndv = dot(nWS.xzy, -mi.viewDir);
    float oneMinus = 1.0 - ndv;
    float mixv = max(VAR_LimLight_Invert * (ndv - oneMinus) + oneMinus, 0.000001);
    float pw = exp(log(mixv) * VAR_LimLight_Pow);
    m.Emissive = VAR_LimLight_Color.rgb * pw;

    // Albedo + 半透明偏置
    float3 alb = base.rgb * VAR_BaseColor.rgb;
    float off = 1.0 + VAR_AlbedoOffsetIntensity;
    float tl  = atos.y * VAR_TranslucencyIntensity;
    m.BaseColor = alb + tl * (off * alb - alb);

    m.Metallic  = base.a * VAR_Metallic;
    m.Roughness = nr.a * VAR_Roughness;

    // 高光反射率(可被贴图调制)
    float refl = 1.0 + VAR_Use_SpecularReflectanceMap * (atos.z - 1.0);
    m.Reflectance = refl * VAR_SpecularReflectance;

    // 次表面(SSS)
    float s3 = mad(VAR_SSS_Channel, 2.0, -1.0);
    float s4 = mad(s3, 0.0714285746, -1.0);
    m.SubSurface = saturate(VAR_SSS_Channel) * s4 + 1.0;

    // 遮挡(可选次 UV)
    float occ = atos.z;
    if (VAR_OcclusionMap_UseSecondaryUV > 0.0)
        occ = AlphaTranslucentOcclusionSSSMap.Sample(AutomaticWrap, mi.uv1).z;
    m.Occlusion = VAR_OcclusionIntensity * (occ - 1.0) + 1.0;
    m.Translucency = tl;
}
