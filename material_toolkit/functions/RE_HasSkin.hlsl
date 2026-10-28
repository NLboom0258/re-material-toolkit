// 判断变体是否具有骨骼矩阵(SkinningMatrices 是否已绑定)
// variantKind: 0=pre 1=world 2=worldinst 3=inst 4=indirect 5=skin 6=skin_indirect
//
// 只有 5/6 才有骨骼矩阵: inst/indirect 走 `mul(InstanceWorldInfo.worldMat, p4)`,
// 它们的 RDEF 里**不含** SkinningMatrices(实测银行 48+44 个变体全无)。若把它们判成
// "有骨骼", 骨骼分支就不会被常量折叠掉, 该变体 RDEF 会凭空多一个 SkinningMatrices
// 绑定 —— 引擎若不为该 draw 绑定骨骼缓冲, 就是未定义行为(表现为不渲染/闪烁)。
bool RE_HasSkin(uint variantKind)
{
    return variantKind >= 5u;
}
