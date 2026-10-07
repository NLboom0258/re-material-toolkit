// alpha 抖动剔除(RE 深度族"屏幕空间 screen-door")—— 延迟材质做半透明 / 近距渐隐用。
// 与 character_default / env_emissive 深度族 PS 的"分支B(DissolveOffset<=0)"逐条一致。
// 返回 true = 剔除该像素。
// 用法: discardPixel = AlphaDitherDiscard(di.svpos, Opacity, isOddFrame, cbr_using, constant32Bits);
//   svpos      像素坐标(SV_Position.xy, 即 DepthInput.svpos)
//   opacity    目标不透明度(覆盖率; >=1 完全不剔, 越小越透; 常取 min(自己的X, VAR_AlphaValue) 让引擎近距渐隐仍可接管)
//   isOddFrame 引擎: EnvironmentInfo.isOddFrame   (帧奇偶, 逐帧翻转相位)
//   cbrUsing   引擎: CheckerBoardInfo.cbr_using   (棋盘渲染 1/0)
//   c32        引擎: RootConstant.constant32Bits  (每 draw 随机相位)
// 说明: 抖动在**屏幕空间** -> 会同时作用于阴影/深度预通道(配合 mdf2 的 BaseAlphaTestEnable);
//       判定只看符号, 故再乘 alpha 图不改变结果(alpha 图只起二值镂空作用)。
bool AlphaDitherDiscard(float2 svpos, float opacity, uint isOddFrame, float cbrUsing, uint c32)
{
    float par = (float)(((int)(svpos.y + (float)isOddFrame)) & 1);          // 行奇偶(逐帧翻转)
    float q   = (float)(uint)(svpos.x * (cbrUsing + 1.0) + par * cbrUsing)
              + 2.0 * (float)(uint)svpos.y;                                 // ★ 每行 +2 -> 斜向点阵
    float d   = min(frac(((float)c32 + q) * 0.2) * 1.25, 1.0);              // 抖动阈值
    float cov = (opacity >= 1.0) ? 1.0 : (opacity - d);                     // 覆盖率
    return (cov <= 0.0);
}
