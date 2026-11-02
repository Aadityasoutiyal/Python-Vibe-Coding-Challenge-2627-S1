"""
JARVIS - AI Study OS
Voice-first desktop shell: boot sequence, HUD desktop, voice in/out,
study-file memory, and a local Ollama model.

Run:  py main.py
"""
import sys
import math
import re
import html as htmllib
from datetime import datetime

from PySide6.QtCore import (Qt, QTimer, QRectF, QPointF, QSize, QUrl, Signal, QObject, QThread,
                            QStandardPaths)
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QRadialGradient, QConicalGradient,
                           QLinearGradient, QFont, QKeySequence, QShortcut, QTextCursor,
                           QTextDocument, QTextBlockFormat, QTextCharFormat, QPainterPath,
                           QPolygonF, QDesktopServices, QPixmap)
from PySide6.QtWidgets import (QApplication, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
                               QHBoxLayout, QStackedLayout, QProgressBar, QTreeWidget,
                               QTreeWidgetItem, QTextEdit, QLineEdit, QCheckBox, QFileDialog,
                               QSizeGrip, QMessageBox, QComboBox)

from Jarvis.core import Memory, OllamaProvider, scan_folders
from Jarvis.voice import WakeListener, Speaker

try:
    import psutil
except ImportError:
    psutil = None

# ---------------------------------------------------------------- theme ----
CYAN = QColor(25, 227, 255)
BLUE = QColor(42, 107, 255)
TEXT = "#cfeaff"
DIM = "#5c7a99"
HUD_FONT = "Bahnschrift"

STATE_COLOR = {
    "idle": (25, 227, 255),
    "listening": (90, 255, 210),
    "thinking": (255, 176, 50),
    "speaking": (80, 160, 255),
}
STATE_TEXT = {"idle": "Say “Hey Jarvis”", "listening": "Listening", "thinking": "Thinking", "speaking": "Speaking"}

STYLE = f"""
* {{ font-family: '{HUD_FONT}', 'Segoe UI', sans-serif; color: {TEXT}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLineEdit, QTextEdit, QTreeWidget {{
    background: rgba(3, 9, 18, 200); border: 1px solid rgba(25, 227, 255, 60);
    padding: 6px; selection-background-color: rgba(25, 227, 255, 90);
}}
QLineEdit:focus {{ border: 1px solid rgba(25, 227, 255, 180); }}
QTreeWidget::item {{ padding: 3px; }}
QTreeWidget::item:selected {{ background: rgba(25, 227, 255, 60); }}
QHeaderView::section {{ background: rgba(25, 227, 255, 25); border: none; padding: 4px; color: {DIM}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(25, 227, 255, 90); min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: rgba(25, 227, 255, 90); min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QComboBox {{ background: rgba(3, 9, 18, 200); border: 1px solid rgba(25, 227, 255, 60); padding: 6px; }}
QComboBox QAbstractItemView {{ background: #07142a; border: 1px solid rgba(25, 227, 255, 90);
    selection-background-color: rgba(25, 227, 255, 90); }}
QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #19e3ff; background: transparent; }}
QCheckBox::indicator:checked {{ background: #19e3ff; }}
QProgressBar {{ background: rgba(25, 227, 255, 25); border: none; max-height: 4px; }}
QProgressBar::chunk {{ background: #19e3ff; }}
"""


def mono(size=11):
    f = QFont("Consolas")
    f.setPointSize(size)
    return f


def hud_font(size, bold=False, spacing=0.0):
    f = QFont(HUD_FONT)
    f.setPointSizeF(size)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def chime():
    """Short rising tone so you know JARVIS heard its name."""
    try:
        import threading
        import winsound
        threading.Thread(target=lambda: (winsound.Beep(784, 70), winsound.Beep(1175, 90)),
                         daemon=True).start()
    except Exception:
        pass


# -------------------------------------------------------- HUD primitives ----
def chamfer_path(r, c, cuts):
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    pts = []
    pts += [(x, y + c), (x + c, y)] if "tl" in cuts else [(x, y)]
    pts += [(x + w - c, y), (x + w, y + c)] if "tr" in cuts else [(x + w, y)]
    pts += [(x + w, y + h - c), (x + w - c, y + h)] if "br" in cuts else [(x + w, y + h)]
    pts += [(x + c, y + h), (x, y + h - c)] if "bl" in cuts else [(x, y + h)]
    path = QPainterPath()
    path.moveTo(*pts[0])
    for pt in pts[1:]:
        path.lineTo(*pt)
    path.closeSubpath()
    return path


def draw_hud(p, rect, c=14, cuts=("tl", "br"), alpha=225):
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(rect).adjusted(1, 1, -1, -1)
    path = chamfer_path(r, c, cuts)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(5, 14, 28, alpha))
    p.drawPath(path)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(25, 227, 255, 90), 1))
    p.drawPath(path)
    p.setPen(QPen(QColor(25, 227, 255, 235), 2))
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    L = min(18, c + 4)
    P = QPointF
    if "tl" in cuts:
        p.drawLine(P(x, y + c), P(x + c, y))
    else:
        p.drawLine(P(x, y), P(x + L, y)); p.drawLine(P(x, y), P(x, y + L))
    if "tr" in cuts:
        p.drawLine(P(x + w - c, y), P(x + w, y + c))
    else:
        p.drawLine(P(x + w, y), P(x + w - L, y)); p.drawLine(P(x + w, y), P(x + w, y + L))
    if "br" in cuts:
        p.drawLine(P(x + w, y + h - c), P(x + w - c, y + h))
    else:
        p.drawLine(P(x + w, y + h), P(x + w - L, y + h)); p.drawLine(P(x + w, y + h), P(x + w, y + h - L))
    if "bl" in cuts:
        p.drawLine(P(x + c, y + h), P(x, y + h - c))
    else:
        p.drawLine(P(x, y + h), P(x + L, y + h)); p.drawLine(P(x, y + h), P(x, y + h - L))


