// ============================================================================
// RE mmtr pass 模板: Deferred —— **零声明最小版(自建用)** —— 系统部分, 勿改
// 定位: "自建 mmtr"用。**只用插值输入, 不声明任何 cbuffer/贴图/sampler** ⇒
//      记录绑定组 = VS 侧资源(不含 UserMaterial / 材质贴图) ⇒ **任何 mdf2 指过来都能跑,
//      不必换 mdf2**(材质声明的输入只有我们真正用到的)。
// 依据: character_default 原版 Deferred PS 的输入/输出签名(4 张 GBuffer)。
// 输出: o0=RT0(Emissive, 旁路光照, 用作测试色) / o1=RT1(BaseColor) /
//       o2=RT2(法线八面体.xy + Roughness.z + Misc.w) / o3=RT3(Occlusion/…).
// 组装方式: 本文件 + 用户的 MaterialMain(插入到下方标记行处)。
// ============================================================================

//__IFACE_BEGIN__
// (零声明: 无 cbuffer / 纹理 / sampler —— 只用插值输入)
//__IFACE_END__

// ---- 输入签名(寄存器 0..5; = Deferred VS 的输出) ----
struct PSIn
{
    float4 svpos : SV_Position;   // reg0  屏幕像素坐标
    float4 v1    : INTERPOLATOR0; // reg1  xyz=法线, w=UV0.x
    float4 v2    : INTERPOLATOR1; // reg2  x=UV0.y, yz=UV1, w=切线.x
    float4 v3    : INTERPOLATOR2; // reg3  xy=切线.yz, z=bitangent 符号, w=世界坐标.x
    float4 v4    : INTERPOLATOR3; // reg4  xy=世界坐标.yz
    float  v5    : INTERPOLATOR4; // reg5  x=速度项(上一帧 w)(VS 仅输出 x ⇒ 声明标量)
};

// ---- 输出签名(4 张 GBuffer) ----
struct PSOut
{
    float4 o0 : SV_Target0;   // 原始 GBuffer RT0(Emissive)
    float4 o1 : SV_Target1;   // 原始 GBuffer RT1(BaseColor.rgb + Metallic/半透明.w)
    float4 o2 : SV_Target2;   // 原始 GBuffer RT2(法线八面体.xy + Roughness.z + Misc.w)
    float4 o3 : SV_Target3;   // 原始 GBuffer RT3(Occlusion.x + Velocity.yz + SubSurface.w)
};

// ---- 材质输入(供 MaterialMain 使用; 全部来自插值, 无需 cbuffer) ----
struct MaterialInput
{
    float2 uv0;            // 主 UV
    float2 uv1;            // 副 UV
    float3 Normal;         // ⚠ GBuffer/插值约定(已 .xzy); 光照请用 NormalWS
    float3 NormalWS;       // 世界(光照)空间法线(= Normal.xzy) —— 与光方向同空间, 做 N·L 用它
    float3 Tangent;        // ⚠ 同 Normal 的约定(已 .xzy); 世界空间计算请 .xzy
    float3 Bitangent;      // ⚠ 同 Normal 的约定(已 .xzy); 世界空间计算请 .xzy
    float3 positionWS;     // 世界坐标
};

// ---- 材质输出(直写原始 GBuffer; 由 MaterialMain 赋值) ----
struct MaterialOutput
{
    float4 RT0;   // 自发光(旁路光照; 测试色写这里最稳)
    float4 RT1;   // BaseColor.rgb + Metallic(.w)
    float4 RT2;   // 法线八面体(.xy) + Roughness(.z) + Misc(.w)
    float4 RT3;   // Occlusion(.x) + Velocity(.yz) + SubSurface(.w)
};

// 曝光补偿常量: 延迟 RT0(自发光) 输出后会被引擎再乘"曝光"+tonemap, 值太小会发黑。
// 本模板零声明(不读 Tonemap/WhitePtSrv) ⇒ 用常量近似; 实测 ×100 时红/法线可见(见 PROJECT_SUMMARY)。
// 若画面过曝/偏暗, 调这个值即可(或改用 deferred_custom 的 exposureScale 精确版)。
#define BARE_RT0_EXPOSURE 100.0

// 法线八面体编码(等价原版; 供材质调用)
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
    // ---- 1. 输入解包(全部来自插值) ----
    MaterialInput mi;
    mi.uv0 = float2(i.v1.w, i.v2.x);
    mi.uv1 = i.v2.yz;
    mi.Normal = normalize(i.v1.xyz).xzy;
    mi.NormalWS = mi.Normal.xzy;   // 世界(光照)空间; 做 N·L 用这个
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
    return o;
}
