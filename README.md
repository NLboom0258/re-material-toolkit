# re-material-toolkit

**RE Engine 材质工具**(mmtr / mdf2 的检视、编辑与**凭空生成**)。

- **Material Studio(GUI,主入口)** —— 检视/编辑 mmtr、mdf2;以"材质源(HLSL)"为作者层,
  编译并**生成**可用的 mmtr;支持顶点效果、深度族(近距渐隐)、自定义函数库(含噪声)等。
- **material_toolkit(库 + CLI)** —— 底层可编程接口;GUI 在其之上构建。适合脚本/批处理。

> 目标引擎:Capcom RE Engine(如 Devil May Cry 5)。本工具**不修改游戏**,只对材质资产文件
> (`mod` 侧)做读写;运行时生效由游戏加载 mod 时进行。

## 快速开始

### 方式 A:用发布包(推荐,开箱即用)

1. 到 **Releases** 下载 `re-material-toolkit-<版本>.zip`,解压。
2. 安装 Python 依赖:`pip install -r requirements.txt`(需要 Python 3.10+)。
3. 双击 `run.bat`(或 `python run.py`)启动 GUI。
   - 发布包已内置 `material_toolkit/bin/` 下的 `D3D_Shaders.exe` 与 `d3dcompiler_47.dll`。

### 方式 B:从源码运行(仓库)

1. `pip install -r requirements.txt`
2. **准备 `D3D_Shaders.exe`**(汇编器):本仓库只含其**源码**(`third_party/D3D_Shaders/`),
   需自行编译;或直接取发布包里的 exe。编译后用 MSBuild 产出 `Release|x64` 的 `D3D_Shaders.exe`,
   放到 `material_toolkit/bin/`(或用环境变量 `D3D_SHADERS_EXE` 指定路径)。
   - `d3dcompiler_47.dll` 已随仓库(`material_toolkit/bin/`),无需另行准备。
3. `python run.py` 启动 GUI。

> 只 clone 仓库、不编译 exe 时:除"汇编/反汇编(D3D_Shaders)"外的多数功能(结构检视、
> HLSL 编译/校验、生成)仍可用;需要汇编功能时再补 exe。

## 依赖

- **Windows**(依赖 `D3DCompile`/`D3DReflect` 与 D3D_Shaders)。
- Python **3.10+**;`PySide6`(见 `requirements.txt`)。
- `d3dcompiler_47.dll`(随仓)、`D3D_Shaders.exe`(发布包内置 / 自行编译)。

## 可选的"混合翻译器"

工具支持一个可选功能:把"DXBC asm + HLSL 标记"的混合文本翻回纯 asm。
该 exe **不随本项目**,见 [hlsl-blend-dxbc-translator](https://github.com/NLboom0258/hlsl-blend-dxbc-translator);
构建后放入 `material_toolkit/bin/` 即可。**缺失不影响其余功能。**

## CLI(高级)

```
python material_toolkit/material_toolkit.py <命令> ...
python material_toolkit/material_toolkit.py            # 列出全部命令
```

## 目录结构

```
run.py / run.bat          GUI 启动器(主入口)
material_toolkit/         底层库 + CLI
  lib/                    核心模块(mmtr/mdf2/生成/预设/自定义函数…)
  presets/v01100004/      版本预设(骨架/标准银行/描述符等)
  functions/              内置自定义函数库(噪声等;每函数一文件)
  pass_templates/         pass HLSL 模板
  bin/                    d3dcompiler_47.dll(随仓); D3D_Shaders.exe(发布包/自编)
material_studio/          GUI(Material Studio)
third_party/D3D_Shaders/  改版 3Dmigoto 汇编器源码(GPL-3.0)
```

## 许可与声明

- 本项目以 **GNU GPL-3.0** 分发(见 `LICENSE`),原因:包含 **3Dmigoto `D3D_Shaders` 的修改副本**。
- 第三方组件与来源见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。
- 本项目**非官方**、与 Capcom 无关联;**不含**游戏原始资产。
