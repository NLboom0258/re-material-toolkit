#!/usr/bin/env python3
"""Material Studio — RE Engine 材质(mmtr/mdf2)底层检视/编辑器(PySide6)。

定位: 一个"底层材质编辑器"薄壳; 核心读写全部复用 material_toolkit/lib
(binding / rdef / mdf2 / mmtr / mmtr_info)。将来换 UI 或升级为更完整的编辑器时, 逻辑不动。

布局: 每页 = 左「列表」 + 右「内容区(标签页)」, 标签名即"栏名"。
- MMTR 页: **多文件标签页**(每文件一页, 可关闭/拖动; 右上「打开 mmtr…」; 无法从0新建, 故空时显示不可关闭的「(未打开)」占位页);
  每个文件 = 左「Blob 列表」 + 右「贴图绑定」(各绑定组+贴图槽, 槽名下拉选池名) / 「名称池」(全局贴图名表, 可全局改名) / 「材质参数」(UserMaterial 参数定义, 只读) / 「变体(材质)」(按 pass 分组的技术 × 标志变体 -> 程序集, 只读)。
- MDF2 页: **多文件标签页**(每文件一页, 可关闭/拖动, 右上「＋」新建空文件, 关掉最后一个自动补空文件);
  每个文件 = 左「材质列表」 + 右「贴图槽」(type 双击改名/路径常驻输入框/增·删) / 「材质参数」(名字双击改名·类型·值可编辑) / 「材质属性」(着色类型+flags)。
- 材质系统页: **语义级“材质资产”编辑**(与 MMTR/MDF2 的结构/字节级编辑分开; 见 analysis §9)。
  左=选项(光照模式/着色类型/材质模板/基础 mmtr/材质名) + 操作(载入默认材质/新建/打开·保存 .mmat.json/编译校验/生成 mmtr/导出材质实例);
  右=「材质源(HLSL)」(带 HLSL 高亮 + 编译报错红线) / 「组装结果」 / 「输入」(pass 输入·系统预制输入·自定义输入(可编辑)) / 「预览」(写盘 + 通知游戏热重载) / 「语义输出·校验」。
交互: 常用按钮 + 对选中项**右键菜单**; 名称列**双击内联改名**(预选原名); 支持**拖拽文件**导入。
MDF2 参数: 类型列为常驻下拉; 值列按分量拆分输入框, float3/float4 额外带**颜色块**(点击取色)。

用法: python app.py [<file.mmtr.*> | <file.mdf2.*>]
自检: python app.py --selftest <file.mmtr.*>                 (offscreen 构建并打印)
截图: python app.py --shot <out.png> [--pane bind|param] <file>   (渲染截图, 调试用)
"""
import hashlib
import os
import json
import re
import sys
import time
from collections import Counter, namedtuple
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 仓库根(material_studio 的上级)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from material_toolkit.lib.binding import (  # noqa: E402
    rename_name_global,
)
from material_toolkit.lib.mdf2 import (  # noqa: E402
    Mdf2, MATERIAL_FLAG_FIELDS, PARAM_TYPES, SHADING_TYPES,
    encode_material_flags, shading_type_value,
)
from material_toolkit.lib.mmtr import Mmtr  # noqa: E402
from material_toolkit.lib.mmtr_info import (  # noqa: E402
    blob_count, blob_group_counts, blob_info, type_label,
)
from material_toolkit.lib.mmtr_blobs import (  # noqa: E402
    extract_blob, disassemble_dxbc, assemble_asm, verify_dxbc,
    find_translator, run_translator, check_asm, find_assembler,
)
from material_toolkit.lib.mmtr_model import MmtrModel  # noqa: E402
from material_toolkit.lib.mmtr_material import MaterialModel, parse_technology  # noqa: E402
from material_toolkit.lib.rdef import (  # noqa: E402
    replace_blob, rdef_cbuffers, rdef_bind_info, rdef_stage,
)
from material_toolkit.lib.derive import (  # noqa: E402
    derive_groups, derive_namepool, stage_text,
)
from material_toolkit.lib.mmtr_rdefgen import (  # noqa: E402
    RESOURCE_KINDS, RESOURCE_LABELS, add_raw_resource, add_resource, parse_spec,
    remove_resource, resource_spec,
)
from material_toolkit.lib import material_pass as mpass  # noqa: E402
from material_toolkit.lib import material_gen as mgen  # noqa: E402
from material_toolkit.lib import mmtr_nogen as nogen  # noqa: E402
from material_toolkit.lib import material_inputs as minp  # noqa: E402
from material_toolkit.lib import semantic_inputs as sinp  # noqa: E402
from material_toolkit.lib import material_asset as masset  # noqa: E402
from material_toolkit.lib import material_inputs_model as mimp  # noqa: E402
from material_toolkit.lib import custom_functions as cfun  # noqa: E402
from material_toolkit.lib import material_instance as minst  # noqa: E402
from material_toolkit.lib import rbridge as rb  # noqa: E402
from material_toolkit.lib import psession as ps  # noqa: E402
from material_toolkit.lib import mmatlib as mlab  # noqa: E402
from material_toolkit.lib import mdf2 as mdf2lib  # noqa: E402
from material_toolkit.lib import mat_attrs as mattr  # noqa: E402

from PySide6.QtCore import (  # noqa: E402
    Qt, QTimer, QSize, QPointF, Signal, QRegularExpression, QObject, QRunnable, QThreadPool,
)
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QIcon, QKeySequence, QPainter, QPalette, QPixmap,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument,
)
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QColorDialog,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox,
    QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTabBar, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

_grip_cache = None


def _grip_icon(size=16):
    """材质参数页的“拖拽手柄”图标(两列×三行圆点; 比字符清晰、可调大小/颜色)。"""
    global _grip_cache
    if _grip_cache is not None:
        return _grip_cache
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(150, 150, 150))
    for x in (size * 0.34, size * 0.66):
        for y in (size * 0.26, size * 0.5, size * 0.74):
            p.drawEllipse(QPointF(x, y), 1.7, 1.7)
    p.end()
    _grip_cache = QIcon(pm)
    return _grip_cache


# D3D_SRV_DIMENSION 名称(用于 RDEF 资源的维度显示)。
_DIMNAME = {0: "?", 1: "buffer", 2: "tex1d", 3: "tex2d", 4: "tex2dms", 5: "tex3d",
            6: "texcube", 7: "tex1darr", 8: "tex2darr", 9: "tex2dmsarr",
            10: "texcubearr", 11: "bufferex"}


def _rb_typename(t):
    """材质参数类型：1=float, 2/3/4=floatN, 12=float4x3, 16=float4x4。

    getMaterialVariableType 返回的是**分量数**（矩阵按列主序展开后的 float 个数）。
    """
    return {1: "float", 2: "float2", 3: "float3", 4: "float4",
            12: "float4x3 (12)", 16: "float4x4 (16)"}.get(t, "type=%s" % (t,))


def _rb_addr_short(a, n=8):
    """对象地址只显示后 n 位（完整值太长，列表里看不清楚）。"""
    s = (a or "").strip()
    return s[-n:] if len(s) > n else s

def _rb_addr_key(a):
    """地址归一化：去 0x 前缀 + 小写。

    各端点的格式不一致：``/identity`` 给 ``0x1FB6F532250``，``/holds`` 给
    ``1FB6F532250``。跨端点比对（比如用 holds 的地址去查 _rb_edits）必须先归一，
    否则会静默对不上。
    """
    s = str(a or "").strip().lower()
    return s[2:] if s.startswith("0x") else s


def _rb_parse_vec(value, n):
    """把 "0, 1.78, -0.06" 解析成 n 个 float；解析不了的置 0。"""
    out = [0.0] * n
    if isinstance(value, (int, float)):
        out[0] = float(value)
        return out
    s = str(value or "").replace("[", "").replace("]", "").replace("(", "").replace(")", "")
    parts = [p.strip() for p in s.split(",")]
    for i in range(min(n, len(parts))):
        try:
            out[i] = float(parts[i])
        except ValueError:
            pass
    return out


def _iter_items(tree):
    """深度遍历 QTreeWidget 的所有条目。"""
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        it = stack.pop()
        yield it
        stack.extend(it.child(j) for j in range(it.childCount()))


def fit_columns(tree, cols, pad=28, min_w=80, max_w=600):
    """按当前条目(含表头 + 子项缩进)最长文本设置列宽(带上下限); 之后用户仍可手动拖动。"""
    fm = tree.fontMetrics()
    hdr = tree.headerItem()
    widths = {c: (fm.horizontalAdvance(hdr.text(c)) if hdr else 0) for c in cols}
    for item in _iter_items(tree):
        depth = 0
        p = item.parent()
        while p is not None:
            depth += 1
            p = p.parent()
        indent = (depth + 1) * tree.indentation()  # 根条目也占一格分支缩进
        for c in cols:
            widths[c] = max(widths[c], indent + fm.horizontalAdvance(item.text(c)))
    for c in cols:
        tree.setColumnWidth(c, max(min_w, min(widths[c] + pad, max_w)))


class ContentSplitter(QSplitter):
    """按“内容完整显示所需最小宽度”分配各栏宽度的分割器。

    每栏给一个 min 提供者(返回该栏完整显示内容所需的最小宽度)。窗口缩放时:
      - 总宽 >= 各栏最小宽度之和: 每栏至少给到最小宽度, 余量按最小宽度比例分配;
      - 总宽 <  各栏最小宽度之和: 说明都被压到最小仍放不下 => 按最小宽度比例一起缩。
    即“变小优先压仍大于最小宽度的栏, 都到最小后一起缩; 变大优先补仍小于最小宽度的栏”。
    用户手动拖动会在下次窗口缩放时按上述规则重算。
    """

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._providers = []

    def set_providers(self, providers):
        self._providers = list(providers)
        self.relayout()

    def relayout(self):
        n = self.count()
        if n == 0 or len(self._providers) != n:
            return
        mins = []
        for f in self._providers:
            try:
                mins.append(max(1, int(f())))
            except Exception:  # noqa: BLE001
                mins.append(1)
        total = self.width() - self.handleWidth() * (n - 1)
        if total <= 0:
            return
        m = sum(mins)
        if total >= m:
            if n == 2:
                # 第0栏(左=blob)固定为其最小完整显示宽度(只缩不涨), 余量全给右栏
                sizes = [mins[0], total - mins[0]]
            else:
                extra = total - m
                sizes = [mins[i] + int(round(extra * mins[i] / m)) for i in range(n)]
        else:
            # 两栏都已被压到最小仍不够 => 保持比例一起缩
            sizes = [max(1, int(round(mins[i] * total / m))) for i in range(n)]
        sizes[-1] += total - sum(sizes)
        if sizes[-1] < 1:
            sizes[-1] = 1
        self.setSizes(sizes)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()


def attach_menu(tree, build_actions):
    """给 tree 挂右键菜单: build_actions(item) 返回 [(标题, 回调), ...] 或 None。"""
    tree.setContextMenuPolicy(Qt.CustomContextMenu)

    def handler(pos):
        item = tree.itemAt(pos)
        acts = build_actions(item)
        if not acts:
            return
        menu = QMenu(tree)
        for title, cb in acts:
            menu.addAction(title, cb)
        menu.exec(tree.viewport().mapToGlobal(pos))

    tree.customContextMenuRequested.connect(handler)


def _seg_go(stack, btns, k):
    """切到堆叠 stack 的第 k 页, 并同步按钮选中态。"""
    stack.setCurrentIndex(k)
    for j, b in enumerate(btns):
        b.setChecked(j == k)


def _seg_switch(items):
    """单页 + 顶部按钮组(分段)切换视图。items=[(标题, widget)] -> (容器, 按钮列表, 堆叠)。

    用于"组装"页: 不再一 pass 一个顶层 Tab, 而是单页内按按钮切换各 pass。
    """
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    bar = QHBoxLayout()
    stack = QStackedWidget()
    btns = []
    for i, (title, wid) in enumerate(items):
        stack.addWidget(wid)
        b = QPushButton(title)
        b.setCheckable(True)
        b.clicked.connect(lambda _=False, k=i: _seg_go(stack, btns, k))
        bar.addWidget(b)
        btns.append(b)
    bar.addStretch(1)
    v.addLayout(bar)
    v.addWidget(stack, 1)
    _seg_go(stack, btns, 0)
    return box, btns, stack


def _copy_to_clipboard(text):
    QApplication.clipboard().setText(text)


class NoWheelComboBox(QComboBox):
    """不响应滚轮的下拉框(防鼠标悬停误改); 点击/键盘选择照常。

    所有 GUI 下拉框都用它(避免悬停下误编辑)。
    """

    def wheelEvent(self, event):
        event.ignore()


class ReorderTree(QTreeWidget):
    """支持内部拖拽重排的树: **仅第 0 列(≡ 手柄)可发起拖拽**(防误拖); 放下后发 reordered。

    用 `dropEvent` 后发信号(而非 `rowsMoved`): QTreeWidget 的内部移动不保证发 rowsMoved。
    """

    reordered = Signal()

    def startDrag(self, actions):
        if self.currentColumn() != 0:
            return
        super().startDrag(actions)

    def dropEvent(self, e):
        super().dropEvent(e)
        self.reordered.emit()


class _InputTree(ReorderTree):
    """输入页的树: 只允许在**可重排分组节点**(参数/贴图)的同一父内部拖拽排序; 其余忽略。

    分组节点用 `Qt.UserRole+1` 标记为 ("group", <kind>) —— 与 `UserRole`(存 kind/token)分开,
    避免影响右键菜单。仅“上/下插入(不嵌套)”才放行。
    """

    REORDER_GROUPS = ("param", "tex")

    def __init__(self, parent=None):
        super().__init__(parent)
        # 启用拖拽重排(ReorderTree 只重写了 startDrag/dropEvent; 这些属性必须在实例上设置)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self._press_pos = None

    def mousePressEvent(self, e):
        self._press_pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        super().mousePressEvent(e)

    @staticmethod
    def _gkind(it):
        d = it.data(0, Qt.UserRole + 1) if it is not None else None
        return d[1] if (isinstance(d, tuple) and len(d) == 2 and d[0] == "group") else None

    def _row_group(self, it):
        """it 所属的“可重排分组节点”(it 是某分组子项时); 否则 None。"""
        p = it.parent() if it is not None else None
        return p if (p is not None and self._gkind(p) in self.REORDER_GROUPS) else None

    def _grip_hit(self, it, pos):
        """pos 是否落在该行列0 的“手柄图标”区域内(限制拖动起点, 避免整列/留白都可拖)。"""
        if it is None or pos is None:
            return False
        r = self.visualItemRect(it)
        if not (r.top() <= pos.y() <= r.bottom()):
            return False
        depth = 0
        p = it.parent()
        while p is not None:
            depth += 1
            p = p.parent()
        x0 = (depth + 1) * self.indentation()      # 列0 内容(手柄图标)左边界
        return x0 - 2 <= pos.x() <= x0 + 22

    def startDrag(self, actions):
        it = self.currentItem()
        if (self.currentColumn() != 0 or self._row_group(it) is None
                or not self._grip_hit(it, self._press_pos)):
            return
        QTreeWidget.startDrag(self, actions)

    def dropEvent(self, e):
        pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        src, tgt = self.currentItem(), self.itemAt(pos)
        if (self._row_group(src) is None or self._row_group(src) is not self._row_group(tgt)
                or self.dropIndicatorPosition() == QAbstractItemView.OnItem):   # 禁嵌套
            e.ignore()
            return
        QTreeWidget.dropEvent(self, e)
        self.reordered.emit()


# asm 高亮/静态检查用到的 DXBC SM5 指令集(基名; 带 _sat/_indexable(...) 等由识别器归一)
_ASM_OPCODES = {
    "mov", "movc", "mova", "mad", "add", "mul", "div", "dp2", "dp3",
    "dp4", "min", "max", "lt", "le", "gt", "ge", "eq", "ne", "and",
    "or", "xor", "not", "sample", "sample_l", "sample_b", "sample_c",
    "sample_c_l", "sample_c_lz", "sample_cmp", "sample_d", "sampleinfo",
    "ld", "ld_ms", "ld_raw", "ld_structured", "ld_uav_typed", "store_raw",
    "store_structured", "store_uav_typed", "resinfo", "discard", "clip",
    "ftoi", "itof", "ftou", "utof", "f16tof32", "f32tof16", "sincos",
    "cos", "sin", "exp", "log", "sqrt", "rsq", "rcp", "frc", "round_ne",
    "round_ni", "round_pi", "round_z", "ishl", "ishr", "ushr", "imad",
    "imul", "umad", "umul", "udiv", "umod", "imod", "ine", "ige", "ilt",
    "ieq", "ineg", "iadd", "inot", "imax", "imin", "umax", "umin",
    "ult", "uge", "ugt", "ule", "ueq", "une",
    # 双精度(SM5 亦有): 常见几条
    "dadd", "dmax", "dmin", "dmul", "ddiv", "dfma", "drcp", "drsq",
    "dsqrt", "dcmp", "dmov", "dmovc", "dtof", "ftod", "dtoi", "itod",
    "dtou", "utod", "dbreakc", "ddiscard",
    # 曲面细分阶段(hs/ds 的非 dcl_ 声明)
    "hs_decls", "hs_control_point_phase", "hs_fork_phase", "hs_join_phase",
    "ubfe", "ibfe", "switch", "case", "default", "endswitch", "loop",
    "endloop", "break", "breakc", "continue", "continuec", "if_nz", "if_z",
    "else", "endif", "ret", "retc", "call", "callc", "nop", "sync", "emit",
    "cut", "gather4", "gather4_c", "gather4_po", "swapc", "bfi", "bfrev",
    "countbits", "firstbit_hi", "firstbit_lo", "firstbit_shi",
    "eval_centroid", "eval_sample_index", "deriv_rtx", "deriv_rty", "ddy", "ddx",
    "imm_atomic_alloc", "imm_atomic_consume", "imm_atomic_iadd",
    "imm_atomic_imax", "imm_atomic_imin", "imm_atomic_and", "imm_atomic_or",
    "imm_atomic_xor", "imm_atomic_exch", "imm_atomic_cmp_exch",
    "atomic_iadd", "atomic_imax", "atomic_imin", "atomic_and", "atomic_or",
    "atomic_xor", "atomic_exch", "atomic_cmp_exch",
    # 补齐汇编器(Assembler.cpp)里有、而这里缺的指令(2026-09-28, 由 _asmops_diff 全量核对)
    "abort", "atomic_cmp_store", "atomic_umax", "atomic_umin",
    "bufinfo", "bufinfo_indexable", "deq", "dge", "dlt", "dne",
    "deriv_rtx_coarse", "deriv_rtx_fine", "deriv_rty_coarse", "deriv_rty_fine",
    "emit_then_cut", "eval_snapped",
    "gather4_aoffimmi", "gather4_aoffimmi_indexable",
    "gather4_c_aoffimmi", "gather4_c_aoffimmi_indexable",
    "gather4_po_c", "gather4_po_c_indexable",
    "imm_atomic_umax", "imm_atomic_umin",
    "ld_aoffimmi", "ld_aoffimmi_indexable", "ldms", "ldms_aoffimmi",
    "ldms_aoffimmi_indexable", "ldms_indexable", "lod", "msad", "round_nz",
    "sample_aoffimmi", "sample_aoffimmi_indexable",
    "sample_b_aoffimmi", "sample_b_aoffimmi_indexable",
    "sample_c_aoffimmi", "sample_c_aoffimmi_indexable",
    "sample_c_lz_aoffimmi", "sample_c_lz_aoffimmi_indexable",
    "sample_d_aoffimmi", "sample_d_aoffimmi_indexable",
    "sample_l_aoffimmi", "sample_l_aoffimmi_indexable",
    "sampled", "uaddc", "usubb",
}

# HLSL 混合标记(见翻译器 README); 不在其中的 HLSL*/DXBC* 会标红
_HLSL_MARKERS = ("HLSLSnippet", "HLSLFunctionImport", "HLSLTexture",
                 "HLSLSampler", "HLSLMov", "HLSLInit", "HLSL", "DXBCMov")
_HLSL_KEYWORDS = {
    "if", "else", "for", "while", "do", "switch", "case", "default",
    "break", "continue", "return", "discard", "true", "false",
    "static", "const", "in", "out", "inout", "struct", "void",
}
_HLSL_TYPES = {
    "float", "float2", "float3", "float4", "float2x2", "float3x3",
    "float4x3", "float3x4", "float4x4", "half", "half2", "half3", "half4", "double", "int",
    "int2", "int3", "int4", "uint", "uint2", "uint3", "uint4", "bool",
    "bool2", "bool3", "bool4", "min16float", "min10float", "min16int",
    "min16uint", "Texture2D", "Texture2DArray", "TextureCube",
    "SamplerState", "SamplerComparisonState", "matrix",
}
_HLSL_INTRINSICS = {
    "abs", "acos", "all", "any", "asin", "atan", "atan2", "ceil", "clamp",
    "clip", "cos", "cosh", "cross", "ddx", "ddy", "degrees", "distance",
    "dot", "exp", "exp2", "faceforward", "floor", "fmod", "frac", "frexp",
    "fwidth", "isfinite", "isinf", "isnan", "ldexp", "length", "lerp",
    "lit", "log", "log2", "log10", "mad", "max", "min", "modf", "mul",
    "normalize", "pow", "radians", "rcp", "reflect", "refract", "round",
    "rsqrt", "saturate", "sign", "sin", "sincos", "sinh", "smoothstep",
    "sqrt", "step", "tan", "tanh", "transpose", "trunc",
    "Sample", "SampleLevel", "SampleBias", "SampleCmp", "SampleGrad",
    "tex2D", "tex2Dlod", "tex2Dproj",
}


def _marker_bad(marker, code):
    """轻量标记语法检查(只查必备/禁止符号, 不是完整语法): 明显乱写 -> True。

    code: 标记之后的代码部分(已去掉行内注释)。
    例: DXBCMov r1.x = Test1 (应为逗号) -> True。
    """
    if marker == "DXBCMov":
        return ("," not in code) or ("=" in code)
    if marker == "HLSLMov":
        return "=" not in code
    if marker in ("HLSLTexture", "HLSLSampler"):
        return "=" not in code
    if marker == "HLSLFunctionImport":
        return '"' not in code
    if marker == "HLSL":
        return code.strip() == ""
    return False


def _marker_msg(marker, code):
    """给命中的标记行生成一句人话说明。"""
    if marker == "DXBCMov":
        return "DXBCMov 应用逗号分隔 (如 DXBCMov r0, r1.x), 不应有 ="
    if marker in ("HLSLMov", "HLSLTexture", "HLSLSampler"):
        return f"{marker} 缺少 ="
    if marker == "HLSLFunctionImport":
        return "HLSLFunctionImport 缺少引号"
    if marker == "HLSL":
        return "HLSL 后缺少语句"
    return f"{marker} 疑似语法错误"


# 指令识别: 允许 _indexable(...)/_sat/_nz/_z/_lz 等后缀 + dcl_ 前缀 + profile 行
_ASM_SUFFIXES = ("_indexable", "_sat", "_nz", "_c_lz", "_lz", "_z")
_PROFILE_RE = re.compile(r"^(ps|vs|cs|gs|hs|ds)_\d")


def _asm_op_ok(tok):
    """某行首个 token 是否为合法 asm 指令(含后缀/前缀) —— 静态检查防误报。"""
    base = tok.split("(", 1)[0]
    if base in _ASM_OPCODES or base.startswith("dcl_") or _PROFILE_RE.match(base):
        return True
    for suf in _ASM_SUFFIXES:
        if base.endswith(suf) and base[:-len(suf)] in _ASM_OPCODES:
            return True
    return False


# 诊断项: 行号(0基) / 起始列 / 结束列 / 说明
Diag = namedtuple("Diag", "line start end msg")

_MARKER_LINE_RE = re.compile(r"^(\s*)(" + "|".join(_HLSL_MARKERS) + r")\b")
_UNKNOWN_LINE_RE = re.compile(r"^(\s*)((?:HLSL|DXBC)[A-Za-z]\w*)")
_ASM_TOK_RE = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9_]*(?:\([^)]*\))*)")


def analyze_asm(text):
    """静态(启发式)扫描整篇 asm/HLSL 混合文本 -> [Diag, ...]。不依赖汇编器(快)。

    覆盖: 未知标记 / HLSLSnippet 缺 `{` / HLSLSnippet 未闭合 / 标记行轻量语法
    (`_marker_bad`) / 裸 asm 的未知指令(疑似乱写)。
    设计上只做"明显乱写"级启发式, 不做完整语法(合法性仍以汇编器为准)。
    """
    diags = []
    depth = 0
    open_line = -1
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if depth > 0:                      # HLSLSnippet 内部: 仅跟踪花括号配平
            for ch in line:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
            if depth <= 0:
                depth = 0
            continue
        if not stripped or stripped.startswith("//") or stripped.startswith(";"):
            continue
        m = _MARKER_LINE_RE.match(line)
        if m:
            ind, marker = len(m.group(1)), m.group(2)
            code = line[m.end(2):].split("//")[0]
            if marker == "HLSLSnippet":
                if "{" not in code:
                    diags.append(Diag(i, ind, m.end(2),
                                      "HLSLSnippet 缺少 { (应写 HLSLSnippet {)"))
                    depth = 1          # 仍按 HLSL 块处理, 避免后续行被误判为 asm
                    open_line = i
                else:
                    d = code.count("{") - code.count("}")
                    if d > 0:
                        depth = d
                        open_line = i
            elif _marker_bad(marker, code):
                diags.append(Diag(i, ind, len(line), _marker_msg(marker, code)))
            continue
        um = _UNKNOWN_LINE_RE.match(line)
        if um:
            diags.append(Diag(i, len(um.group(1)), um.end(2), "未知标记"))
            continue
        if stripped[0] in "{}":            # dcl_immediateConstantBuffer 的 { ... }
            continue
        tm = _ASM_TOK_RE.match(line)
        if tm:
            tok = tm.group(2)
            if line[tm.end(2):].lstrip().startswith(":"):   # 标签行
                continue
            if not _asm_op_ok(tok):
                diags.append(Diag(i, len(tm.group(1)), tm.end(2),
                                  f"未知指令 '{tok.split('(')[0]}'"))
    if depth > 0 and open_line >= 0:
        diags.append(Diag(open_line, 0, len(lines[open_line]),
                          "HLSLSnippet 未闭合 (缺少 })"))
    return diags


class AsmHighlighter(QSyntaxHighlighter):
    """DXBC asm 语法高亮; 在 HLSL 混合标记范围内套用 HLSL 高亮;
    并在诊断(diags)命中处**叠加红色波浪线** —— 高亮与红线**共存**(不互相覆盖)。

    做法: 先按规则把每字符的“基础格式”写进 cells(后者覆盖前者), 再把诊断下划线
    合并进去(保留原前景色), 最后合并相邻同格式一次性 setFormat。
    规则集中在此类, 以后要换混写专用高亮直接替换规则即可。
    """

    def __init__(self, document):
        super().__init__(document)
        self._diags = {}          # blockNumber -> [(start, end, msg)]

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            if italic:
                f.setFontItalic(True)
            return f

        self._f_marker = fmt("#c586c0", bold=True)
        self._err_color = QColor("#f14c4c")

        op = "|".join(sorted(_ASM_OPCODES, key=len, reverse=True))
        self._asm_rules = [
            (QRegularExpression(r"\b(ps_5_[01]|vs_5_[01]|cs_5_[01])\b"), fmt("#dcdcaa")),
            (QRegularExpression(r"\bdcl_[A-Za-z0-9_]+"), fmt("#4ec9b0")),
            (QRegularExpression(r"^\s*(%s)\b" % op), fmt("#569cd6", bold=True)),
            (QRegularExpression(r"\b(r|v|o|t|s|cb|icb|u)[0-9]+"), fmt("#9cdcfe")),
            (QRegularExpression(r"\.[xyzwrgba]{1,4}\b"), fmt("#9cdcfe")),
            (QRegularExpression(r"\bl\([^)]*\)|\b0x[0-9a-fA-F]+\b|-?\d+\.\d+"),
             fmt("#b5cea8")),
            (QRegularExpression(r"//.*$"), fmt("#6a9955", italic=True)),
        ]

        def alt(wordset):
            return "|".join(sorted(wordset, key=len, reverse=True))
        self._hlsl_rules = [
            (QRegularExpression(r"\b(%s)\b" % alt(_HLSL_KEYWORDS)), fmt("#569cd6", bold=True)),
            (QRegularExpression(r"\b(%s)\b" % alt(_HLSL_TYPES)), fmt("#4ec9b0")),
            (QRegularExpression(r"\b(%s)\b" % alt(_HLSL_INTRINSICS)), fmt("#dcdcaa")),
            (QRegularExpression(r"-?\d+\.\d+|\b0x[0-9a-fA-F]+\b|\b\d+\b"), fmt("#b5cea8")),
            (QRegularExpression(r"//.*$"), fmt("#6a9955", italic=True)),
        ]
        self._marker_re = re.compile(r"^\s*(" + "|".join(_HLSL_MARKERS) + r")\b")

    def set_diagnostics(self, diags):
        """设置全文档诊断(analyze_asm 结果)并重绘。diags: [Diag, ...]。"""
        self._diags = {}
        for d in diags:
            self._diags.setdefault(d.line, []).append((d.start, d.end, d.msg))
        self.rehighlight()

    # -- 把某正则的匹配写进 per-char 格式数组(后者覆盖前者) --
    def _collect(self, pat, f, text, lo, hi, cells):
        it = pat.globalMatch(text, lo)
        while it.hasNext():
            m = it.next()
            s = m.capturedStart()
            if s >= hi:
                break
            e = min(s + m.capturedLength(), hi)
            fv = f
            for j in range(s, e):
                cells[j] = fv

    def _collect_hlsl(self, text, lo, hi, cells):
        for pat, f in self._hlsl_rules:
            self._collect(pat, f, text, lo, hi, cells)

    def _scan_braces(self, text, start, depth):
        """从 start 起按花括号配平; 返回 (hlsl_end, new_depth)。"""
        d = depth
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                d += 1
            elif c == "}":
                d -= 1
                if d <= 0:
                    return i + 1, 0
        return len(text), d

    def highlightBlock(self, text):
        n = len(text)
        cells = [None] * n
        # 1) asm 规则打底(整行)
        for pat, f in self._asm_rules:
            self._collect(pat, f, text, 0, n, cells)

        prev = self.previousBlockState()
        depth = prev if (prev is not None and prev > 0) else 0
        if depth > 0:
            hl_end, state = self._scan_braces(text, 0, depth)
            self._collect_hlsl(text, 0, hl_end, cells)
            self.setCurrentBlockState(state)
        else:
            m = self._marker_re.match(text)
            if m:
                for j in range(m.start(1), m.end(1)):
                    cells[j] = self._f_marker
                state = 0
                if m.group(1) == "HLSLSnippet":
                    hl_end, state = self._scan_braces(text, m.end(1), 0)
                    self._collect_hlsl(text, m.end(1), hl_end, cells)
                else:
                    self._collect_hlsl(text, m.end(1), n, cells)
                self.setCurrentBlockState(state)
            else:
                self.setCurrentBlockState(0)

        # 2) 诊断: 叠加红色波浪线(保留原高亮) —— 高亮与红线共存
        for (s, e, _msg) in self._diags.get(self.currentBlock().blockNumber(), ()):
            for j in range(max(0, s), min(n, e)):
                base = (QTextCharFormat(cells[j]) if cells[j] is not None
                        else QTextCharFormat())
                base.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
                base.setUnderlineColor(self._err_color)
                cells[j] = base

        # 3) 合并相邻同格式, 一次性 setFormat
        j = 0
        while j < n:
            f = cells[j]
            k = j + 1
            while k < n and cells[k] == f:
                k += 1
            if f is not None:
                self.setFormat(j, k - j, f)
            j = k


