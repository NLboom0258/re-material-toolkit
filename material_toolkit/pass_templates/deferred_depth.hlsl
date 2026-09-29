// ============================================================================
// RE mmtr pass 模板: Deferred —— **深度/阴影态(cutout)** —— 系统部分, 勿改
// 定位: 深度族 PS(无颜色输出, 只 discard)。材质可编辑 `MaterialDepth` 钩子实现
//       alpha-cutout / 抖动溶解(近距防穿模)等效果; 缺省时**不走本模板**(生成器直接用
//       "极简深度 PS") ⇒ 最小空材质保持一致。
// 材质输入: `DepthInput di`(由深度族 VS 输出派生: 像素坐标 + UV0)。
// 组装方式: 本文件 + 用户的 MaterialDepth(可选; 与本模板的 main 一起编译)。
// ============================================================================

//__IFACE_BEGIN__
//__IFACE_END__

// ---- 深度族输入(寄存器 0..2; 由深度族 VS 输出派生) ----
// 注: `//__DEPTH_UV0_IN__` 等标记由生成器按"深度源是否用到 uv0"替换: 用到才声明
//     INTERPOLATOR0(否则默认最小空实现在 ISGN 里不带多余插值)。
struct PSIn
{
    float4 svpos : SV_Position;    // reg0  像素坐标
    //__DEPTH_UV0_IN__
};

// ---- 深度输入(供 MaterialDepth 使用) ----
struct DepthInput
{
    float4 svpos;
    //__DEPTH_UV0_DI__
};

// ---- 材质主 pass 输入/输出(材质源可能同时含 MaterialMain; 缺省不使用) ----
//__MINPUT_DEF__
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

// ---- 材质函数(必须提供 MaterialDepth; 可选 MaterialMain) ----
//__MATERIAL_MAIN__

void main(PSIn i)
{
    DepthInput di;
    di.svpos = i.svpos;
    //__DEPTH_UV0_BUILD__
    bool discardPixel = false;
    MaterialDepth(di, discardPixel);
    //__KEEPALIVE__
    if (discardPixel)
        discard;
}
