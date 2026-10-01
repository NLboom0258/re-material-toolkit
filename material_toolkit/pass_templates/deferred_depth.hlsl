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
// 注: 插值声明由**依赖系统**按 passes.json 生成(见下方标记行; 深度 uv0 条件化:
//     用到了才声明 INTERPOLATOR0 ⇒ 默认最小空实现不带多余插值)。
struct PSIn
{
    float4 svpos : SV_Position;    // reg0  像素坐标
//__INTERP_DECL__
};

// ---- 深度输入(供 MaterialDepth 使用) ----
struct DepthInput
{
    float4 svpos;
    //__DEPTH_UV0_DI__
};

// ---- 材质函数(只允许 3 个函数: MaterialMain/MaterialDepth/MaterialVertex; 本 pass 用 MaterialDepth) ----
// 注: 不再声明 MaterialInput/MaterialOutput —— 材质源只含这 3 个函数 ⇒ 深度 PS 不会串入主 pass 的类型。
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
