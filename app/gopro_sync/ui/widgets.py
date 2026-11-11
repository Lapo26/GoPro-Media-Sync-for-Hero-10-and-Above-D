"""Controles propios: avisos, interruptores, botones de progreso, insignias."""
import math
import os
import traceback
from datetime import datetime
from PySide6.QtCore import Property, QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import QFileDialog, QFrame, QGraphicsOpacityEffect, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget
from ..env import _safe_local_dir
from ..i18n import _LANGUAGE_LABELS
from ..sound import SoundManager
from ..ui.graphics import cover_scaled_pixmap, screen_dpr

# --- [ UI KOMPONENTEN ] ---

class CameraImageView(QWidget):
    """Foto/ilustracion de la camara (modelos sin video propio, p. ej. HERO5),
    recortada tipo "cover" con esquinas redondeadas y nitida en HiDPI."""

    def __init__(self, radius=24, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._source = QPixmap()
        self._scaled = None

    def set_image(self, path):
        self._source = QPixmap(path) if path and os.path.exists(path) else QPixmap()
        self._scaled = None
        self.update()

    def resizeEvent(self, event):
        self._scaled = None
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor(20, 20, 26))
        if not self._source.isNull():
            if self._scaled is None:
                self._scaled = cover_scaled_pixmap(self._source, self.size(), screen_dpr(self))
            painter.drawPixmap(0, 0, self._scaled)


class RoundedVideoContainer(QWidget):
    """
    Neue Implementierung: rendert Video über QGraphicsVideoItem in einer QGraphicsView.
    Die View bekommt eine abgerundete Maske, sodass das Video exakt runde Ecken hat.
    """
    def __init__(self, parent=None, radius=20):
        super().__init__(parent)
        self.radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setContentsMargins(0, 0, 0, 0)

        # GraphicsView / Scene / VideoItem
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setStyleSheet("background: transparent; border: none;")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

        # QGraphicsVideoItem zum Rendern des Videos
        self.video_item = QGraphicsVideoItem()
        self.video_item.setSize(self.size())
        self.scene.addItem(self.video_item)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        # --- Freeze-Frame-Overlay -------------------------------------------
        # Liegt UEBER der View und zeigt (wenn aktiviert) einen echten
        # Screenshot des letzten Videoframes. Damit ist es egal, ob das
        # Player-Backend beim Pausieren ganz am Ende manchmal kurz einen
        # grauen/leeren Frame anzeigt - der wird dann einfach verdeckt.
        self.freeze_label = QLabel(self)
        self.freeze_label.setScaledContents(True)
        self.freeze_label.setStyleSheet("background: transparent; border: none;")
        self.freeze_label.hide()

    def show_freeze_frame(self, pixmap):
        """Legt einen Screenshot ueber das Video (siehe Klassenkommentar)."""
        if pixmap is None or pixmap.isNull():
            return
        self.freeze_label.setPixmap(pixmap)
        self.freeze_label.setGeometry(0, 0, self.width(), self.height())
        try:
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width(), self.height(), self.radius, self.radius)
            self.freeze_label.setMask(QRegion(path.toFillPolygon().toPolygon()))
        except Exception:
            pass
        self.freeze_label.raise_()
        self.freeze_label.show()

    def hide_freeze_frame(self):
        self.freeze_label.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()

        # View und Scene anpassen
        self.view.setGeometry(0, 0, w, h)
        self.scene.setSceneRect(0, 0, w, h)

        # VideoItem auf die Scene-Größe setzen (Video füllt den Bereich)
        try:
            self.video_item.setSize(self.scene.sceneRect().size())
            self.video_item.setPos(0, 0)
        except Exception:
            pass

        # Abgerundete Maske für die gesamte View/Widget
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, self.radius, self.radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        try:
            self.setMask(region)
        except Exception:
            pass
        try:
            self.view.viewport().setMask(region)
        except Exception:
            pass
        if self.freeze_label.isVisible():
            self.freeze_label.setGeometry(0, 0, w, h)
            try:
                self.freeze_label.setMask(region)
            except Exception:
                pass

    def paintEvent(self, event):
        # Leichtes Hintergrund-Fill, damit die Ecken sauber aussehen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        painter.fillPath(path, QColor(20, 20, 30))
        painter.end()


