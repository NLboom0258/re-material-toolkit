// ============================================================================
// RE mmtr pass 模板: Deferred —— **std · 默认光照 · 逐实例(PBR 语义)** —— 系统部分, 勿改
// 用途: 技术名带 `…Instancing2` 的槽(逐实例材质: 引擎绑 `UserMaterialInstances` 结构化缓冲,
//       VS 多输出一个 NOINTERPOLATOR0 实例索引)。
// 与 deferred_std 的差异: 材质参数走 `UserMaterialInstances`(按实例索引), 无 cb3; 纹理整体 +1;
//       PSIn 多一个实例索引(reg6 = NOINTERPOLATOR0)。
// 接口块由 `material_iface.hlsl_of(style="instance")` 替换(调用方须传 iface)。
// ============================================================================

//__IFACE_BEGIN__
// (占位; 该 pass 的引擎资源依赖由**依赖系统**注入 —— 见 presets/<ver>/passes.json)
//__IFACE_END__

// ---- 输入签名(寄存器 0..6) ----
struct PSIn
{
    float4 svpos : SV_Position;   // reg0
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线.yz, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz
    float  v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)(VS 仅输出 x ⇒ 声明标量)
    nointerpolation float idx : NOINTERPOLATOR0; // reg6  材质实例索引
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;
    float4 o1 : SV_Target1;
    float4 o2 : SV_Target2;
    float4 o3 : SV_Target3;
};

// ---- 材质输出(PBR 语义; 表3a) ----
struct MaterialOutput
{
    float3 BaseColor;
    float  Metallic;
    float  Roughness;
    float3 NormalTS;
    float3 Emissive;
    float  Occlusion;
    float  Translucency;
};

// ---- 材质输入(由已添加的预设输入决定; 生成器注入) ----
//__MINPUT_DEF__

float3 OctEncodeNormal(float3 n)
{
    float l1 = abs(n.x) + abs(n.y) + abs(n.z);
    float2 p = n.xy / l1;
    bool back = (n.z <= 0.0);
    float2 f;
    f.x = (p.x >= 0.0) ? (1.0 - abs(p.y)) : -(1.0 - abs(p.y));
    f.y = (p.y >= 0.0) ? (1.0 - abs(p.x)) : -(1.0 - abs(p.x));
    float2 r = back ? f : p;
    return float3(r * 0.5 + 0.5, 0.0);
}

//__MATERIAL_MAIN__

PSOut main(PSIn i)
{
    // ---- 0. 载入 per-instance 材质参数(写入裸名全局) ----
    LoadMaterialParams(UserMaterialInstances[(uint)i.idx]);

    // ---- 1. 法线基(供 GBuffer 打包) ----
    float3 N = normalize(i.v1.xyz).xzy;
    float3 T = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    float3 B = cross(N, T);
    B = (i.v3.z < 0.0) ? -B : B;
    B = normalize(B);

    float2 ndc = i.svpos.xy * screenInverseSize * float2(2.0, -2.0) + float2(-1.0, 1.0);
    float2 vel = (i.v4.zw / i.v5.x) - ndc;

    // ---- 2. 材质逻辑(PBR 语义; 入参 = 动态生成的 MaterialInput) ----
    //__MINPUT_BUILD__
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 打包进 GBuffer ----
    float metallic = saturate(m.Metallic * 1.02 - 0.02);
    float translucency = m.Translucency;
    bool opaque = (metallic > 0.0) || (translucency <= 0.0);
    float o1w, darkFlag;
    if (opaque)
    {
        o1w = max(metallic, 0.04);
        darkFlag = 0.666667;
    }
    else
    {
        o1w = round(translucency * 15.49 + 0.5) * 0.0627451017 + 0.0313725509;
        darkFlag = 0.0;
    }

    float3 nt = normalize(m.NormalTS);
    float3 nrm = normalize(N * nt.z + T * nt.x + B * nt.y);
    float2 encN = OctEncodeNormal(nrm).xy;

    PSOut o;
    o.o0 = float4(m.Emissive, 0.0);
    o.o1 = float4(m.BaseColor, o1w);
    o.o2 = float4(encN, m.Roughness, gbufferTypeFlag * 0.333333343 + darkFlag);
    o.o3 = float4(m.Occlusion, vel, 1.0);

    // ---- 4. 曝光(固有输入; 材质不感知): 引擎会对 RT0 再乘 <白点*曝光> ⇒ 这里自动抵消 ----
    float _wp = asfloat(WhitePtSrv.Load(0));
    _wp = (useAutoExposure != 0) ? _wp : 1.0;
    _wp = _wp * exposureAdjustment;
    o.o0.rgb *= 1.0 / max(_wp, 0.0001);

    //__KEEPALIVE__
    return o;
}
