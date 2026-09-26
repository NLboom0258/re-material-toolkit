// ---- 默认材质函数(前向/custom) —— 可被用户替换 ----
// 前向: MaterialMain 直接决定输出色(o0 = m.Color); mi 提供 uv/法线基/世界坐标/相机基。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    m.Color = float3(0.5, 0.5, 0.5);
}
