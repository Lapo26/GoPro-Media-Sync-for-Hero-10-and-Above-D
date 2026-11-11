"""Galerias de videos y fotos."""
import os
import re
from datetime import datetime
from datetime import timedelta
from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDateEdit, QDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget
from ..i18n import short_path, tr
from ..winshell import move_to_recycle_bin, show_in_explorer
from ..sound import SoundManager
from ..media import is_broken_mp4
from ..workers import ScanThread
from ..videotools import chapter_group_of, find_chapter_groups
from ..ui.widgets import show_toast
from ..ui.thumbs import ThumbGridView
from ..ui.viewers import ImageViewerDialog, VideoPlayerDialog
from ..ui.map import GpsMapDialog
from ..ui.dialogs import ConfirmDeleteDialog, MediaInfoDialog

class GalleryBase(QWidget):
    KIND = "picture"       # von Subklassen ueberschrieben: "video" oder "picture"
    EXTENSIONS = ()        # von Subklassen ueberschrieben

    def __init__(self, folder, title, empty_icon, empty_text, lang="de"):
        super().__init__()
        self.folder = folder
        self.lang = lang
        self.col_count = 3
        self.loader_thread = None
        self._loaded_count = 0
        # Sortierreihenfolge: True = neueste zuerst (Standard), False = aelteste zuerst
        self.sort_newest_first = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(18)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("font-size: 24px; font-weight: 800; letter-spacing: 0.5px; color: #ffffff; background: transparent;")
        header_row.addWidget(self.title_lbl)
        header_row.addStretch()

        self.btn_sort = QPushButton()
        self.btn_sort.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sort.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,0.07);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 10px;
                padding: 8px 16px;
                color: rgba(255,255,255,0.85);
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
        """)
        self.btn_sort.clicked.connect(self._show_sort_menu)

        # Filtro por fecha: (desde, hasta) como "AAAA-MM-DD" inclusive, o None
        self.date_filter = None
        self._date_filter_key = "date_all"
        self.btn_date = QPushButton()
        self.btn_date.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_date.setStyleSheet(self.btn_sort.styleSheet())
        self.btn_date.clicked.connect(self._show_date_menu)
        header_row.addWidget(self.btn_date)
        self._update_date_button_text()

        header_row.addWidget(self.btn_sort)
        self._update_sort_button_text()

        outer.addLayout(header_row)
        self.header_row = header_row

        self.lbl_loading = QLabel("")
        self.lbl_loading.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.4); background: transparent;")
        outer.addWidget(self.lbl_loading)
        self.lbl_loading.hide()

        # Vista virtualizada (dibuja solo lo visible); "scroll" se mantiene como
        # nombre por compatibilidad con el resto del codigo.
        self.view = ThumbGridView(self)
        self.view.activated.connect(self._open_item)
        self.view.context.connect(self._show_context_menu)
        self.scroll = self.view
        outer.addWidget(self.view)

        self._empty_icon = empty_icon
        self.empty_lbl = QLabel(f"{empty_icon}\n{empty_text}")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet("font-size: 15px; color: rgba(255,255,255,0.4); background: transparent;")
        outer.addWidget(self.empty_lbl)
        self.empty_lbl.hide()

    def _update_sort_button_text(self):
        label = tr(self.lang, "gallery_sort_newest" if self.sort_newest_first else "gallery_sort_oldest")
        self.btn_sort.setText(label)

    def _show_sort_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #23232b;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
                padding: 6px;
            }
            QMenu::item {
                color: #ffffff;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 13px;
            }
            QMenu::item:selected {
                background-color: rgba(255,255,255,0.16);
                color: #ffffff;
            }
        """)
        action_newest = menu.addAction(tr(self.lang, "gallery_sort_newest"))
        action_oldest = menu.addAction(tr(self.lang, "gallery_sort_oldest"))
        chosen = menu.exec(self.btn_sort.mapToGlobal(self.btn_sort.rect().bottomLeft()))
        if chosen is None:
            return
        SoundManager.play_click()
        new_value = chosen == action_newest
        if new_value == self.sort_newest_first:
            return
        self.sort_newest_first = new_value
        self._update_sort_button_text()
        self._rescan_next = False
        self.refresh()

    def retranslate(self, title, empty_text):
        self.title_lbl.setText(title)
        self.empty_lbl.setText((f"{self._empty_icon}\n{empty_text}" if self._empty_icon else empty_text))
        self._update_sort_button_text()

    def show_empty(self):
        self.scroll.hide()
        self.empty_lbl.show()

    def _forget_missing(self, path):
        """El archivo se borro o movio por fuera de la app: sacarlo de la galeria."""
        if os.path.exists(path):
            return False
        self.view.remove_path(path)
        if getattr(self, "_all_files", None) and path in self._all_files:
            self._all_files.remove(path)
        show_toast(self, tr(self.lang, "file_gone", name=os.path.basename(path)))
        return True

    def _open_item(self, path):
        if self._forget_missing(path):
            return
        if self.KIND == "video":
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size == 0:
                show_toast(self, tr(self.lang, "video_empty_msg", name=os.path.basename(path)))
                return
            if is_broken_mp4(path):
                show_toast(self, tr(self.lang, "video_broken_msg", name=os.path.basename(path)))
                return
            self.play_video(path)
        else:
            self.open_image(path)

    def soft_refresh(self):
        """Revisa la carpeta en segundo plano (al volver a la app): saca lo que
        ya no esta sin perder la posicion, o recarga si hay archivos nuevos."""
        if not self.isVisible() or getattr(self, "_all_files", None) is None:
            return
        if getattr(self, "_soft_running", False):
            self._soft_again = True          # se pidio otra mientras corria: repetir al terminar
            return
        self._soft_running = True
        self._soft_again = False
        folder, exts = self.folder, tuple(e.lower() for e in self.EXTENSIONS)

        def scan():
            found, stack = set(), [folder]
            while stack:
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    stack.append(e.path)
                                elif e.name.lower().endswith(exts):
                                    found.add(e.path)
                            except OSError:
                                pass
                except OSError:
                    pass
            return found
        th = ScanThread(0, scan)
        th.done.connect(self._on_soft_scan)
        th.start()

    def _on_soft_scan(self, _t, found):
        self._soft_running = False
        if getattr(self, "_soft_again", False):
            QTimer.singleShot(0, self.soft_refresh)    # el resultado ya puede estar viejo
            return
        if found is None or getattr(self, "_all_files", None) is None:
            return
        current = set(self._all_files)
        if found - current:
            self.refresh()                       # hay archivos nuevos: recargar
            return
        for path in current - found:
            self.view.remove_path(path)
            self._all_files.remove(path)

    def _show_context_menu(self, path, global_pos):
        """Clic derecho en una miniatura: ruta, copiar ruta, Explorador,
        abrir e informacion del archivo (metadatos)."""
        if self._forget_missing(path):
            return
        menu = QMenu(self)
        head = menu.addAction(short_path(path, keep=3))
        head.setEnabled(False)
        menu.addSeparator()
        menu.addAction(tr(self.lang, "ctx_open"),
                       lambda: self.play_video(path) if self.KIND == "video" else self.open_image(path))
        menu.addAction(tr(self.lang, "ctx_info"), lambda: MediaInfoDialog(
            self, self.lang, path, getattr(self.window(), "get_current_theme_color", lambda: "#00f2fe")()).exec())
        menu.addSeparator()
        menu.addAction(tr(self.lang, "ctx_copy_path"), lambda: (QApplication.clipboard().setText(os.path.normpath(path)),
                                                               show_toast(self, tr(self.lang, "path_copied"))))
        menu.addAction(tr(self.lang, "ctx_show_in_explorer"), lambda: show_in_explorer(path))
        main = self.window()
        if self.KIND == "video" and hasattr(main, "make_light_version"):
            menu.addSeparator()
            menu.addAction(tr(self.lang, "ctx_light"), lambda: main.make_light_version(path))
            menu.addAction(tr(self.lang, "ctx_map"), lambda: GpsMapDialog(
                self, self.lang, path, getattr(main, "get_current_theme_color", lambda: "#00f2fe")()).exec())
            if os.path.exists(path) and os.path.getsize(path) > 0 and is_broken_mp4(path):
                menu.addAction(tr(self.lang, "ctx_repair"), lambda: main.repair_video(path))
            group = chapter_group_of(path)
            if group and not group["done"] and not group["damaged"]:
                menu.addAction(tr(self.lang, "ctx_join", n=len(group["parts"])), lambda: main.join_groups([group]))
        menu.addSeparator()
        menu.addAction(tr(self.lang, "ctx_delete"), lambda: self.delete_file(path))
        menu.exec(global_pos)

    def delete_file(self, path):
        """Eliminar con confirmacion: va a la Papelera de reciclaje."""
        if ConfirmDeleteDialog(self, self.lang, path).exec() != QDialog.DialogCode.Accepted:
            return
        name = os.path.basename(path)
        if move_to_recycle_bin(path):
            self.view.remove_path(path)
            if getattr(self, "_all_files", None) and path in self._all_files:
                self._all_files.remove(path)
            show_toast(self, tr(self.lang, "deleted_ok", name=name))
        else:
            show_toast(self, tr(self.lang, "delete_failed", name=name))

    def _relayout_if_needed(self):
        pass   # la vista virtualizada reacomoda sola al redimensionar

    def showEvent(self, event):
        super().showEvent(event)
        # Las galerias se cargan recien la primera vez que se abren (antes se
        # recorrian las ~3000 fotos/videos al arrancar la app).
        if getattr(self, "_needs_refresh", False):
            self._needs_refresh = False
            QTimer.singleShot(0, self.refresh)

    def _update_date_button_text(self, label=None):
        if label is None:
            label = tr(self.lang, self._date_filter_key) if self._date_filter_key else ""
        self._date_label = label
        self.btn_date.setText(tr(self.lang, "date_filter_btn", label=label))

    def _available_years(self):
        years = set()
        try:
            for name in os.listdir(self.folder):
                if re.match(r"^\d{4}-\d{2}-\d{2}$", name):
                    years.add(name[:4])
        except Exception:
            pass
        return sorted(years, reverse=True)

    def _set_date_filter(self, rng, key=None, label=None):
        self.date_filter = rng
        self._date_filter_key = key
        self._update_date_button_text(label)
        self._rescan_next = False
        self.refresh()

    def _show_date_menu(self):
        today = datetime.now().date()
        fmt = lambda d: d.strftime("%Y-%m-%d")
        menu = QMenu(self)
        menu.addAction(tr(self.lang, "date_all"), lambda: self._set_date_filter(None, "date_all"))
        menu.addAction(tr(self.lang, "date_today"), lambda: self._set_date_filter((fmt(today), fmt(today)), "date_today"))
        menu.addAction(tr(self.lang, "date_7"),
                       lambda: self._set_date_filter((fmt(today - timedelta(days=6)), fmt(today)), "date_7"))
        menu.addAction(tr(self.lang, "date_30"),
                       lambda: self._set_date_filter((fmt(today - timedelta(days=29)), fmt(today)), "date_30"))
        menu.addAction(tr(self.lang, "date_this_year"),
                       lambda: self._set_date_filter((f"{today.year}-01-01", f"{today.year}-12-31"), "date_this_year"))
        years = self._available_years()
        if years:
            menu.addSeparator()
            for y in years:
                menu.addAction(y, lambda y=y: self._set_date_filter((f"{y}-01-01", f"{y}-12-31"), None, y))
        menu.addSeparator()
        menu.addAction(tr(self.lang, "date_custom"), self._pick_date_range)
        menu.exec(self.btn_date.mapToGlobal(self.btn_date.rect().bottomLeft()))

    def _pick_date_range(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr(self.lang, "date_range_title"))
        dlg.setStyleSheet("QDialog { background: #1c1e29; } QLabel { color: #fff; }")
        lay = QVBoxLayout(dlg)
        row = QHBoxLayout()
        start = self.date_filter[0] if self.date_filter else datetime.now().strftime("%Y-01-01")
        end = self.date_filter[1] if self.date_filter else datetime.now().strftime("%Y-%m-%d")
        d_from = QDateEdit(QDate.fromString(start, "yyyy-MM-dd"))
        d_to = QDateEdit(QDate.fromString(end, "yyyy-MM-dd"))
        for w in (d_from, d_to):
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd/MM/yyyy")
        row.addWidget(QLabel(tr(self.lang, "date_from")))
        row.addWidget(d_from)
        row.addWidget(QLabel(tr(self.lang, "date_to")))
        row.addWidget(d_to)
        lay.addLayout(row)
        ok = QPushButton(tr(self.lang, "date_apply"))
        ok.clicked.connect(dlg.accept)
        lay.addWidget(ok, alignment=Qt.AlignmentFlag.AlignRight)
        if dlg.exec():
            a, b = d_from.date().toString("yyyy-MM-dd"), d_to.date().toString("yyyy-MM-dd")
            if a > b:
                a, b = b, a
            label = f"{d_from.date().toString('dd/MM/yyyy')} – {d_to.date().toString('dd/MM/yyyy')}" \
                if a <= b else ""
            self._set_date_filter((a, b), None, label)

    def _mtime_for_path(self, path):
        cached = getattr(self, "_mtime_cache", {}).get(path)
        if cached is not None:
            return cached
        try:
            return os.path.getmtime(path)
        except Exception:
            return 0

    def _date_for_path(self, path):
        """Bevorzugt den Datums-Unterordner (YYYY-MM-DD, beim Sync aus dem
        Aufnahmedatum der Datei abgeleitet), faellt sonst auf das
        Datei-Datum (mtime) zurueck - z.B. fuer Dateien, die noch von vor
        der Datums-Sortierung stammen oder direkt im Root liegen."""
        parent_dir = os.path.dirname(path)
        for name in (os.path.basename(parent_dir), os.path.basename(os.path.dirname(parent_dir))):
            if re.match(r"^\d{4}-\d{2}-\d{2}$", name):   # FECHA\\archivo o FECHA\\camara\\archivo
                return name
        try:
            return datetime.fromtimestamp(self._mtime_for_path(path)).strftime("%Y-%m-%d")
        except Exception:
            return "0000-00-00"

    def get_files(self, exts, rescan=True):
        """Recorre el destino recursivamente y devuelve las rutas ordenadas
        por fecha. Usa os.scandir: en Windows la fecha de cada archivo viene
        con el listado de la carpeta, sin consultar el disco archivo por
        archivo (antes eran miles de consultas: ~4 s en disco mecanico)."""
        if not rescan and getattr(self, "_all_files", None) is not None:
            results = list(self._all_files)          # orden/filtro: sin volver a recorrer el disco
            return self._filter_and_sort(results)
        results = []
        self._mtime_cache = {}
        self._meta = {}            # ruta -> (tamano, fecha) para buscar en la cache sin tocar el disco
        stack = [self.folder]
        try:
            while stack:
                d = stack.pop()
                try:
                    it = os.scandir(d)
                except OSError:
                    continue
                with it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.name.lower().endswith(exts):
                                results.append(e.path)
                                st = e.stat(follow_symlinks=False)
                                self._mtime_cache[e.path] = st.st_mtime
                                self._meta[e.path] = (st.st_size, st.st_mtime)
                        except OSError:
                            continue
        except Exception:
            return []
        self._all_files = list(results)
        return self._filter_and_sort(results)

    def _filter_and_sort(self, results):
        if self.date_filter:
            lo, hi = self.date_filter
            results = [p for p in results if lo <= self._date_for_path(p) <= hi]
        # Zuerst nach dem Datums-Ordner sortieren (derselbe Wert, der auch
        # fuer die "Heute"/"Gestern"-Gruppenkoepfe verwendet wird), erst
        # danach nach mtime als Fein-Sortierung INNERHALB eines Tages.
        # So bleibt jede Tages-Gruppe garantiert am Stueck zusammen, statt
        # sich mit einer anderen Gruppe zu vermischen, wenn die mtime-Werte
        # zweier Tage sich einmal ueberschneiden.
        results.sort(
            key=lambda p: (self._date_for_path(p), self._mtime_for_path(p)),
            reverse=self.sort_newest_first
        )
        return results

    def populate(self):
        """Startet das Laden der Vorschaubilder IM HINTERGRUND (siehe
        ThumbnailLoaderThread) - blockiert die Oberflaeche nicht mehr, auch
        nicht bei sehr vielen Dateien."""
        files = self.get_files(self.EXTENSIONS, rescan=getattr(self, "_rescan_next", True))
        self._rescan_next = True
        self.empty_lbl.hide()

        if not files:
            self.show_empty()
            if self.date_filter:
                self.empty_lbl.setText(tr(self.lang, 'date_no_results'))
            return

        self.scroll.show()
        self.lbl_loading.hide()
        self._loaded_count = 0
        self._pending_total = len(files)
        self.view.set_items(files, [self._date_for_path(p) for p in files],
                            getattr(self, "_meta", {}), self.KIND, self.lang)

    def refresh(self):
        """Vuelve a listar la carpeta y redibuja (tras un sync, cambio de carpeta,
        orden o filtro)."""
        if not self.isVisible():
            self._needs_refresh = True    # se recarga al abrir la galeria
            return
        self.empty_lbl.hide()
        self.scroll.show()
        self.populate()

