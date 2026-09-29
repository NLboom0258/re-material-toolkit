#!/usr/bin/env python3
"""Material Studio — RE Engine 材质(mmtr/mdf2)底层检视/编辑器(PySide6)。

定位: 一个"底层材质编辑器"薄壳; 核心读写全部复用 tools/material_toolkit/lib
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
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # 仓库根(tools 的上级)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.material_toolkit.lib.binding import (  # noqa: E402
    add_texture_slot, group_summary,
    name_vocabulary, rename_name_global, _blob_rdef_names,
)
from tools.material_toolkit.lib.mdf2 import (  # noqa: E402
    Mdf2, MATERIAL_FLAG_FIELDS, PARAM_TYPES, SHADING_TYPES,
    encode_material_flags, shading_type_value,
)
from tools.material_toolkit.lib.mmtr import Mmtr  # noqa: E402
from tools.material_toolkit.lib.mmtr_info import (  # noqa: E402
    blob_count, blob_group_counts, blob_info, group_mode, type_label,
)
from tools.material_toolkit.lib.mmtr_blobs import (  # noqa: E402
    extract_blob, disassemble_dxbc, assemble_asm, verify_dxbc,
    find_translator, run_translator, check_asm, find_assembler, BlobSource,
)
from tools.material_toolkit.lib.mmtr_assemble import (  # noqa: E402
    assemble as assemble_mmtr, ProgramInstall,
)
from tools.material_toolkit.lib.mmtr_model import MmtrModel  # noqa: E402
from tools.material_toolkit.lib.mmtr_material import MaterialModel, parse_technology  # noqa: E402
from tools.material_toolkit.lib.rdef import replace_blob  # noqa: E402
from tools.material_toolkit.lib.mmtr_build import new_from_template  # noqa: E402
from tools.material_toolkit.lib import material_pass as mpass  # noqa: E402
from tools.material_toolkit.lib import material_gen as mgen  # noqa: E402
from tools.material_toolkit.lib import mmtr_nogen as nogen  # noqa: E402
from tools.material_toolkit.lib import material_inputs as minp  # noqa: E402
from tools.material_toolkit.lib import semantic_inputs as sinp  # noqa: E402
from tools.material_toolkit.lib import material_asset as masset  # noqa: E402
from tools.material_toolkit.lib import material_instance as minst  # noqa: E402
from tools.material_toolkit.lib import material_iface as miface  # noqa: E402

from PySide6.QtCore import (  # noqa: E402
    Qt, QTimer, QSize, Signal, QRegularExpression, QObject, QRunnable, QThreadPool,
)
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QFontMetrics, QPainter, QPalette, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QColorDialog,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTabBar, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

TYPENAME = {0x02: "tex2d", 0x80: "raw", 0x00: "sampler", 0xFF: "cbuffer"}


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


class CodeEdit(QPlainTextEdit):
    """代码编辑器: 行号 + Tab 缩进(空格) + 等宽字体。

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

    # ---- 行号区 ----
    def line_number_area_width(self):
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_lnarea_width(self):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_lnarea(self, rect, dy):
        if dy:
            self._lnarea.scroll(0, dy)
        else:
            self._lnarea.update(0, rect.y(), self._lnarea.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_lnarea_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._has_numbers:
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
    path = os.path.join(ROOT, "tools", "material_toolkit", "pass_templates",
                        template_name + ".hlsl")
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
        self.btn_open = QPushButton("打开 mmtr")
        self.btn_add = QPushButton("加贴图槽(全部组)")
        self.btn_exp = QPushButton("导出 mmtr")
        for b in (self.btn_open, self.btn_add, self.btn_exp):
            hb.addWidget(b)
        hb.addStretch(1)
        self.chk_pool_rdef = QCheckBox("仅按 RDEF 声明(视图)")
        self.chk_pool_rdef.setChecked(True)
        self.chk_pool_rdef.setToolTip(
            "勾选: “贴图绑定”只显示该 blob 的 RDEF 真正声明的资源; “名称池”的引用 blob 也只按 RDEF 归因。\n"
            "不勾选: 显示“记录引用的池”原样 —— 池常被记录共享/合并, 会把他人资源(如 VS 的\n"
            "SkinningMatrices, 或与本 shader 无关的 ATOS)一并列出。")
        self.chk_pool_rdef.stateChanged.connect(lambda *_: self._on_rdef_view_changed())
        hb.addWidget(self.chk_pool_rdef)

        self.tree_blob = QTreeWidget()
        self.tree_blob.setHeaderLabels(["#", "阶段", "大小", "组", "SRV"])
        self.tree_grp = QTreeWidget()
        self.tree_grp.setHeaderLabels(["项 / 组", "名称(池)", "类型 / 说明"])
        self.tree_pool = QTreeWidget()
        self.tree_pool.setHeaderLabels(["贴图名(池)", "引用组数", "引用 blob"])
        self.tree_param = QTreeWidget()
        self.tree_param.setHeaderLabels(["参数名", "类型", "大小", "offset"])
        self.tree_variant = QTreeWidget()
        self.tree_variant.setHeaderLabels(["技术 / 变体 / 前缀", "程序 (PS·VS·CS)", "维度 / 说明"])

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
        self.tabs.addTab(self.tree_grp, "贴图绑定")
        self.tabs.addTab(self.tree_pool, "名称池")
        self.tabs.addTab(wrap_with_add_button(self.tree_param, "＋ 新增参数", self.add_param),
                         "材质参数")
        self.tabs.addTab(self.tree_variant, "变体(材质)")
        self.tabs.addTab(self.tab_blob, "Blob(shader)")

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree_blob)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.addLayout(hb)
        lay.addWidget(split, 1)

        self.btn_open.clicked.connect(self.open_mmtr)
        self.btn_add.clicked.connect(lambda: self.add_slot(None))
        self.btn_exp.clicked.connect(self.export_mmtr)
        self.btn_dis.clicked.connect(self.disasm_cur_blob)
        self.btn_apply.clicked.connect(self.apply_cur_blob)
        self.btn_asmo.clicked.connect(self.export_asm)
        self.btn_asmi.clicked.connect(self.import_asm)
        self.tree_blob.currentItemChanged.connect(lambda *_: self.refresh_detail())
        attach_menu(self.tree_blob, self._menu_blob)
        attach_menu(self.tree_grp, self._menu_grp)
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
        self.refresh_variant()
        self._emit_title()

    def open_mmtr(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 mmtr", "", "mmtr (*.mmtr.*);;所有文件 (*)")
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
        fit_columns(self.tree_blob, [0, 1, 2, 3], pad=24, min_w=56, max_w=140)
        n = self.tree_blob.topLevelItemCount()
        if n:
            row = keep if isinstance(keep, int) and 0 <= keep < n else 0
            self.tree_blob.setCurrentItem(self.tree_blob.topLevelItem(row))

    def refresh_variant(self):
        """变体(材质)页: 按 pass 分组的技术 × 标志变体 -> 程序集(只读, 来自 MaterialModel)。"""
        tree = self.tree_variant
        tree.clear()
        if self.data is None:
            return
        mm = MaterialModel(self.data)
        s = mm.summary()
        info = QTreeWidgetItem(
            ["材质: 技术=%d  记录=%d(空槽=%d)  blob=%d"
             % (s["technologies"], s["records"], s["empty_records"], s["blobs"]),
             "变体记录=%d" % s["variant_records"],
             "前缀: ''=无 / A=AlphaTest / TS=TwoSide / ATS=两者 (+Direct)"])
        info.setToolTip(2, "前缀由 mdf2 flags 决定: bit1(BaseAlphaTestEnable)->A, "
                           "bit0(BaseTwoSideEnable)->TS")
        tree.addTopLevelItem(info)
        for p, techs in mm.by_pass().items():
            pitem = QTreeWidgetItem(["pass: %s" % p, "%d 技术" % len(techs), ""])
            tree.addTopLevelItem(pitem)
            for tech in techs:
                d = parse_technology(tech)
                sets = mm.program_sets(tech)
                titem = QTreeWidgetItem(
                    [tech, "程序集=%d  真VS=%s" % (len(sets), mm.shared_vs(tech)),
                     self._dim_label(d)])
                pitem.addChild(titem)
                for g in sets:
                    ps, vs, cs = g["programs"]
                    citem = QTreeWidgetItem(
                        ["<- %s" % ",".join(g["prefixes"]),
                         "PS=%d  VS=%d  CS=%d" % (ps, vs, cs),
                         "slots=%s" % ",".join(str(x) for x in g["slots"])])
                    titem.addChild(citem)
        tree.expandToDepth(1)
        fit_columns(tree, [0, 1, 2], pad=24, min_w=90, max_w=680)

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

    def refresh_groups(self):
        self.tree_grp.clear()
        idx = self.cur_blob()
        if idx is None or self.data is None:
            return
        vocab = self._vocab()
        chk = getattr(self, "chk_pool_rdef", None)
        rdef = self._cur_rdef_names() if (chk is not None and chk.isChecked()) else None
        for k, g in enumerate(group_summary(self.data, idx)):
            names = [s["name"] for s in g["srvs"]]      # 组名用全量(便于识别该组)
            srvs = g["srvs"] if rdef is None else [s for s in g["srvs"] if s["name"] in rdef]
            hid = len(g["srvs"]) - len(srvs)
            top = QTreeWidgetItem([f"组{k} · {group_mode(names)}", "",
                                   f"desc@0x{g['desc']:x} pool@0x{g['pool']:x} "
                                   f"n_rec={g['n_rec']} srv={g['b8']}"
                                   + (f"  已隐藏 {hid} 项(非本 shader 声明)" if hid else "")])
            top.setData(0, Qt.UserRole, ("group", k))
            self.tree_grp.addTopLevelItem(top)
            for s in srvs:
                tyname = TYPENAME.get(s["type"], f"0x{s['type']:02x}")
                child = QTreeWidgetItem([f"[{s['idx']}]", "",
                                         f"{tyname}  t{s['slot']}  hash=0x{s['hash']:08x}"])
                child.setData(0, Qt.UserRole, ("slot", k, s["slot"], s["name"]))
                top.addChild(child)
                # 槽名 = 常驻下拉框(候选 = 本文件名称池词汇表; 可自由输入新名)
                cb = NoWheelComboBox()
                cb.setEditable(True)
                cb.addItems(vocab)
                cb.setCurrentText(s["name"])
                cb.textActivated.connect(
                    lambda txt, kk=k, sl=s["slot"], old=s["name"]:
                    self._commit_slot_setname(kk, sl, old, txt))
                cb.lineEdit().editingFinished.connect(
                    lambda kk=k, sl=s["slot"], old=s["name"], c=cb:
                    self._commit_slot_setname(kk, sl, old, c.currentText()))
                self.tree_grp.setItemWidget(child, 1, cb)
            top.setExpanded(True)
        fit_columns(self.tree_grp, [0, 2], pad=28, min_w=120, max_w=520)
        self.tree_grp.setColumnWidth(1, 210)

    def _vocab_map(self):
        """名称池(去重名字 -> 引用统计)缓存; 编辑后失效。"""
        if self.data is None:
            return {}
        if self._vocab_cache is None:
            self._vocab_cache = name_vocabulary(self.data)
        return self._vocab_cache

    def _vocab(self):
        """名称池词汇表(去重名字, 排序)。"""
        return sorted(self._vocab_map())

    def _vocab_declared(self):
        """名称池(仅按 RDEF 声明归因)缓存: 引用 blob = 真正声明该资源的 shader。"""
        if getattr(self, "_vocab_cache_rdef", None) is None:
            self._vocab_cache_rdef = name_vocabulary(self.data, declared_only=True)
        return self._vocab_cache_rdef

    def _cur_rdef_names(self):
        """当前 blob 的 RDEF 声明名集合(shader 真正能采样的资源)。"""
        idx = self.cur_blob()
        if idx is None or self.data is None:
            return set()
        return _blob_rdef_names(self.data, idx)

    def _on_rdef_view_changed(self):
        """切换“仅按 RDEF 声明”视图: 同时刷新贴图绑定页与名称池页。"""
        self.refresh_groups()
        self.refresh_pool()

    def refresh_pool(self):
        """名称池页: 列出该文件用到的所有贴图名 + 引用统计。

        默认按“RDEF 声明”归因(避免共享池把他人资源算到本 blob); 可取消勾选看“按池”口径。
        """
        self.tree_pool.clear()
        if self.data is None:
            return
        chk = getattr(self, "chk_pool_rdef", None)
        vocab = (self._vocab_declared() if (chk is not None and chk.isChecked())
                 else self._vocab_map())
        for nm in sorted(vocab, key=lambda n: (-vocab[n]["groups"], n)):
            d = vocab[nm]
            it = QTreeWidgetItem([nm, str(d["groups"]), str(len(d["blobs"]))])
            it.setData(0, Qt.UserRole, ("poolname", nm))
            self.tree_pool.addTopLevelItem(it)
        fit_columns(self.tree_pool, [0, 1, 2], pad=24, min_w=80, max_w=520)

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

    def _reload_after_rename(self):
        """改名(不动参数/blob 结构)后的轻量刷新: 只刷绑定页与名称池(免整表重解析卡顿)。"""
        self._vocab_cache = None
        self._vocab_cache_rdef = None
        self.refresh_groups()
        self.refresh_pool()

    def _commit_slot_setname(self, group_k, slot, old, new):
        """下拉框改某槽的池名: 按名改(该 blob 的所有组一起改, 绑定键=名)。"""
        new = (new or "").strip()
        if not new or new == old or self.data is None:
            return
        if not self._need_mmtr():
            return
        try:
            out = rename_name_global(self.data, old, new,
                                     blobs=[self.cur_blob()])
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        if out == self.data:
            return                    # 无命中(含重复信号) -> 不动
        self.data = out
        self._reload_after_rename()

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

    def add_slot(self, only_indices=None):
        if not self._need_mmtr():
            return
        idx = self.cur_blob()
        name, ok = QInputDialog.getText(self, "加贴图槽",
                                        "新贴图槽的池名(需与 mdf2 的 type 一致):")
        if not (ok and name):
            return
        try:
            self.data = add_texture_slot(self.data, idx, name, only_indices=only_indices)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_edit(full=True)

    def rename_cur(self, slot, oldname):
        if not self._need_mmtr():
            return
        name, ok = QInputDialog.getText(self, "改名槽", f"把 {oldname} 改名为:")
        if not (ok and name and name != oldname):
            return
        try:
            self.data = rename_name_global(self.data, oldname, name,
                                           blobs=[self.cur_blob()])
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self._reload_after_rename()

    # ---- 右键菜单 ----
    def _menu_blob(self, item):
        if item is None or self.data is None:
            return None
        return [("加贴图槽(全部组)", lambda: self.add_slot(None)),
                ("复制 blob 信息",
                 lambda: _copy_to_clipboard(
                     f"blob {item.text(0)} {item.text(1)} size={item.text(2)} "
                     f"groups={item.text(3)} srv={item.text(4)}"))]

    def _menu_grp(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        if not d:
            return None
        if d[0] == "group":
            k = d[1]
            return [("加贴图槽(仅本组)", lambda: self.add_slot([k])),
                    ("复制组信息", lambda: _copy_to_clipboard(f"{item.text(0)} | {item.text(1)}"))]
        if d[0] == "slot":
            _tag, _k, slot, name = d
            return [("改名槽", lambda: self.rename_cur(slot, name)),
                    ("复制 名字+hash",
                     lambda: _copy_to_clipboard(f"{item.text(0)} {name} | {item.text(2)}"))]
        return None

    def _menu_pool(self, item):
        if item is None or self.data is None:
            return None
        nm = item.text(0)
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


# ---------------------------------------------------------------- 装配(模板+规格) 对话框
def _assemble_install(spec, template):
    """把一条 spec dict 变成一个 ProgramInstall(解析 asm/dxbc/blob 来源)。"""
    src = spec["source"]
    kind = src["kind"]
    if kind == "asm":
        ref = (extract_blob(template, spec["src_blob"])
               if spec.get("src_blob") is not None else None)
        source = BlobSource.from_asm(
            open(src["path"], encoding="utf-8").read(), ref_dxbc=ref)
    elif kind == "dxbc":
        source = BlobSource.from_dxbc(open(src["path"], "rb").read())
    elif kind == "blob":
        source = BlobSource.transport(template, int(src["idx"]))
    else:
        raise ValueError(f"未知来源: {kind!r}")
    return ProgramInstall(spec["role"], source, src_blob=spec.get("src_blob"),
                          in_place=spec.get("in_place", False))


class AssembleInstallDialog(QDialog):
    """编辑"一条安装规格": 角色 + 源 blob + 程序来源 + 就地/追加。"""

    def __init__(self, parent, template):
        super().__init__(parent)
        self.setWindowTitle("添加安装")
        self._template = template
        n = blob_count(template)
        form = QFormLayout(self)
        self.cmb_role = NoWheelComboBox()
        self.cmb_role.addItems(["PS", "VS", "CS"])
        form.addRow("角色", self.cmb_role)
        self.cmb_src = NoWheelComboBox()
        for i in range(n):
            bi = blob_info(template, i)
            self.cmb_src.addItem(f"blob[{i}] {bi['stage']} size={bi['size']}", i)
        form.addRow("源 blob(被替换)", self.cmb_src)
        self.cmb_kind = NoWheelComboBox()
        self.cmb_kind.addItems(["asm 文件", "dxbc 文件", "本文件 blob"])
        form.addRow("程序来源", self.cmb_kind)
        self.ed_path = QLineEdit()
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse)
        self._pw = QWidget()
        hb = QHBoxLayout(self._pw)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.addWidget(self.ed_path, 1)
        hb.addWidget(btn_browse)
        form.addRow("文件", self._pw)
        self.sp_blob = QSpinBox()
        self.sp_blob.setRange(0, max(0, n - 1))
        form.addRow("来源 blob 索引", self.sp_blob)
        self.chk_inplace = QCheckBox("就地替换源 blob(不追加)")
        self.chk_inplace.setToolTip(
            "勾选=直接改写源 blob(blob 数不变); 不勾=追加为新 blob 并重指对应槽")
        form.addRow("", self.chk_inplace)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.cmb_kind.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        k = self.cmb_kind.currentIndex()
        self._pw.setVisible(k in (0, 1))
        self.sp_blob.setVisible(k == 2)

    def _browse(self):
        if self.cmb_kind.currentIndex() == 0:
            p, _ = QFileDialog.getOpenFileName(
                self, "选择 asm", "", "asm (*.asm *.asm.txt *.txt);;所有文件 (*)")
        else:
            p, _ = QFileDialog.getOpenFileName(
                self, "选择 dxbc", "", "dxbc (*.dxbc *.bin *.cbo);;所有文件 (*)")
        if p:
            self.ed_path.setText(p)

    def spec(self):
        k = self.cmb_kind.currentIndex()
        if k == 0:
            src = {"kind": "asm", "path": self.ed_path.text()}
        elif k == 1:
            src = {"kind": "dxbc", "path": self.ed_path.text()}
        else:
            src = {"kind": "blob", "idx": self.sp_blob.value()}
        return {"role": self.cmb_role.currentText(),
                "src_blob": self.cmb_src.currentData(),
                "source": src,
                "in_place": self.chk_inplace.isChecked()}

    def accept(self):
        if self.cmb_kind.currentIndex() in (0, 1) and not self.ed_path.text().strip():
            QMessageBox.warning(self, "提示", "请先选择文件")
            return
        super().accept()


class AssembleDialog(QDialog):
    """装配规格编辑器: 维护一组安装, 确定后由调用方执行装配。"""

    def __init__(self, parent, template, label=""):
        super().__init__(parent)
        self.setWindowTitle("装配 mmtr(模板 + 规格)")
        self.resize(680, 440)
        self._template = template
        self._specs = []
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"模板: {label}  ({blob_count(template)} 个 blob)"))
        self.lst = QListWidget()
        v.addWidget(self.lst, 1)
        hb = QHBoxLayout()
        b_add = QPushButton("＋ 添加安装…")
        b_del = QPushButton("删除所选")
        b_clr = QPushButton("清空")
        b_add.clicked.connect(self._add)
        b_del.clicked.connect(self._del)
        b_clr.clicked.connect(self._clear)
        for b in (b_add, b_del, b_clr):
            hb.addWidget(b)
        hb.addStretch(1)
        v.addLayout(hb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("装配 → 新标签页")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add(self):
        d = AssembleInstallDialog(self, self._template)
        if d.exec() == QDialog.Accepted:
            self._specs.append(d.spec())
            self._refresh()

    def _del(self):
        rows = sorted((self.lst.row(it) for it in self.lst.selectedItems()),
                      reverse=True)
        for r in rows:
            self._specs.pop(r)
        self._refresh()

    def _clear(self):
        self._specs = []
        self._refresh()

    def _refresh(self):
        self.lst.clear()
        for s in self._specs:
            src = s["source"]
            k = src["kind"]
            if k == "asm":
                tgt = f"asm:{os.path.basename(src['path'])}"
            elif k == "dxbc":
                tgt = f"dxbc:{os.path.basename(src['path'])}"
            else:
                tgt = f"blob[{src['idx']}]"
            mode = "就地替换" if s["in_place"] else "追加"
            self.lst.addItem(
                f"{s['role']}  ←  {tgt}   (源 blob[{s['src_blob']}], {mode})")

    def spec_list(self):
        return list(self._specs)


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
    d = os.path.join(ROOT, "tools", "material_toolkit", "pass_templates")
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
        for label, val in (("默认光照", "default"), ("自定义光照", "custom")):
            self.cmb_light.addItem(label, val)
        self.cmb_shading = NoWheelComboBox()
        # 前向暂去掉(模板已归档; 之后重构再加回)
        for label, val in (("延迟", "deferred"),):
            self.cmb_shading.addItem(label, val)
        self.ed_mmtr = QLineEdit()
        self.ed_mmtr.setPlaceholderText("导出 mdf2 时的 MasterMaterial 路径(可留空)")
        self.ed_name = QLineEdit()
        form.addRow("光照模式", self.cmb_light)
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
        self.ed_full = CodeEdit(indent=4)
        self.ed_full.setReadOnly(True)
        self._full_hl = HlslHighlighter(self.ed_full.document())
        self.ed_full_depth = CodeEdit(indent=4)
        self.ed_full_depth.setReadOnly(True)
        self._depth_hl = HlslHighlighter(self.ed_full_depth.document())
        # 组装页: 单页 + 顶部按钮组切换各 pass(不再一 pass 一 Tab)
        self._asm_page, self._asm_btns, self._asm_stack = _seg_switch(
            [("主 pass", self.ed_full), ("深度 pass", self.ed_full_depth)])
        self.tabs.addTab(self._asm_page, "组装")
        self.tabs.addTab(self._wrap_inputs(), "输入")
        self.tree_info = QTreeWidget()
        self.tree_info.setHeaderLabels(["项", "类型", "落点/说明"])
        self.tabs.addTab(self.tree_info, "语义输出 / 校验")
        root.addWidget(self.tabs, 1)
        # 自定义输入(预设/参数/贴图/引擎资源)状态: 与源码 `//!` 声明同步
        self._presets = []
        self._params = []
        self._textures = []
        self._engine = []
        self._tags = {}          # (kind, name) -> 显式 `@pass`; 无 = 按引用自动归属

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

        GUI 与生成器共用, 避免两条路径基座不一致(重复声明/寄存器错配)。按 pass 缓存。
        """
        if not hasattr(self, "_iface_min"):
            self._iface_min = {}
        if pass_name not in self._iface_min:
            self._iface_min[pass_name] = minp.base_iface_for_pass(pass_name)
        return self._iface_min[pass_name]

    def _effective_inputs(self, pass_name="main"):
        """基座(该 pass 的引擎依赖) + 自定义输入(**按 pass 过滤**); 返回 (iface, keepalive, minput)。"""
        src = self.ed_src.toPlainText()
        if pass_name == "main":
            own, other = "MaterialDepth", "MaterialMain"
        else:
            own, other = "MaterialMain", "MaterialDepth"
        base = self._base_iface(pass_name)
        p_src = nogen._strip_hlsl_fn(src, own)
        o_code = (minp._code_only(nogen._strip_hlsl_fn(src, other))
                  if pass_name == "main" else None)
        iface, ka, rep = minp.build_iface_and_keepalive(
            p_src, base, pass_name=pass_name, other_code=o_code)
        if iface is None:
            iface = base
        return iface, ka, (rep or {}).get("minput")

    def _effective_iface(self):
        return self._effective_inputs()[0]

    # ---- 输入页 ----
    def _wrap_inputs(self):
        """输入页: 单页 + 顶部按钮组切换各 pass(每 pass 独立输入树 + 增删)。"""
        _w1, self.tree_inputs = self._inputs_tree()
        _w2, self.tree_inputs_depth = self._inputs_tree()
        page, self._in_btns, self._in_stack = _seg_switch(
            [("主 pass", _w1), ("深度 pass", _w2)])
        return page

    def _inputs_tree(self):
        """一个 pass 的输入视图(输入树 + 增删按钮)。返回 (容器, 树)。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        tree = QTreeWidget()
        tree.setHeaderLabels(["类别 / 名", "类型", "说明"])
        v.addWidget(tree, 1)
        bar = QHBoxLayout()
        for text, cb in (("＋预设输入", self._add_presets), ("＋参数", self._add_param),
                         ("＋贴图", self._add_tex), ("＋引擎资源", self._add_engine),
                         ("删除选中", self._del_custom),
                         ("从源码重载声明", self._reload_decls_from_src)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        attach_menu(tree, self._menu_inputs)
        return w, tree

    def _cur_pass(self):
        """输入页当前查看的 pass 名(按钮组选择)。"""
        return "depth" if self._in_stack.currentIndex() == 1 else "main"

    def _active_input_tree(self):
        return self.tree_inputs_depth if self._cur_pass() == "depth" else self.tree_inputs

    def _load_decls_from_src(self):
        src = self.ed_src.toPlainText()
        self._presets = sinp.parse_preset_decls(src)
        self._params, self._textures = mgen.parse_decls(src)
        self._engine = minp.parse_engine_decls(src)
        tags = minp._decl_name_tags(src)
        self._tags = {}
        for n in self._presets:
            self._tags[("preset", n)] = tags.get(n)
        for n, _t in self._params:
            self._tags[("param", n)] = tags.get(n)
        for n in self._textures:
            self._tags[("tex", n)] = tags.get(n)
        for n in self._engine:
            self._tags[("engine", n)] = tags.get(n)

    def _locked_names(self, pass_name="main"):
        """被锁定(禁止删除)的引擎资源名 -> [来源...]: pass 依赖 + 预设依赖 + 参数所需 UserMaterial。"""
        res = sinp.resolve(self._presets, sinp.names_in(self._base_iface(pass_name)))
        lock = {k: list(v) for k, v in res["lock"].items()}
        _pt = "深度 pass" if pass_name == "depth" else "主 pass"
        for nm in minp.pass_dep_names(pass_name):
            lock.setdefault(nm, [])
            if _pt not in lock[nm]:
                lock[nm].append(_pt)
        if self._params:
            lock.setdefault("UserMaterial", [])
            if "材质参数" not in lock["UserMaterial"]:
                lock["UserMaterial"].append("材质参数")
        return lock

    def normalize_inputs(self):
        """补齐(幂等): 预设依赖的资源 / 参数所需的 UserMaterial 必须在 _engine 里(锁定)。

        仅改**内存列表**, 不写源码 —— 由 `_rewrite_decls()` 落盘。删除锁定资源会被此步补回。
        """
        res = sinp.resolve(self._presets, sinp.names_in(self._base_iface()))
        for nm in res["engine"]:
            if nm not in self._engine:
                self._engine.append(nm)
        if self._params and "UserMaterial" not in self._engine:
            self._engine.append("UserMaterial")
        return res

    def _rewrite_decls(self):
        """把自定义输入写成源码顶部的 `//!` 声明区(先删旧声明行)。"""
        self.normalize_inputs()          # 落盘前补齐锁定依赖
        src = self.ed_src.toPlainText()
        body = "\n".join(l for l in src.splitlines()
                         if not l.lstrip().startswith("//!")).lstrip("\n")
        def _tg(kind, name):
            t = self._tags.get((kind, name))
            return (" @%s" % t) if t else ""
        lines = ["//! preset %s%s" % (n, _tg("preset", n)) for n in self._presets]
        lines += ["//! param %s %s%s" % (t, n, _tg("param", n)) for n, t in self._params]
        lines += ["//! tex %s%s" % (n, _tg("tex", n)) for n in self._textures]
        lines += ["//! engine %s%s" % (n, _tg("engine", n)) for n in self._engine]
        head = ("\n".join(lines) + "\n\n") if lines else ""
        self.ed_src.setPlainText(head + body)
        self.refresh_info()   # 内含 refresh_inputs

    def _reload_decls_from_src(self):
        self._load_decls_from_src()
        self.normalize_inputs()
        self.refresh_inputs()

    def _ensure_inputs(self):
        """从源码重载 + 补齐锁定依赖并写回。载入/编译/生成等关键处调用(防手改源码绕过锁)。"""
        self._load_decls_from_src()
        self._rewrite_decls()

    def _add_presets(self):
        """从预设(语义)输入目录挑选; 依赖的引擎资源会自动加入并锁定。"""
        self._load_decls_from_src()
        d = PresetDialog(self, selected=set(self._presets))
        if d.exec() != QDialog.Accepted:
            return
        _old = set(self._presets)
        self._presets = d.selected_names()
        for n in self._presets:
            if n not in _old:
                self._tags[("preset", n)] = self._cur_pass()
        self._rewrite_decls()

    def _add_engine(self):
        """从允许清单挑选引擎资源(cbuffer/texture/sampler); 声明即保活。"""
        self._load_decls_from_src()
        lock = self._locked_names()
        d = EngineResDialog(self, selected=set(self._engine), locked=set(lock))
        if d.exec() != QDialog.Accepted:
            return
        _old = set(self._engine)
        self._engine = d.selected_names()
        for n in self._engine:
            if n not in _old:
                self._tags[("engine", n)] = self._cur_pass()
        self._rewrite_decls()          # normalize 会补回锁定项

    def _on_src_changed(self):
        # 源码变动 -> 旧诊断(行号)失效; 防抖后再清(不在 textChanged 内同步 rehighlight,
        # 也避免每敲一下键就整篇重绘)。
        self._src_timer.start()

    def _clear_src_diags(self):
        # 仅在确实有旧诊断时才重绘(无错误时不触发整篇 rehighlight)
        if self._src_hl._diags:
            self._src_hl.set_diagnostics([])

    def refresh_inputs(self):
        """刷新两个 pass 的输入树(每 pass 独立: 该 pass 的固有输入 + 归属该 pass 的自定义输入)。"""
        self._load_decls_from_src()
        self.normalize_inputs()
        self._fill_input_tree(self.tree_inputs, "main")
        self._fill_input_tree(self.tree_inputs_depth, "depth")

    def _pass_belongs(self, kind, name, pass_name):
        """声明是否归属该 pass: 显式 `@pass` 优先; 否则按引用; 两边都不引用 ⇒ 归主 pass。"""
        t = self._tags.get((kind, name))
        if t:
            return t == pass_name
        src = self.ed_src.toPlainText()

        def _ref(text):
            return re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(name),
                             text) is not None
        if pass_name == "main":
            this = minp._code_only(nogen._strip_hlsl_fn(src, "MaterialDepth"))
            other = minp._code_only(nogen._strip_hlsl_fn(src, "MaterialMain"))
            return _ref(this) or not _ref(other)
        return _ref(minp._code_only(nogen._strip_hlsl_fn(src, "MaterialMain")))

    def _fill_input_tree(self, tree, pass_name):
        tree.clear()
        # 1) 插值输入(pass 依赖; 非资源; 只读展示)
        _interps = minp.interp_inputs(pass_name)
        if _interps:
            itop = QTreeWidgetItem(["插值输入 (pass 依赖, 只读)", "",
                                    "由 pass 声明依赖; 系统自动添加, 不锁定(无依赖自动去除)"])
            tree.addTopLevelItem(itop)
            for _n, _d in _interps:
                ti = QTreeWidgetItem([_n, "", _d])
                ti.setData(0, Qt.UserRole, ("copy", _n))
                itop.addChild(ti)
        # 2) 自定义输入(归属该 pass 的: 预设/参数/贴图/引擎资源; 会写进 mmtr)
        presets = [n for n in self._presets if self._pass_belongs("preset", n, pass_name)]
        params = [(n, t) for n, t in self._params
                  if self._pass_belongs("param", n, pass_name)]
        textures = [n for n in self._textures if self._pass_belongs("tex", n, pass_name)]
        engine = [n for n in self._engine if self._pass_belongs("engine", n, pass_name)]
        # pass 依赖的引擎资源: 系统自动添加 + 锁定(不出现在材质源)
        for nm in minp.pass_dep_names(pass_name):
            if nm != "UserMaterial" and nm not in engine:
                engine.append(nm)
        lock = self._locked_names(pass_name)
        cust = QTreeWidgetItem(["自定义输入 (预设/参数/贴图)", "", "写进 mmtr 参数表/绑定"])
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
            it = QTreeWidgetItem([n, t, ""])
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
        token = kind[1]
        acts.append(("复制: %s" % token, lambda: self._copy_token(token)))
        return acts

    def _copy_token(self, token):
        _copy_to_clipboard(token)
        self.lbl_status.setText("已复制: %s" % token)

    def _add_param(self):
        name, ok = QInputDialog.getText(self, "新增参数", "参数名(建议 VAR_ 开头):")
        if not ok or not name.strip():
            return
        name = name.strip()
        typ = pick_type(self, "参数类型", "float4")
        if typ is None:
            return
        self._load_decls_from_src()
        if any(n == name for n, _ in self._params):
            QMessageBox.warning(self, "重复", "参数已存在: %s" % name)
            return
        self._params.append((name, typ))
        self._tags[("param", name)] = self._cur_pass()
        self._rewrite_decls()

    def _add_tex(self):
        name, ok = QInputDialog.getText(self, "新增贴图", "贴图槽名(如 BaseMetalMap):")
        if not ok or not name.strip():
            return
        name = name.strip()
        self._load_decls_from_src()
        if name in self._textures:
            QMessageBox.warning(self, "重复", "贴图已存在: %s" % name)
            return
        self._textures.append(name)
        self._tags[("tex", name)] = self._cur_pass()
        self._rewrite_decls()

    def _rename_custom(self, kind):
        cat, name = kind
        new, ok = QInputDialog.getText(self, "改名", "新名字:", text=name)
        if not ok or not new.strip() or new.strip() == name:
            return
        new = new.strip()
        self._load_decls_from_src()
        if cat == "param":
            self._params = [(new if n == name else n, t) for n, t in self._params]
        else:
            self._textures = [new if n == name else n for n in self._textures]
        self._tags[(cat, new)] = self._tags.pop((cat, name), None)
        self._rewrite_decls()

    def _del_custom(self):
        it = self._active_input_tree().currentItem()
        kind = it.data(0, Qt.UserRole) if it is not None else None
        if not kind:
            return
        cat, name = kind
        self._load_decls_from_src()
        lock = self._locked_names()
        if cat == "engine" and name in lock:
            QMessageBox.information(
                self, "已锁定",
                "%s 由 %s 依赖, 不能删除。\n如需删除, 请先移除依赖它的预设输入。"
                % (name, " / ".join(lock[name])))
            return
        if cat == "preset":
            self._presets = [n for n in self._presets if n != name]
        elif cat == "param":
            self._params = [(n, t) for n, t in self._params if n != name]
        elif cat == "engine":
            self._engine = [n for n in self._engine if n != name]
        else:
            self._textures = [n for n in self._textures if n != name]
        self._tags.pop((cat, name), None)
        self._rewrite_decls()

    # ---- 编译诊断 ----
    def _err_diags(self, err_text, tmpl, lmap=None):
        """把 D3DCompile 报错行(组装文行号)映射回用户源码行。返回 [(行1基,start,end,msg)]。

        lmap: 用户编译源(可能已剥离某钩子)行号 -> 编辑器源码(ed_src)行号 的映射。
        """
        off = mpass.material_line_offset(tmpl)
        out = []
        for line in (err_text or "").splitlines():
            m = _HLSL_ERR_RE.search(line)
            if not m:
                continue
            aln = int(m.group(1))
            if aln <= off:
                continue   # 模板内的错(非用户源码)
            uln = aln - off
            if lmap is not None:
                uln = lmap(uln)
            out.append((uln, max(0, int(m.group(2)) - 1), 10 ** 6,
                        "%s %s: %s" % (m.group(4), m.group(5), m.group(6))))
        return out

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
        self._apply_asset()
        self._ensure_inputs()
        self.lbl_status.setText("已打开 %s" % os.path.basename(path))

    def save_asset(self):
        self._sync_asset()
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
        self._ensure_inputs()
        self._sync_asset()
        tmpl = self.asset.template.get("pass_template") or "deferred_std"
        src = self.asset.shading_source or ""
        # ---- 必需函数校验(每个 pass 都必须提供; 缺则报错, 不静默回退默认) ----
        if not src.strip():
            self._src_hl.set_diagnostics([])
            self.lbl_status.setText("[材质源为空] 请先「载入默认材质」或填写材质源")
            self.lbl_status.setStyleSheet("color:#c0392b")
            return
        missing = [nm for nm in ("MaterialMain", "MaterialDepth")
                   if nogen._hlsl_fn_span(src, nm) is None]
        if missing:
            self._src_hl.set_diagnostics([])
            self.tabs.setCurrentIndex(0)
            self.lbl_status.setText("[缺少必需函数] %s (每个 pass 都必须提供)"
                                    % ", ".join("void %s(...)" % n for n in missing))
            self.lbl_status.setStyleSheet("color:#c0392b")
            return
        iface, ka, minput = self._effective_inputs("main")
        # ---- 主 pass(剥掉 MaterialDepth: 未调用函数里的资源引用会污染主 PS 的 RDEF) ----
        m_src, m_map = nogen.strip_line_map(src, "MaterialDepth")
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
        # ---- 深度 pass(恒组装; 剥掉 MaterialMain) ----
        d_src, d_map = nogen.strip_line_map(src, "MaterialMain")
        iface_d, _, _ = self._effective_inputs("depth")
        _dsubs = nogen.depth_subs(d_src)
        try:
            self.ed_full_depth.setPlainText(
                mpass.build_source(d_src, "deferred_depth", iface=iface_d,
                                   keepalive="", minput=None, subs=_dsubs))
        except Exception as e:  # noqa: BLE001
            self.ed_full_depth.setPlainText(";; 组装失败: %s" % e)
        d_dxbc, d_err = mpass.compile_shading(d_src, "deferred_depth", iface=iface_d,
                                              keepalive="", minput=None, subs=_dsubs)
        # ---- 报错(优先主 pass) ----
        if m_err:
            diags = self._err_diags(m_err, tmpl, m_map)
            self._src_hl.set_diagnostics(diags)
            self.tabs.setCurrentIndex(0)   # 跳回材质源看红线
            first = diags[0][3] if diags else (m_err.strip().splitlines()[0] if m_err.strip() else "?")
            self.lbl_status.setText("[主 pass 编译失败] %d 处%s: %s"
                                    % (len(diags),
                                       ("(第%d行)" % diags[0][0]) if diags else "",
                                       first[:90]))
            self.lbl_status.setStyleSheet("color:#c0392b")
            self.lbl_status.setToolTip(m_err[:4000])
            return
        if d_err:
            diags = self._err_diags(d_err, "deferred_depth", d_map)
            self._src_hl.set_diagnostics(diags)
            self._seg_show(1)              # 切到"深度 pass"视图
            first = diags[0][3] if diags else (d_err.strip().splitlines()[0] if d_err.strip() else "?")
            self.lbl_status.setText("[深度 pass 编译失败] %d 处%s: %s"
                                    % (len(diags),
                                       ("(第%d行)" % diags[0][0]) if diags else "",
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
        self.lbl_status.setText(msg)
        self.lbl_status.setStyleSheet("color:#1a7f37")

    def _seg_show(self, k):
        """切到"组装"页并选中第 k 个 pass 视图。"""
        self._asm_stack.setCurrentIndex(k)
        for j, b in enumerate(self._asm_btns):
            b.setChecked(j == k)
        self.tabs.setCurrentWidget(self._asm_page)

    def _generate(self):
        """生成 mmtr: 无 donor(版本预设 + 我们的材质 PS; 不接任何 master)。"""
        self._ensure_inputs()
        self._sync_asset()
        tmpl = self.asset.template.get("pass_template") or "deferred_std"
        if not self.asset.is_ok():
            errs = "\n".join(m for lv, m in self.asset.validate() if lv == "error")
            if QMessageBox.question(self, "配置有误", errs + "\n\n仍要生成吗？") != QMessageBox.Yes:
                return None, None
        try:
            data, rp = nogen.build(self.asset.shading_source or "",
                                   _pass_of_template(tmpl), tmpl)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "生成失败", str(e))
            return None, None
        self._last_mmtr_bytes = data
        return data, {"replaced": [], "replaced_instance": [], "skipped": [],
                      "bad": [], "issues": rp.get("issues", []),
                      "ps_size": rp["ps_size"], "groups": rp["groups"],
                      "records": rp["records"], "nogen": True}

    def generate_mmtr(self):
        data, rep = self._generate()
        if data is None:
            return
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


class MmtrTabs(QTabWidget):
    """MMTR 多文件容器: 每个打开的 mmtr 一个标签页。

    - 标签可关闭 / 可拖动重排; 右上角「新建 mmtr…」(从模板克隆) 与「打开 mmtr…」;
    - mmtr 无法真正从 0 新建(与 mdf2 不同), 故“新建”= 选一个现有 mmtr 当模板克隆;
    - 无文件时显示一个不可关闭的「(未打开)」占位页, 避免"无内容且无处可点"。
    """

    DEFAULT_TITLE = "NewMMTR.mmtr.1808168797"

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
        btn_new = QPushButton("新建 mmtr…")
        btn_new.setToolTip("从模板克隆新建 mmtr(选一个现有 mmtr 作为模板)")
        btn_new.clicked.connect(self.new_dialog)
        btn_open = QPushButton("打开 mmtr…")
        btn_open.setToolTip("打开一个 mmtr 文件(新标签页)")
        btn_open.clicked.connect(self.open_dialog)
        btn_asm = QPushButton("装配…")
        btn_asm.setToolTip("以当前(或选定)mmtr 为模板, 按规格装配程序 -> 新标签页")
        btn_asm.clicked.connect(self.assemble_dialog)
        hb.addWidget(btn_new)
        hb.addWidget(btn_asm)
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

    def new_dialog(self):
        """选一个现有 mmtr 当模板, 克隆为新文件。"""
        p, _ = QFileDialog.getOpenFileName(
            self, "选择模板 mmtr(克隆为新建文件)", "", "mmtr (*.mmtr.*);;所有文件 (*)")
        if not p:
            return
        try:
            data = new_from_template(open(p, "rb").read())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.new_from_data(data)

    def new_from_data(self, data, title=None):
        """以给定 bytes 新建标签页(未保存; 标题用合成名)。"""
        self._drop_placeholder()
        panel = MmtrPanel(container=self)
        panel.load_data(data, path=None, title=title or self.DEFAULT_TITLE)
        return self._add_panel(panel)

    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 mmtr", "", "mmtr (*.mmtr.*);;所有文件 (*)")
        if p:
            self.open_path(p)

    def assemble_dialog(self):
        """装配(模板+规格) -> 新标签页。模板默认取当前页文件。"""
        panel = self.current_panel()
        if panel is not None and panel.data is not None:
            template, label = panel.data, panel.doc_title()
        else:
            p, _ = QFileDialog.getOpenFileName(
                self, "选择模板 mmtr", "", "mmtr (*.mmtr.*);;所有文件 (*)")
            if not p:
                return
            template, label = open(p, "rb").read(), os.path.basename(p)
        dlg = AssembleDialog(self, template, label)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            installs = [_assemble_install(s, template) for s in dlg.spec_list()]
            out = assemble_mmtr(template, installs)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "装配失败", str(e))
            return
        try:
            issues = MmtrModel(out).validate()
        except Exception as e:  # noqa: BLE001
            issues = [str(e)]
        if issues:
            QMessageBox.warning(
                self, "装配后自检",
                f"头部自检发现 {len(issues)} 处问题(建议核对):\n"
                + "\n".join(issues[:8]))
        self.new_from_data(out, title="assembled.mmtr.1808168797")

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
        lab = QLabel("尚未打开 mmtr。\n点击右上角「打开 mmtr…」或「新建 mmtr…」"
                     "(从模板克隆)，或把 .mmtr 文件拖进窗口。")
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
        self.setWindowTitle("Material Studio — mmtr / mdf2")
        self.resize(1150, 720)
        tabs = QTabWidget()
        self.mmtr = MmtrTabs()
        self.mdf2 = Mdf2Tabs()
        self.msys = MaterialSystemPanel()
        tabs.addTab(self.mmtr, "MMTR")
        tabs.addTab(self.mdf2, "MDF2")
        tabs.addTab(self.msys, "材质系统")
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
        mmtr = [p for p in paths if ".mmtr." in p.lower()]
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
            print("名称池:", mp.tree_pool.topLevelItemCount())
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
                idx = {"pool": 1, "param": 2, "variant": 3, "blob": 4}.get(pane, 0)
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
