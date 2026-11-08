#!/usr/bin/env python3
"""mmtr 写入：同尺寸替换 + 改尺寸重排（Stage B）。

两个入口
--------
`replace_blob_at()` / `replace_blob()`
    把某个 blob **原地**换掉（尺寸必须相同），重算 8 字节缓存键。不改偏移。
`resize_blob_at()` / `resize_blob()`
    **任意尺寸**：按顺序重建 blob 区、平移所有 stage 指针、重算全部 8 字节键。
    这是“换一个尺寸不同的 shader”的唯一入口。

为什么改尺寸是干净的（偏移普查，2026-11-09）
--------------------------------------------
全文件头部区里**只有记录里的 7 个 stage 指针**指向 blob 区；
描述符表/名称池/字符串池全部指向头部区，不需要平移；文件尾也没有索引。
所以重排 = 平移这些指针 + 改目标记录的尺寸 + 重算键。

前置校验：首块必须从 `blob_start` 开始、彼此连续、末块 + trailer == 文件尾；
否则拒绝（宁可不写）。改写前建议先 `blob_refs()` 看共享范围 ——
blob 跨记录共享，改一个就是改所有引用它的记录。

不做（留给后续 Stage）
----------------------
* 新增/删除 blob（容器尾部没有索引可挂）；
* 改绑定表、状态块、声明块（这些块的尾哈希算法未解，只能整块复制）；
* 改 per-stage 计数（同尺寸替换不改变绑定数量）。

用法（库）
----------
    from material_toolkit.lib import game_profile as GP, mmtr_patch as PT
    view = GP.resolve(data, 240906211)
    out, info = PT.replace_blob(data, view, rec_index=12, stage="ps", new_blob=blob)
    out, info = PT.replace_blob_at(data, view, ptr, size, blob)   # 按 blob 位置
"""
import struct

try:
    from .hashes import blob_cache_key
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hashes import blob_cache_key

DXBC_MAGIC = b"DXBC"


class PatchError(ValueError):
    """写入前置条件不满足（尺寸不符 / 不是 DXBC / 越界）。"""


def _check_blob(new_blob, size):
    if not isinstance(new_blob, (bytes, bytearray, memoryview)):
        raise PatchError("new_blob 必须是 bytes")
    if len(new_blob) != size:
        raise PatchError("尺寸不同：%d != %d（改尺寸需要重排，不属于本函数）"
                         % (len(new_blob), size))
    if bytes(new_blob[:4]) != DXBC_MAGIC:
        raise PatchError("不是 DXBC 容器（magic=%r）" % bytes(new_blob[:4]))


def patch_blob_at(buf, view, ptr, size, new_blob):
    """把 `buf`（bytearray）里 ptr..ptr+size 换成 new_blob，并重算缓存键。

    就地修改 `buf`，返回 info dict。
    """
    _check_blob(new_blob, size)
    if ptr <= 0 or ptr + size > len(buf):
        raise PatchError("blob 区间越界：ptr=0x%X size=%d len=%d" % (ptr, size, len(buf)))
    if ptr < view.table_end:
        raise PatchError("ptr=0x%X 落在记录表/头部区（应指向 blob 区）" % ptr)
    start = view.blob_start
    if ptr < start:
        raise PatchError("ptr=0x%X 在 blob 起点 0x%X 之前" % (ptr, start))

    buf[ptr:ptr + size] = bytes(new_blob)
    trailer = view.profile.blob_trailer
    key = None
    if trailer:
        key = blob_cache_key(bytes(new_blob))
        if ptr + size + trailer > len(buf):
            raise PatchError("缓存键越界：0x%X + %d + %d > %d"
                             % (ptr, size, trailer, len(buf)))
        struct.pack_into("<Q", buf, ptr + size, key)
    return {"ptr": ptr, "size": size, "trailer": trailer, "cache_key": key}


def replace_blob_at(data, view, ptr, size, new_blob):
    """按 blob 位置替换，返回 (新 bytes, info)。不改动入参 data。"""
    buf = bytearray(data)
    info = patch_blob_at(buf, view, ptr, size, new_blob)
    return bytes(buf), info


def record_blob(data, view, rec_index, stage):
    """取某条记录某 stage 的 (ptr, size, blob)；没有程序返回 (0, 0, b"")。"""
    if not (0 <= rec_index < view.n_records):
        raise PatchError("记录下标越界：%d（0..%d）" % (rec_index, view.n_records - 1))
    slot = view.profile.stage(stage)
    rec = view.rec_base + rec_index * view.rec_stride
    ptr = struct.unpack_from("<Q", data, rec + slot.ptr_off)[0]
    size = struct.unpack_from("<I", data, rec + slot.size_off)[0]
    if not ptr or not size:
        return 0, 0, b""
    return ptr, size, bytes(data[ptr:ptr + size])


def replace_blob(data, view, rec_index, stage, new_blob):
    """按 (记录, stage) 替换，返回 (新 bytes, info)。"""
    ptr, size, _old = record_blob(data, view, rec_index, stage)
    if not ptr:
        raise PatchError("记录 %d 的 %s 没有程序" % (rec_index, stage))
    out, info = replace_blob_at(data, view, ptr, size, new_blob)
    info.update({"rec_index": rec_index, "stage": stage})
    return out, info


