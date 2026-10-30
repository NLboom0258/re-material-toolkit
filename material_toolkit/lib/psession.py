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

from . import mdf2 as mdf2lib

TEMP_DIR = "materialTemp"
SESSIONS = "sessions.json"
# 额外的 mdf2 源目录（解包目录）。游戏根下找不到时从这里取：
# 只有部分模型被解包到游戏根的 natives/x64，其余还在 pak 里；
# 用户自己的解包目录（如 REtool / REE 导出的 natives/x64）可以补上。
SOURCES = "sources.json"
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


# ---------------------------------------------------------------- 额外源目录

_sources_cache = {}
_sources_ts = {}


def load_sources(game_root, ttl=3.0):
    """读取 ``materialTemp/sources.json`` 里登记的额外源目录列表。

    存在理由：只有部分模型被解包到**游戏根**的 ``natives/x64``，其余还在
    ``re_chunk_000.pak`` 里。用户自己的解包目录（REE / REtool 导出的
    ``natives/x64``）可以登记在这里，``mdf2_abs`` 找不到时会去那里取。

    带 3 秒缓存：GUI 刷列表时每个 mesh 都会问一次，不能每次都读盘。
    """
    key = os.path.abspath(game_root)
    now = time.time()
    if key in _sources_cache and now - _sources_ts.get(key, 0.0) < ttl:
        return _sources_cache[key]
    p = os.path.join(temp_root(game_root), SOURCES)
    roots = []
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            roots = [str(r) for r in (d.get("extraRoots") or []) if r and os.path.isdir(str(r))]
    except Exception:
        roots = []
    _sources_cache[key] = roots
    _sources_ts[key] = now
    return roots


def save_sources(game_root, roots, log=None):
    """写入额外源目录列表。传空列表 = 清空（只靠游戏根）。"""
    root = temp_root(game_root)
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, SOURCES)
    clean = []
    for r in (roots or []):
        r = str(r).strip()
        if r and r not in clean:
            clean.append(r)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"extraRoots": clean}, f, ensure_ascii=False, indent=2)
    key = os.path.abspath(game_root)
    _sources_cache.pop(key, None)
    _sources_ts.pop(key, None)
    if log:
        log("源目录已更新：%d 个%s" % (len(clean), ("（" + clean[0] + "）") if clean else ""))
    return p


def mdf2_abs(game_root, mdf_rel, extra_roots=None):
    """``Character/.../pl0800.mdf2`` → 绝对路径（自动补 ``.10``）。

    查找顺序：游戏根 ``natives/x64`` → ``sources.json`` 里登记的额外源目录
    （解包目录）。后者用于「该资源还在 pak 里没解包」的模型。
    """
    rel = norm_rel(mdf_rel)
    rel_os = rel.replace("/", os.sep)
    roots = [os.path.join(os.path.abspath(game_root), "natives", "x64")]
    if extra_roots is None:
        extra_roots = load_sources(game_root)
    roots += [os.path.abspath(r) for r in (extra_roots or []) if r]

    first = None
    for base in roots:
        p = os.path.join(base, rel_os)
        if first is None:
            first = p
        for cand in (p, p + MDF2_EXT):
            if os.path.exists(cand):
                return cand
    return first


# 带序号的形式（同秒多次产出时用）：X_p<YYYYmmdd_HHMMSS>_N.mdf2
_REL_SEQ_RE = re.compile(r"_p(\d{8}_\d{6})(?:_(\d+))?\.mdf2$")


def _unique_rel(game_root, rel):
    """若 rel 在磁盘上已存在，追加 _1 / _2 …（序号按整数比，_10 > _9）。

    为什么需要：时间戳只到秒，同一秒内连续产出（改属性 → 再改 → 再改）
    会拿到同一个路径，后一次直接覆盖前一次 —— 而前一次可能正挂在 mesh 上
    （引擎已加载进内存，磁盘被覆盖不会立即出问题，但下次重载就读到错内容）。
    与 mmatlib.unique_stem 同一个思路。
    """
    if not game_root or not rel:
        return rel
    if not os.path.exists(rel_to_abs(game_root, rel)):
        return rel
    m = _REL_SEQ_RE.search(rel)
    if not m:
        # 不认识的形式（手写的 ts）：直接补一个时间后缀
        return rel[:-len(".mdf2")] + "_" + _stamp() + ".mdf2" if rel.endswith(".mdf2") else rel + "_" + _stamp()
    ts = m.group(1)
    i = int(m.group(2) or 0) + 1
    head = rel[:m.start()]
    while True:
        cand = "%s_p%s_%d.mdf2" % (head, ts, i)
        if not os.path.exists(rel_to_abs(game_root, cand)):
            return cand
        i += 1