class VideoFrameView(QWidget):
    """Zeigt Videoframes an, die per QVideoSink hereinkommen - entweder ganz
    normal ("flat", flächenfüllend/letterboxed) oder als 360-Grad-Pan-Ansicht
    ("360"): dabei wird nur ein 16:9-Ausschnitt aus dem equirektangulären
    Frame gezeigt, durch den man per Ziehen (Maus) waagerecht (umlaufend,
    da ein 360-Video horizontal nahtlos ist) und senkrecht (begrenzt) scrollen
    kann. Mausrad = Hineinzoomen (schmaleres Sichtfeld, mehr Spielraum zum
    senkrechten Schwenken).

    Wird sowohl im normalen Video-Player als auch im Verbindungs-Vorschau-
    Bereich (fuer GoPro MAX / MAX 2) verwendet - EIN Widget, zwei Modi.
    """

    ZOOM_MIN = 1.0
    ZOOM_MAX = 4.0

    def __init__(self, radius=0, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.mode = "flat"          # "flat" oder "360"
        self._frame = None          # aktuelles QImage (volle Aufloesung)
        self._pan_x = 0.5           # 0..1, umlaufend (wrap)
        self._pan_y = 0.5           # 0..1, wird je nach Zoom geclamped
        self._zoom = self.ZOOM_MIN
        self._dragging = False
        self._drag_last = QPointF()
        self.setMinimumSize(120, 68)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet("background: transparent;")

    # -- oeffentliche API -------------------------------------------------
    def set_mode(self, mode):
        if mode not in ("flat", "360"):
            return
        self.mode = mode
        self.setCursor(Qt.CursorShape.OpenHandCursor if mode == "360" else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_frame(self, qimage):
        if qimage is None or qimage.isNull():
            return
        self._frame = qimage
        self.update()

    def clear_frame(self):
        self._frame = None
        self.update()

    def capture_native(self):
        """Gibt das aktuell sichtbare Bild als QImage in voller Aufloesung
        zurueck (fuer den 'Als Foto speichern'-Button) - im 360-Modus genau
        der Ausschnitt, der gerade zu sehen ist, sonst der volle Frame."""
        if self._frame is None:
            return None
        if self.mode != "360":
            return QImage(self._frame)
        img_w, img_h = self._frame.width(), self._frame.height()
        crop_w, crop_h, left, top = self._crop_geometry(img_w, img_h)
        return self._composite_crop(self._frame, left, top, crop_w, crop_h)

    # -- interne Geometrie --------------------------------------------------
    def _crop_geometry(self, img_w, img_h):
        """Berechnet Breite/Hoehe/linke-obere-Ecke (in Bildpixeln) des
        aktuell sichtbaren Ausschnitts, passend zum Seitenverhaeltnis des
        Widgets (im Player standardmaessig 16:9)."""
        wgt_w = max(1, self.width())
        wgt_h = max(1, self.height())
        aspect = wgt_w / wgt_h

        # Bei ZOOM_MIN soll die volle Bildhoehe sichtbar sein (kompletter
        # Schwenkbereich oben/unten nur beim Reinzoomen).
        crop_h = img_h / self._zoom
        crop_w = crop_h * aspect
        if crop_w > img_w:
            crop_w = img_w
            crop_h = crop_w / aspect
        crop_h = min(crop_h, img_h)

        center_x = self._pan_x * img_w
        # vertikaler Schwenkbereich clampen, damit der Ausschnitt nie ueber
        # den oberen/unteren Bildrand hinausragt
        min_center_y = crop_h / 2
        max_center_y = img_h - crop_h / 2
        center_y = min_center_y + self._pan_y * max(0.0, max_center_y - min_center_y)
        center_y = max(min_center_y, min(max_center_y, center_y))

        left = center_x - crop_w / 2
        top = center_y - crop_h / 2
        return crop_w, crop_h, left, top

    def _composite_crop(self, img, left, top, crop_w, crop_h):
        """Schneidet ein crop_w x crop_h grosses Stueck aus img aus, beginnend
        bei (left, top) - left darf negativ sein oder ueber img.width()
        hinausgehen, dann wird nahtlos vom jeweils anderen Bildrand
        weitergezeichnet (die 360-Aufnahme ist horizontal umlaufend)."""
        img_w, img_h = img.width(), img.height()
        out = QImage(max(1, round(crop_w)), max(1, round(crop_h)), QImage.Format.Format_ARGB32)
        out.fill(Qt.GlobalColor.black)
        painter = QPainter(out)
        left_wrapped = left % img_w
        remaining = crop_w
        x_cursor = 0.0
        src_x = left_wrapped
        # In maximal 2 Stuecken zeichnen (Wrap-Around an der 0/img_w-Naht)
        for _ in range(2):
            if remaining <= 0:
                break
            seg_w = min(remaining, img_w - src_x)
            src_rect = QRectF(src_x, top, seg_w, crop_h)
            dst_rect = QRectF(x_cursor, 0, seg_w, crop_h)
            painter.drawImage(dst_rect, img, src_rect)
            x_cursor += seg_w
            remaining -= seg_w
            src_x = 0.0
        painter.end()
        return out

    # -- Zeichnen -------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.radius > 0:
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width(), self.height(), self.radius, self.radius)
            try:
                self.setMask(QRegion(path.toFillPolygon().toPolygon()))
            except Exception:
                pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(10, 10, 14))

        if self._frame is not None:
            if self.mode == "flat":
                dpr = screen_dpr(self)
                scaled = self._frame.scaled(int(rect.width() * dpr), int(rect.height() * dpr),
                                             Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                sz = scaled.deviceIndependentSize()
                x = (rect.width() - sz.width()) / 2
                y = (rect.height() - sz.height()) / 2
                painter.drawImage(QPointF(x, y), scaled)
            else:
                img_w, img_h = self._frame.width(), self._frame.height()
                crop_w, crop_h, left, top = self._crop_geometry(img_w, img_h)
                cropped = self._composite_crop(self._frame, left, top, crop_w, crop_h)
                painter.drawImage(rect, cropped)

        painter.end()

    # -- Maus-Interaktion (nur 360-Modus) --------------------------------
    def mousePressEvent(self, event):
        if self.mode == "360" and self._frame is not None:
            self._dragging = True
            self._drag_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self.mode == "360" and self._frame is not None:
            img_w, img_h = self._frame.width(), self._frame.height()
            crop_w, crop_h, _left, _top = self._crop_geometry(img_w, img_h)
            pos = event.position()
            dx = pos.x() - self._drag_last.x()
            dy = pos.y() - self._drag_last.y()
            self._drag_last = pos
            wgt_w = max(1, self.width())
            wgt_h = max(1, self.height())
            self._pan_x = (self._pan_x - (dx / wgt_w) * (crop_w / img_w)) % 1.0
            min_center_y = crop_h / 2
            max_center_y = img_h - crop_h / 2
            span = max(1.0, max_center_y - min_center_y)
            self._pan_y = min(1.0, max(0.0, self._pan_y - dy / wgt_h * (crop_h / span)))
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        if self.mode == "360":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self.mode == "360" and self._frame is not None:
            factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
            self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom * factor))
            self.update()
            event.accept()
        else:
            super().wheelEvent(event)


