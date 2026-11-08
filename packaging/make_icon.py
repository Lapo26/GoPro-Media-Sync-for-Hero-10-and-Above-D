"""Genera app/assets/app_icon.ico (y app_icon.png) para GoPro Sync.

Diseño propio y simple: cuadrado redondeado oscuro, un "lente" celeste y dos
flechas de sincronización alrededor. Cada tamaño se dibuja por separado
(no se reduce uno grande), así a 16-32 px sigue siendo legible: en los
tamaños chicos se omiten detalles.

Uso:  python packaging/make_icon.py
"""
import math
import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
ACCENT = QColor("#00f2fe")
ACCENT_2 = QColor("#4facfe")


def draw_icon(size):
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)

    # Fondo: cuadrado redondeado con degradado oscuro
    margin = 0 if size <= 24 else s * 0.03
    rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)
    radius = rect.width() * 0.22
    bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg.setColorAt(0, QColor("#2a2f40"))
    bg.setColorAt(1, QColor("#14151d"))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, bg)
    if size >= 32:
        p.setPen(QPen(QColor(255, 255, 255, 30), max(1.0, s / 128)))
        p.drawPath(path)

    c = QPointF(s / 2, s / 2)
    grad = QLinearGradient(QPointF(0, 0), QPointF(s, s))
    grad.setColorAt(0, ACCENT)
    grad.setColorAt(1, ACCENT_2)

    # Flechas de sincronizacion (dos arcos con punta)
    ring_r = s * 0.34
    stroke = max(1.6, s * (0.085 if size <= 32 else 0.07))
    pen = QPen(QBrush(grad), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc_rect = QRectF(c.x() - ring_r, c.y() - ring_r, 2 * ring_r, 2 * ring_r)
    gap = 38 if size > 24 else 46
    for start in (90 + gap / 2, 270 + gap / 2):
        span = 180 - gap
        p.drawArc(arc_rect, int(start * 16), int(span * 16))
        # punta de flecha al final del arco (sentido antihorario)
        end_ang = math.radians(start + span)
        end = QPointF(c.x() + ring_r * math.cos(end_ang), c.y() - ring_r * math.sin(end_ang))
        t = end_ang + math.pi / 2                 # direccion del movimiento (antihorario)
        fwd = QPointF(math.cos(t), -math.sin(t))  # en coordenadas de pantalla (y hacia abajo)
        nrm = QPointF(-fwd.y(), fwd.x())
        head = stroke * 1.25
        # base de la punta centrada en el final del arco, punta hacia adelante
        arrow = QPainterPath()
        arrow.moveTo(end + nrm * head)
        arrow.lineTo(end - nrm * head)
        arrow.lineTo(end + fwd * (head * 1.5))
        arrow.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(arrow)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

    # Lente en el centro
    lens_r = s * (0.17 if size > 24 else 0.19)
    lens = QRadialGradient(c, lens_r)
    lens.setColorAt(0.0, QColor("#0b2b3a"))
    lens.setColorAt(0.7, QColor("#06141c"))
    lens.setColorAt(1.0, QColor("#000000"))
    p.setPen(QPen(QBrush(grad), max(1.2, s * 0.035)))
    p.setBrush(QBrush(lens))
    p.drawEllipse(c, lens_r, lens_r)
    if size >= 32:   # brillo del lente solo donde se aprecia
        hl = lens_r * 0.32
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 170))
        p.drawEllipse(QPointF(c.x() - lens_r * 0.35, c.y() - lens_r * 0.35), hl, hl)
    p.end()
    return img


def png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def write_ico(path, images):
    """ICO con entradas PNG (soportado desde Windows Vista)."""
    entries = [(img.width(), png_bytes(img)) for img in images]
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    dirs, blobs = b"", b""
    for size, data in entries:
        dim = 0 if size >= 256 else size
        dirs += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    with open(path, "wb") as f:
        f.write(header + dirs + blobs)


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, "app", "assets")
    images = [draw_icon(sz) for sz in SIZES]
    write_ico(os.path.join(assets, "app_icon.ico"), images)
    draw_icon(512).save(os.path.join(assets, "app_icon.png"))
    print("Icono generado:", ", ".join(f"{s}px" for s in SIZES))


if __name__ == "__main__":
    main()
