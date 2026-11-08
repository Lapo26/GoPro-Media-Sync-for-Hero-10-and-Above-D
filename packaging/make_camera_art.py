"""Genera app/assets/camera_generic.png: ilustracion propia (sin marcas) de una
camara de accion, que se muestra al conectar un modelo sin video propio
(p. ej. HERO5). El usuario puede reemplazarla por su propia foto desde la app.

Uso:  python packaging/make_camera_art.py
"""
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)

SIZE = 840  # 420 px logicos x 2 (pantallas al 200 %)


def draw(size=SIZE):
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 840.0

    # fondo con brillo suave
    bg = QRadialGradient(QPointF(420 * s, 380 * s), 520 * s)
    bg.setColorAt(0, QColor("#2b3142"))
    bg.setColorAt(1, QColor("#14161e"))
    p.fillRect(QRectF(0, 0, size, size), bg)

    # sombra en el piso
    shadow = QRadialGradient(QPointF(420 * s, 655 * s), 260 * s)
    shadow.setColorAt(0, QColor(0, 0, 0, 150))
    shadow.setColorAt(1, QColor(0, 0, 0, 0))
    p.setBrush(shadow)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(420 * s, 655 * s), 270 * s, 38 * s)

    # cuerpo
    body = QRectF(190 * s, 245 * s, 460 * s, 380 * s)
    grad = QLinearGradient(body.topLeft(), body.bottomRight())
    grad.setColorAt(0, QColor("#3a3f4c"))
    grad.setColorAt(0.55, QColor("#22252e"))
    grad.setColorAt(1, QColor("#16181f"))
    path = QPainterPath()
    path.addRoundedRect(body, 46 * s, 46 * s)
    p.fillPath(path, grad)
    p.setPen(QPen(QColor(255, 255, 255, 40), 3 * s))
    p.drawPath(path)

    # textura de agarre (lineas laterales)
    p.setPen(QPen(QColor(255, 255, 255, 18), 4 * s))
    for i in range(7):
        y = (300 + i * 42) * s
        p.drawLine(QPointF(208 * s, y), QPointF(232 * s, y))
        p.drawLine(QPointF(608 * s, y), QPointF(632 * s, y))

    # boton superior
    btn = QRectF(270 * s, 222 * s, 110 * s, 30 * s)
    bpath = QPainterPath()
    bpath.addRoundedRect(btn, 12 * s, 12 * s)
    p.fillPath(bpath, QColor("#00c8e0"))

    # pantalla frontal pequena
    lcd = QRectF(250 * s, 300 * s, 120 * s, 86 * s)
    lpath = QPainterPath()
    lpath.addRoundedRect(lcd, 12 * s, 12 * s)
    lg = QLinearGradient(lcd.topLeft(), lcd.bottomRight())
    lg.setColorAt(0, QColor("#0e3440"))
    lg.setColorAt(1, QColor("#081a22"))
    p.fillPath(lpath, lg)
    p.setPen(QPen(QColor(0, 242, 254, 170), 4 * s))
    p.drawLine(QPointF(268 * s, 330 * s), QPointF(330 * s, 330 * s))
    p.drawLine(QPointF(268 * s, 352 * s), QPointF(310 * s, 352 * s))

    # luz de grabacion
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ff4c5c"))
    p.drawEllipse(QPointF(600 * s, 290 * s), 10 * s, 10 * s)

    # lente: anillo exterior, anillos, cristal, reflejos
    c = QPointF(500 * s, 450 * s)
    ring = QLinearGradient(QPointF(380 * s, 330 * s), QPointF(620 * s, 570 * s))
    ring.setColorAt(0, QColor("#5a6070"))
    ring.setColorAt(1, QColor("#1a1c22"))
    p.setBrush(ring)
    p.drawEllipse(c, 128 * s, 128 * s)
    p.setBrush(QColor("#0b0c10"))
    p.drawEllipse(c, 104 * s, 104 * s)
    p.setPen(QPen(QColor(0, 242, 254, 120), 4 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(c, 90 * s, 90 * s)
    glass = QRadialGradient(QPointF(c.x() - 20 * s, c.y() - 25 * s), 90 * s)
    glass.setColorAt(0, QColor("#1f4b66"))
    glass.setColorAt(0.5, QColor("#0d2333"))
    glass.setColorAt(1, QColor("#03070b"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(glass)
    p.drawEllipse(c, 80 * s, 80 * s)
    p.setBrush(QColor(255, 255, 255, 190))
    p.drawEllipse(QPointF(c.x() - 32 * s, c.y() - 34 * s), 16 * s, 16 * s)
    p.setBrush(QColor(255, 255, 255, 70))
    p.drawEllipse(QPointF(c.x() + 30 * s, c.y() + 26 * s), 8 * s, 8 * s)
    p.end()
    return img


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "app", "assets", "camera_generic.png")
    draw().save(out)
    print("Generado:", out)


if __name__ == "__main__":
    main()
