#!/usr/bin/env python3
"""MHWS mmtr 头部模型：段划分 + 结构化指针表 + 无损重建。

为什么需要它
------------
`mmtr.py` / `mmtr_patch.py` 各只覆盖一部分：前者用 4B 扫描猜参数条目，
后者只平移 7 个 stage 指针。要做「重建 mmtr」（增删技术 / 参数 / 贴图槽），
必须有一份**权威的段划分 + 指针位置表**，否则改长度后总会漏掉一批指针。

段序（MHWS 240906211 实测；连续、无 padding）
----------------------------------------------
    header     16B     "SDF\\0" + rows u16 + cols u16 + blob_start u32 + pad u32
    records    n×0x110 记录表
    shared     ?       共享 GPU 状态块（输入布局/光栅/混合/深度，无指针）
    names      ?×16    描述符名字池（name_off u64 | hash u32 | 0）
    cbtable    ?×32    cbuffer 表（name 16B + desc 16B: size u32 | bind u32 | members u64）
    params     ?×16    参数定义表（name_off u32 | 0 | hash u32 | size u16|offset u16）
    decl       ?       绑定声明块（纯数值）
    strings    ?       字符串池（\\0 分隔）
    blobs      ?       DXBC 容器 + 8B 尾部

指针清单（实机标定：sig 恒等于 vs，2090/2090）
------------------------------------------------
    记录 +0x00            name        -> strings
    记录 +0x08..+0x38     7 个程序槽   -> blobs（0x08 与 0x10 同值）
    记录 +0x40/48/50/58   4 个状态     -> shared
    记录 +0x60            decl        -> decl
    记录 +0x68/78/88/98/A8 5 个描述符数组 -> decl
    记录 +0x70/80/90/A0/B0 5 个名字池     -> cbtable / names
    names 每项 +0x00 (u64)               -> strings
    cbtable 每项 +0x00 (u64) / +0x18(u64) -> strings / params
    params 每项 +0x00 (u32)              -> strings

用法
----
    from material_toolkit.lib import mmtr_head as HEAD
    m = HEAD.parse(data)                  # 自动探测段
    m.dump()                              # 段表 + 指针统计
    HEAD.verify(data)                     # identity rebuild 逐字节比对
    new = m.rebuild({"names": m.span("names") + 16 bytes})   # 改长后全量重映射
"""
from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass

__all__ = ["HeadError", "Section", "HeadModel", "parse", "verify", "probe_starts"]

_U64 = struct.Struct("<Q")
_U32 = struct.Struct("<I")


class HeadError(ValueError):
    pass


@dataclass
class Section:
    name: str
    start: int
    end: int
    kind: str            # header/records/shared/names/cbtable/params/decl/strings/blobs
    item: int = 0        # 条目字节数（records/names/cbtable/params）
    count: int = 0

    @property
    def size(self):
        return self.end - self.start


