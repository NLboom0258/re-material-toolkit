# -*- coding: utf-8 -*-
"""运行时预览会话：对象身份 / edit·orig 副本 / sessions.json 登记。

背景（见 analysis/runtime_preview_plan.md §12 / §13）：

- 引擎按**路径**缓存资源：同路径 ``CreateResource`` 返回已加载的那份，参数块不重建。
  ⇒ 「重置」必须换一个**从未加载过的路径**。
- 实时调参只改内存（每个 mesh 实例独立），不改文件。
  ⇒ 编辑副本（edit）平时可以稳定复用；只有「写回内容」时才需要换新路径。
- 参数块结构（已逆向）：``mesh+0x1f8`` → 材质数组 → mgr → ``base + offset``。

目录（全部在 ``<root>/natives/<subdir>/materialTemp`` 下）::

    sessions.json                                          ← 唯一真相源
    orig/<mdf2Hash>_p<ts>.mdf2.10                          ← 重置用，所有模型共用
    edit/<meshHash>/<mdf2Hash>/<副本名>_v<rev>.mdf2.10     ← 工作副本

两个 hash 都是 8 位 hex（sha1 前 4 字节），输入是**小写化**的资源路径：

    meshHash = h8(meshPath)    ← getMesh() 的资源路径，代表「哪个模型」
    mdf2Hash = h8(mdf2Path)    ← get_Material() 的资源路径，代表「哪一份 mdf2」

为什么 edit 按 meshHash 分：同一份 mdf2 可能被多个模型使用，需要各自独立编辑。
为什么 orig 不分：它只是「原 mdf2 的字节拷贝 + 一个没加载过的路径」，与模型无关。

一个原 mdf2 可以有**多个具名副本**：同一 (meshHash, mdf2Hash) 下的副本放同一个
文件夹，用用户起的名字区分（同组内唯一）。每次保存 / 换母材质 rev+1 —— 引擎按
路径缓存资源，同路径改了内容不会重载。
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import time
import urllib.parse

from . import mdf2 as mdf2lib

TEMP_DIR = "materialTemp"
TEX_DIR = "tex"          # materialTemp/tex/<meshHash>/<mdf2Hash>/ 导入贴图
# natives 下的游戏子目录：DMC5 = x64，RE2/3/7/8 = stm。
# 预览产物必须放在引擎实际读取的那一层，不能放到 natives 根。
# 适配其它游戏时改这里（或在加载时按游戏覆盖）。
NATIVES_SUBDIR = "x64"
SESSIONS = "sessions.json"
MDF2_EXT = ".10"
_TS_RE = re.compile(r"_p(\d{8}_\d{6})")

SCHEMA_VERSION = 2


# ---------------------------------------------------------------- 基础

def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def norm_rel(p):
    """统一成 natives/<subdir> 下的相对路径：正斜杠、去掉前导 natives/x64 等、保留大小写。

    小写只在计算 hash 时做；这里保留原始大小写以便日志展示。
    """
    if not p:
        return ""
    s = str(p).replace("\\", "/").strip()
    low = s.lower()
    for pre in ("natives/x64/", "natives/stm/", "natives/", "./"):
        if low.startswith(pre):
            s = s[len(pre):]
            break
    # 去掉 .mdf2.10 / .mesh.xxx 之类的后缀？不能：mdf2 与 mesh 的扩展名不同，
    # 保留原样更安全，只统一分隔符。
    return s.lstrip("/")


def h8(text):
    """8 位 hex（sha1 前 4 字节，32 bit）。"""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]


def mesh_hash(mesh_path):
    return h8(norm_rel(mesh_path).lower())


def mdf2_hash(mdf2_path):
    return h8(norm_rel(mdf2_path).lower())


def temp_root(game_root):
    return os.path.join(os.path.abspath(game_root), "natives", NATIVES_SUBDIR, TEMP_DIR)


def list_managed_textures(game_root, mh, dh):
    """该 (mesh, 原 mdf2) 独立管理区里的导入贴图 → 逻辑路径（同名只保留最高 rev）。

    只是工具自己的缓存，不扫磁盘 natives（那里可能是别的 mod 的贴图，
    随时会被卸载，不适合作为可选项）。
    """
    d = tex_area_dir(game_root, mh, dh)
    best = {}
    if os.path.isdir(d):
        for f in os.listdir(d):
            low = f.lower()
            i = low.find(".tex")
            if i < 0:
                continue
            base = f[:i]
            m = re.match(r"^(?P<name>.+)_v(?P<rev>\d+)$", base)
            if m:
                key = m.group("name").lower()
                rev = int(m.group("rev"))
                if key not in best or rev > best[key][0]:
                    best[key] = (rev, base)
            else:
                best.setdefault(low, (0, base))   # 旧 hash 命名 / 手放的文件
    out = ["%s/%s/%s/%s/%s.tex" % (TEMP_DIR, TEX_DIR, mh, dh, base)
           for _k, (_r, base) in best.items()]
    return sorted(out, key=str.lower)


def tex_area_dir(game_root, mh, dh):
    """一个「mesh + 原 mdf2」的导入贴图独立目录。"""
    return os.path.join(temp_root(game_root), TEX_DIR, mh, dh)


_TEX_REV_RE = re.compile(r"^(?P<name>.+)_v(?P<rev>\d+)\.tex(?:\..*)?$", re.I)


def texture_name_error(name):
    """导入贴图名是否可用；可用返回 ``""``。

    与副本名不同，贴图名就是 mdf2 里写的路径的一部分，所以只允许 ASCII：
    非 ASCII 会被 percent-encoding 成难读的长串。
    """
    s = (name or "").strip()
    if not s:
        return "名称不能为空"
    if s != name:
        return "名称首尾不能有空格"
    if len(s) > 48:
        return "名称太长（最多 48 个字符）"
    if _BAD_NAME_CHARS.search(s):
        return '名称不能包含 \\ / : * ? " < > | 等字符'
    if s.endswith("."):
        return "名称不能以句点结尾"
    if s.split(".")[0].lower() in _RESERVED_NAMES:
        return "不能使用 Windows 保留名（CON / NUL / COM1…）"
    if any(ord(c) < 32 or ord(c) > 126 for c in s):
        return "只能用 ASCII 字符（名称会直接写进 mdf2 路径）"
    return ""


def next_tex_rev(game_root, mh, dh, name):
    """同名贴图的下一个版本号（扫描目录里现有的 ``<name>_v<N>.tex*``）。"""
    d = tex_area_dir(game_root, mh, dh)
    rev = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            m = _TEX_REV_RE.match(f)
            if m and m.group("name").lower() == name.lower():
                rev = max(rev, int(m.group("rev")))
    return rev + 1


def import_texture(game_root, src_abs, mh, dh, name):
    """把外部 ``.tex`` / ``.tex.<ver>`` 复制到**该 mesh+mdf2 的独立目录**。

    命名 = ``<name>_v<rev>.tex[.<ver>]``；每次导入同名贴图都生成新 rev
    （引擎按逻辑路径缓存，路径不变就不会重载）。mdf2 里写不带版本后缀的
    逻辑路径 ``materialTemp/tex/<mh>/<dh>/<name>_v<rev>.tex``。
    只接受 ``.tex`` 或 ``.tex.<数字>``，不转码 DDS。
    """
    src = os.path.abspath(src_abs)
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    base = os.path.basename(src)
    low = base.lower()
    i = low.find(".tex")
    if i < 0:
        raise ValueError("只支持 .tex（DDS 请先转成 .tex）: %s" % base)
    ver = base[i + 4:]
    if ver and not (ver.startswith(".") and ver[1:].isdigit()):
        raise ValueError("版本后缀只能是 .tex 或 .tex.<数字>：%s" % base)
    err = texture_name_error(name)
    if err:
        raise ValueError(err)
    rev = next_tex_rev(game_root, mh, dh, name)
    stem = "%s_v%d" % (name, rev)
    dst_dir = tex_area_dir(game_root, mh, dh)
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copyfile(src, os.path.join(dst_dir, stem + ".tex" + ver))
    return "%s/%s/%s/%s/%s.tex" % (TEMP_DIR, TEX_DIR, mh, dh, stem)


def import_texture_bytes(game_root, data, mh, dh, name, suffix=""):
    """把**已转换好的** .tex 字节写进该管理区（``<name>_v<rev>.tex<suffix>``）。

    给 DDS → tex 转换用；``suffix`` 是资源版本后缀（DMC5 = ``.11``），
    写进 mdf2 的逻辑路径不带它。同名再次导入 → rev+1。
    """
    err = texture_name_error(name)
    if err:
        raise ValueError(err)
    if not data:
        raise ValueError("贴图数据为空")
    rev = next_tex_rev(game_root, mh, dh, name)
    stem = "%s_v%d" % (name, rev)
    dst_dir = tex_area_dir(game_root, mh, dh)
    os.makedirs(dst_dir, exist_ok=True)
    with open(os.path.join(dst_dir, stem + ".tex" + suffix), "wb") as f:
        f.write(data)
    return "%s/%s/%s/%s/%s.tex" % (TEMP_DIR, TEX_DIR, mh, dh, stem)



def parse_tex_rel(rel):
    """``materialTemp/tex/<mh>/<dh>/<name>_v<rev>.tex`` → dict；不匹配返回 None。"""
    s = norm_rel(rel)
    parts = s.split("/")
    if len(parts) != 5:
        return None
    if parts[0].lower() != TEMP_DIR.lower() or parts[1].lower() != TEX_DIR.lower():
        return None
    fn = parts[4]
    if not fn.lower().endswith(".tex"):
        return None
    m = re.match(r"^(?P<name>.+)_v(?P<rev>\d+)$", fn[:-4])
    if not m:
        return None
    return {"mh": parts[2], "dh": parts[3], "name": m.group("name"),
            "rev": int(m.group("rev")), "rel": s}


def tex_refs_in_mdf2(doc, name, mh, dh):
    """该 mdf2 里引用导入贴图 ``name``（任意 rev）的槽 → ``{材质: {槽type: 旧路径}}``。"""
    low_name = (name or "").lower()
    low_mh, low_dh = (mh or "").lower(), (dh or "").lower()
    out = {}
    for mat in doc.materials:
        hits = {}
        for tb in mat.textures:
            p = parse_tex_rel(tb.texture_path or "")
            if p and p["mh"].lower() == low_mh and p["dh"].lower() == low_dh \
                    and p["name"].lower() == low_name:
                hits[tb.texture_type] = norm_rel(tb.texture_path)
        if hits:
            out[mat.name] = hits
    return out


def managed_texture_exists(game_root, tex_rel):
    """工具管理区里的贴图文件是否存在（逻辑路径，兼容 ``.tex`` / ``.tex.<ver>``）。"""
    rel = norm_rel(tex_rel)
    low = rel.lower()
    if not low.startswith(TEMP_DIR.lower() + "/"):
        return False
    p = os.path.join(temp_root(game_root), rel[len(TEMP_DIR) + 1:].replace("/", os.sep))
    if os.path.isfile(p):
        return True
    d, fn = os.path.split(p)
    if not os.path.isdir(d):
        return False
    stem = fn[:-4] if fn.lower().endswith(".tex") else fn
    for n in os.listdir(d):
        nl = n.lower()
        if nl == fn.lower():
            return True
        if nl.startswith(stem.lower() + ".") and nl.rsplit(".", 1)[-1].isdigit():
            return True
    return False


def tex_abs(game_root, tex_rel):
    """管理区贴图的**物理路径**（兼容 ``.tex`` / ``.tex.<ver>``）；找不到返回 None。"""
    rel = norm_rel(tex_rel)
    low = rel.lower()
    if not low.startswith(TEMP_DIR.lower() + "/"):
        return None
    p = os.path.join(temp_root(game_root), rel[len(TEMP_DIR) + 1:].replace("/", os.sep))
    if os.path.isfile(p):
        return p
    d, fn = os.path.split(p)
    if not os.path.isdir(d):
        return None
    stem = fn[:-4] if fn.lower().endswith(".tex") else fn
    hits = []
    for n in os.listdir(d):
        nl = n.lower()
        if nl == fn.lower():
            return os.path.join(d, n)
        if nl.startswith(stem.lower() + ".") and nl.rsplit(".", 1)[-1].isdigit():
            hits.append(os.path.join(d, n))
    return sorted(hits)[-1] if hits else None


def delete_unused_texture_revs(game_root, mh, dh, name, log=None):
    """删除该管理区里该名字的旧 rev 文件（只删已无副本引用的）。"""
    doc = load_index(game_root)
    used = set()
    for c in list_copies(doc, mh, dh):
        src = rel_to_abs(game_root, c["rel"])
        if not os.path.exists(src):
            continue
        try:
            d = mdf2lib.Mdf2.load(src)
        except Exception:  # noqa: BLE001
            continue
        for mat in d.materials:
            for tb in mat.textures:
                p = parse_tex_rel(tb.texture_path or "")
                if p and p["name"].lower() == (name or "").lower():
                    used.add(p["rel"].lower())
    area = tex_area_dir(game_root, mh, dh)
    removed = []
    if os.path.isdir(area):
        for f in os.listdir(area):
            m = _TEX_REV_RE.match(f)
            if not m or m.group("name").lower() != (name or "").lower():
                continue
            logical = "%s/%s/%s/%s/%s_v%s.tex" % (
                TEMP_DIR, TEX_DIR, mh, dh, m.group("name"), m.group("rev"))
            if logical.lower() in used:
                continue
            try:
                os.remove(os.path.join(area, f))
                removed.append(f)
            except OSError:
                pass
    if removed and log:
        log("已清理旧贴图文件：%s" % ", ".join(sorted(removed)))
    return removed


def scan_managed_textures(game_root, mh, dh):
    """该管理区里的导入贴图（含每个 rev）→ ``[{name, rev, rel, path, size}]``。

    ``rel`` 是逻辑路径（不带版本后缀），按名字升序、rev 降序。
    """
    d = tex_area_dir(game_root, mh, dh)
    out = []
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        m = _TEX_REV_RE.match(fn)
        if not m:
            continue
        p = os.path.join(d, fn)
        out.append({
            "name": m.group("name"),
            "rev": int(m.group("rev")),
            "rel": "%s/%s/%s/%s/%s_v%s.tex" % (
                TEMP_DIR, TEX_DIR, mh, dh, m.group("name"), m.group("rev")),
            "path": p,
            "size": os.path.getsize(p) if os.path.isfile(p) else 0,
        })
    out.sort(key=lambda x: (x["name"].lower(), -x["rev"]))
    return out


def texture_ref_counts(game_root, mh, dh):
    """该管理区里每个贴图名字被引用的槽数（所有副本 + 该原 mdf2 的 orig）。"""
    doc = load_index(game_root)
    rels = [c["rel"] for c in list_copies(doc, mh, dh)]
    e = get_mdf2(doc, mh, dh) or {}
    if e.get("orig"):
        rels.append(norm_rel(e["orig"]))
    counts = {}
    for rel in rels:
        p = rel_to_abs(game_root, rel)
        if not os.path.exists(p):
            continue
        try:
            d = mdf2lib.Mdf2.load(p)
        except Exception:  # noqa: BLE001
            continue
        for mat in d.materials:
            for tb in mat.textures:
                pt = parse_tex_rel(tb.texture_path or "")
                if pt and pt["mh"].lower() == mh.lower() \
                        and pt["dh"].lower() == dh.lower():
                    k = pt["name"].lower()
                    counts[k] = counts.get(k, 0) + 1
    return counts


def delete_texture_name(game_root, mh, dh, name, log=None):
    """删掉该管理区里该名字的**所有** rev 文件（调用方须先确认无引用）。"""
    d = tex_area_dir(game_root, mh, dh)
    removed = []
    if os.path.isdir(d):
        for fn in os.listdir(d):
            m = _TEX_REV_RE.match(fn)
            if not m or m.group("name").lower() != (name or "").lower():
                continue
            try:
                os.remove(os.path.join(d, fn))
                removed.append(fn)
            except OSError:
                pass
    if removed and log:
        log("已删除贴图 %s（%d 个文件）" % (name, len(removed)))
    return removed


def copy_texture_name(game_root, mh, dh, old, new, log=None):
    """把 ``old`` 的所有 rev 文件复制为 ``new``（保留 rev 与版本后缀）。

    重命名用：先复制，等所有副本重指完再走 ``delete_unused_texture_revs(old)``；
    这样即使有副本重指失败，旧名字文件还留着（不会让引用缺失）。
    """
    err = texture_name_error(new)
    if err:
        raise ValueError(err)
    if old.lower() == new.lower():
        return []
    d = tex_area_dir(game_root, mh, dh)
    if not os.path.isdir(d):
        return []
    existing = {f.lower() for f in os.listdir(d)}
    out = []
    for fn in os.listdir(d):
        m = _TEX_REV_RE.match(fn)
        if not m or m.group("name").lower() != (old or "").lower():
            continue
        i = fn.lower().find(".tex")
        suffix = fn[i:]               # ".tex" / ".tex.11"
        new_fn = "%s_v%s%s" % (new, m.group("rev"), suffix)
        if new_fn.lower() in existing:
            raise ValueError("目标名字已存在同版本文件：%s" % new_fn)
        shutil.copyfile(os.path.join(d, fn), os.path.join(d, new_fn))
        out.append(new_fn)
    if out and log:
        log("已复制贴图 %s → %s（%d 个文件）" % (old, new, len(out)))
    return out


def prune_orig(game_root, keep_rels, dh=None, log=None):
    """删掉 orig 下**未被引用**的旧快照。

    重置必须换新路径（引擎按路径缓存），所以每次取消勾选都会生成一份新 orig；
    旧的不清会一直堆积。``keep_rels`` = 逻辑 rel（``materialTemp/orig/...mdf2``，
    不带 ``.10``）集合（小写比较）；给 ``dh`` 时只清理该原 mdf2 的。
    """
    d = os.path.join(temp_root(game_root), "orig")
    if not os.path.isdir(d):
        return []
    keep = {norm_rel(x).lower() for x in (keep_rels or ()) if x}
    removed = []
    for fn in os.listdir(d):
        low = fn.lower()
        if not low.endswith(".mdf2" + MDF2_EXT):
            continue
        if dh and not low.startswith(dh.lower() + "_p"):
            continue
        logical = "%s/orig/%s" % (TEMP_DIR, fn[:-len(MDF2_EXT)])   # 去掉 ".10"
        if logical.lower() in keep:
            continue
        try:
            os.remove(os.path.join(d, fn))
            removed.append(fn)
        except OSError:
            pass
    if removed and log:
        log("已清理旧 orig 快照：%s" % ", ".join(sorted(removed)))
    return removed


# ---------------------------------------------------------------- sessions.json

def _empty():
    return {"version": SCHEMA_VERSION, "objects": {}}


def load_index(game_root, log=None):
    p = os.path.join(temp_root(game_root), SESSIONS)
    if not os.path.exists(p):
        return _empty()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        if log:
            log("sessions.json 解析失败（按空处理）: %s" % e)
        return _empty()
    if not isinstance(d, dict) or "objects" not in d:
        return _empty()
    try:
        ver = int(d.get("version") or 0)
    except (TypeError, ValueError):
        ver = 0
    if ver != SCHEMA_VERSION:
        # 开发期不做旧结构兼容：旧登记直接忽略（旧副本文件由用户手动清理）。
        if log:
            log("忽略旧版 sessions.json（v%s，当前 v%d）" % (ver, SCHEMA_VERSION))
        return _empty()
    return d


def save_index(game_root, idx, log=None):
    root = temp_root(game_root)
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, SESSIONS)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, p)
    if log:
        log("sessions.json 已更新（%d 个模型）" % len(idx.get("objects") or {}))
    return p


def get_object(idx, mh):
    return (idx.get("objects") or {}).get(mh)


def get_mdf2(idx, mh, dh):
    obj = get_object(idx, mh)
    if not obj:
        return None
    return (obj.get("mdfs") or {}).get(dh)


def upsert_object(idx, mh, mesh_path, go_name, slot_count):
    objs = idx.setdefault("objects", {})
    o = objs.get(mh) or {}
    o["meshPath"] = mesh_path
    o["goName"] = go_name
    o["slotCount"] = slot_count
    o.setdefault("mdfs", {})
    objs[mh] = o
    return o


def upsert_mdf2(idx, mh, dh, original_mdf2, base_mdf2, orig_rel=None):
    o = idx.setdefault("objects", {}).setdefault(mh, {"mdfs": {}})
    mdfs = o.setdefault("mdfs", {})
    e = mdfs.get(dh) or {}
    e["originalMdf2"] = original_mdf2
    e["baseMdf2"] = base_mdf2
    if orig_rel is not None:
        e["orig"] = orig_rel
    e["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    mdfs[dh] = e
    return e


# ---------------------------------------------------------------- 副本登记

def list_copies(idx, mh, dh):
    """该 (mesh, mdf2) 下登记的副本，按创建时间升序。"""
    e = get_mdf2(idx, mh, dh) or {}
    out = []
    for key, c in (e.get("copies") or {}).items():
        rel = norm_rel(c.get("rel") or "")
        if not rel:
            continue
        d = dict(c)
        d["name"] = c.get("name") or key
        d["rel"] = rel
        try:
            d["rev"] = int(c.get("rev") or 0)
        except (TypeError, ValueError):
            d["rev"] = 0
        out.append(d)
    out.sort(key=lambda c: (c.get("createdAt") or "", c["name"]))
    return out


def get_copy(idx, mh, dh, name=None, rel=None):
    """按名字或路径找副本登记项；都没有返回 None。"""
    low = norm_rel(rel or "").lower()
    for c in list_copies(idx, mh, dh):
        if name and c["name"] == name:
            return c
        if low and c["rel"].lower() == low:
            return c
    return None


def upsert_copy(idx, mh, dh, name, rel, rev, current=True):
    """写入/更新副本登记。

    ``current=True`` 时同时把它设为该 (mesh, mdf2) 的「最近使用」副本（edit）；
    批量重建（如编译后更新所有副本）时传 False，避免把最近使用改到别的副本。
    """
    e = get_mdf2(idx, mh, dh)
    if e is None:
        raise KeyError("没有登记项：mesh %s / mdf2 %s" % (mh, dh))
    name = (name or "").strip()
    if not name:
        raise ValueError("副本名不能为空")
    copies = e.setdefault("copies", {})
    c = copies.get(name) or {}
    c["name"] = name
    c["rel"] = norm_rel(rel)
    c["rev"] = int(rev)
    c.setdefault("createdAt", _now_iso())
    copies[name] = c
    if current:
        e["edit"] = c["rel"]      # 兼容/最近使用：现有读写都按它取当前副本
    e["updatedAt"] = _now_iso()
    return c


# ---------------------------------------------------------------- 文件

def remove_copy(idx, mh, dh, name):
    """删除一个副本登记，返回被删的登记项（不存在返回 None）。

    若它正是该 (mesh, mdf2) 的「最近使用」（edit），自动改指到另一个副本
    （没有则清空）。文件删除由调用方负责（必须先确认没有对象在用）。
    """
    e = get_mdf2(idx, mh, dh)
    if not e:
        return None
    copies = e.get("copies") or {}
    c = copies.pop(name, None)
    if c is None:
        return None
    if norm_rel(e.get("edit") or "").lower() == norm_rel(c.get("rel") or "").lower():
        e["edit"] = next((x.get("rel") for x in copies.values() if x.get("rel")), "")
    e["updatedAt"] = _now_iso()
    return c


def rename_copy(idx, mh, dh, old_name, new_name, new_rel):
    """把一个副本改名（登记 + 路径），返回更新后的登记项。

    只改 sessions 里的 key/name/rel；磁盘文件改名由调用方负责（要先确认没在用）。
    新名字已存在 → ValueError。
    """
    e = get_mdf2(idx, mh, dh)
    if not e:
        return None
    copies = e.get("copies") or {}
    c = copies.get(old_name)
    if c is None:
        return None
    if new_name in copies:
        raise ValueError("该原 mdf2 下已有名为「%s」的副本" % new_name)
    old_rel = norm_rel(c.get("rel") or "")
    del copies[old_name]
    c["name"] = new_name
    c["rel"] = norm_rel(new_rel)
    p = parse_temp_rel(c["rel"])
    if p:
        c["rev"] = p["rev"]
    copies[new_name] = c
    if norm_rel(e.get("edit") or "").lower() == old_rel.lower():
        e["edit"] = c["rel"]
    e["updatedAt"] = _now_iso()
    return c


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(data)
    return len(data)


def mdf2_abs(game_root, mdf_rel):
    """``Character/.../pl0800.mdf2`` → 绝对路径（自动补 ``.10``）。

    只在游戏根 ``natives/<subdir>`` 下找；找不到就去 pak 里提（见 ``ensure_mdf2``）。
    """
    rel = norm_rel(mdf_rel)
    p = os.path.join(os.path.abspath(game_root), "natives", NATIVES_SUBDIR,
                     rel.replace("/", os.sep))
    for cand in (p, p + MDF2_EXT):
        if os.path.exists(cand):
            return cand
    return p


# 副本名 → 文件名。只做合法性校验，不做字符替换（避免两个不同名字落到同一文件）。
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = ({"con", "prn", "aux", "nul"}
                   | {"com%d" % i for i in range(1, 10)}
                   | {"lpt%d" % i for i in range(1, 10)})
_COPY_STEM_RE = re.compile(r"^(?P<name>.+)_v(?P<rev>\d+)$")


def copy_name_error(name):
    """副本名是否可用；可用返回 ``""``，否则返回给用户看的原因。"""
    s = (name or "").strip()
    if not s:
        return "副本名不能为空"
    if s != name:
        return "副本名首尾不能有空格"
    if len(s) > 48:
        return "副本名太长（最多 48 个字符）"
    if _BAD_NAME_CHARS.search(s):
        return '副本名不能包含 \\ / : * ? " < > | 等字符'
    if s.endswith("."):
        return "副本名不能以句点结尾"
    if s.split(".")[0].lower() in _RESERVED_NAMES:
        return "副本名不能使用 Windows 保留名（CON / NUL / COM1…）"
    if len(copy_file_stem(s)) > 96:
        return "副本名太长（含非 ASCII 字符时最多约 32 个汉字）"
    return ""


def copy_file_stem(name):
    """副本名 → 文件名 stem（只含 ASCII）。

    ⚠️ 实测：非 ASCII 路径传给 REEngine 会被按 ANSI 编码处理（中文变乱码），
    引擎按乱码路径找不到文件 → 材质变成默认方格，对象也会短暂失效。
    所以磁盘上只用 percent-encoding 后的 ASCII；展示仍用原名（存在 sessions.json）。
    """
    return urllib.parse.quote(name, safe="")


def copy_name_from_stem(stem):
    return urllib.parse.unquote(stem)


def new_copy_rel(mh, dh, name, rev):
    """副本相对路径（不带 ``.10``）：``edit/<mh>/<dh>/<ASCII名>_v<rev>.mdf2``。"""
    err = copy_name_error(name)
    if err:
        raise ValueError(err)
    return posixpath.join(TEMP_DIR, "edit", mh, dh,
                          "%s_v%d.mdf2" % (copy_file_stem(name), int(rev)))


def new_orig_rel(dh, ts=None, game_root=None):
    """新的重置副本路径（不带 `.10`）。给 game_root 时保证磁盘上不重名。"""
    ts = ts or _stamp()
    rel = posixpath.join(TEMP_DIR, "orig", "%s_p%s.mdf2" % (dh, ts))
    if game_root:
        i = 0
        while os.path.exists(rel_to_abs(game_root, rel)):
            i += 1
            rel = posixpath.join(TEMP_DIR, "orig", "%s_p%s_%d.mdf2" % (dh, ts, i))
    return rel


def bump_copy_rel(game_root, rel):
    """同一副本的下一个版本路径（rev+1，避开已存在文件）。"""
    p = parse_temp_rel(rel)
    if not p or p.get("kind") != "edit":
        raise RuntimeError("不是可识别的副本路径：%s" % rel)
    rev = p["rev"]
    while True:
        rev += 1
        cand = new_copy_rel(p["mh"], p["dh"], p["name"], rev)
        if not os.path.exists(rel_to_abs(game_root, cand)):
            break
    return {"name": p["name"], "rev": rev, "rel": cand,
            "mh": p["mh"], "dh": p["dh"]}


def parse_temp_rel(rel):
    """从 materialTemp 下的路径反推身份，认不出返回 None。

    支持两种（都是本模块生成的）：

        materialTemp/edit/<meshHash>/<mdf2Hash>/<副本名>_v<rev>.mdf2
        materialTemp/orig/<mdf2Hash>_p<ts>.mdf2

    用途：sessions.json 丢了（手动清理）时还能把当前挂着的副本「接管」回来，
    而不用重启游戏。
    """
    s = norm_rel(rel)
    if not s.lower().startswith(TEMP_DIR.lower() + "/"):
        return None
    parts = s[len(TEMP_DIR) + 1:].split("/")
    if len(parts) == 4 and parts[0] == "edit":
        stem = parts[3]
        if stem.lower().endswith(".mdf2"):
            stem = stem[:-5]
        m = _COPY_STEM_RE.match(stem)
        if m:
            return {"kind": "edit", "mh": parts[1], "dh": parts[2],
                    "name": copy_name_from_stem(m.group("name")),
                    "rev": int(m.group("rev"))}
    if len(parts) == 2 and parts[0] == "orig":
        m = _TS_RE.search(parts[1])
        if m and "_p" in parts[1]:
            return {"kind": "orig", "mh": None,
                    "dh": parts[1].split("_p")[0], "ts": m.group(1)}
    return None


def _missing_mdf2_msg(path, rel):
    """磁盘和 pak 都找不到 mdf2 时的报错文案。

    查找顺序：游戏根 ``natives/<subdir>`` → **从 pak 按需提取**。
    两者都没有才报错（通常是游戏版本不一致 / pak 里本来就没这个资源）。
    """
    return (
        "找不到 mdf2：\n%s\n\n"
        "资源路径：%s\n\n"
        "已经试过：游戏根 natives/<subdir>、从 re_chunk_*.pak 提取。\n"
        "如果游戏刚更新过，可能是 pak 里该资源的路径变了。"
        % (path, norm_rel(rel))
    )


def pak_has(game_root, mdf_rel):
    """该资源能不能从 pak 里取到（不实际解压）。"""
    try:
        from . import pakio
        return pakio.find_entry(game_root, mdf_rel) is not None
    except Exception:
        return False


def ensure_mdf2(game_root, mdf_rel, log=None):
    """拿一份**可用的** mdf2 文件（磁盘优先，其次从 pak 提取到缓存）。

    只有部分模型被解包到游戏根，其余还在 ``re_chunk_000.pak`` 里。
    pak 只存路径 hash，而游戏给的 ``mdf2Path`` 正好能算出它，所以不需要 listfile。
    提取结果缓存在 ``materialTemp/src/<mdf2Hash>.mdf2.10``，同一资源只解压一次。
    """
    p = mdf2_abs(game_root, mdf_rel)
    if os.path.exists(p):
        return p

    from . import pakio
    data = pakio.read_resource(game_root, mdf_rel)
    if data is None:
        raise RuntimeError(_missing_mdf2_msg(p, mdf_rel))

    cache_dir = os.path.join(temp_root(game_root), "src")
    os.makedirs(cache_dir, exist_ok=True)
    dst = os.path.join(cache_dir, mdf2_hash(mdf_rel) + ".mdf2" + MDF2_EXT)
    try:
        fresh = (not os.path.exists(dst)) or os.path.getsize(dst) != len(data)
    except OSError:
        fresh = True
    if fresh:
        with open(dst, "wb") as f:
            f.write(data)
    if log:
        log("从 pak 提取 mdf2 → materialTemp/src/%s (%d B)"
            % (os.path.basename(dst), len(data)))
    return dst


def create_copy(game_root, mh, dh, base_mdf2, name="默认", rev=1, log=None):
    """新建一份具名副本（内容 = base_mdf2 的字节）。

    返回 ``{name, rev, rel, mh, dh, bytes}``；rel 不带 `.10`，磁盘上写 ``.mdf2.10``
    —— 与 ``/material apply`` 的约定一致：相对路径不带扩展名，引擎自己补。
    """
    err = copy_name_error(name)
    if err:
        raise ValueError(err)
    src = ensure_mdf2(game_root, base_mdf2, log=log)
    while True:
        rel = new_copy_rel(mh, dh, name, rev)
        if not os.path.exists(rel_to_abs(game_root, rel)):
            break
        rev += 1
    dst = rel_to_abs(game_root, rel)
    n = _copy(src, dst)
    if log:
        log("副本 %s（v%d）%s  (%d B)" % (name, rev, rel, n))
    return {"name": name, "rev": rev, "rel": rel,
            "mh": mh, "dh": dh, "bytes": n}


def create_orig(game_root, dh, base_mdf2, log=None):
    """新建一份 orig 副本（重置用，路径必须全新）。"""
    src = ensure_mdf2(game_root, base_mdf2, log=log)
    rel = new_orig_rel(dh, game_root=game_root)
    dst = rel_to_abs(game_root, rel)
    n = _copy(src, dst)
    if log:
        log("orig 副本 %s  (%d B)" % (rel, n))
    return rel


def rel_to_abs(game_root, rel):
    """相对路径（``materialTemp/...``，不带 .10）→ 磁盘绝对路径（带 .10）。"""
    sub = (rel or "")[len(TEMP_DIR) + 1:].replace("/", os.sep)
    p = os.path.join(temp_root(game_root), sub)
    if not p.lower().endswith(MDF2_EXT):
        p += MDF2_EXT
    return p


# ---------------------------------------------------------------- 参数写盘

def save_params(game_root, rel_edit, changes, log=None):
    """把 changes **原地**写进同一副本的**新版本路径**，返回
    ``{rel, rev, name, mh, dh, bytes, touched, skipped, changed}``。

    ``changes`` = ``{材质名: {参数名: [float...]}}``。

    **参数已不存在 / 类型变了**（比如刚在材质系统里删了参数、又编译了新版本）
    会跳过并记入 ``skipped``，而不是整个保存失败；全部失效时 ``changed=False``
    且不产出新文件。

    为什么必须换新路径：引擎按路径缓存 mdf2，同路径改了文件不会重载
    （见 analysis/runtime_preview_plan.md §13.4）。新路径 = 同一个副本的
    ``edit/<mh>/<dh>/<副本名>_v<rev+1>.mdf2``，身份不变，只换版本号。

    为什么原地覆盖而不是 ``Mdf2.save()``：引擎只在加载时读结构，值区改多少字节
    都不影响布局；``save()`` 会重排整个文件（偏移 + hash），风险大且无必要。
    写完会重新 load 逐值比对，不一致就删掉新文件不留半成品。
    """
    src = rel_to_abs(game_root, rel_edit)
    if not os.path.exists(src):
        raise RuntimeError("mdf2 副本不存在：%s" % src)
    if not changes:
        raise RuntimeError("changes 为空")

    with open(src, "rb") as f:
        data = bytearray(f.read())
    doc = mdf2lib.Mdf2.load(src)
    touched = []
    skipped = []
    effective = {}
    for mat_name, props in changes.items():
        mat = doc.get_material(mat_name)
        if mat is None:
            raise mdf2lib.ParamWriteError(
                "mdf2 里没有材质 %r（现有: %s）"
                % (mat_name, ", ".join(m.name for m in doc.materials)))
        keep = {}
        for pname, vals in (props or {}).items():
            pr = mat.get_parameter(pname)
            if pr is None:
                skipped.append((mat_name, pname, "参数已不存在"))
                continue
            if len(vals or []) != len(pr.values):
                skipped.append((mat_name, pname, "类型已变化"))
                continue
            keep[pname] = vals
        if keep:
            effective[mat_name] = keep
            touched += mdf2lib.patch_param_floats(data, mat, keep)

    if skipped and log:
        log("跳过 %d 个已失效的参数：%s"
            % (len(skipped), "; ".join("%s.%s（%s）" % x for x in skipped)))
    if not touched:
        return {"rel": rel_edit, "old": rel_edit, "changed": False,
                "skipped": skipped, "bytes": 0, "touched": []}

    # 新路径：同一副本 rev+1（引擎按路径缓存，必须换路径）
    new = bump_copy_rel(game_root, rel_edit)
    new_rel = new["rel"]

    dst = rel_to_abs(game_root, new_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(bytes(data))

    # 校验：重新 load 逐值比对（只比对真正写进去的参数）
    try:
        chk = mdf2lib.Mdf2.load(dst)
        for mat_name, props in effective.items():
            mat = chk.get_material(mat_name)
            for pname, vals in props.items():
                pr = mat.get_parameter(pname)
                got = [round(x, 6) for x in pr.values]
                want = [round(float(v), 6) for v in vals]
                if got != want:
                    raise RuntimeError(
                        "写盘校验失败 %s.%s: 期望 %s，读回 %s"
                        % (mat_name, pname, want, got))
    except Exception:
        try:
            os.remove(dst)
        except OSError:
            pass
        raise

    if log:
        log("写盘 %s（%s v%d，%d B，%d 个参数）"
            % (new_rel, new["name"], new["rev"], len(data), len(touched)))
    return {"rel": new_rel, "rev": new["rev"], "name": new["name"],
            "mh": new["mh"], "dh": new["dh"], "changed": True,
            "skipped": skipped, "bytes": len(data), "touched": touched}


def exists_edit(game_root, rel):
    return bool(rel) and os.path.exists(rel_to_abs(game_root, rel))
