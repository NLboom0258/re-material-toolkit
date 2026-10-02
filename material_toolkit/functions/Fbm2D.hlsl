// 2D 分形噪声(fbm, 5 层) -> [0,1](依赖 ValueNoise2D)
float Fbm2D(float2 p)
{
    float v = 0.0;
    float a = 0.5;
    for (int k = 0; k < 5; k++)
    {
        v += a * ValueNoise2D(p);
        p = p * 2.02 + float2(17.13, 9.71);
        a = a * 0.5;
    }
    return v;
}