@dataclass
class HeadModel:
    data: bytes
    view: object                       # game_profile.MmtrView
    sections: list                     # [Section]（按地址升序、连续）
    pointers: list                     # [(pos, width)] 头部内的结构指针位置

    # ------------------------------------------------------------ 查询
    def _index(self):
        self._starts = [s.start for s in self.sections]
        self._by_name = {s.name: s for s in self.sections}

    def section_of(self, pos):
        """返回包含 pos 的段（半开区间 [start, end)）。"""
        if not hasattr(self, "_starts"):
            self._index()
        i = bisect.bisect_right(self._starts, pos) - 1
        if i < 0:
            return None
        s = self.sections[i]
        return s if pos < s.end else None

    def span(self, name):
        return self.data[self._by_name[name].start:self._by_name[name].end]

    def target_of(self, val, prefer_prev_end=False):
        """指针目标段。

        `prefer_prev_end=True`：若 val 恰好等于某段的末尾，归**前一段**。
        只给「编辑字节内新增的指针」用——它们常指向「某段旧的末尾」（插入前
        不存在的位置）；普通指针不能这么判，否则指向下一段起点的指针会被误归。
        """
        if prefer_prev_end:
            if not hasattr(self, "_starts"):
                self._index()
            for s in self.sections:
                if s.end == val:
                    return s
        return self.section_of(val)

    def sec(self, name):
        return self._by_name[name]

    def dump(self):
        lines = ["segments (%d):" % len(self.sections)]
        prev = None
        for s in self.sections:
            gap = "" if prev is None or prev == s.start else "  GAP=0x%X" % (s.start - prev)
            extra = ""
            if s.item:
                extra = "  item=0x%X count=%d" % (s.item, s.count)
            lines.append("  0x%06X..0x%06X %-8s %-8s size=0x%-7X%s%s"
                         % (s.start, s.end, s.name, s.kind, s.size, extra, gap))
            prev = s.end
        lines.append("pointers: %d" % len(self.pointers))
        by_sec = {}
        for pos, _w in self.pointers:
            s = self.section_of(pos)
            by_sec[s.name if s else "?"] = by_sec.get(s.name if s else "?", 0) + 1
        for k in sorted(by_sec):
            lines.append("   %-8s %d" % (k, by_sec[k]))
        return "\n".join(lines)

    # ------------------------------------------------------------ 重建
    def rebuild(self, overrides=None, edits=None):
        """按段重建文件。

        `overrides` = {段名: 新字节}：整段替换（长度可变，段内偏移不变）。
        `edits`     = {段名: [(旧坐标起点, 删除长度, 新字节, [(字节内指针相对偏移, 宽度)])]}：
                      段内插入（删 0）/ 删除（新字节空）/ 替换；编辑点**之后的**
                      旧指针自动按净增量平移，编辑字节里的指针按**旧坐标系**写。

        两者对同一段互斥。参数表/名字池这类「段内多个池首尾相接」的结构
        必须用 edits（否则编辑点后的指针会指错）。
        """
        overrides = overrides or {}
        edits = edits or {}
        names = {s.name for s in self.sections}
        bad = (set(overrides) | set(edits)) - names
        if bad:
            raise HeadError("未知段: %s" % ", ".join(sorted(bad)))

        out = bytearray()
        plan = {}          # name -> (new_start, old_start, new_len, edits)
        edit_abs = []      # [(out 内位置, body, rels)]
        edit_out = {}      # (段名, 旧坐标起点) -> 该编辑输出在 out 里的起点
        for s in self.sections:
            items = sorted(edits.get(s.name) or [], key=lambda x: (x[0], x[1]))
            ov = overrides.get(s.name)
            if ov is not None and items:
                raise HeadError("段 %s 不能同时用 overrides 和 edits" % s.name)
            new_start = len(out)
            if ov is not None:
                body = bytes(ov)
                if s.item and len(body) % s.item:
                    raise HeadError("段 %s 长度 %d 不是条目宽 0x%X 的整数倍"
                                    % (s.name, len(body), s.item))
                out += body
                plan[s.name] = (new_start, s.start, len(body), [])
                continue
            prev = s.start
            for at, old_len, body, _rels in items:
                if not (s.start <= at <= at + old_len <= s.end):
                    raise HeadError("段 %s 编辑区间 0x%X+%d 越界" % (s.name, at, old_len))
                if at < prev:
                    raise HeadError("段 %s 编辑区间重叠（0x%X）" % (s.name, at))
                if s.item and ((at - s.start) % s.item or old_len % s.item):
                    raise HeadError("段 %s 编辑点 0x%X / 长度 %d 未对齐到 0x%X"
                                    % (s.name, at, old_len, s.item))
                prev = at + old_len
            plan[s.name] = (new_start, s.start,
                            s.size + sum(len(b) - n for _, n, b, _ in items), items)
            prev = s.start
            for at, old_len, body, rels in items:
                out += self.data[prev:at]
                edit_out[(s.name, at)] = len(out)
                edit_abs.append((len(out), body, rels))
                out += body
                prev = at + old_len
            out += self.data[prev:s.end]

        def remap(sec_name, old, is_new=False):
            """旧绝对地址 -> 新绝对地址（含段内编辑的净位移）。

            `is_new=True` 用于**编辑字节内**的指针：
              * 落在某个旧插入点的**新增内容范围**内 -> 映射到该内容的新位置；
              * 恰好等于某个旧插入点 -> 指向新插入内容的起点（不累计位移）。
            """
            new_start, old_start, _ln, items = plan[sec_name]
            off = old - old_start
            if is_new:
                for at, n, b, _ in items:
                    a = at - old_start
                    if b and a <= off < a + len(b):
                        return edit_out[(sec_name, at)] + (off - a)
            extra = sum(len(b) - n for at, n, b, _ in items
                        if (at - old_start) < off
                        or (not is_new and (at - old_start) == off))
            return new_start + off + extra

        # 结构指针
        for pos, width in self.pointers:
            src = self.section_of(pos)
            if src is None:
                raise HeadError("指针位置 0x%X 不在任何段内" % pos)
            off = pos - src.start
            ov = overrides.get(src.name)
            if ov is not None and off + width > len(ov):
                # 该条目所在的尾部已被 overrides 缩短删掉
                continue
            if any(at <= pos < at + n
                   for at, n, _b, _r in plan[src.name][3]):
                # 指针所在条目本身被编辑区间删除
                continue
            old = _read(self.data, pos, width)
            if not old:
                continue
            tgt = self.target_of(old)
            if tgt is None:
                raise HeadError("指针 0x%X -> 0x%X 目标不在段内" % (pos, old))
            _write(out, remap(src.name, pos), remap(tgt.name, old), width)

        # 编辑字节内的指针（值按旧坐标系给出）
        def find_new_target(old):
            """新增指针的目标段。优先匹配「某编辑的新增内容范围」（旧坐标里越界的位置）。"""
            for s2 in self.sections:
                for at, _n, b, _r in plan[s2.name][3]:
                    if b and at <= old < at + len(b):
                        return s2, at
            return self.target_of(old, prefer_prev_end=True), None

        for abs_pos, body, rels in edit_abs:
            for rel, width in (rels or ()):
                old = _read(body, rel, width)
                if not old:
                    continue
                tgt, hit_at = find_new_target(old)
                if tgt is None:
                    raise HeadError("新增条目指针 0x%X 目标不在段内" % old)
                if hit_at is not None:
                    new_val = edit_out[(tgt.name, hit_at)] + (old - hit_at)
                else:
                    new_val = remap(tgt.name, old, is_new=True)
                _write(out, abs_pos + rel, new_val, width)

        # 文件头的 blob_start 恒等于 blobs 段起点
        out[8:12] = struct.pack("<I", plan["blobs"][0])
        return bytes(out)


