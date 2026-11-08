#!/usr/bin/env python3
"""mmtr 最小写入：同尺寸 blob 替换（Stage B 第一步）。

只做一件事
----------
把某条记录的某个 stage 的 DXBC 容器**原地**换掉（尺寸必须相同），并重算
blob 后的 8 字节缓存键（MHWS；DMC5 没有）。不改偏移、不改尺寸、不改计数，
因此不需要重排，也不影响其他记录。

为什么这是安全的第一步
----------------------
* 容器里没有任何“整文件校验和”，8 字节键只依赖 blob 自身（`xxh64(seed=0xFFFFFFFF)`）；
* 记录里的 (ptr, size) 不变 ⇒ 描述符表、名称池、其他 blob 全部不受影响；
* 替换后可以立刻自校验：`blob_cache_key(new) == 写回的键`。

**注意：blob 是跨记录共享的**（MHWS 里 585 个 blob 中 498 个被多条记录引用，最多 88 条）。
本函数按 (ptr, size) 原地替换，所以**所有引用它的记录会一起变**。若只想改一条记录的
program，需要先给这条记录复制一份 blob——那属于“改尺寸/新增 blob”，不在本函数范围。
先调用 `blob_refs()` 看清共享范围。

不做（留给后续 Stage）
----------------------
* 改尺寸（会导致后续 blob 全部位移，需要重排 + 重写所有指针）；
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