class Toast(QLabel):
    """Kleine, sich selbst wieder ausblendende Benachrichtigung (z.B. fuer
    'In Galerie gespeichert'), die oben ueber einem Parent-Widget schwebt."""
    def __init__(self, parent, text):
        super().__init__(text, parent)
        self.setStyleSheet("""
            QLabel {
                background: rgba(20, 20, 26, 0.92);
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                padding: 10px 18px;
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.14);
            }
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.adjustSize()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)

    def show_animated(self, duration_ms=1800):
        parent = self.parentWidget()
        if parent is not None:
            x = (parent.width() - self.width()) // 2
            y = parent.height() - self.height() - 28
            self.move(max(0, x), max(0, y))
        self.show()
        self.raise_()
        fade_in = QPropertyAnimation(self._effect, b"opacity", self)
        fade_in.setDuration(180)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_in_ref = fade_in

        def start_fade_out():
            fade_out = QPropertyAnimation(self._effect, b"opacity", self)
            fade_out.setDuration(400)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.finished.connect(self.deleteLater)
            fade_out.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._fade_out_ref = fade_out

        QTimer.singleShot(duration_ms, start_fade_out)


def show_toast(parent_widget, text):
    """Zeigt eine kurze 'Toast'-Benachrichtigung ueber parent_widget an."""
    try:
        toast = Toast(parent_widget, text)
        toast.show_animated()
    except Exception:
        traceback.print_exc()


def save_captured_photo(gallery_dir, qimage):
    """Speichert ein aufgenommenes Video-Frame (QImage) als PNG im Gallery-
    Zielordner (derselbe Ordner, den auch die Bilder-Galerie durchsucht -
    das Foto taucht also automatisch dort mit auf). Gibt den Zielpfad
    zurueck oder None bei einem Fehler."""
    try:
        os.makedirs(gallery_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"Frame_{stamp}.png"
        dest = os.path.join(gallery_dir, filename)
        counter = 1
        while os.path.exists(dest):
            filename = f"Frame_{stamp}_{counter}.png"
            dest = os.path.join(gallery_dir, filename)
            counter += 1
        if qimage.save(dest, "PNG"):
            return dest
        return None
    except Exception:
        traceback.print_exc()
        return None


class WaveProgressBar(QWidget):
    def __init__(self, show_battery_nub=False, parent=None):
        super().__init__(parent)
        self.show_battery_nub = show_battery_nub
        self.setFixedHeight(26)
        self.setMinimumWidth(70)
        self._value = -1
        self._display_value = 0.0
        self._color = QColor("#00cc66")
        
        self._anim = QPropertyAnimation(self, b"displayValue", self)
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self._wave_phase = 0.0
        self._wave_timer = QTimer(self)
        self._wave_timer.timeout.connect(self._update_wave)
        self._wave_timer.start(30)

    def _update_wave(self):
        self._wave_phase += 0.15
        if self._value >= 0:
            self.update()

    def _get_display_value(self): return self._display_value
    def _set_display_value(self, v):
        self._display_value = v
        self.update()
    displayValue = Property(float, _get_display_value, _set_display_value)

    def set_value_and_color(self, value, hex_color):
        try: val = int(value)
        except Exception: val = -1
        self._value = val
        self._color = QColor(hex_color)
        
        target = float(max(2, min(100, val))) if val >= 0 else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._display_value)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        
        if self.show_battery_nub:
            nub_w = max(4, h * 0.16)
            body_rect = QRectF(1, 2, w - nub_w - 3, h - 4)
            nub_rect = QRectF(body_rect.right() + 2, h * 0.28, nub_w, h * 0.44)
            painter.setPen(Qt.PenStyle.NoPen)
            nub_path = QPainterPath()
            nub_path.addRoundedRect(nub_rect, 2, 2)
            painter.fillPath(nub_path, QColor("#40404c"))
        else:
            body_rect = QRectF(1, 2, w - 2, h - 4)

        body_path = QPainterPath()
        body_path.addRoundedRect(body_rect, 7, 7)
        painter.fillPath(body_path, QColor(255, 255, 255, 15))
        painter.setPen(QColor(255, 255, 255, 30))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(body_path)

        if self._value < 0:
            painter.setPen(QColor("#ffffff")) 
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(body_rect, Qt.AlignmentFlag.AlignCenter, "--")
            return

        pad = 3
        inner = QRectF(body_rect.left() + pad, body_rect.top() + pad,
                        body_rect.width() - 2 * pad, body_rect.height() - 2 * pad)
        
        fill_w = max(0.0, inner.width() * (self._display_value / 100.0))
        
        painter.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(inner, 5, 5)
        painter.setClipPath(clip_path)

        wave_path = QPainterPath()
        wave_path.moveTo(inner.left(), inner.bottom())
        
        amplitude = 2.5
        for y in range(int(inner.height()), -1, -1):
            x = inner.left() + fill_w + math.sin((y * 0.3) + self._wave_phase) * amplitude
            wave_path.lineTo(x, inner.top() + y)
            
        wave_path.lineTo(inner.left(), inner.top())
        wave_path.closeSubpath()

        grad = QLinearGradient(inner.left(), 0, inner.left() + fill_w, 0)
        grad.setColorAt(0.0, self._color.darker(130))
        grad.setColorAt(1.0, self._color)
        
        painter.fillPath(wave_path, grad)
        painter.restore()

        # Draw percentage text centered over the bar
        try:
            pct_text = f"{int(round(self._display_value))}%"
        except Exception:
            pct_text = "--"
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, pct_text)


class SyncProgressButton(QWidget):
    """
    Kombinierter Button + Fortschrittsanzeige fuer den Sync.
    Im Ruhezustand sieht er aus wie ein normaler Button ("Sync starten").
    Waehrend der Uebertragung fuellt er sich wie die Akku/Speicher-Wellen-Bars
    (gleiche Wellen-Animation, siehe WaveProgressBar) und zeigt den Fortschritt in %.

    Zusaetzlich: rechter Bereich ist ein Dropdown-Pfeil, der ein Menu mit
    "Alles / Nur Bilder / Nur Videos synchronisieren" oeffnet (mode_chosen).
    Klick auf den restlichen (linken) Bereich des Buttons loest weiterhin
    ganz normal `clicked` aus (= "alles synchronisieren", bisheriges Verhalten).
    """
    clicked = Signal()
    mode_chosen = Signal(str)
    progress_requested = Signal()   # clic mientras sincroniza: reabrir la ventana de progreso

    CHEVRON_WIDTH = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(250, 55)
        self._clickable = False
        self._is_syncing = False
        self._idle_text = "Sync starten"
        self._menu_labels = {"all": "", "pictures": "", "videos": ""}   # los pone set_labels() con tr()
        self._accent_color = QColor("#00f2fe")

        self._display_progress = 0.0
        self._progress_anim = QPropertyAnimation(self, b"displayProgress", self)
        self._progress_anim.setDuration(350)
        self._progress_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._wave_phase = 0.0
        self._wave_timer = QTimer(self)
        self._wave_timer.timeout.connect(self._tick_wave)
        self._wave_timer.start(30)

        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _tick_wave(self):
        self._wave_phase += 0.15
        if self._is_syncing:
            self.update()

    def _get_display_progress(self): return self._display_progress
    def _set_display_progress(self, v):
        self._display_progress = v
        self.update()
    displayProgress = Property(float, _get_display_progress, _set_display_progress)

    def set_labels(self, idle_text, menu_all, menu_pictures, menu_videos):
        self._idle_text = idle_text
        self._menu_labels = {"all": menu_all, "pictures": menu_pictures, "videos": menu_videos}
        self.update()

    def set_theme_color(self, hex_color):
        self._accent_color = QColor(hex_color)
        self.update()

    @property
    def is_syncing(self):
        return self._is_syncing

    def set_idle(self, clickable):
        """Ruhezustand: entweder normaler klickbarer Button oder deaktiviert (kein GoPro verbunden)."""
        self._is_syncing = False
        self._clickable = clickable
        self._display_progress = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        self.update()

    def start_sync_visual(self):
        self._is_syncing = True
        self._clickable = False
        self._display_progress = 0.0
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_progress(self, percent, animate=True):
        """percent: 0-100, der Fortschritt der GERADE uebertragenen
        Einzeldatei (nicht mehr current/total ueber ALLE Dateien wie
        vorher) - so springt der Balken fuer jede neue Datei zurueck auf 0%
        und waechst dann synchron mit dem echten Kopierfortschritt, genau
        wie beim nativen Windows-Kopierdialog. animate=False setzt den
        Balken sofort (ohne die 350ms-Animation) - fuer den harten Reset
        auf 0%, wenn eine neue Datei beginnt."""
        pct = max(0.0, min(100.0, percent))
        if not animate:
            self._progress_anim.stop()
            self._display_progress = pct
            self.update()
            return
        self._progress_anim.stop()
        self._progress_anim.setStartValue(self._display_progress)
        self._progress_anim.setEndValue(pct)
        self._progress_anim.start()

    def _show_mode_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: #23232b;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                color: #ffffff;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 13px;
            }}
            QMenu::item:selected {{
                background-color: {self._accent_color.name()};
                color: #0d0d12;
            }}
        """)
        action_all = menu.addAction(self._menu_labels["all"])
        action_pics = menu.addAction(self._menu_labels["pictures"])
        action_vids = menu.addAction(self._menu_labels["videos"])
        chosen = menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
        if chosen is not None:
            SoundManager.play_click()
        if chosen == action_all:
            self.mode_chosen.emit("all")
        elif chosen == action_pics:
            self.mode_chosen.emit("pictures")
        elif chosen == action_vids:
            self.mode_chosen.emit("videos")

    def mousePressEvent(self, event):
        if self._is_syncing:
            self.progress_requested.emit()
        elif self._clickable:
            if event.position().x() >= self.width() - self.CHEVRON_WIDTH:
                SoundManager.play_click()
                self._show_mode_menu()
            else:
                SoundManager.play_click()
                self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        rect = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)

        if self._is_syncing:
            painter.fillPath(path, QColor(255, 255, 255, 15))
            painter.setPen(QColor(255, 255, 255, 30))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

            pad = 3
            inner = QRectF(rect.left() + pad, rect.top() + pad,
                            rect.width() - 2 * pad, rect.height() - 2 * pad)
            fill_w = max(0.0, inner.width() * (self._display_progress / 100.0))

            painter.save()
            clip_path = QPainterPath()
            clip_path.addRoundedRect(inner, 8, 8)
            painter.setClipPath(clip_path)

            def build_wave_path(phase, amplitude, freq, x_offset=0.0):
                """Baut eine weiche Wellen-Kontur per quadratischer Bezier-
                Interpolation zwischen Stuetzpunkten (statt harter
                Geradenstuecke) - wirkt deutlich fluessiger/glatter."""
                step = 4  # Abstand zwischen Stuetzpunkten in px
                points = []
                y = int(inner.height())
                while y >= 0:
                    x = inner.left() + fill_w + x_offset + math.sin((y * freq) + phase) * amplitude
                    points.append((x, inner.top() + y))
                    y -= step
                points.append((inner.left() + fill_w + x_offset + math.sin(phase) * amplitude, inner.top()))

                wp = QPainterPath()
                wp.moveTo(inner.left(), inner.bottom())
                wp.lineTo(*points[0])
                for i in range(1, len(points)):
                    prev_pt = points[i - 1]
                    cur_pt = points[i]
                    mid_x = (prev_pt[0] + cur_pt[0]) / 2
                    mid_y = (prev_pt[1] + cur_pt[1]) / 2
                    wp.quadTo(prev_pt[0], prev_pt[1], mid_x, mid_y)
                wp.lineTo(points[-1][0], points[-1][1])
                wp.lineTo(inner.left(), inner.top())
                wp.closeSubpath()
                return wp

            # Hintere Welle: leicht phasenversetzt, transparenter und etwas
            # groessere Amplitude - erzeugt den Eindruck von Tiefe (wie zwei
            # uebereinanderliegende Wasserschichten).
            back_wave = build_wave_path(self._wave_phase * 0.6 + 1.6, amplitude=3.2, freq=0.16, x_offset=-2)
            back_color = QColor(self._accent_color)
            back_color.setAlpha(90)
            painter.fillPath(back_wave, back_color)

            # Vordere (Haupt-)Welle: ruhigere, sanftere Frequenz als vorher.
            front_wave = build_wave_path(self._wave_phase, amplitude=2.2, freq=0.16)
            grad = QLinearGradient(inner.left(), 0, inner.left() + fill_w, 0)
            grad.setColorAt(0.0, self._accent_color.darker(130))
            grad.setColorAt(1.0, self._accent_color)
            painter.fillPath(front_wave, grad)
            painter.restore()

            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
            pct_text = f"{int(round(self._display_progress))}%"
            if getattr(self, "busy_label", ""):
                pct_text = f"{self.busy_label}  {pct_text}"
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, pct_text)
        else:
            if self._clickable:
                grad = QLinearGradient(0, 0, w, h)
                grad.setColorAt(0.0, self._accent_color)
                grad.setColorAt(1.0, self._accent_color.lighter(160))
                painter.fillPath(path, grad)
                painter.setPen(QColor("#0d0d12"))
            else:
                painter.fillPath(path, QColor(255, 255, 255, 12))
                painter.setPen(QColor(255, 255, 255, 90))
            painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))

            if self._clickable:
                text_rect = QRectF(0, 0, w - self.CHEVRON_WIDTH, h)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._idle_text)

                divider_x = w - self.CHEVRON_WIDTH
                painter.setPen(QColor(13, 13, 18, 70))
                painter.drawLine(QPointF(divider_x, h * 0.22), QPointF(divider_x, h * 0.78))

                # Kleiner Abwaerts-Pfeil (Dropdown-Indikator)
                cx = divider_x + self.CHEVRON_WIDTH / 2
                cy = h / 2
                caret = QPainterPath()
                caret.moveTo(cx - 5, cy - 2.5)
                caret.lineTo(cx, cy + 3.5)
                caret.lineTo(cx + 5, cy - 2.5)
                pen = QPen(QColor(13, 13, 18, 200), 2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(caret)
            else:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._idle_text)