class VideoGallery(GalleryBase):
    KIND = "video"
    EXTENSIONS = (".mp4",)

    def __init__(self, folder, lang="de"):
        super().__init__(folder, tr(lang, "video_gallery_title"), "", tr(lang, "no_videos_yet"), lang)
        self._needs_refresh = True   # se carga al abrirla
        # "Unir videos partidos (N)": aparece si hay partes de la GoPro para unir
        self.btn_join = QPushButton("")
        self.btn_join.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_join.setStyleSheet(self.btn_sort.styleSheet())
        self.btn_join.hide()
        self.btn_join.clicked.connect(lambda: self.window().open_join_dialog()
                                      if hasattr(self.window(), "open_join_dialog") else None)
        self.header_row.insertWidget(self.header_row.indexOf(self.btn_date), self.btn_join)

    def refresh(self, *a, **k):
        r = super().refresh(*a, **k)
        self._update_join_button()
        return r

    def _update_join_button(self):
        def scan():
            try:
                groups = find_chapter_groups(self.folder)
            except Exception:
                groups = []
            return [g for g in groups if not g["done"] and not g["damaged"]]
        th = ScanThread(0, lambda: scan())
        th.done.connect(lambda _t, groups: self._show_join(groups))
        th.start()

    def _show_join(self, groups):
        n = len(groups or [])
        self.btn_join.setText(tr(self.lang, "join_btn", n=n))
        self.btn_join.setVisible(n > 0)

    def play_video(self, path):
        SoundManager.play_click()
        dlg = VideoPlayerDialog(path, gallery_dir=self.folder, lang=self.lang, parent=self)
        dlg.exec()

class PictureGallery(GalleryBase):
    KIND = "picture"
    EXTENSIONS = (".jpg", ".png", ".jpeg")

    def __init__(self, folder, lang="de"):
        super().__init__(folder, tr(lang, "picture_gallery_title"), "", tr(lang, "no_pictures_yet"), lang)
        self._needs_refresh = True   # se carga al abrirla

    def open_image(self, path):
        SoundManager.play_click()
        dlg = ImageViewerDialog(path, self)
        dlg.exec()
