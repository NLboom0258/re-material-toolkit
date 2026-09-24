#!/usr/bin/env python3
"""Material Studio — RE Engine 材质(mmtr/mdf2)底层检视/编辑器(PySide6)。

定位: 一个"底层材质编辑器"薄壳; 核心读写全部复用 tools/material_toolkit/lib
(binding / rdef / mdf2 / mmtr / mmtr_info)。将来换 UI 或升级为更完整的编辑器时, 逻辑不动。

布局: 每页 = 左「列表」 + 右「内容区(标签页)」, 标签名即"栏名"。
- MMTR 页: **多文件标签页**(每文件一页, 可关闭/拖动; 右上「打开 mmtr…」; 无法从0新建, 故空时显示不可关闭的「(未打开)」占位页);
  每个文件 = 左「Blob 列表」 + 右「贴图绑定」(各绑定组+贴图槽, 槽名下拉选池名) / 「名称池」(全局贴图名表, 可全局改名) / 「材质参数」(UserMaterial 参数定义, 只读)。
- MDF2 页: **多文件标签页**(每文件一页, 可关闭/拖动, 右上「＋」新建空文件, 关掉最后一个自动补空文件);
  每个文件 = 左「材质列表」 + 右「贴图槽」(type 双击改名/路径常驻输入框/增·删) / 「材质参数」(名字双击改名·类型·值可编辑) / 「材质属性」(着色类型+flags)。
交互: 常用按钮 + 对选中项**右键菜单**; 名称列**双击内联改名**(预选原名); 支持**拖拽文件**导入。
MDF2 参数: 类型列为常驻下拉; 值列按分量拆分输入框, float3/float4 额外带**颜色块**(点击取色)。

用法: python app.py [<file.mmtr.*> | <file.mdf2.*>]
自检: python app.py --selftest <file.mmtr.*>                 (offscreen 构建并打印)
截图: python app.py --shot <out.png> [--pane bind|param] <file>   (渲染截图, 调试用)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # 仓库根(tools 的上级)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.material_toolkit.lib.binding import (  # noqa: E402
    add_texture_slot, group_summary,
    name_vocabulary, rename_name_global,
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
    find_translator, run_translator,
)
from tools.material_toolkit.lib.rdef import replace_blob  # noqa: E402

from PySide6.QtCore import Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QColor, QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QColorDialog,
    QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStyle, QStyledItemDelegate,
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


def _copy_to_clipboard(text):
    QApplication.clipboard().setText(text)


class NoWheelComboBox(QComboBox):
    """不响应滚轮的下拉框(防鼠标悬停误改); 点击/键盘选择照常。

    所有 GUI 下拉框都用它(避免悬停下误编辑)。
    """

    def wheelEvent(self, event):
        event.ignore()


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

        self.tree_blob = QTreeWidget()
        self.tree_blob.setHeaderLabels(["#", "阶段", "大小", "组", "SRV"])
        self.tree_grp = QTreeWidget()
        self.tree_grp.setHeaderLabels(["项 / 组", "名称(池)", "类型 / 说明"])
        self.tree_pool = QTreeWidget()
        self.tree_pool.setHeaderLabels(["贴图名(池)", "引用组数", "引用 blob"])
        self.tree_param = QTreeWidget()
        self.tree_param.setHeaderLabels(["参数名", "类型", "大小", "offset"])

        self._vocab_cache = None    # 名称池词汇表缓存(编辑后失效)

        # Blob(shader) 编辑页: 反汇编 / 编辑 asm / 汇编放回 / 导入导出
        self.ed_asm = QPlainTextEdit()
        self.ed_asm.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.ed_asm.setFont(mono)
        self._edit_blob = None      # 当前编辑区 asm 对应的 blob 下标
        self.chk_trans = QCheckBox("用混合翻译器预处理")
        if find_translator() is None:
            self.chk_trans.setEnabled(False)
            self.chk_trans.setToolTip(
                "未找到 hlsl_blend_dxbc_translator.exe(可设环境变量 HLSL_BLEND_TRANSLATOR_EXE)")
        else:
            self.chk_trans.setToolTip(
                "把 HLSL+DXBC 混合写法翻回纯 asm 后再汇编(纯 asm 不受影响)")
        tob = QHBoxLayout()
        self.btn_dis = QPushButton("反汇编")
        self.btn_apply = QPushButton("应用(汇编+放回)")
        self.btn_asmo = QPushButton("导出 asm")
        self.btn_asmi = QPushButton("导入 asm")
        for b in (self.btn_dis, self.btn_apply, self.btn_asmo, self.btn_asmi):
            tob.addWidget(b)
        tob.addWidget(self.chk_trans)
        tob.addStretch(1)
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
        """标签页标题: 已打开文件用文件名; 未加载用(未打开)。"""
        return os.path.basename(self.path) if self.path else "(未打开)"

    def _emit_title(self):
        self.title_changed.emit(self.doc_title())

    def load_path(self, path):
        self.data = open(path, "rb").read()
        self.path = path
        self.mmtr = Mmtr.from_bytes(self.data)
        self._um = self.mmtr.cbuffer_members("UserMaterial")
        self.refresh_blobs()
        self.refresh_pool()
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

    def cur_blob(self):
        it = self.tree_blob.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def refresh_detail(self):
        self.refresh_groups()
        self.refresh_params()

    def refresh_groups(self):
        self.tree_grp.clear()
        idx = self.cur_blob()
        if idx is None or self.data is None:
            return
        vocab = self._vocab()
        for k, g in enumerate(group_summary(self.data, idx)):
            names = [s["name"] for s in g["srvs"]]
            top = QTreeWidgetItem([f"组{k} · {group_mode(names)}", "",
                                   f"desc@0x{g['desc']:x} pool@0x{g['pool']:x} "
                                   f"n_rec={g['n_rec']} srv={g['b8']}"])
            top.setData(0, Qt.UserRole, ("group", k))
            self.tree_grp.addTopLevelItem(top)
            for s in g["srvs"]:
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

    def refresh_pool(self):
        """名称池页: 列出该文件用到的所有贴图名 + 引用统计。"""
        self.tree_pool.clear()
        vocab = self._vocab_map()
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
        if full:
            self.refresh_blobs()
        self.refresh_detail()
        self.refresh_pool()

    def _reload_after_rename(self):
        """改名(不动参数/blob 结构)后的轻量刷新: 只刷绑定页与名称池(免整表重解析卡顿)。"""
        self._vocab_cache = None
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
        self.ed_asm.setPlainText(asm)
        self._edit_blob = idx
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
        self._edit_blob = None
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
            self.ed_asm.setPlainText(open(p, encoding="utf-8", errors="replace").read())
            self._edit_blob = self.cur_blob()


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


class MmtrTabs(QTabWidget):
    """MMTR 多文件容器: 每个打开的 mmtr 一个标签页。

    - 标签可关闭 / 可拖动重排; 右上角「打开 mmtr…」;
    - mmtr 无法从 0 新建(与 mdf2 不同), 故没有「＋新建」, 也不在启动时预建空文件;
    - 无文件时显示一个不可关闭的「(未打开)」占位页, 避免"无内容且无处可点"。
    """

    def __init__(self):
        super().__init__()
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self._on_close)
        corner = QPushButton("打开 mmtr…")
        corner.setToolTip("打开一个 mmtr 文件(新标签页)")
        corner.clicked.connect(self.open_dialog)
        self.setCornerWidget(corner, Qt.TopRightCorner)
        self._placeholder = None
        self._ensure_placeholder()

    def open_path(self, path):
        """打开一个 mmtr 文件并新增标签页。"""
        self._drop_placeholder()
        panel = MmtrPanel(container=self)
        panel.load_path(path)
        return self._add_panel(panel)

    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 mmtr", "", "mmtr (*.mmtr.*);;所有文件 (*)")
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
        lab = QLabel("尚未打开 mmtr。\n点击右上角「打开 mmtr…」，或把 .mmtr 文件拖进窗口。")
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
        tabs.addTab(self.mmtr, "MMTR")
        tabs.addTab(self.mdf2, "MDF2")
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
                idx = {"pool": 1, "param": 2, "blob": 3}.get(pane, 0)
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