# --- [ PLATZHALTER-ICONS: WLAN / BLUETOOTH / GPS ] ---
#
# Diese drei Werte werden von der GoPro (aktuell) nicht per USB/MTP
# ausgelesen - es gibt schlicht keine zuverlaessige Schnittstelle dafuer
# in diesem Sync-Tool. Damit die Geraete-Info-Karte trotzdem vollstaendig
# und "fertig" wirkt, zeigen wir hier bewusst dezente, inaktive
# Platzhalter-Symbole (statt Emojis, die je nach System unterschiedlich
# aussehen). Sobald echte Werte verfuegbar sind, kann set_active()
# genutzt werden, um das Icon farbig/aktiv darzustellen.

class ConnectivityBadge(QWidget):
    def __init__(self, icon_type, label_text, tooltip_text=None, parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.label_text = label_text
        self._active = False
        self._color = QColor("#00f2fe")
        self.setFixedSize(74, 64)
        self.setToolTip(tooltip_text or label_text)

    def set_active(self, active, hex_color=None):
        self._active = active
        if hex_color:
            self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, event):
        try:
            self._paint(event)
        except Exception:
            print("=== FEHLER beim Zeichnen von ConnectivityBadge ===")
            traceback.print_exc()

    def _paint(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = 38
        x = (self.width() - size) / 2
        y = 2
        icon_color = self._color if self._active else QColor(255, 255, 255, 130)
        bg_color = QColor(self._color) if self._active else QColor(255, 255, 255, 14)
        if self._active:
            bg_color.setAlpha(30)
        border_color = QColor(self._color) if self._active else QColor(255, 255, 255, 28)
        if self._active:
            border_color.setAlpha(160)
        else:
            border_color.setAlpha(28)

        painter.setPen(QPen(border_color, 1.4 if self._active else 1))
        painter.setBrush(bg_color)
        painter.drawEllipse(QRectF(x, y, size, size))

        cx, cy = x + size / 2, y + size / 2
        pen = QPen(icon_color, 2.1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.icon_type == "wifi":
            for r in (5, 9, 13):
                rect = QRectF(cx - r, cy + 3 - r, r * 2, r * 2)
                painter.drawArc(rect, 35 * 16, 110 * 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)
            painter.drawEllipse(QRectF(cx - 1.8, cy + 4.2, 3.6, 3.6))

        elif self.icon_type == "bluetooth":
            # Exaktes Bluetooth-Logo als gefuellte Flaeche (Koordinaten
            # 1:1 von der offiziellen Glyphen-Geometrie abgeleitet, mit
            # OddEven-Fuellregel fuer die beiden kleinen Dreiecks-Ausschnitte
            # in der Mitte). Das ergibt den sauberen, klassischen Look statt
            # der vorherigen, aus einzelnen Linien "zusammengestrichelten"
            # Variante.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)

            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            outer = [
                (5.40, -3.65), (0.55, -8.50), (-0.30, -8.50), (-0.30, -2.05),
                (-4.20, -5.95), (-5.40, -4.75), (-0.65, 0.00), (-5.40, 4.75),
                (-4.20, 5.95), (-0.30, 2.05), (-0.30, 8.50), (0.55, 8.50),
                (5.40, 3.65), (1.75, 0.00),
            ]
            path.moveTo(cx + outer[0][0], cy + outer[0][1])
            for dx, dy in outer[1:]:
                path.lineTo(cx + dx, cy + dy)
            path.closeSubpath()

            for tri in (
                [(1.40, -5.24), (3.00, -3.65), (1.40, -2.05)],
                [(3.00, 3.65), (1.40, 5.24), (1.40, 2.05)],
            ):
                path.moveTo(cx + tri[0][0], cy + tri[0][1])
                for dx, dy in tri[1:]:
                    path.lineTo(cx + dx, cy + dy)
                path.closeSubpath()

            painter.drawPath(path)

        elif self.icon_type == "gps":
            # Klassischer Standort-Pin (Teardrop) mit ausgestanztem Punkt in
            # der Mitte - wie das Ortungssymbol in den meisten Kamera-Apps.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)
            r = 7.2
            pin = QPainterPath()
            pin.addEllipse(QRectF(cx - r, cy - r - 3, r * 2, r * 2))
            tail = QPainterPath()
            tail.moveTo(cx - 5.2, cy + 1.6)
            tail.lineTo(cx, cy + 12)
            tail.lineTo(cx + 5.2, cy + 1.6)
            tail.closeSubpath()
            pin = pin.united(tail)
            painter.drawPath(pin)
            hole_color = bg_color if self._active else QColor(38, 40, 52, 255)
            painter.setBrush(hole_color)
            painter.drawEllipse(QRectF(cx - 2.6, cy - r - 3 + r - 2.6, 5.2, 5.2))

        painter.setPen(icon_color if self._active else QColor(255, 255, 255, 140))
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)
        text_rect = QRectF(0, y + size + 3, self.width(), 16)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter, self.label_text)


