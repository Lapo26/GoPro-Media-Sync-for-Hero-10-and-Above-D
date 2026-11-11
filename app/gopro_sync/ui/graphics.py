"""Imagenes, iconos, sombras y estilos compartidos por la interfaz."""
import os
from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsPixmapItem, QGraphicsScene, QPushButton
from ..env import LOGO_FILENAMES, asset_path
from ..i18n import tr

_EMOJI_ICON_CACHE = {}


def emoji_icon(ch, px=18):
    """Emoji dibujado como icono con UNA sola fuente (Segoe UI Emoji): unos ms,
    sin la busqueda de fuentes de reserva."""
    try:
        dpr = max(1.0, QGuiApplication.primaryScreen().devicePixelRatio())
    except Exception:
        dpr = 1.0
    key = (ch, px, dpr)
    if key not in _EMOJI_ICON_CACHE:
        pm = QPixmap(int(px * dpr), int(px * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        f = QFont("Segoe UI Emoji")
        f.setPixelSize(int(px * 0.85))
        painter.setFont(f)
        painter.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, ch)
        painter.end()
        _EMOJI_ICON_CACHE[key] = QIcon(pm)
    return _EMOJI_ICON_CACHE[key]


NAV_ICONS = {"nav_sync": "\U0001F4F7", "nav_videos": "\U0001F3AC", "nav_pictures": "\U0001F4F8",
             "nav_activity": "\U0001F4CB", "nav_import": "\U0001F4C2", "nav_backup": "\U0001F4BE",
             "nav_settings": "\u2699"}


def set_nav_text(button, lang, key):
    """Texto del menu lateral + su emoji como icono."""
    button.setText(tr(lang, key))
    ch = NAV_ICONS.get(key)
    if ch:
        button.setIcon(emoji_icon(ch, 18))
        button.setIconSize(QSize(18, 18))


_APP_ICON = None


def load_app_icon():
    global _APP_ICON
    if _APP_ICON is not None:
        return _APP_ICON
    _APP_ICON = _load_app_icon_uncached()
    return _APP_ICON


def _load_app_icon_uncached():
    """Laedt das App-Icon aus dem assets-Ordner. Bevorzugt wird
    'app_icon.ico' (Windows nutzt ohnehin intern .ico-Dateien fuer
    Titelleiste/Taskleiste und diese enthalten meist mehrere Groessen fuer
    scharfe Darstellung), 'GoPro Sync.png' dient als Fallback, falls die
    .ico-Datei mal fehlen sollte. Gibt ein (ggf. leeres) QIcon zurueck -
    ist keine der beiden Dateien vorhanden, bleibt einfach das Standard-
    Icon von Qt/Windows sichtbar."""
    ico_path = asset_path("app_icon.ico")
    if os.path.exists(ico_path):
        return QIcon(ico_path)
    png_path = asset_path("GoPro Sync.png")
    if os.path.exists(png_path):
        return QIcon(png_path)
    return QIcon()


def find_logo_path(model):
    """Sucht die Modell-Logo-Datei (siehe LOGO_FILENAMES) im assets-Ordner.
    Ist zusaetzlich tolerant gegenueber Gross-/Kleinschreibung, falls der
    tatsaechliche Dateiname im Ordner nicht exakt so geschrieben ist wie
    in LOGO_FILENAMES hinterlegt (z.B. "hero 11 logo.png" statt
    "Hero 11 Logo.png"). Gibt None zurueck, wenn nichts gefunden wurde -
    dann greift der Text-Titel als Fallback (siehe _update_ui_state)."""
    filename = LOGO_FILENAMES.get(model)
    if not filename:
        return None
    exact = asset_path(filename)
    if os.path.exists(exact):
        return exact
    assets_dir = os.path.dirname(exact)
    try:
        for f in os.listdir(assets_dir):
            if f.lower() == filename.lower():
                return os.path.join(assets_dir, f)
    except Exception:
        pass
    print(f"[Logo] Keine Logo-Datei gefunden fuer Modell '{model}' - erwartet "
          f"'{filename}' im Ordner '{assets_dir}'. Es wird der Text-Titel angezeigt.")
    return None

def add_text_shadow(widget, blur=16, dx=0, dy=1, alpha=255):
    """Fuegt einem Text-/UI-Element (QLabel, QCheckBox, custom Widgets wie
    ConnectivityBadge/SDCardIcon, ...) einen kraeftigen, eng anliegenden
    dunklen "Halo" hinzu (kleiner Blur-Radius, fast kein Versatz, volle
    Deckkraft) - das wirkt auf unruhigen Fotohintergruenden wie ein
    Text-Outline und sorgt fuer deutlich mehr Kontrast als ein weicher,
    versetzter Streuschatten. Wird zentral von MainWindow.apply_text_shadows()
    ein- bzw. ausgeschaltet, je nachdem ob gerade ein eigenes Hintergrund-
    bild aktiv ist."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(dx, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


def add_drop_shadow(widget, blur_radius=25, offset_y=5, alpha=100, color_hex="#000000"):
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(blur_radius)
    c = QColor(color_hex)
    c.setAlpha(alpha)
    shadow.setColor(c)
    shadow.setOffset(0, offset_y)
    widget.setGraphicsEffect(shadow)


def screen_dpr(widget=None):
    """Escala de pantalla (1.0 = 100 %, 2.0 = 200 %). Con Windows escalado
    las imagenes deben generarse a resolucion real (logica x escala); si no,
    Qt las estira y se ven borrosas."""
    try:
        if widget is not None:
            return max(1.0, float(widget.devicePixelRatioF()))
        screens = QGuiApplication.screens()
        return max([1.0] + [float(sc.devicePixelRatio()) for sc in screens])
    except Exception:
        return 1.0


def hidpi_scaled_to_width(pixmap, logical_width, dpr=None):
    """Como QPixmap.scaledToWidth, pero nitido en pantallas escaladas."""
    dpr = dpr or screen_dpr()
    out = pixmap.scaledToWidth(max(1, int(round(logical_width * dpr))), Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def cover_scaled_pixmap(pixmap, target_size, dpr=1.0):
    """Skaliert ein QPixmap wie CSS 'background-size: cover': füllt die
    Zielgröße komplett aus (kein Verzerren), überschüssige Ränder werden
    mittig abgeschnitten."""
    if pixmap is None or pixmap.isNull() or target_size.width() <= 0 or target_size.height() <= 0:
        return QPixmap()
    tw, th = int(round(target_size.width() * dpr)), int(round(target_size.height() * dpr))
    scaled = pixmap.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - tw) // 2)
    y = max(0, (scaled.height() - th) // 2)
    out = scaled.copy(x, y, tw, th)
    out.setDevicePixelRatio(dpr)
    return out


def make_blurred_pixmap(pixmap, radius, max_dim=900):
    """Erzeugt EINMALIG eine weichgezeichnete Kopie von 'pixmap' (statt den
    Weichzeichner live als QGraphicsEffect auf dem sichtbaren, vollen
    Fenster-Label zu belassen).

    WICHTIG - Hintergrund des Fixes: Ein QGraphicsBlurEffect, das per
    setGraphicsEffect() direkt am (fenstergroßen!) Hintergrund-Label haengt,
    wird von Qt bei JEDEM einzelnen Repaint neu berechnet - also bei jeder
    Fenstergroessenaenderung, jedem Öffnen eines Dialogs, jeder Hover-Glow-
    Animation eines Buttons usw. Ein Weichzeichner-Durchlauf ueber ein
    komplettes (ggf. hochaufloesendes) Foto in Fenstergroesse ist auf dem
    GUI-Thread aber vergleichsweise teuer - genau DAS hat die Oberflaeche
    beim Setzen/Anzeigen eines eigenen Hintergrundbilds (v.a. mit Weich-
    zeichner > 0) immer wieder komplett einfrieren lassen.

    Der Fix: Wir berechnen den Weichzeichner nur EINMAL (wenn sich Bild
    oder Blur-Wert aendern) auf einer verkleinerten Kopie (max_dim) und
    liefern ein fertiges, bereits weichgezeichnetes QPixmap zurueck. Das
    Zuschneiden/Hochskalieren auf die aktuelle Fenstergroesse (siehe
    cover_scaled_pixmap) bleibt danach ein reines - guenstiges - Skalieren
    ohne erneuten Weichzeichner-Durchlauf."""
    if pixmap is None or pixmap.isNull():
        return QPixmap()
    if radius <= 0:
        return pixmap

    # Zuerst verkleinern: der Weichzeichner-Aufwand haengt stark von der
    # Pixelanzahl ab, und ein verwaschener Hintergrund verliert durch das
    # spaetere Wieder-Hochskalieren keine sichtbaren Details.
    small = pixmap
    if max(pixmap.width(), pixmap.height()) > max_dim:
        small = pixmap.scaled(max_dim, max_dim, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    blur = QGraphicsBlurEffect()
    blur.setBlurRadius(radius)
    item.setGraphicsEffect(blur)
    scene.addItem(item)

    result = QPixmap(small.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scene.render(painter, QRectF(0, 0, small.width(), small.height()),
                 QRectF(0, 0, small.width(), small.height()))
    painter.end()
    return result


def trim_transparent_margins(pixmap):
    """Schneidet komplett transparente Ränder rund um ein Logo/PNG ab, damit
    kein unnötiger Leerraum (der wie ein "Rand" wirken kann) übrig bleibt.
    Hat das Bild keinen Alphakanal, wird es unverändert zurückgegeben."""
    if pixmap.isNull():
        return pixmap
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    if not image.hasAlphaChannel():
        return pixmap

    w, h = image.width(), image.height()
    if w * h > 2_000_000:
        # Sehr große Bilder ueberspringen wir hier - das ist fuer ein
        # kleines Sidebar-Logo ohnehin nicht der erwartete Anwendungsfall
        # und wuerde die Pixel-fuer-Pixel-Suche unnoetig verlangsamen.
        return pixmap
    # Buscar cada borde desde afuera y PARAR en el primer pixel visible
    # (antes se recorria la imagen entera en Python: ~1,7 s con el logo).
    def opaque(x, y):
        return (image.pixel(x, y) >> 24) & 0xFF > 8   # Alpha > ~3%

    def row_has(y):
        return any(opaque(x, y) for x in range(w))

    def col_has(x, y0, y1):
        return any(opaque(x, y) for y in range(y0, y1 + 1))

    min_y = next((y for y in range(h) if row_has(y)), None)
    if min_y is None:
        return pixmap  # komplett transparentes Bild - nichts zu tun
    max_y = next(y for y in range(h - 1, -1, -1) if row_has(y))
    min_x = next(x for x in range(w) if col_has(x, min_y, max_y))
    max_x = next(x for x in range(w - 1, -1, -1) if col_has(x, min_y, max_y))

    cropped = image.copy(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
    return QPixmap.fromImage(cropped)


# --- [ ANIMATIONEN & THEME ] ---

class GlowHoverAnimator(QObject):
    def __init__(self, get_color_callback):
        super().__init__()
        self.get_color = get_color_callback

    def eventFilter(self, obj, event):
        if isinstance(obj, QPushButton) and obj.isEnabled():
            if event.type() == QEvent.Type.Enter:
                self._animate_glow(obj, True)
            elif event.type() == QEvent.Type.Leave:
                self._animate_glow(obj, False)
        return False

    def _animate_glow(self, btn, is_hover):
        effect = btn.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(btn)
            effect.setOffset(0, 0)
            btn.setGraphicsEffect(effect)
        
        anim = QPropertyAnimation(effect, b"color", btn)
        anim.setDuration(200)
        anim.setStartValue(effect.color())
        
        theme_color = QColor(self.get_color())
        theme_color.setAlpha(180)
        target_color = theme_color if is_hover else QColor(0, 0, 0, 0)
        anim.setEndValue(target_color)
        
        blur_anim = QPropertyAnimation(effect, b"blurRadius", btn)
        blur_anim.setDuration(200)
        blur_anim.setStartValue(effect.blurRadius())
        blur_anim.setEndValue(30 if is_hover else 0)

        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        blur_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        btn._glow_anim = anim 
        btn._blur_anim = blur_anim


# --- [ THUMBNAILS & GALERIEN ] ---

THUMB_W, THUMB_H = 220, 150

def make_cover_thumbnail(image, w=THUMB_W, h=THUMB_H, radius=12, dpr=1.0):
    """Schneidet/skaliert ein QImage auf ein rundes Vorschau-Thumbnail.
    Arbeitet bewusst mit QImage statt QPixmap: QImage ist (anders als
    QPixmap) auch in einem Hintergrund-Thread sicher nutzbar - das macht
    sich ThumbnailLoaderThread unten zunutze, damit das Erzeugen der
    Vorschaubilder die Oberflaeche nicht mehr blockiert."""
    if image is None or image.isNull():
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#222228"))

    # Se genera a resolucion real de pantalla (w x dpr) para que se vea nitida
    # con Windows escalado; el QImage lleva su devicePixelRatio.
    pw, ph = int(round(w * dpr)), int(round(h * dpr))
    scaled = image.scaled(pw, ph, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - pw) // 2)
    y = max(0, (scaled.height() - ph) // 2)
    cropped = scaled.copy(x, y, pw, ph)
    cropped.setDevicePixelRatio(dpr)

    result = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
    result.setDevicePixelRatio(dpr)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    painter.setClipPath(path)
    painter.drawImage(0, 0, cropped)
    painter.end()
    return result

def draw_play_badge(image):
    result = QImage(image)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    size = result.deviceIndependentSize()   # coordenadas logicas (imagen HiDPI)
    cx, cy = size.width() / 2, size.height() / 2
    r = min(size.width(), size.height()) * 0.16
    painter.setBrush(QColor(0, 0, 0, 130))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    
    triangle = QPainterPath()
    triangle.moveTo(cx - r * 0.35, cy - r * 0.55)
    triangle.lineTo(cx - r * 0.35, cy + r * 0.55)
    triangle.lineTo(cx + r * 0.6, cy)
    triangle.closeSubpath()
    painter.setBrush(QColor(255, 255, 255, 235))
    painter.drawPath(triangle)
    painter.end()
    return result
        # Der restliche Dialog-Text selbst wird erst beim naechsten Oeffnen
        # neu aufgebaut (der Dialog ist modal - der Rest der App links im
        # Hintergrund uebernimmt die neue Sprache aber sofort).


APP_POPUP_STYLE = """
/* ---- Estilo general de la app: se aplica a TODO (tambien a las ventanas que
   se abren aparte), asi nada queda con el aspecto por defecto de Windows ---- */
QDialog { background-color: #1c1e29; }
QDialog QLabel { color: #ffffff; }
QMenu {
    background-color: #1f2230; color: #ffffff; border: 1px solid rgba(255,255,255,0.14);
    border-radius: 10px; padding: 6px; font-size: 13px;
}
QMenu::item { padding: 7px 22px 7px 14px; border-radius: 7px; background: transparent; color: #ffffff; }
QMenu::item:selected { background-color: ACCENT_SOFT; color: #ffffff; }
QMenu::item:disabled { color: rgba(255,255,255,0.45); }
QMenu::separator { height: 1px; background: rgba(255,255,255,0.12); margin: 5px 8px; }
QToolTip {
    background-color: #1f2230; color: #ffffff; border: 1px solid rgba(255,255,255,0.18);
    border-radius: 6px; padding: 5px 8px; font-size: 12px;
}
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px 4px 0px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0px 4px 2px 4px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.18); min-height: 30px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: rgba(255,255,255,0.18); min-width: 30px; border-radius: 5px; }
QScrollBar::handle:hover { background: rgba(255,255,255,0.32); }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QSlider::groove:horizontal { height: 6px; background: rgba(255,255,255,0.12); border-radius: 3px; }
QSlider::sub-page:horizontal { background: ACCENT; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background: ACCENT; }
QLineEdit, QDateEdit {
    background: rgba(255,255,255,0.06); color: #ffffff; border: 1px solid rgba(255,255,255,0.15);
    border-radius: 8px; padding: 6px 10px; selection-background-color: ACCENT_SOFT;
}
QLineEdit:focus, QDateEdit:focus { border: 1px solid ACCENT; }
QDateEdit::drop-down { border: none; width: 22px; }
QCalendarWidget QWidget { background-color: #1f2230; color: #ffffff; alternate-background-color: #1f2230; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background-color: #262a3a; }
QCalendarWidget QToolButton {
    color: #ffffff; background: transparent; border: none; border-radius: 6px; padding: 4px 8px; font-weight: 700;
}
QCalendarWidget QToolButton:hover { background: rgba(255,255,255,0.10); }
QCalendarWidget QAbstractItemView {
    background-color: #1f2230; color: #ffffff; selection-background-color: ACCENT; selection-color: #0d0d12;
    outline: none;
}
QCalendarWidget QAbstractItemView:disabled { color: rgba(255,255,255,0.3); }
QCalendarWidget QSpinBox { background: #262a3a; color: #ffffff; border: none; }
QColorDialog { background-color: #1c1e29; }
QColorDialog QLabel, QColorDialog QSpinBox, QColorDialog QLineEdit { color: #ffffff; }
QColorDialog QSpinBox { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; }
QDialog QPushButton:!flat {
    background: rgba(255,255,255,0.08); color: #ffffff; border: 1px solid rgba(255,255,255,0.14);
    border-radius: 10px; padding: 7px 16px; font-weight: 600;
}
QDialog QPushButton:hover { background: rgba(255,255,255,0.16); }
QTreeWidget {
    background: rgba(255,255,255,0.03); color: #ffffff; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px; alternate-background-color: rgba(255,255,255,0.025); outline: none; font-size: 13px;
}
QTreeWidget::item { padding: 4px 2px; border: none; }
QTreeWidget::item:selected { background: ACCENT_SOFT; color: #ffffff; }
QTreeWidget::item:hover { background: rgba(255,255,255,0.06); }
QTreeWidget::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.35); background: rgba(255,255,255,0.05); }
QTreeWidget::indicator:checked { background: ACCENT; border: 1px solid ACCENT; }
QHeaderView::section {
    background: #262a3a; color: rgba(255,255,255,0.7); border: none; padding: 6px 8px; font-weight: 700; font-size: 12px;
}
"""


def apply_popup_style(accent="#00f2fe"):
    """Menus contextuales y tooltips con los colores de la app en TODA la
    aplicacion (antes usaban los de Windows: texto/fondo que no combinaban)."""
    app = QApplication.instance()
    if app is None:
        return
    c = QColor(accent)
    css = (APP_POPUP_STYLE.replace("ACCENT_SOFT", f"rgba({c.red()},{c.green()},{c.blue()},0.25)")
           .replace("ACCENT", c.name()))
    base = getattr(app, "_base_style", None)
    if base is None:
        base = app.styleSheet() or ""
        app._base_style = base
    app.setStyleSheet(base + css)

        # Sin spinner: durante la construccion de la ventana Qt no puede
        # redibujar (p. ej. ~1 s cargando las fuentes) y un spinner quieto
        # parece colgado. La barra avanza por etapas y nunca "se traba".


# Progreso del arranque: main() lo conecta a la pantalla de carga. Entre
# etapa y etapa se procesan eventos, asi la animacion no se queda trabada.
BOOT_PROGRESS = None


def set_boot_progress(callback):
    global BOOT_PROGRESS
    BOOT_PROGRESS = callback


def boot_step(pct, key):
    if BOOT_PROGRESS is not None:
        try:
            BOOT_PROGRESS(pct, key)
        except Exception:
            pass
