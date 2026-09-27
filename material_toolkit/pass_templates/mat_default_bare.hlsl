// deferred_bare 的缺省材质: **零声明**(不读任何材质参数/贴图)。
// 用途: per-instance(Instancing)输入的槽 —— 实例化绘制绑定的是 UserMaterialInstances(结构化
//       缓冲), 与 cbuffer 风格的主 PS 不自洽; 换成零声明即可"不读任何材质资源"(先保证自洽/可加载,
//       之后可拓展为完整 instance 风格 PS 以显示真实材质)。
void MaterialMain(in MaterialInput mi, out MaterialOutput m)
{
    m.RT0 = float4(0.0, 0.0, 0.0, 0.0);                                  // 自发光 = 0
    m.RT1 = float4(0.5, 0.5, 0.5, 0.0);                                  // 中性灰基础色
    m.RT2 = float4(OctEncodeNormal(mi.Normal).xy, 0.5, 0.666667);        // 法线
    m.RT3 = float4(1.0, 0.0, 0.0, 0.0);
}
