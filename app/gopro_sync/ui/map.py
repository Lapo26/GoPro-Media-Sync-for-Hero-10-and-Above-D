"""Mapa de OpenStreetMap con el recorrido GPS."""
import os
import tempfile
from PySide6.QtCore import QPointF, QRectF, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from ..env import FONT_STACK
from ..i18n import tr
from ..media import read_gopro_track, read_mp4_info
from ..workers import ScanThread
from ..updates import _http_get

class TileMapWidget(QWidget):
    """Mapa de OpenStreetMap con el recorrido: arrastrar para mover, rueda
    para acercar. Las teselas se guardan en disco para no volver a pedirlas."""
    tile_loaded = Signal(str)

    def __init__(self, points, accent, parent=None):
        super().__init__(parent)
        from concurrent.futures import ThreadPoolExecutor
        self.points = points
        self.accent = QColor(accent)
        self.tiles = {}
        self.pending = set()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.cache_dir = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "GoProSyncPro", "tiles")
        self.zoom = 3
        self.cx = self.cy = 0.0
        self._drag = None
        self.setMinimumSize(640, 420)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.tile_loaded.connect(lambda _k: self.update())
        self._fitted = False

    @staticmethod
    def _world(lat, lon, z):
        import math
        n = 256 * (2 ** z)
        lat = max(-85.0511, min(85.0511, lat))
        x = (lon + 180.0) / 360.0 * n
        y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
        return x, y

    def fit(self):
        if not self.points:
            return
        lats = [p[0] for p in self.points]
        lons = [p[1] for p in self.points]
        w, h = max(200, self.width() - 80), max(200, self.height() - 80)
        z = 17
        while z > 2:
            x1, y1 = self._world(max(lats), min(lons), z)
            x2, y2 = self._world(min(lats), max(lons), z)
            if abs(x2 - x1) <= w and abs(y2 - y1) <= h:
                break
            z -= 1
        self.zoom = z
        x1, y1 = self._world(max(lats), min(lons), z)
        x2, y2 = self._world(min(lats), max(lons), z)
        self.cx, self.cy = (x1 + x2) / 2, (y1 + y2) / 2

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._fitted:
            self._fitted = True
            self.fit()

    def _tile(self, z, x, y):
        key = f"{z}/{x}/{y}"
        if key in self.tiles:
            return self.tiles[key]
        path = os.path.join(self.cache_dir, str(z), str(x), f"{y}.png")
        if os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self.tiles[key] = pm
                return pm
        if key not in self.pending:
            self.pending.add(key)
            self.pool.submit(self._download, key, path)
        return None

    def _download(self, key, path):
        try:
            with _http_get(f"https://tile.openstreetmap.org/{key}.png", timeout=15) as r:
                data = r.read()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
            self.tile_loaded.emit(key)
        except Exception as e:
            print("[Mapa]", key, e)

    def paintEvent(self, event):
        import math
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#20232e"))
        z = self.zoom
        n = 2 ** z
        left = self.cx - self.width() / 2
        top = self.cy - self.height() / 2
        for tx in range(int(math.floor(left / 256)), int(math.floor((left + self.width()) / 256)) + 1):
            for ty in range(int(math.floor(top / 256)), int(math.floor((top + self.height()) / 256)) + 1):
                if ty < 0 or ty >= n:
                    continue
                pm = self._tile(z, tx % n, ty)
                if pm is not None:
                    painter.drawPixmap(QRectF(tx * 256 - left, ty * 256 - top, 256, 256), pm, QRectF(pm.rect()))
        if self.points:
            step = max(1, len(self.points) // 3000)
            path = QPainterPath()
            for i, pnt in enumerate(self.points[::step]):
                x, y = self._world(pnt[0], pnt[1], z)
                pt = QPointF(x - left, y - top)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            painter.setPen(QPen(QColor(0, 0, 0, 140), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            painter.setPen(QPen(self.accent, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            for pnt, color in ((self.points[0], "#2ecc71"), (self.points[-1], "#ff5c5c")):
                x, y = self._world(pnt[0], pnt[1], z)
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.setBrush(QColor(color))
                painter.drawEllipse(QPointF(x - left, y - top), 7, 7)
        # Atribucion obligatoria de OpenStreetMap
        text = "© OpenStreetMap contributors"
        f = painter.font()
        f.setPixelSize(11)
        painter.setFont(f)
        tw = painter.fontMetrics().horizontalAdvance(text) + 12
        r = QRectF(self.width() - tw - 4, self.height() - 20, tw, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawRoundedRect(r, 4, 4)
        painter.setPen(QColor("#333333"))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event):
        self._drag = (event.position(), self.cx, self.cy)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag:
            p0, cx, cy = self._drag
            d = event.position() - p0
            self.cx, self.cy = cx - d.x(), cy - d.y()
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event):
        step = 1 if event.angleDelta().y() > 0 else -1
        nz = max(2, min(18, self.zoom + step))
        if nz == self.zoom:
            return
        pos = event.position()
        wx = self.cx - self.width() / 2 + pos.x()
        wy = self.cy - self.height() / 2 + pos.y()
        f = 2 ** (nz - self.zoom)
        self.zoom = nz
        self.cx = wx * f - pos.x() + self.width() / 2
        self.cy = wy * f - pos.y() + self.height() / 2
        self.update()

    def shutdown(self):
        try:
            self.pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


class GpsMapDialog(QDialog):
    """Recorrido GPS de un video: mapa + distancia, velocidades y altura."""

    def __init__(self, parent, lang, path, accent):
        super().__init__(parent)
        self.lang, self.path, self.accent = lang, path, accent
        self.map = None
        self.setWindowTitle(tr(lang, "gps_title", name=os.path.basename(path)))
        self.resize(1000, 720)
        self.setStyleSheet(f"""
            QDialog {{ background: #1c1e29; }}
            QLabel {{ color: #fff; background: transparent; }}
            * {{ font-family: {FONT_STACK}; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
        """)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 18, 22, 16)
        self.lay.setSpacing(10)
        title = QLabel(tr(lang, "gps_title", name=os.path.basename(path)))
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        self.lbl_stats = QLabel(tr(lang, "gps_reading"))
        self.lbl_stats.setWordWrap(True)
        self.lbl_stats.setStyleSheet(f"font-size: 13px; color: {accent};")
        self.lay.addWidget(title)
        self.lay.addWidget(self.lbl_stats)
        self.map_slot = QVBoxLayout()
        self.lay.addLayout(self.map_slot, 1)
        foot = QHBoxLayout()
        self.lbl_hint = QLabel(tr(lang, "gps_hint"))
        self.lbl_hint.setStyleSheet("font-size: 11px; color: rgba(255,255,255,0.45);")
        self.btn_osm = QPushButton(tr(lang, "gps_open_osm"))
        self.btn_osm.hide()
        close = QPushButton(tr(lang, "close_btn2"))
        close.clicked.connect(self.accept)
        foot.addWidget(self.lbl_hint)
        foot.addStretch()
        foot.addWidget(self.btn_osm)
        foot.addWidget(close)
        self.lay.addLayout(foot)
        th = ScanThread(0, read_gopro_track, path)
        th.done.connect(self._on_points)
        th.start()

    def _on_points(self, _t, st):
        pts = st["points"] if st else None
        if not pts:
            self.lbl_stats.setText(tr(self.lang, "gps_none"))
            self.lbl_stats.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.7);")
            self.lbl_hint.hide()
            return
        duration = read_mp4_info(self.path).get("duration") or 0
        if st:
            dist = f"{st['dist'] / 1000:.1f} km" if st["dist"] >= 1000 else f"{st['dist']:.0f} m"
            mins = int(duration // 60)
            dur = f"{mins // 60} h {mins % 60:02d} min" if mins >= 60 else f"{mins} min"
            self.lbl_stats.setText(tr(self.lang, "gps_stats", dist=dist, vmax=f"{st['vmax']:.0f}", vavg=f"{st['vavg']:.0f}",
                                      amin=f"{st['amin']:.0f}", amax=f"{st['amax']:.0f}", dur=dur))
        self.map = TileMapWidget(pts, self.accent, self)
        self.map_slot.addWidget(self.map)
        lat, lon = pts[len(pts) // 2][0], pts[len(pts) // 2][1]
        self.btn_osm.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=14/{lat:.5f}/{lon:.5f}")))
        self.btn_osm.show()

    def done(self, r):
        if self.map is not None:
            self.map.shutdown()
        super().done(r)
