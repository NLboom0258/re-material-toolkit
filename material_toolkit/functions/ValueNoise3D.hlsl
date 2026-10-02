// 3D 值噪声 -> [0,1](依赖 Hash31)
float ValueNoise3D(float3 p)
{
    float3 i = floor(p);
    float3 f = frac(p);
    float3 u = f * f * (3.0 - 2.0 * f);
    float n000 = Hash31(i + float3(0.0, 0.0, 0.0));
    float n100 = Hash31(i + float3(1.0, 0.0, 0.0));
    float n010 = Hash31(i + float3(0.0, 1.0, 0.0));
    float n110 = Hash31(i + float3(1.0, 1.0, 0.0));
    float n001 = Hash31(i + float3(0.0, 0.0, 1.0));
    float n101 = Hash31(i + float3(1.0, 0.0, 1.0));
    float n011 = Hash31(i + float3(0.0, 1.0, 1.0));
    float n111 = Hash31(i + float3(1.0, 1.0, 1.0));
    float x00 = lerp(n000, n100, u.x);
    float x10 = lerp(n010, n110, u.x);
    float x01 = lerp(n001, n101, u.x);
    float x11 = lerp(n011, n111, u.x);
    return lerp(lerp(x00, x10, u.y), lerp(x01, x11, u.y), u.z);
}