class SDCardIcon(QWidget):
    """Kleines, dezent gezeichnetes Speicherkarten-Symbol fuer die
    SPEICHERPLATZ-Beschriftung (ersetzt das 💾-Emoji durch ein zur
    restlichen Geraete-Info passendes Vektor-Icon)."""
    def __init__(self, color_hex="#ffffff", alpha=140, parent=None):
        super().__init__(parent)
        self._color = QColor(color_hex)
        self._alpha = alpha
        self.setFixedSize(16, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._color)
        c.setAlpha(self._alpha)

        w, h = 13, 17
        x, y = (self.width() - w) / 2, (self.height() - h) / 2
        cut = 4.5

        body = QPainterPath()
        body.moveTo(x, y + 3)
        body.lineTo(x, y + h - 2)
        body.quadTo(x, y + h, x + 2, y + h)
        body.lineTo(x + w - 2, y + h)
        body.quadTo(x + w, y + h, x + w, y + h - 2)
        body.lineTo(x + w, y + cut)
        body.lineTo(x + w - cut, y)
        body.lineTo(x + 2, y)
        body.quadTo(x, y, x, y + 2)
        body.closeSubpath()

        pen = QPen(c, 1.3)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(body)

        # Kontakt-Streifen (die kleinen "Pins" oben, typisch fuer SD-Karten)
        pin_pen = QPen(c, 1.1)
        painter.setPen(pin_pen)
        pin_top = y + 1.5
        pin_bottom = y + 5.5
        for i in range(3):
            px = x + 2.5 + i * 3.0
            painter.drawLine(QPointF(px, pin_top), QPointF(px, pin_bottom))