class HudFrame(QFrame):
    def __init__(self, parent=None, c=14, cuts=("tl", "br"), alpha=225):
        super().__init__(parent)
        self._c, self._cuts, self._alpha = c, cuts, alpha

    def paintEvent(self, _):
        p = QPainter(self)
        draw_hud(p, self.rect(), self._c, self._cuts, self._alpha)


class HudButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width() + 24, max(32, s.height()))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        a = 90 if self.isDown() else (60 if self._hover else 22)
        p.setBrush(QColor(25, 227, 255, a))
        p.setPen(QPen(QColor(25, 227, 255, 190 if self._hover else 110), 1))
        p.drawPath(chamfer_path(r, 7, ("tl", "br")))
        p.setPen(QColor(TEXT))
        p.setFont(hud_font(9.5))
        p.drawText(r, Qt.AlignCenter, self.text())


def draw_glyph(p, key, c, s):
    p.setPen(QPen(CYAN, 1.8))
    p.setBrush(Qt.NoBrush)
    if key == "Assistant":
        p.drawEllipse(c, s, s)
        p.drawEllipse(c, s * 0.55, s * 0.55)
        p.setBrush(CYAN)
        p.drawEllipse(c, s * 0.2, s * 0.2)
    elif key == "Files":
        body = QRectF(c.x() - s, c.y() - s * 0.5, 2 * s, 1.4 * s)
        p.drawRect(body)
        p.drawPolyline(QPolygonF([QPointF(c.x() - s, c.y() - s * 0.5), QPointF(c.x() - s, c.y() - s * 0.85),
                                  QPointF(c.x() - s * 0.2, c.y() - s * 0.85), QPointF(c.x() + s * 0.05, c.y() - s * 0.5)]))
    elif key == "Memory":
        pts = [QPointF(c.x() + s * math.cos(math.radians(60 * i + 30)),
                       c.y() + s * math.sin(math.radians(60 * i + 30))) for i in range(6)]
        p.drawPolygon(QPolygonF(pts))
        p.setBrush(CYAN)
        p.drawEllipse(c, s * 0.18, s * 0.18)
        for pt in pts[::2]:
            p.drawLine(c, pt)
    elif key == "Settings":
        p.drawEllipse(c, s * 0.55, s * 0.55)
        p.drawEllipse(c, s * 0.18, s * 0.18)
        for i in range(8):
            a = math.radians(45 * i)
            p.drawLine(QPointF(c.x() + math.cos(a) * s * 0.75, c.y() + math.sin(a) * s * 0.75),
                       QPointF(c.x() + math.cos(a) * s, c.y() + math.sin(a) * s))


class DockButton(QPushButton):
    def __init__(self, key):
        super().__init__()
        self.key = key
        self.setFixedSize(76, 82)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(6, 4, 64, 64)
        p.setBrush(QColor(25, 227, 255, 55 if self._hover else 14))
        p.setPen(QPen(QColor(25, 227, 255, 200 if self._hover else 100), 1))
        p.drawPath(chamfer_path(r, 10, ("tl", "br")))
        draw_glyph(p, self.key, QPointF(38, 36), 15)
        p.setPen(QColor(TEXT if self._hover else DIM))
        p.setFont(hud_font(8.5))
        p.drawText(QRectF(0, 66, 76, 16), Qt.AlignCenter, self.key)


# ---------------------------------------------------------- boot screen ----
class BootScreen(QWidget):
    finished = Signal()
    LINES = ["Starting core services", "Mounting study memory", "Checking privacy permissions",
             "Calibrating voice systems", "Connecting local language model", "Ready"]

    def __init__(self):
        super().__init__()
        self.angle = 0.0
        self.shown = 0
        self.timer = QTimer(self, interval=16, timeout=self._tick)
        self.line_timer = QTimer(self, interval=480, timeout=self._next_line)

    def start(self):
        self.timer.start()
        self.line_timer.start()

    def _tick(self):
        self.angle = (self.angle + 2.2) % 360
        self.update()

    def _next_line(self):
        self.shown += 1
        if self.shown >= len(self.LINES):
            self.line_timer.stop()
            QTimer.singleShot(700, self._done)

    def _done(self):
        self.timer.stop()
        self.finished.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#040a14"))
        cx, cy = self.width() / 2, self.height() / 2 - 60
        for i, (r, speed, span) in enumerate([(120, 1.0, 100), (98, -1.6, 70), (76, 2.4, 140)]):
            pen = QPen(CYAN if i != 1 else BLUE, 3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            for k in range(3):
                p.drawArc(rect, int((self.angle * speed + k * 120) * 16), span * 16)
        p.setPen(CYAN)
        p.setFont(hud_font(34, True, 12))
        p.drawText(QRectF(0, cy + 150, self.width(), 56), Qt.AlignCenter, "J.A.R.V.I.S")
        p.setFont(mono(11))
        y = cy + 230
        for i in range(min(self.shown + 1, len(self.LINES))):
            p.setPen(QColor(DIM) if i < self.shown else CYAN)
            p.drawText(QRectF(0, y + i * 22, self.width(), 22), Qt.AlignHCenter, self.LINES[i])
        w = 380
        x = cx - w / 2
        yb = self.height() - 70
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(25, 227, 255, 30))
        p.drawRect(QRectF(x, yb, w, 4))
        p.setBrush(CYAN)
        p.drawRect(QRectF(x, yb, w * min(self.shown, len(self.LINES)) / len(self.LINES), 4))