def new_edit_rel(mh, dh, ts=None, game_root=None):
    """新的工作副本路径（不带 `.10`）。给 game_root 时保证磁盘上不重名。"""
    rel = posixpath.join(TEMP_DIR, "edit", mh, "%s_p%s.mdf2" % (dh, ts or _stamp()))
    return _unique_rel(game_root, rel)


def new_orig_rel(dh, ts=None, game_root=None):
    """新的重置副本路径（不带 `.10`）。给 game_root 时保证磁盘上不重名。"""
    rel = posixpath.join(TEMP_DIR, "orig", "%s_p%s.mdf2" % (dh, ts or _stamp()))
    return _unique_rel(game_root, rel)


def parse_temp_rel(rel):
    """从 materialTemp 下的路径反推身份，认不出返回 None。

    支持两种（都是本模块生成的）：

        materialTemp/edit/<meshHash>/<mdf2Hash>_p<ts>.mdf2
        materialTemp/orig/<mdf2Hash>_p<ts>.mdf2

    用途：sessions.json 丢了（手动清理 / 旧版流程）时还能把当前挂着的副本
    「接管」回来，而不用重启游戏。
    """
    s = norm_rel(rel)
    if not s.lower().startswith(TEMP_DIR.lower() + "/"):
        return None
    parts = s[len(TEMP_DIR) + 1:].split("/")
    if len(parts) == 3 and parts[0] == "edit":
        m = _TS_RE.search(parts[2])
        if m and "_p" in parts[2]:
            return {"kind": "edit", "mh": parts[1],
                    "dh": parts[2].split("_p")[0], "ts": m.group(1)}
    if len(parts) == 2 and parts[0] == "orig":
        m = _TS_RE.search(parts[1])
        if m and "_p" in parts[1]:
            return {"kind": "orig", "mh": None,
                    "dh": parts[1].split("_p")[0], "ts": m.group(1)}
    return None


