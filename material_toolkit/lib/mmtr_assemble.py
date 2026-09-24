#!/usr/bin/env python3
"""mmtr 装配器: 从模板 + 程序安装规格 -> 新 mmtr(路线 B 的可用形态)。

因 B2(头部内容字段全推导)受阻, "从0构建 mmtr"采用 **模板 + 移植 + 装配**:
以模板的 [0,0x46350) 骨架为底(L0~L3 结构照抄), 把(可能是全新的)DXBC 程序装入
blob 区, 并重指对应的变体槽(record)程序指针; 其余内容照旧, 装配出新 mmtr。

规格 = 一组 `ProgramInstall`。核心动作:
  - 解析 `ProgramInstall.source`(`BlobSource`: transport/dxbc/asm) -> 规范化 DXBC;
  - **追加到 blob 区末尾**(不移动既有 blob ⇒ 既有绝对偏移全部保持有效);
  - 把"角色指向 `src_blob` 的槽"(或显式 `slots`)程序指针指到新 blob;
  - PS 同步 `+0x9c`、VS 同步 `+0x88/+0x8c`(该槽程序字节码大小);
  - 可选(`recount=True`)按该槽 PS/VS 的 RDEF **重算计数/打包字段**
    (`+0xa4/+0xa8/+0xac/+0xb4/+0xb8/+0xc4`..., 见 `mmtr_build.recount_slot`);
  - 可选(`sync=True`)从"**同组**原生 donor 槽"(按 `(desc,pool)` 匹配)同步绑定指针+计数。

两种安装方式(`ProgramInstall.in_place`):
  - **append**(默认): 追加重指 —— 非破坏性, 可一次装多个不同程序; 副作用: 原 blob 变成未引用的死 blob、
    blob 序号增位。
  - **in_place=True**: 用 `rdef.replace_blob` **就地改写 `src_blob` 本身**(blob 数不变、无死 blob、无需重指);
    更符合“改某个 shader 就替换它”。需给 `src_blob`。

限制(v1): 假设新程序与源程序**资源布局同构**(典型: 同一 shader 改指令后装回)。
同布局时 `sync=False` 即可(槽的绑定/计数本就对应该程序); 跨布局需要"目标程序的 donor",
而新 blob 无原生 donor ⇒ 不在 v1 范围。
"""
try:
    from .mmtr_build import MmtrImage, REC_N, ROLE_FIELDS
    from .mmtr_blobs import BlobSource, list_blobs
    from .rdef import replace_blob
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mmtr_build import MmtrImage, REC_N, ROLE_FIELDS
    from mmtr_blobs import BlobSource, list_blobs
    from rdef import replace_blob

ROLES = ("PS", "VS", "CS")


class ProgramInstall(object):
    """一条"把某个程序装进 mmtr"的规格。

    role      "PS"/"VS"/"CS"
    source    BlobSource(transport/dxbc/asm) —— 要装入的程序
    src_blob  被替换的程序在模板里的 blob 下标(用于定位"哪些槽"+同步绑定/计数)
    slots     显式目标槽下标(与 src_blob 二选一; 给定时忽略 src_blob 的自动定位)
    sync      True: 从"同组(desc,pool)原生 donor"同步绑定指针+计数(默认 False)
    recount   True: 装配后按该槽 PS/VS 的 RDEF 重算计数/打包字段(默认 False)
    in_place  True: 就地改写 src_blob 本身(blob 数不变/无死 blob); 默认 False(追加+重指)
    """

    def __init__(self, role, source, src_blob=None, slots=None, sync=False,
                 recount=False, in_place=False):
        if role not in ROLES:
            raise ValueError(f"role 必须是 {ROLES}")
        if in_place:
            if src_blob is None:
                raise ValueError("in_place 需给出 src_blob")
        elif src_blob is None and slots is None:
            raise ValueError("需给出 src_blob 或 slots 以定位目标槽")
        if not isinstance(source, BlobSource):
            raise TypeError("source 必须是 BlobSource")
        self.role = role
        self.source = source
        self.src_blob = src_blob
        self.slots = list(slots) if slots is not None else None
        self.sync = sync
        self.recount = recount
        self.in_place = in_place


def blob_offsets(template):
    """模板里各 blob 的绝对偏移(按顺序)。"""
    return [off for off, _sz in list_blobs(template)]


def _target_slots(image, inst, src_off):
    if inst.slots is not None:
        return list(inst.slots)
    return image.slots_using(src_off, inst.role)


def _donor_map(image, role, src_off):
    """(desc,pool) -> 首个"该角色指向 src_off"的槽(donor); 仅 sync=True 时用。"""
    if src_off is None:
        return {}
    fields = ROLE_FIELDS[role]
    out = {}
    for s in range(REC_N):
        if any(image.rec_field(s, fo) == src_off for fo in fields):
            key = (image.rec_field(s, 0x58), image.rec_field(s, 0x60))
            out.setdefault(key, s)
    return out


def _recount_slots_using(data, off):
    """把"任一角色程序指针 == off"的槽按 RDEF 重算计数/打包字段, 返回新 bytes。"""
    image = MmtrImage.from_bytes(data)
    flds = [fo for f in ROLE_FIELDS.values() for fo in f]
    changed = False
    for s in range(REC_N):
        if any(image.rec_field(s, fo) == off for fo in flds):
            if image.recount_slot(s) is not None:
                changed = True
    return image.to_bytes() if changed else data


def assemble(template, installs):
    """按规格装配新 mmtr。

    template: 模板 mmtr 的 bytes; installs: [ProgramInstall, ...]。
    返回新 mmtr 的 bytes。append 安装追加新 blob 并重指对应槽;
    in_place 安装就地改写 src_blob(可混合; 按列表顺序依次应用)。
    """
    data = template
    for inst in installs:
        dxbc = inst.source.resolve()
        if inst.in_place:
            data = replace_blob(data, inst.src_blob, bytes(dxbc))
            if inst.recount:
                data = _recount_slots_using(data, blob_offsets(data)[inst.src_blob])
            continue
        image = MmtrImage.from_bytes(data)
        bs = image.blob_start
        offs = blob_offsets(data)
        src_off = offs[inst.src_blob] if inst.src_blob is not None else None
        slots = _target_slots(image, inst, src_off)
        donors = _donor_map(image, inst.role, src_off) if inst.sync else {}
        new_off = bs + len(image.blobs)          # 追加点(既有 blob 偏移不变)
        image.blobs += bytes(dxbc)
        size = len(dxbc) if inst.role in ("PS", "VS") else None
        for s in slots:
            key = (image.rec_field(s, 0x58), image.rec_field(s, 0x60))
            d = donors.get(key)
            if d is not None:
                image.sync_binding_from(s, d, role=inst.role,
                                        blob_off=new_off, size=size,
                                        recount=inst.recount)
            else:
                image.set_program(s, inst.role, new_off, size=size,
                                  recount=inst.recount)
        data = image.to_bytes()
    return data