# --- [ MAIN WINDOW ] ---

# --- [ THEME-EINSTELLUNGEN: FARBE, HINTERGRUNDBILD, WEICHZEICHNER ] ---

class _ToggleTrackWidget(QWidget):
    """Nur die eigentliche Schalter-Flaeche (Track + Knopf) von ToggleSwitch -
    als eigenes Widget, damit der Knopf per QPropertyAnimation animiert
    werden kann."""
    def __init__(self, w, h, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self._w = w
        self._h = h
        self._knob_x = 3.0
        self._checked = False
        self._accent = QColor("#00f2fe")

    def _get_knob_x(self):
        return self._knob_x

    def _set_knob_x(self, v):
        self._knob_x = v
        self.update()

    knobX = Property(float, _get_knob_x, _set_knob_x)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self._accent) if self._checked else QColor(255, 255, 255, 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(0, 0, self._w, self._h, self._h / 2, self._h / 2)
        knob_d = self._h - 6
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(self._knob_x, 3, knob_d, knob_d))


class ToggleSwitch(QWidget):
    """Ersetzt QCheckBox in der ganzen App - absichtlich API-kompatibel
    (setText/text/isChecked/setChecked/toggled), damit bestehender Code nur
    minimal angepasst werden musste. Ein Umschalten spielt toggle_on.wav
    bzw. toggle_off.wav (statt des generischen Klick-Sounds) - dafuer
    mussten echte An/Aus-Schalter her statt Checkboxen."""
    toggled = Signal(bool)

    TRACK_W = 42
    TRACK_H = 24

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._checked = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._track = _ToggleTrackWidget(self.TRACK_W, self.TRACK_H)
        layout.addWidget(self._track)

        self.label = QLabel(text)
        self.label.setStyleSheet("color: rgba(255,255,255,0.75); font-size: 13px; background: transparent; border: none;")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        self._anim = QPropertyAnimation(self._track, b"knobX", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def setText(self, text):
        self.label.setText(text)

    def text(self):
        return self.label.text()

    def set_accent(self, hex_color):
        self._track._accent = QColor(hex_color)
        self._track.update()

    def isChecked(self):
        return self._checked

    def _target_x(self, checked):
        knob_d = self.TRACK_H - 6
        return float(self.TRACK_W - knob_d - 3) if checked else 3.0

    def setChecked(self, checked, animate=True):
        checked = bool(checked)
        self._checked = checked
        self._track._checked = checked
        target = self._target_x(checked)
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._track._knob_x)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._track._knob_x = target
        self._track.update()

    def changeEvent(self, event):
        # Deshabilitado = atenuado; antes se veia igual que uno activo.
        if event.type() == QEvent.Type.EnabledChange:
            effect = None
            if not self.isEnabled():
                effect = QGraphicsOpacityEffect(self)
                effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)
        super().changeEvent(event)

    def mousePressEvent(self, event):
        if self.isEnabled():
            new_state = not self._checked
            self.setChecked(new_state)
            SoundManager.play_toggle(new_state)
            self.toggled.emit(new_state)
        super().mousePressEvent(event)


