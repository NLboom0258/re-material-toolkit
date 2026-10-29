# -*- coding: utf-8 -*-
"""运行时预览会话：对象身份 / edit·orig 副本 / sessions.json 登记。

背景（见 analysis/runtime_preview_plan.md §12 / §13）：

- 引擎按**路径**缓存资源：同路径 ``CreateResource`` 返回已加载的那份，参数块不重建。
  ⇒ 「重置」必须换一个**从未加载过的路径**。
- 实时调参只改内存（每个 mesh 实例独立），不改文件。
  ⇒ 编辑副本（edit）平时可以稳定复用；只有「写回内容」时才需要换新路径。
- 参数块结构（已逆向）：``mesh+0x1f8`` → 材质数组 → mgr → ``base + offset``。

目录（全部在 ``<root>/natives/x64/materialTemp`` 下）::

    sessions.json                                  ← 唯一真相源
    orig/<mdf2Hash>_p<ts>.mdf2.10                  ← 重置用，所有模型共用
    edit/<meshHash>/<mdf2Hash>_p<ts>.mdf2.10       ← 工作副本，按模型分类

两个 hash 都是 8 位 hex（sha1 前 4 字节），输入是**小写化**的资源路径：

    meshHash = h8(meshPath)    ← getMesh() 的资源路径，代表「哪个模型」
    mdf2Hash = h8(mdf2Path)    ← get_Material() 的资源路径，代表「哪一份 mdf2」

为什么 edit 按 meshHash 分：同一份 mdf2 可能被多个模型使用，需要各自独立编辑。
为什么 orig 不分：它只是「原 mdf2 的字节拷贝 + 一个没加载过的路径」，与模型无关。
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import time

TEMP_DIR = "materialTemp"
SESSIONS = "sessions.json"
MDF2_EXT = ".10"
_TS_RE = re.compile(r"_p(\d{8}_\d{6})")

SCHEMA_VERSION = 1


# ---------------------------------------------------------------- 基础

def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def norm_rel(p):
    """统一成 natives/x64 下的相对路径：正斜杠、去掉前导 natives/x64、小写。

    引擎返回的路径大小写混用（``Character/Player/...`` 与 ``character/player/...``），
    而 hash 必须稳定，所以一律小写。原始大小写另存一份用于日志展示。
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
    return os.path.join(os.path.abspath(game_root), "natives", "x64", TEMP_DIR)


def rel_to_natives(game_root, abs_path):
    """绝对路径 → ``materialTemp/...`` 形式的相对路径（给 /material apply 用）。"""
    nat = os.path.join(os.path.abspath(game_root), "natives", "x64")
    try:
        rel = os.path.relpath(abs_path, nat)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    return rel.replace("\\", "/")


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
    d.setdefault("version", SCHEMA_VERSION)
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


