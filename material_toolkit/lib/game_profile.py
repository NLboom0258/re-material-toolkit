#!/usr/bin/env python3
"""按游戏 / 资源版本划分的容器布局（mmtr 程序记录表的唯一事实来源）。

实测结论（2026-11-07，DMC5 681 个 + MHWS 962 个 mmtr 的头部扫描）
----------------------------------------------------------------
**记录步长跟"版本"走，与表形状无关；表形状（rows×cols）每个文件都可能不同。**

    资源版本        游戏   记录基址   步长      已知表形状(rows×cols)
    1808168797      DMC5   0x10      0x108     4×272 / 4×228 / 4×104 / 4×36 / 4×33 …
    240906211       MHWS   0x10      0x110     5×479 / 5×542 / 5×918 / 5×10 / 5×84 …
    250206176       MHWS   0x10      0x110     4×463 / 4×486 / 4×914 / 4×10 / 4×908 …

字段偏移三者一致（实测 + 校验器 0 不一致；并与 REasy 的 SDF profile 交叉核对，
`PROGRAM_LAYOUT_0X108` / `PROGRAM_LAYOUT_0X110` 与本模块完全对应）：

    记录: +0x00 名字(u64)
          +0x08/+0xB8 输入签名   +0x10/+0xBC VS   +0x18/+0xC0 HS
          +0x20/+0xC4 DS         +0x28/+0xC8 GS   +0x30/+0xCC PS   +0x38/+0xD0 CS
          +0x40 输入布局   +0x48/+0x50/+0x58 渲染状态   +0x60 紧凑声明
          +0x68/0x78/0x88/0x98/0xA8  5 类描述符表(CB/SMP/SRV/UAV/OPT)
          +0xD4 总数; +0xD8..0xF5 5×6 per-stage 计数; +0xF6/0xF7/0xFC/0xFD/0xFE 表条数
          +0x100 计算组尺寸(*0x110 记录位移到 +0x108*); +0x106 图元拓扑(*0x110: +0x10E*)

新游戏的登记参考（未逐字节验证，仅作调研起点）：RE2/RE3/RE8/MHRISE/DD2(legacy) 用 0x108；
RE9/PRAGMATA/DD2(新版)/ONIMUSHA 用 0x110 且 **header_layout=per-target**（头部 +0x04 起是
每 target 一个 u16 计数再 padding，而非本模块假设的 rows+cols）—— 接入这些游戏前要先把
header 解析也做成 profile 字段。

所以本模块把「版本 → 布局」登记成 profile；「形状」由 `TableShape` 从文件头读取，
两者组合成 `MmtrView`（调用方真正用的对象，带 n_records / table_end / record_offsets）。

历史包袱（只对 DMC5 成立，勿用于新代码）
----------------------------------------
`binding.py` / `mmtr_model.py` / `mmtr_tail.py` / `mmtr_presets.py` 仍在用一套
**264B「偏移窗口」视图**：基址 0x568、步长 264、跳过前 5 条。它其实是早期误判
（0x568 = 真实第 5 条 + 0x30），字段恰好落在真实字段上，所以工作正常，但：

  * 名字字段(+0xD8)实际写到「下一条记录的名字」；
  * 前 5 条基础记录不在窗口内；
  * 步长写死 264，MHWS(0x108/0x110) 完全套不上。

新代码（读数、校验、MHWS 适配）一律走 `MmtrView` 的真实表；旧视图只作为
DMC5 既有编辑路径的 `sdf_view` 兼容保留，不扩散。

用法
----
    from material_toolkit.lib import game_profile as GP
    view = GP.resolve(data)                  # 无版本号：形状 + 步长启发式
    view = GP.resolve(data, 240906211)       # 有文件名后缀时最准
    view.profile.game, view.n_records, hex(view.table_end)
"""
from dataclasses import dataclass
import struct

__all__ = [
    "StageSlot", "DescTable", "SdfView", "MmtrProfile", "TableShape", "MmtrView",
    "DMC5", "MHWS", "PROFILES", "SDF_VIEW", "VERSION_STRIDE",
    "by_version", "resolve", "guess_stride",
]


# ---------------------------------------------------------------- 结构描述
@dataclass(frozen=True)
class StageSlot:
    """记录内的一个程序槽：指针(u64) + 字节大小(u32)。"""
    key: str          # 稳定标识（vs0/vs1/hs/ds/gs/ps/cs）
    label: str        # 展示名
    ptr_off: int
    size_off: int