class AvatarPreview(QWidget):
    """Rundes Profilbild-Vorschau-Widget - klickbar, um per Dateidialog ein
    neues Bild auszuwaehlen. Ohne gesetztes Bild wird ein dezenter
    Platzhalter (Initiale des Namens oder ein generisches Personen-Icon)
    gezeichnet."""
    changed = Signal(str)

    def __init__(self, size=88, accent="#00f2fe", parent=None):
        super().__init__(parent)
        self._size = size
        self._accent = QColor(accent)
        self._path = ""
        self._pixmap = None
        self._initial = ""
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_accent(self, hex_color):
        self._accent = QColor(hex_color)
        self.update()

    def set_initial(self, text):
        self._initial = (text or "").strip()[:1].upper()
        self.update()

    def set_image_path(self, path):
        self._path = path or ""
        if self._path and os.path.exists(self._path):
            pix = QPixmap(self._path)
            self._pixmap = pix if not pix.isNull() else None
        else:
            self._pixmap = None
        self.update()

    def mousePressEvent(self, event):
        path, _ = QFileDialog.getOpenFileName(
            self, "", _safe_local_dir(), "*.png *.jpg *.jpeg *.bmp *.webp",
        )
        if path:
            self.set_image_path(path)
            self.changed.emit(path)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._size

        clip_path = QPainterPath()
        clip_path.addEllipse(0, 0, r, r)
        painter.setClipPath(clip_path)

        if self._pixmap is not None:
            dpr = screen_dpr(self)
            scaled = self._pixmap.scaled(int(r * dpr), int(r * dpr), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            sz = scaled.deviceIndependentSize()
            x = int((sz.width() - r) // 2)
            y = int((sz.height() - r) // 2)
            painter.drawPixmap(-x, -y, scaled)
        else:
            # Platzhalter bewusst in Graustufen (statt Akzentfarbe) - wirkt
            # neutraler/dezenter, solange noch kein echtes Profilbild gewaehlt ist.
            grad = QLinearGradient(0, 0, r, r)
            grad.setColorAt(0.0, QColor(120, 122, 130))
            grad.setColorAt(1.0, QColor(60, 62, 70))
            painter.fillRect(0, 0, r, r, grad)
            painter.setPen(QColor(255, 255, 255, 235))
            if self._initial:
                font = QFont("Segoe UI", int(r * 0.36), QFont.Weight.Bold)
                painter.setFont(font)
                painter.drawText(QRectF(0, 0, r, r), Qt.AlignmentFlag.AlignCenter, self._initial)
            else:
                # Statt des "👤"-Emojis (das Windows IMMER in seinen eigenen
                # Farben - meist lila - rendert, egal welchen Stift man
                # setzt) hier ein selbst gezeichnetes, garantiert einfarbiges
                # Personen-Symbol: Kopf (Kreis) + Schultern (Bogen).
                painter.setBrush(QColor(255, 255, 255, 220))
                painter.setPen(Qt.PenStyle.NoPen)
                head_r = r * 0.15
                painter.drawEllipse(QRectF(r / 2 - head_r, r * 0.28 - head_r, head_r * 2, head_r * 2))
                shoulders = QPainterPath()
                shoulders.moveTo(r * 0.24, r * 0.82)
                shoulders.cubicTo(r * 0.24, r * 0.52, r * 0.76, r * 0.52, r * 0.76, r * 0.82)
                shoulders.closeSubpath()
                painter.drawPath(shoulders)

        painter.setClipping(False)
        pen = QPen(QColor(255, 255, 255, 60), 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(1, 1, r - 2, r - 2)


def _make_language_buttons(current, accent, on_pick):
    """Fila vertical de botones grandes para elegir idioma (asistente y dialogo)."""
    box = QVBoxLayout()
    box.setSpacing(10)
    buttons = {}
    for code, label, hint in _LANGUAGE_LABELS:
        b = QPushButton(label + "\n" + hint)
        b.setCheckable(True)
        b.setChecked(code == current)
        b.setMinimumHeight(58)
        b.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.06); color: #fff; font-size: 14px; font-weight: 700;"
            " border: 1px solid rgba(255,255,255,0.15); border-radius: 12px; padding: 10px 18px; text-align: left; }"
            " QPushButton:hover { background: rgba(255,255,255,0.12); }"
            f" QPushButton:checked {{ border: 2px solid {accent}; background: rgba(255,255,255,0.14); }}"
        )
        b.clicked.connect(lambda checked=False, c=code: on_pick(c))
        box.addWidget(b)
        buttons[code] = b
    return box, buttons


class ActivityNavButton(QPushButton):
    """Item "Actividad" del menu: mientras hay una copia en curso se rellena
    de fondo segun el progreso total y muestra el porcentaje."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._pct = None
        self._accent = QColor("#00f2fe")

    def set_progress(self, pct, accent=None):
        self._pct = pct
        if accent:
            self._accent = QColor(accent)
        self.update()

    def paintEvent(self, event):
        if self._pct is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.setClipPath(path)
            p.fillRect(r, QColor(255, 255, 255, 14))
            fill = QColor(self._accent)
            fill.setAlpha(70)
            p.fillRect(QRectF(r.left(), r.top(), r.width() * max(0.0, min(100.0, self._pct)) / 100.0, r.height()), fill)
            p.end()
        super().paintEvent(event)
        if self._pct is not None:
            p = QPainter(self)
            p.setPen(QColor(255, 255, 255, 220))
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            p.drawText(self.rect().adjusted(0, 0, -14, 0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self._pct:.0f}%")
            p.end()
