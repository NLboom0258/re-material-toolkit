// 区间重映射 + 饱和(通用工具; 纯标量)
float Remap(float v, float inMin, float inMax, float outMin, float outMax)
{
    float t = saturate((v - inMin) / max(1e-6, inMax - inMin));
    return outMin + t * (outMax - outMin);
}
