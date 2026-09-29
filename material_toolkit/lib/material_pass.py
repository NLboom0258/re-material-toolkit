"""材质 pass 模板组装 + 编译(M2-③)。

把 pass 模板(.hlsl, 含 ``//__MATERIAL_MAIN__`` 标记)与用户的 MaterialMain 拼成完整
HLSL, 再用 d3dcompiler 编译成 ps_5_0。模板负责输入解包 / 法线基 / GBuffer 打包。
"""
import os
import re

from . import mmtr_blobs as B

MARKER = "//__MATERIAL_MAIN__"
IFACE_BEGIN = "//__IFACE_BEGIN__"
IFACE_END = "//__IFACE_END__"
KEEPALIVE = "//__KEEPALIVE__"
# 预设(语义)输入动态生成的两块: 结构定义(文件域) + main 内构造代码
MINPUT_DEF = "//__MINPUT_DEF__"
MINPUT_BUILD = "//__MINPUT_BUILD__"
# 插值声明(真驱动): 由 passes.json 生成(主 pass 全量; 深度 uv0 条件化)。
INTERP_DECL = "//__INTERP_DECL__"
DEPTH_UV0_DI = "//__DEPTH_UV0_DI__"
DEPTH_UV0_BUILD = "//__DEPTH_UV0_BUILD__"
_TDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "pass_templates")

# ---- 寄存器模式 ----
# "explicit": 在 HLSL 里显式写 `register(bN/tN/sN)`; 因为 d3dcompiler 会剔除"整段未被引用"
#   的资源, 而引擎是按"RDEF 顺序连续"绑定的 ⇒ 必须**保活**防止寄存器出现空洞。
# "auto": **不写 register**, 由 d3dcompiler **自动紧凑分配**(寄存器号 ≡ RDEF 位置)
#   ⇒ 结构上不可能出现空洞(2026-09-30 实机验证: 5 例 AC_* 均正常)。
# 切 "auto" 时必须同时不注入保活(`build_source` 内部已处理)。
# 2026-09-30: 已切为 "auto"(实机验证: 5 例 AC_* 均正常); 保留 "explicit" 作应急口子。
REGISTER_MODE = "auto"
_REG_RE = re.compile(r"\s*:\s*register\([^)]*\)")


def _bare_registers(bare_registers):
    """是否用"自动紧凑"(不写 register)。None = 取模块默认 REGISTER_MODE。"""
    return (REGISTER_MODE == "auto") if bare_registers is None else bool(bare_registers)


def _read(fname):
    with open(os.path.join(_TDIR, fname), "r", encoding="utf-8") as f:
        return f.read()


def default_material(template="deferred_env"):
    """该 pass 模板配套的默认材质函数(如 env 的等价实现)。

    `<name>_instance` 变体与 `<name>` 共用默认材质(参数均已裸名暴露)。
    """
    suffix = template.split("_", 1)[1] if "_" in template else template
    fname = "mat_default_%s.hlsl" % suffix
    if not os.path.exists(os.path.join(_TDIR, fname)):
        base = suffix[:-len("_instance")] if suffix.endswith("_instance") else suffix
        fname = "mat_default_%s.hlsl" % base
    return _read(fname)


def material_line_offset(template="deferred_env"):
    """组装文里“材质源第一行”之前的行数(用于把编译报错行号映射回用户源码行)。"""
    tpl = _read(template + ".hlsl")
    return tpl[:tpl.index(MARKER)].count("\n")


