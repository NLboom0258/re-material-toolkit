"""导出副本为 mod 结构：依赖收集、目录布局、zip、缺失检查。"""
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from material_toolkit.lib import exporter, mdf2 as mdf2lib, psession as ps


def main():
    root = os.path.join(tempfile.gettempdir(), "rt_export")
    if os.path.isdir(root):
        shutil.rmtree(root)
    mt = os.path.join(root, "natives", "x64", "materialTemp")
    mh, dh = "mh1", "dh1"

    # 工具管理的依赖
    os.makedirs(os.path.join(mt, "mmtr"))
    with open(os.path.join(mt, "mmtr", "Mat_r1.mmtr.1808168797"), "wb") as f:
        f.write(b"MMTR" + b"\0" * 32)
    td = os.path.join(mt, "tex", mh, dh)
    os.makedirs(td)
    with open(os.path.join(td, "Test_v1.tex.11"), "wb") as f:
        f.write(b"TEX0" + b"\0" * 32)

    # 副本 mdf2：A 引用工具 mmtr + 导入贴图；B 引用原版资源（不应收集）
    d = mdf2lib.Mdf2.new(10)
    a = d.add_material("A", "materialTemp/mmtr/Mat_r1.mmtr")
    a.add_texture("BaseColorMap", "materialTemp/tex/%s/%s/Test_v1.tex" % (mh, dh))
    b = d.add_material("B", "shaders/orig.mmtr")
    b.add_texture("NormalMap", "texture/orig_n.tex")
    rel_edit = "materialTemp/edit/%s/%s/Test_v1.mdf2" % (mh, dh)
    os.makedirs(os.path.dirname(ps.rel_to_abs(root, rel_edit)), exist_ok=True)
    d.save(ps.rel_to_abs(root, rel_edit))

    info = exporter.collect(root, rel_edit)
    print("mmtr:", [x["rel"] for x in info["mmtr"]])
    print("tex :", [x["rel"] for x in info["tex"]])
    assert [x["rel"] for x in info["mmtr"]] == ["materialTemp/mmtr/Mat_r1.mmtr"], info
    assert [x["rel"] for x in info["tex"]] == [
        "materialTemp/tex/%s/%s/Test_v1.tex" % (mh, dh)], info
    assert not info["missing"], info["missing"]

    out = os.path.join(root, "out")
    r = exporter.export(root, rel_edit, out, "character/x.mdf2",
                        make_zip=True, zip_name="MyMod")
    print("files:", [(f["kind"], f["rel"]) for f in r["files"]])
    base = os.path.join(out, "natives", "x64")
    want = [
        os.path.join(base, "character", "x.mdf2.10"),
        os.path.join(base, "materialTemp", "mmtr", "Mat_r1.mmtr.1808168797"),
        os.path.join(base, "materialTemp", "tex", mh, dh, "Test_v1.tex.11"),
    ]
    for p in want:
        assert os.path.isfile(p), p
    assert os.path.isfile(r["zip"]), r["zip"]
    with zipfile.ZipFile(r["zip"]) as z:
        names = set(z.namelist())
    print("zip:", sorted(names))
    assert names == {
        "natives/x64/character/x.mdf2.10",
        "natives/x64/materialTemp/mmtr/Mat_r1.mmtr.1808168797",
        "natives/x64/materialTemp/tex/%s/%s/Test_v1.tex.11" % (mh, dh),
    }, names

    # 复用输出目录：目录里已有的旧文件不能进 zip
    stale = os.path.join(base, "character", "stale.mdf2.10")
    with open(stale, "wb") as f:
        f.write(b"OLD")
    r2 = exporter.export(root, rel_edit, out, "character/x.mdf2",
                         make_zip=True, zip_name="MyMod2")
    with zipfile.ZipFile(r2["zip"]) as z:
        names2 = set(z.namelist())
    assert "natives/x64/character/stale.mdf2.10" not in names2, names2

    # 缺失依赖：删掉贴图后 collect/export 应报错
    os.remove(os.path.join(td, "Test_v1.tex.11"))
    info2 = exporter.collect(root, rel_edit)
    assert info2["missing"], info2
    try:
        exporter.export(root, rel_edit, os.path.join(root, "out2"), "character/x.mdf2")
        raise AssertionError("依赖缺失应当报错")
    except RuntimeError as ex:
        assert "依赖缺失" in str(ex), ex

    # 路径规范化
    assert exporter.norm_mdf2_rel("character/x.mdf2.10") == "character/x.mdf2"
    assert exporter.norm_mdf2_rel("character/x") == "character/x.mdf2"
    assert exporter.norm_mdf2_rel("natives/x64/character/x.mdf2") == "character/x.mdf2"
    for bad in ("../evil/x.mdf2", "a/../../b.mdf2", "/abs/x.mdf2",
                "C:/tmp/x.mdf2", ".", ".."):
        try:
            exporter.norm_mdf2_rel(bad)
            raise AssertionError("应当拒绝：%s" % bad)
        except ValueError:
            pass

    shutil.rmtree(root, ignore_errors=True)
    print("test_export: OK")


if __name__ == "__main__":
    main()
