"""缓存管理页的数据层：母材质产物 / 导入贴图的扫描、引用、删除、改名。"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from material_toolkit.lib import mdf2 as mdf2lib, mmatlib as mlab, psession as ps


def _mkroot():
    root = os.path.join(tempfile.gettempdir(), "rt_manage")
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(os.path.join(root, "natives", "x64", "materialTemp"))
    return root


def main():
    root = _mkroot()
    mh, dh = "mh1", "dh1"
    mt = os.path.join(root, "natives", "x64", "materialTemp")

    # --- 母材质产物 ---
    md = os.path.join(mt, "mmtr")
    os.makedirs(md)
    for fn in ("Mat_r1.mmtr.1808168797", "Mat_r2.mmtr.1808168797",
               "Other_r1.mmtr.1808168797", "junk.txt"):
        with open(os.path.join(md, fn), "wb") as f:
            f.write(b"M" * 16)
    # 登记 Mat → r2
    mlab.save(root, {"version": 2, "mmtrVer": mlab.MMTR_VER, "materials": {
        "Mat": {"name": "Mat", "rev": 2,
                "rel": "materialTemp/mmtr/Mat_r2.mmtr",
                "bytes": 16, "createdAt": "2026-11-01T00:00:00"}}})
    arts = mlab.list_artifacts(root)
    print("arts:", [(a["name"], a["rev"]) for a in arts])
    assert [(a["name"], a["rev"]) for a in arts] == [
        ("Mat", 2), ("Mat", 1), ("Other", 1)], arts

    # 一个副本引用 Mat_r1（旧版本）
    ed = os.path.join(mt, "edit", mh, dh)
    os.makedirs(ed)
    d = mdf2lib.Mdf2.new(10)
    m = d.add_material("A", "materialTemp/mmtr/Mat_r1.mmtr")
    m.add_texture("BaseColorMap", "materialTemp/tex/%s/%s/Tex_v1.tex" % (mh, dh))
    rel_edit = "materialTemp/edit/%s/%s/Test_v1.mdf2" % (mh, dh)
    d.save(ps.rel_to_abs(root, rel_edit))
    idx = ps.load_index(root)
    ps.upsert_object(idx, mh, "character/x.mesh", "x", 1)
    ps.upsert_mdf2(idx, mh, dh, "character/x.mdf2", "character/x.mdf2")
    ps.upsert_copy(idx, mh, dh, "Test", rel_edit, 1)
    ps.save_index(root, idx)

    counts = mlab.mmtr_ref_counts(root)
    print("mmtr refs:", counts)
    assert counts.get("materialtemp/mmtr/mat_r1.mmtr") == 1, counts
    assert counts.get("materialtemp/mmtr/mat_r2.mmtr", 0) == 0

    deleted, dropped = mlab.delete_artifact(root, "materialTemp/mmtr/Other_r1.mmtr")
    assert deleted and not dropped, (deleted, dropped)
    assert not os.path.exists(os.path.join(md, "Other_r1.mmtr.1808168797"))
    deleted, dropped = mlab.delete_artifact(root, "materialTemp/mmtr/Mat_r2.mmtr")
    assert deleted and dropped, (deleted, dropped)
    doc = mlab.load(root)
    assert "Mat" not in (doc.get("materials") or {}), doc
    # 被引用的旧版本仍在（调用方会先查引用，不该删）
    assert os.path.exists(os.path.join(md, "Mat_r1.mmtr.1808168797"))

    # --- 导入贴图 ---
    td = os.path.join(mt, "tex", mh, dh)
    os.makedirs(td)
    for fn in ("Tex_v1.tex.11", "Tex_v2.tex", "Other.tex"):
        with open(os.path.join(td, fn), "wb") as f:
            f.write(b"T" * 8)
    rows = ps.scan_managed_textures(root, mh, dh)
    print("tex rows:", [(r["name"], r["rev"]) for r in rows])
    # 没有 _v<rev> 的（旧 hash 命名 / 其它文件）不识别
    assert [(r["name"], r["rev"]) for r in rows] == [
        ("Tex", 2), ("Tex", 1)], rows

    counts = ps.texture_ref_counts(root, mh, dh)
    print("tex refs:", counts)
    assert counts == {"tex": 1}, counts

    # 复制名字：Tex → NewTex（保留 rev 与版本后缀）
    made = ps.copy_texture_name(root, mh, dh, "Tex", "NewTex")
    print("copied:", sorted(made))
    assert sorted(made) == ["NewTex_v1.tex.11", "NewTex_v2.tex"], made
    assert os.path.exists(os.path.join(td, "NewTex_v1.tex.11"))
    # 重名拒绝
    try:
        ps.copy_texture_name(root, mh, dh, "Tex", "NewTex")
        raise AssertionError("应当拒绝已存在的名字")
    except ValueError:
        pass
    # 非 ASCII 名拒绝
    try:
        ps.copy_texture_name(root, mh, dh, "Tex", "贴图")
        raise AssertionError("应当拒绝非 ASCII 名")
    except ValueError:
        pass

    # 删旧 rev：Tex_v2 未被引用 → 删；Tex_v1 被引用 → 留
    removed = ps.delete_unused_texture_revs(root, mh, dh, "Tex")
    assert removed == ["Tex_v2.tex"], removed
    assert os.path.exists(os.path.join(td, "Tex_v1.tex.11"))

    # 按名字删除（调用方先确认无引用）
    gone = ps.delete_texture_name(root, mh, dh, "NewTex")
    assert sorted(gone) == ["NewTex_v1.tex.11", "NewTex_v2.tex"], gone
    assert not os.path.exists(os.path.join(td, "NewTex_v2.tex"))
    assert os.path.exists(os.path.join(td, "Tex_v1.tex.11"))

    shutil.rmtree(root)
    print("test_manage: OK")


if __name__ == "__main__":
    main()
