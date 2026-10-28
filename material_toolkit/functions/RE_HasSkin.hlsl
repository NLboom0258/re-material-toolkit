// 判断变体是否具有骨骼矩阵
// variantKind: 0=pre 1=world 2=worldinst 3=inst 4=indirect 5=skin 6=skin_indirect；>=3 才有骨骼矩阵
bool RE_HasSkin(uint variantKind)
{
    return variantKind >= 3u;
}
