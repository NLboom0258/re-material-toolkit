# 第三方组件与来源声明 (THIRD_PARTY)

本工具(以下称"本项目")包含或依赖以下第三方/派生内容。分发与使用前请阅读本文件与 `LICENSE`。

## 1. D3D_Shaders(3Dmigoto)—— GPL-3.0,含修改

- 来源:3Dmigoto 项目(https://github.com/bo3b/3Dmigoto)的 `D3D_Shaders`(着色器反汇编/汇编器)子项目。
- 许可:**GNU GPL-3.0**(见 `LICENSE`)。
- **本项目对其进行了修改**(例如新增 `asm2cbo` 逐步汇编模式,以适配 RE Engine mmtr 工作流)。
- **源码位置**:本仓库 `third_party/D3D_Shaders/`(依 GPL-3.0,随二进制提供对应**修改后源码**)。
  **发布包为精简运行时,不含该源码**;需要时请到本仓库获取。
- **发布包**内含其编译产物 `material_toolkit/bin/D3D_Shaders.exe`。
- 本项目整体亦以 **GPL-3.0** 分发(见 `LICENSE`)。

## 2. d3dcompiler_47.dll —— Microsoft DirectX 可再分发组件

- 位置:`material_toolkit/bin/d3dcompiler_47.dll`(随仓库与发布包)。
- 来源:Microsoft DirectX SDK / Windows SDK 的 HLSL 编译器运行时(D3DCompile / D3DReflect / D3DDisassemble)。
- 性质:微软**可再分发**运行时组件,**无对应源码**;以微软的再分发条款使用。
- 用途:本项目用它将 HLSL 编译为 DXBC(ps_5_0/vs_5_0) 并做反射/校验。

## 3. hlsl_blend_dxbc_translator(可选功能)

- 本项目支持一个**可选**功能:把"DXBC asm + HLSL 标记"的混合文本翻回纯 asm。
- 该翻译器为**独立开源项目**:https://github.com/NLboom0258/hlsl-blend-dxbc-translator
- **发布包已内置**其编译产物 `material_toolkit/bin/hlsl_blend_dxbc_translator.exe`。
- **仓库不含其 exe**;从仓库运行如需该功能,请按上述仓库自行构建并放入 `material_toolkit/bin/`。
  可用环境变量 `HLSL_BLEND_TRANSLATOR_EXE` 指定。
- 缺失该 exe 时,其余功能不受影响(纯 asm 照常)。
- 许可:见其自身仓库。

## 4. `presets/v01100004/*.bin` —— 游戏派生数据(版本预设)

- 位置:`material_toolkit/presets/v01100004/`(骨架 / 标准银行 / 描述符 / 程序表等二进制)。
- 性质:为从 **Resident Evil Engine(如 Devil May Cry 5)** 的材质数据中**统计/提取**得到的
  **版本级预设**,用于生成 mmtr。它们**不是**游戏原始资产文件,但**派生自游戏数据**。
- 说明:随包提供是为保证工具"开箱可用";此数据可按结构重新统计生成,属可替换项。

## 5. 免责声明

- 本项目与 Capcom / RE Engine 官方**无任何关联**,非官方、非授权工具。
- 本项目**不包含**任何游戏原始资产(游戏内的 `.mmtr` / `.mdf2` 等)。
- 用户须自行拥有合法授权的游戏,并自行承担使用本工具(mod 与分发)的一切后果。