def blob_refs(data, view, ptr):
    """列出所有引用 ptr 的 (rec_index, stage)（用于判断共享范围）。"""
    out = []
    for i in range(view.n_records):
        rec = view.rec_base + i * view.rec_stride
        for slot in view.profile.stages:
            p = struct.unpack_from("<Q", data, rec + slot.ptr_off)[0]
            if p == ptr and ptr:
                out.append((i, slot.key))
    return out


def verify_trailers(data, view):
    """校验所有 blob 后的缓存键 -> (ok, bad, details)。DMC5 直接返回 (0, 0, [])。"""
    if not view.profile.blob_trailer:
        return 0, 0, []
    try:
        from .mmtr_scan import blob_list
    except ImportError:  # pragma: no cover
        from mmtr_scan import blob_list
    ok = bad = 0
    details = []
    for ptr, size in blob_list(data, view):
        want = struct.unpack_from("<Q", data, ptr + size)[0]
        got = blob_cache_key(bytes(data[ptr:ptr + size]))
        if got == want:
            ok += 1
        else:
            bad += 1
            if len(details) < 5:
                details.append((ptr, size, want, got))
    return ok, bad, details


# ---------------------------------------------------------------- 改尺寸重排
def _blob_layout(data, view):
    """blob 区按位置排序的 [(ptr, size)]，并校验“首块在 blob_start、彼此连续、到文件尾”。"""
    try:
        from .mmtr_scan import blob_list
    except ImportError:  # pragma: no cover
        from mmtr_scan import blob_list
    blobs = blob_list(data, view)
    if not blobs:
        raise PatchError("没有找到 blob")
    trailer = view.profile.blob_trailer
    cur = view.blob_start
    for ptr, size in blobs:
        if ptr != cur:
            raise PatchError("blob 区不连续：期望 0x%X，实际 0x%X" % (cur, ptr))
        cur = ptr + size + trailer
    if cur != len(data):
        raise PatchError("blob 区未到文件尾：0x%X != %d" % (cur, len(data)))
    return blobs


def resize_blob_at(data, view, ptr, new_blob):
    """把 ptr 处的 blob 换成**任意尺寸**，返回 (新 bytes, info)。

    重排 = 重建 blob 区 + 平移所有 stage 指针 + 重算全部 8 字节键。
    只更新引用目标 blob 的记录的尺寸；其余记录除指针外不动。
    """
    if not isinstance(new_blob, (bytes, bytearray, memoryview)):
        raise PatchError("new_blob 必须是 bytes")
    new_blob = bytes(new_blob)
    if new_blob[:4] != DXBC_MAGIC:
        raise PatchError("不是 DXBC 容器（magic=%r）" % new_blob[:4])
    blobs = _blob_layout(data, view)
    sizes = dict(blobs)
    if ptr not in sizes:
        raise PatchError("ptr=0x%X 不是 blob 起点" % ptr)
    trailer = view.profile.blob_trailer

    # 1) 按顺序重建 blob 区（内容不变的块直接搬，键按内容重算）
    out = bytearray(data[:view.blob_start])
    pos = {}
    cur = view.blob_start
    keyed = 0
    for p, s in blobs:
        body = new_blob if p == ptr else bytes(data[p:p + s])
        pos[p] = cur
        out += body
        cur += len(body)
        if trailer:
            out += struct.pack("<Q", blob_cache_key(body))
            cur += trailer
            keyed += 1
    if cur != len(out):
        raise PatchError("重建后长度不符：%d != %d" % (cur, len(out)))

    # 2) 平移记录里的指针，并把引用目标的记录尺寸改成新尺寸
    new_size = len(new_blob)
    slots = 0
    touched = set()
    for i in range(view.n_records):
        rec = view.rec_base + i * view.rec_stride
        rec_hit = False
        for slot in view.profile.stages:
            o = rec + slot.ptr_off
            old = struct.unpack_from("<Q", out, o)[0]
            if not old:
                continue
            np = pos.get(old)
            if np is None:
                raise PatchError("记录 %d %s 指向未知 blob 0x%X" % (i, slot.key, old))
            if np != old:
                struct.pack_into("<Q", out, o, np)
            if old == ptr:
                struct.pack_into("<I", out, rec + slot.size_off, new_size)
                slots += 1
                rec_hit = True
        if rec_hit:
            touched.add(i)

    info = {"old_ptr": ptr, "new_ptr": pos[ptr], "old_size": sizes[ptr],
            "new_size": new_size, "delta": new_size - sizes[ptr],
            "trailer": trailer, "trailers_recomputed": keyed,
            "stage_slots_updated": slots, "records_updated": len(touched)}
    return bytes(out), info


def resize_blob(data, view, rec_index, stage, new_blob):
    """按 (记录, stage) 改尺寸替换，返回 (新 bytes, info)。"""
    ptr, size, _old = record_blob(data, view, rec_index, stage)
    if not ptr:
        raise PatchError("记录 %d 的 %s 没有程序" % (rec_index, stage))
    out, info = resize_blob_at(data, view, ptr, new_blob)
    info.update({"rec_index": rec_index, "stage": stage})
    return out, info
