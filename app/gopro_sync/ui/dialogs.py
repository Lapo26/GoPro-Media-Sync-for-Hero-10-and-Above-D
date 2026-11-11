"""Dialogos: importar, respaldar, confirmar, informacion, unir partes, idioma."""
import os
import shutil
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget
from ..env import FONT_STACK, _safe_local_dir, _valid_start_dir
from ..i18n import _human_size, tr, wrap_path
from ..winshell import show_in_explorer
from ..media import describe_media
from ..fileops import _is_same_or_inside, list_import_files, scan_backup, scan_import_source
from ..workers import ScanThread
from ..ui.widgets import ToggleSwitch, _make_language_buttons, show_toast
from ..ui.thumbs import MixedThumbProvider, ThumbGridView

class ImportPickDialog(QDialog):
    """Elegir que archivos de la tarjeta/carpeta importar: miniaturas por
    fecha con marca de ya importado / nuevo y seleccion (como en la camara)."""

    def __init__(self, parent, lang, source, target, accent, preselected=None):
        super().__init__(parent)
        self.lang, self.source, self.target = lang, source, target
        self.entries = {}
        self.all_paths = []
        self._preselected = preselected
        self.setWindowTitle(tr(lang, "import_pick_title"))
        self.setModal(True)
        self.resize(1120, 760)
        self.setStyleSheet(f"""
            QDialog {{ background: #1c1e29; }}
            QLabel {{ color: #fff; background: transparent; }}
            * {{ font-family: {FONT_STACK}; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
            QPushButton#primary {{ background: {accent}; color: #0d0d12; border: none; font-weight: 800; }}
            QPushButton#primary:disabled {{ background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.4); }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel(tr(lang, "import_pick_title"))
        title.setStyleSheet("font-size: 19px; font-weight: 800;")
        head.addWidget(title)
        head.addStretch()
        self.btn_sel_new = QPushButton(tr(lang, "cam_select_new"))
        self.btn_sel_none = QPushButton(tr(lang, "cam_select_none"))
        self.btn_sel_new.clicked.connect(lambda: self._select(lambda e: not e["synced"] and e["size"] > 0))
        self.btn_sel_none.clicked.connect(lambda: self._select(lambda e: False))
        head.addWidget(self.btn_sel_new)
        head.addWidget(self.btn_sel_none)
        lay.addLayout(head)
        row = QHBoxLayout()
        self.lbl_summary = QLabel(tr(lang, "import_scanning"))
        self.lbl_summary.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.6);")
        hint = QLabel(tr(lang, "import_pick_hint"))
        hint.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.4);")
        self.chk_only_new = ToggleSwitch(tr(lang, "cam_only_new"))
        self.chk_only_new.set_accent(accent)
        self.chk_only_new.toggled.connect(lambda _s: self._apply_filter())
        row.addWidget(self.lbl_summary)
        row.addSpacing(16)
        row.addWidget(hint)
        row.addStretch()
        row.addWidget(self.chk_only_new)
        lay.addLayout(row)
        self.view = ThumbGridView(self)
        self.view.accent = QColor(accent)
        self.view.selectable = True
        self.view.activated.connect(self.view.toggle_selected)   # clic = elegir / quitar
        self.view.selection_changed.connect(self._update_footer)
        lay.addWidget(self.view, 1)
        foot = QHBoxLayout()
        self.lbl_sel = QLabel("")
        self.lbl_sel.setStyleSheet(f"font-size: 13px; color: {accent};")
        cancel = QPushButton(tr(lang, "cancel_btn"))
        cancel.clicked.connect(self.reject)
        self.btn_use = QPushButton(tr(lang, "import_pick_use"))
        self.btn_use.setObjectName("primary")
        self.btn_use.setEnabled(False)
        self.btn_use.clicked.connect(self.accept)
        foot.addWidget(self.lbl_sel)
        foot.addStretch()
        foot.addWidget(cancel)
        foot.addWidget(self.btn_use)
        lay.addLayout(foot)
        self.provider = MixedThumbProvider(lang, self)
        th = ScanThread(1, list_import_files, source, target)
        th.done.connect(self._on_listed)
        th.start()

    def _on_listed(self, _token, entries):
        if entries is None:
            self.lbl_summary.setText(tr(self.lang, "import_empty"))
            return
        self.entries = {e["path"]: e for e in entries}
        self.all_paths = [e["path"] for e in entries]
        self.view.status = {pth: ("synced" if e["synced"] else "new") for pth, e in self.entries.items()}
        self.view.tooltips = {pth: f"{e['name']}  ·  {_human_size(e['size'])}" for pth, e in self.entries.items()}
        new = sum(1 for e in entries if not e["synced"])
        self.lbl_summary.setText(tr(self.lang, "import_pick_summary", total=len(entries), new=new))
        if self._preselected is not None:
            self.view.selected = {x for x in self._preselected if x in self.entries}
        else:
            self.view.selected = {pth for pth, e in self.entries.items() if not e["synced"] and e["size"] > 0}
        self._apply_filter(first=True)

    def _apply_filter(self, first=False):
        if not self.entries:
            return
        paths = [x for x in self.all_paths if not (self.chk_only_new.isChecked() and self.entries[x]["synced"])]
        dates = [datetime.fromtimestamp(self.entries[x]["mtime"]).strftime("%Y-%m-%d") for x in paths]
        self.view.set_items(paths, dates, {}, "picture", self.lang, provider=self.provider, keep_cache=not first)
        self._update_footer()

    def _select(self, pred):
        self.view.selected = {x for x in self.view.files if pred(self.entries[x])}
        self.view.viewport().update()
        self._update_footer()

    def _update_footer(self):
        sel = [x for x in self.view.selected if x in self.entries]
        self.lbl_sel.setText(tr(self.lang, "cam_selection", n=len(sel),
                                size=_human_size(sum(self.entries[x]["size"] for x in sel))))
        self.btn_use.setEnabled(bool(sel))

    def selected_paths(self):
        return [x for x in self.all_paths if x in self.view.selected]

    def done(self, r):
        self.view.shutdown()
        super().done(r)


class ImportConfirmDialog(QDialog):
    """Paso 2 de "Importar SD / carpeta": resumen + origen y destino
    cambiables antes de empezar."""

    def __init__(self, parent, lang, source, target, accent):
        super().__init__(parent)
        self.lang = lang
        self.source = source
        self.target = target
        self.accent = accent
        self.only_paths = None       # None = todo lo nuevo; lista = solo esos archivos
        self.setWindowTitle(tr(lang, "import_confirm_title"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(f"""
            QDialog {{ background: #1c1e29; }}
            QLabel {{ color: #fff; background: transparent; }}
            * {{ font-family: {FONT_STACK}; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
            QPushButton#primary {{ background: {accent}; color: #0d0d12; border: none; font-weight: 800; }}
            QPushButton#primary:disabled {{ background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.4); }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(12)

        title = QLabel(tr(lang, "import_confirm_title"))
        title.setStyleSheet("font-size: 19px; font-weight: 800;")
        lay.addWidget(title)

        path_style = ("color: rgba(255,255,255,0.8); font-size: 12px; background: rgba(255,255,255,0.05);"
                      " border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 8px 10px;")
        self.lbl_source = QLabel()
        self.lbl_target = QLabel()
        for caption_key, lbl, handler in (("import_from", self.lbl_source, self._change_source),
                                          ("import_to", self.lbl_target, self._change_target)):
            cap = QLabel(tr(lang, caption_key))
            cap.setStyleSheet("font-size: 12px; font-weight: 800; color: rgba(255,255,255,0.5); letter-spacing: 1px;")
            lay.addWidget(cap)
            row = QHBoxLayout()
            lbl.setStyleSheet(path_style)
            lbl.setWordWrap(True)
            btn = QPushButton(tr(lang, "import_change"))
            btn.clicked.connect(handler)
            row.addWidget(lbl, 1)
            row.addWidget(btn)
            lay.addLayout(row)

        self.lbl_summary = QLabel()
        self.lbl_summary.setStyleSheet("font-size: 15px; font-weight: 700; margin-top: 6px;")
        self.lbl_new = QLabel()
        self.lbl_new.setStyleSheet(f"font-size: 13px; color: {accent};")
        self.lbl_new.setWordWrap(True)
        lay.addWidget(self.lbl_summary)
        lay.addWidget(self.lbl_new)

        buttons = QHBoxLayout()
        self.btn_pick = QPushButton(tr(lang, "import_pick_btn"))
        self.btn_pick.setEnabled(False)
        self.btn_pick.clicked.connect(self._pick_files)
        buttons.addWidget(self.btn_pick)
        buttons.addStretch()
        cancel = QPushButton(tr(lang, "cancel_btn"))
        cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton(tr(lang, "import_btn"))
        self.btn_ok.setObjectName("primary")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.btn_ok)
        lay.addLayout(buttons)
        self._refresh()

    def _refresh(self):
        self.lbl_source.setText(wrap_path(self.source))
        self.lbl_target.setText(wrap_path(self.target))
        self.lbl_summary.setText(tr(self.lang, "import_scanning"))
        self.lbl_new.setText("")
        self.btn_ok.setEnabled(False)
        self.btn_pick.setEnabled(False)
        self.only_paths = None           # cambio el origen o el destino: la eleccion anterior no vale
        # Contar en segundo plano: la ventana sigue respondiendo (se puede
        # cancelar o cambiar la carpeta mientras tanto).
        self._scan_token = getattr(self, "_scan_token", 0) + 1
        th = ScanThread(self._scan_token, scan_import_source, self.source, self.target)
        th.done.connect(self._on_scanned)
        th.start()

    def _on_scanned(self, token, info):
        if token != self._scan_token:
            return                  # resultado viejo: se cambio la carpeta o el destino
        if info is None:
            self.lbl_summary.setText(tr(self.lang, "import_empty"))
            return
        self.info = info
        if info["photos"] + info["videos"] == 0:
            self.lbl_summary.setText(tr(self.lang, "import_empty"))
            return
        self.btn_pick.setEnabled(True)
        self.lbl_summary.setText(tr(self.lang, "import_summary", photos=info["photos"], videos=info["videos"],
                                    size=_human_size(info["bytes"])))
        if info["new"] == 0:
            self.lbl_new.setText(tr(self.lang, "import_nothing_new", n=info["existing"]))
        else:
            self.lbl_new.setText(tr(self.lang, "import_new_summary", new=info["new"], existing=info["existing"])
                                 + f"  ({_human_size(info['new_bytes'])})")
            self.btn_ok.setEnabled(True)

    def _change_source(self):
        d = QFileDialog.getExistingDirectory(self, tr(self.lang, "import_title"), _valid_start_dir(self.source))
        if d:
            self.source = os.path.normpath(d)
            self._refresh()

    def _change_target(self):
        d = QFileDialog.getExistingDirectory(self, tr(self.lang, "choose_target_dir_title"), _valid_start_dir(self.target))
        if d:
            self.target = os.path.normpath(d)
            self._refresh()

    def _pick_files(self):
        dlg = ImportPickDialog(self, self.lang, self.source, self.target, self.accent, preselected=self.only_paths)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.only_paths = dlg.selected_paths()
        size = sum(dlg.entries[x]["size"] for x in self.only_paths)
        self.lbl_new.setText(tr(self.lang, "import_picked", n=len(self.only_paths), size=_human_size(size)))
        self.btn_ok.setEnabled(bool(self.only_paths))

    def accept(self):
        if _is_same_or_inside(self.source, self.target) or _is_same_or_inside(self.target, self.source):
            show_toast(self, tr(self.lang, "import_same_as_target"))
            return
        super().accept()


class BackupDialog(QDialog):
    """Respaldar a disco: confirmar carpeta a respaldar y destino (ambos
    cambiables) con un resumen. La copia corre despues en Actividad."""

    def __init__(self, parent, lang, source, dest, accent, remember_default=True, verify_default=False):
        super().__init__(parent)
        self.lang = lang
        self.source = source
        self.dest = dest or ""
        self.setWindowTitle(tr(lang, "backup_dialog_title"))
        self.setModal(True)
        self.setMinimumWidth(580)
        self.setStyleSheet(f"""
            QDialog {{ background: #1c1e29; }}
            QLabel {{ color: #fff; background: transparent; }}
            * {{ font-family: {FONT_STACK}; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
            QPushButton#primary {{ background: {accent}; color: #0d0d12; border: none; font-weight: 800; }}
            QPushButton#primary:disabled {{ background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.4); }}
            QProgressBar {{ background: rgba(255,255,255,0.08); border: none; border-radius: 8px; height: 16px;
                           text-align: center; color: #fff; font-weight: 700; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 8px; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 20)
        outer.setSpacing(12)
        title = QLabel(tr(lang, "backup_dialog_title"))
        title.setStyleSheet("font-size: 19px; font-weight: 800;")
        outer.addWidget(title)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        # ---- pagina 1: confirmacion
        p1 = QWidget()
        lay = QVBoxLayout(p1)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        path_style = ("color: rgba(255,255,255,0.8); font-size: 12px; background: rgba(255,255,255,0.05);"
                      " border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 8px 10px;")
        self.lbl_source = QLabel()
        self.lbl_dest = QLabel()
        for caption_key, lbl, handler in (("backup_from", self.lbl_source, self._change_source),
                                          ("backup_to", self.lbl_dest, self._change_dest)):
            cap = QLabel(tr(lang, caption_key))
            cap.setStyleSheet("font-size: 12px; font-weight: 800; color: rgba(255,255,255,0.5); letter-spacing: 1px;")
            lay.addWidget(cap)
            row = QHBoxLayout()
            lbl.setStyleSheet(path_style)
            lbl.setWordWrap(True)
            btn = QPushButton(tr(lang, "import_change"))
            btn.clicked.connect(handler)
            row.addWidget(lbl, 1)
            row.addWidget(btn)
            lay.addLayout(row)
        self.lbl_summary = QLabel()
        self.lbl_summary.setStyleSheet("font-size: 15px; font-weight: 700; margin-top: 6px;")
        self.lbl_pending = QLabel()
        self.lbl_pending.setWordWrap(True)
        self.lbl_pending.setStyleSheet(f"font-size: 13px; color: {accent};")
        lay.addWidget(self.lbl_summary)
        lay.addWidget(self.lbl_pending)
        self.chk_remember = ToggleSwitch(tr(lang, "backup_remember"))
        self.chk_remember.set_accent(accent)
        self.chk_remember.setChecked(remember_default, animate=False)
        lay.addWidget(self.chk_remember)
        self.chk_verify = ToggleSwitch(tr(lang, "backup_verify"))
        self.chk_verify.set_accent(accent)
        self.chk_verify.setChecked(verify_default, animate=False)
        lay.addWidget(self.chk_verify)
        note = QLabel(tr(lang, "backup_hint"))
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px; color: #888;")
        lay.addWidget(note)
        b1 = QHBoxLayout()
        b1.addStretch()
        cancel = QPushButton(tr(lang, "cancel_btn"))
        cancel.clicked.connect(self.reject)
        self.btn_start = QPushButton(tr(lang, "backup_start_btn"))
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        b1.addWidget(cancel)
        b1.addWidget(self.btn_start)
        lay.addLayout(b1)
        self.pages.addWidget(p1)

        self._refresh()

    # ---- confirmacion
    def _refresh(self):
        self.lbl_source.setText(wrap_path(self.source))
        self.lbl_dest.setText(wrap_path(self.dest) if self.dest else "—")
        self.btn_start.setEnabled(False)
        if not self.dest:
            self.lbl_summary.setText("")
            self.lbl_pending.setText(tr(self.lang, "backup_dest_missing"))
            return
        self.lbl_summary.setText(tr(self.lang, "import_scanning"))
        self.lbl_pending.setText("")
        self._scan_token = getattr(self, "_scan_token", 0) + 1
        th = ScanThread(self._scan_token, scan_backup, self.source, self.dest)
        th.done.connect(self._on_scanned)
        th.start()

    def _on_scanned(self, token, info):
        if token != self._scan_token:
            return                  # resultado viejo: se cambio la carpeta o el destino
        if info is None:
            self.lbl_summary.setText("")
            return
        self.lbl_summary.setText(tr(self.lang, "backup_summary", total=info["total"], size=_human_size(info["bytes"])))
        if info["pending"] == 0:
            self.lbl_pending.setText(tr(self.lang, "backup_up_to_date"))
        else:
            self.lbl_pending.setText(tr(self.lang, "backup_pending", n=info["pending"],
                                        size=_human_size(info["pending_bytes"]), skipped=info["skipped"]))
            self.btn_start.setEnabled(True)

    def _change_source(self):
        d = QFileDialog.getExistingDirectory(self, tr(self.lang, "backup_choose_source_title"), _valid_start_dir(self.source))
        if d:
            self.source = os.path.normpath(d)
            self._refresh()

    def _change_dest(self):
        d = QFileDialog.getExistingDirectory(self, tr(self.lang, "backup_choose_title"),
                                             _valid_start_dir(self.dest) if self.dest else _safe_local_dir())
        if d:
            self.dest = os.path.normpath(d)
            self._refresh()

    def _start(self):
        if _is_same_or_inside(self.dest, self.source) or _is_same_or_inside(self.source, self.dest):
            show_toast(self, tr(self.lang, "backup_same_as_target"))
            return
        self.accept()      # la copia corre en segundo plano (Actividad)

    def remember(self):
        return self.chk_remember.isChecked()


class ConfirmDeleteDialog(QDialog):
    """Confirmacion antes de eliminar (con el estilo de la app)."""

    def __init__(self, parent, lang, path):
        super().__init__(parent)
        self.setWindowTitle(tr(lang, "delete_title"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            f"QDialog {{ background: #1c1e29; }} QLabel {{ color: #fff; background: transparent; }}"
            f" * {{ font-family: {FONT_STACK}; }}"
            " QPushButton { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);"
            " border-radius: 10px; padding: 8px 18px; font-weight: 600; }"
            " QPushButton:hover { background: rgba(255,255,255,0.16); }"
            " QPushButton#danger { background: #e5484d; border: none; font-weight: 800; }"
            " QPushButton#danger:hover { background: #f2555a; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(10)
        q = QLabel(tr(lang, "delete_question", name=os.path.basename(path)))
        q.setWordWrap(True)
        q.setStyleSheet("font-size: 17px; font-weight: 800;")
        d = QLabel(tr(lang, "delete_detail"))
        d.setWordWrap(True)
        d.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.65);")
        where = QLabel(wrap_path(os.path.dirname(path)))
        where.setWordWrap(True)
        where.setStyleSheet("font-size: 11px; color: rgba(255,255,255,0.45);")
        lay.addWidget(q)
        lay.addWidget(d)
        lay.addWidget(where)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr(lang, "cancel_btn"))
        cancel.setDefault(True)               # Enter = cancelar (lo seguro)
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr(lang, "delete_btn"))
        ok.setObjectName("danger")
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)


class ConfirmQuitDialog(QDialog):
    """Salir con una copia en curso: confirmar (con el estilo de la app)."""

    def __init__(self, parent, lang):
        super().__init__(parent)
        self.setWindowTitle(tr(lang, "quit_busy_title"))
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setStyleSheet(
            f"QDialog {{ background: #1c1e29; }} QLabel {{ color: #fff; background: transparent; }}"
            f" * {{ font-family: {FONT_STACK}; }}"
            " QPushButton { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);"
            " border-radius: 10px; padding: 8px 18px; font-weight: 600; }"
            " QPushButton:hover { background: rgba(255,255,255,0.16); }"
            " QPushButton#danger { background: #e5484d; border: none; font-weight: 800; }"
            " QPushButton#danger:hover { background: #f2555a; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(10)
        q = QLabel(tr(lang, "quit_busy_question"))
        q.setWordWrap(True)
        q.setStyleSheet("font-size: 17px; font-weight: 800;")
        d = QLabel(tr(lang, "quit_busy_detail"))
        d.setWordWrap(True)
        d.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.65);")
        lay.addWidget(q)
        lay.addWidget(d)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch()
        keep = QPushButton(tr(lang, "quit_busy_keep"))
        keep.setDefault(True)                 # Enter = seguir copiando (lo seguro)
        keep.clicked.connect(self.reject)
        ok = QPushButton(tr(lang, "quit_busy_quit"))
        ok.setObjectName("danger")
        ok.clicked.connect(self.accept)
        row.addWidget(keep)
        row.addWidget(ok)
        lay.addLayout(row)


class MediaInfoDialog(QDialog):
    """Ficha de informacion de una foto/video (clic derecho > Informacion)."""

    def __init__(self, parent, lang, path, accent):
        super().__init__(parent)
        self.setWindowTitle(tr(lang, "info_title"))
        self.setMinimumWidth(560)
        self.setStyleSheet(f"""
            QDialog {{ background: #1c1e29; }}
            QLabel {{ color: #fff; background: transparent; }}
            * {{ font-family: {FONT_STACK}; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
            QPushButton#primary {{ background: {accent}; color: #0d0d12; border: none; font-weight: 800; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(6)
        t = QLabel(os.path.basename(path))
        t.setStyleSheet("font-size: 18px; font-weight: 800;")
        lay.addWidget(t)
        current = None
        grid = None
        for section, label, val in describe_media(path, lang):
            if section != current:
                current = section
                cap = QLabel(tr(lang, "info_file" if section == "file" else "info_media"))
                cap.setStyleSheet("font-size: 11px; font-weight: 800; letter-spacing: 1px; color: rgba(255,255,255,0.45);"
                                  " margin-top: 10px;")
                lay.addWidget(cap)
                grid = QGridLayout()
                grid.setHorizontalSpacing(16)
                grid.setVerticalSpacing(5)
                grid.setColumnStretch(1, 1)
                grid.setColumnMinimumWidth(0, 130)   # mismas columnas en ambas secciones
                lay.addLayout(grid)
            r = grid.rowCount()
            k = QLabel(label)
            k.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.55);")
            v = QLabel(wrap_path(val) if label == tr(lang, "info_path") else val)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setStyleSheet("font-size: 13px;")
            grid.addWidget(k, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(v, r, 1)
        lay.addSpacing(10)
        row = QHBoxLayout()
        b_copy = QPushButton(tr(lang, "ctx_copy_path"))
        b_copy.clicked.connect(lambda: (QApplication.clipboard().setText(os.path.normpath(path)),
                                        show_toast(self, tr(lang, "path_copied"))))
        b_show = QPushButton(tr(lang, "ctx_show_in_explorer"))
        b_show.clicked.connect(lambda: show_in_explorer(path))
        b_close = QPushButton(tr(lang, "close_btn2"))
        b_close.setObjectName("primary")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_copy)
        row.addWidget(b_show)
        row.addStretch()
        row.addWidget(b_close)
        lay.addLayout(row)


class LanguageDialog(QDialog):
    """Se muestra una vez en instalaciones que ya habian hecho el asistente
    antes de que existiera la eleccion de idioma (quedaban en aleman)."""

    def __init__(self, parent, current, accent):
        super().__init__(parent)
        self.chosen = current
        self.setWindowTitle("GoPro Sync Pro")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setStyleSheet(f"QDialog {{ background: #1c1e29; }} QLabel {{ color: #fff; background: transparent; }}"
                           f" * {{ font-family: {FONT_STACK}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        title = QLabel("Idioma · Language · Sprache")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        lay.addWidget(title)
        box, _ = _make_language_buttons(current, accent, self._pick)
        lay.addLayout(box)

    def _pick(self, code):
        self.chosen = code
        self.accept()


class JoinDialog(QDialog):
    """Lista los videos partidos y los une (todos o los elegidos)."""

    def __init__(self, parent, lang, groups, accent):
        super().__init__(parent)
        self.lang = lang
        self.groups = [g for g in groups if not g["done"]]
        self.setWindowTitle(tr(lang, "join_title"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(f"""
            QDialog {{ background: #1c1e29; }}
            QLabel {{ color: #fff; background: transparent; }}
            * {{ font-family: {FONT_STACK}; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
            QPushButton#primary {{ background: {accent}; color: #0d0d12; border: none; font-weight: 800; }}
            QPushButton#primary:disabled {{ background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.4); }}
            QScrollArea {{ border: none; background: transparent; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(10)
        title = QLabel(tr(lang, "join_title"))
        title.setStyleSheet("font-size: 19px; font-weight: 800;")
        intro = QLabel(tr(lang, "join_intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.65);")
        lay.addWidget(title)
        lay.addWidget(intro)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 4, 0, 4)
        bl.setSpacing(6)
        self.toggles = []
        for g in self.groups:
            folder = os.path.basename(os.path.dirname(g["parts"][0]))
            if g["damaged"]:
                lbl = QLabel(tr(lang, "join_damaged", name=f"{folder} · {g['name']}", part=", ".join(g["damaged"])))
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size: 12px; color: #ff8a8a;")
                bl.addWidget(lbl)
                continue
            t = ToggleSwitch(tr(lang, "join_group", name=f"{folder} · {g['name']}", n=len(g["parts"]),
                                size=_human_size(g["size"])))
            t.set_accent(accent)
            t.setChecked(True, animate=False)
            t.toggled.connect(lambda _s: self._update())
            self.toggles.append((t, g))
            bl.addWidget(t)
        if not self.groups:
            none = QLabel(tr(lang, "join_none"))
            none.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.6);")
            bl.addWidget(none)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        scroll.setMinimumHeight(min(360, 40 + 34 * max(1, len(self.groups))))
        lay.addWidget(scroll, 1)
        self.chk_recycle = ToggleSwitch(tr(lang, "join_recycle"))
        self.chk_recycle.set_accent(accent)
        lay.addWidget(self.chk_recycle)
        self.lbl_space = QLabel("")
        self.lbl_space.setWordWrap(True)
        self.lbl_space.setStyleSheet("font-size: 12px; color: #ff8a8a;")
        lay.addWidget(self.lbl_space)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr(lang, "cancel_btn"))
        cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("")
        self.btn_ok.setObjectName("primary")
        self.btn_ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.btn_ok)
        lay.addLayout(row)
        self._update()

    def chosen(self):
        return [g for t, g in self.toggles if t.isChecked()]

    def _update(self):
        sel = self.chosen()
        self.btn_ok.setText(tr(self.lang, "join_start", n=len(sel)))
        need = sum(g["size"] for g in sel)
        ok = bool(sel)
        self.lbl_space.setText("")
        if sel:
            try:
                free = shutil.disk_usage(os.path.dirname(sel[0]["parts"][0])).free
                if free < need * 1.02:
                    self.lbl_space.setText(tr(self.lang, "join_space", need=_human_size(need), free=_human_size(free)))
                    ok = False
            except Exception:
                pass
        self.btn_ok.setEnabled(ok)
