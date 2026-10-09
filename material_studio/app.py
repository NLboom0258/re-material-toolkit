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
  右=「材质源(HLSL)」(带 HLSL 高亮 + 编译报错红线) / 「组装结果」 / 「输入」(pass 输入·系统预制输入·自定义输入(可编辑)) / 「语义输出·校验」。
交互: 常用按钮 + 对选中项**右键菜单**; 名称列**双击内联改名**(预选原名); 支持**拖拽文件**导入。
MDF2 参数: 类型列为常驻下拉; 值列按分量拆分输入框, float3/float4 额外带**颜色块**(点击取色)。

用法: python app.py [<file.mmtr.*> | <file.mdf2.*>]
自检: python app.py --selftest <file.mmtr.*>                 (offscreen 构建并打印)
截图: python app.py --shot <out.png> [--pane bind|param] <file>   (渲染截图, 调试用)
"""
import os
import re
import sys
from collections import Counter, namedtuple

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
from material_toolkit.lib import material_iface as miface  # noqa: E402

from PySide6.QtCore import (  # noqa: E402
    Qt, QTimer, QSize, Signal, QRegularExpression, QObject, QRunnable, QThreadPool,
)
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPalette,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument,
)
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QColorDialog,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox,
    QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTabBar, QTabWidget, QTextEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

# D3D_SRV_DIMENSION 名称(用于 RDEF 资源的维度显示)。
_DIMNAME = {0: "?", 1: "buffer", 2: "tex1d", 3: "tex2d", 4: "tex2dms", 5: "tex3d",
            6: "texcube", 7: "tex1darr", 8: "tex2darr", 9: "tex2dmsarr",
            10: "texcubearr", 11: "bufferex"}


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
    "float4x4", "half", "half2", "half3", "half4", "double", "int",
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
    """按 cb_offset/顺序重算材质各参数的 data_offset(与 save 的口径一致)。

    用于让“新参数”的 offset 显示正确(不再是一开始的 0), 且与实际导出一致。
    """
    off = 0
    for pr in mat.properties:
        pr.param_count = len(pr.values)
        if pr.cb_offset is not None:
            pr.data_offset = pr.cb_offset
            off = max(off, pr.cb_offset + pr.param_count * 4)
        else:
            pr.data_offset = off
            off += pr.param_count * 4
    mat.prop_block_size = off


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
        self.tree_param = QTreeWidget()
        self.tree_param.setHeaderLabels(["参数名", "类型", "大小", "offset"])
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
        p, _ = QFileDialog.getSaveFileName(self, "导出 mmtr", self.path or "out.mmtr.1808168797")
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
            self.tree_param.addTopLevelItem(
                QTreeWidgetItem([pr.name, type_label(pr.size), f"{pr.size}B",
                                 f"0x{pr.offset:04x}"]))
        fit_columns(self.tree_param, [0, 1, 2, 3], pad=24, min_w=80, max_w=320)

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
            acts.append(("复制行",
                         lambda: _copy_to_clipboard(
                             " | ".join(item.text(c) for c in range(4)))))
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
        self.tree_param = QTreeWidget()
        self.tree_param.setHeaderLabels(["参数名", "类型", "值", "offset"])
        # 参数名双击内联改名(预选原名); 类型/值列为常驻控件
        self.tree_param.setItemDelegate(
            InlineNameDelegate(self.tree_param, 0, self._param_name,
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
        # 类型列宽度按"最长的类型名"算(如 float4x4), 避免固定宽度截断长名
        fm = self.tree_param.fontMetrics()
        type_w = max(fm.horizontalAdvance(n) for n in names) + 36   # 文字 + 下拉箭头/内边距
        for pr in m.properties:
            it = QTreeWidgetItem([pr.name, "", "", f"0x{pr.data_offset:04x}"])
            it.setData(0, Qt.UserRole, ("param", pr.name))
            it.setFlags(it.flags() | Qt.ItemIsEditable)   # 参数名可双击内联改名
            self.tree_param.addTopLevelItem(it)
            # 类型列: 常驻下拉(改类型 = 改值个数)
            cb = NoWheelComboBox()
            cb.addItems(names)
            cb.setMinimumWidth(type_w)
            if pr.type in names:
                cb.setCurrentText(pr.type)
            cb.currentTextChanged.connect(
                lambda t, nm=pr.name: self._defer_set_type(nm, t))
            self.tree_param.setItemWidget(it, 1, cb)
            # 值列: 类型化控件(分量输入框; float3/4 带颜色块)
            self.tree_param.setItemWidget(it, 2, ValueEditor(pr))
        fit_columns(self.tree_param, [0, 3], pad=24, min_w=90, max_w=420)
        self.tree_param.setColumnWidth(1, type_w + 12)
        self.tree_param.setColumnWidth(2, 360)

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
        p, _ = QFileDialog.getSaveFileName(self, "导出 mdf2", self.path or "out.mdf2.10")
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
            acts = [("重命名", lambda: self.tree_param.editItem(item, 0)),
                    ("改类型", lambda: self.set_param_type(d[1])),
                    ("删除参数", lambda: self.delete_param(d[1])),
                    ("新增参数", self.add_param),
                    ("复制行",
                     lambda: _copy_to_clipboard(
                         " | ".join(item.text(c) for c in range(4))))]
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
        self.refresh_info()

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
                                                      extra=self.asset.shading_extra)
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
        _w1, self.tree_inputs = self._inputs_tree()
        _w2, self.tree_inputs_depth = self._inputs_tree()
        _w3, self.tree_inputs_vs = self._inputs_tree(with_force=True)
        page, self._in_btns, self._in_stack = _seg_switch(
            [("主 pass", _w1), ("深度 pass", _w2), ("顶点 (VS)", _w3)])
        return page

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

    def _inputs_tree(self, with_force=False):
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
        tree = QTreeWidget()
        tree.setHeaderLabels(["类别 / 名", "类型", "说明"])
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

    def _locked_names(self, pass_name="main"):
        """被锁定(禁止删除)的引擎资源名 -> [来源...]: pass 依赖 + 预设依赖 + 参数所需 UserMaterial。"""
        _pre = mimp.presets(self.asset.inputs, pass_name)
        res = sinp.resolve(_pre, sinp.names_in(self._base_iface(pass_name)))
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
        _own = mimp.presets(self.asset.inputs, _pass)
        d = PresetDialog(self, selected=set(_own))
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
        # 1) 插值输入(pass 依赖; 非资源; 只读展示)
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
        # 2) 自定义输入(该 pass: 预设/参数/贴图/引擎资源; 会写进 mmtr)
        presets = list(_presets)
        params = mimp.params(self.asset.inputs)
        textures = mimp.textures(self.asset.inputs, pass_name)
        engine = mimp.engine(self.asset.inputs, pass_name)
        # pass 结构依赖的引擎资源: 系统自动添加 + 锁定(只读展示; 不出现在资产)
        for nm in minp.pass_dep_names(pass_name):
            if nm != "UserMaterial" and nm not in engine:
                engine.append(nm)
        # 有材质参数的 pass: UserMaterial 由系统自动供给 + 锁定
        if params and "UserMaterial" not in engine:
            engine.append("UserMaterial")
        lock = self._locked_names(pass_name)
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
                deps = e.get("depends") or []
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
        cust.addChild(pnode)
        for n, t in params:
            # 此处显示/编辑的是**裸名**(= mdf2 / mmtr 参数表名); RDEF 名(带 `VAR_`) 只在
            # UserMaterial 处展示 —— 避免误改到自动生成的 `VAR_` 前缀。
            base = mgen.param_base_name(n)
            rdef_hint = ("RDEF 成员: %s" % n) if n.startswith(mgen.PARAM_PREFIX) else ""
            it = QTreeWidgetItem([base, t, rdef_hint])
            it.setData(0, Qt.UserRole, ("param", n))
            pnode.addChild(it)
        tnode = QTreeWidgetItem(["贴图 (//! tex)", "", "%d" % len(textures)])
        cust.addChild(tnode)
        for n in textures:
            it = QTreeWidgetItem([n, "", ""])
            it.setData(0, Qt.UserRole, ("tex", n))
            tnode.addChild(it)
        enode = QTreeWidgetItem(["引擎资源 (//! engine)", "", "%d" % len(engine)])
        cust.addChild(enode)
        for n in engine:
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
            it = QTreeWidgetItem([n, kd, desc])
            it.setToolTip(2, ("%s\n%s" % (extra, desc)).strip())
            if n in lock:
                it.setForeground(0, QColor("#b8791a"))
            it.setData(0, Qt.UserRole, ("engine", n))
            enode.addChild(it)
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
        try:
            src = mpass.default_material(tmpl)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "载入失败", str(e))
            return
        self.ed_src.setPlainText(src)
        self.asset.shading_source = src
        self.asset.inputs = mimp.empty()
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
        self._sync_inputs()
        self._apply_asset()
        self.lbl_status.setText("已打开 %s" % os.path.basename(path))

    def save_asset(self):
        self._sync_asset()
        self._sync_inputs()
        path, _ = QFileDialog.getSaveFileName(self, "保存材质资产",
                                              self.asset.name + ".mmat.json",
                                              "材质资产 (*.mmat.json);;All (*)")
        if not path:
            return
        try:
            self.asset.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
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
                                   extra=self.asset.shading_extra)
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
        path, _ = QFileDialog.getSaveFileName(self, "生成 mmtr",
                                              self.asset.name + ".mmtr.1808168797",
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
        path, _ = QFileDialog.getSaveFileName(self, "导出材质实例",
                                              self.asset.name + ".mdf2.10",
                                              "mdf2 (*.mdf2.10);;All (*)")
        if not path:
            return
        mpath = (self.asset.template.get("mmtr_path")
                 or "MasterMaterial/Master/%s.mmtr" % self.asset.name)
        try:
            m = minst.from_mmtr(data, mpath, self.asset.name, shading_type="Standard")
            n = m.save(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))
            return
        self.lbl_status.setText("已导出材质实例 %s (%dB) master=%s"
                                % (os.path.basename(path), n, mpath))
        QMessageBox.information(self, "导出成功",
                                "-> %s (%d B)\nmaster=%s" % (path, n, mpath))


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

    def __init__(self, parent=None, selected=()):
        super().__init__(parent)
        self.setWindowTitle("添加预设输入")
        self.resize(900, 520)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("按分组挑选语义输入项(可单独添加); 勾选后其依赖的引擎资源会自动加入(并锁定)。"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["预设输入", "类型", "依赖的引擎资源", "说明"])
        for col, w in ((0, 190), (1, 90), (2, 200), (3, 380)):
            self.tree.setColumnWidth(col, w)
        sel = set(selected)
        for g in sinp.groups():
            gnode = QTreeWidgetItem([g["group"], "", "", ""])
            gnode.setFlags(gnode.flags() & ~Qt.ItemIsUserCheckable)
            for e in g["items"]:
                f = e.get("field") or {}
                it = QTreeWidgetItem([e["name"], f.get("type", ""),
                                      ", ".join(e.get("depends") or []) or "(无)",
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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
