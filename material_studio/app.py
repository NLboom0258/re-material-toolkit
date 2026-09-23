#!/usr/bin/env python3
"""Material Studio — RE Engine 材质(mmtr/mdf2)底层检视/编辑器(PySide6)。

定位: 一个"底层材质编辑器"薄壳; 核心读写全部复用 tools/material_toolkit/lib
(binding / rdef / mdf2 / mmtr / mmtr_info)。将来换 UI 或升级为更完整的编辑器时, 逻辑不动。

布局: 每页 = 左「列表」 + 右「内容区(标签页)」, 标签名即"栏名"。
- MMTR 页: 左=Blob 列表; 右标签 = 「贴图绑定」(各绑定组+贴图槽) / 「材质参数」(UserMaterial 参数定义, 只读)。
- MDF2 页: 左=材质列表; 右标签 = 「贴图槽」 / 「材质参数」(该材质的参数值, 只读)。
交互: 常用按钮保留, 另支持对选中项**右键菜单**。

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
    add_texture_slot, group_summary, rename_slot,
)
from tools.material_toolkit.lib.mdf2 import Mdf2  # noqa: E402
from tools.material_toolkit.lib.mmtr import Mmtr  # noqa: E402
from tools.material_toolkit.lib.mmtr_info import (  # noqa: E402
    blob_count, blob_group_counts, blob_info, group_mode, type_label,
)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFileDialog, QHBoxLayout, QInputDialog, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSplitter, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
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


PARAM_TYPES = [("float", 4), ("float2", 8), ("float3", 12), ("float4", 16),
               ("float4x3", 48), ("float4x4", 64)]


def pick_type_size(parent, title):
    """下拉选择参数类型 - 返回字节大小(取消返回 None)。"""
    items = [f"{n}  ({s} 字节)" for n, s in PARAM_TYPES]
    choice, ok = QInputDialog.getItem(parent, title, "类型:", items, 0, False)
    if not ok:
        return None
    return PARAM_TYPES[items.index(choice)][1]


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
        if pr.cb_offset is not None:
            pr.data_offset = pr.cb_offset
            off = max(off, pr.cb_offset + pr.param_count * 4)
        else:
            pr.data_offset = off
            off += pr.param_count * 4
    mat.prop_block_size = off


class MmtrPanel(QWidget):
    """mmtr 检视/编辑面板。"""

    def __init__(self):
        super().__init__()
        self.data = None            # 当前 mmtr bytes
        self.path = None
        self.mmtr = None            # Mmtr 对象(用于参数栏)
        self._um = []               # UserMaterial 成员缓存

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
        self.tree_grp.setHeaderLabels(["槽 / 组", "类型 / 说明"])
        self.tree_param = QTreeWidget()
        self.tree_param.setHeaderLabels(["参数名", "类型", "大小", "offset"])

        self.tabs = QTabWidget()
        self.tabs.addTab(self.tree_grp, "贴图绑定")
        self.tabs.addTab(wrap_with_add_button(self.tree_param, "＋ 新增参数", self.add_param),
                         "材质参数")

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
        self.tree_blob.currentItemChanged.connect(lambda *_: self.refresh_detail())
        attach_menu(self.tree_blob, self._menu_blob)
        attach_menu(self.tree_grp, self._menu_grp)
        attach_menu(self.tree_param, self._menu_param)

    # ---- 打开 / 导出 ----
    def load_path(self, path):
        self.data = open(path, "rb").read()
        self.path = path
        self.mmtr = Mmtr.from_bytes(self.data)
        self._um = self.mmtr.cbuffer_members("UserMaterial")
        self.refresh_blobs()

    def open_mmtr(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 mmtr", "", "mmtr (*.mmtr.*);;所有文件 (*)")
        if p:
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
        self.tree_blob.clear()
        gcount = blob_group_counts(self.data)
        for i in range(blob_count(self.data)):
            bi = blob_info(self.data, i)
            it = QTreeWidgetItem([str(i), bi["stage"], str(bi["size"]),
                                  str(gcount.get(bi["off"], 0)), str(bi["n_br"])])
            it.setData(0, Qt.UserRole, i)
            self.tree_blob.addTopLevelItem(it)
        fit_columns(self.tree_blob, [0, 1, 2, 3], pad=24, min_w=56, max_w=140)
        if self.tree_blob.topLevelItemCount():
            self.tree_blob.setCurrentItem(self.tree_blob.topLevelItem(0))

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
        for k, g in enumerate(group_summary(self.data, idx)):
            names = [s["name"] for s in g["srvs"]]
            top = QTreeWidgetItem([f"组{k} · {group_mode(names)}",
                                   f"desc@0x{g['desc']:x} pool@0x{g['pool']:x} "
                                   f"n_rec={g['n_rec']} srv={g['b8']}"])
            top.setData(0, Qt.UserRole, ("group", k))
            self.tree_grp.addTopLevelItem(top)
            for s in g["srvs"]:
                tyname = TYPENAME.get(s["type"], f"0x{s['type']:02x}")
                child = QTreeWidgetItem([f"t{s['slot']}  {s['name']}",
                                         f"{tyname}  hash=0x{s['hash']:08x}"])
                child.setData(0, Qt.UserRole, ("slot", k, s["slot"], s["name"]))
                top.addChild(child)
            top.setExpanded(True)
        fit_columns(self.tree_grp, [0], pad=28, min_w=200, max_w=480)

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
        self.mmtr = Mmtr.from_bytes(self.data)
        self._um = self.mmtr.cbuffer_members("UserMaterial")
        self.refresh_blobs()
        self.refresh_detail()

    def rename_cur(self, slot, oldname):
        if not self._need_mmtr():
            return
        name, ok = QInputDialog.getText(self, "改名槽", f"把 t{slot}({oldname}) 改名为:")
        if not (ok and name):
            return
        try:
            self.data = rename_slot(self.data, self.cur_blob(), slot, name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.mmtr = Mmtr.from_bytes(self.data)
        self._um = self.mmtr.cbuffer_members("UserMaterial")
        self.refresh_blobs()
        self.refresh_detail()

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
                     lambda: _copy_to_clipboard(f"{item.text(0)} | {item.text(1)}"))]
        return None

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
        size = pick_type_size(self, "新增参数")
        if size is None:
            return
        entries = self.mmtr._scan_cbuffer_entries("UserMaterial")
        offset = entries[0][3] if entries else 0   # 追加到现有成员之后
        try:
            self.data = self.mmtr.add_cbuffer_param("UserMaterial", name, size, offset)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.mmtr = Mmtr.from_bytes(self.data)
        self._um = self.mmtr.cbuffer_members("UserMaterial")
        self.refresh_blobs()
        self.refresh_detail()


class Mdf2Panel(QWidget):
    """mdf2 检视/编辑面板。"""

    def __init__(self):
        super().__init__()
        self.obj = None
        self.path = None

        hb = QHBoxLayout()
        self.btn_open = QPushButton("打开 mdf2")
        self.btn_set = QPushButton("设置/新增贴图槽")
        self.btn_exp = QPushButton("导出 mdf2")
        for b in (self.btn_open, self.btn_set, self.btn_exp):
            hb.addWidget(b)
        hb.addStretch(1)

        self.tree_mat = QTreeWidget()
        self.tree_mat.setHeaderLabels(["材质"])
        self.tree_tex = QTreeWidget()
        self.tree_tex.setHeaderLabels(["贴图槽(type)", "贴图路径"])
        self.tree_param = QTreeWidget()
        self.tree_param.setHeaderLabels(["参数名", "类型", "值", "offset"])

        self.tabs = QTabWidget()
        self.tabs.addTab(self.tree_tex, "贴图槽")
        self.tabs.addTab(wrap_with_add_button(self.tree_param, "＋ 新增参数", self.add_param),
                         "材质参数")

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree_mat)
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

    def load_path(self, path):
        self.obj = Mdf2.load(path)
        self.path = path
        self.refresh_mats()

    def open_mdf2(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开 mdf2", "", "mdf2 (*.mdf2.*);;所有文件 (*)")
        if p:
            self.load_path(p)

    def refresh_mats(self):
        self.tree_mat.clear()
        for m in self.obj.materials:
            it = QTreeWidgetItem([m.name])
            it.setData(0, Qt.UserRole, m.name)
            self.tree_mat.addTopLevelItem(it)
        fit_columns(self.tree_mat, [0], pad=24, min_w=120, max_w=280)
        if self.tree_mat.topLevelItemCount():
            self.tree_mat.setCurrentItem(self.tree_mat.topLevelItem(0))

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

    def refresh_tex(self):
        self.tree_tex.clear()
        m = self._cur_material()
        if m is None:
            return
        for t in m.textures:
            it = QTreeWidgetItem([t.texture_type, t.texture_path])
            it.setData(0, Qt.UserRole, ("tex", t.texture_type, t.texture_path))
            self.tree_tex.addTopLevelItem(it)
        fit_columns(self.tree_tex, [0], pad=28, min_w=200, max_w=340)

    def refresh_params(self):
        self.tree_param.clear()
        m = self._cur_material()
        if m is None:
            return
        relayout_offsets(m)   # 让 offset 显示与实际导出一致(新参数不再显示 0)
        for pr in m.properties:
            vals = ", ".join(f"{v:g}" for v in pr.values)
            it = QTreeWidgetItem([pr.name, type_label(pr.param_count * 4), vals,
                                  f"0x{pr.data_offset:04x}"])
            it.setData(0, Qt.UserRole, ("param", pr.name))
            self.tree_param.addTopLevelItem(it)
        fit_columns(self.tree_param, [0, 1, 2, 3], pad=24, min_w=90, max_w=420)

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
        return [("设置/新增贴图槽", self.set_texture),
                ("复制材质名", lambda: _copy_to_clipboard(item.text(0)))]

    def _menu_tex(self, item):
        d = item.data(0, Qt.UserRole) if item else None
        if not d or d[0] != "tex":
            return None
        _tag, ty, old = d
        return [("改路径", lambda: self._set_path(ty, old)),
                ("复制 type/路径",
                 lambda: _copy_to_clipboard(f"{item.text(0)} | {item.text(1)}"))]

    def _menu_param(self, item):
        acts = [("新增参数", self.add_param)]
        d = item.data(0, Qt.UserRole) if item else None
        if d and d[0] == "param":
            acts.insert(0, ("编辑值", lambda: self.edit_param(d[1])))
            acts.append(("复制行",
                         lambda: _copy_to_clipboard(
                             " | ".join(item.text(c) for c in range(4)))))
        return acts

    def _find_prop(self, name):
        m = self._cur_material()
        if m is None:
            return None
        for pr in m.properties:
            if pr.name == name:
                return pr
        return None

    def edit_param(self, name):
        if self.obj is None:
            return
        pr = self._find_prop(name)
        if pr is None:
            return
        cur = ", ".join(f"{v:g}" for v in pr.values)
        text, ok = QInputDialog.getText(self, "编辑值",
                                        f"{name} 的值({pr.param_count} 个, 逗号/空格分隔):",
                                        text=cur)
        if not ok:
            return
        try:
            vals = [float(x) for x in text.replace(",", " ").split()]
        except ValueError:
            QMessageBox.critical(self, "失败", "无法解析为数字(用逗号/空格分隔)")
            return
        if not vals:
            return
        pr.values = vals
        self.refresh_detail()

    def add_param(self):
        if self.obj is None:
            QMessageBox.warning(self, "提示", "请先打开一个 mdf2")
            return
        mn = self.cur_mat()
        if not mn:
            return
        name, ok = QInputDialog.getText(self, "新增参数", "参数名:")
        if not (ok and name):
            return
        size = pick_type_size(self, "新增参数")
        if size is None:
            return
        count = max(1, size // 4)
        default = ", ".join("0" for _ in range(count))
        text, ok = QInputDialog.getText(self, "新增参数", f"值({count} 个, 逗号/空格分隔):",
                                        text=default)
        if not ok:
            return
        try:
            vals = [float(x) for x in text.replace(",", " ").split()]
        except ValueError:
            QMessageBox.critical(self, "失败", "无法解析为数字")
            return
        if not vals:
            QMessageBox.critical(self, "失败", "值不能为空")
            return
        try:
            self.obj.add_property(mn, name, vals)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        for m in self.obj.materials:
            relayout_offsets(m)
        self.refresh_detail()

    def _set_path(self, ty, old):
        mn = self.cur_mat()
        if not mn:
            return
        p, ok = QInputDialog.getText(self, "改路径", f"{ty} 的贴图路径:", text=old)
        if not ok:
            return
        try:
            self.obj.set_texture(mn, ty, p or None)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.refresh_detail()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Material Studio — mmtr / mdf2")
        self.resize(1150, 720)
        tabs = QTabWidget()
        self.mmtr = MmtrPanel()
        self.mdf2 = Mdf2Panel()
        tabs.addTab(self.mmtr, "MMTR")
        tabs.addTab(self.mdf2, "MDF2")
        self.tabs = tabs
        self.setCentralWidget(tabs)

    def open_file(self, path):
        if ".mdf2." in path.lower():
            self.mdf2.load_path(path)
        else:
            self.mmtr.load_path(path)


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
            win.mmtr.tree_blob.setCurrentItem(win.mmtr.tree_blob.topLevelItem(33))
            win.mmtr.refresh_detail()
            print("blobs:", win.mmtr.tree_blob.topLevelItemCount())
            print("UserMaterial 参数:", win.mmtr.tree_param.topLevelItemCount())
            for i in range(win.mmtr.tree_grp.topLevelItemCount()):
                top = win.mmtr.tree_grp.topLevelItem(i)
                print("  ", top.text(0), "|", top.text(1))
        print("selftest OK")
        return 0

    if args:
        win.open_file(args[0])
    win.resize(1150, 720)
    if shot:
        is_mdf2 = bool(args) and ".mdf2." in args[0].lower()
        if is_mdf2:
            win.tabs.setCurrentIndex(1)
            panel = win.mdf2
        else:
            panel = win.mmtr
            if args and panel.tree_blob.topLevelItemCount() > 33:
                panel.tree_blob.setCurrentItem(panel.tree_blob.topLevelItem(33))
        panel.tabs.setCurrentIndex(1 if pane == "param" else 0)
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
