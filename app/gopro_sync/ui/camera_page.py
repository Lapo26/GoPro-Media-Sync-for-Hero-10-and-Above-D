"""Galeria de los archivos de la camara."""
import os
import tempfile
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from ..env import CAMERA_BROWSING
from ..i18n import _human_size, tr
from ..sound import SoundManager
from ..fileops import JOINED_PARTS, _parse_camera_dt, existing_candidates, index_existing
from ..mtp import CameraListThread, WpdCameraWorker, camera_item_key
from ..ui.graphics import THUMB_H, THUMB_W, draw_play_badge, make_cover_thumbnail, screen_dpr
from ..ui.widgets import ToggleSwitch, show_toast
from ..ui.thumbs import ThumbGridView, _ThumbIO
from ..ui.viewers import ImageViewerDialog, VideoPlayerDialog

class CameraThumbAdapter(QObject):
    """Proveedor de miniaturas para la galeria de la camara: las ya
    sincronizadas salen de la cache local (nitidas); las nuevas, de la camara."""
    ready = Signal(str, QImage)

    def __init__(self, info, worker, lang, parent=None):
        super().__init__(parent)
        from concurrent.futures import ThreadPoolExecutor
        self.info = info                  # clave -> {"name", "local", "video"}
        self.worker = worker
        self.lang = lang
        self.generation = 0
        self.inflight = set()
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.dpr = screen_dpr()
        self.by_name = {}
        for key, inf in info.items():
            self.by_name.setdefault(inf["name"].upper(), []).append(key)
        worker.thumb_ready.connect(self._on_camera_thumb)

    def _cover(self, img, video):
        cover = make_cover_thumbnail(img, THUMB_W, THUMB_H, dpr=self.dpr)
        return draw_play_badge(cover) if video else cover

    def request(self, keys):
        camera_names = []
        for key in keys:
            inf = self.info.get(key)
            if inf is None or key in self.inflight:
                continue
            self.inflight.add(key)
            if inf["size"] == 0:
                # archivo vacio (grabacion cortada): no pedirle nada a la camara,
                # leer 0 bytes por MTP puede dejarla colgada
                io = _ThumbIO("video" if inf["video"] else "picture", {}, self.lang)
                self.ready.emit(key, self._cover(io._damaged_image(), False))
            elif inf["local"]:
                self.pool.submit(self._load_local, key, inf)
            else:
                camera_names.append(inf["name"])
        self.worker.set_thumb_requests(camera_names)

    def _load_local(self, key, inf):
        io = _ThumbIO("video" if inf["video"] else "picture", {}, self.lang)
        img = io._load_cached(inf["local"])
        if img is None or img.isNull():
            # no hay miniatura en cache: pedirsela a la camara
            self.worker.add_thumb_request(inf["name"])
            return
        self.ready.emit(key, self._cover(img, inf["video"]))

    def _on_camera_thumb(self, name, img):
        for key in self.by_name.get(name.upper(), []):
            inf = self.info[key]
            self.ready.emit(key, self._cover(img, inf["video"]) if not img.isNull() else QImage())

    def shutdown(self):
        self.generation += 1
        try:
            self.pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