def build_source(material_src=None, template="deferred_env", iface=None,
                 style="cbuffer", keepalive=None, minput=None, subs=None,
                 bare_registers=None):
    """组装完整 HLSL。material_src 为 None 时用该模板的默认材质函数。

    iface 给定(来自 material_iface)时, 用它**替换**模板里 //__IFACE_BEGIN__~END__ 之间的
    接口声明(否则用模板自带的写死声明, 便于单独编译/测试)。
    keepalive: 非 None 时替换模板里 //__KEEPALIVE__ 标记(自定义输入的“保活”语句,
               引用资源防被编译器剔除); 模板无该标记则忽略。
    minput: {"def":..., "build":...} 预设(语义)输入动态生成的 MaterialInput 定义/构造;
               None 时用**空**(struct 带占位成员) —— 保证模板标记总被替换。
    style: "cbuffer"(材质参数走 cbuffer) / "instance"(走结构化缓冲, per-instance)。
    subs: 额外标记替换 {标记: 文本}(如深度模板的 `//__DEPTH_UV0_*__`, 按需声明插值)。
    bare_registers: True = 去掉所有 `register(...)`(交给编译器自动紧凑) + 不注入保活。
    """
    _bare = _bare_registers(bare_registers)
    tpl = _read(template + ".hlsl")
    if subs:
        for k, v in subs.items():
            tpl = tpl.replace(k, v)
    # 插值声明(真驱动): 未显式提供时, 按 passes.json 生成该 pass 的声明(深度 uv0 条件化)。
    if INTERP_DECL in tpl:
        from . import material_inputs as _INP
        _pn = "depth" if "depth" in template else "main"
        tpl = tpl.replace(INTERP_DECL, _INP.interp_decls(_pn, material_src))
        _uv0 = (_INP.interp_has("depth", "INTERPOLATOR0", material_src)
                if _pn == "depth" else True)
        if DEPTH_UV0_DI in tpl:
            tpl = tpl.replace(DEPTH_UV0_DI, "float2 uv0;" if _uv0 else "")
        if DEPTH_UV0_BUILD in tpl:
            tpl = tpl.replace(DEPTH_UV0_BUILD, "di.uv0 = i.v1.xy;" if _uv0 else "")
    if MARKER not in tpl:
        raise ValueError("模板缺少标记 %s: %s.hlsl" % (MARKER, template))
    if iface is not None:
        from . import material_iface as MI
        i0 = tpl.index(IFACE_BEGIN) + len(IFACE_BEGIN)
        i1 = tpl.index(IFACE_END)
        tpl = tpl[:i0] + "\n" + MI.hlsl_of(iface, style=style) + tpl[i1:]
    if minput is None:
        from . import semantic_inputs as SI
        minput = SI.empty_texts()
    if MINPUT_DEF in tpl:
        tpl = tpl.replace(MINPUT_DEF, minput["def"])
    if MINPUT_BUILD in tpl:
        tpl = tpl.replace(MINPUT_BUILD, minput["build"])
    if not _bare and keepalive is not None and KEEPALIVE in tpl:
        tpl = tpl.replace(KEEPALIVE, keepalive)
    src = material_src if material_src is not None else default_material(template)
    out = tpl.replace(MARKER, src)
    # auto 模式: 剥掉所有 `register(...)` ⇒ 编译器自动紧凑(寄存器号 ≡ RDEF 位置, 无空洞)。
    return _REG_RE.sub("", out) if _bare else out


def compile_shading(material_src=None, template="deferred_env",
                    entry="main", target="ps_5_0", iface=None, style="cbuffer",
                    keepalive=None, minput=None, graft=False, subs=None,
                    bare_registers=None):
    """编译为 ps_5_0。返回 (dxbc_bytes, err_text)。

    注: **默认不再做"元数据嫁接"**(graft=False)。2026-09-27 已查明: DX12 下材质 pass 被
    静默跳过的根因是 **PS 的 ISGN 声明分量超出了 VS 的 OSGN 输出**(如把 `INTERPOLATOR4`
    声明成 `float4` 而 VS 只输出 `.x`); 模板改成标量后, 我们自编译的元数据本身就合法,
    **无需移植参考块**。graft=True 仅作为旧的兑底手段保留(lib/ps_meta)。
    """
    dxbc, err = B.compile_hlsl(build_source(material_src, template, iface, style,
                                            keepalive, minput, subs,
                                            bare_registers),
                               entry, target, name="%s.hlsl" % template)
    if err or not graft:
        return dxbc, err
    from . import ps_meta
    dxbc, _info = ps_meta.graft(dxbc)
    return dxbc, err