# ------------------------------------------------------------ the core ----
class Orb(QWidget):
    """The assistant's core. Reacts to listening, thinking and speaking."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.state = "idle"
        self.level = 0.0
        self._level_target = 0.0
        self.animated = True
        self.col = list(map(float, STATE_COLOR["idle"]))
        QTimer(self, interval=33, timeout=self._tick).start()

    def set_state(self, state):
        self.state = state

    def set_level(self, v):
        self._level_target = max(self._level_target, v)

    def mousePressEvent(self, e):
        c = QPointF(self.width() / 2, self.height() / 2)
        if math.hypot(e.position().x() - c.x(), e.position().y() - c.y()) < min(self.width(), self.height()) * 0.3:
            self.clicked.emit()

    def _tick(self):
        target = STATE_COLOR[self.state]
        for i in range(3):
            self.col[i] += (target[i] - self.col[i]) * 0.12
        self.level += (self._level_target - self.level) * 0.35
        self._level_target *= 0.85
        if self.animated:
            self.phase += 0.033
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height())
        c = QPointF(self.width() / 2, self.height() / 2)
        R = s * 0.46
        col = QColor(int(self.col[0]), int(self.col[1]), int(self.col[2]))

        def rgba(a):
            return QColor(col.red(), col.green(), col.blue(), int(a))

        speed = {"idle": 1.0, "listening": 1.6, "thinking": 3.2, "speaking": 1.8}[self.state]

        glow = QRadialGradient(c, R)
        glow.setColorAt(0, rgba(70 + 90 * self.level))
        glow.setColorAt(1, rgba(0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(c, R, R)

        # tick ring
        p.save()
        p.translate(c)
        p.rotate(self.phase * 5 * speed)
        for i in range(90):
            long_tick = i % 5 == 0
            p.setPen(QPen(rgba(170 if long_tick else 90), 1.5 if long_tick else 1))
            p.drawLine(QPointF(0, -R), QPointF(0, -R + (11 if long_tick else 5)))
            p.rotate(4)
        p.restore()

        # radar sweep
        sweep_r = R * 0.74
        cg = QConicalGradient(c, -self.phase * 40 * speed)
        cg.setColorAt(0, rgba(80))
        cg.setColorAt(0.22, rgba(0))
        cg.setColorAt(1, rgba(0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(cg))
        p.drawEllipse(c, sweep_r, sweep_r)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(rgba(60), 1))
        p.drawEllipse(c, sweep_r, sweep_r)
        p.drawEllipse(c, R * 0.52, R * 0.52)

        # rotating arcs
        for mult, spd, width in [(0.88, -22, 2), (0.64, 38, 3), (0.42, -60, 2)]:
            r = R * mult
            pen = QPen(rgba(210), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            for k in range(3):
                p.drawArc(rect, int((self.phase * spd * speed + k * 120) * 16), 46 * 16)

        # core
        pulse = {
            "idle": 0.04 * math.sin(self.phase * 2),
            "listening": 0.05 + 0.5 * self.level,
            "thinking": 0.07 * math.sin(self.phase * 6),
            "speaking": 0.05 + 0.14 * math.sin(self.phase * 9) ** 2,
        }[self.state]
        base = R * 0.24 * (1 + pulse)
        core = QRadialGradient(c, base)
        core.setColorAt(0, QColor(235, 252, 255, 255))
        core.setColorAt(0.5, rgba(235))
        core.setColorAt(1, rgba(40))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(core))
        p.drawEllipse(c, base, base)


# ----------------------------------------------------------- side panels ----
class Panel(HudFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent, c=12, cuts=("tl", "br"), alpha=200)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 14)
        self.body.setSpacing(4)
        t = QLabel(title)
        t.setFont(hud_font(9.5))
        t.setStyleSheet(f"color:{DIM};")
        self.body.addWidget(t)


class ClockPanel(Panel):
    def __init__(self, parent=None):
        super().__init__("Local time", parent)
        self.time = QLabel()
        self.time.setFont(hud_font(26))
        self.time.setStyleSheet("color:#19e3ff;")
        self.date = QLabel()
        self.body.addWidget(self.time)
        self.body.addWidget(self.date)
        QTimer(self, interval=1000, timeout=self.refresh).start()
        self.refresh()

    def refresh(self):
        now = datetime.now()
        self.time.setText(now.strftime("%H:%M:%S"))
        self.date.setText(now.strftime("%A, %B %d"))


class MonitorPanel(Panel):
    def __init__(self, parent=None):
        super().__init__("System", parent)
        self.cpu, self.ram = QProgressBar(), QProgressBar()
        self.cpu_l, self.ram_l = QLabel("Processor"), QLabel("Memory")
        for lbl, bar in ((self.cpu_l, self.cpu), (self.ram_l, self.ram)):
            bar.setTextVisible(False)
            self.body.addWidget(lbl)
            self.body.addWidget(bar)
        if psutil:
            QTimer(self, interval=1500, timeout=self.refresh).start()
            self.refresh()
        else:
            self.cpu_l.setText("Install psutil to see system stats")
            self.ram_l.hide()
            self.ram.hide()
            self.cpu.hide()

    def refresh(self):
        c, m = psutil.cpu_percent(), psutil.virtual_memory().percent
        self.cpu.setValue(int(c))
        self.ram.setValue(int(m))
        self.cpu_l.setText(f"Processor  {c:.0f}%")
        self.ram_l.setText(f"Memory  {m:.0f}%")


class StudyPanel(Panel):
    def __init__(self, brain, parent=None):
        super().__init__("Study memory", parent)
        self.brain = brain
        self.count = QLabel()
        self.count.setFont(hud_font(24))
        self.count.setStyleSheet("color:#19e3ff;")
        self.detail = QLabel()
        self.detail.setStyleSheet(f"color:{DIM};")
        self.detail.setWordWrap(True)
        self.progress = QLabel("")
        self.progress.setWordWrap(True)
        self.progress.setStyleSheet("color:#ffb032;")
        for w in (self.count, self.detail, self.progress):
            self.body.addWidget(w)
        brain.scan_progress.connect(self.progress.setText)
        brain.scan_done.connect(self._scan_done)
        QTimer(self, interval=3000, timeout=self.refresh).start()
        self.refresh()

    def _scan_done(self, _msg):
        self.progress.setText("")
        self.refresh()

    def refresh(self):
        total, rows = self.brain.mem.stats()
        self.count.setText(f"{total} files")
        if rows:
            self.detail.setText("\n".join(f"{n}  {s}" for s, n in rows[:5]))
        else:
            self.detail.setText("Nothing remembered yet. Open Files and choose folders to scan.")


# ------------------------------------------------------ floating windows ----
class TitleBar(QFrame):
    def __init__(self, window, title):
        super().__init__(window)
        self.win = window
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 0, 6, 0)
        label = QLabel(title)
        label.setFont(hud_font(10.5, True, 1.5))
        label.setStyleSheet("color:#19e3ff;")
        lay.addWidget(label)
        lay.addStretch()
        x = HudButton("✕")
        x.setFixedSize(30, 24)
        x.clicked.connect(window.close_window)
        lay.addWidget(x)
        self._start = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(QPen(QColor(25, 227, 255, 70), 1))
        p.drawLine(14, self.height() - 1, self.width() - 14, self.height() - 1)

    def mousePressEvent(self, e):
        self.win.raise_()
        self._start = (e.globalPosition().toPoint(), self.win.pos())

    def mouseMoveEvent(self, e):
        if self._start and e.buttons() & Qt.LeftButton:
            g0, p0 = self._start
            new = p0 + (e.globalPosition().toPoint() - g0)
            par = self.win.parentWidget().rect()
            new.setX(max(-self.win.width() + 80, min(new.x(), par.width() - 80)))
            new.setY(max(0, min(new.y(), par.height() - 40)))
            self.win.move(new)

    def mouseReleaseEvent(self, _):
        self._start = None


class FloatingWindow(HudFrame):
    closed = Signal(str)

    def __init__(self, key, title, content, parent):
        super().__init__(parent, c=20, cuts=("tl",), alpha=232)
        self.key = key
        self.setMinimumSize(420, 320)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 6)
        lay.setSpacing(0)
        lay.addWidget(TitleBar(self, title))
        inner = QVBoxLayout()
        inner.setContentsMargins(14, 12, 14, 0)
        inner.addWidget(content)
        lay.addLayout(inner, 1)
        grip = QHBoxLayout()
        grip.setContentsMargins(0, 0, 4, 0)
        grip.addStretch()
        grip.addWidget(QSizeGrip(self))
        lay.addLayout(grip)

    def close_window(self):
        self.closed.emit(self.key)
        self.deleteLater()


# --------------------------------------------------------------- workers ----
class AskWorker(QThread):
    token = Signal(str)
    failed = Signal(str)

    def __init__(self, provider, question, passages):
        super().__init__()
        self.provider, self.question, self.passages = provider, question, passages

    def run(self):
        try:
            for t in self.provider.stream(self.question, self.passages):
                self.token.emit(t)
        except Exception as e:
            self.failed.emit(str(e))


class ScanWorker(QThread):
    progress = Signal(str)
    done = Signal(str)

    def __init__(self, mem, roots):
        super().__init__()
        self.mem, self.roots, self._stop = mem, roots, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            seen, added = scan_folders(
                self.roots, self.mem,
                lambda s, a, n: self.progress.emit(f"Reading {n}  ({a} study files found)"),
                lambda: self._stop)
            self.done.emit(f"Finished. Checked {seen} documents and remembered {added} study files.")
        except Exception as e:
            self.done.emit(f"Scan stopped: {e}")


# ----------------------------------------------------------------- brain ----
class Brain(QObject):
    """Connects voice, memory, the scanner and the language model."""
    state_changed = Signal(str)
    level = Signal(float)
    you_said = Signal(str)
    ai_caption = Signal(str)
    scan_progress = Signal(str)
    scan_done = Signal(str)
    open_requested = Signal(str)
    mic_changed = Signal(bool)

    OPEN_NAMES = {
        "Assistant": ("assistant", "chat"),
        "Files": ("files", "library", "documents", "study files"),
        "Memory": ("memory", "notes"),
        "Settings": ("settings",),
    }

    def __init__(self):
        super().__init__()
        self.mem = Memory()
        self.provider = OllamaProvider("llama3.2")
        self.speaker = Speaker(self)
        self.speaker.speaking_changed.connect(self._speaking)
        self.listener = None
        self.worker = None
        self.scanner = None
        self.state = "idle"
        self.speak_replies = True
        self.follow_up = False
        self.mic_on = True
        self._relisten_ok = False
        self._error = False
        self._reply = ""
        self._sentence = ""
        self._sources = []
        self.last = ("", "", "")
        self.doc = QTextDocument()
        self._add_html(f"<span style='color:{DIM}'>JARVIS online. Say “Hey Jarvis” to talk, or type below.</span>")

    # ---- chat log (shared by every Assistant window) ----
    def _cursor(self):
        c = QTextCursor(self.doc)
        c.movePosition(QTextCursor.End)
        return c

    def _add_html(self, html):
        c = self._cursor()
        if not self.doc.isEmpty():
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        c.insertHtml(html)

    def _new_block(self):
        self._cursor().insertBlock(QTextBlockFormat(), QTextCharFormat())

    def _add_text(self, t):
        self._cursor().insertText(t, QTextCharFormat())

    # ---- state ----
    def set_state(self, s):
        if s != self.state:
            self.state = s
            self.state_changed.emit(s)
        if self.listener:
            if s in ("thinking", "speaking"):
                self.listener.set_paused(True)   # never listen to its own voice
            elif s == "idle":
                QTimer.singleShot(450, self._resume_listening)

    def _resume_listening(self):
        if self.state == "idle" and self.listener:
            self.listener.set_paused(False)

    def _speaking(self, on):
        if on:
            self.set_state("speaking")
        else:
            QTimer.singleShot(200, self._maybe_idle)

    def _maybe_idle(self):
        if (self.worker and self.worker.isRunning()) or self.speaker.speaking:
            return
        if self.state in ("thinking", "speaking"):
            self.set_state("idle")
        if self.follow_up and self._relisten_ok and self.state == "idle" and self.listener:
            self._relisten_ok = False
            QTimer.singleShot(600, self._follow_up_listen)

    def _follow_up_listen(self):
        if self.state == "idle" and self.listener:
            self.listener.set_paused(False)
            self.listener.arm(7.0)
            self.set_state("listening")

    # ---- voice ----
    def start_listening(self):
        """Open the microphone. JARVIS reacts only after hearing its name."""
        if self.listener:
            return
        l = WakeListener()
        self.listener = l
        l.level.connect(self._on_level)
        l.partial.connect(self.you_said)
        l.status.connect(self.ai_caption)
        l.wake.connect(self._wake)
        l.heard.connect(self._heard)
        l.dismissed.connect(self._dismissed)
        l.failed.connect(self._voice_failed)
        l.ready.connect(lambda: self.ai_caption.emit("Say “Hey Jarvis” any time."))
        l.set_muted(not self.mic_on)
        l.start()

    def toggle_mic(self):
        self.mic_on = not self.mic_on
        if self.listener:
            self.listener.set_muted(not self.mic_on)
        if not self.mic_on and self.state == "listening":
            self.set_state("idle")
        self.mic_changed.emit(self.mic_on)

    def talk(self):
        """F2 or clicking the core: talk right now without saying the name."""
        if not self.listener or not self.mic_on:
            return
        self.speaker.stop()
        self.listener.set_paused(False)
        self.listener.arm(8.0)
        self._wake()

    def _on_level(self, v):
        if self.state == "listening":
            self.level.emit(v)

    def _wake(self):
        if self.state in ("thinking", "speaking"):
            return
        chime()
        self.set_state("listening")
        self.you_said.emit("")
        self.ai_caption.emit("")

    def _dismissed(self):
        if self.state == "listening":
            self.set_state("idle")
            self.you_said.emit("")
            self.ai_caption.emit("")

    def _heard(self, text):
        self.ai_caption.emit("")
        self.you_said.emit(text)
        self.handle(text)

    def _voice_failed(self, msg):
        self.ai_caption.emit(msg)
        self._add_html(f"<span style='color:#ff6b7a'>{htmllib.escape(msg)}</span>")

    # ---- handling a request ----
    def handle(self, text):
        t = re.sub(r"^(hey |ok |okay )?jarvis[,.!]?\s*", "", text.lower().strip())
        reply = self._command(t)
        self._add_html(f"<b style='color:#19e3ff'>You</b>  {htmllib.escape(text)}")
        if reply is not None:
            self._respond_local(reply)
        else:
            self.ask(text)

    def _command(self, t):
        if t in ("stop", "be quiet", "quiet", "stop talking", "cancel"):
            self.speaker.stop()
            return ""
        if t.startswith("open") or t.startswith("show"):
            for key, names in self.OPEN_NAMES.items():
                if any(n in t for n in names):
                    self.open_requested.emit(key)
                    return f"Opening {key}."
        if "scan" in t and ("file" in t or "computer" in t or "documents" in t):
            self.open_requested.emit("Files")
            return "Opening Files. Choose which folders I'm allowed to scan."
        if t in ("save that", "save this", "remember that", "save that to memory"):
            q, a, src = self.last
            if a:
                self.mem.save_note(q, a, src)
                return "Saved to your memory."
            return "There's nothing to save yet."
        if t in ("shut down", "power off", "goodbye", "shutdown"):
            QTimer.singleShot(2500, QApplication.quit)
            return "Shutting down. Goodbye."
        return None

    def _respond_local(self, reply):
        if not reply:
            self.set_state("idle")
            return
        self._add_html("<b style='color:#2a9bff'>JARVIS</b>")
        self._new_block()
        self._add_text(reply)
        self.ai_caption.emit(reply)
        if self.speak_replies and self.speaker.available:
            self.speaker.say(reply)
        else:
            self.set_state("idle")

    def ask(self, text):
        if self.worker and self.worker.isRunning():
            return
        self.set_state("thinking")
        hits = self.mem.search(text, 5)
        passages = [f"[{h['name']}, {self._unit(h['name'])} {h['page']}] {h['text']}" for h in hits]
        seen, self._sources = set(), []
        for h in hits:
            key = (h["name"], h["page"])
            if key not in seen:
                seen.add(key)
                self._sources.append(f"{h['name']} {self._unit(h['name'])} {h['page']}")
        self._add_html("<b style='color:#2a9bff'>JARVIS</b>")
        self._new_block()
        self._reply, self._sentence, self._error = "", "", False
        self.ai_caption.emit("")
        w = AskWorker(self.provider, text, passages)
        w.token.connect(self._token)
        w.failed.connect(self._failed)
        w.finished.connect(lambda q=text: self._ask_done(q))
        self.worker = w
        w.start()

    @staticmethod
    def _unit(name):
        n = name.lower()
        return "slide" if n.endswith(".pptx") else ("page" if n.endswith(".pdf") else "part")

    def _token(self, t):
        self._reply += t
        self._add_text(t)
        self.ai_caption.emit(self._reply[-280:])
        if self.speak_replies and self.speaker.available:
            self._sentence += t
            while True:
                m = re.search(r"[.!?]\s", self._sentence)
                if not m:
                    break
                self.speaker.queue(self._sentence[:m.end()])
                self._sentence = self._sentence[m.end():]

    def _failed(self, msg):
        self._error = True
        self._relisten_ok = False
        self._add_html(f"<span style='color:#ff6b7a'>{htmllib.escape(msg)}</span>")
        self.ai_caption.emit(msg)
        self.set_state("idle")

    def _ask_done(self, question):
        if self._error:
            return
        if self.speak_replies and self.speaker.available:
            self.speaker.queue(self._sentence)
            self.speaker.flush()
        self._sentence = ""
        src = "; ".join(self._sources)
        if self._sources:
            self._add_html(f"<span style='color:{DIM}'>From your files: {htmllib.escape(src)}</span>")
        self.last = (question, self._reply, src)
        self._relisten_ok = True
        QTimer.singleShot(400, self._maybe_idle)

    # ---- scanning ----
    def scan(self, paths):
        if self.scanner and self.scanner.isRunning():
            return
        self.scanner = ScanWorker(self.mem, [p for p in paths if p])
        self.scanner.progress.connect(self.scan_progress)
        self.scanner.done.connect(self._scan_finished)
        self.scanner.start()

    def stop_scan(self):
        if self.scanner and self.scanner.isRunning():
            self.scanner.stop()

    def _scan_finished(self, msg):
        self.scan_done.emit(msg)
        self.ai_caption.emit(msg)
        if self.speak_replies and self.speaker.available:
            self.speaker.say(msg)

    def shutdown(self):
        self.speaker.shutdown()
        for t in (self.listener, self.scanner):
            if t and t.isRunning():
                t.stop()
                t.wait(1500)
        if self.worker and self.worker.isRunning():
            self.worker.wait(1500)


# ------------------------------------------------------------ app views ----
def dim_label(text):
    l = QLabel(text)
    l.setWordWrap(True)
    l.setStyleSheet(f"color:{DIM};")
    return l


def assistant_view(brain):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    chat = QTextEdit()
    chat.setReadOnly(True)
    chat.setDocument(brain.doc)
    sb = chat.verticalScrollBar()
    sb.rangeChanged.connect(lambda lo, hi: sb.setValue(hi))
    row = QHBoxLayout()
    entry = QLineEdit()
    entry.setPlaceholderText("Type a question, or use your voice")
    mic = HudButton("Talk")
    mic.clicked.connect(brain.talk)
    row.addWidget(entry, 1)
    row.addWidget(mic)

    def send():
        q = entry.text().strip()
        if q:
            entry.clear()
            brain.handle(q)

    entry.returnPressed.connect(send)
    lay.addWidget(chat, 1)
    lay.addLayout(row)
    return w


class FilesView(QWidget):
    def __init__(self, brain):
        super().__init__()
        self.brain = brain
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(dim_label("JARVIS only reads folders you choose here. Files that look like schoolwork "
                                "are remembered and everything else is ignored. Files that are online-only "
                                "in OneDrive are skipped so nothing gets downloaded."))
        row = QHBoxLayout()
        for label, loc in (("Documents", QStandardPaths.DocumentsLocation),
                           ("Desktop", QStandardPaths.DesktopLocation),
                           ("Downloads", QStandardPaths.DownloadLocation)):
            b = HudButton(f"Scan {label}")
            b.clicked.connect(lambda _=False, l=loc: brain.scan([QStandardPaths.writableLocation(l)]))
            row.addWidget(b)
        add = HudButton("Add folder")
        add.clicked.connect(self.add_folder)
        stop = HudButton("Stop")
        stop.clicked.connect(brain.stop_scan)
        row.addWidget(add)
        row.addWidget(stop)
        lay.addLayout(row)
        self.progress = QLabel("")
        self.progress.setStyleSheet("color:#ffb032;")
        self.progress.setWordWrap(True)
        lay.addWidget(self.progress)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Remembered study files (double-click to open)")
        self.tree.itemDoubleClicked.connect(self.open_item)
        lay.addWidget(self.tree, 1)
        forget = HudButton("Forget selected file")
        forget.clicked.connect(self.forget)
        lay.addWidget(forget)
        brain.scan_progress.connect(self.progress.setText)
        brain.scan_done.connect(self.refresh)
        self.refresh()

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder for JARVIS to scan")
        if d:
            self.brain.scan([d])

    def refresh(self, *_):
        self.tree.clear()
        self.progress.setText("")
        groups = {}
        for doc_id, name, subject, path in self.brain.mem.list_documents():
            if subject not in groups:
                groups[subject] = QTreeWidgetItem([subject])
                self.tree.addTopLevelItem(groups[subject])
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.UserRole, (doc_id, path))
            item.setToolTip(0, path)
            groups[subject].addChild(item)
        self.tree.expandAll()

    def open_item(self, item, _col):
        data = item.data(0, Qt.UserRole)
        if data:
            QDesktopServices.openUrl(QUrl.fromLocalFile(data[1]))

    def forget(self):
        item = self.tree.currentItem()
        data = item.data(0, Qt.UserRole) if item else None
        if data:
            self.brain.mem.forget(data[0])
            self.refresh()


class MemoryView(QWidget):
    def __init__(self, brain):
        super().__init__()
        self.brain = brain
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your files and saved answers")
        self.search.returnPressed.connect(self.run)
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        lay.addWidget(self.search)
        lay.addWidget(self.out, 1)
        self.run()

    def run(self):
        q = self.search.text().strip()
        parts = []
        for h in (self.brain.mem.search(q, 8) if q else []):
            unit = Brain._unit(h["name"])
            parts.append(f"<p><b style='color:#19e3ff'>{htmllib.escape(h['name'])}</b> "
                         f"<span style='color:{DIM}'>{h['subject']}, {unit} {h['page']}</span><br>"
                         f"{htmllib.escape(h['text'][:400])}</p>")
        for question, answer, sources, created in self.brain.mem.search_notes(q, 10):
            parts.append(f"<p><b style='color:#2a9bff'>Saved answer</b> "
                         f"<span style='color:{DIM}'>{created}</span><br>"
                         f"<i>{htmllib.escape(question)}</i><br>{htmllib.escape(answer)}</p>")
        if not parts:
            parts = [f"<span style='color:{DIM}'>Nothing found. Say “save that” after an answer to keep it "
                     f"here, or scan your study folders in Files.</span>"]
        self.out.setHtml("".join(parts))


def settings_view(brain, orb):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)

    speak = QCheckBox("Speak replies out loud", checked=brain.speak_replies)
    speak.toggled.connect(lambda v: setattr(brain, "speak_replies", v))
    free = QCheckBox("Follow-up window: after a reply, keep listening for 7 seconds without the wake word",
                     checked=brain.follow_up)
    free.toggled.connect(lambda v: setattr(brain, "follow_up", v))
    anim = QCheckBox("Animate the core", checked=orb.animated)
    anim.toggled.connect(lambda v: setattr(orb, "animated", v))
    for c in (speak, free, anim):
        lay.addWidget(c)

    nat = QCheckBox("Natural voice (needs internet; the reply text is sent to Microsoft's speech service)",
                    checked=brain.speaker.natural)
    nat.toggled.connect(lambda v: setattr(brain.speaker, "natural", v))
    if not brain.speaker.natural_available:
        nat.setChecked(False)
        nat.setEnabled(False)
        nat.setText("Natural voice unavailable. Run: py -m pip install edge-tts")
    lay.addWidget(nat)
    voice = QComboBox()
    for label, v in Speaker.VOICES:
        voice.addItem(label, v)
    voice.setCurrentIndex(max(0, voice.findData(brain.speaker.voice)))
    voice.currentIndexChanged.connect(lambda i: setattr(brain.speaker, "voice", voice.itemData(i)))
    lay.addWidget(voice)
    test = HudButton("Hear this voice")
    test.clicked.connect(lambda: brain.speaker.say("Good evening. I'm JARVIS. How can I help?"))
    lay.addWidget(test)

    lay.addWidget(dim_label("Language model (must be installed in Ollama)"))
    model = QLineEdit(brain.provider.model)
    model.editingFinished.connect(lambda: setattr(brain.provider, "model", model.text().strip() or "llama3.2"))
    lay.addWidget(model)

    lay.addWidget(dim_label("The microphone stays on so JARVIS can hear its name. Speech recognition runs "
                            "on this computer, nothing is recorded, and anything said without "
                            "“Jarvis” is discarded. Use the mic button in the top bar to turn it off. "
                            "Your files are read only from folders you choose, and "
                            "everything is stored on this computer."))
    wipe = HudButton("Forget everything JARVIS remembers")

    def do_wipe():
        if QMessageBox.question(w, "Forget everything",
                                "Delete all remembered files and saved answers?") == QMessageBox.Yes:
            brain.mem.wipe()

    wipe.clicked.connect(do_wipe)
    lay.addWidget(wipe)
    lay.addStretch()
    return w


# ------------------------------------------------------------- desktop ----
class Workspace(QWidget):
    def __init__(self, brain):
        super().__init__()
        self.brain = brain
        self.orb = Orb(self)
        self.orb.clicked.connect(brain.talk)
        brain.state_changed.connect(self.orb.set_state)
        brain.level.connect(self.orb.set_level)

        self.study = StudyPanel(brain, self)
        self.clock = ClockPanel(self)
        self.monitor = MonitorPanel(self)
        for w in (self.study, self.clock, self.monitor):
            w.setFixedWidth(250)
            w.adjustSize()

        self.you = QLabel("", self)
        self.you.setAlignment(Qt.AlignCenter)
        self.you.setWordWrap(True)
        self.you.setFont(hud_font(13))
        self.you.setStyleSheet(f"color:{DIM};")
        self.ai = QLabel("", self)
        self.ai.setAlignment(Qt.AlignCenter)
        self.ai.setWordWrap(True)
        self.ai.setFont(hud_font(15))
        brain.you_said.connect(lambda t: self.you.setText(f"“{t}”" if t else ""))
        brain.ai_caption.connect(self.ai.setText)
        brain.open_requested.connect(self.open_app)

        self.windows = {}
        self._cascade = 0
        self._bg = None
        QApplication.instance().focusChanged.connect(self._focus)

    def _focus(self, _old, new):
        w = new
        while w is not None:
            if isinstance(w, FloatingWindow):
                w.raise_()
                return
            w = w.parentWidget()

    def _render_bg(self):
        pm = QPixmap(self.size())
        p = QPainter(pm)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#061326"))
        g.setColorAt(1, QColor("#030811"))
        p.fillRect(pm.rect(), g)
        p.setPen(QPen(QColor(25, 227, 255, 12), 1))
        for x in range(0, self.width(), 48):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 48):
            p.drawLine(0, y, self.width(), y)
        p.setPen(QPen(QColor(25, 227, 255, 28), 1))
        cx, cy = self.width() / 2, self.height() * 0.45
        p.drawLine(QPointF(0, cy), QPointF(self.width(), cy))
        p.drawLine(QPointF(cx, 0), QPointF(cx, self.height()))
        p.setPen(QColor(DIM))
        p.setFont(hud_font(9.5))
        p.drawText(QRectF(0, self.height() - 30, self.width(), 20), Qt.AlignCenter,
                   "Say “Hey Jarvis”, or click the core to talk")
        p.end()
        return pm

    def paintEvent(self, _):
        if self._bg is None or self._bg.size() != self.size():
            self._bg = self._render_bg()
        QPainter(self).drawPixmap(0, 0, self._bg)

    def resizeEvent(self, _):
        s = int(min(self.width(), self.height()) * 0.82)
        cy = int(self.height() * 0.45)
        self.orb.setGeometry(int(self.width() / 2 - s / 2), int(cy - s / 2), s, s)
        self.study.move(24, 24)
        self.clock.move(self.width() - 274, 24)
        self.monitor.move(self.width() - 274, 24 + self.clock.height() + 14)
        cap_w = max(300, min(720, self.width() - 620))
        x = int((self.width() - cap_w) / 2)
        y = int(cy + s * 0.46 + 4)
        self.you.setGeometry(x, y, cap_w, 26)
        self.ai.setGeometry(x, y + 28, cap_w, 80)
        self._bg = None

    def open_app(self, key):
        if key in self.windows:
            self.windows[key].raise_()
            return
        builders = {
            "Assistant": ("AI Study Assistant", lambda: assistant_view(self.brain)),
            "Files": ("Study Files", lambda: FilesView(self.brain)),
            "Memory": ("Academic Memory", lambda: MemoryView(self.brain)),
            "Settings": ("System Settings", lambda: settings_view(self.brain, self.orb)),
        }
        title, build = builders[key]
        win = FloatingWindow(key, title, build(), self)
        win.resize(560, 420)
        off = 36 * (self._cascade % 6)
        win.move(int(self.width() / 2 - 280) + off - 100, 90 + off)
        self._cascade += 1
        win.closed.connect(lambda k: self.windows.pop(k, None))
        win.show()
        win.raise_()
        self.windows[key] = win


class TopBar(QWidget):
    def __init__(self, brain):
        super().__init__()
        self.setFixedHeight(48)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 10, 0)
        lay.setSpacing(10)
        name = QLabel("J.A.R.V.I.S")
        name.setFont(hud_font(14, True, 4))
        name.setStyleSheet("color:#19e3ff;")
        self.status = QLabel()
        self.status.setFont(hud_font(10.5))
        self.mic = HudButton("Mic on")
        self.mic.clicked.connect(brain.toggle_mic)
        brain.mic_changed.connect(self._mic_changed)
        self.state = "idle"
        self.mic_on = True
        self.clock = QLabel()
        self.clock.setFont(hud_font(11))
        off = HudButton("Shut down")
        off.clicked.connect(QApplication.quit)
        lay.addWidget(name)
        lay.addStretch()
        lay.addWidget(self.status)
        lay.addStretch()
        lay.addWidget(self.mic)
        lay.addWidget(self.clock)
        lay.addWidget(off)
        brain.state_changed.connect(self.set_state)
        self.set_state("idle")
        QTimer(self, interval=1000, timeout=self.tick).start()
        self.tick()

    def _mic_changed(self, on):
        self.mic_on = on
        self.mic.setText("Mic on" if on else "Mic off")
        self.set_state(self.state)

    def set_state(self, s):
        self.state = s
        r, g, b = STATE_COLOR[s]
        text = STATE_TEXT[s] if self.mic_on else "Microphone off"
        self.status.setText(f"●  {text}")
        self.status.setStyleSheet(f"color: rgb({r},{g},{b});" if self.mic_on else f"color:{DIM};")

    def tick(self):
        self.clock.setText(datetime.now().strftime("%H:%M"))

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(3, 9, 18, 245))
        p.setPen(QPen(QColor(25, 227, 255, 70), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.setPen(QPen(CYAN, 2))
        p.drawLine(0, self.height() - 1, 160, self.height() - 1)
        p.drawLine(self.width() - 160, self.height() - 1, self.width(), self.height() - 1)


class Dock(QWidget):
    def __init__(self, workspace):
        super().__init__()
        self.setFixedWidth(100)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 18, 12, 18)
        lay.setSpacing(8)
        for key in ("Assistant", "Files", "Memory", "Settings"):
            b = DockButton(key)
            b.clicked.connect(lambda _=False, k=key: workspace.open_app(k))
            lay.addWidget(b)
        lay.addStretch()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(3, 9, 18, 245))
        p.setPen(QPen(QColor(25, 227, 255, 70), 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


class Desktop(QWidget):
    def __init__(self, brain):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(TopBar(brain))
        row = QHBoxLayout()
        row.setSpacing(0)
        self.workspace = Workspace(brain)
        row.addWidget(Dock(self.workspace))
        row.addWidget(self.workspace, 1)
        root.addLayout(row, 1)


class Shell(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JARVIS")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.brain = Brain()
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.boot = BootScreen()
        self.desktop = Desktop(self.brain)
        self.stack.addWidget(self.boot)
        self.stack.addWidget(self.desktop)
        self.boot.finished.connect(self._booted)
        QShortcut(QKeySequence("F2"), self, activated=self.brain.talk)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_full)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=QApplication.quit)
        QApplication.instance().aboutToQuit.connect(self.brain.shutdown)

    def _booted(self):
        self.stack.setCurrentWidget(self.desktop)
        self.brain.start_listening()
        h = datetime.now().hour
        greeting = "Good morning." if h < 12 else ("Good afternoon." if h < 18 else "Good evening.")
        msg = f"{greeting} JARVIS is online."
        self.brain.ai_caption.emit(msg)
        if self.brain.speak_replies and self.brain.speaker.available:
            QTimer.singleShot(600, lambda: self.brain.speaker.say(msg))

    def toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def run(self):
        self.showFullScreen()
        self.boot.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    shell = Shell()
    shell.run()
    sys.exit(app.exec())