@dataclass(frozen=True)
class DescTable:
    """记录内的一类资源绑定表：描述符数组 + 名称池 + 条数(u8)。"""
    key: str          # cb / smp / srv / uav / opt
    label: str        # 展示名
    desc_off: int
    pool_off: int
    count_off: int


@dataclass(frozen=True)
class SdfView:
    """DMC5 旧的 264B 偏移窗口（兼容用，勿用于新代码）。"""
    base: int
    stride: int
    skip: int

    def records(self, rows, cols, blob_start):
        """窗口内的记录基址列表（按头部 rows/cols 与 blob 起点收口）。"""
        n = rows * cols - self.skip
        if n <= 0:
            return []
        maxn = max(0, (blob_start - self.base) // self.stride)
        return [self.base + i * self.stride for i in range(min(n, maxn))]


# 5 类资源在真实记录内的偏移（DMC5 / MHWS 实测一致）
_DESC_TABLES = (
    DescTable("cb", "CB", 0x68, 0x70, 0xF6),
    DescTable("smp", "SMP", 0x78, 0x80, 0xF7),
    DescTable("srv", "SRV", 0x88, 0x90, 0xFC),
    DescTable("uav", "UAV", 0x98, 0xA0, 0xFD),
    DescTable("opt", "OPT", 0xA8, 0xB0, 0xFE),
)

# 7 个程序槽：输入签名 + 6 个 D3D 阶段（顶点程序在 0x10；0x08 是输入签名）
_STAGES = (
    StageSlot("sig", "InputSig", 0x08, 0xB8),
    StageSlot("vs", "VS", 0x10, 0xBC),
    StageSlot("hs", "HS", 0x18, 0xC0),
    StageSlot("ds", "DS", 0x20, 0xC4),
    StageSlot("gs", "GS", 0x28, 0xC8),
    StageSlot("ps", "PS", 0x30, 0xCC),
    StageSlot("cs", "CS", 0x38, 0xD0),
)


@dataclass(frozen=True)
class MmtrProfile:
    """一个资源版本的布局（步长 + 字段偏移；不含表形状）。"""
    game: str
    versions: tuple          # 已知资源版本（文件名后缀）
    rec_stride: int
    rec_base: int = 0x10
    name_off: int = 0x00
    count_total_off: int = 0xD4
    count_matrix_off: int = 0xD8
    count_matrix_len: int = 30       # 5 类资源 × 6 stage
    # 渲染状态/输入布局等辅助指针（B2 记录详情页用；与 REasy 的 PROGRAM_LAYOUT_0X108 对齐）
    input_layout_off: int = 0x40
    rasterizer_state_off: int = 0x48
    blend_state_off: int = 0x50
    depth_stencil_state_off: int = 0x58
    compact_declaration_off: int = 0x60
    compact_declaration_count_off: int = 0xF8
    compute_group_size_off: int = 0x100   # 0x110 记录: 0x108（多插一个保留 qword）
    primitive_topology_off: int = 0x106   # 0x110 记录: 0x10E
    blob_trailer: int = 0                 # 每个 blob 后的尾部字节数（MHWS=8，实测）
    stages: tuple = _STAGES
    desc_tables: tuple = _DESC_TABLES
    sdf_view: SdfView | None = None   # 旧 264B 窗口（仅 DMC5）
    source: str = "registered"        # registered / guessed

    def stage(self, key):
        for s in self.stages:
            if s.key == key:
                return s
        raise KeyError(key)

    def desc(self, key):
        for d in self.desc_tables:
            if d.key == key:
                return d
        raise KeyError(key)


@dataclass(frozen=True)
class TableShape:
    """文件头里的表形状（rows×cols）。"""
    rows: int
    cols: int

    @property
    def n_records(self):
        return self.rows * self.cols


@dataclass(frozen=True)
class MmtrView:
    """一次读取的完整布局 = profile(版本) + shape(文件头) + blob 起点。"""
    profile: MmtrProfile
    shape: TableShape
    blob_start: int

    # ---- 常用派生量（调用方直接用，不再自己算） ----
    @property
    def n_records(self):
        return self.shape.n_records

    @property
    def rec_base(self):
        return self.profile.rec_base

    @property
    def rec_stride(self):
        return self.profile.rec_stride

    @property
    def table_end(self):
        """记录表末端（其后的头部区域才是描述符表 / 名称池 / 字符串池）。"""
        return self.rec_base + self.n_records * self.rec_stride

    @property
    def stages(self):
        return self.profile.stages

    @property
    def desc_tables(self):
        return self.profile.desc_tables

    @property
    def count_total_off(self):
        return self.profile.count_total_off

    @property
    def count_matrix_off(self):
        return self.profile.count_matrix_off

    @property
    def count_matrix_len(self):
        return self.profile.count_matrix_len

    def record_offsets(self):
        """真实记录基址列表（以 blob 起点收口，容忍截断文件）。"""
        n = max(0, (self.blob_start - self.rec_base) // self.rec_stride)
        return [self.rec_base + i * self.rec_stride
                for i in range(min(n, self.n_records))]


# ---------------------------------------------------------------- 登记表
DMC5 = MmtrProfile(
    game="DMC5",
    versions=(1808168797,),
    rec_stride=0x108,
    sdf_view=SdfView(0x568, 264, 5),
)

MHWS = MmtrProfile(
    game="MHWS",
    versions=(240906211, 250206176),
    rec_stride=0x110,
    sdf_view=None,
    # 0x110 记录在 0x100 多插一个保留 qword：计算组尺寸与图元拓扑都后移 8。
    compute_group_size_off=0x108,
    primitive_topology_off=0x10E,
    # 每个 DXBC 容器后跟 8 字节（实测；REasy 的 runtime_cache_keys 同一机制）。
    blob_trailer=8,
)

PROFILES = (DMC5, MHWS)

# 版本 -> 步长（实测；新版本先在这里登记，未知版本走启发式）
VERSION_STRIDE = {v: p.rec_stride for p in PROFILES for v in p.versions}

# DMC5 旧的 264B 偏移窗口（binding / mmtr_model / mmtr_tail 兼容用）
SDF_VIEW = DMC5.sdf_view

DEFAULT = DMC5


def by_version(version):
    """按资源版本（文件名后缀）找 profile；未知返回 None。"""
    try:
        v = int(version)
    except (TypeError, ValueError):
        return None
    for p in PROFILES:
        if v in p.versions:
            return p
    return None


def _printable_name(data, off, blob_start, limit=64):
    if not (0x40 <= off < blob_start):
        return False
    end = data.find(b"\x00", off, min(off + limit, blob_start))
    if end < 0:
        return False
    raw = data[off:end]
    if not raw:
        return False
    return all(0x20 <= c < 0x7F for c in raw)


def guess_stride(data, shape, candidates=None):
    """未知版本的步长启发式：采样记录，看 +0x00 是否指向合法 ASCII 名字。

    只用于「版本没登记」的情况（新游戏 / 新补丁第一眼）；有效样本命中率
    < 80% 返回 None。候选先试已知步长（0x108 / 0x110），再线性扫。
    """
    if len(data) < 0x40:
        return None
    blob_start = struct.unpack_from("<I", data, 8)[0]
    if not (0x40 < blob_start <= len(data)):
        return None
    n = shape.n_records
    if n <= 0:
        return None
    cands = list(candidates or (0x108, 0x110))
    if candidates is None:
        cands += [s for s in range(0x100, 0x141, 8) if s not in cands]
    sample = [int(i * (n - 1) / 63) for i in range(64)] if n > 64 else list(range(n))
    best, best_ratio = None, 0.0
    for stride in cands:
        if 0x10 + (n - 1) * stride >= blob_start:
            continue
        hit = valid = 0
        for i in sample:
            off = struct.unpack_from("<Q", data, 0x10 + i * stride)[0]
            if off == 0:
                continue
            valid += 1
            if _printable_name(data, off, blob_start):
                hit += 1
        if valid < 4:
            continue
        ratio = hit / valid
        if ratio > best_ratio:
            best, best_ratio = stride, ratio
    if best is None or best_ratio < 0.8:
        return None
    return best


def resolve(data, version=None):
    """识别文件的 mmtr 布局 -> MmtrView。

    顺序：资源版本（最准，来自文件名后缀）→ 步长启发式（未知版本）。
    都不成立时抛 ValueError（调用方决定是跳过校验还是提示"未适配"）。
    """
    if len(data) < 16 or data[:4] != b"SDF\x00":
        raise ValueError("not an SDF/mmtr file")
    rows, cols = struct.unpack_from("<HH", data, 4)
    shape = TableShape(rows, cols)
    blob_start = struct.unpack_from("<I", data, 8)[0]

    prof = by_version(version) if version is not None else None
    if prof is None:
        stride = guess_stride(data, shape)
        if stride is None:
            raise ValueError(f"unknown mmtr layout: {rows}x{cols} (version={version})")
        prof = MmtrProfile(game=f"unknown-{rows}x{cols}", versions=(),
                           rec_stride=stride, source="guessed")
    return MmtrView(prof, shape, blob_start)