class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._ed = editor

    def sizeHint(self):
        return QSize(self._ed.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._ed.line_number_area_paint_event(event)


class _FindBar(QWidget):
    """编辑器内嵌查找栏(Ctrl+F): 上一个/下一个 + 全部高亮 + 区分大小写。"""

    def __init__(self, editor):
        super().__init__(editor)
        self._ed = editor
        self.setObjectName("reFindBar")
        self.setStyleSheet(
            "#reFindBar { background: palette(window);"
            " border-bottom: 1px solid palette(mid); }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(4)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("查找\u2026")
        self.ed_find.setFixedWidth(200)
        self.lbl = QLabel("")
        self.lbl.setMinimumWidth(52)
        self.btn_prev = QPushButton("上一个")
        self.btn_next = QPushButton("下一个")
        self.btn_case = QPushButton("Aa")
        self.btn_case.setCheckable(True)
        self.btn_case.setToolTip("区分大小写")
        self.btn_close = QPushButton("\u2715")
        self.btn_close.setFixedWidth(28)
        for w in (self.ed_find, self.lbl, self.btn_prev, self.btn_next,
                  self.btn_case, self.btn_close):
            lay.addWidget(w)
        lay.addStretch(1)
        self.btn_close.clicked.connect(self._ed.hide_find)
        self.btn_next.clicked.connect(lambda: self._ed.find_next(True))
        self.btn_prev.clicked.connect(lambda: self._ed.find_next(False))
        self.btn_case.toggled.connect(lambda _=False: self._ed._refresh_matches())
        self.ed_find.textChanged.connect(lambda _t: self._ed._refresh_matches())
        self.ed_find.returnPressed.connect(lambda: self._ed.find_next(True))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._ed.hide_find()
            return
        super().keyPressEvent(event)


class CodeEdit(QPlainTextEdit):
    """代码编辑器: 行号 + Tab 缩进(空格) + 等宽字体 + Ctrl+F 查找。

    indent = Tab 插入空格数(asm=2 / HLSL=4); Shift+Tab 反缩进。
    """

    def __init__(self, indent=4, numbers=True, parent=None):
        super().__init__(parent)
        self._indent = indent
        self._has_numbers = numbers
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.setFont(mono)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._lnarea = _LineNumberArea(self) if numbers else None
        if numbers:
            # 行号区底色跟随代码区(QPalette.Base), 明/暗主题自适应;
            # 不要写死浅色 —— 暗色主题下会变成刺眼的“白条”。
            base = self.palette().color(QPalette.Base)
            self._lnbg = QColor(base)
            self._lnfg = QColor("#8f8f8f") if base.lightness() < 128 else QColor("#7a7a7a")
            # 分隔线(紧贴代码一侧, 与行号分开, 仿周道/主流 IDE 样式)
            self._lnborder = (QColor(base).lighter(150) if base.lightness() < 128
                              else QColor(base).darker(140))
            pal = self._lnarea.palette()
            pal.setColor(QPalette.Window, self._lnbg)
            self._lnarea.setPalette(pal)
            self._lnarea.setAutoFillBackground(True)
            self.blockCountChanged.connect(lambda *_: self._update_lnarea_width())
            self.updateRequest.connect(self._update_lnarea)
            self._update_lnarea_width()
        self._find_open = False
        self._find = _FindBar(self)
        self._find.hide()
        self.textChanged.connect(self._on_text_for_find)

    # ---- 行号区 ----
    def line_number_area_width(self):
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _find_height(self):
        """查找栏停靠带高度(未打开=0)。"""
        if not self._find_open:
            return 0
        return self._find.sizeHint().height()

    def _update_lnarea_width(self):
        ft = self._find_height() if getattr(self, "_find", None) is not None else 0
        self.setViewportMargins(self.line_number_area_width(), ft, 0, 0)

    def _update_lnarea(self, rect, dy):
        if dy:
            self._lnarea.scroll(0, dy)
        else:
            self._lnarea.update(0, rect.y(), self._lnarea.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_lnarea_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_find", None) is not None:
            self._apply_find_layout()
        elif self._has_numbers:
            cr = self.contentsRect()
            self._lnarea.setGeometry(cr.left(), cr.top(),
                                     self.line_number_area_width(), cr.height())

    def line_number_area_paint_event(self, event):
        painter = QPainter(self._lnarea)
        painter.fillRect(event.rect(), self._lnbg)
        block = self.firstVisibleBlock()
        num = block.blockNumber() + 1
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(self._lnfg)
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._lnarea.width() - 8, h,
                                 Qt.AlignRight, str(num))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        # 行号区与代码之间画一条竖线(紧贴代码内容一侧)
        painter.setPen(self._lnborder)
        x = self._lnarea.width() - 1
        painter.drawLine(x, event.rect().top(), x, event.rect().bottom())

    # ---- Tab 缩进 ----
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Find):
            self.show_find()
            return
        if event.key() == Qt.Key_Escape and self._find_open:
            self.hide_find()
            return
        if event.key() == Qt.Key_F3:
            self.find_next(not (event.modifiers() & Qt.ShiftModifier))
            return
        if event.key() == Qt.Key_Tab and not (event.modifiers() & Qt.ControlModifier):
            self._indent_sel(bool(event.modifiers() & Qt.ShiftModifier))
            return
        if event.key() == Qt.Key_Backtab:
            self._indent_sel(True)
            return
        super().keyPressEvent(event)

    def _indent_sel(self, dedent):
        n = self._indent
        cursor = self.textCursor()
        if not cursor.hasSelection():
            block = cursor.block()
            if dedent:
                t = block.text()
                k = min(len(t) - len(t.lstrip(" ")), n)
                if k == 0:
                    return
                cursor.movePosition(QTextCursor.StartOfBlock)
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, k)
                cursor.removeSelectedText()
            else:
                cursor.insertText(" " * n)
            return
        doc = self.document()
        start = doc.findBlock(cursor.selectionStart())
        end = doc.findBlock(cursor.selectionEnd())
        cursor.beginEditBlock()
        block = start
        while True:
            c = QTextCursor(block)
            if dedent:
                t = block.text()
                k = min(len(t) - len(t.lstrip(" ")), n)
                if k:
                    c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, k)
                    c.removeSelectedText()
            else:
                c.movePosition(QTextCursor.StartOfBlock)
                c.insertText(" " * n)
            if block == end:
                break
            block = block.next()
        cursor.endEditBlock()

    # ---- 查找(Ctrl+F) ----
    def _find_flags(self):
        f = QTextDocument.FindFlags()
        if self._find.btn_case.isChecked():
            f |= QTextDocument.FindCaseSensitively
        return f

    def _all_matches(self, text):
        """全部匹配的 (起点, 终点) 位置。"""
        out = []
        if not text:
            return out
        doc = self.document()
        cur = QTextCursor(doc)
        while True:
            cur = doc.find(text, cur, self._find_flags())
            if cur.isNull():
                break
            out.append((cur.selectionStart(), cur.selectionEnd()))
        return out

    def _on_text_for_find(self):
        """文本变化时刷新高亮(仅查找栏打开时)。"""
        if self._find_open:
            self._refresh_matches()

    def _refresh_matches(self):
        """重算全部匹配并高亮(全部=黄底, 当前选中由光标高亮)。"""
        text = self._find.ed_find.text()
        sels = []
        for (s, e) in self._all_matches(text):
            c = QTextCursor(self.document())
            c.setPosition(s)
            c.setPosition(e, QTextCursor.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#6b5a00"))
            fmt.setForeground(QColor("#ffffff"))
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = fmt
            sels.append(sel)
        self.setExtraSelections(sels)
        self._find.lbl.setText("" if not text else "%d \u5904" % len(sels))

    def _apply_find_layout(self):
        """查找栏**停靠在顶部边框**(非悬浮): 预留一条固定区域并放置。

        打开: 顶部让出 `_find_height()`, 查找栏占满该条(代码在其下方滚动);
        关闭: 边距归零, 代码区恢复。
        """
        ft = self._find_height()
        cr = self.contentsRect()
        self.setViewportMargins(self.line_number_area_width(), ft, 0, 0)
        if self._has_numbers:
            self._lnarea.setGeometry(cr.left(), cr.top() + ft,
                                     self.line_number_area_width(), cr.height() - ft)
        if self._find_open:
            self._find.setGeometry(cr.left(), cr.top(), cr.width(), ft)
            self._find.raise_()

    def show_find(self):
        """显示查找栏; 若有单行选中的文本则预填。"""
        sel = self.textCursor().selectedText()
        if sel and "\u2029" not in sel and "\n" not in sel:
            self._find.ed_find.setText(sel)
        self._find_open = True
        self._find.show()
        self._apply_find_layout()
        self._find.ed_find.setFocus()
        self._find.ed_find.selectAll()
        self._refresh_matches()

    def hide_find(self):
        self._find_open = False
        self._find.hide()
        self._apply_find_layout()
        self.setExtraSelections([])
        self.setFocus()

    def find_next(self, forward=True):
        """查找下一个/上一个(到头则回绕)。"""
        text = self._find.ed_find.text()
        if not text:
            self.show_find()
            return
        flags = self._find_flags()
        if not forward:
            flags |= QTextDocument.FindBackward
        if not self.find(text, flags):
            c = self.textCursor()
            c.movePosition(QTextCursor.Start if forward else QTextCursor.End)
            self.setTextCursor(c)
            self.find(text, flags)


class HlslHighlighter(QSyntaxHighlighter):
    """HLSL 语法高亮 + 编译诊断红波浪线(与高亮共存)。"""

    def __init__(self, document):
        super().__init__(document)
        self._diags = {}          # 1基行号 -> [(start, end, msg)]

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            if italic:
                f.setFontItalic(True)
            return f

        def alt(ws):
            return "|".join(sorted(ws, key=len, reverse=True))

        self._err_color = QColor("#f14c4c")
        self._rules = [
            (QRegularExpression(r"\b(%s)\b" % alt(_HLSL_KEYWORDS)), fmt("#569cd6", bold=True)),
            (QRegularExpression(r"\b(%s)\b" % alt(_HLSL_TYPES)), fmt("#4ec9b0")),
            (QRegularExpression(r"\b(%s)\b" % alt(_HLSL_INTRINSICS)), fmt("#dcdcaa")),
            (QRegularExpression(r"-?\d+\.\d+|\b0x[0-9a-fA-F]+\b|\b\d+\b"), fmt("#b5cea8")),
            (QRegularExpression(r"//.*$"), fmt("#6a9955", italic=True)),
        ]

    def set_diagnostics(self, diags):
        """diags: [(行1基, start, end, msg)]。"""
        self._diags = {}
        for ln, s, e, msg in diags:
            self._diags.setdefault(ln, []).append((s, e, msg))
        self.rehighlight()

    def highlightBlock(self, text):
        n = len(text)
        cells = [None] * n
        for pat, f in self._rules:
            it = pat.globalMatch(text, 0)
            while it.hasNext():
                m = it.next()
                s = m.capturedStart()
                if s >= n:
                    break
                e = min(s + m.capturedLength(), n)
                for j in range(s, e):
                    cells[j] = f
        for (s, e, _m) in self._diags.get(self.currentBlock().blockNumber() + 1, ()):
            for j in range(max(0, s), min(n, e)):
                base = (QTextCharFormat(cells[j]) if cells[j] is not None
                        else QTextCharFormat())
                base.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
                base.setUnderlineColor(self._err_color)
                cells[j] = base
        j = 0
        while j < n:
            f = cells[j]
            k = j + 1
            while k < n and cells[k] == f:
                k += 1
            if f is not None:
                self.setFormat(j, k - j, f)
            j = k


def _template_struct_fields(template_name, struct_name):
    """从 pass 模板文件抽 struct 成员: [(类型, 名, 说明), ...]。"""
    path = os.path.join(mpass._TDIR, template_name + ".hlsl")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r"struct\s+%s\s*\{(.*?)\}" % struct_name, txt, re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        code, _, comment = line.partition("//")
        code = code.strip().rstrip(";").strip()
        parts = code.split()
        if len(parts) >= 2:
            out.append((" ".join(parts[:-1]), parts[-1], comment.strip()))
    return out


class _AsmCheckSignals(QObject):
    """后台汇编检查结果信号(gen, ok, errors[(line1b,msg)], log)。"""
    finished = Signal(int, bool, list, str)


class _AsmCheckTask(QRunnable):
    """后台“编译即检查”: (可选翻译) -> 试汇编, 回传行级错误。"""

    def __init__(self, gen, text, translate, ref, signals):
        super().__init__()
        self._gen = gen
        self._text = text
        self._translate = translate
        self._ref = ref
        self._sig = signals

    def run(self):
        try:
            t = self._text
            if self._translate and find_translator():
                t = run_translator(t)
            r = check_asm(t, ref_dxbc=self._ref)
            self._sig.finished.emit(self._gen, r["ok"], r["errors"], r["log"])
        except Exception as e:  # noqa: BLE001
            self._sig.finished.emit(self._gen, False, [], str(e))


# PARAM_TYPES 由 lib.mdf2 提供(单一来源: [(类型名, 字节大小), ...])


def pick_type(parent, title, default="float4"):
    """下拉选择参数类型 - 返回类型名(取消返回 None)。"""
    names = [n for n, _ in PARAM_TYPES]
    idx = names.index(default) if default in names else 0
    choice, ok = QInputDialog.getItem(parent, title, "类型:", names, idx, False)
    return choice if ok else None


def type_size(type_name):
    """类型名 -> 字节大小。"""
    for n, s in PARAM_TYPES:
        if n == type_name:
            return s
    return 4


class InlineNameDelegate(QStyledItemDelegate):
    """在指定列双击内联改名: 编辑框预填当前名并全选; 提交时回调 commit(key, text)。

    key = 该条目的 data(0, Qt.UserRole)(提交时同步取出, 避免延时回调时条目已被回收)。
    仅该列可用; 其他列 createEditor 返回 None(不提供编辑器)。
    """

    def __init__(self, tree, col, getter, commit, parent=None):
        super().__init__(parent if parent is not None else tree)
        self._tree = tree
        self._col = col
        self._get = getter
        self._commit = commit

    def createEditor(self, parent, option, index):
        if index.column() != self._col:
            return None
        ed = QLineEdit(parent)
        item = self._tree.itemFromIndex(index)
        if item is not None:
            original = item.text(self._col)
            # 编辑期间隐去底层文字(否则会与编辑器文字错位重叠); 取消/无改变时恢复
            QTimer.singleShot(0, lambda it=item: self._hide_text(it))
            ed.destroyed.connect(
                lambda *_a, it=item, txt=original: self._restore_text(it, txt))
        return ed

    def _hide_text(self, item):
        try:
            item.setText(self._col, "")
        except RuntimeError:
            pass

    def _restore_text(self, item, text):
        try:
            if item.text(self._col) == "":
                item.setText(self._col, text)
        except RuntimeError:
            pass

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)

    def paint(self, painter, option, index):
        # 编辑中不画文字: 否则底层原文会从编辑器背后透出(与编辑器文字错位重叠)
        if index.column() == self._col and (option.state & QStyle.State_Editing):
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            style = opt.widget.style() if opt.widget else QApplication.style()
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
            return
        super().paint(painter, option, index)

    def setEditorData(self, editor, index):
        item = self._tree.itemFromIndex(index)
        editor.setText((self._get(item) if item else "") or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        item = self._tree.itemFromIndex(index)
        if item is None:
            return
        key = item.data(0, Qt.UserRole)
        text = editor.text().strip()
        QTimer.singleShot(0, lambda: self._commit(key, text))


def _is_color_type(type_name):
    """该类型是否适合用颜色表示(float3 / float4)。"""
    return type_name in ("float3", "float4")


class ValueEditor(QWidget):
    """参数值的类型化控件: 按分量拆输入框; float3/float4 额外带颜色块(点击取色)。

    - float~float4: 每个分量一个 QDoubleSpinBox(float3/4 前面加色块, 取色回写前 3 分量);
    - 分量 > 4(float4x3/float4x4): 回退为单个文本输入框(逗号分隔), 避免过宽。
    on_change: 值变化后的回调(不重建树, 仅通知)。
    """

    def __init__(self, prop, on_change=None, parent=None):
        super().__init__(parent)
        self.prop = prop
        self._on_change = on_change
        self._spins = []
        n = len(prop.values)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        if n > 4:
            le = QLineEdit(", ".join(f"{v:g}" for v in prop.values))
            le.editingFinished.connect(lambda: self._commit_text(le.text()))
            lay.addWidget(le)
            lay.addStretch(1)
            return
        if n >= 3 and _is_color_type(prop.type):
            self.swatch = QPushButton()
            self.swatch.setFixedSize(18, 18)
            self.swatch.setToolTip("点击取色")
            self.swatch.clicked.connect(self._pick_color)
            lay.addWidget(self.swatch)
        for i in range(n):
            sp = QDoubleSpinBox()
            sp.setDecimals(4)
            sp.setRange(-1e9, 1e9)
            sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
            sp.setFixedWidth(68)
            sp.setValue(float(prop.values[i]))
            sp.valueChanged.connect(lambda v, i=i: self._on_spin(i, v))
            lay.addWidget(sp)
            self._spins.append(sp)
        lay.addStretch(1)
        self._sync_swatch()

    def _notify(self):
        if self._on_change:
            self._on_change()

    def _on_spin(self, i, v):
        self.prop.values[i] = float(v)
        self._sync_swatch()
        self._notify()

    def _commit_text(self, text):
        try:
            vals = [float(x) for x in text.replace(",", " ").split()]
        except ValueError:
            return
        if not vals:
            return
        self.prop.values = vals
        self.prop.param_count = len(vals)
        self._notify()

    def _pick_color(self):
        vals = list(self.prop.values) + [1.0, 1.0, 1.0]
        cur = QColor.fromRgbF(*[max(0.0, min(1.0, float(x))) for x in vals[:3]])
        c = QColorDialog.getColor(cur, self, "选择颜色")
        if not c.isValid():
            return
        for i in range(min(3, len(self.prop.values))):
            self.prop.values[i] = [c.redF(), c.greenF(), c.blueF()][i]
            sp = self._spins[i]
            sp.blockSignals(True)
            sp.setValue(self.prop.values[i])
            sp.blockSignals(False)
        self._sync_swatch()
        self._notify()

    def _sync_swatch(self):
        if not hasattr(self, "swatch"):
            return
        vals = list(self.prop.values) + [1.0, 1.0, 1.0]
        c = QColor.fromRgbF(*[max(0.0, min(1.0, float(x))) for x in vals[:3]])
        self.swatch.setStyleSheet(f"background:{c.name()}; border:1px solid #888;")


def wrap_with_add_button(tree, text, slot):
    """把 tree 与下方一个“新增”按钮包成可放进标签页的 widget。"""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.addWidget(tree)
    btn = QPushButton(text)
    btn.clicked.connect(slot)
    v.addWidget(btn)
    return w


def relayout_offsets(mat):
    """按 d3dcompiler cbuffer 打包规则重算各参数 data_offset(与 Mdf2.save 同口径)。

    让“新参数”的 offset 显示正确, 且与实际导出完全一致。
    """
    from material_toolkit.lib.mdf2 import layout_prop_offsets
    mat.prop_block_size = layout_prop_offsets(mat.properties)


_VER_SUFFIX_RE = re.compile(r"\.(?:mmtr|mdf2|sdf)\.\d+$")


def _strip_ver_suffix(name):
    """去掉文件名尾部的版本后缀(.mmtr.1808168797 / .mdf2.10 / .sdf.* / .mmat.json), 可反复。"""
    name = os.path.basename(name or "")
    changed = True
    while changed:
        changed = False
        m = _VER_SUFFIX_RE.search(name)
        if m:
            name = name[:m.start()]
            changed = True
        elif name.endswith(".mmat.json"):
            name = name[:-len(".mmat.json")]
            changed = True
    return name


def _get_save_path(parent, title, default_name, name_filter):
    """保存对话框(**保持原生外观 + 真正的类型过滤**): 让 defext 命中“最后一个点”以杜绝叠加。

    Windows 原生框会把筛选器的扩展名当“默认扩展名(defext)”追加, 且按“最后一个点”判定
    ⇒ 多点后缀 .mmtr.1808168797 每切一次类型叠一层、已带后缀也再加; Qt 关不掉。
    故把 pattern 的多点扩展名收成“末尾一段”((*.mmtr.1808168797)->(*.1808168797)):
    原生下拉**只显示描述**(如“mmtr”)所以用户无感; 过滤仍命中(文件本就以 .1808168797 结尾);
    而 defext 与文件名末段一致 ⇒ 系统不再追加。取消/关闭返回 None。
    """
    cleaned = []
    for f in name_filter.split(";;"):
        # (*.a.b.c) -> (*.c): 描述不变; defext 命中文件名“最后一个点” => 不追加; 过滤仍生效
        cleaned.append(re.sub(r"\(\*\.([^) ]+)\)",
                              lambda m: "(*." + m.group(1).rsplit(".", 1)[-1] + ")", f))
    dlg = QFileDialog(parent, title, default_name)   # 不加 DontUseNativeDialog ⇒ 保留原生外观
    dlg.setAcceptMode(QFileDialog.AcceptSave)
    dlg.setFileMode(QFileDialog.AnyFile)
    dlg.setNameFilters(cleaned)
    if dlg.exec() != QFileDialog.Accepted:
        return None
    fs = dlg.selectedFiles()
    return fs[0] if fs else None


class MmtrPanel(QWidget):
    """mmtr 检视/编辑面板(单个文件)。

    由 MmtrTabs 承载时传入 container: 「打开 mmtr」改为在容器里新开标签页,
    文件路径变化经 title_changed 通知容器刷新标签文字。
    """

    title_changed = Signal(str)

    def __init__(self, container=None):
        super().__init__()
        self.data = None            # 当前 mmtr bytes
        self.path = None
        self._title = None          # 合成标题(克隆/新建); 空则用文件名
        self.mmtr = None            # Mmtr 对象(用于参数栏)
        self._um = []               # UserMaterial 成员缓存
        self._container = container

        hb = QHBoxLayout()
        self.btn_exp = QPushButton("导出 mmtr")
        hb.addWidget(self.btn_exp)
        hb.addStretch(1)

        self.tree_blob = QTreeWidget()
        self.tree_blob.setHeaderLabels(["#", "阶段", "大小", "组", "SRV"])
        self.tree_grp = QTreeWidget()
        self.tree_grp.setHeaderLabels(["项 / 类", "名称", "类型 / 槽位"])
        self.tree_pool = QTreeWidget()
        self.tree_pool.setHeaderLabels(["类别 / 资源名", "引用 blob"])
        self.tree_group = QTreeWidget()
        self.tree_group.setHeaderLabels(["组", "代表 / 成员", "记录·blob·内容"])
        self.tree_param = ReorderTree()
        self.tree_param.setHeaderLabels(["", "参数名", "类型", "大小", "offset"])
        # 末列不拉伸（页面宽时右侧留白，各列宽度可自由拖）
        self.tree_param.header().setStretchLastSection(False)
        # 拖拽重排(材质参数顺序; 偏移自动重算): 仅第 0 列(≡ 手柄)可发起拖拽, 防误拖。
        self.tree_param.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree_param.setDragEnabled(True)
        self.tree_param.setAcceptDrops(True)
        self.tree_param.setDropIndicatorShown(True)
        self.tree_param.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree_param.setIndentation(0)   # 扁平列表: 手柄列不被树缩进推挤(避免与参数名重叠)
        self.tree_param.setColumnWidth(0, 22)
        self.tree_variant = QTreeWidget()
        self.tree_variant.setHeaderLabels(["技术 / 变体 / 前缀", "程序 (PS·VS·HS·DS·GS·CS)", "维度 / 说明"])

        self._vocab_cache = None    # 名称池词汇表缓存(编辑后失效)

        # Blob(shader) 编辑页: 反汇编 / 编辑 asm / 汇编放回 / 导入导出
        self.ed_asm = CodeEdit(indent=2)
        self._asm_hl = AsmHighlighter(self.ed_asm.document())
        self._edit_blob = None      # 当前编辑区 asm 对应的 blob 下标
        self._asm_cache = {}        # 每 blob 一份 asm(切换 blob 时保存/恢复)
        # 诊断: 静态(即时) + 汇编(防抖, 后台线程)
        self._last_diags = []
        self._compile_errors = []   # [(行号1基, msg)]
        self._compile_ok = None     # None=未检, True/False
        self._compile_log = ""
        self._chk_gen = 0
        self._diag_busy = False     # 抑制 rehighlight() 回触发的 textChanged
        self._chk_sig = _AsmCheckSignals()
        self._chk_sig.finished.connect(self._on_compile_checked)
        self._chk_pool = QThreadPool(self)
        self._asm_timer = QTimer(self)
        self._asm_timer.setSingleShot(True)
        self._asm_timer.setInterval(180)
        self._asm_timer.timeout.connect(self._reanalyze_asm)
        self._ccheck_timer = QTimer(self)
        self._ccheck_timer.setSingleShot(True)
        self._ccheck_timer.setInterval(700)
        self._ccheck_timer.timeout.connect(self._start_compile_check)
        self.ed_asm.textChanged.connect(self._on_asm_changed)
        self.chk_trans = QCheckBox("用混合翻译器预处理")
        if find_translator() is None:
            self.chk_trans.setEnabled(False)
            self.chk_trans.setToolTip(
                "未找到 hlsl_blend_dxbc_translator.exe(可设环境变量 HLSL_BLEND_TRANSLATOR_EXE)")
        else:
            self.chk_trans.setToolTip(
                "把 HLSL+DXBC 混合写法翻回纯 asm 后再汇编(纯 asm 不受影响)")
        self.chk_trans.toggled.connect(lambda *_: self._on_asm_changed())  # 翻译开关影响检查结果
        self.chk_ccheck = QCheckBox("实时汇编检查")
        try:
            find_assembler()
            self.chk_ccheck.setChecked(True)
        except FileNotFoundError:
            self.chk_ccheck.setEnabled(False)
            self.chk_ccheck.setToolTip("未找到 D3D_Shaders.exe(汇编器), 无法实时汇编检查")
        self.chk_ccheck.toggled.connect(lambda *_: self._on_asm_changed())
        self.lbl_asm = QLabel("")
        tob = QHBoxLayout()
        self.btn_dis = QPushButton("反汇编")
        self.btn_apply = QPushButton("应用(汇编+放回)")
        self.btn_asmo = QPushButton("导出 asm")
        self.btn_asmi = QPushButton("导入 asm")
        for b in (self.btn_dis, self.btn_apply, self.btn_asmo, self.btn_asmi):
            tob.addWidget(b)
        tob.addWidget(self.chk_trans)
        tob.addWidget(self.chk_ccheck)
        tob.addStretch(1)
        tob.addWidget(self.lbl_asm)
        self.tab_blob = QWidget()
        tv = QVBoxLayout(self.tab_blob)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addLayout(tob)
        tv.addWidget(self.ed_asm, 1)

        self.tabs = QTabWidget()
        rb = QWidget()
        rv = QVBoxLayout(rb)
        rv.setContentsMargins(0, 0, 0, 0)
        rh = QHBoxLayout()
        self.btn_add_res = QPushButton("＋ 添加资源")
        self.btn_paste_res = QPushButton("粘贴资源")
        rh.addWidget(self.btn_add_res)
        rh.addWidget(self.btn_paste_res)
        rh.addStretch(1)
        rv.addLayout(rh)
        rv.addWidget(self.tree_grp, 1)
        self.tabs.addTab(rb, "资源绑定")
        self.tabs.addTab(self.tree_group, "组")
        self.tabs.addTab(self.tree_pool, "资源名池")
        self.tabs.addTab(wrap_with_add_button(self.tree_param, "＋ 新增参数", self.add_param),
                         "材质参数")
        self.tabs.addTab(self.tree_variant, "变体")
        self.tabs.addTab(self.tab_blob, "Blob(shader)")

        split = ContentSplitter(self)
        split.addWidget(self.tree_blob)
        split.addWidget(self.tabs)
        split.set_providers([
            lambda: self._tree_min_width(self.tree_blob),
            lambda: self._right_min_width(),
        ])
        self._split = split
        self.tabs.currentChanged.connect(lambda *_: self._relayout())

        lay = QVBoxLayout(self)
        lay.addLayout(hb)
        lay.addWidget(split, 1)

        self.btn_exp.clicked.connect(self.export_mmtr)
        self.btn_add_res.clicked.connect(self.add_resource_dialog)
        self.btn_paste_res.clicked.connect(self.paste_resource)
        self.btn_dis.clicked.connect(self.disasm_cur_blob)
        self.btn_apply.clicked.connect(self.apply_cur_blob)
        self.btn_asmo.clicked.connect(self.export_asm)
        self.btn_asmi.clicked.connect(self.import_asm)
        self.tree_blob.currentItemChanged.connect(lambda *_: self.refresh_detail())
        attach_menu(self.tree_blob, self._menu_blob)
        attach_menu(self.tree_grp, self._menu_grp)
        attach_menu(self.tree_group, self._menu_bgrp)
        attach_menu(self.tree_pool, self._menu_pool)
        attach_menu(self.tree_param, self._menu_param)
        self.tree_param.reordered.connect(self._on_param_moved)

    # ---- 打开 / 导出 ----
    def doc_title(self):
        """标签页标题: 优先合成标题(克隆新建); 否则文件名; 未加载用(未打开)。"""
        if self._title:
            return self._title
        return os.path.basename(self.path) if self.path else "(未打开)"

    def _emit_title(self):
        self.title_changed.emit(self.doc_title())

    def load_path(self, path):
        """打开文件: 读文件并载入。"""
        self.load_data(open(path, "rb").read(), path=path)

    def load_data(self, data, path=None, title=None):
        """从 bytes 载入(打开文件或克隆新建); path=None 表示未保存的新文件。"""
        self.data = bytes(data)
        self.path = path
        self._title = title
        self.mmtr = Mmtr.from_bytes(self.data)
        self._um = self.mmtr.cbuffer_members("UserMaterial")
        self._vocab_cache = None
        self._vocab_cache_rdef = None
        self._asm_cache = {}
        self.ed_asm.clear()
        self._edit_blob = None
        self.refresh_blobs()
        self.refresh_pool()
        self.refresh_group_page()
        self.refresh_variant()
        self._emit_title()

    def open_mmtr(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "打开 mmtr/SDF", "",
            "mmtr/SDF (*.mmtr.* *.sdf.*);;mmtr (*.mmtr.*);;SDF (*.sdf.*);;所有文件 (*)")
        if not p:
            return
        if self._container is not None:
            self._container.open_path(p)      # 容器接管: 每个文件一个新标签页
        else:
            self.load_path(p)

    def export_mmtr(self):
        if self.data is None:
            return
        p = _get_save_path(self, "导出 mmtr", self.path or "out.mmtr.1808168797",
                           "mmtr/SDF (*.1808168797);;所有文件 (*)")
        if p:
            open(p, "wb").write(self.data)
            QMessageBox.information(self, "导出", f"已写出 {len(self.data)} 字节:\n{p}")

    # ---- 视图 ----
    def refresh_blobs(self):
        keep = self.cur_blob()          # 重刷新时保留当前选中(避免改名后跳回第 0 个)
        self.tree_blob.clear()
        gcount = blob_group_counts(self.data)
        for i in range(blob_count(self.data)):
            bi = blob_info(self.data, i)
            it = QTreeWidgetItem([str(i), bi["stage"], str(bi["size"]),
                                  str(gcount.get(bi["off"], 0)), str(bi["n_br"])])
            it.setData(0, Qt.UserRole, i)
            self.tree_blob.addTopLevelItem(it)
        fit_columns(self.tree_blob, [0, 1, 2, 3, 4], pad=12, min_w=36, max_w=150)
        self._relayout()
        n = self.tree_blob.topLevelItemCount()
        if n:
            row = keep if isinstance(keep, int) and 0 <= keep < n else 0
            self.tree_blob.setCurrentItem(self.tree_blob.topLevelItem(row))

    def refresh_variant(self):
        """变体页(通用): 技术 -> 程序集(PS·VS·CS)。

        - 通用: 列出每个技术的程序集(第1列=变体名, 便于按名定位 blob);
        - 若识别出材质"pass"(mmtr): 按 pass 分组; 否则(SDF/未识别)直接平铺;
        - 前缀列仅对材质有意义(mdf2 flags); 无标志时显示 (无标志)。
        """
        tree = self.tree_variant
        tree.clear()
        if self.data is None:
            return
        mm = MaterialModel(self.data)
        s = mm.summary()
        prefs = s["prefix_counts"]
        pre_txt = ("前缀: " + " ".join("%s=%d" % (k or "''", v) for k, v in prefs.items())
                   if prefs and set(prefs) - {""} else "")
        info = QTreeWidgetItem(
            ["容器: 技术=%d  记录=%d(空槽=%d)  blob=%d"
             % (s["technologies"], s["records"], s["empty_records"], s["blobs"]),
             "变体记录=%d" % s["variant_records"], pre_txt])
        info.setToolTip(2, "前缀(mdf2 flags): bit1(BaseAlphaTestEnable)->A, "
                           "bit0(BaseTwoSideEnable)->TS; 空=无标志")
        tree.addTopLevelItem(info)
        bp = mm.by_pass()
        material_like = any(p != "(none)" for p in bp)

        def add_tech(parent, tech):
            d = parse_technology(tech)
            sets = mm.program_sets(tech)
            titem = QTreeWidgetItem(
                [tech, "程序集=%d" % len(sets),
                 self._dim_label(d) if material_like else ""])
            parent.addChild(titem)
            for g in sets:
                ps, vs, hs, ds, gs, cs = g["programs"]
                plab = ",".join(p for p in g["prefixes"] if p and p != "-") or "(无标志)"
                progs = ["PS=%d" % ps, "VS=%d" % vs]
                for lab, val in (("HS", hs), ("DS", ds), ("GS", gs), ("CS", cs)):
                    if val >= 0:
                        progs.append("%s=%d" % (lab, val))
                citem = QTreeWidgetItem(
                    ["<- %s" % plab, "  ".join(progs),
                     "slots=%s" % ",".join(str(x) for x in g["slots"])])
                titem.addChild(citem)

        if material_like:
            for p, techs in bp.items():
                pitem = QTreeWidgetItem(["pass: %s" % p, "%d 技术" % len(techs), ""])
                tree.addTopLevelItem(pitem)
                for tech in techs:
                    add_tech(pitem, tech)
        else:
            root = QTreeWidgetItem(["(全部技术)", "%d 技术" % len(mm.technologies()), ""])
            tree.addTopLevelItem(root)
            for tech in mm.technologies():
                add_tech(root, tech)
        tree.expandToDepth(1)
        fit_columns(tree, [0, 1, 2], pad=24, min_w=90, max_w=680)
        self._relayout()

    @staticmethod
    def _dim_label(d):
        """parse_technology 结果 -> 可读维度串(用于变体页第 3 列)。"""
        parts = []
        if d["input"]:
            parts.append("input=%s" % d["input"])
        for k, lab in (("instancing", "Instancing"), ("clip", "Clip"),
                       ("lw", "LW"), ("with_norm", "WithNorm"), ("cs", "CS")):
            if d[k]:
                parts.append(lab)
        if d["variant_tag"]:
            parts.append("tag=%s" % d["variant_tag"])
        if d["unknown"]:
            parts.append("?? %s" % "".join(d["unknown"]))
        return " ".join(parts)

    def cur_blob(self):
        it = self.tree_blob.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _sync_editor_to_blob(self, idx):
        """切到某个 blob 时, 编辑区显示该 blob 自己那份 asm(没反汇编过则清空)。

        每个 blob 各存一份 asm(含未应用的编辑): 切走时保存, 切回时恢复。
        """
        if idx == self._edit_blob:
            return
        if self._edit_blob is not None:
            self._asm_cache[self._edit_blob] = self.ed_asm.toPlainText()
        self._edit_blob = idx
        self.ed_asm.setPlainText(self._asm_cache.get(idx, "") if idx is not None else "")

    def refresh_detail(self):
        self._sync_editor_to_blob(self.cur_blob())
        self.refresh_groups()
        self.refresh_params()
        self._relayout()

    # ---- 两栏“内容完整显示所需最小宽度” ----
    @staticmethod
    def _tree_min_width(tree, slack=26):
        """该树“完整显示内容”所需的最小宽度(自然内容宽, 不含当前拉伸)。"""
        if not isinstance(tree, QTreeWidget):
            return 0
        nc = tree.columnCount()
        if nc == 0:
            return 0
        fm = tree.fontMetrics()
        hdr = tree.headerItem()
        w = 0
        for c in range(nc):
            cw = tree.sizeHintForColumn(c)
            if hdr is not None:
                cw = max(cw, fm.horizontalAdvance(hdr.text(c)))
            w += cw + tree.indentation()
        w += 2 * tree.frameWidth()
        sb = tree.verticalScrollBar()
        if sb is not None:
            w += sb.sizeHint().width()
        return w + slack

    def _right_min_width(self):
        w = self.tabs.currentWidget()
        mw = self._tree_min_width(w) if isinstance(w, QTreeWidget) else 0
        return max(mw, 360)

    def _relayout(self):
        sp = getattr(self, "_split", None)
        if sp is not None:
            sp.relayout()

    def _blob_cbuffer_members(self, idx):
        """当前 blob 的 RDEF cbuffer -> {cbuffer 名: [(成员, offset, size), ...]}。"""
        try:
            cbs = rdef_cbuffers(extract_blob(self.data, idx)) or []
        except Exception:  # noqa: BLE001
            return {}
        return {name: mem for (name, _size, mem) in cbs}

    def refresh_groups(self):
        """资源绑定页: 当前 blob 的 RDEF 声明的资源绑定(cbuffer/sampler/SRV/UAV)。

        只读展示。RDEF = 该 shader 自己声明的资源(引擎据此 + mdf2/系统 提供实际资源);
        容器里的“绑定组 / 资源名池”都是这些声明的派生数据(另行生成)。
        """
        self.tree_grp.clear()
        idx = self.cur_blob()
        if idx is None or self.data is None:
            return
        try:
            blob = extract_blob(self.data, idx)
            info = rdef_bind_info(blob) or []
            stage = rdef_stage(blob)
        except Exception:  # noqa: BLE001
            info, stage = [], None
        cb_members = self._blob_cbuffer_members(idx)
        cats = (("cbuffer", (0,), "b"), ("sampler", (3,), "s"),
                ("SRV", (1, 2, 5, 7), "t"), ("UAV", (4, 6, 8, 9, 10, 11), "u"))
        buckets = {name: [] for name, _t, _p in cats}
        for (nm, t, bp, dim, _ret) in info:
            for name, types, _p in cats:
                if t in types:
                    buckets[name].append((nm, bp, dim))
                    break
        head = QTreeWidgetItem(
            [f"RDEF · {stage or '?'}", "",
             f"cb={len(buckets['cbuffer'])} smp={len(buckets['sampler'])} "
             f"srv={len(buckets['SRV'])} uav={len(buckets['UAV'])}"])
        head.setData(0, Qt.UserRole, ("rdefhdr",))
        self.tree_grp.addTopLevelItem(head)
        for name, _types, pfx in cats:
            items = buckets[name]
            if not items:
                continue
            top = QTreeWidgetItem([name, "", f"{len(items)} 项"])
            top.setData(0, Qt.UserRole, ("rdefcat", name))
            self.tree_grp.addTopLevelItem(top)
            for (nm, bp, dim) in items:
                desc = f"{name.lower()} {pfx}{bp}"
                if name in ("SRV", "UAV") and _DIMNAME.get(dim, "?") != "?":
                    desc += f" · {_DIMNAME[dim]}"
                it = QTreeWidgetItem([f"[{pfx}{bp}]", nm, desc])
                it.setData(0, Qt.UserRole, ("rdef", name, bp, nm))
                top.addChild(it)
                if name == "cbuffer":
                    for (mn, mo, ms) in cb_members.get(nm, []):
                        it.addChild(QTreeWidgetItem(["", mn, f"@off {mo}  size {ms}"]))
            top.setExpanded(True)
        fit_columns(self.tree_grp, [0, 2], pad=28, min_w=120, max_w=520)
        self.tree_grp.setColumnWidth(1, 210)

    def refresh_pool(self):
        """资源名池页: 由 RDEF 程序化派生的资源名, 按类别展开(cbuffer/sampler/SRV/UAV)。

        名 -> 引用 blob 数(= 真正声明它的 shader 数); 未被引用的 blob 不计。
        名可属多类时归入首个类别(cb>smp>tex>uav)。
        """
        self.tree_pool.clear()
        if self.data is None:
            return
        try:
            vocab = derive_namepool(self.data)
        except Exception:  # noqa: BLE001
            return
        order = ("cb", "smp", "tex", "uav")
        title = {"cb": "cbuffer", "smp": "sampler", "tex": "SRV", "uav": "UAV"}
        buckets = {c: [] for c in order}
        for nm, d in vocab.items():
            c = next((x for x in order if x in d["cats"]), None)
            if c:
                buckets[c].append((nm, len(d["blobs"])))
        for c in order:
            items = buckets[c]
            if not items:
                continue
            items.sort(key=lambda t: (-t[1], t[0]))
            top = QTreeWidgetItem([title[c], f"{len(items)} 项"])
            top.setData(0, Qt.UserRole, ("poolcat", c))
            self.tree_pool.addTopLevelItem(top)
            for nm, cnt in items:
                it = QTreeWidgetItem([nm, str(cnt)])
                it.setData(0, Qt.UserRole, ("poolname", nm))
                top.addChild(it)
            top.setExpanded(True)
        fit_columns(self.tree_pool, [0, 1], pad=20, min_w=90, max_w=560)

    def refresh_group_page(self):
        """组页(只读): 由 RDEF 程序化派生的绑定组(按“资源声明签名”去重)。

        组无固有名 ⇒ 用“组N + 代表成员名(等K种)”标识; 展开看内容(池条目, 按类分组)。
        组 = 一次 draw 的完整绑定状态, 被跨 pass 的多个变体记录共享。
        """
        self.tree_group.clear()
        if self.data is None:
            return
        try:
            groups = derive_groups(self.data)
        except Exception:  # noqa: BLE001
            return
        name_of = {}
        try:
            for r in MmtrModel(self.data).parse_records():
                # ⚠ 不能用 is_empty 过滤(只看 P0): CS/HS/DS/GS-only 记录会被漏 => 组显示 "?"
                name_of[r.off] = r.name or "?"
        except Exception:  # noqa: BLE001
            pass
        rows = [(g, sorted({name_of.get(o, "?") for o in g["recs"]})) for g in groups.values()]
        rows.sort(key=lambda t: (-t[0]["n_rec"], t[0]["recs"][0]))
        pfx = {"cb": "b", "smp": "s", "tex": "t", "uav": "u"}
        for i, (g, names) in enumerate(rows):
            cnt = Counter(e["cat"] for e in g["entries"])
            rep = names[0] if names else "?"
            more = f" 等{len(names)}种" if len(names) > 1 else ""
            top = QTreeWidgetItem(
                [f"组{i}", f"{rep}{more}",
                 f"{g['n_rec']} rec · {len(g['blobs'])} blob · "
                 f"cb{cnt['cb']} smp{cnt['smp']} tex{cnt['tex']} uav{cnt['uav']}"])
            top.setData(0, Qt.UserRole, ("bgrp", i, g["recs"][0]))
            self.tree_group.addTopLevelItem(top)
            for c in ("cb", "smp", "tex", "uav"):
                es = [e for e in g["entries"] if e["cat"] == c]
                if not es:
                    continue
                ctop = QTreeWidgetItem([c, "", f"{len(es)} 项"])
                top.addChild(ctop)
                for e in es:
                    stg = stage_text(e["stage"])
                    ctop.addChild(QTreeWidgetItem(
                        [f"[{pfx[c]}{e['slot']}]", e["name"],
                         f"{c} {pfx[c]}{e['slot']} · {stg}"]))
        fit_columns(self.tree_group, [0, 1], pad=20, min_w=90, max_w=560)

    def _reload_after_edit(self, full=False):
        """改 bytes 后统一刷新。full=True 才重建 blob 列表(仅 blob 计数/结构变化时需要)。"""
        self.mmtr = Mmtr.from_bytes(self.data)
        self._um = self.mmtr.cbuffer_members("UserMaterial")
        self._vocab_cache = None
        self._vocab_cache_rdef = None
        if full:
            self.refresh_blobs()
        self.refresh_detail()
        self.refresh_pool()
        self.refresh_group_page()

    def _reload_after_rename(self):
        """改名(不动参数/blob 结构)后的轻量刷新: 只刷绑定页与名称池(免整表重解析卡顿)。"""
        self._vocab_cache = None
        self._vocab_cache_rdef = None
        self.refresh_groups()
        self.refresh_pool()
        self.refresh_group_page()

    def refresh_params(self):
        self.tree_param.clear()
        for pr in self._um:
            it = QTreeWidgetItem(["", pr.name, type_label(pr.size), f"{pr.size}B",
                                  f"0x{pr.offset:04x}"])
            it.setIcon(0, _grip_icon())
            it.setFlags(it.flags() & ~Qt.ItemIsDropEnabled)   # 仅同级重排(不嵌套)
            self.tree_param.addTopLevelItem(it)
        fit_columns(self.tree_param, [1, 2, 3, 4], pad=24, min_w=80, max_w=320)

    def _on_param_moved(self, *_a):
        """材质参数页拖拽重排(仅手柄列可拖): 读当前行顺序 -> 重算偏移并写回容器。"""
        if self.data is None:
            return
        order = []

        def _collect(it):
            order.append(it.text(1))
            for j in range(it.childCount()):
                _collect(it.child(j))

        for i in range(self.tree_param.topLevelItemCount()):
            _collect(self.tree_param.topLevelItem(i))
        if order == [pr.name for pr in self._um]:
            return
        try:
            self.data = Mmtr.from_bytes(self.data).reorder_cbuffer_params(
                "UserMaterial", order)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "重排失败", str(e))
            self.refresh_params()          # 回滚显示
            return
        self._reload_after_edit()
        self.refresh_params()

    def remove_param(self, name):
        """删除 UserMaterial 某参数(就地压缩 + 偏移重算)。"""
        if self.data is None:
            return
        if QMessageBox.question(self, "删除参数",
                                "删除 UserMaterial 参数 %s ?" % name) != QMessageBox.Yes:
            return
        try:
            self.data = Mmtr.from_bytes(self.data).remove_cbuffer_param("UserMaterial", name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_edit()
        self.refresh_params()

    # ---- 编辑 ----
    def _need_mmtr(self):
        if self.data is None:
            QMessageBox.warning(self, "提示", "请先打开一个 mmtr")
            return False
        return True

    # ---- 右键菜单 ----
    def _menu_blob(self, item):
        if item is None or self.data is None:
            return None
        return [("复制 blob 信息",
                 lambda: _copy_to_clipboard(
                     f"blob {item.text(0)} {item.text(1)} size={item.text(2)} "
                     f"groups={item.text(3)} srv={item.text(4)}"))]

    def _menu_grp(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        if not d:
            return None
        if d[0] == "rdef":
            _tag, cat, _slot, name = d
            items = [("复制资源信息(跨文件粘贴用)", lambda: self.copy_resource_info(name)),
                     ("复制 名字+槽位",
                      lambda: _copy_to_clipboard(f"{cat} {name} @ {item.text(0)}")),
                     ("复制名", lambda: _copy_to_clipboard(name))]
            if cat in ("SRV", "sampler"):
                # 仅 SRV/sampler 支持删除; cbuffer/UAV 属 DXBC 层(需同时改 shader), 不暴露。
                items.insert(0, ("删除资源", lambda: self.delete_resource(name)))
            return items
        return None

    def _menu_bgrp(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        if not d or d[0] != "bgrp":
            return None
        return [("复制行", lambda: _copy_to_clipboard(
                    " | ".join(item.text(c) for c in range(3))))]

    def _menu_pool(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        if not d or d[0] != "poolname":
            return None
        nm = d[1]
        return [("全局改名…", lambda: self.global_rename(nm)),
                ("复制名", lambda: _copy_to_clipboard(nm))]

    def global_rename(self, old):
        """名称池页: 把某贴图名在所有组里全局改名(弹对话框)。"""
        if not self._need_mmtr():
            return
        new, ok = QInputDialog.getText(self, "全局改名", f"把 {old} 在所有组里改名为:")
        if not (ok and new and new != old):
            return
        self._apply_global_rename(old, new)

    def _apply_global_rename(self, old, new):
        """执行全局改名(无对话框; 便于测试)。"""
        try:
            self.data = rename_name_global(self.data, old, new)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_rename()

    def _menu_param(self, item):
        acts = [("新增参数(UserMaterial)", self.add_param)]
        if item is not None:
            acts.append(("删除参数", lambda: self.remove_param(item.text(1))))
            acts.append(("复制行",
                         lambda: _copy_to_clipboard(
                             " | ".join(item.text(c) for c in range(1, 5)))))
        return acts

    def add_param(self):
        if not self._need_mmtr():
            return
        name, ok = QInputDialog.getText(self, "新增参数", "参数名(加入 UserMaterial):")
        if not (ok and name):
            return
        ty = pick_type(self, "新增参数")
        if ty is None:
            return
        size = type_size(ty)
        m = Mmtr.from_bytes(self.data)     # 用最新字节(改名后 self.mmtr 可能落后)
        entries = m._scan_cbuffer_entries("UserMaterial")
        offset = entries[0][3] if entries else 0   # 追加到现有成员之后
        try:
            self.data = m.add_cbuffer_param("UserMaterial", name, size, offset)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_edit()

    def add_resource_dialog(self):
        """资源绑定页: 给当前 blob 的 RDEF 加一条绑定资源(选类别 + 名), 再**按 RDEF 重建整个容器**。

        可直接选容器现有名, 也可自定义。cbuffer 不在列(需定义表/改 shader 源, 见 mmtr_rdefgen 注释)。
        """
        if self.data is None:
            QMessageBox.warning(self, "提示", "请先打开 mmtr/SDF")
            return
        idx = self.cur_blob()
        lab, ok = QInputDialog.getItem(self, "添加资源", "类别:",
                                       [t for _k, t in RESOURCE_LABELS], 0, False)
        if not ok:
            return
        cat = next(k for k, t in RESOURCE_LABELS if t == lab)
        stride = None
        if RESOURCE_KINDS[cat].get("stride"):
            stride, ok = QInputDialog.getInt(self, "添加资源",
                                             "结构体步长 stride(字节):", 80, 1, 1 << 20)
            if not ok:
                return
        try:
            vocab = derive_namepool(self.data)
            items = sorted(nm for nm, d in vocab.items()
                           if ("smp" in d["cats"]) == (cat == "smp"))
        except Exception:  # noqa: BLE001
            items = []
        name, ok = QInputDialog.getItem(
            self, "添加资源", "名称(= mdf2 的类型; 可选现有或自定义):", items, 0, True)
        if not (ok and name):
            return
        try:
            self.data = add_resource(self.data, idx, cat, name, stride=stride)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_edit(full=True)
        QMessageBox.information(self, "添加资源", "已加 %s(%s), 并重建容器。" % (name, cat))

    def copy_resource_info(self, name):
        """复制某资源在 RDEF 里的"全部信息"(跨文件粘贴用)。"""
        if self.data is None:
            return
        try:
            spec = resource_spec(self.data, self.cur_blob(), name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "提示", str(e))
            return
        _copy_to_clipboard(spec)
        QMessageBox.information(self, "复制资源", "已复制(可到别的文件「粘贴资源」):\n%s" % spec)

    def paste_resource(self):
        """从剪贴板粘贴一个资源(全部信息)加到当前 blob。"""
        if self.data is None:
            QMessageBox.warning(self, "提示", "请先打开 mmtr/SDF")
            return
        clp = QApplication.clipboard()
        spec = parse_spec(clp.text() if clp else "")
        if spec is None:
            QMessageBox.warning(self, "提示", "剪贴板不是资源信息(先用「复制资源信息」复制)。")
            return
        name, ty, ret, dim, nsamp, flags = spec
        try:
            self.data = add_raw_resource(self.data, self.cur_blob(), name,
                                         ty, ret, dim, nsamp, flags)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_edit(full=True)
        QMessageBox.information(self, "粘贴资源", "已粘贴 %s, 并重建容器。" % name)

    def delete_resource(self, name):
        """从当前 blob 的 RDEF 删除某资源, 并重建容器。"""
        if self.data is None:
            return
        if QMessageBox.question(self, "删除资源",
                                "从当前 blob 删除资源 %s ?" % name) != QMessageBox.Yes:
            return
        try:
            self.data = remove_resource(self.data, self.cur_blob(), name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_edit(full=True)
        QMessageBox.information(self, "删除资源", "已删除 %s, 并重建容器。" % name)

    # ---- Blob(shader) 编辑 ----
    def disasm_cur_blob(self):
        if not self._need_mmtr():
            return
        idx = self.cur_blob()
        if idx is None:
            QMessageBox.warning(self, "提示", "请先在左侧选择一个 blob")
            return
        try:
            asm = disassemble_dxbc(extract_blob(self.data, idx))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "反汇编失败", str(e))
            return
        self._edit_blob = idx
        self.ed_asm.setPlainText(asm)
        self._asm_cache[idx] = asm
        QMessageBox.information(self, "反汇编",
                                f"blob[{idx}] 已反汇编({len(asm)} 字符), 编辑后点「应用」。")

    def apply_cur_blob(self):
        if not self._need_mmtr():
            return
        idx = self.cur_blob()
        if idx is None:
            return
        if self._edit_blob not in (None, idx):
            if QMessageBox.question(
                    self, "提示",
                    f"当前 asm 是对 blob[{self._edit_blob}] 反汇编的, 选中的却是 blob[{idx}]。\n"
                    f"继续会以 blob[{idx}] 为基准汇编放回。继续?") != QMessageBox.Yes:
                return
        text = self.ed_asm.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, "提示", "asm 为空")
            return
        diags = analyze_asm(text)
        if diags:
            lst = "\n".join(f"  第 {d.line + 1} 行: {d.msg}" for d in diags[:10])
            more = "" if len(diags) <= 10 else f"\n  … 共 {len(diags)} 处"
            if QMessageBox.warning(
                    self, "检测到疑似语法错误",
                    f"静态检查发现 {len(diags)} 处疑似错误:\n{lst}{more}\n\n"
                    "仍要应用吗?", QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        ref = extract_blob(self.data, idx)
        try:
            if self.chk_trans.isChecked() and find_translator():
                text = run_translator(text)
            blob = assemble_asm(text, ref_dxbc=ref)
            v = verify_dxbc(blob)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "汇编失败", str(e))
            return
        if not (v["disasm_ok"] and v["strip_ok"] and v["reflect_ok"]):
            if QMessageBox.question(
                    self, "D3D 校验未过",
                    f"disasm={v['disasm_ok']} strip={v['strip_ok']} "
                    f"reflect={v['reflect_ok']}。\n仍要放回吗?") != QMessageBox.Yes:
                return
        try:
            self.data = replace_blob(self.data, idx, blob)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "放回失败", str(e))
            return
        self._asm_cache[idx] = self.ed_asm.toPlainText()   # 保留该 blob 的 asm(已应用版本)
        self._edit_blob = idx
        self._reload_after_edit(full=True)
        QMessageBox.information(self, "应用", f"blob[{idx}] 已放回({len(blob)} 字节)")

    def export_asm(self):
        if not self.ed_asm.toPlainText().strip():
            QMessageBox.warning(self, "提示", "没有 asm 可导出")
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "导出 asm", f"blob_{self.cur_blob()}.asm.txt",
            "asm (*.asm *.asm.txt *.txt);;所有文件 (*)")
        if p:
            open(p, "w", encoding="utf-8").write(self.ed_asm.toPlainText())
            QMessageBox.information(self, "导出", f"已写出:\n{p}")

    def import_asm(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "导入 asm", "", "asm (*.asm *.asm.txt *.txt);;所有文件 (*)")
        if p:
            idx = self.cur_blob()
            self._edit_blob = idx
            self.ed_asm.setPlainText(open(p, encoding="utf-8", errors="replace").read())
            if idx is not None:
                self._asm_cache[idx] = self.ed_asm.toPlainText()

    # ---- Blob(shader) 诊断(静态 + 汇编) ----
    def _ccheck_enabled(self):
        return self.chk_ccheck.isEnabled() and self.chk_ccheck.isChecked()

    def _on_asm_changed(self):
        """文本变化: 立即排队静态检查(180ms) + 作废在途汇编结果 + 重排汇编检查。"""
        if self._diag_busy:               # rehighlight() 会回触发 textChanged, 需忽略
            return
        self._asm_timer.start()
        self._chk_gen += 1
        self._compile_errors = []
        self._compile_ok = None
        self._compile_log = ""
        if self._ccheck_enabled():
            self._ccheck_timer.start()
        else:
            self._ccheck_timer.stop()
            self._refresh_diags()

    def _reanalyze_asm(self):
        self._refresh_diags()

    def _refresh_diags(self):
        """合并静态诊断 + 汇编诊断 -> 高亮器标注 + 状态栏。"""
        text = self.ed_asm.toPlainText()
        static = analyze_asm(text)
        lines = text.splitlines()
        for (ln, msg) in self._compile_errors:
            i = ln - 1
            if 0 <= i < len(lines):
                raw = lines[i]
                s = len(raw) - len(raw.lstrip())
                static.append(Diag(i, s, len(raw), "汇编: " + msg))
        self._last_diags = static
        self._diag_busy = True            # rehighlight() 会回触发 textChanged
        try:
            self._asm_hl.set_diagnostics(static)
        finally:
            self._diag_busy = False
        self._update_asm_status()

    def _update_asm_status(self):
        if not self.ed_asm.toPlainText().strip():
            self.lbl_asm.setText("")
            self.lbl_asm.setToolTip("")
            return
        n_s = sum(1 for d in self._last_diags if not d.msg.startswith("汇编:"))
        n_c = len(self._compile_errors)
        parts = ["静态: " + ("OK" if n_s == 0 else f"{n_s} 处")]
        if self._compile_ok is None:
            parts.append("汇编: …")
        elif self._compile_ok:
            parts.append("汇编: OK")
        else:
            parts.append("汇编: " + (f"{n_c} 处" if n_c else "失败"))
        bad = n_s + n_c
        self.lbl_asm.setText(("[✓] " if bad == 0 and self._compile_ok else "[⚠] ")
                             + " | ".join(parts))
        tip = "\n".join(f"第 {d.line + 1} 行: {d.msg}" for d in self._last_diags[:20])
        if self._compile_log and not self._compile_ok:
            tip += "\n\n--- 汇编日志 ---\n" + self._compile_log[-1500:]
        self.lbl_asm.setToolTip(tip)

    def _start_compile_check(self):
        if not self._ccheck_enabled():
            return
        text = self.ed_asm.toPlainText()
        if not text.strip():
            self._compile_ok = None
            self._refresh_diags()
            return
        idx = self._edit_blob if self._edit_blob is not None else self.cur_blob()
        ref = None
        if idx is not None and self.data is not None:
            try:
                ref = extract_blob(self.data, idx)
            except Exception:  # noqa: BLE001
                ref = None
        task = _AsmCheckTask(self._chk_gen, text,
                             self.chk_trans.isChecked(), ref, self._chk_sig)
        self._chk_pool.start(task)

    def _on_compile_checked(self, gen, ok, errors, log):
        if gen != self._chk_gen:      # 文本已变, 丢弃过期结果
            return
        self._compile_ok = ok
        self._compile_errors = errors
        self._compile_log = log
        self._refresh_diags()


class Mdf2Panel(QWidget):
    """mdf2 检视/编辑面板(单个文件)。

    由 Mdf2Tabs 承载时传入 container: 「打开 mdf2」改为在容器里新开标签页,
    文件路径/标题变化经 title_changed 通知容器刷新标签文字。
    """

    title_changed = Signal(str)

    def __init__(self, container=None):
        super().__init__()
        self.obj = None
        self.path = None
        self._container = container

        hb = QHBoxLayout()
        self.btn_open = QPushButton("打开 mdf2")
        self.btn_set = QPushButton("设置/新增贴图槽")
        self.btn_exp = QPushButton("导出 mdf2")
        for b in (self.btn_open, self.btn_set, self.btn_exp):
            hb.addWidget(b)
        hb.addStretch(1)

        self.tree_mat = QTreeWidget()
        self.tree_mat.setHeaderLabels(["材质"])
        # 材质名双击内联改名
        self.tree_mat.setItemDelegate(
            InlineNameDelegate(self.tree_mat, 0, self._mat_name,
                               self._commit_mat_rename))
        self.tree_tex = QTreeWidget()
        self.tree_tex.setHeaderLabels(["贴图槽(type)", "贴图路径"])
        # 贴图槽 type 双击内联改名
        self.tree_tex.setItemDelegate(
            InlineNameDelegate(self.tree_tex, 0, self._tex_name,
                               self._commit_tex_rename))
        self.tree_param = ReorderTree()
        self.tree_param.setHeaderLabels(["", "参数名", "类型", "值", "offset"])
        # 末列不拉伸（页面宽时右侧留白，各列宽度可自由拖）
        self.tree_param.header().setStretchLastSection(False)
        # 拖拽重排(仅第0列 ≡ 手柄可发起拖拽, 防误拖)
        self.tree_param.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree_param.setDragEnabled(True)
        self.tree_param.setAcceptDrops(True)
        self.tree_param.setDropIndicatorShown(True)
        self.tree_param.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree_param.setIndentation(0)
        self.tree_param.setColumnWidth(0, 22)
        # 参数名双击内联改名(预选原名); 类型/值列为常驻控件
        self.tree_param.setItemDelegate(
            InlineNameDelegate(self.tree_param, 1, self._param_name,
                               self._commit_param_rename))

        # 材质属性页: 着色类型 + flags 位 + Tess/Phong
        self.tab_matprops = QWidget()
        mv = QVBoxLayout(self.tab_matprops)
        rowt = QHBoxLayout()
        rowt.addWidget(QLabel("着色类型 (shaderType)"))
        self.cmb_shading = NoWheelComboBox()
        self.cmb_shading.addItems([n for n, _ in SHADING_TYPES])
        rowt.addWidget(self.cmb_shading)
        rowt.addStretch(1)
        mv.addLayout(rowt)
        # MasterMaterial = 该材质引用的 mmtr 路径(MDF-Manager 的命名; 导出时进字符串表)
        rowm = QHBoxLayout()
        rowm.addWidget(QLabel("MasterMaterial (mmtr 路径)"))
        self.ed_mmtr = QLineEdit()
        self.ed_mmtr.setPlaceholderText("材质引用的 mmtr 路径(留空 = 无)")
        self.ed_mmtr.editingFinished.connect(self._apply_mmtr_path)
        rowm.addWidget(self.ed_mmtr, 1)
        mv.addLayout(rowm)
        gb = QGroupBox("材质 flags")
        grid = QGridLayout(gb)
        self.flag_checks = {}
        self.flag_spins = {}
        ints = []
        r = c = 0
        for name, width in MATERIAL_FLAG_FIELDS:
            if width == 1:
                cb = QCheckBox(name)
                cb.toggled.connect(lambda v, nm=name: self._apply_flag(nm, v))
                self.flag_checks[name] = cb
                grid.addWidget(cb, r, c)
                c += 1
                if c >= 3:
                    c = 0
                    r += 1
            else:
                ints.append((name, width))
        if c != 0:
            r += 1
        for name, width in ints:
            sp = QSpinBox()
            sp.setRange(0, (1 << width) - 1)
            sp.valueChanged.connect(lambda v, nm=name: self._apply_flag(nm, v))
            self.flag_spins[name] = sp
            grid.addWidget(QLabel(name), r, 0)
            grid.addWidget(sp, r, 1)
            r += 1
        mv.addWidget(gb)
        mv.addStretch(1)
        self.cmb_shading.currentTextChanged.connect(self._apply_shading)

        self.tabs = QTabWidget()
        self.tabs.addTab(wrap_with_add_button(self.tree_tex, "＋ 新增贴图槽", self.set_texture),
                         "贴图槽")
        self.tabs.addTab(wrap_with_add_button(self.tree_param, "＋ 新增参数", self.add_param),
                         "材质参数")
        self.tabs.addTab(self.tab_matprops, "材质属性")

        self.tree_mat_wrap = wrap_with_add_button(self.tree_mat, "＋ 新增材质",
                                                  self.add_material_gui)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree_mat_wrap)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.addLayout(hb)
        lay.addWidget(split, 1)

        self.btn_open.clicked.connect(self.open_mdf2)
        self.btn_set.clicked.connect(self.set_texture)
        self.btn_exp.clicked.connect(self.export_mdf2)
        self.tree_mat.currentItemChanged.connect(lambda *_: self.refresh_detail())
        attach_menu(self.tree_mat, self._menu_mat)
        attach_menu(self.tree_tex, self._menu_tex)
        attach_menu(self.tree_param, self._menu_param)
        self.tree_param.reordered.connect(self._on_param_moved)

    def doc_title(self):
        """标签页标题: 已打开文件用文件名; 未命名(新建)用 NewMDF2.mdf2.10。"""
        return os.path.basename(self.path) if self.path else "NewMDF2.mdf2.10"

    def _emit_title(self):
        self.title_changed.emit(self.doc_title())

    def load_path(self, path):
        self.obj = Mdf2.load(path)
        self.path = path
        self.refresh_mats()
        self._emit_title()

    def open_mdf2(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 mdf2", "", "mdf2 (*.mdf2.*);;所有文件 (*)")
        if not p:
            return
        if self._container is not None:
            self._container.open_path(p)      # 容器接管: 每个文件一个新标签页
        else:
            self.load_path(p)

    def reset_new(self):
        """把本面板重置为一个空的 mdf2(DMC5, version 10)。"""
        self.obj = Mdf2.new(10)
        self.path = None
        self.refresh_mats()
        self._emit_title()

    def refresh_mats(self):
        keep = self.cur_mat()
        self.tree_mat.clear()
        for m in self.obj.materials:
            it = QTreeWidgetItem([m.name])
            it.setData(0, Qt.UserRole, m.name)
            it.setFlags(it.flags() | Qt.ItemIsEditable)   # 名可双击改名
            self.tree_mat.addTopLevelItem(it)
        fit_columns(self.tree_mat, [0], pad=24, min_w=120, max_w=280)
        n = self.tree_mat.topLevelItemCount()
        if n:
            names = [self.tree_mat.topLevelItem(i).text(0) for i in range(n)]
            row = names.index(keep) if keep in names else 0
            self.tree_mat.setCurrentItem(self.tree_mat.topLevelItem(row))

    def cur_mat(self):
        it = self.tree_mat.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _cur_material(self):
        mn = self.cur_mat()
        if not mn:
            return None
        for m in self.obj.materials:
            if m.name == mn:
                return m
        return None

    def refresh_detail(self):
        self.refresh_tex()
        self.refresh_params()
        self.refresh_mat_props()

    def refresh_tex(self):
        self.tree_tex.clear()
        m = self._cur_material()
        if m is None:
            return
        for t in m.textures:
            it = QTreeWidgetItem([t.texture_type, ""])
            it.setData(0, Qt.UserRole, ("tex", t.texture_type))
            it.setFlags(it.flags() | Qt.ItemIsEditable)   # type 可双击改名
            self.tree_tex.addTopLevelItem(it)
            # 路径列: 常驻输入框(改完失焦/回车即写回)
            le = QLineEdit(t.texture_path)
            le.setPlaceholderText("(留空 = Null.tex 占位)")
            le.editingFinished.connect(
                lambda ty=t.texture_type, w=le: self._commit_tex_path(ty, w.text()))
            self.tree_tex.setItemWidget(it, 1, le)
        fit_columns(self.tree_tex, [0], pad=28, min_w=200, max_w=340)
        self.tree_tex.setColumnWidth(1, 320)

    def refresh_params(self):
        self.tree_param.clear()
        m = self._cur_material()
        if m is None:
            return
        relayout_offsets(m)   # 让 offset 显示与实际导出一致(新参数不再显示 0)
        names = [n for n, _ in PARAM_TYPES]
        # 类型列宽度按**当前材质实际用到的类型**算（不是全部类型的最长名，
        # 否则材质里只有 float 时也会按 float4x4 留出很宽的列）
        fm = self.tree_param.fontMetrics()
        cur_types = {pr.type for pr in m.properties} or {"float4"}
        type_w = max(fm.horizontalAdvance(n) for n in cur_types) + 36
        for pr in m.properties:
            it = QTreeWidgetItem(["", pr.name, "", "", f"0x{pr.data_offset:04x}"])
            it.setData(0, Qt.UserRole, ("param", pr.name))
            it.setFlags(it.flags() | Qt.ItemIsEditable)   # 参数名可双击内联改名
            it.setIcon(0, _grip_icon())
            it.setFlags(it.flags() & ~Qt.ItemIsDropEnabled)   # 仅同级重排(不嵌套)
            self.tree_param.addTopLevelItem(it)
            # 类型列: 常驻下拉(改类型 = 改值个数)
            cb = NoWheelComboBox()
            cb.addItems(names)
            cb.setMinimumWidth(type_w)
            if pr.type in names:
                cb.setCurrentText(pr.type)
            cb.currentTextChanged.connect(
                lambda t, nm=pr.name: self._defer_set_type(nm, t))
            self.tree_param.setItemWidget(it, 2, cb)
            # 值列: 类型化控件(分量输入框; float3/4 带颜色块)
            self.tree_param.setItemWidget(it, 3, ValueEditor(pr))
        fit_columns(self.tree_param, [1, 4], pad=24, min_w=90, max_w=420)
        self.tree_param.setColumnWidth(2, type_w + 12)
        # 值列按当前材质最大分量数给初宽（矩阵在 mdf2 页是逗号文本，给 360）
        max_n = max((len(pr.values) for pr in m.properties), default=4)
        self.tree_param.setColumnWidth(
            3, 360 if max_n > 4 else max(120, max_n * 72 + 24))

    def _on_param_moved(self, *_a):
        """材质参数拖拽重排: 读当前行顺序 -> 重排 properties + 重算偏移。"""
        m = self._cur_material()
        if m is None:
            return
        order = []

        def _collect(it):
            order.append(it.text(1))
            for j in range(it.childCount()):
                _collect(it.child(j))

        for i in range(self.tree_param.topLevelItemCount()):
            _collect(self.tree_param.topLevelItem(i))
        if order == [pr.name for pr in m.properties]:
            return
        by = {pr.name: pr for pr in m.properties}
        if set(order) != set(by):
            self.refresh_params()
            return
        m.properties = [by[n] for n in order]
        relayout_offsets(m)
        self.refresh_params()

    # ---- 材质级编辑(新增/删除/改名 + 着色类型/flags) ----
    def _mat_name(self, item):
        return item.data(0, Qt.UserRole) if item else ""

    def _commit_mat_rename(self, key, text):
        if not key or not text or text == key or self.obj is None:
            return
        if self.obj.get_material(text) is not None:
            QMessageBox.warning(self, "提示", f"已存在同名材质 {text!r}")
            return
        try:
            self.obj.rename_material(key, text)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.refresh_mats()

    def _unique_material_name(self, base):
        names = {m.name for m in self.obj.materials} if self.obj else set()
        if base not in names:
            return base
        i = 1
        while f"{base}_{i}" in names:
            i += 1
        return f"{base}_{i}"

    def add_material_gui(self):
        if self.obj is None:
            QMessageBox.warning(self, "提示", "请先打开/新建一个 mdf2")
            return
        name, ok = QInputDialog.getText(self, "新增材质", "材质名:",
                                        text=self._unique_material_name("NewMaterial"))
        if not (ok and name):
            return
        if self.obj.get_material(name) is not None:
            QMessageBox.warning(self, "提示", f"已存在同名材质 {name!r}")
            return
        mmtr, ok = QInputDialog.getText(self, "新增材质", "mmtr 路径(可留空):")
        if not ok:
            return
        self.obj.add_material(name, mmtr)
        self.refresh_mats()
        for i in range(self.tree_mat.topLevelItemCount()):
            if self.tree_mat.topLevelItem(i).text(0) == name:
                self.tree_mat.setCurrentItem(self.tree_mat.topLevelItem(i))
                break

    def delete_material_gui(self, name):
        if self.obj is None or not name:
            return
        ok = QMessageBox.question(self, "删除材质", f"确定删除材质 {name!r}?")
        if ok != QMessageBox.Yes:
            return
        if self.obj.delete_material(name):
            self.refresh_mats()
            self.refresh_detail()

    def refresh_mat_props(self):
        m = self._cur_material()
        if m is None:
            return
        self.cmb_shading.blockSignals(True)
        self.cmb_shading.setCurrentText(m.shading_type_name())
        self.cmb_shading.blockSignals(False)
        self.ed_mmtr.blockSignals(True)
        self.ed_mmtr.setText(m.mmtr_path or "")
        self.ed_mmtr.blockSignals(False)
        d = m.flags_dict()
        for name, cb in self.flag_checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(d.get(name, False)))
            cb.blockSignals(False)
        for name, sp in self.flag_spins.items():
            sp.blockSignals(True)
            sp.setValue(int(d.get(name, 0)))
            sp.blockSignals(False)

    def _apply_shading(self, name):
        m = self._cur_material()
        if m is None:
            return
        v = shading_type_value(name)
        if v is not None:
            m.shader_type = v

    def _apply_mmtr_path(self):
        """MasterMaterial 编辑: 写回材质引用的 mmtr 路径(导出时进字符串表)。"""
        m = self._cur_material()
        if m is None:
            return
        m.mmtr_path = self.ed_mmtr.text().strip()

    def _apply_flag(self, name, val):
        m = self._cur_material()
        if m is None:
            return
        d = m.flags_dict()
        d[name] = val
        m.flags = encode_material_flags(d)

    def set_texture(self):
        if self.obj is None:
            QMessageBox.warning(self, "提示", "请先打开一个 mdf2")
            return
        mn = self.cur_mat()
        if not mn:
            return
        ty, ok = QInputDialog.getText(self, "贴图槽 type", "type 名(需与 mmtr 池名一致):")
        if not (ok and ty):
            return
        path, ok = QInputDialog.getText(self, "贴图路径", "贴图路径(留空=占位 Null.tex):")
        if not ok:
            return
        try:
            self.obj.set_texture(mn, ty, path or None)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.refresh_detail()

    def export_mdf2(self):
        if self.obj is None:
            return
        p = _get_save_path(self, "导出 mdf2", self.path or "out.mdf2.10",
                           "mdf2 (*.mdf2.10);;所有文件 (*)")
        if p:
            self.obj.save(p)
            QMessageBox.information(self, "导出", f"已写出:\n{p}")

    # ---- 右键菜单 ----
    def _menu_mat(self, item):
        if item is None:
            return None
        return [("新增材质", self.add_material_gui),
                ("重命名", lambda: self.tree_mat.editItem(item, 0)),
                ("删除材质", lambda: self.delete_material_gui(item.text(0))),
                ("设置/新增贴图槽", self.set_texture),
                ("复制材质名", lambda: _copy_to_clipboard(item.text(0)))]

    def _menu_tex(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        if not d or d[0] != "tex":
            return None
        ty = d[1]
        return [("重命名", lambda: self.tree_tex.editItem(item, 0)),
                ("删除贴图槽", lambda: self.delete_tex(ty)),
                ("复制 type/路径", lambda: self._copy_tex(ty))]

    def _menu_param(self, item):
        acts = [("新增参数", self.add_param)]
        d = item.data(0, Qt.UserRole) if item else None
        if d and d[0] == "param":
            acts = [("重命名", lambda: self.tree_param.editItem(item, 1)),
                    ("改类型", lambda: self.set_param_type(d[1])),
                    ("删除参数", lambda: self.delete_param(d[1])),
                    ("新增参数", self.add_param),
                    ("复制行",
                     lambda: _copy_to_clipboard(
                         " | ".join(item.text(c) for c in range(1, 5))))]
        return acts

    def add_param(self):
        if self.obj is None:
            QMessageBox.warning(self, "提示", "请先打开一个 mdf2")
            return
        m = self._cur_material()
        if m is None:
            return
        # 1. 参数名(预填一个不重名占位)
        name, ok = QInputDialog.getText(self, "新增参数", "参数名:",
                                        text=m.unique_parameter_name("NewParam"))
        if not (ok and name):
            return
        if m.get_parameter(name) is not None:
            QMessageBox.warning(self, "提示", f"已存在同名参数 {name!r}")
            return
        # 2. 类型(值 = 全 0 占位, 之后可编辑)
        ty = pick_type(self, "新增参数")
        if ty is None:
            return
        m.add_parameter(name, ty)
        relayout_offsets(m)
        self.refresh_detail()

    def set_param_type(self, name):
        m = self._cur_material()
        if m is None:
            return
        pr = m.get_parameter(name)
        if pr is None:
            return
        ty = pick_type(self, "改类型", default=pr.type)
        if ty is None:
            return
        pr.set_type(ty)
        relayout_offsets(m)
        self.refresh_detail()

    def _param_name(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        return d[1] if d and d[0] == "param" else ""

    def _defer_set_type(self, name, new_ty):
        """常驻下拉改类型 -> 延迟应用(会重建树, 避免在信号中删掉控件)。"""
        QTimer.singleShot(0, lambda: self._apply_param_type(name, new_ty))

    def _apply_param_type(self, name, new_ty):
        m = self._cur_material()
        if m is None:
            return
        pr = m.get_parameter(name)
        if pr is None or pr.type == new_ty:
            return
        try:
            pr.set_type(new_ty)
        except ValueError:
            return
        relayout_offsets(m)
        self.refresh_detail()

    def _commit_param_rename(self, key, text):
        """内联改名提交: key=("param", 旧名)。"""
        if not key or key[0] != "param" or not text or text == key[1]:
            return
        m = self._cur_material()
        if m is None:
            return
        if m.get_parameter(text) is not None:
            QMessageBox.warning(self, "提示", f"已存在同名参数 {text!r}")
            return
        try:
            m.rename_parameter(key[1], text)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        relayout_offsets(m)
        self.refresh_detail()

    def delete_param(self, name):
        m = self._cur_material()
        if m is None:
            return
        if not m.delete_parameter(name):
            return
        relayout_offsets(m)
        self.refresh_detail()

    def _tex_name(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        return d[1] if d and d[0] == "tex" else ""

    def _commit_tex_rename(self, key, text):
        """贴图槽 type 内联改名提交: key=("tex", 旧 type)。"""
        if not key or key[0] != "tex" or not text or text == key[1]:
            return
        m = self._cur_material()
        if m is None:
            return
        if m.get_texture(text) is not None:
            QMessageBox.warning(self, "提示", f"已存在同名贴图槽 {text!r}")
            return
        try:
            m.rename_texture(key[1], text)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.refresh_tex()

    def _commit_tex_path(self, ty, path):
        m = self._cur_material()
        if m is None:
            return
        tb = m.get_texture(ty)
        if tb is not None:
            tb.texture_path = path

    def delete_tex(self, ty):
        m = self._cur_material()
        if m is None:
            return
        if m.delete_texture(ty):
            self.refresh_tex()

    def _copy_tex(self, ty):
        m = self._cur_material()
        tb = m.get_texture(ty) if m else None
        path = tb.texture_path if tb else ""
        _copy_to_clipboard(f"{ty} | {path}")


# D3DCompile 错误行: 形如 "file.hlsl(12,5-9): error X3000: ..."
_HLSL_ERR_RE = re.compile(r"\((\d+),(\d+)(?:-(\d+))?\):\s*(error|warning)\s+(\w+):\s*(.*)")


def _pass_of_template(tmpl):
    """模板名 -> 目标 pass 名(deferred_* -> Deferred / forward_* -> Forward）。"""
    return "Forward" if (tmpl or "").startswith("forward") else "Deferred"


def _template_category(name):
    """模板归属 (shading, lighting):
    - forward_*_lit     -> ("forward", "default")  前向引擎式光照(继承引擎光照)
    - 其它 forward_*    -> ("forward", "custom")   前向自定义输出
    - deferred_*_custom -> ("deferred", "custom")  延迟直写(原始 GBuffer)
    - 其它 deferred_*   -> ("deferred", "default") 延迟默认光照
    """
    if name.startswith("forward"):
        return ("forward", "default" if name.endswith("_lit") else "custom")
    if name.endswith("_custom"):
        return ("deferred", "custom")
    return ("deferred", "default")


def _pass_template_names(shading=None, lighting=None):
    """可用的 pass 模板名(扫描 pass_templates/, 排除默认材质与 *_instance)。

    shading/lighting 给定时只返回匹配 (着色类型, 光照模式) 的模板。
    """
    import glob
    d = mpass._TDIR
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.hlsl"))):
        n = os.path.basename(p)[:-5]
        if n.startswith("mat_default_") or n.endswith("_instance"):
            continue
        cs, cl = _template_category(n)
        if shading and cs != shading:
            continue
        if lighting and cl != lighting:
            continue
        out.append(n)
    return out


# 各 (着色类型, 光照模式) 的“默认模板”
_PREFERRED_TEMPLATES = {
    ("deferred", "default"): ("deferred_std", "deferred_character", "deferred_env"),
    ("deferred", "custom"): ("deferred_std_custom", "deferred_custom"),
    ("forward", "custom"): ("forward_hairtransparentex", "forward_eyetransparentex"),
    ("forward", "default"): ("forward_hair_lit",),
}


def _default_template(shading, lighting):
    names = _pass_template_names(shading, lighting)
    for cand in _PREFERRED_TEMPLATES.get((shading, lighting), ()):
        if cand in names:
            return cand
    return names[0] if names else None


class MaterialSystemPanel(QWidget):
    """材质系统页: 语义级“材质资产”编辑(选项 + 材质函数 HLSL) -> 生成 mmtr。

    与 MMTR 页(结构/字节级)分开: 本页是“作者源 -> 生成”的语义层(见 analysis §9)。
    资产存为 `*.mmat.json`(lib/material_asset.py), 导出 = mmtr / 材质实例(mdf2)。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._last_mmtr_bytes = None
        self.asset = masset.MaterialAsset(template={"pass_template": "deferred_std"})
        self._build_ui()
        self._apply_asset()
        self._load_default_material()

    # ---- UI ----
    def _build_ui(self):
        self._asset_path = ""
        root = QHBoxLayout(self)
        left = QVBoxLayout()

        form = QFormLayout()
        self.cmb_light = NoWheelComboBox()
        for label, val in (("默认", "default"), ("自定义", "custom")):
            self.cmb_light.addItem(label, val)
        self.cmb_shading = NoWheelComboBox()
        # 前向暂去掉(模板已归档; 之后重构再加回)
        for label, val in (("延迟", "deferred"),):
            self.cmb_shading.addItem(label, val)
        self.ed_mmtr = QLineEdit()
        self.ed_mmtr.setPlaceholderText("导出 mdf2 时的 MasterMaterial 路径(可留空)")
        self.ed_name = QLineEdit()
        form.addRow("输出模式", self.cmb_light)
        form.addRow("着色类型", self.cmb_shading)
        form.addRow("材质路径(mdf2 用)", self.ed_mmtr)
        form.addRow("材质名", self.ed_name)
        left.addLayout(form)

        self.cmb_light.currentIndexChanged.connect(self._on_mode_changed)
        self.cmb_shading.currentIndexChanged.connect(self._on_mode_changed)
        self.ed_name.textChanged.connect(self._on_name_changed)

        btns = QHBoxLayout()
        for text, cb in (("载入默认材质", self._load_default_material),
                         ("新建", self._new_asset),
                         ("打开…", self.open_asset),
                         ("保存…", self.save_asset)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            btns.addWidget(b)
        left.addLayout(btns)

        btns2 = QHBoxLayout()
        for text, cb in (("编译校验", self.compile_check),
                         ("生成 mmtr…", self.generate_mmtr),
                         ("导出材质实例…", self.export_instance)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            btns2.addWidget(b)
        left.addLayout(btns2)

        # 编译到预览目录：产物进缓存并登记，供「实时预览」换母材质时选用。
        # 目录由游戏内 REF 桥（/paths）给出，用户不用填游戏路径。
        # 未连接时禁用 —— 没连接就不知道往哪写，也不能保证游戏能加载到。
        btns3 = QHBoxLayout()
        self.btn_compile_prev = QPushButton("编译到预览目录")
        self.btn_compile_prev.setToolTip(
            "把当前材质资产编译成 mmtr，写进游戏缓存目录（materialTemp/mmtr）并登记。\n"
            "文件名带版本号（<材质名>_r<rev>）：引擎按路径缓存资源，同路径改了内容不会重载。\n\n"
            "需要先在「实时预览」页连上游戏内 REF 服务。")
        self.btn_compile_prev.setEnabled(False)
        self.btn_compile_prev.clicked.connect(self.compile_to_preview)
        btns3.addWidget(self.btn_compile_prev)
        self.lbl_prev_state = QLabel("未连接 REF 服务")
        self.lbl_prev_state.setStyleSheet("color:#b00020")
        btns3.addWidget(self.lbl_prev_state, 1)
        left.addLayout(btns3)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setWordWrap(True)
        left.addWidget(self.lbl_status)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(330)
        root.addWidget(lw)

        self.tabs = QTabWidget()
        self.ed_src = CodeEdit(indent=4)
        self._src_hl = HlslHighlighter(self.ed_src.document())
        self._src_timer = QTimer(self)
        self._src_timer.setSingleShot(True)
        self._src_timer.setInterval(250)
        self._src_timer.timeout.connect(self._clear_src_diags)
        self.ed_src.textChanged.connect(self._on_src_changed)
        self.tabs.addTab(self.ed_src, "材质源 (HLSL)")
        # 附加内容(per-pass): 拼在材质源之前的 HLSL(struct/typedef/helper); 模板不主动使用,
        #   由用户在入口函数里调用; 可引用接口作用域内的引擎资源/参数/贴图。
        self.ed_extra = {}
        self._ex_hl = {}
        _ex_items = []
        for _p, _t in (("main", "主 pass"), ("depth", "深度 pass"), ("vertex", "顶点 (VS)")):
            _e = CodeEdit(indent=4)
            _e.setPlaceholderText(
                "在此写该 pass 的附加 HLSL(struct / typedef / helper 函数)。\n"
                "会拼在材质源之前(接口声明之后); 模板不会主动调用 —— 需你在入口函数里调用。\n"
                "可直接用接口作用域里的引擎资源/材质参数/贴图; 预设输入(uv/NormalWS…)需由入口传参。")
            self._ex_hl[_p] = HlslHighlighter(_e.document())
            _e.textChanged.connect(self._on_src_changed)
            self.ed_extra[_p] = _e
            _ex_items.append((_t, _e))
        self._extra_page, self._extra_btns, self._extra_stack = _seg_switch(_ex_items)
        self.tabs.addTab(self._extra_page, "附加内容")
        self.ed_full = CodeEdit(indent=4)
        self.ed_full.setReadOnly(True)
        self._full_hl = HlslHighlighter(self.ed_full.document())
        self.ed_full_depth = CodeEdit(indent=4)
        self.ed_full_depth.setReadOnly(True)
        self._depth_hl = HlslHighlighter(self.ed_full_depth.document())
        # 顶点(VS) 视图: 变体下拉(代表变体可切换) + 组装源码(只读) + 状态
        self.ed_full_vs = CodeEdit(indent=4)
        self.ed_full_vs.setReadOnly(True)
        self._vs_hl = HlslHighlighter(self.ed_full_vs.document())
        _vsbox = QWidget()
        _vsv = QVBoxLayout(_vsbox)
        _vsv.setContentsMargins(0, 0, 0, 0)
        _vrow = QHBoxLayout()
        _vrow.addWidget(QLabel("标准 VS 变体:"))
        self.cmb_vs = NoWheelComboBox()
        self.cmb_vs.currentIndexChanged.connect(self._on_vs_variant_changed)
        _vrow.addWidget(self.cmb_vs, 1)
        _vsv.addLayout(_vrow)
        self.lbl_vs = QLabel("")
        self.lbl_vs.setWordWrap(True)
        _vsv.addWidget(self.lbl_vs)
        _vsv.addWidget(self.ed_full_vs, 1)
        # 组装页: 单页 + 顶部按钮组切换各 pass/顶点(不再一 pass 一 Tab)
        self._asm_page, self._asm_btns, self._asm_stack = _seg_switch(
            [("主 pass", self.ed_full), ("深度 pass", self.ed_full_depth),
             ("顶点 (VS)", _vsbox)])
        self._asm_btns[2].clicked.connect(self._refresh_vs_preview)
        self.tabs.addTab(self._asm_page, "组装")
        self.tabs.addTab(self._wrap_inputs(), "输入")
        self.tabs.addTab(self._wrap_rbridge(), "实时预览")
        self.tree_info = QTreeWidget()
        self.tree_info.setHeaderLabels(["项", "类型", "落点/说明"])
        self.tabs.addTab(self.tree_info, "语义输出 / 校验")
        root.addWidget(self.tabs, 1)
        # 输入注册 = 结构化真源(self.asset.inputs; 见 lib/material_inputs_model.py)
        self.asset.inputs = mimp.normalize(self.asset.inputs)

    # ---- 状态同步 ----
    def _sync_asset(self):
        self.asset.lighting_mode = self.cmb_light.currentData()
        self.asset.shading_type = self.cmb_shading.currentData()
        self.asset.template["pass_template"] = (
            _default_template(self.cmb_shading.currentData(),
                              self.cmb_light.currentData()) or "deferred_std")
        self.asset.template["mmtr_path"] = self.ed_mmtr.text().strip()
        self.asset.name = self.ed_name.text().strip() or "NewMaterial"
        self.asset.shading_source = self.ed_src.toPlainText()
        if getattr(self, "ed_extra", None):
            self.asset.shading_extra = {p: e.toPlainText() for p, e in self.ed_extra.items()}

    def _apply_asset(self):
        self._loading = True
        try:
            for cmb, val in ((self.cmb_light, self.asset.lighting_mode),
                             (self.cmb_shading, self.asset.shading_type)):
                i = cmb.findData(val)
                if i >= 0:
                    cmb.setCurrentIndex(i)
            self._repopulate_templates()
            self.ed_mmtr.setText(self.asset.template.get("mmtr_path", ""))
            self.ed_name.setText(self.asset.name)
            if self.ed_src.toPlainText() != self.asset.shading_source:
                self.ed_src.setPlainText(self.asset.shading_source)
            for _p, _e in getattr(self, "ed_extra", {}).items():
                _v = self.asset.extra_for(_p)
                if _e.toPlainText() != _v:
                    _e.setPlainText(_v)
        finally:
            self._loading = False
        self.refresh_info()

    def _on_option_changed(self, *_):
        if self._loading:
            return
        # 必须先 _sync_asset: _ensure_custom_vertex_presets 读的是 asset.lighting_mode,
        # 而它只在 refresh_info->_sync_asset 里被写入。先补再同步 ⇒ 用的是**上一次**的模式
        # (默认→自定义 不补预设; 自定义→默认 反而硬加进去)。
        self._sync_asset()
        self._ensure_custom_vertex_presets()
        self.refresh_info()
        self._refresh_vs_preview()      # vo_kind 随模式变 ⇒ 预览需重算

    def _ensure_custom_vertex_presets(self):
        """切到自定义光照模式时, 自动补齐 MaterialVertex 默认实现依赖的顶点预设。

        默认实现直接引用 vi.positionWS/NormalWS/TangentWS/bitangentSign/uv0/uv1,
        缺任一项都会编译失败。只在自定义模式下补, 默认为空(不产生额外输入/升级)。
        """
        if self.asset.lighting_mode != "custom":
            return
        _vp = list(mimp.presets(self.asset.inputs, "vertex"))
        _add = [p for p in masset.CUSTOM_VERTEX_PRESETS if p not in _vp]
        if not _add:
            return
        self.asset.inputs["preset"]["vertex"] = _vp + _add
        self._sync_inputs()

    def _repopulate_templates(self):
        """无模板选择: 按 (着色类型, 光照模式) 取默认模板作为"空材质基准"。"""
        self.asset.template["pass_template"] = (
            _default_template(self.cmb_shading.currentData(),
                              self.cmb_light.currentData()) or "deferred_std")

    # ---- 顶点(VS) 预览 ----
    def _ensure_vs_variants(self):
        """惰性载入标准 VS 变体列表(供组装页「顶点(VS)」视图的下拉)。"""
        if getattr(self, "_vs_variants_ready", False):
            return
        self._vs_variants_ready = True
        try:
            self._vs_variants = nogen.standard_variants()
        except Exception:  # noqa: BLE001
            self._vs_variants = []
        self.cmb_vs.blockSignals(True)
        for _label, _key in self._vs_variants:
            self.cmb_vs.addItem(_label, _key)
        _def = next((i for i, (_l, k) in enumerate(self._vs_variants)
                     if k.endswith("|DeferredStatic")), 0)
        if self._vs_variants:
            self.cmb_vs.setCurrentIndex(_def)
        self.cmb_vs.blockSignals(False)

    def _on_vs_variant_changed(self, *_):
        self._refresh_vs_preview()

    def _vo_kind(self):
        """顶点输出形态: 自定义光照模式用完整 VertexOutput, 其余只用位置偏移。"""
        return "full" if self.asset.lighting_mode == "custom" else "offset"

    def _refresh_vs_preview(self, *_):
        """组装+编译当前选中变体的标准 VS(注入材质源的顶点钩子)并展示。

        注: 同一 mmtr 内所有变体共用一个 `MaterialVertex` ⇒ 只需看一个代表即可。
        """
        self._ensure_vs_variants()
        key = self.cmb_vs.currentData()
        if not key:
            self.ed_full_vs.setPlainText(";; 无标准 VS 变体(预设未就绪?)")
            self.lbl_vs.setText("")
            return
        src = self.ed_src.toPlainText()
        try:
            vsrc, dxbc, err = nogen.build_standard_vs(src, key, inputs=self.asset.inputs,
                                                      extra=self.asset.shading_extra,
                                                      vo_kind=self._vo_kind())
        except Exception as e:  # noqa: BLE001
            self.ed_full_vs.setPlainText(";; 组装失败: %s" % e)
            self.lbl_vs.setText("[组装失败] %s" % e)
            self.lbl_vs.setStyleSheet("color:#c0392b")
            return
        self.ed_full_vs.setPlainText(vsrc if vsrc else (";; " + str(err)))
        if err:
            self.lbl_vs.setText("[编译失败] %s" % err)
            self.lbl_vs.setStyleSheet("color:#c0392b")
        else:
            self.lbl_vs.setText("编译 OK %dB  ——  标准 VS 共 %d 个变体, 全部注入同一 MaterialVertex"
                                % (len(dxbc), len(getattr(self, "_vs_variants", []))))
            self.lbl_vs.setStyleSheet("color:#1a7f37")

    def _on_mode_changed(self, *_):
        if self._loading:
            return
        self._repopulate_templates()
        self._on_option_changed()

    def _on_name_changed(self, *_):
        if self._loading:
            return
        self.asset.name = self.ed_name.text().strip() or "NewMaterial"

    def refresh_info(self):
        self._sync_asset()
        self.tree_info.clear()
        root = QTreeWidgetItem(["配置", "", ""])
        root.addChild(QTreeWidgetItem(["光照模式", self.asset.lighting_mode, ""]))
        root.addChild(QTreeWidgetItem(["着色类型", self.asset.shading_type, ""]))
        for lv, msg in self.asset.validate():
            it = QTreeWidgetItem(["校验: " + lv, msg, ""])
            it.setForeground(0, QColor("#c0392b") if lv == "error" else QColor("#b8791a"))
            root.addChild(it)
        self.tree_info.addTopLevelItem(root)

        tmpl_now = self.asset.template.get("pass_template") or "deferred_std"
        if self.asset.lighting_mode == "custom":
            so = QTreeWidgetItem(["输出 (自定义光照 · 直控)", "", ""])
            for n, desc in masset.CUSTOM_OUTPUTS.get(self.asset.shading_type, []):
                so.addChild(QTreeWidgetItem([n, "", desc]))
            self.tree_info.addTopLevelItem(so)
            po = QTreeWidgetItem(["后处理 (交给材质)", "", ""])
            for n, desc in masset.POST_EXPOSED:
                po.addChild(QTreeWidgetItem([n, "", desc]))
            self.tree_info.addTopLevelItem(po)
        elif tmpl_now.startswith("deferred"):
            so = QTreeWidgetItem(["语义输出 (表3a · GBuffer 落点)", "", ""])
            for n, t, tgt in masset.SEMANTIC_OUTPUTS:
                so.addChild(QTreeWidgetItem([n, t, tgt]))
            self.tree_info.addTopLevelItem(so)
            mx = QTreeWidgetItem(["互斥(共用 GBuffer 通道)", "", ""])
            for a, b, ch in masset.MUTEX:
                mx.addChild(QTreeWidgetItem([a, b, ch]))
            self.tree_info.addTopLevelItem(mx)
        else:
            so = QTreeWidgetItem(["输出 (模板 MaterialOutput)", "", "逐字段=最终输出项"])
            for typ, nm, desc in _template_struct_fields(tmpl_now, "MaterialOutput"):
                so.addChild(QTreeWidgetItem([nm, typ, desc]))
            self.tree_info.addTopLevelItem(so)
        self.tree_info.expandAll()
        fit_columns(self.tree_info, (0, 1, 2))
        self.refresh_inputs()
        self._set_status()

    def _set_status(self):
        errs = [m for lv, m in self.asset.validate() if lv == "error"]
        warns = [m for lv, m in self.asset.validate() if lv == "warn"]
        if errs:
            self.lbl_status.setText("[错误] " + "；".join(errs))
            self.lbl_status.setStyleSheet("color:#c0392b")
        elif warns:
            self.lbl_status.setText("[提示] " + "；".join(warns))
            self.lbl_status.setStyleSheet("color:#b8791a")
        else:
            self.lbl_status.setText("配置合法")
            self.lbl_status.setStyleSheet("color:#1a7f37")

    def _base_iface(self, pass_name="main"):
        """该 pass 的基座接口 = 它声明的引擎资源依赖(**单一真源**: `minp.base_iface_for_pass`)。

        GUI 与生成器共用, 避免两条路径基座不一致(重复声明/寄存器错配)。按 (pass, 模板) 缓存
        —— 不同**模板**的结构性依赖可不同(如自定义光照薄模板依赖为空)。
        """
        tmpl = self.asset.template.get("pass_template") or "deferred_std"
        if not hasattr(self, "_iface_min"):
            self._iface_min = {}
        key = (pass_name, tmpl)
        if key not in self._iface_min:
            self._iface_min[key] = minp.base_iface_for_pass(pass_name, tmpl)
        return self._iface_min[key]

    def _effective_inputs(self, pass_name="main"):
        """基座(该 pass 的引擎依赖) + 该 pass 的输入清单; 返回 (iface, keepalive, minput)。"""
        _stage = "depth" if pass_name == "depth" else "ps"
        _sn, _rc = (("DepthInput", "di") if pass_name == "depth"
                    else ("MaterialInput", "mi"))
        iface, ka, rep = minp.build_iface_and_keepalive(
            self._base_iface(pass_name),
            engine_names=mimp.engine(self.asset.inputs, pass_name),
            params=mimp.params(self.asset.inputs),
            textures=mimp.textures(self.asset.inputs, pass_name),
            presets=mimp.presets(self.asset.inputs, pass_name),
            stage=_stage, struct_name=_sn, recv=_rc,
            all_params=mimp.all_params(self.asset.inputs), pass_name=pass_name)
        return iface, ka, (rep or {}).get("minput")

    def _effective_iface(self):
        return self._effective_inputs()[0]

    def _funcs_text(self, pass_name, stage):
        """该 pass 要注入的自定义函数定义文本(依赖在前; 分阶段校验, 不通过并抛错)。"""
        return cfun.injection_text(mimp.funcs(self.asset.inputs, pass_name), stage, pass_name)

    def _missing_funcs(self, pass_name, entry_name):
        """该 pass 入口调用了、却未导入到本 pass 的库函数(清晰报错用)。"""
        known = set(cfun.list_names())
        if not known:
            return []
        src = self.asset.shading_source or ""
        sp = nogen._hlsl_fn_span(src, entry_name)
        body = src[sp[0]:sp[1]] if sp else ""
        direct = cfun.calls_in(body, known)
        if not direct:
            return []
        need = cfun.closure(direct)
        imported = set(mimp.funcs(self.asset.inputs, pass_name))
        return sorted(n for n in need if n not in imported)

    # ---- 输入页 ----
    def _wrap_inputs(self):
        """输入页: 单页 + 顶部按钮组切换各 pass/顶点(每 pass 独立输入树 + 增删)。"""
        _w1, self.tree_inputs = self._inputs_tree("main")
        _w2, self.tree_inputs_depth = self._inputs_tree("depth")
        _w3, self.tree_inputs_vs = self._inputs_tree("vertex", with_force=True)
        page, self._in_btns, self._in_stack = _seg_switch(
            [("主 pass", _w1), ("深度 pass", _w2), ("顶点 (VS)", _w3)])
        return page

    # ---- 配置（游戏根 / 端口）----------------------------------------------
    # 文件位置沿用旧的 preview.json（只保留这两个键，旧的 base_mdf2/keep 忽略）。
    def _cfg_path(self):
        return os.path.join(os.path.expanduser("~"), ".re-material-toolkit", "preview.json")

    def _cfg_load(self):
        try:
            with open(self._cfg_path(), encoding="utf-8") as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def _cfg_save(self):
        try:
            p = self._cfg_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"game_root": getattr(self, "_rb_game_root", ""),
                           "port": self.sp_rb_port.value()}, f,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---- 实时预览（游戏内 C# 桥） ---------------------------------------
    # 见 analysis/runtime_preview_plan.md §10：游戏内跑 mmtr_bridge.cs（REFramework.NET），
    # 暴露 /probe 与 /set；setMaterialFloat 改的是内存值，**下一帧就生效**，且重启即恢复。
    def _rb_say(self, s):
        self.ed_rb_log.appendPlainText(s)

    def _rb_check(self, quiet=False, force=False):
        """连接状态。

        - ``force=True``：同步探测一次（手动点「连接」时用，最坏阻塞 ~1.5s）
        - 默认只返回当前状态，**不发请求** —— 实时性由 `_rb_ping_tick` 的后台线程保证；
          每次刷新列表都同步 ping 会卡 UI。
        """
        if force:
            d = None
            try:
                d = rb.ping(port=self._rb_port(), timeout=3.0)
            except rb.BridgeError as e:
                if not quiet:
                    self._rb_say(str(e))
            ok = bool(d and d.get("ok"))
            self._rb_set_link(ok, self._rb_detail(d))
            return ok
        return bool(getattr(self, "_rb_link_ok", False))

    @staticmethod
    def _rb_detail(d):
        """连接状态后缀（stage 号，用于区分“旧插件/新插件”）。"""
        if d and d.get("stage") is not None:
            return "stage %s" % d.get("stage")
        return ""

    def _rb_port(self):
        sp = getattr(self, "sp_rb_port", None)
        return sp.value() if sp is not None else rb.get_default_port()

    def _rb_set_link(self, ok, detail=""):
        """更新连接状态显示，并同步「编译到预览目录」按钮的可用性。"""
        self._rb_link_ok = bool(ok)
        self.lbl_rb.setText(("已连接" + (" · " + detail if detail else ""))
                            if ok else (detail or "未连接"))
        self.lbl_rb.setStyleSheet("color:#2e7d32" if ok else "color:#b00020")
        self.btn_compile_prev.setEnabled(self._rb_link_ok)
        self.lbl_prev_state.setText("已连接 · 可编译" if ok else "未连接 REF 服务")
        self.lbl_prev_state.setStyleSheet("color:#1a7f37" if ok else "color:#b00020")
        self._rb_set_ops_enabled(self._rb_link_ok)

    def _rb_set_ops_enabled(self, on):
        """断开时禁用整块操作区（列表 / 参数表 / 按钮）。"""
        for name in ("lst_rb", "cmb_rb_mat", "ed_rb_filter", "btn_rb_refresh",
                     "btn_rb_save", "btn_rb_attrs", "btn_rb_tex", "btn_rb_copy", "btn_rb_manage"):
            w = getattr(self, name, None)
            if w is not None:
                w.setEnabled(on)
        if on:
            self._rb_sync_enabled()      # 参数表仍要求「该行已勾选」
        else:
            tbl = getattr(self, "tbl_rb", None)
            if tbl is not None:
                tbl.setEnabled(False)

    def _rb_ping_tick(self):
        """每 1s 探活一次（后台线程，UI 永不阻塞）。

        只做“确认还活着”；一旦失败就**退回未连接**（停轮询、按钮变回「连接」），
        不做自动重连 —— 重连必须用户再点一次。
        """
        if not self._rb_connected:
            return
        if self._rb_ping_fut is not None and not self._rb_ping_fut.done():
            return
        if self._rb_ping_fut is not None:
            fut, self._rb_ping_fut = self._rb_ping_fut, None
            try:
                d = fut.result()
            except Exception:  # noqa: BLE001 —— 连不上就是常态
                d = None
            if not (d and d.get("ok")):
                self._rb_say("连接已断开（游戏退出 / 服务已停止）")
                self._rb_drop_link()
                return
            self._rb_set_link(True, self._rb_detail(d))
        try:
            self._rb_ping_fut = self._rb_pool.submit(rb.ping, self._rb_port(), 1.2)
        except RuntimeError:          # 线程池已关闭（程序退出中）
            self._rb_ping_fut = None

    def _rb_sync_tick(self):
        """当前查看的材质/对象：

        - 已预览：定时从游戏读参数实际值，更新未被编辑的输入框；
        - 未预览：每 2 个 tick 查一次 /identity，更新顶部当前 mdf2
          （游戏切 _Wet 等变体时自动跟上）。

        解决“工具侧显示的值和游戏实际值不一致，切到别的对象再切回来才刷新”。
        游戏里有些参数会被引擎自己改（湿身/变体切换、动画驱动的 Alpha 等），
        只靠“打开页面时读一次”不够。

        与用户输入的冲突：正在拖动/聚焦的框、以及已在待写队列里的参数不覆盖。
        """
        if not self._rb_connected:
            return
        ent = self._rb_cur_ent()
        if ent is None:
            return
        if not ent.get("prev"):
            # 未预览的对象：低频查一次当前 mdf2。游戏会自己切 _Wet 等变体，
            # 不刷新列表也要让顶部状态跟得上；预览中的行由 HoldTick 保持，无需查询。
            self._rb_watch_identity(ent)
            return
        if self._rb_sync_fut is not None and not self._rb_sync_fut.done():
            return
        if self._rb_sync_fut is not None:
            fut, self._rb_sync_fut = self._rb_sync_fut, None
            try:
                d = fut.result()
            except Exception:  # noqa: BLE001
                d = None
            # 关键：只应用“比最后一次用户修改更新”的请求结果。
            # 否则一个在修改前发出的旧请求返回后，会把刚输入的值覆盖回旧值。
            fresh = (self._rb_sync_sent is not None
                     and self._rb_sync_sent > self._rb_last_edit_ts)
            if d and fresh:
                self._rb_apply_live_params(ent, d)
        try:
            self._rb_sync_sent = time.monotonic()
            self._rb_sync_fut = self._rb_pool.submit(
                rb.list_params, ent.get("go"), 32, None, 10.0)
        except (RuntimeError, TypeError):
            self._rb_sync_fut = None

    def _rb_watch_identity(self, ent):
        """未预览对象：每 2 个 tick（约 3s）异步查一次 /identity，更新 mdf2 状态。

        只更新**当前行**且 addr 仍匹配的对象；不重建列表、不动勾选。
        """
        if self._rb_ident_fut is not None and not self._rb_ident_fut.done():
            return
        if self._rb_ident_fut is not None:
            fut, self._rb_ident_fut = self._rb_ident_fut, None
            try:
                d = fut.result()
            except Exception:  # noqa: BLE001
                d = None
            if d and self._rb_cur_ent() is ent:
                self._rb_apply_live_identity(ent, d)
        self._rb_ident_tick = (self._rb_ident_tick + 1) % 2
        if self._rb_ident_tick:
            return
        try:
            self._rb_ident_fut = self._rb_pool.submit(
                rb.identity, ent.get("go"), 32, False)
        except (RuntimeError, TypeError):
            self._rb_ident_fut = None

    def _rb_apply_live_identity(self, ent, data):
        """把 /identity 结果里该对象的最新 mdf2 路径写回条目并刷新标签。"""
        addr = (ent.get("addr") or "").lower()
        for m in (data.get("meshes") or []):
            if (m.get("addr") or "").lower() != addr:
                continue
            new = ps.norm_rel(m.get("mdf2Path") or "")
            old = ps.norm_rel(ent.get("mdf2Path") or "")
            if new and new != old:
                ent["mdf2Path"] = m.get("mdf2Path")
                ent["meshPath"] = m.get("meshPath") or ent.get("meshPath")
                self._rb_update_mdf_label(live_path=new)
                self._rb_say("当前 mdf2 已变化: %s" % new)
            return

    def _rb_apply_live_params(self, ent, data):
        """把游戏里的实际值填回输入框（正在编辑 / 已有待写值的参数跳过）。"""
        addr = ent.get("addr")
        target = None
        for mm in (data.get("meshes") or []):
            if addr and mm.get("addr") == addr:
                target = mm
                break
        if target is None:
            return
        matname = getattr(self, "_rb_matname", None)
        mat = next((m for m in (target.get("mats") or [])
                    if m.get("name") == matname), None)
        if mat is None:
            return
        pending = getattr(self, "_rb_pending", {}) or {}
        for vv in (mat.get("vars") or []):
            name = vv.get("name")
            if not name or name in pending:
                continue
            sp = self._rb_spins.get(name)
            if sp is not None:
                if sp.hasFocus():
                    continue
                try:
                    val = float(vv.get("value"))
                except (TypeError, ValueError):
                    continue
                if abs(sp.value() - val) > 1e-6:
                    sp.blockSignals(True)
                    sp.setValue(val)
                    sp.blockSignals(False)
                continue
            sps = self._rb_vec_spins.get(name)
            if not sps:
                continue
            if any(s.hasFocus() for s in sps):
                continue
            vals = _rb_parse_vec(vv.get("value"), len(sps))
            for s, v in zip(sps, vals):
                if abs(s.value() - v) > 1e-6:
                    s.blockSignals(True)
                    s.setValue(v)
                    s.blockSignals(False)

    def _rb_read_port_file(self):
        """游戏侧改端口后会写这个文件；连接时以它为准。"""
        root = self._rb_root()
        if not root:
            return None
        p = os.path.join(root, "reframework", "data", "mmtr_bridge_port.txt")
        try:
            with open(p, encoding="utf-8") as f:
                v = int((f.read() or "").strip())
        except (OSError, ValueError):
            return None
        return v if 1 <= v <= 65535 else None

    def _rb_toggle_conn(self):
        if self._rb_connected:
            self._rb_disconnect()
        else:
            self._rb_connect()

    def _rb_connect(self):
        p2 = self._rb_read_port_file()
        if p2 and p2 != self.sp_rb_port.value():
            self._rb_say("服务侧端口 = %d，已跟随（原 %d）" % (p2, self.sp_rb_port.value()))
            self.sp_rb_port.setValue(p2)
        rb.set_default_port(self.sp_rb_port.value())
        self._cfg_save()
        self._rb_connected = True
        self.btn_rb_conn.setText("断开连接")
        self._rb_say("连接 localhost:%d …" % self._rb_port())
        if not self._rb_check(force=True):
            self._rb_say("连接失败：确认游戏在运行、C# 插件已加载、端口一致")
            self._rb_disconnect(say=False)
            return
        self._rb_ping_timer.start()
        self._rb_sync_timer.start()
        # 游戏根由服务给出（/paths 只读进程信息，不碰引擎，安全）；不要求用户手填。
        try:
            g = rb.paths().get("gameRoot")
        except rb.BridgeError:
            g = None
        if g:
            self._rb_game_root = os.path.normpath(g)
            self._cfg_save()
            self._rb_say("游戏根目录（服务）: %s" % self._rb_game_root)
        else:
            self._rb_say("⚠ 服务未返回游戏根目录，写盘 / 副本功能暂不可用")
        # 不自动刷新列表：连接只做一次 ping。拉场景（/identity）是引擎调用，
        # 由用户点「刷新列表」单独触发，出问题也好定位。
        self._rb_say("已连接。点「刷新列表」拉取场景对象。")
        return

    def _rb_disconnect(self, say=True):
        was = self._rb_connected
        self._rb_drop_link()
        if say and was:
            self._rb_say("已断开连接（游戏内已挂载的预览仍会继续生效）")

    def _rb_drop_link(self):
        """退回未连接：停轮询、按钮变回「连接」、清空列表。"""
        self._rb_connected = False
        self._rb_ping_timer.stop()
        self._rb_sync_timer.stop()
        self._rb_sync_fut = None
        self._rb_ident_fut = None
        self.btn_rb_conn.setText("连接")
        self._rb_set_link(False, "未连接")
        self._rb_clear_list()

    def _rb_clear_list(self):
        self._rb_building = True
        self.lst_rb.blockSignals(True)
        self.lst_rb.clear()
        self.lst_rb.blockSignals(False)
        self._rb_building = False
        self._rb_meshes = []
        self._rb_tex_edits = {}
        self._rb_cur_addr = None
        self._rb_mats = []
        self._rb_fill_mats()
        self.lbl_rb_mdf.setText("")

    def _rb_refresh(self):
        """重建列表：每项带独立的预览勾选框，状态以游戏**实际挂载**的 mdf2 为准。

        用 /identity 而不是 /probe?what=materials：一次就能拿到 addr / meshPath / mdf2Path，
        之后所有操作按 addr 定位。**绝不能用 i=**：findComponents 的索引会因重启 / 变体切换
        整体位移（1812→1479→2295），用 i= 定位会在湿身后直接打空（touched=0）。
        """
        if not self._rb_check():
            return
        go = self.ed_rb_filter.text().strip()
        root = self._rb_root()
        try:
            d = rb.identity(go=go, limit=120, names=False)
        except rb.BridgeError as e:
            self._rb_say("拉取失败: %s" % e)
            return
        ms = d.get("meshes") or []
        idx_doc = ps.load_index(root) if root else {"objects": {}}

        prev_addr = getattr(self, "_rb_cur_addr", None)
        self._rb_building = True
        self.lst_rb.blockSignals(True)
        self.lst_rb.clear()
        for m in ms:
            cur = ps.norm_rel(m.get("mdf2Path") or "")
            hit = self._rb_lookup_by_path(idx_doc, cur) if cur else None
            m["prev"] = hit["dh"] if (hit and hit["kind"] == "edit") else None
            m["copy"] = hit.get("copy") if m["prev"] else None
            # 磁盘上没有时，还能从 pak 按需提取（只部分模型被解包）
            missing = False
            pak_only = False
            if root and cur and not m["prev"]:
                try:
                    if not os.path.exists(ps.mdf2_abs(root, cur)):
                        if ps.pak_has(root, cur):
                            pak_only = True
                        else:
                            missing = True
                except Exception:
                    pass
            m["noSrc"] = missing
            m["pakOnly"] = pak_only
            tag = "   [不可用]" if missing else ("   [pak]" if pak_only else "")
            it = QListWidgetItem("%s   [%s]   %d 槽%s" % (
                m.get("go") or "?", _rb_addr_short(m.get("addr")),
                m.get("matCount") or 0, tag))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if m["prev"] else Qt.Unchecked)
            it.setData(Qt.UserRole, m.get("addr"))
            self.lst_rb.addItem(it)
        self.lst_rb.blockSignals(False)
        self._rb_building = False
        self._rb_meshes = ms

        row = -1
        for r, m in enumerate(ms):
            if prev_addr and m.get("addr") == prev_addr:
                row = r
                break
        if row < 0 and ms:
            row = 0
        if row >= 0:
            self.lst_rb.setCurrentRow(row)      # 触发 _rb_pick_mesh
        else:
            self._rb_clear_params()
        self._rb_say("列表: %d 个 mesh（预览中 %d 个，从 pak 提取 %d 个，不可用 %d 个）" % (
            len(ms), sum(1 for m in ms if m.get("prev")),
            sum(1 for m in ms if m.get("pakOnly")),
            sum(1 for m in ms if m.get("noSrc"))))

    def _rb_cur_ent(self):
        """当前列表行对应的 mesh 条目（含 addr / prev）。"""
        lst = getattr(self, "lst_rb", None)
        ms = getattr(self, "_rb_meshes", None) or []
        if lst is None:
            return None
        row = lst.currentRow()
        return ms[row] if 0 <= row < len(ms) else None

    def _rb_pick_mesh(self, row):
        """切换当前编辑对象。**不动任何一行的勾选**：每行的预览状态是独立的。"""
        if row < 0 or row >= len(getattr(self, "_rb_meshes", [])):
            return
        ent = self._rb_meshes[row]
        self._rb_cur_addr = ent.get("addr")
        self._rb_mesh_i = ent.get("i")
        self._rb_update_mdf_label()
        self._rb_load_params(ent)

    def _rb_load_params(self, ent, keep_mat=None):
        """按 addr 读这个对象的材质参数（索引变了也能对上）。

        keep_mat：重读后尽量保持当前选中的材质（参数集变了也能回到原材质）。
        """
        go, addr = ent.get("go"), ent.get("addr")
        try:
            p = rb.list_params(go, limit=32)
        except rb.BridgeError as e:
            self._rb_say("读参数失败: %s" % e)
            self._rb_mats = []
            self._rb_fill_mats()
            return
        target = None
        for mm in (p.get("meshes") or []):
            if addr and mm.get("addr") == addr:
                target = mm
                break
        if target is None and not addr:
            for mm in (p.get("meshes") or []):
                if mm.get("i") == ent.get("i"):
                    target = mm
                    break
        if target is None:
            self._rb_say("读不到该对象的参数（addr=%s）——可能已被游戏重载，请刷新列表"
                         % _rb_addr_short(addr))
            self._rb_mesh_i = None
            self._rb_mats = []
        else:
            self._rb_mesh_i = target.get("i")
            self._rb_mats = target.get("mats") or []
        self._rb_fill_mats(keep_mat=keep_mat)
        self._rb_say("mesh [%s] %s: %d 个材质（i=%s）" % (
            _rb_addr_short(addr), go, len(self._rb_mats), self._rb_mesh_i))

    def _rb_fill_mats(self, keep_mat=None):
        self.cmb_rb_mat.blockSignals(True)
        self.cmb_rb_mat.clear()
        for i, m in enumerate(self._rb_mats):
            self.cmb_rb_mat.addItem("%s  (%d 参数)" % (m.get("name"), m.get("varCount") or 0), i)
        self.cmb_rb_mat.blockSignals(False)
        if self._rb_mats:
            idx = 0
            if keep_mat:
                for i, m in enumerate(self._rb_mats):
                    if m.get("name") == keep_mat:
                        idx = i
                        break
            self.cmb_rb_mat.setCurrentIndex(idx)
            self._rb_pick_mat(idx)
        else:
            self.tbl_rb.setRowCount(0)
        self._rb_sync_enabled()

    def _rb_clear_params(self):
        self._rb_mats = []
        self._rb_fill_mats()
        lbl = getattr(self, "lbl_rb_mdf", None)
        if lbl is not None:
            lbl.setText("")

    def _rb_sync_enabled(self):
        """参数表只在「当前对象处于预览中」时可编辑。"""
        ent = self._rb_cur_ent()
        self.tbl_rb.setEnabled(bool(ent and ent.get("prev")))

    def _rb_set_item_check(self, addr, on):
        """不触发 itemChanged 地设置某一行的勾选状态。"""
        self.lst_rb.blockSignals(True)
        for r in range(self.lst_rb.count()):
            it = self.lst_rb.item(r)
            if it.data(Qt.UserRole) == addr:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                break
        self.lst_rb.blockSignals(False)

    def _rb_on_item_changed(self, item):
        """列表项的勾选框变化 → 该对象的预览开关（每行独立）。"""
        if getattr(self, "_rb_building", False):
            return
        addr = item.data(Qt.UserRole)
        ent = None
        for m in (getattr(self, "_rb_meshes", None) or []):
            if m.get("addr") == addr:
                ent = m
                break
        if ent is None:
            return
        want = item.checkState() == Qt.Checked
        if want == bool(ent.get("prev")):
            return
        root = self._rb_root()
        if not root:
            self._rb_say("游戏根目录未就绪（连接服务后自动获取）")
            self._rb_set_item_check(addr, not want)
            return
        try:
            if want:
                ent["prev"] = self._rb_iso_begin(root, ent)
            else:
                self._rb_iso_end(root, ent)
                ent["prev"] = None
                ent["copy"] = None
        except (rb.BridgeError, RuntimeError, OSError) as e:
            self._rb_say("预览开关失败: %s" % e)
            self._rb_set_item_check(addr, not want)
            return
        self._rb_set_item_check(addr, want)
        # 换 mdf2 后参数块重建，重新读一遍（只针对当前显示的那一行）
        if self._rb_cur_addr == addr:
            self._rb_update_mdf_label()
            self._rb_load_params(ent)
        self._rb_sync_enabled()

    def _rb_pick_mat(self, idx):
        mats = getattr(self, "_rb_mats", None) or []
        if idx < 0 or idx >= len(mats):
            return
        m = mats[idx]
        self._rb_matname = m.get("name")
        self.tbl_rb.setRowCount(0)
        self._rb_spins = {}
        self._rb_vec_spins = {}
        for vv in (m.get("vars") or []):
            r = self.tbl_rb.rowCount()
            self.tbl_rb.insertRow(r)
            name = vv.get("name") or "?"
            vt = vv.get("type")
            self.tbl_rb.setItem(r, 0, QTableWidgetItem(name))
            self.tbl_rb.setItem(r, 1, QTableWidgetItem(_rb_typename(vt)))
            if vt == 1:
                sp = QDoubleSpinBox()
                sp.setDecimals(4)
                sp.setRange(-100000.0, 100000.0)
                sp.setSingleStep(0.05)
                sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
                try:
                    sp.setValue(float(vv.get("value")))
                except (TypeError, ValueError):
                    pass
                sp.valueChanged.connect(lambda _v, n=name, s=sp: self._rb_schedule(n, s))
                self.tbl_rb.setCellWidget(r, 2, sp)
                self._rb_spins[name] = sp
            elif vt in (2, 3, 4):
                # float2/3/4：每个分量一个输入框。写入走 /set3（直写内存 + 自动 flush）。
                vals = _rb_parse_vec(vv.get("value"), vt)
                cont = QWidget()
                h = QHBoxLayout(cont)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(2)
                sps = []
                for c in range(vt):
                    sp = QDoubleSpinBox()
                    sp.setDecimals(4)
                    sp.setRange(-100000.0, 100000.0)
                    sp.setSingleStep(0.05)
                    sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
                    sp.setValue(vals[c])
                    sp.valueChanged.connect(lambda _v, n=name: self._rb_vec_changed(n))
                    h.addWidget(sp)
                    sps.append(sp)
                self.tbl_rb.setCellWidget(r, 2, cont)
                self._rb_vec_spins[name] = sps
            elif vt > 4:
                # 矩阵参数（float4x3=12 / float4x4=16）：引擎按分量数暴露，但 REF 的
                # getMaterialFloat4 / setMaterialFloat4 只覆盖 1~4。这里只读展示，
                # 不建输入框、不进 _rb_edits —— 保存时原样不动。
                self.tbl_rb.setItem(
                    r, 2, QTableWidgetItem("矩阵（%d 分量，只读）" % vt))
            else:
                self.tbl_rb.setItem(r, 2, QTableWidgetItem(str(vv.get("value"))))
        self._rb_fit_name_width(m)
        self._rb_fit_type_width(m)
        self._rb_fit_value_width(m)

    def _rb_fit_name_width(self, m):
        """按参数名长度给「参数」列一个合适的初始宽度（值列宽度见 _rb_fit_value_width）。"""
        fm = self.tbl_rb.fontMetrics()
        need = fm.horizontalAdvance("参数") + 40
        for vv in (m.get("vars") or []):
            need = max(need, fm.horizontalAdvance(str(vv.get("name") or "")) + 32)
        self.tbl_rb.setColumnWidth(0, min(max(160, int(need)), 320))

    def _rb_fit_type_width(self, m):
        """「类型」列按当前材质实际用到的类型文本给初宽（min 56 / max 120）。"""
        fm = self.tbl_rb.fontMetrics()
        need = fm.horizontalAdvance("类型") + 24
        for vv in (m.get("vars") or []):
            need = max(need, fm.horizontalAdvance(_rb_typename(vv.get("type"))) + 24)
        self.tbl_rb.setColumnWidth(1, min(max(56, need), 120))

    def _rb_fit_value_width(self, m):
        """按当前材质最大的值控件给「值」列（第 2 列）一个初始宽度（之后仍可拖）。"""
        fm = self.tbl_rb.fontMetrics()
        need = 96
        for vv in (m.get("vars") or []):
            vt = vv.get("type") or 0
            if 2 <= vt <= 4:
                need = max(need, vt * 72 + 16)
            elif vt > 4:
                need = max(need, fm.horizontalAdvance("矩阵（%d 分量，只读）" % vt) + 28)
        self.tbl_rb.setColumnWidth(2, min(max(96, need), 600))

    def _rb_schedule(self, name, spin):
        """合并连续拖动：只发最后一次值（否则拖一下会发几十个请求）。"""
        self._rb_pending[name] = spin.value()
        self._rb_last_edit_ts = time.monotonic()
        self._rb_timer.start()

    def _rb_vec_changed(self, name):
        """float2/3/4 的任一分量变动：拼成 "x,y,z" 交给防抖写入。"""
        sps = self._rb_vec_spins.get(name) or []
        if not sps:
            return
        self._rb_pending[name] = ",".join("%.6f" % s.value() for s in sps)
        self._rb_last_edit_ts = time.monotonic()
        self._rb_timer.start()

    def _rb_flush(self):
        if not self._rb_pending:
            return
        pending, self._rb_pending = self._rb_pending, {}
        ent = self._rb_cur_ent()
        if ent is None or not ent.get("prev"):
            self._rb_say("当前对象未启用预览，已忽略 %d 项修改" % len(pending))
            return
        for name, val in pending.items():
            sval = val if isinstance(val, str) else ("%.6f" % float(val))
            try:
                r = rb.apply_slot(ent.get("go"), self._rb_matname, name, sval,
                                  addr=ent.get("addr"))
            except rb.BridgeError as e:
                self._rb_say("set %s 失败: %s" % (name, e))
                continue
            res = r.get("results") or []
            if not res:
                # addr 没命中任何 mesh = 对象已被游戏重载（索引和地址都对不上了）
                self._rb_obj_gone(ent, "写参数失败：addr 未命中任何 mesh")
                return
            after = [x.get("after") for x in res]
            via = next((x.get("via") for x in res if x.get("via")), "?")
            self._rb_say("set %s = %s  ->  %s  (%s)" % (name, sval, after, via))
            self._rb_last_edit_ts = time.monotonic()   # 写入完成后才算“基准”
            # 记下改动，供「保存到 mdf2」写盘（按对象地址分组）
            try:
                vec = [float(t) for t in str(sval).split(",")]
            except ValueError:
                continue
            (self._rb_edits.setdefault(_rb_addr_key(ent.get("addr")), {})
                            .setdefault(self._rb_matname, {}))[name] = vec

    def _rb_obj_gone(self, ent, why):
        """对象已被游戏重载：清掉该行的勾选与状态，并提示重选。"""
        addr = ent.get("addr")
        ent["prev"] = None
        ent["copy"] = None
        if getattr(self, "_rb_cur_addr", None) == addr:
            self._rb_mesh_i = None
        self._rb_set_item_check(addr, False)
        self._rb_sync_enabled()
        self._rb_edits.pop(_rb_addr_key(addr), None)
        self._rb_tex_edits.pop(_rb_addr_key(addr), None)
        self._rb_say("⚠ 对象已失效（%s）：%s" % (_rb_addr_short(addr), why))
        QMessageBox.warning(
            self, "对象已失效",
            "该对象已被游戏重载，预览状态已清除。\n\n地址：%s\n原因：%s\n\n"
            "请点「刷新列表」后重新选择对象。" % (addr, why))

    # ---- 参数写盘 -------------------------------------------------------
    # 流程：内存里的改动（_rb_edits） → 原地覆盖进工作副本 → 换全新路径让引擎重载
    #       → 所有共用该副本的对象一起切过去 → 更新 sessions.json → 删旧文件
    # 为什么必须换新路径：引擎按路径缓存 mdf2，同路径改了文件不会重新加载（§13.4）。
    # ---- 共用：关联对象查找 / 切换副本 -----------------------------------
    def _rb_attach_points(self, ent, edit_rel):
        """找出所有挂在 edit_rel 这份副本上的对象。

        返回 ``{归一化地址: (原始地址, goName)}``。
        为什么要找多个：同一模型的多个实例（或同一 mdf2 被多个 mesh 使用）
        都可能挂着同一份文件，产出新副本后必须一起切过去，否则旧文件不能删。
        """
        addr_go = {}
        try:
            hd = rb.holds().get("holdDetail") or []
        except rb.BridgeError as ex:
            self._rb_say("读 holds 失败（%s），只处理当前对象" % ex)
            hd = []
        low = ps.norm_rel(edit_rel).lower()
        for h in hd:
            raw = h.get("addr")
            k = _rb_addr_key(raw)
            if not k:
                continue
            if ps.norm_rel(h.get("wantPath") or "").lower() == low:
                addr_go[k] = (raw, h.get("go") or ent.get("go"))
        if not addr_go:
            addr_go[_rb_addr_key(ent.get("addr"))] = (ent.get("addr"), ent.get("go"))
        return addr_go

    def _rb_switch_all_to(self, root, mh, dh, edit_rel, new_rel, addr_go,
                          name, rev):
        """把挂在旧副本上的对象全部切到新副本 + 更新登记 + 删旧文件。

        返回 ``(moved_keys, failed_msgs)``。moved 为空时不改登记也不删旧文件
        （否则会把还在用的文件删掉）。
        """
        doc = ps.load_index(root)
        moved, failed = [], []
        for k, (raw, g) in addr_go.items():
            try:
                d = rb.material(g, "apply", mdf=new_rel, addr=raw, keep=True)
            except rb.BridgeError as ex:
                failed.append("%s: %s" % (_rb_addr_short(k), ex))
                continue
            ms = d.get("meshes") or []
            if d.get("err") or not ms or not ms[0].get("ok"):
                why = d.get("err") or (ms[0].get("err") if ms else "无命中")
                failed.append("%s: %s" % (_rb_addr_short(k), why))
            else:
                moved.append(k)

        if not moved:
            return moved, failed

        ps.upsert_copy(doc, mh, dh, name, new_rel, rev)
        ps.save_index(root, doc, log=self._rb_say)
        # 旧文件此时已无 mesh 引用
        try:
            os.remove(ps.rel_to_abs(root, edit_rel))
            self._rb_say("已删除旧副本: %s" % edit_rel)
        except OSError as ex:
            self._rb_say("旧副本删除失败（可忽略）: %s" % ex)
        return moved, failed

    def _rb_save_to_mdf2(self):
        """把本会话改过的参数写进工作副本（新路径），并让游戏重载。"""
        ent = self._rb_cur_ent()
        if ent is None:
            self._rb_say("保存：没有选中对象")
            return
        if not ent.get("prev"):
            self._rb_say("保存：当前对象未启用预览（勾选后才能保存到 mdf2）")
            return
        root = self._rb_root()
        if not root:
            self._rb_say("保存：游戏根目录未就绪（连接服务后自动获取）")
            return
        mh = ps.mesh_hash(ent.get("meshPath") or "")
        dh = ent.get("prev")
        doc = ps.load_index(root)
        c = self._rb_copy_of(doc, mh, dh, ent)
        edit_rel = ps.norm_rel((c or {}).get("rel") or "")
        if not edit_rel or not ps.exists_edit(root, edit_rel):
            self._rb_say("保存：登记的工作副本不存在（%s）" % (edit_rel or "未登记"))
            return

        # 谁在用这份副本 + 合并改动（同名参数后写覆盖先写）
        addr_go = self._rb_attach_points(ent, edit_rel)
        merged = {}
        for k in addr_go:
            for mat, props in (self._rb_edits.get(k) or {}).items():
                merged.setdefault(mat, {}).update(props)
        tex = {}
        for k in addr_go:
            for mat, slots in (self._rb_tex_edits.get(k) or {}).items():
                tex.setdefault(mat, {}).update(slots)
        if not merged and not tex:
            self._rb_say("保存：没有改动")
            return

        # 有贴图改动：必须 Mdf2.save() 重排（贴图路径是字符串），参数值一并写入。
        if tex:
            changes = {}
            for mat in set(merged) | set(tex):
                ch = {}
                if merged.get(mat):
                    ch["params"] = merged[mat]
                if tex.get(mat):
                    ch["textures"] = tex[mat]
                changes[mat] = ch
            try:
                r2 = mattr.apply_attrs(root, edit_rel, changes, log=self._rb_say)
            except Exception as ex:  # noqa: BLE001
                self._rb_say("保存失败: %s" % ex)
                QMessageBox.warning(self, "保存失败", str(ex))
                return
            if not r2.get("changed"):
                self._rb_say("保存：没有改动")
                return
            new_rel = r2["rel"]
            moved, failed = self._rb_switch_all_to(
                root, mh, dh, edit_rel, new_rel, addr_go,
                name=r2["name"], rev=r2["rev"])
            if moved:
                for a in moved:
                    self._rb_edits.pop(a, None)
                    self._rb_tex_edits.pop(a, None)
            self._rb_update_mdf_label()
            if _rb_addr_key(getattr(self, "_rb_cur_addr", None)) in moved:
                QTimer.singleShot(600, self._rb_reread_current)
            ntex = sum(len(v) for v in tex.values())
            msg = ("已写入 %d 个参数 + %d 个贴图槽到副本「%s」v%d：\n%s\n\n"
                   "切到新副本的对象：%d 个"
                   % (sum(len(v) for v in merged.values()), ntex,
                      r2["name"], r2["rev"], new_rel, len(moved)))
            rep = r2.get("report") or []
            if rep:
                msg += "\n\n" + "\n".join(rep)
            if failed:
                msg += "\n\n未切换成功：\n" + "\n".join(failed)
            self._rb_say(msg.replace("\n", "  "))
            QMessageBox.information(self, "保存到 mdf2", msg)
            return

        # 无贴图改动：原地覆盖参数值（更轻，不重排文件）
        try:
            r = ps.save_params(root, edit_rel, merged, log=self._rb_say)
        except Exception as ex:
            self._rb_say("保存失败: %s" % ex)
            QMessageBox.warning(self, "保存失败", str(ex))
            return
        # 删掉已失效的参数名（材质删参 / 改类型后旧值不再适用）
        skipped = r.get("skipped") or []
        for a in addr_go:
            per = self._rb_edits.get(a) or {}
            for mat_name, pname, _why in skipped:
                (per.get(mat_name) or {}).pop(pname, None)
        if not r.get("changed", True):
            self._rb_say("保存：没有改动")
            return
        new_rel = r["rel"]

        # 所有共用该副本的对象切到新文件（keep 保持）
        moved, failed = self._rb_switch_all_to(root, mh, dh, edit_rel, new_rel,
                                              addr_go, name=r["name"], rev=r["rev"])
        if moved:
            for a in moved:
                self._rb_edits.pop(a, None)

        self._rb_update_mdf_label()
        if _rb_addr_key(getattr(self, "_rb_cur_addr", None)) in moved:
            # apply 后引擎重建参数块要一点时间，立即读会拿到旧值（延迟读 + 轮询兜底）
            QTimer.singleShot(600, self._rb_reread_current)

        msg = ("已写入 %d 个参数到副本「%s」v%d：\n%s\n\n切到新副本的对象：%d 个"
               % (len(r.get("touched") or []), r["name"], r["rev"],
                  new_rel, len(moved)))
        if skipped:
            msg += "\n\n已忽略 %d 个失效参数：%s" % (
                len(skipped), "; ".join("%s.%s（%s）" % x for x in skipped))
        if failed:
            msg += "\n\n未切换成功：\n" + "\n".join(failed)
        self._rb_say(msg.replace("\n", "  "))
        QMessageBox.information(self, "保存到 mdf2", msg)

    # ---- 贴图槽编辑（运行时 setMaterialTexture；持久化见 _rb_save_to_mdf2）----
    def _rb_edit_textures(self):
        """编辑当前材质的贴图槽：应用 = 内存热替换，保存 = 写入 mdf2 新 rev。"""
        ent = self._rb_cur_ent()
        if ent is None or not ent.get("prev"):
            self._rb_say("贴图编辑：先勾选列表里的对象（改贴图需要工作副本）")
            return
        matname = getattr(self, "_rb_matname", None)
        if not matname:
            self._rb_say("贴图编辑：先在右侧选一个材质")
            return
        root = self._rb_root()
        if not root:
            self._rb_say("贴图编辑：游戏根目录未就绪（连接服务后自动获取）")
            return
        mh = ps.mesh_hash(ent.get("meshPath") or "")
        dh = ent.get("prev")

        # 运行时列表（EMV 同款：getMaterialTextureNum/Name/Texture）
        slots = []
        try:
            d = rb.texture_list(ent.get("go"), matname, addr=ent.get("addr"))
            for mm in (d.get("meshes") or []):
                if _rb_addr_key(mm.get("addr")) != _rb_addr_key(ent.get("addr")):
                    continue
                for m in (mm.get("mats") or []):
                    if m.get("name") != matname:
                        continue
                    for s in (m.get("slots") or []):
                        slots.append({"slot": s.get("slot"),
                                      "name": s.get("name") or "?",
                                      "path": ps.norm_rel(s.get("path") or "")})
                    break
        except rb.BridgeError as ex:
            self._rb_say("贴图编辑：读运行时贴图槽失败（%s），改用工作副本文件" % ex)
        if not slots:
            # 旧插件 / 读不到时退回工作副本文件（只能显示路径，应用仍需新插件）
            try:
                doc = ps.load_index(root)
                c = self._rb_copy_of(doc, mh, dh, ent)
                edit_rel = ps.norm_rel((c or {}).get("rel") or "")
                dm = mdf2lib.Mdf2.load(ps.rel_to_abs(root, edit_rel)) if edit_rel else None
                fm = dm.get_material(matname) if dm else None
                for i, tb in enumerate((fm.textures if fm else []) or []):
                    slots.append({"slot": i, "name": tb.texture_type,
                                  "path": ps.norm_rel(tb.texture_path)})
            except Exception as ex:  # noqa: BLE001
                self._rb_say("贴图编辑：读工作副本贴图槽失败（%s）" % ex)
        if not slots:
            self._rb_say("贴图编辑：材质 %s 没有贴图槽（或服务未更新）" % matname)
            return
        # 只列两类贴图：原 mdf2 自带（所有材质）/ 工具导入管理。
        # 不扫磁盘、不读 pak 全量列表：那些路径可能属于别的 mod，卸载后就缺失了。
        try:
            orig_tex = mattr.original_textures(root, mh, dh)
        except Exception as ex:  # noqa: BLE001
            self._rb_say("贴图编辑：读原 mdf2 贴图失败（%s）" % ex)
            orig_tex = []
        managed = ps.list_managed_textures(root, mh, dh)
        sources = [("原 mdf2 自带", orig_tex),
                   ("工具导入管理", managed)]

        dlg = TextureEditDialog(self, matname, slots, root, mh, dh, sources)
        if dlg.exec() != QDialog.Accepted:
            return
        ch = dlg.changes()
        if not ch:
            self._rb_say("贴图编辑：没有改动")
            return

        key = _rb_addr_key(ent.get("addr"))
        per = self._rb_tex_edits.setdefault(key, {}).setdefault(matname, {})
        ok, bad = [], []
        for name, path in ch.items():
            s = next((x for x in slots if x["name"] == name), None)
            if s is None:
                continue
            try:
                r = rb.texture_set(ent.get("go"), matname, s["slot"], path,
                                   addr=ent.get("addr"))
                if not r.get("ok") or not (r.get("touched") or 0):
                    err = r.get("err") or "; ".join(
                        (x.get("err") or "?") for x in (r.get("results") or [])) or "无命中"
                    bad.append("%s: %s" % (name, err))
                    continue
                per[name] = path
                ok.append(name)
            except rb.BridgeError as ex:
                bad.append("%s: %s" % (name, ex))
        if not per:
            self._rb_tex_edits.get(key, {}).pop(matname, None)
            if not self._rb_tex_edits.get(key):
                self._rb_tex_edits.pop(key, None)
        msg = "已应用 %d 个贴图（内存）" % len(ok)
        if bad:
            msg += "；失败 %d 个：\n%s" % (len(bad), "\n".join(bad))
        msg += "\n点「保存到 mdf2」持久化。"
        self._rb_say(msg.replace("\n", "  "))
        if bad:
            QMessageBox.warning(self, "贴图编辑", msg)

    # ---- 材质属性：换母材质 / 改 flags -----------------------------------
    # 三者（mmtr 引用 / 参数集 / flags）必须**一次 save 一起做**：
    #   - 换母材质会重排参数集（数量/offset），必须走 Mdf2.save()
    #   - flags 在材质记录里，同样只能随 save 写
    #   - 重载会丢内存里调好的值，所以把本会话改过的参数一并写进去
    # 实测依据见 analysis/runtime_preview_plan.md §21.6 / §22。
    def _rb_edit_attrs(self):
        """打开材质属性对话框（母材质 + flags），应用后生成新副本并重载。"""
        ent = self._rb_cur_ent()
        if ent is None or not ent.get("prev"):
            self._rb_say("材质属性：先勾选列表里的对象（改属性需要工作副本）")
            return
        matname = getattr(self, "_rb_matname", None)
        if not matname:
            self._rb_say("材质属性：先在右侧选一个材质")
            return
        root = self._rb_root()
        if not root:
            self._rb_say("材质属性：游戏根目录未就绪（连接服务后自动获取）")
            return
        mh = ps.mesh_hash(ent.get("meshPath") or "")
        dh = ent.get("prev")
        doc = ps.load_index(root)
        c = self._rb_copy_of(doc, mh, dh, ent)
        edit_rel = ps.norm_rel((c or {}).get("rel") or "")
        if not edit_rel or not ps.exists_edit(root, edit_rel):
            self._rb_say("材质属性：工作副本不存在（%s）" % (edit_rel or "未登记"))
            return

        # 从**文件**读当前状态（不是内存：内存里的 flags 无法读取）
        try:
            d = mdf2lib.Mdf2.load(ps.rel_to_abs(root, edit_rel))
            m = d.get_material(matname)
            if m is None:
                self._rb_say("材质属性：工作副本里没有材质 %s" % matname)
                return
            cur = {"mmtr": m.mmtr_path, "flags": m.flags_dict(),
                   "flags_raw": m.flags & 0xFFFFFFFF}
            try:
                # 从 mmtr 路径反推材质名（只对 materialTemp/mmtr/ 下的产物）
                cur["resolved_name"] = mlab.resolve_material(root, m.mmtr_path)[0]
            except Exception:  # noqa: BLE001
                cur["resolved_name"] = ""
        except Exception as ex:  # noqa: BLE001
            self._rb_say("材质属性：读工作副本失败（%s）" % ex)
            return

        # 原始母材质（从原始 mdf2 读；读不到就只列当前 + 工具材质）
        try:
            orig = mattr.original_mmtr(root, mh, dh, matname)
        except Exception as ex:  # noqa: BLE001
            self._rb_say("材质属性：读原始母材质失败（%s）" % ex)
            orig = ""
        opts = mattr.list_mmtr_options(root)
        dlg = MaterialAttrsDialog(self, matname, cur, opts, original=orig)
        if dlg.exec() != QDialog.Accepted:
            return
        ch = dlg.changes()
        if not ch:
            self._rb_say("材质属性：没有改动")
            return

        # 带上本会话改过的参数值（否则重载后回到文件里的旧值）
        edits = {}
        for k in self._rb_attach_points(ent, edit_rel):
            edits.update((self._rb_edits.get(k) or {}).get(matname) or {})
        if edits:
            ch["params"] = edits
            self._rb_say("一并写入 %d 个改过的参数值" % len(edits))
        tex_edits = {}
        for k in self._rb_attach_points(ent, edit_rel):
            tex_edits.update((self._rb_tex_edits.get(k) or {}).get(matname) or {})
        if tex_edits:
            ch["textures"] = tex_edits
            self._rb_say("一并写入 %d 个改过的贴图槽" % len(tex_edits))

        try:
            r = mattr.apply_attrs(root, edit_rel, {matname: ch}, log=self._rb_say)
        except mattr.AttrError as ex:
            self._rb_say("材质属性失败: %s" % ex)
            QMessageBox.warning(self, "材质属性失败", str(ex))
            return
        if not r.get("changed"):
            self._rb_say("材质属性：值没有变化")
            return

        # 所有挂旧副本的对象切到新文件
        addr_go = self._rb_attach_points(ent, edit_rel)
        moved, failed = self._rb_switch_all_to(root, mh, dh, edit_rel, r["rel"],
                                              addr_go, name=r["name"], rev=r["rev"])
        for a in moved:
            self._rb_edits.pop(a, None)
            self._rb_tex_edits.pop(a, None)
        self._rb_update_mdf_label()
        if _rb_addr_key(getattr(self, "_rb_cur_addr", None)) in moved:
            # 重载后参数集可能变了（换母材质）；延迟读，等引擎重建完参数块
            QTimer.singleShot(600, self._rb_reread_current)

        msg = "副本「%s」→ v%d：\n%s\n\n切到新副本的对象：%d 个" % (
            r["name"], r["rev"], r["rel"], len(moved))
        rep = r.get("report") or []
        if rep:
            msg += "\n\n" + "\n".join(rep)
        if failed:
            msg += "\n\n未切换成功：\n" + "\n".join(failed)
        if ch.get("mmtr"):
            msg += ("\n\n提示：换母材质后新增参数默认 0"
                    "（比如 Alpha=0 会让材质变透明），请到参数表里调。")
        self._rb_say(msg.replace("\n", "  "))
        QMessageBox.information(self, "材质属性", msg)

    def _rb_pick_copy(self):
        """选择/新建当前 mesh 可用的副本（跨该 mesh 用到的所有原 mdf2）。

        树形（按原 mdf2 分组）：每行「副本名（vN）· 原 mdf2 路径」。同一 mesh 下不同原 mdf2
        （正常 / Wet / Burn…）的副本都能挂，切过去时以那一份的原 mdf2 为准。
        """
        ent = self._rb_cur_ent()
        if ent is None:
            self._rb_say("选择副本：先选一个对象")
            return
        root = self._rb_root()
        mesh_path = ent.get("meshPath") or ""
        if not root or not mesh_path:
            self._rb_say("选择副本：缺少游戏根目录或 meshPath（连接服务 / 刷新列表后再试）")
            return
        mh = ps.mesh_hash(mesh_path)
        doc = ps.load_index(root)
        mdfs = (ps.get_object(doc, mh) or {}).get("mdfs") or {}
        groups = []
        for dh in mdfs:
            e = mdfs[dh]
            base = ps.norm_rel(e.get("baseMdf2") or e.get("originalMdf2") or "")
            cs = [c for c in ps.list_copies(doc, mh, dh)
                  if ps.exists_edit(root, c["rel"])]
            if cs:
                groups.append((dh, base, cs))
        if not groups:
            self._rb_say("选择副本：该 mesh 还没有编辑副本（先勾选建一份）")
            return
        groups.sort(key=lambda g: g[1])
        cur = (ent.get("prev") or "", ent.get("copy") or "")
        cur_desc = ""
        if ent.get("copy"):
            e0 = ps.get_mdf2(doc, mh, cur[0]) or {}
            b0 = ps.norm_rel(e0.get("baseMdf2") or e0.get("originalMdf2") or "")
            cur_desc = "\n当前：%s · %s" % (ent["copy"], self._rb_short(b0, 80))
        dlg = CopyPickerDialog(
            self, groups, current=cur, mode="pick",
            prompt="对象：%s%s\n选一份挂到这个对象（可跨该 mesh 用到的所有原 mdf2）；"
                   "带「＋」的行可为该原 mdf2 新建副本："
                   % (self._rb_short(mesh_path, 90), cur_desc))
        if dlg.exec() != QDialog.Accepted or not dlg.choice:
            return
        kind, dh, c = dlg.choice
        if kind == "copy" and (dh, c["name"]) == cur:
            self._rb_say("仍是当前副本：%s（v%d）" % (c["name"], c["rev"]))
            return
        if kind == "new":
            c = self._rb_new_copy(root, doc, mh, dh)
            if c is None:
                return
        self._rb_switch_copy(ent, doc, mh, dh, c, root)

    def _rb_switch_copy(self, ent, doc, mh, dh, c, root):
        """把当前对象挂到副本 c（并把它设为该原 mdf2 的最近使用）。"""
        addr, go = ent.get("addr"), ent.get("go")
        try:
            d = rb.material(go, "apply", mdf=c["rel"], addr=addr, keep=True)
        except rb.BridgeError as ex:
            self._rb_say("切换副本失败: %s" % ex)
            return False
        ms = d.get("meshes") or []
        if d.get("err") or not ms or not ms[0].get("ok"):
            self._rb_say("切换副本失败: %s" % (d.get("err") or "无命中"))
            return False

        self._rb_edits.pop(_rb_addr_key(addr), None)   # 换了副本，旧副本的待存值作废
        self._rb_tex_edits.pop(_rb_addr_key(addr), None)
        ent["prev"] = dh
        ent["copy"] = c["name"]
        ps.upsert_copy(doc, mh, dh, c["name"], c["rel"], c["rev"])
        ps.save_index(root, doc, log=self._rb_say)
        self._rb_set_item_check(addr, True)
        self._rb_sync_enabled()
        # apply 后引擎重建参数块要一点时间；跨原 mdf2 切换时材质集可能不同，整表重读
        QTimer.singleShot(600, self._rb_update_mdf_label)
        QTimer.singleShot(600, lambda e=ent: self._rb_load_params(e))
        self._rb_say("已切换到副本「%s」v%d：%s" % (c["name"], c["rev"], c["rel"]))
        return True

    def _rb_manage_copy(self):
        """管理当前 mesh 的副本：重命名 / 删除（都只对未使用副本）。

        本工具任何一行在用、或游戏仍 hold 的副本标「使用中」，不能改名/删除：
        引擎下次重载读不到文件会变默认方格。
        """
        ent = self._rb_cur_ent()
        if ent is None:
            self._rb_say("管理副本：先选一个对象")
            return
        root = self._rb_root()
        mesh_path = ent.get("meshPath") or ""
        if not root or not mesh_path:
            self._rb_say("管理副本：缺少游戏根目录或 meshPath（连接服务 / 刷新列表后再试）")
            return
        if not (self._rb_connected and self._rb_link_ok):
            self._rb_say("管理副本：需要先连接游戏（确认没有对象在用它）")
            return
        mh = ps.mesh_hash(mesh_path)
        doc = ps.load_index(root)
        mdfs = (ps.get_object(doc, mh) or {}).get("mdfs") or {}
        if not mdfs:
            self._rb_say("管理副本：该 mesh 还没有副本")
            return
        in_use = {(m.get("prev"), m.get("copy"))
                  for m in (getattr(self, "_rb_meshes", None) or [])
                  if m.get("prev")}
        held = set()
        try:
            for h in (rb.holds().get("holdDetail") or []):
                p = ps.norm_rel(h.get("wantPath") or "").lower()
                if p:
                    held.add(p)
        except rb.BridgeError as ex:
            self._rb_say("管理副本：读 holds 失败（%s），暂不操作" % ex)
            return

        groups = []
        for dh in sorted(mdfs):
            e = mdfs[dh]
            base = ps.norm_rel(e.get("baseMdf2") or e.get("originalMdf2") or "")
            cs = []
            for c in ps.list_copies(doc, mh, dh):
                c = dict(c)
                c["_in_use"] = ((dh, c["name"]) in in_use
                                or c["rel"].lower() in held)
                cs.append(c)
            if cs:
                groups.append((dh, base, cs))

        dlg = CopyPickerDialog(
            self, groups,
            current=(ent.get("prev") or "", ent.get("copy") or ""),
            mode="manage", title="管理副本",
            prompt="对象：%s\n选中**未使用**的副本后可重命名 / 删除；"
                   "标「使用中」的需先切换或取消勾选："
                   % self._rb_short(mesh_path, 90))
        if dlg.exec() != QDialog.Accepted or not dlg.action:
            return
        kind, dh, c = dlg.action
        if kind == "delete":
            self._rb_delete_copy(ent, root, doc, mh, dh, c)
        else:
            self._rb_rename_copy(ent, root, doc, mh, dh, c)

    def _rb_delete_copy(self, ent, root, doc, mh, dh, c):
        if QMessageBox.question(
                self, "删除副本",
                "确定删除副本「%s」v%d？\n\n文件：%s\n\n不可恢复。"
                % (c["name"], c["rev"], c["rel"]),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            os.remove(ps.rel_to_abs(root, c["rel"]))
        except FileNotFoundError:
            pass
        except OSError as ex:
            self._rb_say("删除文件失败: %s" % ex)
            QMessageBox.warning(self, "删除失败", str(ex))
            return
        ps.remove_copy(doc, mh, dh, c["name"])
        ps.save_index(root, doc, log=self._rb_say)
        self._rb_edits.pop(_rb_addr_key(ent.get("addr")), None)
        self._rb_tex_edits.pop(_rb_addr_key(ent.get("addr")), None)
        self._rb_update_mdf_label()
        self._rb_say("已删除副本「%s」v%d" % (c["name"], c["rev"]))

    def _rb_rename_copy(self, ent, root, doc, mh, dh, c):
        e = ps.get_mdf2(doc, mh, dh) or {}
        base = ps.norm_rel(e.get("baseMdf2") or e.get("originalMdf2") or "")
        names = {x["name"] for x in ps.list_copies(doc, mh, dh)} - {c["name"]}
        new_name = self._rb_prompt_copy_name(
            names, c["name"], "重命名副本",
            "原 mdf2：%s\n\n新名字（同一原 mdf2 下唯一）："
            % (self._rb_short(base, 70) or "?"))
        if not new_name or new_name == c["name"]:
            return
        rev = c["rev"]
        while True:
            new_rel = ps.new_copy_rel(mh, dh, new_name, rev)
            if not os.path.exists(ps.rel_to_abs(root, new_rel)):
                break
            rev += 1
        old_abs = ps.rel_to_abs(root, c["rel"])
        new_abs = ps.rel_to_abs(root, new_rel)
        try:
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            os.replace(old_abs, new_abs)
        except OSError as ex:
            self._rb_say("重命名失败（文件）: %s" % ex)
            QMessageBox.warning(self, "重命名失败", str(ex))
            return
        try:
            ps.rename_copy(doc, mh, dh, c["name"], new_name, new_rel)
        except Exception as ex:  # noqa: BLE001
            try:
                os.replace(new_abs, old_abs)
            except OSError:
                pass
            self._rb_say("重命名失败（登记）: %s" % ex)
            QMessageBox.warning(self, "重命名失败", str(ex))
            return
        ps.save_index(root, doc, log=self._rb_say)
        if ent.get("copy") == c["name"]:
            ent["copy"] = new_name
        self._rb_update_mdf_label()
        self._rb_say("副本「%s」→「%s」（v%d）" % (c["name"], new_name, rev))

    def _rb_prompt_copy_name(self, taken, default, title, prompt):
        """弹名字输入并校验（非法字符 / 重名）；取消返回 ""。"""
        while True:
            name, ok = QInputDialog.getText(self, title, prompt, text=default)
            if not ok:
                return ""
            err = ps.copy_name_error(name)
            if err:
                QMessageBox.warning(self, "副本名不可用", err)
                continue
            if name in taken:
                QMessageBox.warning(self, "副本名重复",
                                    "该原 mdf2 下已有名为「%s」的副本。" % name)
                continue
            return name

    # ---- 独立 mdf2 预览开关 --------------------------------------------
    # 勾选：把当前 mesh 的 mdf2 换成一份**内容相同、路径不同**的副本（clone_mdf2），
    #       之后调参作用于新加载的参数块；取消勾选换回原 mdf2 → 引擎重建参数块 = 重置。
    # 实测：同路径重载无效（引擎按路径缓存），必须换路径。
    def _rb_update_mdf_label(self, live_path=None):
        """顶部只显示**当前行**对象的 mdf2 副本。

        预览中的行不调 get_Material（set_Material 后引擎要过一会儿才更新它的返回值，
        刚挂上时读到的还是旧路径），直接按登记的 copies 显示。
        live_path：自动轮询已经拿到的路径（避免再同步请求一次）。
        """
        lbl = getattr(self, "lbl_rb_mdf", None)
        if lbl is None:
            return
        ent = self._rb_cur_ent()
        if ent is None:
            lbl.setText("")
            return
        root = self._rb_root()
        prev = ent.get("prev")
        if prev and root:
            doc = ps.load_index(root)
            mh = ps.mesh_hash(ent.get("meshPath") or "")
            c = self._rb_copy_of(doc, mh, prev, ent)
            if c:
                e = ps.get_mdf2(doc, mh, prev) or {}
                base = ps.norm_rel(e.get("baseMdf2") or e.get("originalMdf2") or "")
                lbl.setText("%s · %s   [预览中 · v%d]"
                            % (c["name"], base or "?", c["rev"]))
                return
        p = live_path
        if p is None:
            try:
                d = rb.material(ent.get("go"), "info", addr=ent.get("addr"))
                ms = d.get("meshes") or []
                p = ms[0].get("path") if ms else None
            except rb.BridgeError:
                p = ent.get("mdf2Path")
        if not p:
            lbl.setText("当前 mdf2: ?")
            return
        cur = ps.norm_rel(p)
        if cur.lower().startswith(ps.TEMP_DIR.lower() + "/"):
            lbl.setText("%s   [预览中 · 未登记]" % cur)
        else:
            lbl.setText("未预览 · %s" % cur)

    # ---- 独立 mdf2 预览：勾选 / 取消流程 -------------------------------
    # 设计（见 analysis/runtime_preview_plan.md §27）：
    #   勾选 → /identity 拿 meshPath + mdf2Path → meshHash / mdf2Hash
    #        → 查 sessions.json（materialTemp/sessions.json）
    #            无该 mdf2          → 确认后建「默认」副本
    #            已有副本           → 弹窗选一份 / 新建
    #            当前挂的就是副本   → 直接沿用
    #        → create_copy 生成 edit/<mh>/<dh>/<副本名>_v<rev>（避开引擎路径缓存）
    #        → /material apply(addr=) + keep=1
    #   取消 → create_orig 生成全新路径 → /material apply(keep=0) → 参数回到原始值
    #   轮询 /holds → dead 为真则清勾选 + 提示重选
    def _rb_root(self):
        """游戏根目录：由 C# 服务 /paths 提供（连接后设置，不要求用户手填）。"""
        return (getattr(self, "_rb_game_root", "") or "").strip()

    # ---- 额外源目录（已移除）----------------------------------------------
    # 早期支持登记另一个解包目录（sources.json）作为 mdf2 源；实际用不上，
    # 且容易误点「应用」。现在只在游戏根 natives/x64 找，找不到就从 pak 提取。

    def _rb_find_identity(self, ent):
        """重新查一次对象的身份（按 addr 匹配）。

        勾选/取消前必须现查：ent 里的 mdf2Path 是 refresh 时的快照，
        湿身/燃烧变体切换后它会过期。
        """
        go, addr = ent.get("go"), (ent.get("addr") or "").lower()
        d = rb.identity(go=go, limit=64, names=False)
        for m in (d.get("meshes") or []):
            if addr and (m.get("addr") or "").lower() == addr:
                return m
        return None

    @staticmethod
    def _rb_short(rel, n=52):
        s = rel or ""
        return s if len(s) <= n else "…" + s[-n:]

    @staticmethod
    def _rb_lookup_by_path(idx_doc, rel):
        """按挂载路径反查 sessions.json 登记项。

        mesh 索引会变（重启 / 变体切换都会），副本 / orig 的路径却是唯一的，
        所以「当前挂在哪个副本上」只能靠路径反查，不能靠 i=。
        返回 {mh, dh, ent, kind: "edit"|"orig", copy} 或 None。
        """
        low = ps.norm_rel(rel or "").lower()
        if not low:
            return None
        for mh, o in (idx_doc.get("objects") or {}).items():
            for dh, e in (o.get("mdfs") or {}).items():
                orig = ps.norm_rel(e.get("orig") or "")
                if orig and orig.lower() == low:
                    return {"mh": mh, "dh": dh, "ent": e, "kind": "orig"}
                for c in ps.list_copies(idx_doc, mh, dh):
                    if c["rel"].lower() == low:
                        return {"mh": mh, "dh": dh, "ent": e,
                                "kind": "edit", "copy": c["name"]}
        return None

    @staticmethod
    def _rb_copy_of(idx_doc, mh, dh, ent):
        """当前对象在用的副本登记项（按 ent["copy"]；找不到退回最近使用）。"""
        c = ps.get_copy(idx_doc, mh, dh, name=ent.get("copy"))
        if c is None:
            e = ps.get_mdf2(idx_doc, mh, dh) or {}
            c = ps.get_copy(idx_doc, mh, dh, rel=e.get("edit") or "")
        return c

    def _rb_apply_iso(self, ent, rel):
        """把 mesh 挂到指定副本并开启每帧保持（被游戏抢走时自动抢回 + 重写参数）。"""
        d = rb.material(ent.get("go"), "apply", mdf=rel, addr=ent.get("addr"), keep=True)
        if d.get("err"):
            raise rb.BridgeError(d["err"])
        ms = d.get("meshes") or []
        if not ms:
            raise rb.BridgeError("apply 没有命中任何 mesh（addr=%s，对象可能已被重载）"
                                 % ent.get("addr"))
        for m in ms:
            if not m.get("ok"):
                raise rb.BridgeError("apply 失败: %s" % (m.get("err") or "?"))
        self._rb_say("已挂载工作副本（keep=%s）: %s" % (d.get("keep"), ms[0].get("now") or rel))

    def _rb_ask_copy(self, idx_doc, mh, dh, copies):
        """该原 mdf2 已有多个副本时让用户挑。返回副本登记项 | "__new__" | None。"""
        e = ps.get_mdf2(idx_doc, mh, dh) or {}
        base = ps.norm_rel(e.get("baseMdf2") or e.get("originalMdf2") or "")
        last = ps.get_copy(idx_doc, mh, dh, rel=e.get("edit") or "")
        labels = ["%s（v%d）%s" % (c["name"], c["rev"],
                                  "  ← 上次" if last and c["name"] == last["name"] else "")
                  for c in copies]
        labels.append("＋ 新建副本…")
        default = next((i for i, c in enumerate(copies)
                        if last and c["name"] == last["name"]), 0)
        pick, ok = QInputDialog.getItem(
            self, "启用预览",
            "原 mdf2：%s\n\n这份 mdf2 已有 %d 个副本。选一份挂到当前对象，或新建："
            % (self._rb_short(base, 70) or "?", len(copies)),
            labels, default, False)
        if not ok:
            return None
        i = labels.index(pick)
        return "__new__" if i == len(copies) else copies[i]

    def _rb_new_copy(self, root, idx_doc, mh, dh, first=False, mesh_path=""):
        """弹名字输入 → 基于原 mdf2 建新副本 → 写进登记（不 save_index）。

        first=True：首次为这份原 mdf2 建副本（文案带上模型与原路径）。
        任何创建入口（首次勾选 / 选择副本里的新建）都必须经过这里，不自作主张用默认名。
        """
        e = ps.get_mdf2(idx_doc, mh, dh) or {}
        base = ps.norm_rel(e.get("baseMdf2") or e.get("originalMdf2") or "")
        if not base:
            self._rb_say("新建副本：登记项里没有原始 mdf2 路径")
            return None
        names = {c["name"] for c in ps.list_copies(idx_doc, mh, dh)}
        default = "默认" if "默认" not in names else "副本%d" % (len(names) + 1)
        if first:
            title = "创建副本"
            prompt = ("这份原 mdf2 首次进入预览。\n\n"
                      "模型：%s\n原 mdf2：%s\n\n"
                      "将复制一份作为工作副本，原文件不会改动。\n"
                      "副本名（同一原 mdf2 下唯一）：" % (mesh_path, base))
        else:
            title = "新建副本"
            prompt = ("原 mdf2：%s\n\n副本名（同一原 mdf2 下唯一）："
                      % (self._rb_short(base, 70) or "?"))
        name = self._rb_prompt_copy_name(names, default, title, prompt)
        if not name:
            return None
        try:
            info = ps.create_copy(root, mh, dh, base, name=name, log=self._rb_say)
        except Exception as ex:  # noqa: BLE001
            self._rb_say("新建副本失败: %s" % ex)
            QMessageBox.warning(self, "新建副本失败", str(ex))
            return None
        return ps.upsert_copy(idx_doc, mh, dh, info["name"], info["rel"], info["rev"])

    def _rb_iso_begin(self, root, ent):
        """开启某对象的预览。返回该对象对应的 mdf2Hash（写入 ent["prev"]）。

        已有副本时让用户挑一份或新建；首次则输入名字后建副本。
        """
        ident = self._rb_find_identity(ent)
        if not ident:
            raise rb.BridgeError(
                "读不到该对象的身份（addr=%s）——已被游戏重载，请刷新列表后重选"
                % _rb_addr_short(ent.get("addr")))
        mesh_path = ident.get("meshPath") or ""
        mdf_path = ident.get("mdf2Path") or ""
        if not mesh_path or not mdf_path:
            raise rb.BridgeError("meshPath / mdf2Path 为空，稍后再试（变体切换中？）")
        cur = ps.norm_rel(mdf_path)
        go = ent.get("go")
        idx_doc = ps.load_index(root)

        # ① 当前路径已是登记副本 → 继续用它（换新路径会让引擎重载，调好的参数全丢）
        hit = self._rb_lookup_by_path(idx_doc, cur)
        if hit and hit["kind"] == "edit":
            self._rb_apply_iso(ent, cur)
            ent["copy"] = hit.get("copy")
            return hit["dh"]

        if hit:
            mh, dh = hit["mh"], hit["dh"]
            reg = hit["ent"]
            base = ps.norm_rel(reg.get("originalMdf2") or reg.get("baseMdf2") or cur)
            self._rb_say("已重置过（挂在 orig 副本上），从原始 mdf2 重新开始")
        else:
            if cur.lower().startswith(ps.TEMP_DIR.lower() + "/"):
                # 挂在 materialTemp 下但登记丢了（手动清了 sessions.json）：
                # 路径本身带身份，直接接管成副本，不重建、不丢当前状态。
                parsed = ps.parse_temp_rel(cur)
                if not parsed or parsed.get("kind") != "edit":
                    raise rb.BridgeError(
                        "该对象挂在无法识别的临时副本上：\n%s\n\n请重启游戏后再试。" % cur)
                mh, dh = ps.mesh_hash(mesh_path), parsed["dh"]
                # 可能挂在某个副本的旧版本上（离线编译后登记已指向新 rev）：
                # 不要用旧文件覆盖登记，直接切到登记的新版本。
                known = ps.get_copy(idx_doc, mh, dh, name=parsed["name"])
                if known and ps.exists_edit(root, known["rel"]):
                    self._rb_say("当前挂的是副本「%s」旧版本（v%d），切到登记的 v%d"
                                 % (known["name"], parsed["rev"], known["rev"]))
                    self._rb_apply_iso(ent, known["rel"])
                    ent["copy"] = known["name"]
                    return dh
                self._rb_say("接管未登记的副本（身份从路径反推）: mh=%s dh=%s" % (mh, dh))
                self._rb_apply_iso(ent, cur)
                ps.upsert_object(idx_doc, mh, mesh_path, go, ident.get("matCount") or 0)
                ps.upsert_mdf2(idx_doc, mh, dh, cur, cur)
                ps.upsert_copy(idx_doc, mh, dh,
                               parsed["name"], cur, parsed["rev"])
                ps.save_index(root, idx_doc, log=self._rb_say)
                ent["copy"] = parsed["name"]
                return dh
            mh, dh = ps.mesh_hash(mesh_path), ps.mdf2_hash(cur)
            base = cur

        self._rb_say("身份 mesh=%s / mdf2=%s" % (mh, dh))
        self._rb_say("  mesh = %s" % mesh_path)
        self._rb_say("  base = %s" % base)

        # 从**真实原 mdf2** 进来的，base 以它为准（否则重置会回到临时副本）
        reg = ps.get_mdf2(idx_doc, mh, dh) or {}
        if (reg and not cur.lower().startswith(ps.TEMP_DIR.lower() + "/")
                and ps.norm_rel(reg.get("baseMdf2") or "") != cur):
            self._rb_say("base 校正为原 mdf2: %s" % cur)
            base = cur
            ps.upsert_mdf2(idx_doc, mh, dh, base, base)
            ps.save_index(root, idx_doc, log=self._rb_say)

        # ② 该原 mdf2 已有副本 → 挑一份 / 新建；没有 → 先问名字再建
        copies = [c for c in ps.list_copies(idx_doc, mh, dh)
                  if ps.exists_edit(root, c["rel"])]
        if copies:
            if len(copies) == 1:
                c = copies[0]
            else:
                c = self._rb_ask_copy(idx_doc, mh, dh, copies)
                if c is None:
                    raise RuntimeError("已取消")
                if c == "__new__":
                    c = self._rb_new_copy(root, idx_doc, mh, dh)
                    if c is None:
                        raise RuntimeError("已取消")
        else:
            ps.upsert_mdf2(idx_doc, mh, dh, base, base)
            c = self._rb_new_copy(root, idx_doc, mh, dh,
                                  first=True, mesh_path=mesh_path)
            if c is None:
                raise RuntimeError("已取消")
            ps.save_index(root, idx_doc, log=self._rb_say)

        self._rb_apply_iso(ent, c["rel"])
        ps.upsert_object(idx_doc, mh, mesh_path, go, ident.get("matCount") or 0)
        ps.upsert_mdf2(idx_doc, mh, dh, base, base)
        ps.upsert_copy(idx_doc, mh, dh, c["name"], c["rel"], c["rev"])
        ps.save_index(root, idx_doc, log=self._rb_say)
        ent["copy"] = c["name"]
        return dh

    def _rb_iso_end(self, root, ent):
        """取消某对象的预览：新建一份 orig 副本再挂上去。

        不能挂回原路径——引擎按路径缓存，同路径不会重建参数块（§13.6 实测）。
        原文件本身从不改动，orig 只是它的字节拷贝。
        """
        ident = self._rb_find_identity(ent)
        if not ident:
            raise rb.BridgeError("读不到该对象的身份（addr=%s）——已被游戏重载"
                                 % _rb_addr_short(ent.get("addr")))
        cur = ps.norm_rel(ident.get("mdf2Path") or "")
        idx_doc = ps.load_index(root)

        hit = self._rb_lookup_by_path(idx_doc, cur)
        if hit and hit["kind"] == "edit":
            mh_hit, dh_hit, reg = hit["mh"], hit["dh"], hit["ent"]
        else:
            # 可能挂在某个副本的旧版本上（离线编译后登记已指向新 rev）
            parsed = ps.parse_temp_rel(cur)
            mh_hit = ps.mesh_hash(ident.get("meshPath") or "")
            dh_hit = parsed["dh"] if parsed and parsed.get("kind") == "edit" else ""
            reg = ps.get_mdf2(idx_doc, mh_hit, dh_hit) if dh_hit else None
            if not reg:
                raise rb.BridgeError("当前不在工作副本上（%s），无需取消" % cur)
        orig_target = ps.norm_rel(reg.get("originalMdf2")
                                  or reg.get("baseMdf2") or "")
        if not orig_target:
            raise rb.BridgeError("登记项里没有原始 mdf2 路径，无法重置")

        orig = ps.create_orig(root, dh_hit, orig_target, log=self._rb_say)
        d = rb.material(ent.get("go"), "apply", mdf=orig, addr=ent.get("addr"), keep=False)
        if d.get("err"):
            raise rb.BridgeError(d["err"])
        ms = d.get("meshes") or []
        if not ms or not ms[0].get("ok"):
            # addr 没命中 = 对象被重载了；这里不静默通过，否则会出现
            # “看着取消了勾选、参数却没重置”。
            raise rb.BridgeError("重置失败：apply 没有命中对象（addr=%s）" % ent.get("addr"))
        ps.upsert_mdf2(idx_doc, mh_hit, dh_hit, orig_target, orig_target,
                       orig_rel=orig)
        ps.save_index(root, idx_doc, log=self._rb_say)
        # 取消 = 放弃本会话的改动，写盘记录一并丢弃（否则下次保存会把旧值写回去）
        self._rb_edits.pop(_rb_addr_key(ent.get("addr")), None)
        self._rb_tex_edits.pop(_rb_addr_key(ent.get("addr")), None)
        self._rb_say("已重置：挂到 orig 副本 %s（参数回到原始值，keep 已关）" % orig)

    def _rb_poll_hold(self):
        """定时查保持项：发现 dead 就清掉那行勾选并提示重选。"""
        if not self._rb_connected:
            return
        ms = getattr(self, "_rb_meshes", None) or []
        if not any(m.get("prev") for m in ms):
            return
        try:
            k = rb.holds()
        except rb.BridgeError:
            return
        dead = {}
        for it in (k.get("holdDetail") or []):
            if "ticks" in it or not it.get("dead"):
                continue
            a = _rb_addr_key(it.get("addr"))
            if a:
                dead[a] = it.get("deadReason") or "gone"
        if not dead:
            return
        for m in ms:
            if (_rb_addr_key(m.get("addr")) in dead) and m.get("prev"):
                self._rb_obj_gone(m, dead[_rb_addr_key(m.get("addr"))])
                return          # 一次只提示一个，避免连环弹窗

    def _wrap_rbridge(self):
        """实时预览页：连接游戏内 REF 服务 → 列表里每个 mesh 一个勾选框（**每行独立**）
        → 选材质 → 改参数。

        未连接时整页禁用（不向游戏发任何请求）；连接后才拉列表。
        未勾选的行完全只读；勾选后为该对象建一份 mdf2 工作副本，调参只作用于副本，
        取消勾选即重置。所有操作按 **addr**（对象地址）定位：findComponents 的索引会
        随重启 / 变体切换位移。
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)

        # 游戏根：由 C# 服务 /paths 返回（连上后自动取，不需要用户手填）

        top = QHBoxLayout()
        top.addWidget(QLabel("REF 服务端口:"))
        self.sp_rb_port = QSpinBox()
        self.sp_rb_port.setRange(1, 65535)
        self.sp_rb_port.setFixedWidth(90)
        self.sp_rb_port.setToolTip(
            "游戏内 REF 菜单 → MMTR Bridge 页可改端口（会写 reframework/data/mmtr_bridge_port.txt）。\n"
            "连接时如果读到该文件，会自动采用它。")
        top.addWidget(self.sp_rb_port)
        self.btn_rb_conn = QPushButton("连接")
        self.btn_rb_conn.setToolTip(
            "连接后每 1s 探活一次（游戏退出会变回未连接）。\n"
            "断开只是本工具停止通信，不影响游戏内已挂载的预览。")
        self.btn_rb_conn.clicked.connect(self._rb_toggle_conn)
        top.addWidget(self.btn_rb_conn)
        self.lbl_rb = QLabel("未连接")
        self.lbl_rb.setStyleSheet("color:#b00020")
        top.addWidget(self.lbl_rb)
        top.addStretch(1)
        v.addLayout(top)

        iso = QHBoxLayout()
        self.lbl_rb_mdf = QLabel("")
        self.lbl_rb_mdf.setStyleSheet("color:#666")
        self.lbl_rb_mdf.setTextInteractionFlags(Qt.TextSelectableByMouse)
        iso.addWidget(self.lbl_rb_mdf, 1)
        self.btn_rb_save = QPushButton("保存到 mdf2")
        self.btn_rb_save.setToolTip(
            "把本会话改过的参数值写进工作副本，并生成一份**新路径**的副本让游戏重载。\n"
            "为什么换新路径：引擎按路径缓存 mdf2，同路径改了文件不会重新加载。\n\n"
            "只写「改过的参数」（同名参数后写覆盖先写），未改的保持文件原值。\n"
            "所有共用该副本的对象会一起切到新文件。")
        self.btn_rb_save.clicked.connect(self._rb_save_to_mdf2)
        iso.addWidget(self.btn_rb_save)
        self.btn_rb_attrs = QPushButton("材质属性…")
        self.btn_rb_attrs.setToolTip(
            "换母材质 / 改 flags（阴影、alpha test、双面、顶点着色器…）。\n\n"
            "换母材质会按新 mmtr 的参数表重建该材质的参数集：\n"
            "  同名参数保留值、新增参数默认 0、旧独有参数丢弃。\n"
            "应用后生成新路径副本并让所有关联对象重载（会短暂闪一下）。\n\n"
            "母材质只能选「编译到预览目录」产出的 mmtr（登记在 materials.json）。")
        self.btn_rb_attrs.clicked.connect(self._rb_edit_attrs)
        iso.addWidget(self.btn_rb_attrs)
        self.btn_rb_tex = QPushButton("贴图编辑…")
        self.btn_rb_tex.setToolTip(
            "编辑当前材质的贴图槽（只改路径，不改槽名）。\n"
            "应用 = 运行时 setMaterialTexture 热替换（不写盘）；\n"
            "点「保存到 mdf2」后才会持久化到工作副本的新版本。\n"
            "新贴图首次加载在游戏暂停时同样会延迟（恢复后生效）。")
        self.btn_rb_tex.clicked.connect(self._rb_edit_textures)
        iso.addWidget(self.btn_rb_tex)
        self.btn_rb_copy = QPushButton("选择副本…")
        self.btn_rb_copy.setToolTip(
            "选择/新建当前 mesh 可用的副本（跨该 mesh 用到的所有原 mdf2）。\n"
            "每行显示「副本名（版本）· 原 mdf2 路径」；取消勾选才是回到原始状态。")
        self.btn_rb_copy.clicked.connect(self._rb_pick_copy)
        iso.addWidget(self.btn_rb_copy)
        self.btn_rb_manage = QPushButton("管理副本…")
        self.btn_rb_manage.setToolTip(
            "重命名 / 删除当前 mesh 下的副本（不可恢复）。\n"
            "标「使用中」的副本（本工具挂着 / 游戏仍保持）不能操作。")
        self.btn_rb_manage.clicked.connect(self._rb_manage_copy)
        iso.addWidget(self.btn_rb_manage)
        v.addLayout(iso)

        f = QHBoxLayout()
        f.addWidget(QLabel("GameObject 过滤:"))
        self.ed_rb_filter = QLineEdit()
        self.ed_rb_filter.setPlaceholderText("留空 = 列出全部（上限 120）；可填 GameObject 名子串")
        f.addWidget(self.ed_rb_filter, 1)
        self.btn_rb_refresh = QPushButton("刷新列表")
        self.btn_rb_refresh.setToolTip("重新拉取场景里的 mesh。刷新不会丢掉任何一行的勾选（按对象地址匹配）")
        self.btn_rb_refresh.clicked.connect(self._rb_refresh)
        f.addWidget(self.btn_rb_refresh)
        v.addLayout(f)

        split = QSplitter(Qt.Horizontal)
        self.lst_rb = QListWidget()
        self.lst_rb.currentRowChanged.connect(self._rb_pick_mesh)
        # 每行一个勾选框 = 该对象是否处于预览状态（互不影响）
        self.lst_rb.itemChanged.connect(self._rb_on_item_changed)
        self.lst_rb.setToolTip(
            "勾选 = 为该对象建 mdf2 工作副本并实时改参（每行独立，可同时开多个）\n"
            "取消 = 挂回 orig 副本把参数重置回原始值\n"
            "点击行文字 = 切换当前编辑对象（不影响任何一行的勾选）")
        split.addWidget(self.lst_rb)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        mr = QHBoxLayout()
        mr.addWidget(QLabel("材质:"))
        self.cmb_rb_mat = NoWheelComboBox()
        self.cmb_rb_mat.currentIndexChanged.connect(self._rb_pick_mat)
        mr.addWidget(self.cmb_rb_mat, 1)
        rv.addLayout(mr)
        self.tbl_rb = QTableWidget(0, 3)
        self.tbl_rb.setHorizontalHeaderLabels(["参数", "类型", "值"])
        self.tbl_rb.verticalHeader().setVisible(False)
        self.tbl_rb.setEditTriggers(QAbstractItemView.NoEditTriggers)   # 名字/类型纯显示
        hh = self.tbl_rb.horizontalHeader()
        # 不强制总宽 = 视口宽：末列保持自己的宽度，页面更宽时右侧留白。
        # （stretch 会让“值”列在宽页面下被无谓拉长，拖动前面的边界时全部由末列承担。）
        hh.setMinimumSectionSize(40)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        self.tbl_rb.setColumnWidth(0, 200)
        self.tbl_rb.setColumnWidth(1, 80)
        self.tbl_rb.setEnabled(False)      # 只有该行勾选了预览才可编辑
        rv.addWidget(self.tbl_rb, 1)
        split.addWidget(right)
        split.setSizes([230, 520])
        v.addWidget(split, 3)

        hint = QLabel("勾选列表里某个对象 = 给它建一份 mdf2 工作副本（不改原文件）并实时改参；"
                      "取消勾选 = 挂回 orig 副本，参数重置（不用重启游戏）。"
                      "每行独立，可同时开多个；点行文字只是切换当前编辑对象。"
                      "同一原 mdf2 可建多份具名副本（「选择副本…」切换/新建），"
                      "存放在 materialTemp/edit/<meshHash>/<mdf2Hash>/<副本名>_v<rev>。")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.ed_rb_log = QPlainTextEdit()
        self.ed_rb_log.setReadOnly(True)
        self.ed_rb_log.setMaximumBlockCount(300)
        v.addWidget(self.ed_rb_log, 1)

        self._rb_meshes = []
        self._rb_mats = []
        self._rb_spins = {}
        self._rb_vec_spins = {}
        self._rb_mesh_i = None
        self._rb_cur_addr = None      # 当前行的对象地址（一切操作的定位键）
        self._rb_building = False     # 重建列表时忽略 itemChanged
        self._rb_matname = None
        self._rb_pending = {}
        # 本会话改过的参数：{对象地址: {材质名: {参数名: [float...]}}}
        # 保存到 mdf2 时只写这些（不动文件里其他参数）。
        self._rb_edits = {}
        self._rb_tex_edits = {}   # {addr: {材质: {槽名: 新路径}}}
        self._rb_timer = QTimer(self)
        self._rb_timer.setSingleShot(True)
        self._rb_timer.setInterval(250)
        self._rb_timer.timeout.connect(self._rb_flush)
        # 每 2 秒看一次 keep 状态：对象被游戏重载时提示重选（不阻塞 UI）
        self._rb_poll = QTimer(self)
        self._rb_poll.setInterval(2000)
        self._rb_poll.timeout.connect(self._rb_poll_hold)
        self._rb_poll.start()

        # 连接开关（默认不连接）：按下「连接」才开始探测与轮询。
        self._rb_connected = False     # 用户是否按下了「连接」
        self._rb_link_ok = False       # 实际链路状态（后台轮询结果）
        self._rb_ping_fut = None
        self._rb_ping_timer = QTimer(self)
        self._rb_ping_timer.setInterval(1000)
        self._rb_ping_timer.timeout.connect(self._rb_ping_tick)
        self._rb_pool = ThreadPoolExecutor(max_workers=2)
        # 当前材质的参数值实时同步（1.5s 一次；只更新没在编辑的框）
        self._rb_sync_fut = None
        self._rb_sync_sent = None
        self._rb_last_edit_ts = 0.0
        self._rb_ident_fut = None     # 未预览对象的身份轮询（见 _rb_watch_identity）
        self._rb_ident_tick = 0
        self._rb_sync_timer = QTimer(self)
        self._rb_sync_timer.setInterval(1500)
        self._rb_sync_timer.timeout.connect(self._rb_sync_tick)

        cfg = self._cfg_load()
        # 游戏根由 /paths 从服务拿；这里的缓存只用于重启后读端口文件（不是用户输入）
        self._rb_game_root = (os.path.normpath(cfg["game_root"])
                              if cfg.get("game_root") else "")
        try:
            self.sp_rb_port.setValue(int(cfg.get("port") or rb.DEFAULT_PORT))
        except (TypeError, ValueError):
            self.sp_rb_port.setValue(rb.DEFAULT_PORT)
        rb.set_default_port(self.sp_rb_port.value())
        self._rb_set_link(False, "未连接")
        return w

    def _sync_force_full(self):
        """同步「强制完整输入」指示框: 由**顶点预设依赖**自动推导(只读)。

        当顶点预设用到 NORMAL/TANGENT/TEXCOORD0 时, 生成端自动把 reduced 族
        (pos_uv1/skin_min/nrm_uv1/skin_min_nrm) 换到 full/skin_full + d30 重指。
        """
        chk = getattr(self, "chk_force_full", None)
        if chk is None:
            return
        _forced = sinp.presets_need_upgrade(mimp.presets(self.asset.inputs, "vertex"))
        chk.blockSignals(True)
        chk.setChecked(bool(_forced))
        chk.setEnabled(False)
        chk.blockSignals(False)

    def _inputs_tree(self, pass_name="main", with_force=False):
        """一个 pass 的输入视图(输入树 + 增删按钮)。返回 (容器, 树)。

        with_force=True(顶点页): 顶部加「强制完整输入」勾选(只有顶点页有, 是顶点唯一的多余项)。
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        if with_force:
            self.chk_force_full = QCheckBox(
                "强制完整输入 (自动: 顶点预设依赖缺失几何输入时)")
            self.chk_force_full.setToolTip(
                "只读指示: 顶点预设用到 NORMAL/TANGENT/TEXCOORD0 时自动开启。\n"
                "开启后把精简族(pos_uv1/skin_min/nrm_uv1/skin_min_nrm)换成 full/skin_full"
                "(其余 world/pack/绑定不变), 输入布局码(d30)同步。\n"
                "去掉相关顶点预设即自动取消。")
            self.chk_force_full.setEnabled(False)
            v.addWidget(self.chk_force_full)
        tree = _InputTree()
        tree.setHeaderLabels(["类别 / 名", "类型", "说明"])
        tree.reordered.connect(lambda _t=tree, _p=pass_name: self._on_input_reordered(_t, _p))
        v.addWidget(tree, 1)
        bar = QHBoxLayout()
        for text, cb in (("＋预设输入", self._add_presets), ("＋参数", self._add_param),
                         ("＋贴图", self._add_tex), ("＋引擎资源", self._add_engine),
                         ("＋函数", self._add_funcs),
                         ("删除选中", self._del_custom),
                         ("重载输入(补依赖)", self._reload_inputs)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        attach_menu(tree, self._menu_inputs)
        return w, tree

    def _stage_of(self, pass_name):
        """pass 名 -> stage(ps/depth/vs); 预设依赖/可用性按 stage 区分。"""
        return "vs" if pass_name == "vertex" else ("depth" if pass_name == "depth" else "ps")

    def _cur_pass(self):
        """输入页当前查看的 pass 名(按钮组: 主/深度/顶点)。"""
        _i = self._in_stack.currentIndex()
        return "depth" if _i == 1 else ("vertex" if _i == 2 else "main")

    def _active_input_tree(self):
        _p = self._cur_pass()
        if _p == "depth":
            return self.tree_inputs_depth
        if _p == "vertex":
            return self.tree_inputs_vs
        return self.tree_inputs

    def _sync_inputs(self):
        """规范化输入(补齐预设依赖)并写回资产(仅内存)。"""
        self.asset.inputs = mimp.normalize(self.asset.inputs)

    def _after_inputs_changed(self):
        """增删输入后的统一收尾: 补依赖 -> 刷新输入树/指示框 -> 刷新顶点预览。"""
        self._sync_inputs()
        self.refresh_inputs()
        self._refresh_vs_preview()

    def _on_input_reordered(self, tree, pass_name):
        """输入树内拖拽重排(仅 参数/贴图 分组): 读改组顺序 -> 写回 asset.inputs -> 刷新。

        - 参数是**全局**的: 任一棵树改都作用于同一份 `inputs["param"]`;
        - 贴图按 pass: 只改当前树的 `inputs["tex"][pass_name]`;
        - 顺序 = 生成时的声明/绑定顺序(材质源按名引用, 顺序只影响参数表/SRV 分配)。
        """
        _param, _tex = None, None

        def _scan(it):
            nonlocal _param, _tex
            g = it.data(0, Qt.UserRole + 1)
            if isinstance(g, tuple) and g[:1] == ("group",):
                _names = []
                for j in range(it.childCount()):
                    c = it.child(j)
                    cd = c.data(0, Qt.UserRole)
                    if isinstance(cd, tuple) and cd and cd[0] == g[1]:
                        _names.append((cd[1], c.text(1)))
                if g[1] == "param":
                    _param = [{"name": n, "type": t} for (n, t) in _names]
                elif g[1] == "tex":
                    _tex = [n for (n, _t) in _names]
            for j in range(it.childCount()):
                _scan(it.child(j))

        for i in range(tree.topLevelItemCount()):
            _scan(tree.topLevelItem(i))
        # 安全网: 仅在“集合不变(只改顺序)”时写回 —— 防止误拖出组导致丢项/串项。
        _cur_p = [(e.get("name"), e.get("type") or "float")
                  for e in (self.asset.inputs.get("param") or [])]
        if _param is not None and {p["name"] for p in _param} == {n for n, _t in _cur_p}:
            self.asset.inputs["param"] = _param
        _cur_t = mimp.textures(self.asset.inputs, pass_name)
        if _tex is not None and set(_tex) == set(_cur_t):
            self.asset.inputs["tex"][pass_name] = _tex
        self._after_inputs_changed()

    def _locked_names(self, pass_name="main"):
        """被锁定(禁止删除)的引擎资源名 -> [来源...]: pass 依赖 + 预设依赖 + 参数所需 UserMaterial。"""
        _pre = mimp.presets(self.asset.inputs, pass_name)
        # 依赖按 stage 区分(如 uv0 在 PS 靠 INTERPOLATOR、在 VS 直接用 i.uv0)
        res = sinp.resolve(_pre, sinp.names_in(self._base_iface(pass_name)),
                           stage=self._stage_of(pass_name))
        lock = {k: list(v) for k, v in res["lock"].items()}
        _pt = {"depth": "深度 pass", "vertex": "顶点"}.get(pass_name, "主 pass")
        _tmpl = self.asset.template.get("pass_template") or "deferred_std"
        for nm in minp.pass_dep_names(pass_name, _tmpl):
            lock.setdefault(nm, [])
            if _pt not in lock[nm]:
                lock[nm].append(_pt)
        if mimp.params(self.asset.inputs):
            lock.setdefault("UserMaterial", [])
            if "材质参数" not in lock["UserMaterial"]:
                lock["UserMaterial"].append("材质参数")
        return lock

    def _reload_inputs(self):
        self._after_inputs_changed()

    def _add_presets(self):
        """从预设(语义)输入目录挑选(**只增删当前 pass 的项**); 依赖的引擎资源自动加入并锁定。"""
        _pass = self._cur_pass()
        _stage = "vs" if _pass == "vertex" else ("depth" if _pass == "depth" else "ps")
        _own = mimp.presets(self.asset.inputs, _pass)
        d = PresetDialog(self, selected=set(_own), stage=_stage)
        if d.exec() != QDialog.Accepted:
            return
        self.asset.inputs["preset"][_pass] = list(d.selected_names())
        self._after_inputs_changed()

    def _add_engine(self):
        """从允许清单挑选引擎资源(cbuffer/texture/sampler); **只增删当前 pass**(其它 pass 不变)。"""
        _pass = self._cur_pass()
        lock = self._locked_names(_pass)
        _own = mimp.engine(self.asset.inputs, _pass)
        _old = set(_own)
        d = EngineResDialog(self, selected=_old | set(lock), locked=set(lock))
        if d.exec() != QDialog.Accepted:
            return
        # 持久化: 保留原本已有的 + 新勾选的非锁定项(结构性/预设依赖由系统自动供给, 不落盘)
        self.asset.inputs["engine"][_pass] = [
            n for n in d.selected_names() if (n in _old) or (n not in lock)]
        self._after_inputs_changed()

    def _add_funcs(self):
        """从函数库挑选用到当前 pass 的自定义函数(导入后直接在材质源调用)。

        门控: 目标阶段(主/深度=PS; 顶点=VS)编译检查不通过的**拒绝导入**; "未确认"导入前先检查。
        """
        _pass = self._cur_pass()
        _stage = "vs" if _pass == "vertex" else "ps"
        _own = mimp.funcs(self.asset.inputs, _pass)
        d = FuncDialog(self, selected=set(_own), stage=_stage)
        if d.exec() != QDialog.Accepted:
            return
        chosen = d.selected_names()
        bad = []
        for n in chosen:
            r = cfun.check(n)          # "未确认" -> 在此检查并缓存
            if not r[_stage]["ok"]:
                _e = (r[_stage]["err"] or "").strip().splitlines()
                bad.append((n, _e[0] if _e else "?"))
        if bad:
            QMessageBox.warning(
                self, "阶段检查不通过",
                "以下函数在本 pass 的阶段(%s)检查不通过, 不予导入:\n\n%s"
                % (_stage, "\n".join("%s:\n  %s" % (n, e) for n, e in bad)))
            return
        self.asset.inputs["func"][_pass] = chosen
        self._after_inputs_changed()

    def _on_src_changed(self):
        # 源码变动 -> 旧诊断(行号)失效; 防抖后再清(不在 textChanged 内同步 rehighlight,
        # 也避免每敲一下键就整篇重绘)。
        self._src_timer.start()

    def _clear_src_diags(self):
        # 仅在确实有旧诊断时才重绘(无错误时不触发整篇 rehighlight)
        if self._src_hl._diags:
            self._src_hl.set_diagnostics([])
        for _h in getattr(self, "_ex_hl", {}).values():
            if _h._diags:
                _h.set_diagnostics([])

    def refresh_inputs(self):
        """刷新三个 pass 的输入树(每 pass 独立)。"""
        self._sync_force_full()
        self._fill_input_tree(self.tree_inputs, "main")
        self._fill_input_tree(self.tree_inputs_depth, "depth")
        self._fill_input_tree(self.tree_inputs_vs, "vertex")

    def _fill_input_tree(self, tree, pass_name):
        tree.clear()
        # 1) 插值输入(pass 依赖; 非资源; 只读展示) —— 不属于引擎资源栏
        _presets = mimp.presets(self.asset.inputs, pass_name)
        _interps = minp.interp_inputs(pass_name, _presets)
        if _interps:
            itop = QTreeWidgetItem(["插值输入 (pass 依赖, 只读)", "",
                                    "由 pass 声明依赖; 系统自动添加, 不锁定(无依赖自动去除)"])
            tree.addTopLevelItem(itop)
            for _n, _d in _interps:
                ti = QTreeWidgetItem([_n, "", _d])
                ti.setData(0, Qt.UserRole, ("copy", _n))
                itop.addChild(ti)
        # 2) 自定义输入(该 pass: 预设/参数/贴图/函数/引擎资源; 会写进 mmtr)
        presets = list(_presets)
        params = mimp.params(self.asset.inputs)
        textures = mimp.textures(self.asset.inputs, pass_name)
        engine = mimp.engine(self.asset.inputs, pass_name)
        lock = self._locked_names(pass_name)
        _tmpl = self.asset.template.get("pass_template") or "deferred_std"
        _explicit = set(engine)
        # 自动供给(不进资产)的引擎资源: 模板/pass 结构依赖 + 预设依赖 + UserMaterial。
        # 与手动添加的**同栏展示**, 仅靠橙色+锁定区分。
        _auto = []
        for _n in list(minp.pass_dep_names(pass_name, _tmpl)) + list(lock):
            # 只列真正的引擎资源; INTERPOLATOR* 这类插值依赖不进树(不是资源)。
            if _n not in _explicit and _n not in _auto and minp.find(_n) is not None:
                _auto.append(_n)
        if params and "UserMaterial" not in _explicit and "UserMaterial" not in _auto:
            _auto.append("UserMaterial")
        engine.extend(x for x in _auto if x not in engine)
        engine = minp.sort_names(engine)      # 显示顺序由工具全序决定(不由材质书写顺序)
        cust = QTreeWidgetItem(["自定义输入 (预设/参数/贴图/函数)", "", "写进 mmtr 参数表/绑定"])
        tree.addTopLevelItem(cust)
        pre = QTreeWidgetItem(["预设输入 (//! preset)", "", "%d" % len(presets)])
        cust.addChild(pre)
        _sel = set(presets)
        for g in sinp.groups():                      # 按分组列已添加的项(单项粒度)
            _items = [e for e in g["items"] if e["name"] in _sel]
            if not _items:
                continue
            gnode = QTreeWidgetItem([g["group"], "", ""])
            pre.addChild(gnode)
            for e in _items:
                deps = sinp.deps_of(e["name"], self._stage_of(pass_name))
                d = e.get("desc", "")
                if deps:
                    d = ("%s | 依赖: %s" % (d, ", ".join(deps))).strip(" |")
                it = QTreeWidgetItem([e["name"],
                                      (e.get("field") or {}).get("type", ""), d])
                it.setData(0, Qt.UserRole, ("preset", e["name"]))
                gnode.addChild(it)
        for n in presets:                    # 目录里没有的(旧/手写)也列出, 便于删除
            if sinp.find(n) is None:
                it = QTreeWidgetItem([n, "", "(未知语义输入项)"])
                it.setData(0, Qt.UserRole, ("preset", n))
                pre.addChild(it)
        pnode = QTreeWidgetItem(["参数 (//! param)", "", "%d" % len(params)])
        pnode.setData(0, Qt.UserRole + 1, ("group", "param"))   # 可拖拽重排(组内)
        cust.addChild(pnode)
        for n, t in params:
            # 此处显示/编辑的是**裸名**(= mdf2 / mmtr 参数表名); RDEF 名(带 `VAR_`) 只在
            # UserMaterial 处展示 —— 避免误改到自动生成的 `VAR_` 前缀。
            base = mgen.param_base_name(n)
            rdef_hint = ("RDEF 成员: %s" % n) if n.startswith(mgen.PARAM_PREFIX) else ""
            it = QTreeWidgetItem([base, t, rdef_hint])
            it.setIcon(0, _grip_icon())                        # 拖拽手柄(组内排序)
            it.setData(0, Qt.UserRole, ("param", n))
            pnode.addChild(it)
        tnode = QTreeWidgetItem(["贴图 (//! tex)", "", "%d" % len(textures)])
        tnode.setData(0, Qt.UserRole + 1, ("group", "tex"))
        cust.addChild(tnode)
        for n in textures:
            it = QTreeWidgetItem([n, "", ""])
            it.setIcon(0, _grip_icon())
            it.setData(0, Qt.UserRole, ("tex", n))
            tnode.addChild(it)
        enode = QTreeWidgetItem(["引擎资源 (//! engine)", "", "%d" % len(engine)])
        cust.addChild(enode)

        def _fill_engine(parent, names):
            for n in names:
                e = minp.find(n) or {}
                kd = e.get("kind", "")
                if kd == "cbuffer":
                    extra = "%d 成员" % len(e.get("members", []))
                elif kd == "texture":
                    extra = "%s/%s" % (e.get("fmt"), e.get("dim"))
                else:
                    extra = ""
                desc = minp.desc_of(n)
                if n in lock:
                    extra = ("锁定: 由 %s 依赖" % " / ".join(lock[n])).strip()
                _is_auto = (n not in _explicit)
                if _is_auto:
                    extra = ("自动供给(不写入资产) %s" % extra).strip()
                it = QTreeWidgetItem([n, kd, desc])
                it.setToolTip(2, ("%s\n%s" % (extra, desc)).strip())
                if n in lock or _is_auto:
                    it.setForeground(0, QColor("#b8791a"))
                it.setData(0, Qt.UserRole, ("engine", n))
                parent.addChild(it)
                if n == "UserMaterial":
                    # UserMaterial 的成员 = 自定义材质参数(单独声明在 //! param)
                    for pn, pt in params:
                        cm = QTreeWidgetItem([pn, pt, "(材质参数)"])
                        cm.setData(0, Qt.UserRole, ("param", pn))
                        it.addChild(cm)
                else:
                    for m in (e.get("members") or []):
                        mn = m.get("name", "")
                        cm = QTreeWidgetItem([mn, m.get("type", ""),
                                             minp.member_desc(n, mn)])
                        cm.setData(0, Qt.UserRole, ("copy", mn))
                        it.addChild(cm)

        _fill_engine(enode, engine)
        funcs = mimp.funcs(self.asset.inputs, pass_name)
        fnode = QTreeWidgetItem(["函数 (自定义库)", "", "%d" % len(funcs)])
        cust.addChild(fnode)
        for n in funcs:
            _r = cfun.cached(n)
            if cfun.get(n) is None:
                status, col = "库中不存在", QColor("#c0392b")
            elif _r is None:
                status, col = "未检查", QColor("#b8791a")
            else:
                _ps = "✓" if _r["ps"]["ok"] else "✗"
                _vs = "✓" if _r["vs"]["ok"] else "✗"
                status = "PS %s / VS %s" % (_ps, _vs)
                col = (QColor("#1a7f37") if (_r["ps"]["ok"] and _r["vs"]["ok"])
                       else QColor("#b8791a"))
            _dep = sorted(cfun.function_calls(n))
            it = QTreeWidgetItem([n, status, ("依赖: " + ", ".join(_dep)) if _dep else ""])
            it.setForeground(1, col)
            it.setData(0, Qt.UserRole, ("func", n))
            fnode.addChild(it)
        # 默认只展开到"分类"层(0=顶层, 1=分类); 资源项与 cbuffer 成员默认折叠
        tree.expandToDepth(1)
        fit_columns(tree, (0, 1, 2))

    def _menu_inputs(self, item):
        kind = item.data(0, Qt.UserRole) if item is not None else None
        if not kind:
            return None
        acts = []
        if kind[0] in ("param", "tex"):
            acts.append(("改名", lambda: self._rename_custom(kind)))
            acts.append(("删除", self._del_custom))
        elif kind[0] == "preset":
            acts.append(("删除", self._del_custom))
        elif kind[0] == "engine":
            acts.append(("删除", self._del_custom))
        elif kind[0] == "func":
            acts.append(("删除", self._del_custom))
        token = kind[1]
        acts.append(("复制: %s" % token, lambda: self._copy_token(token)))
        return acts

    def _copy_token(self, token):
        _copy_to_clipboard(token)
        self.lbl_status.setText("已复制: %s" % token)

    def _param_dialog(self, title, base0, pre0):
        """参数名对话框: 编辑**裸名** + 是否加 `VAR_` 前缀(RDEF 成员名)。返回 (裸名, 带前缀) 或 None。

        `VAR_` 前缀由勾选**自动生成、不可直接编辑**; 裸名 = mdf2 / mmtr 参数表名。
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)
        ed_name = QLineEdit(base0)
        form.addRow("参数名(裸名):", ed_name)
        chk = QCheckBox("加 VAR_ 前缀 (RDEF 成员名; 材质代码里就用它引用)")
        chk.setChecked(pre0)
        form.addRow("", chk)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return None
        base = ed_name.text().strip()
        if not base:
            return None
        # 裸名不能以 `VAR_` 开头(否则"不带前缀"时会与"裸名本身带 VAR_"混淆)。
        if not chk.isChecked() and base.startswith(mgen.PARAM_PREFIX):
            QMessageBox.warning(self, "非法名",
                                "裸名不能以 `VAR_` 开头。\n请改名, 或勾选「加 VAR_ 前缀」。")
            return None
        return base, chk.isChecked()

    def _add_param(self):
        got = self._param_dialog("新增参数", "NewParam", True)
        if got is None:
            return
        base, pre = got
        name = mgen.param_rdef_name(base, pre)
        typ = pick_type(self, "参数类型", "float4")
        if typ is None:
            return
        # 命名校验: RDEF 名唯一 + 裸名唯一(防 `A`/`VAR_A` 撞同一 mdf2/参数表名)。
        confl = mgen.param_name_conflicts(mimp.params(self.asset.inputs) + [(name, typ)])
        if confl:
            QMessageBox.warning(self, "命名冲突", "\n".join(confl))
            return
        self.asset.inputs["param"].append({"name": name, "type": typ})
        self._after_inputs_changed()

    def _add_tex(self):
        name, ok = QInputDialog.getText(self, "新增贴图", "贴图槽名(如 BaseMetalMap):")
        if not ok or not name.strip():
            return
        name = name.strip()
        _pass = self._cur_pass()
        if name in self.asset.inputs["tex"][_pass]:
            QMessageBox.warning(self, "重复", "贴图已存在: %s" % name)
            return
        if minp.find(name) is not None:
            QMessageBox.warning(
                self, "与引擎资源重名",
                "%s 是引擎资源名, 不能用作材质贴图 ——\n"
                "该槽会同时被引擎(按名供给)和 mdf2 争用, 归谁不确定, 可能顶掉引擎资源或贴图不生效。\n"
                "请改名(如 %s_Map)。" % (name, name))
            return
        self.asset.inputs["tex"][_pass].append(name)
        self._after_inputs_changed()

    def _rename_custom(self, kind):
        cat, name = kind
        _pass = self._cur_pass()
        if cat == "param":
            # 改名: 编辑**裸名** + 是否带 `VAR_` 前缀; RDEF 名自动 = 前缀 + 裸名。
            got = self._param_dialog("改名参数", mgen.param_base_name(name),
                                     name.startswith(mgen.PARAM_PREFIX))
            if got is None:
                return
            base, pre = got
            new = mgen.param_rdef_name(base, pre)
            if new == name:
                return
            _all = [(new if n == name else n, t) for (n, t) in mimp.params(self.asset.inputs)]
            confl = mgen.param_name_conflicts(_all)
            if confl:
                QMessageBox.warning(self, "命名冲突", "\n".join(confl))
                return
            self.asset.inputs["param"] = [
                {"name": (new if e["name"] == name else e["name"]), "type": e["type"]}
                for e in self.asset.inputs["param"]]
        else:
            new, ok = QInputDialog.getText(self, "改名", "新名字:", text=name)
            if not ok or not new.strip() or new.strip() == name:
                return
            new = new.strip()
            if new in self.asset.inputs["tex"][_pass]:
                QMessageBox.warning(self, "重复", "贴图已存在: %s" % new)
                return
            if minp.find(new) is not None:
                QMessageBox.warning(self, "与引擎资源重名",
                                    "%s 是引擎资源名, 不能用作材质贴图。请改名。" % new)
                return
            self.asset.inputs["tex"][_pass] = [
                new if n == name else n for n in self.asset.inputs["tex"][_pass]]
        self._after_inputs_changed()

    def _del_custom(self):
        it = self._active_input_tree().currentItem()
        kind = it.data(0, Qt.UserRole) if it is not None else None
        if not kind:
            return
        cat, name = kind
        _pass = self._cur_pass()
        lock = self._locked_names(_pass)
        if cat == "engine" and name in lock:
            QMessageBox.information(
                self, "已锁定",
                "%s 由 %s 依赖, 不能删除。\n如需删除, 请先移除依赖它的预设输入。"
                % (name, " / ".join(lock[name])))
            return
        if cat == "preset":
            self.asset.inputs["preset"][_pass] = [
                n for n in mimp.presets(self.asset.inputs, _pass) if n != name]
        elif cat == "param":
            self.asset.inputs["param"] = [
                e for e in self.asset.inputs["param"] if e["name"] != name]
        elif cat == "engine":
            self.asset.inputs["engine"][_pass] = [
                n for n in mimp.engine(self.asset.inputs, _pass) if n != name]
        elif cat == "func":
            self.asset.inputs["func"][_pass] = [
                n for n in mimp.funcs(self.asset.inputs, _pass) if n != name]
        else:
            self.asset.inputs["tex"][_pass] = [
                n for n in mimp.textures(self.asset.inputs, _pass) if n != name]
        self._after_inputs_changed()

    # ---- 编译诊断 ----
    def _split_diags(self, err_text, off_tmpl, fmain_n, ex_n, lmap=None):
        """把组装报错按区域拆成 (材质源诊断, 附加内容诊断)。

        区域布局(材质源之前): 模板(<=off_tmpl) | 库函数注入段 | 附加内容 | 材质源。
        lmap: 材质源(已剥离某钩子)行号 -> ed_src 行号 的映射。
        行号分别映射回 ed_src(经 lmap) 与对应附加内容编辑器。
        """
        out_src, out_ex = [], []
        base = off_tmpl + fmain_n
        for line in (err_text or "").splitlines():
            m = _HLSL_ERR_RE.search(line)
            if not m:
                continue
            aln = int(m.group(1))
            col = max(0, int(m.group(2)) - 1)
            msg = "%s %s: %s" % (m.group(4), m.group(5), m.group(6))
            if aln <= base:
                continue     # 模板 / 库函数注入段(库函数另有「函数库页」检查)
            if aln <= base + ex_n:
                out_ex.append((aln - base, col, 10 ** 6, msg))     # 附加内容编辑器行号
            else:
                uln = aln - (base + ex_n)
                if lmap is not None:
                    uln = lmap(uln)
                out_src.append((uln, col, 10 ** 6, msg))
        return out_src, out_ex

    # ---- 操作 ----
    def _load_default_material(self):
        tmpl = self.asset.template.get("pass_template") or "deferred_std"
        # 优先用配套的默认材质资产(.mmat.json): 源 + inputs 一起, 与 open_asset 同口径。
        _jp = mpass.default_asset_file(tmpl)
        if _jp:
            try:
                _d = masset.MaterialAsset.load(_jp)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "载入失败", str(e))
                return
            self.ed_src.setPlainText(_d.shading_source)
            self.asset.shading_source = _d.shading_source
            self.asset.inputs = mimp.from_dict(mimp.to_dict(_d.inputs))
            self.asset.shading_extra = dict(_d.shading_extra)
            for _p, _e in getattr(self, "ed_extra", {}).items():
                _e.setPlainText(self.asset.shading_extra.get(_p, ""))
            self.refresh_info()
            return
        # 回退(无配套资产): 只给源码, 输入需用户自己加
        try:
            src = mpass.default_material(tmpl)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "载入失败", str(e))
            return
        self.ed_src.setPlainText(src)
        self.asset.shading_source = src
        self.asset.inputs = mimp.empty()
        # 自定义模式的默认 MaterialVertex 直接引用 vi.xxx ⇒ 载入默认材质后立即补齐顶点预设。
        self._ensure_custom_vertex_presets()
        self.asset.shading_extra = {p: "" for p in mimp.PASSES}
        for _e in getattr(self, "ed_extra", {}).values():
            _e.setPlainText("")
        self.refresh_info()

    def _new_asset(self):
        sh = self.cmb_shading.currentData()
        lt = self.cmb_light.currentData()
        tmpl = _default_template(sh, lt)
        self.asset = masset.MaterialAsset(lighting_mode=lt, shading_type=sh,
                                          template={"pass_template": tmpl})
        self._last_mmtr_bytes = None
        self.ed_src.setPlainText("")
        self._apply_asset()
        self._load_default_material()

    def open_asset(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开材质资产", "",
                                              "材质资产 (*.mmat.json);;All (*)")
        if not path:
            return
        try:
            self.asset = masset.MaterialAsset.load(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "打开失败", str(e))
            return
        self._last_mmtr_bytes = None
        self._asset_path = path
        self._sync_inputs()
        self._apply_asset()
        self.lbl_status.setText("已打开 %s" % os.path.basename(path))

    def save_asset(self):
        self._sync_asset()
        self._sync_inputs()
        path = _get_save_path(self, "保存材质资产",
                              _strip_ver_suffix(self.asset.name) + ".mmat.json",
                              "材质资产 (*.mmat.json);;All (*)")
        if not path:
            return
        try:
            self.asset.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self._asset_path = path
        self.lbl_status.setText("已保存 %s" % os.path.basename(path))

    def compile_check(self):
        self._sync_asset()
        self._sync_inputs()
        tmpl = self.asset.template.get("pass_template") or "deferred_std"
        src = self.asset.shading_source or ""
        # ---- 必需函数校验(每个 pass 都必须提供; 缺则报错, 不静默回退默认) ----
        if not src.strip():
            self._src_hl.set_diagnostics([])
            self.lbl_status.setText("[材质源为空] 请先「载入默认材质」或填写材质源")
            self.lbl_status.setStyleSheet("color:#c0392b")
            return
        missing = [nm for nm in ("MaterialMain", "MaterialDepth", "MaterialVertex")
                   if nogen._hlsl_fn_span(src, nm) is None]
        if missing:
            self._src_hl.set_diagnostics([])
            self.tabs.setCurrentIndex(0)
            self.lbl_status.setText("[缺少必需函数] %s (每个 pass 都必须提供)"
                                    % ", ".join("void %s(...)" % n for n in missing))
            self.lbl_status.setStyleSheet("color:#c0392b")
            return
        # ---- 自定义函数: 校验"调用了未导入的函数", 取出分阶段校验过的注入文本 ----
        for _p, _fn in (("main", "MaterialMain"), ("depth", "MaterialDepth"),
                        ("vertex", "MaterialVertex")):
            _miss = self._missing_funcs(_p, _fn)
            if _miss:
                self.lbl_status.setText(
                    "[缺少函数导入] pass '%s' 调用了未导入的函数: %s (请在「输入」页的「函数」里导入)"
                    % (_p, ", ".join(_miss)))
                self.lbl_status.setStyleSheet("color:#c0392b")
                return
        try:
            _fmain = self._funcs_text("main", "ps")
            _fdepth = self._funcs_text("depth", "ps")
        except ValueError as e:
            self.lbl_status.setText("[自定义函数检查不通过] %s" % str(e).replace("\n", "  "))
            self.lbl_status.setStyleSheet("color:#c0392b")
            return
        _ex_main = nogen.extra_text(self.asset.shading_extra, "main")
        _ex_depth = nogen.extra_text(self.asset.shading_extra, "depth")
        for _h in self._ex_hl.values():
            _h.set_diagnostics([])
        iface, ka, minput = self._effective_inputs("main")
        # ---- 主 pass(剥掉 MaterialDepth/MaterialVertex: 未调用函数/VS 类型会污染主 PS) ----
        m_src, m_map = nogen.strip_line_map_multi(src, ("MaterialDepth", "MaterialVertex"))
        m_src = _fmain + _ex_main + m_src
        try:
            self.ed_full.setPlainText(mpass.build_source(m_src, tmpl, iface=iface,
                                                         keepalive=ka, minput=minput))
        except Exception as e:  # noqa: BLE001
            self.ed_full.setPlainText(";; 组装失败: %s" % e)
            self._src_hl.set_diagnostics([])
            self.lbl_status.setText("[组装失败] %s" % e)
            self.lbl_status.setStyleSheet("color:#c0392b")
            return
        m_dxbc, m_err = mpass.compile_shading(m_src, tmpl, iface=iface,
                                              keepalive=ka, minput=minput)
        # ---- 深度 pass(恒组装; 剥掉 MaterialMain/MaterialVertex) ----
        d_src, d_map = nogen.strip_line_map_multi(src, ("MaterialMain", "MaterialVertex"))
        d_src = _fdepth + _ex_depth + d_src
        iface_d, _, minput_d = self._effective_inputs("depth")
        try:
            self.ed_full_depth.setPlainText(
                mpass.build_source(d_src, "deferred_depth", iface=iface_d,
                                   keepalive="", minput=minput_d,
                                   presets=mimp.presets(self.asset.inputs, "depth")))
        except Exception as e:  # noqa: BLE001
            self.ed_full_depth.setPlainText(";; 组装失败: %s" % e)
        d_dxbc, d_err = mpass.compile_shading(d_src, "deferred_depth", iface=iface_d,
                                              keepalive="", minput=minput_d,
                                              presets=mimp.presets(self.asset.inputs, "depth"))
        # ---- 报错(优先主 pass) ----
        if m_err:
            _sd, _ed = self._split_diags(
                m_err, mpass.material_line_offset(tmpl),
                _fmain.count("\n"), _ex_main.count("\n"), m_map)
            self._src_hl.set_diagnostics(_sd)
            self._ex_hl["main"].set_diagnostics(_ed)
            if _sd:
                self.tabs.setCurrentWidget(self.ed_src)       # 材质源红线
            elif _ed:
                self.tabs.setCurrentWidget(self._extra_page)  # 附加内容红线
                self._extra_stack.setCurrentIndex(0)
            _all = _sd + _ed
            first = _all[0][3] if _all else (m_err.strip().splitlines()[0] if m_err.strip() else "?")
            self.lbl_status.setText("[主 pass 编译失败] %d 处%s: %s"
                                    % (len(_all),
                                       ("(第%d行)" % _all[0][0]) if _all else "",
                                       first[:90]))
            self.lbl_status.setStyleSheet("color:#c0392b")
            self.lbl_status.setToolTip(m_err[:4000])
            return
        if d_err:
            _sd, _ed = self._split_diags(
                d_err, mpass.material_line_offset("deferred_depth"),
                _fdepth.count("\n"), _ex_depth.count("\n"), d_map)
            self._src_hl.set_diagnostics(_sd)
            self._ex_hl["depth"].set_diagnostics(_ed)
            if _sd:
                self.tabs.setCurrentWidget(self.ed_src)
            elif _ed:
                self.tabs.setCurrentWidget(self._extra_page)
                self._extra_stack.setCurrentIndex(1)
            else:
                self._seg_show(1)          # 切到"深度 pass"视图
            _all = _sd + _ed
            first = _all[0][3] if _all else (d_err.strip().splitlines()[0] if d_err.strip() else "?")
            self.lbl_status.setText("[深度 pass 编译失败] %d 处%s: %s"
                                    % (len(_all),
                                       ("(第%d行)" % _all[0][0]) if _all else "",
                                       first[:90]))
            self.lbl_status.setStyleSheet("color:#c0392b")
            self.lbl_status.setToolTip(d_err[:4000])
            return
        self._src_hl.set_diagnostics([])
        self.lbl_status.setToolTip("")
        mv = verify_dxbc(m_dxbc)
        msg = ("编译 OK: 主 %dB stage=%s disasm=%s strip=%s reflect=%s"
               % (len(m_dxbc), mv["stage"], mv["disasm_ok"], mv["strip_ok"], mv["reflect_ok"]))
        dv = verify_dxbc(d_dxbc)
        msg += (" | 深度 %dB out=%s disasm=%s strip=%s reflect=%s"
                % (len(d_dxbc), "[]" if not dv.get("stage") else "",
                   dv["disasm_ok"], dv["strip_ok"], dv["reflect_ok"]))
        self._refresh_vs_preview()
        _vs_ok = self.lbl_vs.text().startswith("编译 OK")
        msg += " | 顶点(VS) %s" % ("OK" if _vs_ok else "失败(见「顶点 (VS)」页)")
        self.lbl_status.setText(msg)
        self.lbl_status.setStyleSheet("color:#1a7f37" if _vs_ok else "color:#c0392b")

    def _seg_show(self, k):
        """切到"组装"页并选中第 k 个 pass 视图。"""
        self._asm_stack.setCurrentIndex(k)
        for j, b in enumerate(self._asm_btns):
            b.setChecked(j == k)
        self.tabs.setCurrentWidget(self._asm_page)

    def _generate(self):
        """生成 mmtr: 无 donor(版本预设 + 我们的材质 PS; 不接任何 master)。"""
        self._sync_asset()
        self._sync_inputs()
        tmpl = self.asset.template.get("pass_template") or "deferred_std"
        if not self.asset.is_ok():
            errs = "\n".join(m for lv, m in self.asset.validate() if lv == "error")
            if QMessageBox.question(self, "配置有误", errs + "\n\n仍要生成吗？") != QMessageBox.Yes:
                return None, None
        try:
            data, rp = nogen.build(self.asset.shading_source or "",
                                   _pass_of_template(tmpl), tmpl,
                                   inputs=self.asset.inputs,
                                   extra=self.asset.shading_extra,
                                   vo_kind=self._vo_kind())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "生成失败", str(e))
            return None, None
        self._last_mmtr_bytes = data
        return data, {"replaced": [], "replaced_instance": [], "skipped": [],
                      "bad": [], "issues": rp.get("issues", []),
                      "ps_size": rp["ps_size"], "groups": rp["groups"],
                      "records": rp["records"], "nogen": True,
                      "vs_gen_failed": rp.get("vs_gen_failed") or 0,
                      "vs_gen_errors": rp.get("vs_gen_errors") or []}

    def generate_mmtr(self):
        data, rep = self._generate()
        if data is None:
            return
        _vf = rep.get("vs_gen_failed") or 0
        if _vf:
            _e = "\n".join("  %s\n      %s" % (k, v)
                            for k, v in (rep.get("vs_gen_errors") or []))
            QMessageBox.warning(
                self, "顶点(VS) 生成失败(已回退银行)",
                "有 %d 个 VS 变体编译失败 ⇒ 已回退为银行字节; 该材质的顶点效果不会生效。\n\n%s\n\n"
                "常见原因: 顶点里采样必须用 SampleLevel(显式 LOD); 变量名拼写; 未声明标识符。"
                % (_vf, _e))
        path = _get_save_path(self, "生成 mmtr",
                              _strip_ver_suffix(self.asset.name) + ".mmtr.1808168797",
                              "mmtr (*.mmtr.1808168797);;All (*)")
        if not path:
            return
        open(path, "wb").write(data)
        tag = "无 donor"
        self.lbl_status.setText("已生成(%s) %s (%dB) PS=%sB 绑定组=%s 槽=%s"
                                % (tag, os.path.basename(path), len(data),
                                   rep.get("ps_size"), rep.get("groups"),
                                   rep.get("records")))
        QMessageBox.information(self, "生成成功",
                                "-> %s\n\n方式=%s\nPS=%sB\n绑定组=%s\n记录槽=%s"
                                % (path, tag, rep.get("ps_size"), rep.get("groups"),
                                   rep.get("records")))

    def export_instance(self):
        self._sync_asset()
        data = self._last_mmtr_bytes
        if data is None:
            data, rep = self._generate()
            if data is None:
                return
        path = _get_save_path(self, "导出材质实例",
                              _strip_ver_suffix(self.asset.name) + ".mdf2.10",
                              "mdf2 (*.mdf2.10);;All (*)")
        if not path:
            return
        mpath = (self.asset.template.get("mmtr_path")
                 or "MasterMaterial/Master/%s.mmtr" % self.asset.name)
        _slots = []
        for _p in ("main", "depth", "vertex"):
            for _n in mimp.textures(self.asset.inputs, _p):
                if _n not in _slots:
                    _slots.append(_n)
        try:
            m = minst.from_mmtr(data, mpath, self.asset.name, shading_type="Standard",
                                texture_slots=_slots)
            n = m.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))
            return
        self.lbl_status.setText("已导出材质实例 %s (%dB) master=%s"
                                % (os.path.basename(path), n, mpath))
        QMessageBox.information(self, "导出成功",
                                "-> %s (%d B)\nmaster=%s" % (path, n, mpath))


    # ---- 编译到预览目录 -------------------------------------------------
    # 产物进游戏缓存目录（目录由服务 /paths 给出，用户不用填游戏路径），并登记到
    # mmatlib 的「母材质库」；「实时预览」页换母材质时**只能用登记过的**。
    # 文件名带版本号（<材质名>_r<rev>）：引擎按路径缓存资源，同路径改了内容不会重载。
    def compile_to_preview(self):
        """编译当前资产 → 写进服务给出的缓存目录 → 登记 → 清理旧产物。"""
        if not rb.alive(timeout=2.0):
            QMessageBox.warning(self, "未连接",
                                "需要先在「实时预览」页连上 REF 服务。\n\n"
                                "请确认游戏在运行、C# 插件已加载。")
            return
        try:
            info = rb.paths()
        except rb.BridgeError as e:
            QMessageBox.warning(self, "取不到目录", "服务没给出游戏目录：\n%s" % e)
            return
        game_root = info.get("gameRoot")
        if not game_root:
            QMessageBox.warning(self, "取不到目录",
                                "服务返回的 gameRoot 为空（插件版本过旧？）")
            return

        self._sync_asset()
        self._sync_inputs()
        data, _rep = self._generate()
        if data is None:
            return
        digest = hashlib.sha1(
            json.dumps(self.asset.to_dict(), sort_keys=True,
                       ensure_ascii=False).encode("utf-8")).hexdigest()
        logs = []
        try:
            old_item = mlab.get_material(game_root, self.asset.name)
        except Exception:  # noqa: BLE001
            old_item = None
        try:
            item = mlab.register(
                game_root, self.asset.name, data,
                asset_path=getattr(self, "_asset_path", "") or "",
                shading={"lighting_mode": self.asset.lighting_mode,
                         "shading_type": self.asset.shading_type},
                source_hash=digest, log=logs.append)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "写产物失败", str(e))
            return
        # 编译出新版 → 把所有引用旧产物的预览副本自动切过去（UE 式：编译即生效）
        auto = self._apply_compiled_material(game_root, old_item, item, logs)
        try:
            mlab.prune(game_root, dry_run=False, log=logs.append)
        except Exception as e:  # noqa: BLE001
            logs.append("清理旧产物失败（不影响本次结果）：%s" % e)

        prof = item.get("userMaterial") or []
        mat_name = item.get("name") or os.path.basename(item["rel"])
        rev = item.get("rev") or 1
        self.lbl_status.setStyleSheet("color:#1a7f37")
        self.lbl_status.setText("已编译材质 %s（第 %d 版，%dB，UserMaterial=%d）"
                                % (mat_name, rev, item["bytes"], len(prof)))
        auto_note = ("\n已自动把 %d 个预览中的工作副本切到新版本。" % len(auto)
                     if auto else "")
        QMessageBox.information(
            self, "编译成功",
            "材质：%s（第 %d 版）\n产物：%s\n大小：%d B\nUserMaterial：%d 个\n贴图槽：%d 个\n%s\n\n"
            "接下来在「实时预览」页把某个材质槽的母材质换成它。\n\n"
            "注意：新 mmtr 是全新的资源路径，游戏暂停（ESC 菜单）时不会加载它；\n"
            "若在暂停中切换母材质 / 副本，可能暂时显示默认方格，恢复游戏后才会生效。\n\n%s"
            % (mat_name, rev, item["rel"], item["bytes"], len(prof),
               len(item.get("textureSlots") or []), auto_note, "\n".join(logs)))

    def _rb_addrs_on(self, edit_rel):
        """当前挂载着某个工作副本的对象 ``[(addr, go)]``（来自 /holds）。"""
        try:
            hd = rb.holds().get("holdDetail") or []
        except rb.BridgeError:
            return []
        low = ps.norm_rel(edit_rel).lower()
        out = []
        for h in hd:
            if "ticks" in h:
                continue
            if ps.norm_rel(h.get("wantPath") or "").lower() == low:
                addr = h.get("addr")
                if addr:
                    out.append((addr, h.get("go")))
        return out

    def _rb_reread_current(self):
        """apply 后延迟**整表重读**（参数集可能变了：换母材质 / 编译新版本）。

        以前只拿旧的 _rb_mats 快照重画，新增/删除的参数根本不会出现，
        只能切到别的对象再切回来才好 —— 现在直接重新问引擎，并保留当前材质。
        """
        ent = self._rb_cur_ent()
        if ent is None:
            return
        self._rb_load_params(ent, keep_mat=getattr(self, "_rb_matname", None))

    def _apply_compiled_material(self, root, old_item, item, logs):
        """编译出新版母材质后，把所有引用旧产物的预览副本自动切过去。

        返回被更新的副本列表 ``[{mh, dh, name, old, new, rev, materials}]``
        （没有则空）。
        """
        old_rel = ps.norm_rel((old_item or {}).get("rel") or "")
        new_rel = ps.norm_rel(item.get("rel") or "")
        data = mlab.mmtr_from_item(root, item)
        if not (old_rel and new_rel and old_rel != new_rel and data):
            return []
        try:
            changed = mattr.repoint_edits(root, old_rel, new_rel, data,
                                          log=logs.append,
                                          material_name=item.get("name") or "")
        except Exception as e:  # noqa: BLE001
            logs.append("自动更新预览副本失败：%s" % e)
            return []
        if not changed:
            logs.append("没有预览中的材质引用旧母材质（下次勾选时生效）")
            return []
        logs.append("已更新 %d 个预览副本（参数槽位/母材质）" % len(changed))

        # 先更新登记（无论是否在线，下次勾选都要拿到新路径）
        doc = ps.load_index(root)
        for rec in changed:
            e = ps.get_mdf2(doc, rec["mh"], rec["dh"]) or {}
            # 只有原本就是「最近使用」的那份才把 edit 跟着挪；其余副本保持原指向
            was_cur = ps.norm_rel(e.get("edit") or "").lower() == rec["old"].lower()
            ps.upsert_copy(doc, rec["mh"], rec["dh"], rec["name"],
                           rec["new"], rec["rev"], current=was_cur)
        ps.save_index(root, doc, log=logs.append)

        if not self._rb_connected or not self._rb_link_ok:
            logs.append("已生成 %d 个新副本；当前未连接游戏，重新连接并勾选后生效"
                        % len(changed))
            return changed

        moved = 0
        failed_old = set()
        for rec in changed:
            for addr, go in self._rb_addrs_on(rec["old"]):
                try:
                    d = rb.material(go, "apply", mdf=rec["new"], addr=addr, keep=True)
                    ms = d.get("meshes") or []
                    if d.get("err") or not ms or not ms[0].get("ok"):
                        logs.append("切换失败 %s: %s" % (go, d.get("err") or "?"))
                        failed_old.add(rec["old"])
                    else:
                        moved += 1
                except rb.BridgeError as e:
                    logs.append("切换失败 %s: %s" % (go, e))
                    failed_old.add(rec["old"])
        # 旧副本已无任何 hold 引用才删（否则游戏下次重载会读不到文件）
        for rec in changed:
            if rec["old"] in failed_old:
                continue
            try:
                os.remove(ps.rel_to_abs(root, rec["old"]))
            except OSError:
                pass
        logs.append("已自动切换 %d 个对象到新母材质" % moved)
        if moved:
            QTimer.singleShot(600, self._rb_reread_current)
        return changed


class EngineResDialog(QDialog):
    """引擎资源选择器: 从允许清单挑 cbuffer/texture/sampler(声明即保活)。"""

    def __init__(self, parent=None, selected=(), locked=()):
        super().__init__(parent)
        self.setWindowTitle("添加引擎资源")
        self.resize(920, 560)
        locked_set = set(locked)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("从允许清单挑选引擎已有资源; 勾选后加入自定义输入(寄存器自动分配)。\n"
                           "带 [锁定] 标记的是预设输入/材质参数依赖的资源, 不可取消。"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["资源", "类型/定义", "参考寄存器(仅参考)", "说明"])
        self.tree.setColumnWidth(0, 230)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 520)
        for kind in ("cbuffer", "texture", "sampler"):
            root = QTreeWidgetItem([kind, "", "", ""])
            root.setFlags(root.flags() & ~Qt.ItemIsSelectable)
            for e in minp.by_kind(kind, include_std=False):
                if kind == "cbuffer":
                    extra = "%d 成员" % len(e.get("members", []))
                elif kind == "texture":
                    extra = "%s/%s" % (e.get("fmt"), e.get("dim"))
                else:
                    extra = "compare" if e.get("cmp") else ""
                desc = minp.desc_of(e["name"])
                is_lock = e["name"] in locked_set
                if is_lock:
                    desc = "[锁定: 由预设/参数依赖] %s" % desc
                it = QTreeWidgetItem([e["name"], extra, e.get("reg_ref", ""), desc])
                it.setToolTip(3, desc)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if (is_lock or e["name"] in selected)
                                 else Qt.Unchecked)
                if is_lock:
                    it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)   # 不可取消
                    it.setForeground(0, QColor("#b8791a"))
                root.addChild(it)
                # 引擎 cbuffer: 展开看成员(勾选前先看清定义)
                if kind == "cbuffer":
                    for m in (e.get("members") or []):
                        mn = m.get("name", "")
                        off = m.get("offset")
                        mc = QTreeWidgetItem([mn, m.get("type", ""),
                                              ("@%d" % off) if off is not None else "",
                                              minp.member_desc(e["name"], mn)])
                        it.addChild(mc)
            self.tree.addTopLevelItem(root)
        # 默认只展开到分类层; 资源项(及其 cbuffer 成员)默认折叠
        self.tree.expandToDepth(0)
        v.addWidget(self.tree, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def selected_names(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            for j in range(root.childCount()):
                c = root.child(j)
                if c.checkState(0) == Qt.Checked:
                    out.append(c.text(0))
        return out


class PresetDialog(QDialog):
    """语义输入选择器: **按分组列出、可单独勾选**每一项; 其依赖的引擎资源自动加入并锁定。

    分组(见目录的 `group`)仅用于界面归类显示; 添加单位始终是**单个语义输入项**(如 `camPos`),
    不提供"添加一整类"。
    """

    def __init__(self, parent=None, selected=(), stage="ps"):
        super().__init__(parent)
        self.setWindowTitle("添加预设输入")
        self.resize(900, 520)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("按分组挑选语义输入项(可单独添加); 勾选后其依赖的引擎资源会自动加入(并锁定)。\n"
                           "只列出当前 pass 阶段能供值的项(如 variantKind/jointOffset 仅顶点阶段可用)。"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["预设输入", "类型", "依赖的引擎资源", "说明"])
        for col, w in ((0, 190), (1, 90), (2, 200), (3, 380)):
            self.tree.setColumnWidth(col, w)
        sel = set(selected)
        for g in sinp.groups():
            _items = [e for e in g["items"] if sinp.available(e["name"], stage)]
            if not _items:
                continue
            gnode = QTreeWidgetItem([g["group"], "", "", ""])
            gnode.setFlags(gnode.flags() & ~Qt.ItemIsUserCheckable)
            for e in _items:
                f = e.get("field") or {}
                it = QTreeWidgetItem([e["name"], f.get("type", ""),
                                      ", ".join(sinp.deps_of(e["name"], stage)) or "(无)",
                                      e.get("desc", "")])
                it.setToolTip(3, e.get("desc", ""))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if e["name"] in sel else Qt.Unchecked)
                gnode.addChild(it)
            self.tree.addTopLevelItem(gnode)
        self.tree.expandAll()
        v.addWidget(self.tree, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def selected_names(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                c = g.child(j)
                if c.checkState(0) == Qt.Checked:
                    out.append(c.text(0))
        return out


class _CheckResultDialog(QDialog):
    """编译检查结果: 上=测试 shader(合成的调用者), 下=报错。方便排错。"""

    def __init__(self, parent, name, stage_label, harness, err):
        super().__init__(parent)
        self.setWindowTitle("编译检查: %s" % name)
        self.resize(920, 660)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("阶段: %s" % stage_label))
        sp = QSplitter(Qt.Vertical)
        self.ed_shader = QPlainTextEdit()
        self.ed_shader.setReadOnly(True)
        self.ed_shader.setPlainText(harness)
        sp.addWidget(self._pane("测试 shader (检查时实际编译的内容)", self.ed_shader))
        self.ed_err = QPlainTextEdit()
        self.ed_err.setReadOnly(True)
        self.ed_err.setPlainText(err or "(无报错)")
        sp.addWidget(self._pane("报错", self.ed_err))
        sp.setSizes([430, 200])
        v.addWidget(sp, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)

    @staticmethod
    def _pane(title, w):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel(title))
        v.addWidget(w, 1)
        return box


class FuncDialog(QDialog):
    """自定义函数选择器: 列出函数库(带 PS/VS 检查状态); 目标阶段不通过的禁用。"""

    def __init__(self, parent=None, selected=(), stage="ps"):
        super().__init__(parent)
        self.setWindowTitle("导入自定义函数")
        self.resize(780, 520)
        self._stage = stage
        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "从函数库挑选用到本 pass 的函数(无需声明, 导入后直接在材质源调用)。\n"
            "状态为 PS/VS 编译检查结果; 目标阶段不通过的会被禁用(请先到「函数库」页修复/检查)。"))
        bar = QHBoxLayout()
        self.btn_all = QPushButton("全体编译检查")
        self.btn_all.clicked.connect(self._check_all)
        bar.addWidget(self.btn_all)
        self.lbl = QLabel("")
        bar.addWidget(self.lbl)
        bar.addStretch(1)
        v.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["函数", "PS", "VS", "依赖", "说明"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(3, 170)
        v.addWidget(self.tree, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._fill(set(selected), {n: cfun.cached(n) for n in cfun.list_names()})

    def _fill(self, selected, status):
        self.tree.clear()
        for n in cfun.list_names():
            r = status.get(n)
            _ps = ("✓" if r and r["ps"]["ok"] else ("✗" if r else "?"))
            _vs = ("✓" if r and r["vs"]["ok"] else ("✗" if r else "?"))
            dep = sorted(cfun.function_calls(n))
            sig = cfun.parse_signature(cfun.get(n) or "")
            desc = ("%s %s(...)" % (sig["ret"], sig["name"])) if sig else ""
            it = QTreeWidgetItem([n, _ps, _vs, ", ".join(dep), desc])
            it.setData(0, Qt.UserRole, n)
            if r is not None and not r[self._stage]["ok"]:
                it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                it.setText(0, n + "   [本阶段不通过]")
                it.setForeground(0, QColor("#c0392b"))
            else:
                it.setCheckState(0, Qt.Checked if n in selected else Qt.Unchecked)
            self.tree.addTopLevelItem(it)

    def _check_all(self):
        self.lbl.setText("检查中…")
        QApplication.processEvents()
        _sel = self.selected_names()
        status = {n: cfun.check(n) for n in cfun.list_names()}
        self._fill(set(_sel), status)
        self.lbl.setText("已检查 %d 个" % len(status))

    def selected_names(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                out.append(it.data(0, Qt.UserRole))
        return out


_ERR_LINE_RE = re.compile(r"\((\d+),(\d+)(?:-\d+)?\):\s*(error|warning)\s+(\w+):\s*(.*)")


class FunctionLibPanel(QWidget):
    """自定义函数库页: 左列表(名 + PS/VS 状态) + 右编辑; 新建/删除/改名/保存/编译检查/全体检查。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = None
        self._loading = False
        h = QHBoxLayout(self)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("函数库 (每函数一文件; 纯函数)"))
        self.list = QListWidget()
        lv.addWidget(self.list, 1)
        bar = QHBoxLayout()
        for t, cb in (("新建", self.new_func), ("删除", self.del_func),
                      ("改名", self.rename_func)):
            b = QPushButton(t)
            b.clicked.connect(cb)
            bar.addWidget(b)
        bar.addStretch(1)
        lv.addLayout(bar)
        h.addWidget(left, 1)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("函数源 (只吃形参; 不得引用引擎资源/预设输入)"))
        self.ed = CodeEdit(indent=4)
        self._hl = HlslHighlighter(self.ed.document())
        rv.addWidget(self.ed, 1)
        rbar = QHBoxLayout()
        for t, cb in (("保存", self.save_func), ("编译检查", self.check_one),
                      ("全体编译检查", self.check_all)):
            b = QPushButton(t)
            b.clicked.connect(cb)
            rbar.addWidget(b)
        rbar.addStretch(1)
        self.lbl = QLabel("")
        rbar.addWidget(self.lbl)
        rv.addLayout(rbar)
        h.addWidget(right, 2)
        self.list.currentRowChanged.connect(self._on_sel)
        self.reload()

    def reload(self, keep=None):
        self._loading = True
        self.list.clear()
        names = cfun.list_names()
        for n in names:
            r = cfun.cached(n)
            if r is None:
                tag, col = "   [未确认]", None
            else:
                tag = "   [PS %s VS %s]" % ("✓" if r["ps"]["ok"] else "✗",
                                            "✓" if r["vs"]["ok"] else "✗")
                col = None if (r["ps"]["ok"] and r["vs"]["ok"]) else QColor("#b8791a")
            it = QListWidgetItem(n + tag)
            it.setData(Qt.UserRole, n)
            if col is not None:
                it.setForeground(col)
            self.list.addItem(it)
        self._loading = False
        if names:
            self.list.setCurrentRow(names.index(keep) if keep in names else 0)
        else:
            self._name = None
            self._set_src("")

    def _on_sel(self, row):
        if self._loading or row < 0:
            return
        self._name = self.list.item(row).data(Qt.UserRole)
        self._set_src(cfun.get(self._name) or "")
        self.lbl.setText("")

    def _set_src(self, text):
        self._loading = True
        self.ed.setPlainText(text)
        self._hl.set_diagnostics([])
        self._loading = False

    def new_func(self):
        name, ok = QInputDialog.getText(self, "新建函数", "函数名(即文件名; 撞名自动改):")
        if not ok or not name.strip():
            return
        base = re.sub(r"\W", "_", name.strip())
        actual = cfun.unique_name(base)
        cfun.save(actual, "float %s(float x)\n{\n    return x;\n}\n" % actual)
        self.reload(keep=actual)
        if actual != base:
            QMessageBox.warning(self, "已改名", "函数名冲突, 已自动改为: %s" % actual)

    def del_func(self):
        if not self._name:
            return
        if QMessageBox.question(self, "删除函数", "删除 %s ?" % self._name) != QMessageBox.Yes:
            return
        cfun.delete(self._name)
        self.reload()

    def rename_func(self):
        if not self._name:
            return
        new, ok = QInputDialog.getText(self, "改函数名", "新名:", text=self._name)
        if not ok or not new.strip() or new.strip() == self._name:
            return
        actual = cfun.rename(self._name, re.sub(r"\W", "_", new.strip()))
        self.reload(keep=actual)
        if actual != re.sub(r"\W", "_", new.strip()):
            QMessageBox.warning(self, "已改名", "函数名冲突, 已自动改为: %s" % actual)

    def save_func(self):
        if not self._name:
            return
        cfun.save(self._name, self.ed.toPlainText())
        self.check_one()          # 保存即检查

    def _show(self, r):
        self.lbl.setText("PS %s / VS %s" % ("✓" if r["ps"]["ok"] else "✗",
                                            "✓" if r["vs"]["ok"] else "✗"))
        ok = r["ps"]["ok"] and r["vs"]["ok"]
        self.lbl.setStyleSheet("color:#1a7f37" if ok else "color:#c0392b")
        self.lbl.setToolTip((r["ps"]["err"] or r["vs"]["err"] or "")[:2000])
        diags = []
        for line in (r["ps"]["err"] or r["vs"]["err"] or "").splitlines():
            m = _ERR_LINE_RE.search(line)
            if m:
                diags.append((int(m.group(1)), max(0, int(m.group(2)) - 1), 10 ** 6,
                              "%s %s: %s" % (m.group(3), m.group(4), m.group(5))))
        self._hl.set_diagnostics(diags)

    def _harness_for(self, name, target):
        """合成该阶段的测试 shader(依赖 + 函数 + 调用者)。"""
        src = cfun.get(name) or ""
        order = cfun.topo_order([name])
        deps = order[:-1]
        pre = "\n\n".join((cfun.get(d) or "").rstrip() for d in deps) if deps else ""
        hs, _unsup = cfun.build_harness(name, src, target, prelude=pre)
        return hs or ";; 无法解析函数签名(检查函数定义是否完整)"

    def check_one(self):
        if not self._name:
            return
        r = cfun.check(self._name)
        self.reload(keep=self._name)     # 先刷新列表(reload 会触发选中变化, 勿在此前设标签)
        self._show(r)
        if not (r["ps"]["ok"] and r["vs"]["ok"]):
            if not r["ps"]["ok"]:
                tgt, key, lbl = "ps_5_0", "ps", "PS (主/深度)"
            else:
                tgt, key, lbl = "vs_5_0", "vs", "VS (顶点)"
            _CheckResultDialog(self, self._name, lbl,
                               self._harness_for(self._name, tgt), r[key]["err"]).exec()

    def check_all(self):
        self.lbl.setText("检查中…")
        self.lbl.setStyleSheet("color:#b8791a")
        QApplication.processEvents()
        names = cfun.list_names()
        for n in names:
            cfun.check(n)
        self.reload(keep=self._name)
        self.lbl.setText("已检查 %d 个" % len(names))
        self.lbl.setStyleSheet("color:#1a7f37")


class MmtrTabs(QTabWidget):
    """MMTR/SDF 多文件容器: 每个打开的 mmtr/sdf 一个标签页。

    - 标签可关闭 / 可拖动重排; 右上角「新建 mmtr…」(从模板克隆) 与「打开 mmtr…」;
    - mmtr 无法真正从 0 新建(与 mdf2 不同), 故“新建”= 选一个现有 mmtr 当模板克隆;
    - 无文件时显示一个不可关闭的「(未打开)」占位页, 避免"无内容且无处可点"。
    """

    def __init__(self):
        super().__init__()
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self._on_close)
        box = QWidget()
        hb = QHBoxLayout(box)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(4)
        btn_open = QPushButton("打开 mmtr/SDF…")
        btn_open.setToolTip("打开一个 mmtr/SDF 文件(新标签页)")
        btn_open.clicked.connect(self.open_dialog)
        hb.addWidget(btn_open)
        self.setCornerWidget(box, Qt.TopRightCorner)
        self._placeholder = None
        self._ensure_placeholder()

    def open_path(self, path):
        """打开一个 mmtr 文件并新增标签页。"""
        self._drop_placeholder()
        panel = MmtrPanel(container=self)
        panel.load_path(path)
        return self._add_panel(panel)

    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "打开 mmtr/SDF", "",
            "mmtr/SDF (*.mmtr.* *.sdf.*);;mmtr (*.mmtr.*);;SDF (*.sdf.*);;所有文件 (*)")
        if p:
            self.open_path(p)

    def current_panel(self):
        w = self.currentWidget()
        return w if isinstance(w, MmtrPanel) else None

    def _add_panel(self, panel):
        panel.title_changed.connect(lambda _t, p=panel: self._on_title(p))
        idx = self.addTab(panel, panel.doc_title())
        self.setTabToolTip(idx, panel.path or panel.doc_title())
        self.setCurrentIndex(idx)
        return panel

    def _on_title(self, panel):
        i = self.indexOf(panel)
        if i >= 0:
            self.setTabText(i, panel.doc_title())
            self.setTabToolTip(i, panel.path or panel.doc_title())

    def _on_close(self, index):
        w = self.widget(index)
        self.removeTab(index)
        if w is not None:
            w.deleteLater()
        self._ensure_placeholder()      # 关掉最后一个 -> 回到占位页

    def _ensure_placeholder(self):
        if self._placeholder is not None or self.count() > 0:
            return
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(1)
        lab = QLabel("尚未打开 mmtr/SDF。\n点击右上角「打开 mmtr…」或「新建 mmtr…」"
                     "(从模板克隆)，或把 .mmtr/.sdf 文件拖进窗口。")
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("color:#888;")
        v.addWidget(lab)
        v.addStretch(1)
        self._placeholder = w
        idx = self.addTab(w, "(未打开)")
        self.tabBar().setTabButton(idx, QTabBar.RightSide, None)   # 占位页不可关闭
        self.setCurrentIndex(idx)

    def _drop_placeholder(self):
        if self._placeholder is None:
            return
        i = self.indexOf(self._placeholder)
        if i >= 0:
            self.removeTab(i)
        self._placeholder = None