def _read(data, pos, width):
    return _U64.unpack_from(data, pos)[0] if width == 8 else _U32.unpack_from(data, pos)[0]


def _write(buf, pos, value, width):
    (_U64 if width == 8 else _U32).pack_into(buf, pos, value)


# ---------------------------------------------------------------- 探测
def _cstr(data, off, limit=96):
    end = off
    while end < len(data) and end - off < limit and data[end]:
        end += 1
    return data[off:end] if end > off else b""


def _is_name_entry(data, off, lo, hi):
    """16B 名字条目：name_off(u64) 指向 [lo,hi)、type(u32)==0、名字可读。"""
    if off + 16 > len(data):
        return False
    a, _h, t = struct.unpack_from("<QII", data, off)
    if t != 0 or not (lo <= a < hi):
        return False
    name = _cstr(data, a, 64)
    return 3 <= len(name) <= 96


def probe_starts(data, view):
    """用记录里的引用反推各段起点（权威来源，不靠内容猜测）。"""
    from . import mmtr_scan as SC

    te, bs = view.table_end, view.blob_start
    prof = view.profile
    stage_offs = [s.ptr_off for s in prof.stages]
    state_offs = [0x40, 0x48, 0x50, 0x58]
    desc_offs = [d.desc_off for d in prof.desc_tables]
    pool_offs = [d.pool_off for d in prof.desc_tables]

    pools_cb, pools_nc, descs, name_refs = set(), set(), set(), set()
    for e in SC.record_entries(data, view):
        rec = e["off"]
        nm = _U64.unpack_from(data, rec)[0]
        if te <= nm < bs:
            name_refs.add(nm)
        for o in desc_offs:
            v = _U64.unpack_from(data, rec + o)[0]
            if te <= v < bs:
                descs.add(v)
        for i, o in enumerate(pool_offs):
            v = _U64.unpack_from(data, rec + o)[0]
            if te <= v < bs:
                (pools_cb if i == 0 else pools_nc).add(v)
        dv = _U64.unpack_from(data, rec + 0x60)[0]
        if te <= dv < bs:
            descs.add(dv)

    def _min(s, what):
        if not s:
            raise HeadError("无法探测 %s 段起点（无任何引用）" % what)
        return min(s)

    names_start = _min(pools_nc | pools_cb, "names/cbtable")
    cbtable_start = min(pools_cb) if pools_cb else None
    decl_start = _min(descs, "decl")

    # cbtable: 32B/项（name 16B + desc 16B），desc 的 +0x18 是成员表指针
    members = set()
    pos = cbtable_start
    if pos is not None:
        while pos + 32 <= bs and _is_name_entry(data, pos, 0x10, bs):
            mv = _U64.unpack_from(data, pos + 24)[0]
            if 0x10 <= mv < bs:
                members.add(mv)
            pos += 32
        cbtable_end = pos
    else:
        cbtable_end = names_start

    params_start = min(members) if members else decl_start

    # names 段：16B/项，直到 cbtable 起点或首个非名字条目
    pos = names_start
    while pos + 16 <= bs and _is_name_entry(data, pos, 0x10, bs):
        name_refs.add(_U64.unpack_from(data, pos)[0])
        pos += 16
    names_end = pos
    if cbtable_start is not None:
        names_end = min(names_end, cbtable_start)

    strings_start = _min(name_refs, "strings")
    starts = {
        "names": names_start,
        "cbtable": cbtable_start if cbtable_start is not None else cbtable_end,
        "cbtable_end": cbtable_end,
        "params": params_start,
        "decl": decl_start,
        "strings": strings_start,
    }
    return starts


