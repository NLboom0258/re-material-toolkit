// ============================================================================
// RE mmtr pass 模板: Deferred —— **std instance 版(逐实例材质)** —— 系统部分, 勿改
// 用途: 技术名带 `…Instancing2` 的槽(=「逐实例材质」: 引擎绑 `UserMaterialInstances` 结构化缓冲,
//       VS 多输出一个 NOINTERPOLATOR0 实例索引)。
// 与 deferred_std 的差异: 材质参数走 `UserMaterialInstances` 结构化缓冲(按实例索引), 无 cb3;
//       纹理整体 +1; PSIn 多一个实例索引(reg6 = NOINTERPOLATOR0)。
// 接口块由 `material_iface.hlsl_of(style="instance")` 替换(调用方须传 iface)。
// ============================================================================

//__IFACE_BEGIN__
// (占位; nogen 传 iface 时会被 material_iface(style="instance") 替换)
cbuffer SceneInfo : register(b0) { float4x4 viewProjMat; };
//__IFACE_END__

// ---- 输入签名(= 模板 VS 的输出; 寄存器 0..6) ----
struct PSIn
{
    float4 svpos : SV_Position;   // reg0
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线.yz, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz
    float4 v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)
    nointerpolation float idx : NOINTERPOLATOR0; // reg6  材质实例索引
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;   // 原始 GBuffer RT0(Emissive)
    float4 o1 : SV_Target1;   // 原始 GBuffer RT1(BaseColor.rgb + Metallic/半透明.w)
    float4 o2 : SV_Target2;   // 原始 GBuffer RT2(法线八面体.xy + Roughness.z + Misc.w)
    float4 o3 : SV_Target3;   // 原始 GBuffer RT3(Occlusion.x + Velocity.yz + SubSurface.w)
};

// ---- 材质输入(供 MaterialMain 使用; 全部来自插值) ----
struct MaterialInput
{
    float2 uv0;
    float2 uv1;
    float3 Normal;
    float3 Tangent;
    float3 Bitangent;
    float3 positionWS;
};

// ---- 材质输出(直写原始 GBuffer) ----
struct MaterialOutput
{
    float4 RT0;
    float4 RT1;
    float4 RT2;
    float4 RT3;
};

// 曝光补偿常量(延迟 RT0 会被引擎再乘曝光+tonemap)
#define BARE_RT0_EXPOSURE 100.0

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

    // ---- 1. 输入解包 ----
    MaterialInput mi;
    mi.uv0 = float2(i.v1.w, i.v2.x);
    mi.uv1 = i.v2.yz;
    mi.Normal = normalize(i.v1.xyz).xzy;
    mi.Tangent = normalize(float3(i.v2.w, i.v3.y, i.v3.x));
    mi.Bitangent = normalize(cross(mi.Normal, mi.Tangent));
    mi.positionWS = float3(i.v3.w, i.v4.x, i.v4.y);

    // ---- 2. 材质逻辑 ----
    MaterialOutput m;
    MaterialMain(mi, m);

    // ---- 3. 写 4 张 GBuffer ----
    PSOut o;
    o.o0 = m.RT0;
    o.o1 = m.RT1;
    o.o2 = m.RT2;
    o.o3 = m.RT3;

    // ---- 4. 保活(死分支): 让标准/自定义接口资源留在 RDEF ----
    if (i.v1.w > 1e30) {
        //__KEEPALIVE__
    }
    return o;
}
