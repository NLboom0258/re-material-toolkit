"""贴图来源：原 mdf2 自带（所有材质）+ 工具导入管理（materialTemp/tex）。

不依赖 pak 文件表 / 外部 list：mdf2 自己就记着贴图路径。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from material_toolkit.lib import mat_attrs, mdf2 as mdf2lib, psession as ps


def _mkroot():
    root = os.path.join(tempfile.gettempdir(), "rt_tex_sources")
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(os.path.join(root, "natives", "x64", "materialTemp", "orig"))
    return root


def main():
    root = _mkroot()
    mh, dh = "mh1", "dh1"

    # 假 orig mdf2：两个材质、4 条绑定（其中一条跨材质重复）
    doc = mdf2lib.Mdf2.new(10)
    a = doc.add_material("A", "character/a.mmtr")
    a.add_texture("BaseColorMap", "texture/Shared.tex")
    a.add_texture("NormalMap", "texture/a_nrm.tex")
    b = doc.add_material("B", "character/b.mmtr")
    b.add_texture("ATOSMap", "texture/a_nrm.tex")
    b.add_texture("MaskMap", "texture/B_mask.tex")
    p = os.path.join(root, "natives", "x64", "materialTemp", "orig",
                     dh + "_p20261101_000000.mdf2.10")
    doc.save(p)

    idx = ps.load_index(root)
    ps.upsert_object(idx, mh, "character/x.mesh", "x", 1)
    ps.upsert_mdf2(idx, mh, dh, "character/x.mdf2", "character/x.mdf2",
                   orig_rel="materialTemp/orig/%s_p20261101_000000.mdf2" % dh)
    ps.save_index(root, idx)

    got = mat_attrs.original_textures(root, mh, dh)
    want = ["texture/a_nrm.tex", "texture/B_mask.tex", "texture/Shared.tex"]
    print("orig:", got)
    assert got == want, (got, want)

    # 工具导入管理的贴图（带 .tex.<ver> 版本后缀）
    d = os.path.join(root, "natives", "x64", "materialTemp", "tex", mh, dh)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "MyTex_deadbeef.tex.11"), "wb").write(b"TEX0")
    open(os.path.join(d, "Other_cafebabe.tex"), "wb").write(b"TEX0")
    got2 = ps.list_managed_textures(root)
    want2 = ["materialTemp/tex/%s/%s/MyTex_deadbeef.tex" % (mh, dh),
             "materialTemp/tex/%s/%s/Other_cafebabe.tex" % (mh, dh)]
    print("managed:", got2)
    assert got2 == want2, (got2, want2)

    shutil.rmtree(root)
    print("test_tex_sources: OK")


if __name__ == "__main__":
    main()