class Mdf2Tabs(QTabWidget):
    """MDF2 多文件容器: 每个打开的文件一个标签页。

    - 标签可关闭 / 可拖动重排; 右上角「＋」新建空 mdf2;
    - 关闭最后一个标签页后自动补一个空文件(不进入"无标签"状态);
    - 启动默认开一个空文件(NewMDF2.mdf2.10)。
    """

    DEFAULT_TITLE = "NewMDF2.mdf2.10"

    def __init__(self):
        super().__init__()
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self._on_close)
        corner = QPushButton("＋")
        corner.setToolTip("新建空 mdf2")
        corner.setFixedSize(26, 22)
        corner.clicked.connect(self.new_tab)
        self.setCornerWidget(corner, Qt.TopRightCorner)
        self.new_tab()      # 启动默认开一个空文件

    def new_tab(self):
        """新增一个空 mdf2 标签页。"""
        panel = Mdf2Panel(container=self)
        panel.reset_new()
        return self._add_panel(panel)

    def open_path(self, path):
        """打开一个 mdf2 文件并新增标签页。"""
        panel = Mdf2Panel(container=self)
        panel.load_path(path)
        return self._add_panel(panel)

    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 mdf2", "", "mdf2 (*.mdf2.*);;所有文件 (*)")
        if p:
            self.open_path(p)

    def current_panel(self):
        w = self.currentWidget()
        return w if isinstance(w, Mdf2Panel) else None

    def _add_panel(self, panel):
        panel.title_changed.connect(lambda _t, p=panel: self._on_title(p))
        idx = self.addTab(panel, panel.doc_title())
        self.setTabToolTip(idx, panel.path or self.DEFAULT_TITLE)
        self.setCurrentIndex(idx)
        return panel

    def _on_title(self, panel):
        i = self.indexOf(panel)
        if i >= 0:
            self.setTabText(i, panel.doc_title())
            self.setTabToolTip(i, panel.path or panel.doc_title())

    def _on_close(self, index):
        w = self.widget(index)
        self.removeTab(index)
        if w is not None:
            w.deleteLater()
        if self.count() == 0:      # 关掉最后一个 -> 自动补一个空文件
            self.new_tab()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Material Studio — mmtr/sdf / mdf2")
        self.resize(1150, 720)
        tabs = QTabWidget()
        self.mmtr = MmtrTabs()
        self.mdf2 = Mdf2Tabs()
        self.msys = MaterialSystemPanel()
        self.funcs = FunctionLibPanel()
        tabs.addTab(self.mmtr, "MMTR/SDF")
        tabs.addTab(self.mdf2, "MDF2")
        tabs.addTab(self.msys, "材质系统")
        tabs.addTab(self.funcs, "函数库")
        self.tabs = tabs
        self.setCentralWidget(tabs)
        self.setAcceptDrops(True)

    def closeEvent(self, e):
        # 后台探活线程不给它拖住退出（ping 最长 1.2s）
        try:
            self.msys._rb_pool.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)

    def open_file(self, path):
        if ".mdf2." in path.lower():
            self.mdf2.open_path(path)
        else:
            self.mmtr.open_path(path)

    # ---- 拖拽导入(支持多文件): mmtr -> MMTR 页, mdf2 -> MDF2 页(每文件一个标签页) ----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        mmtr = [p for p in paths if ".mmtr." in p.lower() or ".sdf." in p.lower()]
        mdf2 = [p for p in paths if ".mdf2." in p.lower()]
        if mmtr:
            for p in mmtr:
                self.mmtr.open_path(p)
            self.tabs.setCurrentIndex(0)
        if mdf2:
            for p in mdf2:
                self.mdf2.open_path(p)
            if not mmtr:
                self.tabs.setCurrentIndex(1)
        if mmtr or mdf2:
            e.acceptProposedAction()


