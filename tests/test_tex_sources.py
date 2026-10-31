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

    # 独立管理区（mesh + 原 mdf2）里的导入贴图；同名只保留最高 rev
    d = os.path.join(root, "natives", "x64", "materialTemp", "tex", mh, dh)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "Old_deadbeef.tex.11"), "wb").write(b"TEX0")
    got2 = ps.list_managed_textures(root, mh, dh)
    want2 = ["materialTemp/tex/%s/%s/Old_deadbeef.tex" % (mh, dh)]
    print("managed:", got2)
    assert got2 == want2, (got2, want2)

    # import_texture：<name>_v<rev>.tex，保留 .tex.<数字> 版本后缀
    src1 = os.path.join(root, "in1.tex.11")
    open(src1, "wb").write(b"TEX0-abc")
    rel1 = ps.import_texture(root, src1, mh, dh, "MyTex")
    assert rel1.endswith("/MyTex_v1.tex"), rel1
    dst1 = os.path.join(root, "natives", "x64", rel1.replace("/", os.sep))
    assert os.path.exists(dst1 + ".11"), dst1 + ".11"

    # 同名再导入 → v2，列表只给最新版
    open(src1, "wb").write(b"TEX0-abc2")
    rel2 = ps.import_texture(root, src1, mh, dh, "MyTex")
    assert rel2.endswith("/MyTex_v2.tex"), rel2
    got3 = ps.list_managed_textures(root, mh, dh)
    assert "materialTemp/tex/%s/%s/MyTex_v2.tex" % (mh, dh) in got3, got3
    assert "materialTemp/tex/%s/%s/MyTex_v1.tex" % (mh, dh) not in got3, got3

    # 其他管理区互不可见
    os.makedirs(os.path.join(root, "natives", "x64", "materialTemp", "tex",
                             "mh2", "dh2"), exist_ok=True)
    open(os.path.join(root, "natives", "x64", "materialTemp", "tex",
                      "mh2", "dh2", "Other_v1.tex"), "wb").write(b"TEX0")
    assert ps.list_managed_textures(root, mh, dh) == got3, "不应包含其他区"

    # 拒绝 .tex.bak；拒绝非 ASCII 名
    src2 = os.path.join(root, "in2.tex.bak")
    open(src2, "wb").write(b"TEX0-def")
    try:
        ps.import_texture(root, src2, mh, dh, "in2")
        raise AssertionError("应当拒绝 .tex.bak")
    except ValueError:
        pass

    src3 = os.path.join(root, "in3.tex")
    open(src3, "wb").write(b"TEX0-ghi")
    try:
        ps.import_texture(root, src3, mh, dh, "贴图")
        raise AssertionError("应当拒绝非 ASCII 名")
    except ValueError:
        pass

    shutil.rmtree(root)
    print("test_tex_sources: OK")


if __name__ == "__main__":
    main()
