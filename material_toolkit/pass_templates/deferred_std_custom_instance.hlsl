// ============================================================================
// RE mmtr pass 模板: Deferred —— **std · 自定义光照 · 逐实例(直控 GBuffer)** —— 系统部分, 勿改
// 用途: 技术名带 `…Instancing2` 且自定义光照的槽(逐实例材质: 引擎绑 `UserMaterialInstances`,
//       VS 多输出一个 NOINTERPOLATOR0 实例索引)。
// 与 deferred_std_custom 的差异: 材质参数走 `UserMaterialInstances`(按实例索引), 无 cb3;
//       纹理整体 +1; PSIn 多一个实例索引(reg6 = NOINTERPOLATOR0)。
// 接口块由 `material_iface.hlsl_of(style="instance")` 替换(调用方须传 iface)。
// ============================================================================

//__IFACE_BEGIN__
// (占位; 该 pass 的引擎资源依赖由**依赖系统**注入 —— 见 presets/<ver>/passes.json)
//__IFACE_END__

// ---- 输入签名(寄存器 0..6) ----
// 插值声明由**依赖系统**按 passes.json 生成(见下方标记行)。
struct PSIn
{
    float4 svpos : SV_Position;   // reg0
//__INTERP_DECL__
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

// ---- 材质输出(直写原始 GBuffer; 由 MaterialMain 赋值) ----
struct MaterialOutput
{
    float4 RT0;
    float4 RT1;
    float4 RT2;
    float4 RT3;
};

// ---- 材质输入(由已添加的预设输入决定; 生成器注入) ----
//__MINPUT_DEF__

// 法线八面体编码(等价原版; 供材质直写 RT2 时调用)
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

    // ---- 1. 材质输入(由已添加的预设输入构造; 生成器注入) ----
    //__MINPUT_BUILD__

    // ---- 2. 材质逻辑(直写 4 个 GBuffer RT) ----
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 输出 ----
    PSOut o;
    o.o0 = m.RT0;
    o.o1 = m.RT1;
    o.o2 = m.RT2;
    o.o3 = m.RT3;

    //__KEEPALIVE__
    return o;
}
