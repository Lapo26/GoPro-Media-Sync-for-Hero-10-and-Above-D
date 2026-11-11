"""Seccion Actividad: progreso, cola e historial."""
import time
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget
from ..i18n import _fmt_eta, _human_size, tr

class SyncProgressPanel(QFrame):
    """Progreso de sincronizacion/importacion INTEGRADO en la pantalla
    principal (debajo del boton): fase y total, barra total, archivo actual
    con velocidad/tiempo restante, barra por archivo y Cancelar. Al terminar
    muestra el resultado hasta que se cierra."""
    cancel_requested = Signal()

    def __init__(self, lang, accent, parent=None):
        super().__init__(parent)
        self.lang = lang
        self.sizes = []
        self.total_bytes = 0
        self._samples = []
        self.setObjectName("syncPanel")
        self.setFixedWidth(560)
        self.setStyleSheet(f"""
            QFrame#syncPanel {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12);
                               border-radius: 14px; }}
            QLabel {{ color: #fff; background: transparent; border: none; }}
            QPushButton {{ background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);
                          border-radius: 9px; padding: 5px 14px; font-weight: 600; font-size: 12px; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
            QProgressBar {{ background: rgba(255,255,255,0.08); border: none; border-radius: 5px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
            QProgressBar#file::chunk {{ background: rgba(255,255,255,0.5); }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        row1 = QHBoxLayout()
        self.lbl_phase = QLabel()
        self.lbl_phase.setStyleSheet("font-size: 13px; font-weight: 800;")
        self.lbl_total = QLabel()
        self.lbl_total.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.8);")
        row1.addWidget(self.lbl_phase)
        row1.addStretch()
        row1.addWidget(self.lbl_total)
        lay.addLayout(row1)

        self.bar_total = QProgressBar()
        self.bar_total.setTextVisible(False)
        self.bar_total.setFixedHeight(10)
        lay.addWidget(self.bar_total)

        row2 = QHBoxLayout()
        self.lbl_file = QLabel()
        self.lbl_file.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.7);")
        self.lbl_speed = QLabel()
        self.lbl_speed.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.55);")
        row2.addWidget(self.lbl_file)
        row2.addStretch()
        row2.addWidget(self.lbl_speed)
        lay.addLayout(row2)

        self.bar_file = QProgressBar()
        self.bar_file.setObjectName("file")
        self.bar_file.setRange(0, 1000)
        self.bar_file.setTextVisible(False)
        self.bar_file.setFixedHeight(5)
        lay.addWidget(self.bar_file)

        row3 = QHBoxLayout()
        self.lbl_result = QLabel()
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setStyleSheet("font-size: 12px;")
        self.lbl_queue = QLabel()
        self.lbl_queue.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.6);")
        self.btn_cancel = QPushButton(tr(lang, "cancel_btn"))
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedWidth(36)
        self.btn_close.clicked.connect(self.hide)
        row3.addWidget(self.lbl_result, 1)
        row3.addWidget(self.lbl_queue)
        row3.addWidget(self.btn_cancel)
        row3.addWidget(self.btn_close)
        lay.addLayout(row3)
        self.hide()

    # ---- ciclo de vida
    def start(self, title, worker, queued=0):
        self.sizes = []
        self.total_bytes = 0
        self._samples = []
        self.lbl_phase.ensurePolished()
        self.lbl_phase.setText(self.lbl_phase.fontMetrics().elidedText(title, Qt.TextElideMode.ElideMiddle, 300))
        self.lbl_phase.setToolTip(title)
        self.lbl_total.setText(tr(self.lang, "sync_phase_searching"))
        self.bar_total.setRange(0, 0)           # indeterminada mientras busca
        self.lbl_file.setText(" ")
        self.lbl_speed.setText(" ")
        self.bar_file.setValue(0)
        self.bar_file.show()
        self.lbl_result.setText("")
        self.set_queue(queued)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.show()
        self.btn_close.hide()
        worker.plan.connect(self.set_plan)
        worker.progress.connect(self.on_progress)
        worker.finished_sync.connect(self.on_finished)
        self.show()

    def set_queue(self, n):
        self.lbl_queue.setText(tr(self.lang, "sync_queue", n=n) if n else "")

    def set_plan(self, sizes):
        self.sizes = [int(x or 0) for x in sizes]
        self.total_bytes = max(1, sum(self.sizes))
        self.bar_total.setRange(0, 1000)
        self.bar_total.setValue(0)
        self._samples = [(time.monotonic(), 0)]
        self._update_total(0, 0.0)

    def _update_total(self, idx, pct):
        n = len(self.sizes)
        if not n:
            return
        before = sum(self.sizes[:max(0, idx - 1)])
        cur = self.sizes[idx - 1] * pct / 100.0 if 0 < idx <= n else 0
        done_bytes = before + cur
        self.bar_total.setValue(int(done_bytes / self.total_bytes * 1000))
        files_done = max(0, idx - 1) + (1 if pct >= 100 else 0)
        self.lbl_total.setText(tr(self.lang, "sync_files_progress", current=files_done, total=n,
                                  done=_human_size(done_bytes), size=_human_size(self.total_bytes)))
        now = time.monotonic()
        self._samples.append((now, done_bytes))
        self._samples = [sm for sm in self._samples if now - sm[0] <= 8.0] or [(now, done_bytes)]
        t_old, b_old = self._samples[0]
        if now - t_old >= 1.0 and done_bytes > b_old:
            speed = (done_bytes - b_old) / (now - t_old)
            eta = (self.total_bytes - done_bytes) / speed if speed > 0 else 0
            self.lbl_speed.setText(tr(self.lang, "sync_speed_eta", speed=_human_size(speed), eta=_fmt_eta(eta)))

    def total_percent(self, idx, pct):
        if not self.sizes:
            return 0.0
        before = sum(self.sizes[:max(0, idx - 1)])
        cur = self.sizes[idx - 1] * pct / 100.0 if 0 < idx <= len(self.sizes) else 0
        return min(100.0, (before + cur) / self.total_bytes * 100.0)

    def on_progress(self, idx, total, name, pct):
        if name.startswith("__CLEANUP__"):
            self.lbl_file.setText(name[len("__CLEANUP__"):])
            self.bar_file.setValue(0)
            self.btn_cancel.setEnabled(False)   # el borrado final no se interrumpe a medias
            return
        if self.sizes:
            self._update_total(idx, pct)
        self.lbl_file.setText(f"{name}   {pct:.0f}%")
        self.bar_file.setValue(int(pct * 10))

    def on_finished(self, ok, message, info):
        if self.bar_total.maximum() == 0:
            self.bar_total.setRange(0, 1000)
        if not info.get("cancelled"):
            self.bar_total.setValue(1000)
        self.lbl_file.setText(" ")
        self.lbl_speed.setText(" ")
        self.bar_file.hide()
        self.lbl_result.setText(message)
        self.btn_cancel.hide()
        self.btn_close.show()

    def _cancel(self):
        self.btn_cancel.setEnabled(False)
        self.lbl_file.setText(tr(self.lang, "sync_cancelling"))
        self.cancel_requested.emit()


class ActivityPage(QWidget):
    """Seccion "Actividad": la copia en curso (panel de progreso), la cola y
    el historial de la sesion con el detalle de cada error."""
    remove_queued = Signal(int)   # indice de la tarea en cola a quitar

    def __init__(self, lang, accent, parent=None):
        super().__init__(parent)
        self.lang = lang
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(14)
        self.title_lbl = QLabel(tr(lang, "activity_title"))
        self.title_lbl.setStyleSheet("font-size: 24px; font-weight: 800; color: #fff; background: transparent;")
        outer.addWidget(self.title_lbl)
        cap_style = "font-size: 12px; font-weight: 800; letter-spacing: 1px; color: rgba(255,255,255,0.45); background: transparent;"
        self.cap_current = QLabel(tr(lang, "activity_current"))
        self.cap_current.setStyleSheet(cap_style)
        outer.addWidget(self.cap_current)
        self.lbl_idle = QLabel(tr(lang, "activity_idle"))
        self.lbl_idle.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.5); background: transparent;")
        outer.addWidget(self.lbl_idle)
        self.panel_slot = QVBoxLayout()
        outer.addLayout(self.panel_slot)
        # Cola: una fila por tarea en espera, cada una con "Quitar"
        self.cap_queue = QLabel(tr(lang, "activity_queue"))
        self.cap_queue.setStyleSheet(cap_style + " margin-top: 6px;")
        self.cap_queue.hide()
        outer.addWidget(self.cap_queue)
        self.queue_box = QVBoxLayout()
        self.queue_box.setSpacing(6)
        outer.addLayout(self.queue_box)
        hist_head = QHBoxLayout()
        self.cap_history = QLabel(tr(lang, "activity_history"))
        self.cap_history.setStyleSheet(cap_style + " margin-top: 10px;")
        self.btn_clear_history = QPushButton(tr(lang, "history_clear"))
        self.btn_clear_history.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid rgba(255,255,255,0.14);"
            " border-radius: 8px; padding: 4px 12px; font-size: 12px; margin-top: 10px; }"
            " QPushButton:hover { background: rgba(255,255,255,0.16); }")
        self.btn_clear_history.clicked.connect(self.clear_history)
        self.btn_clear_history.hide()
        hist_head.addWidget(self.cap_history)
        hist_head.addStretch()
        hist_head.addWidget(self.btn_clear_history)
        outer.addLayout(hist_head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self.history = QVBoxLayout(host)
        self.history.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.history.setSpacing(10)
        self.lbl_empty = QLabel(tr(lang, "activity_empty"))
        self.lbl_empty.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.5);")
        self.history.addWidget(self.lbl_empty)
        self.scroll.setWidget(host)
        outer.addWidget(self.scroll, 1)

    def set_panel(self, panel):
        self.panel_slot.addWidget(panel, alignment=Qt.AlignmentFlag.AlignLeft)

    def set_running(self, running):
        self.lbl_idle.setVisible(not running)

    def set_queue(self, items):
        while self.queue_box.count():
            w = self.queue_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.cap_queue.setVisible(bool(items))
        for i, (label, tooltip) in enumerate(items):
            row = QFrame()
            row.setFixedWidth(560)
            row.setStyleSheet("QFrame { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.10);"
                              " border-radius: 10px; } QLabel { border: none; background: transparent; color: #fff; }"
                              " QPushButton { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid"
                              " rgba(255,255,255,0.14); border-radius: 8px; padding: 4px 12px; font-size: 12px; }"
                              " QPushButton:hover { background: rgba(255,90,100,0.35); }")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(12, 6, 8, 6)
            num = QLabel(f"{i + 1}.")
            num.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.5);")
            lbl = QLabel(lbl_text := label)
            lbl.setStyleSheet("font-size: 12px;")
            lbl.setToolTip(tooltip)
            lbl.ensurePolished()   # aplicar el tamano de letra del estilo antes de medir
            lbl.setText(lbl.fontMetrics().elidedText(lbl_text, Qt.TextElideMode.ElideMiddle, 380))
            lbl.setMinimumWidth(0)
            btn = QPushButton(tr(self.lang, "queue_remove"))
            btn.clicked.connect(lambda _=False, idx=i: self.remove_queued.emit(idx))
            lay.addWidget(num)
            lay.addWidget(lbl, 1)
            lay.addWidget(btn)
            self.queue_box.addWidget(row, alignment=Qt.AlignmentFlag.AlignLeft)

    def clear_history(self):
        for i in reversed(range(self.history.count())):
            w = self.history.itemAt(i).widget()
            if w is not None and w is not self.lbl_empty:
                self.history.takeAt(i)
                w.deleteLater()
        self.lbl_empty.show()
        self.btn_clear_history.hide()

    def remove_history(self, card):
        self.history.removeWidget(card)
        card.deleteLater()
        left = [self.history.itemAt(i).widget() for i in range(self.history.count())]
        if not any(w is not None and w is not self.lbl_empty and w is not card for w in left):
            self.lbl_empty.show()
            self.btn_clear_history.hide()

    def add_history(self, title, message, info):
        self.lbl_empty.hide()
        self.btn_clear_history.show()
        card = QFrame()
        card.setStyleSheet("QFrame { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.10);"
                           " border-radius: 12px; } QLabel { border: none; background: transparent; color: #fff; }"
                           " QPushButton { background: rgba(255,255,255,0.08); color: #fff; border: 1px solid"
                           " rgba(255,255,255,0.14); border-radius: 8px; padding: 4px 12px; font-size: 12px; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet("font-size: 13px; font-weight: 800;")
        when = QLabel(datetime.now().strftime("%H:%M"))
        when.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.5);")
        btn_del = QPushButton(tr(self.lang, "queue_remove"))
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.setStyleSheet("QPushButton { background: transparent; border: none; color: rgba(255,255,255,0.45);"
                              " font-size: 12px; padding: 2px 6px; } QPushButton:hover { color: #ff8a8a; }")
        btn_del.clicked.connect(lambda _=False, c=card: self.remove_history(c))
        head.addWidget(t)
        head.addStretch()
        head.addWidget(when)
        head.addWidget(btn_del)
        lay.addLayout(head)
        msg = QLabel(message)
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.8);")
        lay.addWidget(msg)
        errors = info.get("error_list") or []
        if errors:
            row = QHBoxLayout()
            warn = QLabel(tr(self.lang, "activity_errors", n=len(errors)))
            warn.setStyleSheet("font-size: 12px; font-weight: 700; color: #ff8a8a;")
            btn = QPushButton(tr(self.lang, "activity_show_errors"))
            row.addWidget(warn)
            row.addStretch()
            row.addWidget(btn)
            lay.addLayout(row)
            detail = QLabel("\n".join(f"• {name} — {reason}" for name, reason in errors))
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.75);")
            detail.hide()
            lay.addWidget(detail)

            def toggle():
                vis = not detail.isVisible()
                detail.setVisible(vis)
                btn.setText(tr(self.lang, "activity_hide_errors" if vis else "activity_show_errors"))
            btn.clicked.connect(toggle)
        self.history.insertWidget(0, card)