class CameraGalleryPage(QWidget):
    """Galeria de la camara: miniaturas por fecha con marca de sincronizado /
    nuevo, seleccion de uno o varios y sincronizar solo esos. Clic = vista
    previa sin sincronizar."""
    back_requested = Signal()
    sync_requested = Signal(object)      # lista de claves "carpeta|nombre"
    PREVIEW_VIDEO_MAX = 300 * 1024 * 1024

    def __init__(self, lang, accent, get_target_dir, parent=None):
        super().__init__(parent)
        self.lang, self.accent, self.get_target_dir = lang, accent, get_target_dir
        self.worker = None
        self.lister = None
        self.info = {}
        self.all_keys = []
        self._pending_preview = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 20)
        outer.setSpacing(12)
        btn_css = ("QPushButton { background-color: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.14);"
                   " border-radius: 10px; padding: 8px 16px; color: rgba(255,255,255,0.85); font-size: 13px; font-weight: 600; }"
                   " QPushButton:hover { background-color: rgba(255,255,255,0.12); }")
        head = QHBoxLayout()
        self.btn_back = QPushButton(tr(lang, "cam_back"))
        self.btn_back.setStyleSheet(btn_css)
        self.btn_back.clicked.connect(self.close_page)
        self.title_lbl = QLabel(tr(lang, "cam_page_title"))
        self.title_lbl.setStyleSheet("font-size: 24px; font-weight: 800; color: #ffffff; background: transparent;")
        head.addWidget(self.btn_back)
        head.addSpacing(10)
        head.addWidget(self.title_lbl)
        head.addStretch()
        self.btn_sel_new = QPushButton(tr(lang, "cam_select_new"))
        self.btn_sel_none = QPushButton(tr(lang, "cam_select_none"))
        for b in (self.btn_sel_new, self.btn_sel_none):
            b.setStyleSheet(btn_css)
            head.addWidget(b)
        self.btn_sel_new.clicked.connect(lambda: self._select(lambda k: not self.info[k]["local"] and self.info[k]["size"] > 0))
        self.btn_sel_none.clicked.connect(lambda: self._select(lambda k: False))
        outer.addLayout(head)
        row = QHBoxLayout()
        self.lbl_summary = QLabel("")
        self.lbl_summary.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.6); background: transparent;")
        self.chk_only_new = ToggleSwitch(tr(lang, "cam_only_new"))
        self.chk_only_new.set_accent(accent)
        self.chk_only_new.toggled.connect(lambda _s: self._apply_filter())
        row.addWidget(self.lbl_summary)
        row.addStretch()
        row.addWidget(self.chk_only_new)
        outer.addLayout(row)
        self.view = ThumbGridView(self)
        self.view.accent = QColor(accent)
        self.view.selectable = True
        self.view.activated.connect(self._preview)
        self.view.selection_changed.connect(self._update_footer)
        outer.addWidget(self.view, 1)
        foot = QHBoxLayout()
        self.lbl_sel = QLabel("")
        self.lbl_sel.setStyleSheet(f"font-size: 13px; color: {accent}; background: transparent;")
        self.btn_sync = QPushButton(tr(lang, "cam_sync_selected"))
        self.btn_sync.setStyleSheet(
            f"QPushButton {{ background: {accent}; color: #0d0d12; border: none; border-radius: 10px; padding: 10px 20px;"
            f" font-weight: 800; font-size: 13px; }} QPushButton:disabled {{ background: rgba(255,255,255,0.12);"
            f" color: rgba(255,255,255,0.4); }}")
        self.btn_sync.setEnabled(False)
        self.btn_sync.clicked.connect(self._sync_selected)
        foot.addWidget(self.lbl_sel)
        foot.addStretch()
        foot.addWidget(self.btn_sync)
        outer.addLayout(foot)

    # ---- abrir / cerrar
    def open_page(self, device_name=""):
        self.close_workers()
        self.title_lbl.setText(tr(self.lang, "cam_page_title") + (f" · {device_name}" if device_name else ""))
        self.lbl_summary.setText(tr(self.lang, "cam_reading"))
        self.view.selected.clear()
        self.view.status = {}
        self.view.set_items([], [], {}, "picture", self.lang)
        self._update_footer()
        CAMERA_BROWSING.set()
        self.worker = WpdCameraWorker("HERO", self)
        self.worker.file_ready.connect(self._on_preview_file)
        self.worker.not_responding.connect(lambda: show_toast(self, tr(self.lang, "cam_not_responding")))
        self.lister = CameraListThread(self)
        self.lister.listed.connect(self._on_listed)
        self.lister.start()

    def close_workers(self):
        CAMERA_BROWSING.clear()
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(3000)
            self.worker = None

    def close_page(self):
        self.close_workers()
        self.back_requested.emit()

    # ---- datos
    def _on_listed(self, items):
        if items is None:
            self.lbl_summary.setText(tr(self.lang, "cam_list_error"))
            return
        index = {}
        try:
            for root, _d, names in os.walk(self.get_target_dir()):
                for n in names:
                    full = os.path.join(root, n)
                    try:
                        index_existing(index, n, (os.path.getsize(full), full))
                    except OSError:
                        pass
        except Exception:
            pass
        info, entries = {}, []
        for e in items:
            name, size = e.get("name") or "", int(e.get("size") or 0)
            key = camera_item_key(e)
            local = next((pth for sz, pth in existing_candidates(index, name) if sz == size), None)
            if local is None and f"{name.lower()}|{size}" in JOINED_PARTS:
                local = "(unido)"
            dt = _parse_camera_dt(e.get("dt"))
            info[key] = {"name": name, "size": size, "local": local, "video": name.lower().endswith(".mp4"), "dt": dt}
            entries.append((dt.timestamp() if dt else 0.0, key))
        entries.sort(reverse=True)
        self.info = info
        self.all_keys = [k for _t, k in entries]
        self.view.status = {k: ("synced" if info[k]["local"] else "new") for k in self.all_keys}
        self.view.tooltips = {k: ((info[k]["local"] or info[k]["name"]) + "  ·  " + _human_size(info[k]["size"])
                                  + ("  ·  " + tr(self.lang, "cam_empty_file", name="").strip(" :") if info[k]["size"] == 0 else ""))
                              for k in self.all_keys}
        self.provider = CameraThumbAdapter(info, self.worker, self.lang, self)
        if self.worker is not None and not self.worker.isRunning():
            self.worker.start()           # despues del listado: no pelean por la camara
        new = sum(1 for k in self.all_keys if not info[k]["local"])
        self.lbl_summary.setText(tr(self.lang, "cam_summary", total=len(self.all_keys), new=new))
        self._apply_filter(first=True)

    def _apply_filter(self, first=False):
        if not self.info:
            return
        keys = [k for k in self.all_keys if not (self.chk_only_new.isChecked() and self.info[k]["local"])]
        dates = [self.info[k]["dt"].strftime("%Y-%m-%d") if self.info[k]["dt"] else "0000-00-00" for k in keys]
        self.view.set_items(keys, dates, {}, "picture", self.lang, provider=self.provider, keep_cache=not first)
        self._update_footer()

    def _select(self, pred):
        self.view.selected = {k for k in self.view.files if pred(k)}
        self.view.viewport().update()
        self._update_footer()

    def _update_footer(self):
        sel = [k for k in self.view.selected if k in self.info]
        total = sum(self.info[k]["size"] for k in sel)
        self.lbl_sel.setText(tr(self.lang, "cam_selection", n=len(sel), size=_human_size(total)) if self.info else "")
        self.btn_sync.setEnabled(bool(sel))

    def _sync_selected(self):
        keys = [k for k in self.all_keys if k in self.view.selected]
        if keys:
            self.close_workers()
            self.sync_requested.emit(keys)

    # ---- vista previa (sin sincronizar)
    def _preview(self, key):
        inf = self.info.get(key)
        if not inf:
            return
        if inf["size"] == 0:
            show_toast(self, tr(self.lang, "cam_empty_file", name=inf["name"]))
            return
        if inf["local"]:
            return self._open_viewer(inf["local"], inf["video"])
        name = inf["name"]
        if inf["video"]:
            base = os.path.splitext(name)[0].upper()
            lrv = ("GL" + base[2:] if base.startswith("GP") else base) + ".LRV"
            if self.worker is not None and lrv in self.worker.objects:
                name = lrv                       # version liviana del video (si la camara la muestra)
            elif inf["size"] > self.PREVIEW_VIDEO_MAX:
                show_toast(self, tr(self.lang, "cam_preview_too_big", size=_human_size(inf["size"])))
                return
        if self.worker is not None and self.worker.dead:
            show_toast(self, tr(self.lang, "cam_not_responding"))
            return
        dest_dir = os.path.join(tempfile.gettempdir(), "GoProSync-preview")
        os.makedirs(dest_dir, exist_ok=True)
        ext = ".mp4" if inf["video"] else os.path.splitext(name)[1]
        dest = os.path.join(dest_dir, os.path.splitext(name)[0] + ext)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return self._open_viewer(dest, inf["video"])
        self._pending_preview[name.upper()] = inf["video"]
        show_toast(self, tr(self.lang, "cam_preview_loading"))
        if self.worker is not None:
            self.worker.request_file(name, dest)

    def _on_preview_file(self, name, path):
        video = self._pending_preview.pop(name.upper(), None)
        if video is None:
            return
        if not path:
            show_toast(self, tr(self.lang, "cam_preview_failed"))
            return
        self._open_viewer(path, video)

    def _open_viewer(self, path, video):
        SoundManager.play_click()
        if video:
            VideoPlayerDialog(path, gallery_dir=self.get_target_dir(), lang=self.lang, parent=self).exec()
        else:
            ImageViewerDialog(path, self).exec()
