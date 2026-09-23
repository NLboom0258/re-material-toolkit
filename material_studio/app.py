#!/usr/bin/env python3
"""Material Studio — RE Engine 材质(mmtr/mdf2)底层检视/编辑器(MVP, PySide6)。

定位: 一个"底层材质编辑器"薄壳; 核心读写全部复用 tools/material_toolkit/lib
(binding / rdef / mdf2 / mmtr_info)。将来换 UI 或升级为更完整的编辑器时, 逻辑不动。

功能(MVP):
- MMTR: 打开 → blob 列表(阶段/大小/组数/SRV 数) → 选 blob 看"各绑定组 + 贴图槽"
  (组 = 顶点处理模式: Static/Instance/Skinning/Indirect) → 加贴图槽(全部组)/改名槽 → 导出。
- MDF2: 打开 → 材质列表 → 看贴图槽 → 设置/新增贴图槽(type+路径) → 导出。

用法: python app.py [<file.mmtr.*> | <file.mdf2.*>]
自检: python app.py --selftest <file.mmtr.*>   (offscreen 构建并打印, 不开窗)
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
from tools.material_toolkit.lib.mmtr_info import (  # noqa: E402
    blob_count, blob_group_counts, blob_info, group_mode,
)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFileDialog, QHBoxLayout, QInputDialog, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)


def _iter_items(tree):
    """深度遍历 QTreeWidget 的所有条目。"""
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        it = stack.pop()
        yield it
        stack.extend(it.child(j) for j in range(it.childCount()))


def fit_columns(tree, cols, pad=28, min_w=80, max_w=600):
    """按当前条目(含表头)最长文本设置列宽(带上下限); 之后用户仍可手动拖动。"""
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


class MmtrPanel(QWidget):
    """mmtr 检视/编辑面板。"""

    def __init__(self):
        super().__init__()
        self.data = None            # 当前 mmtr bytes
        self.path = None

        hb = QHBoxLayout()
        self.btn_open = QPushButton("打开 mmtr")
        self.btn_add = QPushButton("加贴图槽(全部组)")
        self.btn_ren = QPushButton("改名槽")
        self.btn_exp = QPushButton("导出 mmtr")
        for b in (self.btn_open, self.btn_add, self.btn_ren, self.btn_exp):
            hb.addWidget(b)
        hb.addStretch(1)

        self.tree_blob = QTreeWidget()
        self.tree_blob.setHeaderLabels(["#", "阶段", "大小", "组", "SRV"])
        self.tree_grp = QTreeWidget()
        self.tree_grp.setHeaderLabels(["槽 / 组", "类型 / 说明"])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree_blob)
        split.addWidget(self.tree_grp)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.addLayout(hb)
        lay.addWidget(split, 1)

        self.btn_open.clicked.connect(self.open_mmtr)
        self.btn_add.clicked.connect(self.add_slot)
        self.btn_ren.clicked.connect(self.rename_slot)
        self.btn_exp.clicked.connect(self.export_mmtr)
        self.tree_blob.currentItemChanged.connect(lambda *_: self.refresh_groups())

    # ---- 打开 / 导出 ----
    def load_path(self, path):
        self.data = open(path, "rb").read()
        self.path = path
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

    def refresh_groups(self):
        self.tree_grp.clear()
        idx = self.cur_blob()
        if idx is None or self.data is None:
            return
        for k, g in enumerate(group_summary(self.data, idx)):
            names = [s["name"] for s in g["srvs"]]
            mode = group_mode(names)
            top = QTreeWidgetItem([f"组{k} · {mode}",
                                   f"desc@0x{g['desc']:x} pool@0x{g['pool']:x} "
                                   f"n_rec={g['n_rec']} srv={g['b8']}"])
            self.tree_grp.addTopLevelItem(top)
            for s in g["srvs"]:
                tyname = {0x02: "tex2d", 0x80: "raw", 0x00: "sampler",
                          0xff: "cbuffer"}.get(s["type"], f"0x{s['type']:02x}")
                child = QTreeWidgetItem([f"t{s['slot']}  {s['name']}",
                                         f"{tyname}  hash=0x{s['hash']:08x}"])
                child.setData(0, Qt.UserRole, (k, s["slot"], s["name"]))
                top.addChild(child)
            top.setExpanded(True)
        fit_columns(self.tree_grp, [0], pad=28, min_w=200, max_w=480)

    # ---- 编辑 ----
    def _need_mmtr(self):
        if self.data is None:
            QMessageBox.warning(self, "提示", "请先打开一个 mmtr")
            return False
        return True

    def add_slot(self):
        if not self._need_mmtr():
            return
        idx = self.cur_blob()
        name, ok = QInputDialog.getText(self, "加贴图槽",
                                        "新贴图槽的池名(需与 mdf2 的 type 一致):")
        if not (ok and name):
            return
        try:
            self.data = add_texture_slot(self.data, idx, name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.refresh_blobs()
        self.refresh_groups()

    def rename_slot(self):
        if not self._need_mmtr():
            return
        it = self.tree_grp.currentItem()
        got = it.data(0, Qt.UserRole) if it else None
        if not got:
            QMessageBox.warning(self, "提示", "请在右侧选中一个贴图槽(t?)")
            return
        _gi, slot, oldname = got
        name, ok = QInputDialog.getText(self, "改名槽", f"把 t{slot}({oldname}) 改名为:")
        if not (ok and name):
            return
        try:
            self.data = rename_slot(self.data, self.cur_blob(), slot, name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "失败", str(e))
            return
        self.refresh_blobs()
        self.refresh_groups()


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

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree_mat)
        split.addWidget(self.tree_tex)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.addLayout(hb)
        lay.addWidget(split, 1)

        self.btn_open.clicked.connect(self.open_mdf2)
        self.btn_set.clicked.connect(self.set_texture)
        self.btn_exp.clicked.connect(self.export_mdf2)
        self.tree_mat.currentItemChanged.connect(lambda *_: self.refresh_tex())

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
        if self.tree_mat.topLevelItemCount():
            self.tree_mat.setCurrentItem(self.tree_mat.topLevelItem(0))

    def cur_mat(self):
        it = self.tree_mat.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def refresh_tex(self):
        self.tree_tex.clear()
        mn = self.cur_mat()
        if not mn:
            return
        for m in self.obj.materials:
            if m.name == mn:
                for t in m.textures:
                    self.tree_tex.addTopLevelItem(
                        QTreeWidgetItem([t.texture_type, t.texture_path]))
                break
        fit_columns(self.tree_tex, [0], pad=28, min_w=200, max_w=340)

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
        self.refresh_tex()

    def export_mdf2(self):
        if self.obj is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "导出 mdf2", self.path or "out.mdf2.10")
        if p:
            self.obj.save(p)
            QMessageBox.information(self, "导出", f"已写出:\n{p}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Material Studio — mmtr / mdf2")
        self.resize(1100, 700)
        tabs = QTabWidget()
        self.mmtr = MmtrPanel()
        self.mdf2 = Mdf2Panel()
        tabs.addTab(self.mmtr, "MMTR")
        tabs.addTab(self.mdf2, "MDF2")
        self.tabs = tabs
        self.setCentralWidget(tabs)

    def open_file(self, path):
        low = path.lower()
        if ".mdf2." in low:
            self.mdf2.load_path(path)
        else:
            self.mmtr.load_path(path)


def main(argv):
    app = QApplication(argv)
    win = MainWindow()

    rest = list(argv[1:])
    shot = None
    if "--shot" in rest:
        i = rest.index("--shot")
        shot = rest[i + 1] if i + 1 < len(rest) else None
        del rest[i:i + 2]
    selftest = "--selftest" in rest
    if selftest:
        rest.remove("--selftest")
    args = [a for a in rest if not a.startswith("--")]

    if selftest:
        if args:
            win.open_file(args[0])
            win.mmtr.tree_blob.setCurrentItem(win.mmtr.tree_blob.topLevelItem(33))
            win.mmtr.refresh_groups()
            print("blobs:", win.mmtr.tree_blob.topLevelItemCount())
            for i in range(win.mmtr.tree_grp.topLevelItemCount()):
                top = win.mmtr.tree_grp.topLevelItem(i)
                print("  ", top.text(0), "|", top.text(1))
                for j in range(top.childCount()):
                    c = top.child(j)
                    print("      ", c.text(0), "|", c.text(1))
        print("selftest OK")
        return 0

    if args:
        win.open_file(args[0])
    win.resize(1150, 720)
    if shot:
        if args and ".mdf2." in args[0].lower():
            win.tabs.setCurrentIndex(1)
        elif args and win.mmtr.tree_blob.topLevelItemCount() > 33:
            win.mmtr.tree_blob.setCurrentItem(win.mmtr.tree_blob.topLevelItem(33))
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
