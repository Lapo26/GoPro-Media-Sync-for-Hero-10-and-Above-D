"""Busqueda de versiones nuevas en GitHub y actualizacion."""
import json
import os
import re
import subprocess
import tempfile
from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout
from .version import APP_VERSION, GITHUB_REPO
from .env import FONT_STACK, IS_MAC, IS_WINDOWS
from .i18n import _human_size, tr
from .ui.widgets import show_toast

def _version_tuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])
    except Exception:
        return (0,)


def _http_get(url, timeout=10):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"GoProSync/{APP_VERSION}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


class UpdateCheckThread(QThread):
    """Consulta la ultima version publicada en GitHub (en segundo plano)."""
    found = Signal(dict)          # {"version", "url", "size", "page"} si hay una mas nueva

    def run(self):
        try:
            with _http_get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest") as r:
                data = json.loads(r.read().decode("utf-8"))
            tag = (data.get("tag_name") or "").lstrip("vV")
            if _version_tuple(tag) <= _version_tuple(APP_VERSION):
                return
            def wanted(n):
                n = n.lower()
                if IS_WINDOWS:
                    return n.startswith("goprosync-setup") and n.endswith(".exe")
                if IS_MAC:
                    return n.endswith(".dmg")
                return n.endswith(".appimage")
            asset = next((a for a in data.get("assets") or [] if wanted(a.get("name", ""))), None)
            if asset:
                self.found.emit({"version": tag, "url": asset["browser_download_url"], "size": asset.get("size", 0),
                                 "page": data.get("html_url", "")})
        except Exception as e:
            print("[Actualizaciones]", e)


class UpdateDownloadThread(QThread):
    progress = Signal(int)
    done = Signal(str, str)       # ruta, error

    def __init__(self, url, dest):
        super().__init__()
        self.url, self.dest = url, dest
        self._cancel = False

    def run(self):
        part = self.dest + ".part"
        try:
            with _http_get(self.url, timeout=30) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while not self._cancel:
                    buf = r.read(256 * 1024)
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    if total:
                        self.progress.emit(int(got * 100 / total))
            if self._cancel:
                raise RuntimeError("cancelado")
            os.replace(part, self.dest)
            self.done.emit(self.dest, "")
        except Exception as e:
            try:
                os.remove(part)
            except OSError:
                pass
            self.done.emit("", str(e))


class UpdateDialog(QDialog):
    """Ofrece la version nueva; si se acepta, descarga el instalador y lo
    corre en modo silencioso (se cierra la app y se vuelve a abrir sola)."""

    def __init__(self, parent, lang, info, accent):
        super().__init__(parent)
        self.lang, self.info = lang, info
        self.worker = None
        self.setWindowTitle(tr(lang, "update_title"))
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setStyleSheet(f"""
            QDialog {{ background: #1c1e29; }}
            QLabel {{ color: #fff; background: transparent; }}
            * {{ font-family: {FONT_STACK}; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
            QPushButton#primary {{ background: {accent}; color: #0d0d12; border: none; font-weight: 800; }}
            QPushButton#primary:disabled {{ background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.4); }}
            QProgressBar {{ background: rgba(255,255,255,0.08); border: none; border-radius: 6px; height: 10px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(10)
        q = QLabel(tr(lang, "update_question", v=info["version"], cur=APP_VERSION))
        q.setWordWrap(True)
        q.setStyleSheet("font-size: 17px; font-weight: 800;")
        d = QLabel(tr(lang, "update_detail", size=_human_size(info.get("size") or 0)))
        d.setWordWrap(True)
        d.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.65);")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.65);")
        lay.addWidget(q)
        lay.addWidget(d)
        lay.addWidget(self.bar)
        lay.addWidget(self.lbl_status)
        lay.addSpacing(6)
        row = QHBoxLayout()
        if info.get("page"):
            notes = QPushButton(tr(lang, "update_notes"))
            notes.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(info["page"])))
            row.addWidget(notes)
        row.addStretch()
        self.btn_later = QPushButton(tr(lang, "update_later"))
        self.btn_later.clicked.connect(self.reject)
        self.btn_now = QPushButton(tr(lang, "update_now" if IS_WINDOWS else "update_open_page"))
        self.btn_now.setObjectName("primary")
        self.btn_now.clicked.connect(self._download)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_now)
        lay.addLayout(row)

    def _download(self):
        if not IS_WINDOWS:
            # macOS / Linux: se baja el .dmg / .AppImage desde la pagina del Release
            QDesktopServices.openUrl(QUrl(self.info.get("page") or self.info["url"]))
            self.accept()
            return
        self.btn_now.setEnabled(False)
        self.bar.show()
        self.lbl_status.setText(tr(self.lang, "update_downloading", pct=0))
        dest = os.path.join(tempfile.gettempdir(), f"GoProSync-Setup-{self.info['version']}.exe")
        self.worker = UpdateDownloadThread(self.info["url"], dest)
        self.worker.progress.connect(lambda pct: (self.bar.setValue(pct),
                                                  self.lbl_status.setText(tr(self.lang, "update_downloading", pct=pct))))
        self.worker.done.connect(self._on_downloaded)
        self.worker.start()

    def _on_downloaded(self, path, error):
        if error:
            self.lbl_status.setText(tr(self.lang, "update_failed", e=error))
            self.btn_now.setEnabled(True)
            return
        self.accept()
        try:
            # /SILENT: solo una barrita de progreso; el instalador cierra la app y la vuelve a abrir.
            subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
        except Exception as e:
            show_toast(self.parent(), tr(self.lang, "update_failed", e=e))
            return
        main = self.parent()
        if hasattr(main, "quit_app"):
            QTimer.singleShot(300, main.quit_app)

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker._cancel = True
            self.worker.wait(3000)
        super().reject()
