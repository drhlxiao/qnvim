#!/usr/bin/env python3
"""qnvim: minimal gvim-style GUI for Neovim.
Qt grid renderer (ext_linegrid) + menus generated from menu.vim + toolbar.
Requires: nvim >= 0.9 on PATH, pip install pynvim PySide6
Usage:    python qnvim.py [files...]
"""
import signal
import sys
import threading

import pynvim
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QStyle, QWidget

SPECIAL = {
    Qt.Key_Escape: "Esc", Qt.Key_Return: "CR", Qt.Key_Enter: "CR", Qt.Key_Backspace: "BS",
    Qt.Key_Tab: "Tab", Qt.Key_Backtab: "Tab", Qt.Key_Delete: "Del", Qt.Key_Insert: "Insert",
    Qt.Key_Left: "Left", Qt.Key_Right: "Right", Qt.Key_Up: "Up", Qt.Key_Down: "Down",
    Qt.Key_Home: "Home", Qt.Key_End: "End", Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown",
    Qt.Key_Space: "Space",
    **{getattr(Qt, f"Key_F{i}"): f"F{i}" for i in range(1, 13)},
}
MODIFIER_KEYS = (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr)
col = QColor.fromRgb


def key_to_nvim(e):
    k, m, t = e.key(), e.modifiers(), e.text()
    if k in MODIFIER_KEYS:
        return None
    ctrl, alt, shift = (bool(m & x) for x in (Qt.ControlModifier, Qt.AltModifier, Qt.ShiftModifier))
    name = SPECIAL.get(k)
    if name is None and (ctrl or alt) and 32 < k < 127:
        name = chr(k) if shift else chr(k).lower()
        shift = False
    if name:
        mods = ""
        mods += "C-" if ctrl else ""
        mods += "A-" if alt else ""
        mods += "S-" if shift or k == Qt.Key_Backtab else ""
        return f"<{mods}{name}>"
    if t and t.isprintable():
        return {"<": "<lt>", "\\": "<Bslash>"}.get(t, t)
    return None


class Bridge(QObject):  # nvim thread -> GUI thread
    flushed = Signal(object)
    menus = Signal(object)
    title = Signal(str)
    closed = Signal()
    dirty = Signal(object)


class MainWindow(QMainWindow):
    closeRequested = Signal()

    def closeEvent(self, e):
        e.ignore()  # nvim decides whether the app exits
        self.closeRequested.emit()