def main(argv):
    app = QApplication(argv)
    win = MainWindow()

    rest = list(argv[1:])
    shot = None
    pane = None
    for opt in ("--shot", "--pane"):
        if opt in rest:
            i = rest.index(opt)
            val = rest[i + 1] if i + 1 < len(rest) else None
            del rest[i:i + 2]
            if opt == "--shot":
                shot = val
            else:
                pane = val
    selftest = "--selftest" in rest
    if selftest:
        rest.remove("--selftest")
    args = [a for a in rest if not a.startswith("--")]

    if selftest:
        if args:
            win.open_file(args[0])
            mp = win.mmtr.current_panel()
            mp.tree_blob.setCurrentItem(mp.tree_blob.topLevelItem(33))
            mp.refresh_detail()
            print("blobs:", mp.tree_blob.topLevelItemCount())
            print("UserMaterial 参数:", mp.tree_param.topLevelItemCount())
            print("资源名池:", ["%s=%d" % (mp.tree_pool.topLevelItem(i).text(0),
                                       mp.tree_pool.topLevelItem(i).childCount())
                                 for i in range(mp.tree_pool.topLevelItemCount())])
            print("变体页 pass 组:", [mp.tree_variant.topLevelItem(i).text(0)
                                     for i in range(mp.tree_variant.topLevelItemCount())])
            for i in range(mp.tree_grp.topLevelItemCount()):
                top = mp.tree_grp.topLevelItem(i)
                print("  ", top.text(0), "|", top.text(2))
        print("selftest OK")
        return 0

    if args:
        win.open_file(args[0])
    win.resize(1150, 720)
    if shot:
        is_mdf2 = bool(args) and ".mdf2." in args[0].lower()
        if is_mdf2:
            win.tabs.setCurrentIndex(1)
            panel = win.mdf2.current_panel()
        else:
            panel = win.mmtr.current_panel()
            if panel is not None and panel.tree_blob.topLevelItemCount() > 33:
                panel.tree_blob.setCurrentItem(panel.tree_blob.topLevelItem(33))
        if panel is not None:
            if isinstance(panel, Mdf2Panel):
                idx = {"param": 1, "props": 2}.get(pane, 0)
            else:
                idx = {"group": 1, "pool": 2, "param": 3, "variant": 4, "blob": 5}.get(pane, 0)
            panel.tabs.setCurrentIndex(idx)
        win.show()
        for _ in range(3):
            app.processEvents()
        win.grab().save(shot)
        print("shot saved:", shot)
        return 0
    win.show()
    return app.exec()


