# D3D_Shaders — 3Dmigoto 汇编器独立副本

> 来源:从 3Dmigoto 仓库 `D3D_Shaders` 项目独立复制,用于 mmtr shader 离线 asm 编辑。
> **含 asm2cbo 单步模式改动**(后述),与原生 3Dmigoto 的批量流程不同。

## 用途

`tools/mmtr_asm_edit.py`(封装工具)调用本项目的 exe 做:
- `bin->asm`:把 DXBC blob 反汇编成标准 asm 文本(disassembler)
- `asm2cbo`:把改好 asm 汇编回 DXBC blob(单步,只汇编不反汇编)

## 与原生 3Dmigoto 的差异:asm2cbo

原生 3Dmigoto 的 `Shaders.cpp` 只有**批量二合一**(`bin->asm` 然后 `asm->cbo`),
一次运行先反汇编再汇编。这带来一个问题:**它总是先用原始 bin 反汇编,覆盖
ShaderCache 里的 asm 文本**——如果我放好"用户改后 asm"再跑,会被冲掉,汇编出来等于没改。

因此我们给 `Shaders.cpp` 加了 `asm2cbo` 单步模式(见 `Shaders.cpp` 顶部):
**只做 asm->cbo,跳过 bin->asm**,让"用户改完 asm 直接汇编"成为可能。
`asm2cbo <workdir>` 会枚举 `<workdir>\ShaderCache\*-??.txt` + 同名 `.bin`(参考),汇编出 `.cbo`。

## 编译

需 VS 2022 的 MSBuild(配合 Windows SDK,提供 `d3dcompiler.lib`):

```
cd reference/D3D_Shaders
C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe D3D_Shaders.vcxproj /t:Rebuild /p:Configuration=Release /p:Platform=x64 /v:minimal /m
```

产物:`reference/D3D_Shaders/bin/x64/Release/D3D_Shaders.exe`(已 gitignore,不入库)。

> 注意:这个副本的 vcxproj 已把 `IncludePath` 从 `$(SolutionDir);...BinaryDecompiler`
> 改为 `$(ProjectDir)`(因 `log.h`/`shader.h` 已随副本带入,不再依赖 3Dmigoto 根目录/BinaryDecompiler)。

## 依赖

本项目自带 `log.h`、`shader.h`(来自 3Dmigoto 根目录,内容只依赖标准库)。
系统需安装 Windows SDK(d3dcompiler.lib、d3dx9shader.h)。