def make_icon():
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(col(0x2B2B2B))
    p.drawRoundedRect(0, 0, 256, 256, 48, 48)
    p.setPen(col(0x57A143))
    p.setFont(QFont("Monospace", 110, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, "N")
    p.end()
    return QIcon(pm)


class Screen(QWidget):
    def __init__(self, nvim, bridge):
        super().__init__()
        self.nvim, self.attached = nvim, False
        self.font = QFont("JetBrainsMono Nerd Font", 11)
        fm = QFontMetrics(self.font)
        self.cw, self.ch, self.asc = fm.horizontalAdvance("M"), fm.height(), fm.ascent()
        # nvim-thread state
        self.cells = []
        self.hl = {}
        self.fg = 0xFFFFFF
        self.bg = 0
        self.cur = (0, 0)
        self.mode = "normal"
        # GUI-thread snapshot
        self.snap = ([], {}, self.fg, self.bg, self.cur, self.mode)
        self.bridge = bridge
        bridge.flushed.connect(self.on_flush)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def grid_size(self):
        return max(self.width() // self.cw, 1), max(self.height() // self.ch, 1)

    # ---- nvim thread: apply redraw events ----
    def redraw(self, updates):
        for name, *calls in updates:
            for a in calls:
                if name == "grid_resize":
                    w, h = a[1:3]
                    self.cells = [[(" ", 0)] * w for _ in range(h)]
                elif name == "grid_clear":
                    self.cells = [[(" ", 0)] * len(r) for r in self.cells]
                elif name == "grid_line":
                    _, row, c, cells = a[:4]
                    hl = 0
                    for cell in cells:
                        if len(cell) > 1:
                            hl = cell[1]
                        for _ in range(cell[2] if len(cell) > 2 else 1):
                            self.cells[row][c] = (cell[0], hl)
                            c += 1
                elif name == "grid_scroll":
                    _, top, bot, left, right, rows = a[:6]
                    if rows > 0:
                        rng = range(top, bot - rows)
                    else:
                        rng = range(bot - 1, top - rows - 1, -1)
                    for r in rng:
                        self.cells[r][left:right] = self.cells[r + rows][left:right]
                elif name == "hl_attr_define":
                    self.hl[a[0]] = a[1]
                elif name == "default_colors_set":
                    if a[0] >= 0:
                        self.fg = a[0]
                    if a[1] >= 0:
                        self.bg = a[1]
                elif name == "grid_cursor_goto":
                    self.cur = (a[1], a[2])
                elif name == "mode_change":
                    self.mode = a[0]
                elif name == "set_title":
                    self.bridge.title.emit(a[0])
                elif name == "flush":
                    snap = ([r[:] for r in self.cells], dict(self.hl), self.fg, self.bg, self.cur, self.mode)
                    self.bridge.flushed.emit(snap)

    # ---- GUI thread ----
    def on_flush(self, snap):
        self.snap = snap
        self.update()

    def paintEvent(self, _):
        cells, hl, fg, bg, (cy, cx), mode = self.snap
        p = QPainter(self)
        p.fillRect(self.rect(), col(bg))
        for y, row in enumerate(cells):
            x = 0
            while x < len(row):
                h, e = row[x][1], x
                while e < len(row) and row[e][1] == h:
                    e += 1
                a = hl.get(h, {})
                f, b = a.get("foreground", fg), a.get("background", bg)
                if a.get("reverse"):
                    f, b = b, f
                p.fillRect(x * self.cw, y * self.ch, (e - x) * self.cw, self.ch, col(b))
                font = self.font
                if a.get("bold") or a.get("italic") or a.get("underline"):
                    font = QFont(self.font)
                    font.setBold(bool(a.get("bold")))
                    font.setItalic(bool(a.get("italic")))
                    font.setUnderline(bool(a.get("underline")))
                p.setFont(font)
                p.setPen(col(f))
                for i in range(x, e):
                    if row[i][0].strip():
                        p.drawText(i * self.cw, y * self.ch + self.asc, row[i][0])
                x = e
        c = col(fg)
        c.setAlpha(255 if mode.startswith("insert") else 140)
        p.fillRect(cx * self.cw, cy * self.ch, 2 if mode.startswith("insert") else self.cw, self.ch, c)

    def resizeEvent(self, _):
        if self.attached:
            self.nvim.async_call(self.nvim.ui_try_resize, *self.grid_size())

    def keyPressEvent(self, e):
        k = key_to_nvim(e)
        if k:
            self.nvim.async_call(self.nvim.input, k)

    def mouse(self, btn, action, pos):
        row, col_ = int(pos.y() // self.ch), int(pos.x() // self.cw)
        self.nvim.async_call(self.nvim.api.input_mouse, btn, action, "", 0, row, col_)

    def _btn(self, e):
        buttons = {Qt.LeftButton: "left", Qt.RightButton: "right", Qt.MiddleButton: "middle"}
        return buttons.get(e.button() or e.buttons())

    def mousePressEvent(self, e):
        btn = self._btn(e)
        if btn:
            self.mouse(btn, "press", e.position())

    def mouseReleaseEvent(self, e):
        btn = self._btn(e)
        if btn:
            self.mouse(btn, "release", e.position())

    def mouseMoveEvent(self, e):
        btn = self._btn(e)
        if btn:
            self.mouse(btn, "drag", e.position())

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy:
            self.mouse("wheel", "up" if dy > 0 else "down", e.position())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("qnvim")
    icon = make_icon()
    app.setWindowIcon(icon)
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # inherited by nvim, so terminal Ctrl+C can't kill it
    nvim = pynvim.attach("child", argv=["nvim", "--embed", *sys.argv[1:]])
    bridge = Bridge()
    scr = Screen(nvim, bridge)
    win = MainWindow()
    win.setWindowTitle("qnvim")
    win.setWindowIcon(icon)
    win.setCentralWidget(scr)
    win.resize(960, 640)
    win.show()
    signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, win.close))
    heartbeat = QTimer()  # lets the Python SIGINT handler run inside the Qt loop
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(200)

    def run(fn, *a):  # thread-safe, swallows nvim errors (e.g. menu disabled in current mode)
        def go():
            try:
                fn(*a)
            except pynvim.NvimError:
                pass
        nvim.async_call(go)

    # ---- menus from menu.vim ----
    esc = lambda s: s.replace("\\", "\\\\").replace(" ", "\\ ").replace(".", "\\.")

    def add(parent, items, path):
        for m in items:
            n = m["name"]
            if m.get("hidden"):
                continue
            if n.startswith("-") and n.endswith("-"):
                parent.addSeparator()
                continue
            p = path + [n]
            sc = m.get("shortcut")
            label = n.replace(sc, "&" + sc, 1) if sc and sc in n else n
            if m.get("submenus"):
                add(parent.addMenu(label), m["submenus"], p)
                continue
            if m.get("actext"):
                label += "\t" + m["actext"]
            cmd = "emenu " + ".".join(esc(x) for x in p)
            action = parent.addAction(label)
            action.triggered.connect(lambda _=False, c=cmd: run(nvim.command, c))

    def build_menus(items):
        add(win.menuBar(), [m for m in items if m["name"] not in ("ToolBar", "PopUp")], [])

    bridge.menus.connect(build_menus)
    bridge.title.connect(win.setWindowTitle)

    # ---- toolbar ----
    def open_file():
        path, _ = QFileDialog.getOpenFileName(win)
        if path:
            run(lambda: nvim.command("edit " + nvim.call("fnameescape", path)))

    emenu = lambda m: (lambda: run(nvim.command, "emenu " + m))
    tools = [
        ("New", "SP_FileIcon", lambda: run(nvim.command, "enew")),
        ("Open", "SP_DirOpenIcon", open_file),
        ("Save", "SP_DialogSaveButton", lambda: run(nvim.command, "write")),
        None,
        ("Undo", "SP_ArrowBack", lambda: run(nvim.command, "undo")),
        ("Redo", "SP_ArrowForward", lambda: run(nvim.command, "redo")),
        None,
        ("Cut", "SP_DialogDiscardButton", emenu("Edit.Cut")),
        ("Copy", "SP_FileDialogDetailedView", emenu("Edit.Copy")),
        ("Paste", "SP_FileDialogContentsView", emenu("Edit.Paste")),
        None,
        ("Find", "SP_FileDialogContentsView", lambda: run(nvim.input, "<Esc>/")),
    ]
    tb = win.addToolBar("main")
    tb.setMovable(False)
    for t in tools:
        if t is None:
            tb.addSeparator()
            continue
        label, icon_name, fn = t
        ico = win.style().standardIcon(getattr(QStyle.StandardPixmap, icon_name))
        act = tb.addAction(ico, label)
        act.triggered.connect(lambda _=False, f=fn: f())

    # ---- nvim session ----
    def setup():
        nvim.ui_attach(*scr.grid_size(), rgb=True, ext_linegrid=True)
        scr.attached = True
        for c in ("set mouse=a", "set title", "runtime! menu.vim"):
            nvim.command(c)
        bridge.menus.emit(nvim.call("menu_get", ""))

    def loop():
        try:
            nvim.run_loop(lambda *a: None, lambda n, a: n == "redraw" and scr.redraw(a), setup)
        finally:
            bridge.closed.emit()

    # ---- quit: ask about unsaved buffers ----
    def check_dirty():
        bufs = nvim.call("getbufinfo", {"bufmodified": 1})
        bridge.dirty.emit([b["name"] or "[No Name]" for b in bufs])

    def quit_nvim():
        nvim.command("qa!")

    def save_all():
        try:
            nvim.command("silent! wa")  # unnamed buffers fail; they stay listed in the prompt
        except pynvim.NvimError:
            pass
        check_dirty()

    def ask_quit(names):
        if not names:
            run(quit_nvim)
            return
        box = QMessageBox(QMessageBox.Warning, "Unsaved changes",
                          "Save changes before quitting?", parent=win)
        box.setInformativeText("\n".join(names))
        save = box.addButton("Save All", QMessageBox.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save:
            run(save_all)
        elif clicked == discard:
            run(quit_nvim)

    win.closeRequested.connect(lambda: run(check_dirty))
    bridge.dirty.connect(ask_quit)
    bridge.closed.connect(lambda: app.exit(0))
    threading.Thread(target=loop, daemon=True).start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
