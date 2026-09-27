"""材质 pass 模板组装 + 编译(M2-③)。

把 pass 模板(.hlsl, 含 ``//__MATERIAL_MAIN__`` 标记)与用户的 MaterialMain 拼成完整
HLSL, 再用 d3dcompiler 编译成 ps_5_0。模板负责输入解包 / 法线基 / GBuffer 打包。
"""
import os

from . import mmtr_blobs as B

MARKER = "//__MATERIAL_MAIN__"
IFACE_BEGIN = "//__IFACE_BEGIN__"
IFACE_END = "//__IFACE_END__"
KEEPALIVE = "//__KEEPALIVE__"
_TDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "pass_templates")


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
                 style="cbuffer", keepalive=None):
    """组装完整 HLSL。material_src 为 None 时用该模板的默认材质函数。

    iface 给定(来自 material_iface)时, 用它**替换**模板里 //__IFACE_BEGIN__~END__ 之间的
    接口声明(否则用模板自带的写死声明, 便于单独编译/测试)。
    keepalive: 非 None 时替换模板里 //__KEEPALIVE__ 标记(自定义输入的“保活”语句,
               引用资源防被编译器剔除); 模板无该标记则忽略。
    style: "cbuffer"(材质参数走 cbuffer) / "instance"(走结构化缓冲, per-instance)。
    """
    tpl = _read(template + ".hlsl")
    if MARKER not in tpl:
        raise ValueError("模板缺少标记 %s: %s.hlsl" % (MARKER, template))
    if iface is not None:
        from . import material_iface as MI
        i0 = tpl.index(IFACE_BEGIN) + len(IFACE_BEGIN)
        i1 = tpl.index(IFACE_END)
        tpl = tpl[:i0] + "\n" + MI.hlsl_of(iface, style=style) + tpl[i1:]
    if keepalive is not None and KEEPALIVE in tpl:
        tpl = tpl.replace(KEEPALIVE, keepalive)
    src = material_src if material_src is not None else default_material(template)
    return tpl.replace(MARKER, src)


def compile_shading(material_src=None, template="deferred_env",
                    entry="main", target="ps_5_0", iface=None, style="cbuffer",
                    keepalive=None):
    """编译为 ps_5_0。返回 (dxbc_bytes, err_text)。"""
    return B.compile_hlsl(build_source(material_src, template, iface, style, keepalive),
                          entry, target, name="%s.hlsl" % template)
