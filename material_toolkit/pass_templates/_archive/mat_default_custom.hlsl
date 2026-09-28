// ---- 默认材质函数(延迟 / 自定义直写) —— 复刻原版 character 的“语义 + 打包”; 可被替换 ----
// 直写: 直接给出 4 个 GBuffer RT(原始值)。系统量(速度/曝光)由模板经 mi 提供。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    float4 base = BaseMetalMap.Sample(AutomaticWrap, mi.uv0);
    float4 nr   = NormalRoughnessMap.Sample(AutomaticWrap, mi.uv0);
    float4 atos = AlphaTranslucentOcclusionSSSMap.Sample(AutomaticWrap, mi.uv0);

    // 切线空间法线 -> 世界空间法线
    float3 nt  = nr.xyz * 2.0 - 1.0;
    float3 nWS = normalize(mi.Tangent * nt.x + mi.Bitangent * nt.y + mi.Normal * nt.z);

    // RT0: 限界光(LimLight, 旁路光照) × 曝光
    float ndv = dot(nWS.xzy, -mi.viewDir);
    float oneMinus = 1.0 - ndv;
    float mixv = max(VAR_LimLight_Invert * (ndv - oneMinus) + oneMinus, 0.000001);
    float pw = exp(log(mixv) * VAR_LimLight_Pow);
    float3 emissive = VAR_LimLight_Color.rgb * pw;
    float inten = max(VAR_LimLight_Intensity / max(1.0 - tonemapRange, 0.0001)
                      * mi.exposureScale, 0.0);
    m.RT0 = float4(emissive * inten, 0.0);

    // BaseColor + 半透明偏置
    float3 alb = base.rgb * VAR_BaseColor.rgb;
    float off  = 1.0 + VAR_AlbedoOffsetIntensity;
    float tl   = atos.y * VAR_TranslucencyIntensity;
    float3 baseColor = alb + tl * (off * alb - alb);
    float metallic = base.a * VAR_Metallic;

    // RT1.w: 不透明 -> metallic/reflectance; 半透明 -> translucency+reflectance(各量化 4bit)
    float refl = (1.0 + VAR_Use_SpecularReflectanceMap * (atos.z - 1.0)) * VAR_SpecularReflectance;
    float metallic2 = saturate(metallic * 1.02 - 0.02);
    float o1w, darkFlag;
    if ((metallic2 > 0.0) || (tl <= 0.0))
    {
        o1w = max(metallic2, min(refl * 0.04, 0.08));
        darkFlag = 0.666667;
    }
    else
    {
        float t4 = round(tl * 15.49 + 0.5);
        float r4 = round(saturate(refl * 0.5) * 15.49 + 0.5);
        o1w = t4 * 0.0627451017 + r4 * 0.00392156886;
        darkFlag = 0.0;
    }
    m.RT1 = float4(baseColor, o1w);

    // RT2: 法线(八面体) + Roughness + Misc(GBufferType/3 + 不透明标志)
    float2 encN = OctEncodeNormal(nWS).xy;
    m.RT2 = float4(encN, nr.a * VAR_Roughness, gbufferTypeFlag * 0.333333343 + darkFlag);

    // RT3: Occlusion + Velocity + SubSurface
    float occ = atos.z;
    if (VAR_OcclusionMap_UseSecondaryUV > 0.0)
        occ = AlphaTranslucentOcclusionSSSMap.Sample(AutomaticWrap, mi.uv1).z;
    occ = VAR_OcclusionIntensity * (occ - 1.0) + 1.0;
    float s3 = mad(VAR_SSS_Channel, 2.0, -1.0);
    float s4 = mad(s3, 0.0714285746, -1.0);
    float ss = saturate(VAR_SSS_Channel) * s4 + 1.0;
    m.RT3 = float4(occ, mi.velocity, ss);
}