# ---------------------------------------------------------------- 材质属性对话框

# 常用 flags 的中文说明（悬停提示）。没列的只有英文名 + bit 号。
_FLAG_TIPS = {
    "BaseTwoSideEnable": "双面渲染（基础位）：背面也绘制",
    "BaseAlphaTestEnable": "alpha 测试（基础位）：低于阈值的像素丢弃（镂空）",
    "ShadowCastDisable": "关闭阴影投射（半透明材质常用）",
    "VertexShaderUsed": "使用材质自定义顶点着色器（需 mmtr 有对应 VS 变体）",
    "EmissiveUsed": "使用自发光",
    "TessellationEnable": "启用细分（配合 TessFactor）",
    "EnableIgnoreDepth": "忽略深度测试",
    "AlphaMaskUsed": "使用 alpha 遮罩（DMC5 的 Toon 材质全部开着）",
    "ForcedTwoSideEnable": "强制双面（不论其它条件）",
    "TwoSideEnable": "双面渲染",
    "TransparentZPostPassEnable": "透明材质参与 Z 后处理 pass",
    "TessFactor": "细分因子（5 位）",
    "PhongFactor": "Phong 高光强度（8 位）",
    "RoughTransparentEnable": "粗糙半透明",
    "ForcedAlphaTestEnable": "强制 alpha 测试",
    "AlphaTestEnable": "alpha 测试（镂空）",
    "SSSProfileUsed": "使用 SSS 预设（需额外参数）",
    "EnableStencilPriority": "启用模板优先级",
    "RequireDualQuaternion": "要求双四元数蒙皮",
    "PixelDepthOffsetUsed": "使用像素深度偏移",
    "NoRayTracing": "不参与光追",
}


