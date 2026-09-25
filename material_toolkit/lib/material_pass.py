"""材质 pass 模板组装 + 编译(M2-③)。

把 pass 模板(.hlsl, 含 ``//__MATERIAL_MAIN__`` 标记)与用户的 MaterialMain 拼成完整
HLSL, 再用 d3dcompiler 编译成 ps_5_0。模板负责输入解包 / 法线基 / GBuffer 打包。
"""
import os

from . import mmtr_blobs as B

MARKER = "//__MATERIAL_MAIN__"
_TDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "pass_templates")


def _read(fname):
    with open(os.path.join(_TDIR, fname), "r", encoding="utf-8") as f:
        return f.read()


def default_material(template="deferred_env"):
    """该 pass 模板配套的默认材质函数(如 env 的等价实现)。"""
    suffix = template.split("_", 1)[1] if "_" in template else template
    return _read("mat_default_%s.hlsl" % suffix)


def build_source(material_src=None, template="deferred_env"):
    """组装完整 HLSL。material_src 为 None 时用该模板的默认材质函数。"""
    tpl = _read(template + ".hlsl")
    if MARKER not in tpl:
        raise ValueError("模板缺少标记 %s: %s.hlsl" % (MARKER, template))
    src = material_src if material_src is not None else default_material(template)
    return tpl.replace(MARKER, src)


def compile_shading(material_src=None, template="deferred_env",
                    entry="main", target="ps_5_0"):
    """编译为 ps_5_0。返回 (dxbc_bytes, err_text)。"""
    return B.compile_hlsl(build_source(material_src, template), entry, target,
                          name="%s.hlsl" % template)