def _missing_mdf2_msg(path, rel):
    """两处都找不到 mdf2 时的报错文案。

    查找顺序：游戏根 ``natives/x64`` → 登记的额外源目录 → **从 pak 按需提取**。
    三者都没有才报错（通常是游戏版本不一致 / pak 里本来就没这个资源）。
    """
    return (
        "找不到 mdf2：\n%s\n\n"
        "资源路径：%s\n\n"
        "已经试过：游戏根 natives/x64、登记的额外源目录、从 re_chunk_*.pak 提取。\n"
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


def create_edit(game_root, mh, dh, base_mdf2, log=None):
    """新建一份 edit 副本（内容 = base_mdf2 的字节）。

    返回相对路径（``materialTemp/edit/<mh>/<dh>_p<ts>.mdf2``，**不带** `.10`），
    磁盘上实际写的是 ``....mdf2.10`` —— 与 ``/material apply`` 的约定一致：
    相对路径不带扩展名，引擎自己补。
    """
    src = ensure_mdf2(game_root, base_mdf2, log=log)
    rel = new_edit_rel(mh, dh, game_root=game_root)
    dst = rel_to_abs(game_root, rel)
    n = _copy(src, dst)
    if log:
        log("edit 副本 %s  (%d B)" % (rel, n))
    return rel


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

def read_params(game_root, rel_edit, material=None):
    """读 mdf2 副本里的参数值 → ``{材质名: {参数名: [float...]}}``。

    注意这是**文件里**的值，不是引擎内存里的当前值（后者要用
    ``/probe?what=params``）。给 material 时只返回该材质。
    """
    src = rel_to_abs(game_root, rel_edit)
    if not os.path.exists(src):
        raise RuntimeError("mdf2 副本不存在：%s" % src)
    doc = mdf2lib.Mdf2.load(src)
    out = {}
    for mat in doc.materials:
        if material and mat.name != material:
            continue
        out[mat.name] = {pr.name: list(pr.values) for pr in mat.properties}
    return out


def save_params(game_root, rel_edit, changes, log=None):
    """把 changes **原地**写进一份**新路径**副本，返回 ``{rel, bytes, touched}``。

    ``changes`` = ``{材质名: {参数名: [float...]}}``（值个数必须与 mdf2 一致）。

    为什么必须换新路径：引擎按路径缓存 mdf2，同路径改了文件不会重载
    （见 analysis/runtime_preview_plan.md §13.4）。新路径 = 同一个
    ``edit/<mh>/<dh>_p<新时间戳>.mdf2``，dh 不变，只换时间戳。

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
    for mat_name, props in changes.items():
        mat = doc.get_material(mat_name)
        if mat is None:
            raise mdf2lib.ParamWriteError(
                "mdf2 里没有材质 %r（现有: %s）"
                % (mat_name, ", ".join(m.name for m in doc.materials)))
        touched += mdf2lib.patch_param_floats(data, mat, props)

    parsed = parse_temp_rel(rel_edit)
    if not parsed or parsed.get("kind") != "edit":
        raise RuntimeError("不是可识别的 edit 副本路径：%s" % rel_edit)

    # 新路径：同 dh、新时间戳。同秒多次保存时逐秒后挪，避免撞名
    new_rel = new_edit_rel(parsed["mh"], parsed["dh"])
    guard = 0
    while (new_rel.lower() == rel_edit.lower()
           or os.path.exists(rel_to_abs(game_root, new_rel))):
        guard += 1
        if guard > 8:
            raise RuntimeError("无法生成新路径（时间戳冲突）：%s" % new_rel)
        new_rel = new_edit_rel(
            parsed["mh"], parsed["dh"],
            ts=time.strftime("%Y%m%d_%H%M%S", time.localtime(time.time() + guard)))

    dst = rel_to_abs(game_root, new_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(bytes(data))

    # 校验：重新 load 逐值比对
    try:
        chk = mdf2lib.Mdf2.load(dst)
        for mat_name, props in changes.items():
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
        log("写盘 %s  (%d B, %d 个参数)" % (new_rel, len(data), len(touched)))
    return {"rel": new_rel, "bytes": len(data), "touched": touched}



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


def update_edit_paths(idx, old_rel, new_rel):
    """把**所有**借旧副本路径的登记项改成新路径，返回改了哪几条 ``[(mh, dh)]``。

    为什么不是只改当前 dh：同一份文件可能被两条登记链到（历史遗留：同一个 mdf2
    在正常 / 湿身两种状态下各被登记过一次），只改一条会留下指向已删文件的死引用。
    """
    old = norm_rel(old_rel).lower()
    out = []
    for mh, o in (idx.get("objects") or {}).items():
        for dh, e in (o.get("mdfs") or {}).items():
            if norm_rel(e.get("edit") or "").lower() == old:
                e["edit"] = new_rel
                e["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                out.append((mh, dh))
    return out


def heal_missing_edits(game_root, idx, mh, good_rel):
    """把某个模型下指向**已不存在文件**的 edit 登记改到 good_rel，返回 s[(mh, dh)]。

    起因（实测）：同一个 mdf2 在正常 / 湿身两种状态下各被登记过一次，早期保存
    只更新了当前 dh，另一条就变成指向已删文件的死引用 —— 下次点勾选时才报
    「登记的副本已丢失，重新生成」并白白重建一次。
    """
    o = (idx.get("objects") or {}).get(mh) or {}
    out = []
    for dh, e in (o.get("mdfs") or {}).items():
        rel = norm_rel(e.get("edit") or "")
        if not rel or rel.lower() == norm_rel(good_rel).lower():
            continue
        if os.path.exists(rel_to_abs(game_root, rel)):
            continue
        e["edit"] = good_rel
        e["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out.append((mh, dh))
    return out


def exists_edit(game_root, rel):
    return bool(rel) and os.path.exists(rel_to_abs(game_root, rel))