class CopyPickerDialog(QDialog):
    """副本选择器：按原 mdf2 分组 + 过滤。

    一个 mesh 可能用到几十个原 mdf2（变体 / 共用模型 / 污浊 / 换色 / 残影 /
    过场专用材质…），扁平列表会太长，所以按原 mdf2 分组；pick 模式每组末尾
    带「＋ 新建副本…」。选中子项通过 ``choice`` 返回：
        ("copy", dh, 副本 dict) / ("new", dh, None)
    """

    def __init__(self, parent, groups, current=("", ""), mode="pick",
                 title="选择副本", prompt=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(840, 580)
        self._mode = mode
        self.choice = None
        self.action = None

        v = QVBoxLayout(self)
        if prompt:
            lab = QLabel(prompt)
            lab.setWordWrap(True)
            v.addWidget(lab)
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("过滤：原 mdf2 路径 / 副本名（输入即筛选）")
        v.addWidget(self.ed_filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["原 mdf2 / 副本", "标记"])
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(1, 320)
        self.tree.setAlternatingRowColors(True)
        v.addWidget(self.tree, 1)

        for dh, base, copies in groups:
            g = QTreeWidgetItem(["%s  (%d)" % (os.path.basename(base) or dh,
                                                len(copies)), base])
            g.setToolTip(1, base)
            g.setFlags(g.flags() & ~Qt.ItemIsSelectable)
            g.setData(0, Qt.UserRole, ("group", dh, None))
            for c in copies:
                marks = []
                if c.get("_in_use"):
                    marks.append("使用中")
                if (dh, c["name"]) == current:
                    marks.append("← 当前")
                it = QTreeWidgetItem(["%s（v%d）" % (c["name"], c["rev"]),
                                      " · ".join(marks)])
                it.setToolTip(0, c["rel"])
                it.setData(0, Qt.UserRole, ("copy", dh, c))
                g.addChild(it)
            if mode == "pick":
                add = QTreeWidgetItem(["＋ 新建副本…", ""])
                add.setData(0, Qt.UserRole, ("new", dh, None))
                g.addChild(add)
            self.tree.addTopLevelItem(g)
            # 默认全部折叠、不预选：用户要哪份就展开哪组自己点
            g.setExpanded(False)

        if mode == "manage":
            self.bb = QDialogButtonBox(QDialogButtonBox.Close)
            self.bb.button(QDialogButtonBox.Close).setText("关闭")
            self.btn_rename = self.bb.addButton("重命名…", QDialogButtonBox.ActionRole)
            self.btn_delete = self.bb.addButton("删除…", QDialogButtonBox.ActionRole)
            self.btn_rename.clicked.connect(lambda: self._act("rename"))
            self.btn_delete.clicked.connect(lambda: self._act("delete"))
            self.bb.rejected.connect(self.reject)
        else:
            self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            self.bb.button(QDialogButtonBox.Ok).setText("确定")
            self.bb.button(QDialogButtonBox.Cancel).setText("取消")
            self.bb.accepted.connect(self.accept)
            self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)

        self.ed_filter.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(self._dbl)
        self.tree.currentItemChanged.connect(lambda *_: self._sync_buttons())
        self._sync_buttons()

    def _first_visible_copy(self):
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            if g.isHidden():
                continue
            for j in range(g.childCount()):
                c = g.child(j)
                if c.isHidden():
                    continue
                if c.data(0, Qt.UserRole)[0] == "copy":
                    return c
        return None

    def _selected(self):
        it = self.tree.currentItem()
        if it is None or it.isHidden():
            return None
        d = it.data(0, Qt.UserRole)
        return d if d and d[0] != "group" else None

    def _sync_buttons(self):
        d = self._selected()
        if self._mode == "manage":
            usable = bool(d and d[0] == "copy" and not d[2].get("_in_use"))
            self.btn_rename.setEnabled(usable)
            self.btn_delete.setEnabled(usable)
        else:
            self.bb.button(QDialogButtonBox.Ok).setEnabled(d is not None)

    def _act(self, kind):
        d = self._selected()
        if not d or d[0] != "copy" or d[2].get("_in_use"):
            return
        self.action = (kind, d[1], d[2])
        super().accept()

    def _dbl(self, item, _col):
        if self._mode != "pick":
            return
        d = item.data(0, Qt.UserRole)
        if d and d[0] != "group":
            self.accept()

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            ghit = bool(t) and t in (g.text(0) + " " + g.text(1)).lower()
            shown = 0
            for j in range(g.childCount()):
                c = g.child(j)
                hit = (not t) or ghit or (t in c.text(0).lower())
                c.setHidden(not hit)
                if hit:
                    shown += 1
            g.setHidden(shown == 0)
            if t and shown:
                g.setExpanded(True)
        cur = self.tree.currentItem()
        if cur is not None and cur.isHidden():
            self.tree.setCurrentItem(self._first_visible_copy())
        self._sync_buttons()

    def accept(self):
        if self._mode != "pick":
            return
        d = self._selected()
        if d is None:
            return
        self.choice = d
        super().accept()