def parse(data, view=None, version=None):
    """解析 MHWS mmtr，返回 HeadModel（段 + 指针表）。"""
    from . import game_profile as GP
    from . import mmtr_scan as SC

    view = view or GP.resolve(data, version)
    prof = view.profile
    if prof.blob_trailer == 0:
        raise HeadError("该 profile 不是 MHWS 布局（blob_trailer=0）")
    if data[:4] != b"SDF\0":
        raise HeadError("不是 SDF/mmtr 文件")

    st = probe_starts(data, view)
    te, bs = view.table_end, view.blob_start
    order = [
        ("names", st["names"]),
        ("cbtable", st["cbtable"]),
        ("params", st["params"]),
        ("decl", st["decl"]),
        ("strings", st["strings"]),
    ]
    for (n1, v1), (n2, v2) in zip(order, order[1:]):
        if v1 is None or v2 is None or not (v1 <= v2):
            raise HeadError("段起点顺序异常：%s=0x%X  %s=0x%X" % (n1, v1 or 0, n2, v2 or 0))

    sections = [
        Section("header", 0, view.rec_base, "header"),
        Section("records", view.rec_base, te, "records", prof.rec_stride, view.n_records),
        Section("shared", te, st["names"], "shared"),
        Section("names", st["names"], st["cbtable"], "names", 16,
                (st["cbtable"] - st["names"]) // 16),
        Section("cbtable", st["cbtable"], st["cbtable_end"], "cbtable", 32,
                (st["cbtable_end"] - st["cbtable"]) // 32),
        Section("params", st["params"], st["decl"], "params", 16,
                (st["decl"] - st["params"]) // 16),
        Section("decl", st["decl"], st["strings"], "decl"),
        Section("strings", st["strings"], bs, "strings"),
        Section("blobs", bs, len(data), "blobs"),
    ]
    # 段间必须连续（这是「头部无 padding」的实测结论）
    for a, b in zip(sections, sections[1:]):
        if a.end != b.start:
            raise HeadError("段 %s 与 %s 之间有 0x%X 字节未归类"
                            % (a.name, b.name, b.start - a.end))

    # ---- 指针位置 ----
    ptr = []
    for i in range(view.n_records):
        rec = view.rec_base + i * prof.rec_stride
        ptr.append((rec, 8))
        for o in [s.ptr_off for s in prof.stages]:
            ptr.append((rec + o, 8))
        for o in (0x40, 0x48, 0x50, 0x58, 0x60):
            ptr.append((rec + o, 8))
        for d in prof.desc_tables:
            ptr.append((rec + d.desc_off, 8))
            ptr.append((rec + d.pool_off, 8))

    names = sections[3]
    for i in range(names.count):
        ptr.append((names.start + i * 16, 8))
    cb = sections[4]
    for i in range(cb.count):
        ptr.append((cb.start + i * 32, 8))
        ptr.append((cb.start + i * 32 + 24, 8))
    pm = sections[5]
    for i in range(pm.count):
        ptr.append((pm.start + i * 16, 4))

    m = HeadModel(data=bytes(data), view=view, sections=sections, pointers=ptr)
    m._index()

    # 自检：每个非零指针都必须落在某个段内（或段尾追加点）
    bad = []
    for pos, width in ptr:
        v = _read(data, pos, width)
        if v and m.target_of(v) is None:
            bad.append((pos, v))
    if bad:
        raise HeadError("%d 个指针目标不在段内，首个：0x%X -> 0x%X"
                        % (len(bad), bad[0][0], bad[0][1]))
    return m


def verify(data, view=None, version=None):
    """identity rebuild 逐字节比对。返回 (ok, info)。"""
    m = parse(data, view, version)
    out = m.rebuild()
    if out == bytes(data):
        return True, {"sections": len(m.sections), "pointers": len(m.pointers)}
    diff = next(i for i in range(min(len(out), len(data))) if out[i] != data[i])
    return False, {"sections": len(m.sections), "pointers": len(m.pointers),
                   "first_diff": diff, "size_out": len(out), "size_in": len(data)}