def upsert_mdf2(idx, mh, dh, original_mdf2, base_mdf2, edit_rel=None,
                orig_rel=None, active=True):
    o = idx.setdefault("objects", {}).setdefault(mh, {"mdfs": {}})
    mdfs = o.setdefault("mdfs", {})
    e = mdfs.get(dh) or {}
    e["originalMdf2"] = original_mdf2
    e["baseMdf2"] = base_mdf2
    if edit_rel is not None:
        e["edit"] = edit_rel
    if orig_rel is not None:
        e["orig"] = orig_rel
    e["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    mdfs[dh] = e
    if active:
        o["active"] = dh
    return e


# ---------------------------------------------------------------- 文件

def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(data)
    return len(data)


def mdf2_abs(game_root, mdf_rel):
    """``Character/.../pl0800.mdf2`` → 绝对路径（自动补 ``.10``）。"""
    nat = os.path.join(os.path.abspath(game_root), "natives", "x64")
    rel = norm_rel(mdf_rel)
    p = os.path.join(nat, rel.replace("/", os.sep))
    if os.path.exists(p):
        return p
    if not p.lower().endswith(MDF2_EXT):
        p2 = p + MDF2_EXT
        if os.path.exists(p2):
            return p2
    return p


def new_edit_rel(mh, dh, ts=None):
    return posixpath.join(TEMP_DIR, "edit", mh, "%s_p%s.mdf2" % (dh, ts or _stamp()))


def new_orig_rel(dh, ts=None):
    return posixpath.join(TEMP_DIR, "orig", "%s_p%s.mdf2" % (dh, ts or _stamp()))


def create_edit(game_root, mh, dh, base_mdf2, log=None):
    """新建一份 edit 副本（内容 = base_mdf2 的字节）。

    返回相对路径（``materialTemp/edit/<mh>/<dh>_p<ts>.mdf2``，**不带** `.10`），
    磁盘上实际写的是 ``....mdf2.10`` —— 与 ``/material apply`` 的约定一致：
    相对路径不带扩展名，引擎自己补。
    """
    src = mdf2_abs(game_root, base_mdf2)
    if not os.path.exists(src):
        raise RuntimeError("base mdf2 不存在：%s" % src)
    rel = new_edit_rel(mh, dh)
    dst = rel_to_abs(game_root, rel)
    n = _copy(src, dst)
    if log:
        log("edit 副本 %s  (%d B)" % (rel, n))
    return rel


def create_orig(game_root, dh, base_mdf2, log=None):
    """新建一份 orig 副本（重置用，路径必须全新）。"""
    src = mdf2_abs(game_root, base_mdf2)
    if not os.path.exists(src):
        raise RuntimeError("base mdf2 不存在：%s" % src)
    rel = new_orig_rel(dh)
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


# ---------------------------------------------------------------- 清理

def _referenced(idx):
    used = set()
    for o in (idx.get("objects") or {}).values():
        for e in (o.get("mdfs") or {}).values():
            for k in ("edit", "orig"):
                v = e.get(k)
                if v:
                    used.add(v.replace("\\", "/").lower())
    return used


def prune(game_root, idx, keep_edit=2, keep_orig=1, dry_run=True, log=None):
    """找出可清理的旧副本（默认**只报告不删**）。

    ⚠️ 实测教训：删掉一份「当前正挂在 mesh 上」的 mdf2，当时看起来没事
    （引擎已加载进内存），但一旦触发重载（湿身变体切换 / 对象重建），
    引擎读不到文件 → 默认方格材质 → 最终闪退。
    所以默认 dry_run=True，且永远跳过 sessions.json 里登记的 edit / orig。
    """
    root = temp_root(game_root)
    used = _referenced(idx)
    candidates = []

    def _rel(abs_path):
        return rel_to_natives(game_root, abs_path)

    # edit：每个 <meshHash>/ 目录，按 (mdf2Hash) 分组保留最近 keep_edit 个
    edit_root = os.path.join(root, "edit")
    if os.path.isdir(edit_root):
        for mh in os.listdir(edit_root):
            d = os.path.join(edit_root, mh)
            if not os.path.isdir(d):
                continue
            groups = {}
            for name in os.listdir(d):
                m = re.match(r"^([0-9a-f]{8})_p\d{8}_\d{6}", name, re.I)
                groups.setdefault((m.group(1).lower() if m else name), []).append(name)
            for _dh, names in groups.items():
                names.sort()          # 时间戳升序
                if len(names) <= keep_edit:
                    continue
                for name in names[:len(names) - keep_edit]:
                    abs_p = os.path.join(d, name)
                    rel = _rel(abs_p)
                    if rel and rel.lower() in used:
                        continue
                    candidates.append(rel)
                    if dry_run:
                        continue
                    try:
                        os.remove(abs_p)
                    except OSError:
                        pass

    # orig：平铺，按 mdf2Hash 分组，保留最近 keep_orig 个
    orig_root = os.path.join(root, "orig")
    groups = {}
    if os.path.isdir(orig_root):
        for name in os.listdir(orig_root):
            m = re.match(r"^([0-9a-f]{8})_p\d{8}_\d{6}", name, re.I)
            groups.setdefault((m.group(1).lower() if m else name), []).append(name)
    for _dh, names in groups.items():
        names.sort()
        if len(names) <= keep_orig:
            continue
        for name in names[:len(names) - keep_orig]:
            abs_p = os.path.join(orig_root, name)
            rel = _rel(abs_p)
            if rel and rel.lower() in used:
                continue
            candidates.append(rel)
            if dry_run:
                continue
            try:
                os.remove(abs_p)
            except OSError:
                pass

    if log:
        log("%s %d 个旧副本" % ("可清理" if dry_run else "已删除", len(candidates)))
    return candidates


def exists_edit(game_root, rel):
    return bool(rel) and os.path.exists(rel_to_abs(game_root, rel))