class TexturePickerDialog(QDialog):
    """贴图选择器：只列「原 mdf2 自带」和「工具导入管理」的贴图。

    sources = [(标签, [逻辑路径…]), …]，第一个为默认来源。
    不扫磁盘、不读 pak 文件表：那些路径可能属于别的 mod，卸载后就缺失了，
    也不好确认状态。
    """

    def __init__(self, parent, sources, current=""):
        super().__init__(parent)
        self.setWindowTitle("选择贴图")
        self.resize(900, 600)
        self._sources = [(label, list(items or []))
                         for label, items in (sources or [])]
        self._all = []
        self._info = ""
        self._limit = 2000

        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("来源:"))
        self.cmb_src = NoWheelComboBox()
        self.cmb_src.addItems([label for label, _ in self._sources] or ["（无）"])
        row.addWidget(self.cmb_src)
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("过滤：路径/文件名子串（输入即筛选）")
        row.addWidget(self.ed_filter, 1)
        v.addLayout(row)
        self.lbl = QLabel("")
        v.addWidget(self.lbl)
        self.lst = QListWidget()
        self.lst.setAlternatingRowColors(True)
        v.addWidget(self.lst, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.cmb_src.currentIndexChanged.connect(lambda *_: self._load_source())
        self.ed_filter.textChanged.connect(self._fill)
        self.lst.itemDoubleClicked.connect(lambda *_: self.accept())
        self._load_source()
        if current:
            self.ed_filter.setText(os.path.basename(current))
            items = self.lst.findItems(current, Qt.MatchExactly)
            if items:
                self.lst.setCurrentItem(items[0])
                self.lst.scrollToItem(items[0])

    def _load_source(self):
        i = self.cmb_src.currentIndex()
        label, items = (self._sources[i] if 0 <= i < len(self._sources) else ("", []))
        self._all = list(items)
        self._info = "%s：%d 条" % (label, len(self._all))
        self._fill()

    def _fill(self):
        q = self.ed_filter.text().strip().lower()
        items = [p for p in self._all if q in p.lower()] if q else self._all
        self.lst.clear()
        for p in items[:self._limit]:
            self.lst.addItem(p)
        extra = (" · 显示前 %d / 共 %d（继续输入过滤）" % (self._limit, len(items))
                 if len(items) > self._limit else "")
        self.lbl.setText(self._info + (" · 匹配 %d" % len(items) if q else "") + extra)

    def chosen(self):
        it = self.lst.currentItem()
        return it.text() if it else ""


class TextureEditDialog(QDialog):
    """当前材质的贴图槽编辑（只改 path，不改槽名）。

    "选择…" = 原 mdf2 自带的贴图 / 工具导入管理的贴图；外部文件用 "导入…"
    （复制到 materialTemp/tex，按内容 hash 命名）。应用结果由调用方
    用 /texture set 热替换；本对话框只收集路径改动。
    """

    def __init__(self, parent, matname, slots, root, mh, dh, sources=None):
        super().__init__(parent)
        self.setWindowTitle("贴图编辑 - %s" % matname)
        self.resize(880, 480)
        self._root = root
        self._mh, self._dh = mh, dh
        self._sources = sources or []
        self._rows = []

        v = QVBoxLayout(self)
        tip = QLabel("材质：%s\n只改路径；槽名（= mmtr SRV 名）不可改。"
                     "应用后先改内存，点「保存到 mdf2」才持久化；"
                     "「选择…」= 原 mdf2 自带的贴图 / 工具导入管理的贴图，"
                     "外部文件用「导入…」复制进缓存。" % matname)
        tip.setWordWrap(True)
        v.addWidget(tip)

        self.tbl = QTableWidget(len(slots), 3)
        self.tbl.setHorizontalHeaderLabels(["槽位", "路径", "操作"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.tbl.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        self.tbl.setColumnWidth(0, 150)
        self.tbl.setColumnWidth(2, 190)

        for r, s in enumerate(slots):
            name = s.get("name") or "?"
            it = QTableWidgetItem("%s  (#%s)" % (name, s.get("slot")))
            it.setToolTip(name)
            self.tbl.setItem(r, 0, it)
            le = QLineEdit(s.get("path") or "")
            le.setPlaceholderText("相对 natives/%s 的 .tex 路径" % ps.NATIVES_SUBDIR)
            self.tbl.setCellWidget(r, 1, le)
            ops = QWidget()
            h = QHBoxLayout(ops)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(2)
            b_pick = QPushButton("选择…")
            b_imp = QPushButton("导入…")
            b_rst = QPushButton("还原")
            for b in (b_pick, b_imp, b_rst):
                h.addWidget(b)
            self.tbl.setCellWidget(r, 2, ops)
            self._rows.append((s, le))
            b_pick.clicked.connect(lambda _=False, rr=r: self._pick(rr))
            b_imp.clicked.connect(lambda _=False, rr=r: self._import(rr))
            b_rst.clicked.connect(lambda _=False, rr=r: self._restore(rr))

        v.addWidget(self.tbl, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("应用")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _pick(self, r):
        s, le = self._rows[r]
        dlg = TexturePickerDialog(self, self._sources, le.text())
        if dlg.exec() == QDialog.Accepted:
            p = dlg.chosen()
            if p:
                le.setText(p)

    def _import(self, r):
        _s, le = self._rows[r]
        p, _ = QFileDialog.getOpenFileName(
            self, "导入 .tex（复制到 materialTemp/tex）", "",
            "RE 贴图 (*.tex *.tex.*);;所有文件 (*)")
        if not p:
            return
        base = os.path.basename(p)
        i = base.lower().find(".tex")
        prefill = (base[:i] if i >= 0 else base)
        prefill = "".join(c for c in prefill if 32 <= ord(c) <= 126).strip()[:48]
        name, ok = QInputDialog.getText(
            self, "导入贴图",
            "名称（同一名称 = 同一张贴图；重名会生成新版本）：", text=prefill)
        if not ok:
            return
        try:
            rel = ps.import_texture(self._root, p, self._mh, self._dh,
                                    name.strip())
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(ex))
            return
        le.setText(rel)

    def _restore(self, r):
        s, le = self._rows[r]
        le.setText(s.get("path") or "")

    def changes(self):
        out = {}
        for s, le in self._rows:
            p = ps.norm_rel(le.text())
            if p and p != (s.get("path") or ""):
                out[s.get("name")] = p
        return out


class MaterialAttrsDialog(QDialog):
    """材质属性：母材质 + flags。

    只负责收集用户改动（``changes()``）；写盘、切副本、重载由
    ``_rb_edit_attrs`` 负责。
    """

    def __init__(self, parent, mat_name, cur, options, original=""):
        super().__init__(parent)
        self.setWindowTitle("材质属性 - %s" % mat_name)
        self.resize(660, 580)
        self._cur = cur
        self._w = {}

        v = QVBoxLayout(self)

        head = QLabel("材质：<b>%s</b>　　当前 flags = 0x%08X"
                      % (mat_name, cur.get("flags_raw") or 0))
        head.setTextFormat(Qt.RichText)
        v.addWidget(head)

        cur_mmtr = ps.norm_rel(cur.get("mmtr") or "")
        self._cur_mmtr = cur_mmtr.lower()
        self._original = ps.norm_rel(original or "")
        resolved = (cur.get("resolved_name") or "").strip()
        resolved_low = resolved.lower()
        row = QHBoxLayout()
        row.addWidget(QLabel("母材质:"))
        self.cmb = NoWheelComboBox()
        cur_idx = -1
        if original:
            self.cmb.addItem("原始母材质：%s" % original, "__orig__")
            if self._original.lower() == self._cur_mmtr:
                cur_idx = self.cmb.count() - 1
        for o in options:
            same = ps.norm_rel(o["rel"]).lower() == self._cur_mmtr
            match_old = bool(resolved_low) and (o.get("name") or "").lower() == resolved_low
            label = "%s（第 %s 版 · %s 参数）" % (
                o["name"], o.get("rev") or 1, o.get("nParams") or 0)
            if same:
                label += "  ← 当前"
            elif match_old:
                label += "  ← 当前（旧版）"
            self.cmb.addItem(label, o["rel"])
            if same:
                cur_idx = self.cmb.count() - 1
            elif match_old and cur_idx < 0:
                cur_idx = self.cmb.count() - 1
        if cur_idx < 0:
            # 当前值不在选项里（旧产物已删 / 手改的路径）→ 补一项，只用于显示
            shown = resolved or cur_mmtr or "(空)"
            self.cmb.addItem("当前（未登记）：%s" % shown, cur_mmtr)
            cur_idx = self.cmb.count() - 1
        self.cmb.setCurrentIndex(cur_idx)
        self.cmb.setToolTip(
            "打开时选中 mdf2 里实际在用的那一项。\n"
            "原始母材质 = 原始 mdf2 里该材质指向的那份（从 orig 副本 / 原文件读）。\n"
            "其余是工具「编译到预览目录」产出的材质（每个材质名一项）。\n"
            "选新的一项 = 换母材质，按它的 UserMaterial 重建参数集。")
        row.addWidget(self.cmb, 1)
        v.addLayout(row)
        if not options:
            tip = QLabel("母材质库为空 —— 先到「材质系统」页点「编译到预览目录」。")
            tip.setStyleSheet("color:#b00020")
            tip.setWordWrap(True)
            v.addWidget(tip)

        box = QGroupBox("flags（渲染路径开关；不改 shader 变体，只改管线状态）")
        g = QGridLayout(box)
        g.setVerticalSpacing(4)
        col = rowi = 0
        for name, width, bit in mdf2lib.FLAG_BIT_OFFSETS:
            cur_v = cur["flags"].get(name)
            if width == 1:
                w = QCheckBox("%s  (bit %d)" % (name, bit))
                w.setChecked(bool(cur_v))
                w.toggled.connect(self._refresh_flags_label)
            else:
                w = QSpinBox()
                w.setRange(0, (1 << width) - 1)
                w.setValue(int(cur_v or 0))
                w.setPrefix("%s(%d位) " % (name, width))
                w.valueChanged.connect(self._refresh_flags_label)
            tip = _FLAG_TIPS.get(name)
            if tip:
                w.setToolTip(tip)
            self._w[name] = w
            g.addWidget(w, rowi, col)
            col += 1
            if col >= 2:
                col = 0
                rowi += 1
        v.addWidget(box)

        self.lbl_new = QLabel("")
        self.lbl_new.setStyleSheet("color:#1a7f37")
        v.addWidget(self.lbl_new)
        self._refresh_flags_label()

        hint = QLabel(
            "• 换母材质会按新材质的 UserMaterial 重建参数集：同名参数保留值、"
            "新增参数默认 0、旧独有参数丢弃。\n"
            "• 选「原始母材质」= 从原始 mdf2 恢复该材质的母材质与参数集。\n"
            "• 应用后生成**新路径**副本并让所有关联对象重载"
            "（引擎按路径缓存，同路径改了不生效），画面会短暂闪一下。\n"
            "• 新增参数默认 0 —— 例如 Alpha=0 会让材质变透明，应用后请到参数表里调。\n"
            "• 若新母材质缺少某个 flags 所需的 shader 变体，可能显示异常；"
            "取消勾选该项即可排查。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666")
        v.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("应用")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _preview_flags(self):
        d = {}
        for name, w in self._w.items():
            d[name] = w.isChecked() if isinstance(w, QCheckBox) else w.value()
        return mdf2lib.encode_material_flags(d) & 0xFFFFFFFF

    def _refresh_flags_label(self):
        lbl = getattr(self, "lbl_new", None)
        if lbl is None:
            return
        lbl.setText("应用后 flags = 0x%08X（当前 0x%08X）"
                    % (self._preview_flags(), self._cur.get("flags_raw") or 0))

    def changes(self):
        """返回改动：``{"mmtr": 逻辑路径/__orig__, "flags": {名字: 值}?}``（没改的键不出现）。"""
        out = {}
        sel = self.cmb.currentData()
        if sel == "__orig__":
            if self._original.lower() != self._cur_mmtr:
                out["mmtr"] = sel
        elif sel and ps.norm_rel(sel).lower() != self._cur_mmtr:
            out["mmtr"] = sel
        fl = {}
        for name, w in self._w.items():
            v = w.isChecked() if isinstance(w, QCheckBox) else w.value()
            if v != self._cur["flags"].get(name):
                fl[name] = v
        if fl:
            out["flags"] = fl
        return out


if __name__ == "__main__":
    sys.exit(main(sys.argv))
