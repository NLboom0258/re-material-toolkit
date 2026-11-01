# -*- coding: utf-8 -*-
"""导出当前副本为可分发的 mod 结构。

相对输出目录：

    natives/x64/<mdf2_rel>.10
    natives/x64/materialTemp/mmtr/<...>.mmtr.1808168797
    natives/x64/materialTemp/tex/<mh>/<dh>/<...>.tex.11

mdf2 内容不改：原版资源路径继续指向游戏，``materialTemp`` 路径靠随包文件命中。
只收集副本里**工具管理**的依赖（``materialTemp/mmtr``、``materialTemp/tex``），
原版贴图/母材质不随包。
"""
from __future__ import annotations

import os
import shutil
import zipfile

from . import mdf2 as mdf2lib
from . import mmatlib as mlab
from . import psession as ps

NATIVES = "natives"


def norm_mdf2_rel(rel):
    """用户填的 mdf2 相对路径 → ``character/.../x.mdf2``（无版本后缀）。"""
    s = ps.norm_rel(rel or "")
    if not s:
        raise ValueError("mdf2 相对路径为空")
    low = s.lower()
    if low.endswith(ps.MDF2_EXT):
        s = s[: -len(ps.MDF2_EXT)]
    if not s.lower().endswith(".mdf2"):
        s += ".mdf2"
    return s


def _phys_rel(game_root, path):
    """物理路径 → 相对 ``materialTemp`` 的正斜杠路径（含版本后缀）。"""
    rel = os.path.relpath(path, ps.temp_root(game_root))
    return rel.replace("\\", "/")


def collect(game_root, edit_rel):
    """读副本 mdf2，收集工具管理的依赖。

    返回 ``{mdf2_abs, mmtr: [{rel, path}], tex: [...], missing: [{kind, rel, mat}]}``。
    """
    src = ps.rel_to_abs(game_root, edit_rel)
    if not os.path.isfile(src):
        raise RuntimeError("副本文件不存在：%s" % src)
    doc = mdf2lib.Mdf2.load(src)
    mmtr, tex, missing = {}, {}, []
    mmtr_pre = (ps.TEMP_DIR + "/mmtr/").lower()
    tex_pre = (ps.TEMP_DIR + "/tex/").lower()
    for mat in doc.materials:
        mp = ps.norm_rel(mat.mmtr_path or "")
        if mp.lower().startswith(mmtr_pre):
            p = mlab.mmtr_abs(game_root, mp)
            if os.path.isfile(p):
                mmtr.setdefault(mp.lower(), {
                    "rel": mp, "path": p,
                    "rel_out": "%s/%s" % (ps.TEMP_DIR, _phys_rel(game_root, p))})
            else:
                missing.append({"kind": "mmtr", "rel": mp, "mat": mat.name})
        for tb in mat.textures:
            tp = ps.norm_rel(tb.texture_path or "")
            if tp.lower().startswith(tex_pre):
                p = ps.tex_abs(game_root, tp)
                if p:
                    tex.setdefault(tp.lower(), {
                        "rel": tp, "path": p,
                        "rel_out": "%s/%s" % (ps.TEMP_DIR, _phys_rel(game_root, p))})
                else:
                    missing.append({"kind": "tex", "rel": tp, "mat": mat.name})
    return {
        "mdf2_abs": src,
        "mmtr": sorted(mmtr.values(), key=lambda x: x["rel"].lower()),
        "tex": sorted(tex.values(), key=lambda x: x["rel"].lower()),
        "missing": missing,
    }


def plan(game_root, edit_rel, out_dir, mdf2_rel, include_mmtr=True, include_tex=True):
    """返回 ``(info, files)``；files 是待写清单（``kind/rel/path``，rel 相对 natives/x64）。"""
    info = collect(game_root, edit_rel)
    rel = norm_mdf2_rel(mdf2_rel)
    base = os.path.join(out_dir, NATIVES, ps.NATIVES_SUBDIR)
    files = [{"kind": "mdf2", "rel": rel + ps.MDF2_EXT,
              "path": os.path.join(base, rel.replace("/", os.sep)) + ps.MDF2_EXT}]
    if include_mmtr:
        for it in info["mmtr"]:
            r = it["rel_out"]
            files.append({"kind": "mmtr", "rel": r,
                          "path": os.path.join(base, r.replace("/", os.sep))})
    if include_tex:
        for it in info["tex"]:
            r = it["rel_out"]
            files.append({"kind": "tex", "rel": r,
                          "path": os.path.join(base, r.replace("/", os.sep))})
    return info, files


def export(game_root, edit_rel, out_dir, mdf2_rel, include_mmtr=True,
           include_tex=True, make_zip=False, zip_name=None, log=None):
    """写文件（同名覆盖）+ 可选 zip；返回 ``{out_dir, files, zip}``。"""
    if not out_dir:
        raise ValueError("输出目录为空")
    info, files = plan(game_root, edit_rel, out_dir, mdf2_rel,
                       include_mmtr=include_mmtr, include_tex=include_tex)
    if info["missing"]:
        raise RuntimeError("依赖缺失：\n" + "\n".join(
            "%s: %s（材质 %s）" % (m["kind"], m["rel"], m["mat"])
            for m in info["missing"]))

    src_by_rel = {}
    for it in info["mmtr"] + info["tex"]:
        src_by_rel[it["rel_out"]] = it["path"]

    written = []
    for f in files:
        dst = f["path"]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        src = info["mdf2_abs"] if f["kind"] == "mdf2" else src_by_rel.get(f["rel"])
        if not src:
            raise RuntimeError("内部错误：找不到依赖源 %s" % f["rel"])
        shutil.copy2(src, dst)
        written.append({"kind": f["kind"], "rel": f["rel"],
                        "size": os.path.getsize(dst)})
        if log:
            log("导出 %-4s %s" % (f["kind"], f["rel"]))

    zip_path = None
    if make_zip:
        nm = (zip_name or os.path.basename(os.path.normpath(out_dir)) or "mod").strip()
        if not nm.lower().endswith(".zip"):
            nm += ".zip"
        zip_path = os.path.join(out_dir, nm)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for d, _dirs, fs in os.walk(os.path.join(out_dir, NATIVES)):
                for name in fs:
                    p = os.path.join(d, name)
                    z.write(p, os.path.relpath(p, out_dir).replace("\\", "/"))
        if log:
            log("已生成 %s" % zip_path)

    return {"out_dir": out_dir, "files": written, "zip": zip_path}
