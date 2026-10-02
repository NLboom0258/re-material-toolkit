// 3D 分形噪声(fbm, 4 层) -> [0,1](依赖 ValueNoise3D)
float Fbm3D(float3 p)
{
    float v = 0.0;
    float a = 0.5;
    for (int k = 0; k < 4; k++)
    {
        v += a * ValueNoise3D(p);
        p = p * 2.03 + float3(11.7, 5.3, 19.1);
        a = a * 0.5;
    }
    return v;
}
