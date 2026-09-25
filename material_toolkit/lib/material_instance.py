#!/usr/bin/env python3
"""⑥ 从 master mmtr 生成“材质实例(mdf2)”。

- `from_mmtr`: 从一个 mmtr 造一个新的 mdf2(+1 材质): 参数=mmtr 的 `UserMaterial` 成员
  (默认值 0, 按 offset 对齐), 贴图槽=材质主 pass(Deferred)PS 的非引擎 SRV(占位路径)。
- `repoint`: 克隆一份现有 mdf2, 把指定材质的 master(mmtr 路径)改指到目标(可选同步参数)。

mmtr 是接口唯一权威, mdf2 只给值(见 lib/sync.py)。路径约定: mdf2 里 master 路径形如
`MasterMaterial/Master/<Name>.mmtr`(无 natives 前缀、无版本后缀)。
"""
try:
    from .mdf2 import Mdf2, shading_type_value
except ImportError:  # 直接运行
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mdf2 import Mdf2, shading_type_value


def material_texture_slots(mmtr_bytes, pass_name="Deferred"):
    """材质主 pass(Deferred)的非引擎纹理槽名(排除 byte/raw 的引擎 SRV, 如 WhitePt)。"""
    from . import mmtr_blobs as B
    from . import material_gen as G
    from . import material_iface as MI
    idx = G.pick_iface_ps(mmtr_bytes, pass_name)
    if idx is None:
        return []
    iface = MI.iface_from_dxbc(B.extract_blob(mmtr_bytes, idx))
    return [t["name"] for t in iface["textures"] if t["fmt"] != "byte"]


def from_mmtr(mmtr_bytes, mmtr_path, material_name, shading_type="Standard",
              game_version=10, cbuffer="UserMaterial",
              texture_placeholder="Null.tex"):
    """由一个 mmtr 造新 mdf2(单个材质)。返回 Mdf2。"""
    from .mmtr import Mmtr
    from .sync import sync_mdf2

    m = Mdf2.new(game_version=game_version)
    st = shading_type_value(shading_type)
    if st is None:
        raise ValueError("unknown shading type %r" % shading_type)
    mat = m.add_material(material_name, mmtr_path, shader_type=st, flags=0)
    sync_mdf2(m, Mmtr.from_bytes(mmtr_bytes), cbuffer)  # 参数: 来自 mmtr UserMaterial
    for name in material_texture_slots(mmtr_bytes):
        mat.add_texture(name, texture_placeholder)
    return m


def repoint(mdf2, material_name, mmtr_path, mmtr_bytes=None, cbuffer="UserMaterial",
            sync=True):
    """把 mdf2 里某材质的 master 改指到 mmtr_path; 可选按 mmtr 同步参数。返回 mdf2。"""
    mat = mdf2.get_material(material_name)
    if mat is None:
        raise ValueError("material %r not found" % material_name)
    mat.mmtr_path = mmtr_path
    if sync and mmtr_bytes is not None:
        from .mmtr import Mmtr
        from .sync import sync_mdf2
        sync_mdf2(mdf2, Mmtr.from_bytes(mmtr_bytes), cbuffer)
    return mdf2
