"""Asistente de primer inicio y Ajustes."""
import os
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSlider, QStackedWidget, QTabWidget, QVBoxLayout, QWidget
from ..version import APP_VERSION
from ..env import FONT_STACK, _safe_local_dir, _valid_start_dir, save_config
from ..i18n import LANGUAGE_NATIVE_NAMES, tr
from ..winshell import set_autostart
from ..sound import SoundManager, TabHoverSoundFilter
from ..fileops import _is_same_or_inside
from ..ui.widgets import AvatarPreview, ToggleSwitch, _make_language_buttons, show_toast
from ..ui.thumbs import ThumbnailLoaderThread, move_thumb_cache, set_thumb_cache_dir

class OnboardingWizard(QWidget):
    """Einrichtungsassistent, der beim allerersten Start EINGEBETTET im
    Hauptfenster erscheint (keine separate Popup-Dialogbox) - drei
    Schritte mit weichem Überblend-Übergang dazwischen:
      1) Profilname + Profilbild
      2) Zielordner für synchronisierte Bilder/Videos
      3) Autostart mit Windows + automatisches Öffnen bei GoPro-Anschluss
    """
    finished_setup = Signal(dict)

    TOTAL_STEPS = 4   # idioma, perfil, carpeta, inicio automatico

    def __init__(self, lang, accent_color, default_target_dir, parent=None, on_language_selected=None):
        super().__init__(parent)
        self.lang = lang
        self.on_language_selected = on_language_selected
        self.accent_color = accent_color
        self.chosen_avatar_path = ""
        self.chosen_target_dir = default_target_dir
        self.step = 0
        self._anim_refs = []

        self.setStyleSheet(f"* {{ font-family: {FONT_STACK}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setFixedWidth(520)
        card.setObjectName("onboardCard")
        card.setStyleSheet(f"""
            QFrame#onboardCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1c1e29, stop:1 #262a3a);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 20px;
            }}
            QLabel {{ color: #ffffff; background: transparent; border: none; }}
        """)
        # HINWEIS: Bewusst KEIN add_drop_shadow() auf der Karte selbst - die
        # Karte enthaelt echte QPushButtons (Weiter/Zurueck/Ordner waehlen),
        # die beim Hover ihrerseits einen QGraphicsDropShadowEffect bekommen
        # (siehe GlowHoverAnimator). Zwei verschachtelte QGraphicsEffects
        # (Karte + Button) fuehren in Qt zu Render-Fehlern ("QPainter::begin:
        # A paint device can only be painted by one painter at a time") und
        # dazu, dass die betroffenen Buttons beim Hover verschwinden.
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(38, 34, 38, 26)
        card_layout.setSpacing(16)

        self.lbl_step = QLabel()
        self.lbl_step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_step.setStyleSheet("font-size: 11px; font-weight: 800; letter-spacing: 2px; color: rgba(255,255,255,0.4); border: none; background: transparent;")
        card_layout.addWidget(self.lbl_step)

        self.stack = QStackedWidget()
        card_layout.addWidget(self.stack)
        self.stack.addWidget(self._build_language_step())
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())

        nav_row = QHBoxLayout()
        self.btn_skip = QPushButton(tr(lang, "onboard_skip_btn"))
        self.btn_skip.setStyleSheet(
            "QPushButton { background: transparent; color: rgba(255,255,255,0.4); border: none; font-size: 12px; padding: 8px; } "
            "QPushButton:hover { color: #fff; }"
        )
        self.btn_skip.clicked.connect(self._skip)
        self.btn_back = QPushButton(tr(lang, "onboard_back_btn"))
        self.btn_back.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #fff; border-radius: 10px; padding: 10px 20px; border: 1px solid rgba(255,255,255,0.14); } "
            "QPushButton:hover { background: rgba(255,255,255,0.16); }"
        )
        self.btn_back.clicked.connect(self._go_back)
        self.btn_next = QPushButton(tr(lang, "onboard_next_btn"))
        self.btn_next.setStyleSheet(
            f"QPushButton {{ background: {accent_color}; color: #0d0d12; font-weight: 800; border-radius: 10px; padding: 10px 22px; border: none; }} "
            f"QPushButton:hover {{ background: {QColor(accent_color).lighter(115).name()}; }}"
        )
        self.btn_next.clicked.connect(self._go_next)
        nav_row.addWidget(self.btn_skip)
        nav_row.addStretch()
        nav_row.addWidget(self.btn_back)
        nav_row.addWidget(self.btn_next)
        card_layout.addLayout(nav_row)

        outer.addStretch(1)
        center_row = QHBoxLayout()
        center_row.addStretch(1)
        center_row.addWidget(card)
        center_row.addStretch(1)
        outer.addLayout(center_row)
        outer.addStretch(2)

        self._update_step_label()

    def _build_language_step(self):
        page = QWidget()
        p = QVBoxLayout(page)
        p.setSpacing(12)
        self.lbl_lang_title = QLabel(tr(self.lang, "onboard_language_title"))
        self.lbl_lang_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_lang_title.setStyleSheet("font-size: 22px; font-weight: 800; border: none; background: transparent;")
        self.lbl_lang_sub = QLabel(tr(self.lang, "onboard_language_sub"))
        self.lbl_lang_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_lang_sub.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 13px; border: none; background: transparent;")
        p.addWidget(self.lbl_lang_title)
        p.addWidget(self.lbl_lang_sub)
        p.addSpacing(8)
        box, self._lang_buttons = _make_language_buttons(self.lang, self.accent_color, self._pick_language)
        p.addLayout(box)
        p.addStretch()
        return page

    def _pick_language(self, code):
        """Cambia el idioma del asistente (y de toda la app) y avanza."""
        for c, b in self._lang_buttons.items():
            b.setChecked(c == code)
        if code != self.lang:
            self.lang = code
            if self.on_language_selected:
                self.on_language_selected(code)
            self._retranslate()
        self._go_next()

    def _retranslate(self):
        """Reconstruye los pasos 1-3 en el nuevo idioma (todavia no tienen datos)."""
        self.lbl_lang_title.setText(tr(self.lang, "onboard_language_title"))
        self.lbl_lang_sub.setText(tr(self.lang, "onboard_language_sub"))
        for _ in range(self.stack.count() - 1):
            w = self.stack.widget(1)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.addWidget(self._build_step1())
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())
        self.btn_skip.setText(tr(self.lang, "onboard_skip_btn"))
        self.btn_back.setText(tr(self.lang, "onboard_back_btn"))
        self._update_step_label()

    def _build_step1(self):
        page = QWidget()
        p = QVBoxLayout(page)
        p.setSpacing(12)

        title = QLabel(tr(self.lang, "onboard_welcome_title"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 800; border: none; background: transparent;")
        sub = QLabel(tr(self.lang, "onboard_welcome_sub"))
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 13px; border: none; background: transparent;")
        p.addWidget(title)
        p.addWidget(sub)
        p.addSpacing(8)

        self.avatar = AvatarPreview(size=92, accent=self.accent_color)
        self.avatar.changed.connect(lambda path: setattr(self, "chosen_avatar_path", path))
        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        avatar_row.addWidget(self.avatar)
        avatar_row.addStretch()
        p.addLayout(avatar_row)

        avatar_hint = QLabel(tr(self.lang, "onboard_avatar_hint"))
        avatar_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar_hint.setStyleSheet("color: #777; font-size: 11px; border: none; background: transparent;")
        p.addWidget(avatar_hint)

        name_label = QLabel(tr(self.lang, "onboard_name_label"))
        name_label.setStyleSheet("font-size: 13px; font-weight: 700; margin-top: 6px; border: none; background: transparent;")
        p.addWidget(name_label)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr(self.lang, "onboard_name_placeholder"))
        self.name_edit.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15);
                border-radius: 10px; padding: 10px 14px; font-size: 14px; color: #fff;
            }}
            QLineEdit:focus {{ border: 1px solid {self.accent_color}; }}
        """)
        self.name_edit.textChanged.connect(self.avatar.set_initial)
        p.addWidget(self.name_edit)
        p.addStretch()
        return page

    def _build_step2(self):
        page = QWidget()
        p = QVBoxLayout(page)
        p.setSpacing(14)

        title = QLabel(tr(self.lang, "onboard_path_title"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 20px; font-weight: 800; border: none; background: transparent;")
        sub = QLabel(tr(self.lang, "onboard_path_sub"))
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 12px; border: none; background: transparent;")
        p.addWidget(title)
        p.addWidget(sub)
        p.addSpacing(20)

        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        self.lbl_onboard_path = QLabel(self.chosen_target_dir)
        self.lbl_onboard_path.setWordWrap(True)
        self.lbl_onboard_path.setStyleSheet("""
            color: rgba(255,255,255,0.75); font-size: 12px; background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 10px 14px;
        """)
        btn_choose_path = QPushButton(tr(self.lang, "choose_folder_btn"))
        btn_choose_path.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #fff; border-radius: 8px; padding: 9px 16px; border: 1px solid rgba(255,255,255,0.14); } "
            "QPushButton:hover { background: rgba(255,255,255,0.16); }"
        )
        btn_choose_path.clicked.connect(self._pick_path)
        path_row.addWidget(self.lbl_onboard_path, 1)
        path_row.addWidget(btn_choose_path)
        p.addLayout(path_row)
        p.addStretch()
        return page

    def _build_step3(self):
        page = QWidget()
        p = QVBoxLayout(page)
        p.setSpacing(14)

        title = QLabel(tr(self.lang, "onboard_autostart_title"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 800; border: none; background: transparent;")
        p.addWidget(title)
        p.addSpacing(8)

        self.chk_autostart = ToggleSwitch(tr(self.lang, "onboard_autostart_checkbox"))
        self.chk_autostart.set_accent(self.accent_color)
        self.chk_autostart.toggled.connect(self._on_autostart_toggle)
        p.addWidget(self.chk_autostart)

        self.chk_open_on_connect = ToggleSwitch(tr(self.lang, "onboard_open_on_connect_checkbox"))
        self.chk_open_on_connect.set_accent(self.accent_color)
        self.chk_open_on_connect.toggled.connect(self._on_open_on_connect_toggle)
        p.addWidget(self.chk_open_on_connect)

        hint = QLabel(tr(self.lang, "onboard_open_on_connect_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #777; font-size: 11px; margin-left: 22px; border: none; background: transparent;")
        p.addWidget(hint)
        p.addStretch()
        return page

    def _pick_path(self):
        new_dir = QFileDialog.getExistingDirectory(
            self, tr(self.lang, "choose_target_dir_title"), _valid_start_dir(self.chosen_target_dir),
        )
        if new_dir:
            self.chosen_target_dir = os.path.normpath(new_dir)
            self.lbl_onboard_path.setText(self.chosen_target_dir)

    def _on_autostart_toggle(self, state):
        # "Abrir al conectar" necesita que la app arranque con Windows:
        # si se apaga el inicio automatico, se apaga tambien.
        if not state and self.chk_open_on_connect.isChecked():
            self.chk_open_on_connect.setChecked(False)

    def _on_open_on_connect_toggle(self, state):
        # Antes este interruptor quedaba deshabilitado (sin verse distinto)
        # hasta activar el otro, y parecia roto. Ahora activarlo enciende
        # tambien el inicio automatico, que es lo que necesita.
        if state and not self.chk_autostart.isChecked():
            self.chk_autostart.setChecked(True)

    def _update_step_label(self):
        last = self.TOTAL_STEPS - 1
        self.lbl_step.setText(tr(self.lang, "onboard_step_of", current=self.step + 1, total=self.TOTAL_STEPS))
        self.btn_back.setVisible(self.step > 0)
        self.btn_skip.setVisible(self.step == 1)
        self.btn_next.setText(tr(self.lang, "onboard_finish_btn") if self.step == last else tr(self.lang, "onboard_next_btn"))

    def _crossfade_to(self, index):
        """Weicher Überblend-Übergang zwischen den Schritten statt eines
        harten Umschaltens - macht den Assistenten deutlich "flüssiger".
        WICHTIG: Der Opacity-Effekt wird nach dem Einblenden wieder komplett
        entfernt (nicht nur auf 1.0 gesetzt) - sonst bleibt er dauerhaft auf
        self.stack haengen und kollidiert mit dem Hover-Glow-Effekt der
        Buttons innerhalb der Seiten (siehe GlowHoverAnimator) -> genau das
        fuehrte zu den Paint-Fehlern und verschwindenden Buttons."""
        effect = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(effect)

        anim_out = QPropertyAnimation(effect, b"opacity", self)
        anim_out.setDuration(140)
        anim_out.setStartValue(1.0)
        anim_out.setEndValue(0.0)
        anim_out.setEasingCurve(QEasingCurve.Type.InOutQuad)

        def _switch():
            self.stack.setCurrentIndex(index)
            anim_in = QPropertyAnimation(effect, b"opacity", self)
            anim_in.setDuration(200)
            anim_in.setStartValue(0.0)
            anim_in.setEndValue(1.0)
            anim_in.setEasingCurve(QEasingCurve.Type.InOutQuad)
            anim_in.finished.connect(lambda: self.stack.setGraphicsEffect(None))
            anim_in.start()
            self._anim_refs.append(anim_in)

        anim_out.finished.connect(_switch)
        anim_out.start()
        self._anim_refs.append(anim_out)

    def _go_next(self):
        SoundManager.play_click()
        if self.step < self.TOTAL_STEPS - 1:
            self.step += 1
            self._crossfade_to(self.step)
            self._update_step_label()
        else:
            self._finish()

    def _go_back(self):
        SoundManager.play_click()
        if self.step > 0:
            self.step -= 1
            self._crossfade_to(self.step)
            self._update_step_label()

    def _skip(self):
        SoundManager.play_click()
        self._finish(skipped=True)

    def _finish(self, skipped=False):
        result = {
            "language": self.lang,
            "profile_name": "" if skipped else self.name_edit.text().strip(),
            "profile_picture": "" if skipped else self.chosen_avatar_path,
            "target_dir": self.chosen_target_dir,
            "autostart_enabled": False if skipped else self.chk_autostart.isChecked(),
            "open_on_gopro_connect": False if skipped else self.chk_open_on_connect.isChecked(),
        }
        self.finished_setup.emit(result)


class SettingsDialog(QDialog):
    """Ein zusammengelegter Einstellungs-Dialog: Design (Akzentfarbe +
    Hintergrundbild + Weichzeichner), Speicherort (Zielordner) und Sprache
    (Deutsch/Englisch) - alles sauber in Abschnitten sortiert statt auf
    mehrere Buttons/Fenster verteilt."""

    def __init__(self, parent, config, on_apply, on_folder_change, on_language_change):
        super().__init__(parent)
        self.config = config
        self.on_apply = on_apply
        self.on_folder_change = on_folder_change
        self.on_language_change = on_language_change
        self.lang = config.get("language", "de")
        self._settings_sound_played = False
        self.setWindowTitle(f"{tr(self.lang, 'settings_window_title')}  ·  v{APP_VERSION}")
        self.setMinimumWidth(540)
        self.resize(540, 880)
        accent = config.get("accent_color", "#00f2fe")
        self.setStyleSheet(f"""
            QDialog {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1c1e29, stop:1 #262a3a);
                border-radius: 16px;
            }}
            * {{ color: #ffffff; font-family: {FONT_STACK}; }}
            QPushButton {{
                background-color: rgba(255,255,255,0.07);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 9px; padding: 9px 16px;
                font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: rgba(255,255,255,0.15); }}
            QPushButton:checked {{
                background-color: {accent}; color: #0d0d12; border: 1px solid {accent};
            }}
            QSlider::groove:horizontal {{
                height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {accent}; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px;
            }}
            QSlider::sub-page:horizontal {{
                background: {accent}; border-radius: 3px;
            }}
            QTabWidget::pane {{
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                top: -1px;
                background: rgba(255,255,255,0.02);
            }}
            QTabBar::tab {{
                background: transparent;
                color: rgba(255,255,255,0.5);
                font-size: 12px; font-weight: 700;
                padding: 9px 16px;
                margin-right: 4px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
            QTabBar::tab:hover {{ color: rgba(255,255,255,0.85); }}
            QTabBar::tab:selected {{
                background: rgba(255,255,255,0.07);
                color: #ffffff;
                border-bottom: 2px solid {accent};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)

        header = QLabel(tr(self.lang, "settings_header"))
        header.setStyleSheet("font-size: 18px; font-weight: 800; letter-spacing: 0.5px; background: transparent;")
        layout.addWidget(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        tab_design = QWidget()
        tab_folder = QWidget()
        tab_sounds = QWidget()
        tab_system = QWidget()
        self.tabs.addTab(tab_design, tr(self.lang, "tab_design"))
        self.tabs.addTab(tab_folder, tr(self.lang, "tab_folder"))
        self.tabs.addTab(tab_sounds, tr(self.lang, "tab_sounds"))
        self.tabs.addTab(tab_system, tr(self.lang, "tab_system"))

        self._tab_hover_filter = TabHoverSoundFilter(self.tabs.tabBar(), self)
        self.tabs.tabBar().installEventFilter(self._tab_hover_filter)
        self.tabs.currentChanged.connect(lambda _idx: SoundManager.play_click())

        # ============ Tab: Design ============
        design_layout = QVBoxLayout(tab_design)
        design_layout.setContentsMargins(18, 18, 18, 18)
        design_layout.setSpacing(14)

        color_row = QHBoxLayout()
        color_label = QLabel(tr(self.lang, "accent_color"))
        color_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        color_row.addWidget(color_label)
        color_row.addStretch()
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(48, 30)
        self._update_color_button()
        self.btn_color.clicked.connect(self.pick_color)
        color_row.addWidget(self.btn_color)
        design_layout.addLayout(color_row)

        bg_label = QLabel(tr(self.lang, "custom_background"))
        bg_label.setStyleSheet("font-size: 14px; font-weight: bold; margin-top: 6px;")
        design_layout.addWidget(bg_label)

        bg_btn_row = QHBoxLayout()
        self.btn_choose_bg = QPushButton(tr(self.lang, "choose_image_btn"))
        self.btn_choose_bg.clicked.connect(self.pick_background)
        self.btn_clear_bg = QPushButton(tr(self.lang, "remove_btn"))
        self.btn_clear_bg.clicked.connect(self.clear_background)
        bg_btn_row.addWidget(self.btn_choose_bg)
        bg_btn_row.addWidget(self.btn_clear_bg)
        design_layout.addLayout(bg_btn_row)

        self.lbl_bg_path = QLabel(self._bg_display_text())
        self.lbl_bg_path.setStyleSheet("color: #999; font-size: 11px;")
        self.lbl_bg_path.setWordWrap(True)
        design_layout.addWidget(self.lbl_bg_path)

        blur_row = QHBoxLayout()
        blur_title = QLabel(tr(self.lang, "blur_label"))
        blur_title.setStyleSheet("font-size: 14px; font-weight: bold;")
        blur_row.addWidget(blur_title)
        blur_row.addStretch()
        self.lbl_blur_value = QLabel(f"{int(config.get('background_blur', 0))} px")
        self.lbl_blur_value.setStyleSheet("color: #ccc; font-size: 12px;")
        blur_row.addWidget(self.lbl_blur_value)
        design_layout.addLayout(blur_row)

        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(0, 60)
        self.slider_blur.setValue(int(config.get("background_blur", 0)))
        self.slider_blur.valueChanged.connect(self.on_blur_changed)
        design_layout.addWidget(self.slider_blur)

        hint = QLabel(tr(self.lang, "blur_hint"))
        hint.setStyleSheet("color: #777; font-size: 11px;")
        hint.setWordWrap(True)
        design_layout.addWidget(hint)
        design_layout.addStretch()

        # ============ Tab: Speicherort ============
        folder_layout = QVBoxLayout(tab_folder)
        folder_layout.setContentsMargins(18, 18, 18, 18)
        folder_layout.setSpacing(14)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(12)
        self.lbl_folder = QLabel(self.config.get('target_dir', ''))
        self.lbl_folder.setStyleSheet("""
            QLabel {
                color: rgba(255,255,255,0.65); font-size: 12px;
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px; padding: 8px 12px;
            }
        """)
        self.lbl_folder.setWordWrap(True)
        self.btn_choose_folder = QPushButton(tr(self.lang, "choose_folder_btn"))
        self.btn_choose_folder.clicked.connect(self.pick_folder)
        folder_row.addWidget(self.lbl_folder, 1)
        folder_row.addWidget(self.btn_choose_folder)
        folder_layout.addLayout(folder_row)

        folder_hint = QLabel(tr(self.lang, "folder_hint"))
        folder_hint.setStyleSheet("color: #777; font-size: 11px;")
        folder_hint.setWordWrap(True)
        folder_layout.addWidget(folder_hint)

        divider_folder = QFrame()
        divider_folder.setFixedHeight(1)
        divider_folder.setStyleSheet("background-color: rgba(255,255,255,0.08); border: none; margin-top: 6px; margin-bottom: 6px;")
        folder_layout.addWidget(divider_folder)

        self.chk_sort_dates = ToggleSwitch(tr(self.lang, "sort_date_folders_checkbox"))
        self.chk_sort_dates.set_accent(self.config.get("accent_color", "#00f2fe"))
        self.chk_sort_dates.setChecked(self.config.get("sort_into_date_folders", True), animate=False)
        self.chk_sort_dates.toggled.connect(self.on_sort_dates_toggle)
        folder_layout.addWidget(self.chk_sort_dates)

        sort_hint = QLabel(tr(self.lang, "sort_date_folders_hint"))
        sort_hint.setStyleSheet("color: #777; font-size: 11px;")
        sort_hint.setWordWrap(True)
        folder_layout.addWidget(sort_hint)

        # --- Cache de miniaturas ---
        cache_title = QLabel(tr(self.lang, "thumb_cache_label"))
        cache_title.setStyleSheet("font-size: 13px; font-weight: 700;")
        folder_layout.addWidget(cache_title)
        cache_row = QHBoxLayout()
        cache_row.setSpacing(12)
        self.lbl_cache = QLabel(ThumbnailLoaderThread.CACHE_DIR)
        self.lbl_cache.setWordWrap(True)
        self.lbl_cache.setStyleSheet(self.lbl_folder.styleSheet())
        self.btn_cache = QPushButton(tr(self.lang, "thumb_cache_btn"))
        self.btn_cache.clicked.connect(self.pick_cache_dir)
        cache_row.addWidget(self.lbl_cache, 1)
        cache_row.addWidget(self.btn_cache)
        folder_layout.addLayout(cache_row)
        cache_hint = QLabel(tr(self.lang, "thumb_cache_hint"))
        cache_hint.setStyleSheet("color: #777; font-size: 11px;")
        cache_hint.setWordWrap(True)
        folder_layout.addWidget(cache_hint)

        # --- Respaldos en discos externos ---
        divider_backup = QFrame()
        divider_backup.setFixedHeight(1)
        divider_backup.setStyleSheet("background-color: rgba(255,255,255,0.08); border: none; margin-top: 6px; margin-bottom: 6px;")
        folder_layout.addWidget(divider_backup)

        section_backup = QLabel(tr(self.lang, "section_backup"))
        section_backup.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 1px; color: rgba(255,255,255,0.45); background: transparent;")
        folder_layout.addWidget(section_backup)

        self.list_backup = QListWidget()
        self.list_backup.setMinimumHeight(84)
        self.list_backup.setStyleSheet("""
            QListWidget {
                color: rgba(255,255,255,0.75); font-size: 12px;
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 4px;
            }
            QListWidget::item:selected { background-color: rgba(255,255,255,0.12); }
        """)
        for d in self.config.get("backup_dirs", []) or []:
            self.list_backup.addItem(d)
        folder_layout.addWidget(self.list_backup)

        backup_btn_row = QHBoxLayout()
        backup_btn_row.setSpacing(10)
        self.btn_backup_add = QPushButton(tr(self.lang, "backup_add_btn"))
        self.btn_backup_add.clicked.connect(self.add_backup_dir)
        self.btn_backup_remove = QPushButton(tr(self.lang, "backup_remove_btn"))
        self.btn_backup_remove.clicked.connect(self.remove_backup_dir)
        self.btn_backup_now = QPushButton(tr(self.lang, "backup_now_btn"))
        self.btn_backup_now.clicked.connect(self.backup_now)
        backup_btn_row.addWidget(self.btn_backup_add)
        backup_btn_row.addWidget(self.btn_backup_remove)
        backup_btn_row.addStretch()
        backup_btn_row.addWidget(self.btn_backup_now)
        folder_layout.addLayout(backup_btn_row)

        self.chk_backup_after = ToggleSwitch(tr(self.lang, "backup_after_sync_checkbox"))
        self.chk_backup_after.set_accent(self.config.get("accent_color", "#00f2fe"))
        self.chk_backup_after.setChecked(self.config.get("backup_after_sync", True), animate=False)
        self.chk_backup_after.toggled.connect(self.on_backup_after_toggle)
        folder_layout.addWidget(self.chk_backup_after)

        backup_hint = QLabel(tr(self.lang, "backup_hint"))
        backup_hint.setStyleSheet("color: #777; font-size: 11px;")
        backup_hint.setWordWrap(True)
        folder_layout.addWidget(backup_hint)
        self.chk_check_updates = ToggleSwitch(tr(self.lang, "update_check_setting"))
        self.chk_check_updates.set_accent(self.config.get("accent_color", "#00f2fe"))
        self.chk_check_updates.setChecked(self.config.get("check_updates", True), animate=False)
        self.chk_check_updates.toggled.connect(self._on_check_updates_toggle)
        folder_layout.addSpacing(10)
        folder_layout.addWidget(self.chk_check_updates)
        folder_layout.addStretch()

        # ============ Tab: Sounds ============
        sounds_layout = QVBoxLayout(tab_sounds)
        sounds_layout.setContentsMargins(18, 18, 18, 18)
        sounds_layout.setSpacing(14)

        vol_row = QHBoxLayout()
        vol_title = QLabel(tr(self.lang, "sound_volume_label"))
        vol_title.setStyleSheet("font-size: 14px; font-weight: bold;")
        vol_row.addWidget(vol_title)
        vol_row.addStretch()
        self.lbl_volume_value = QLabel(f"{int(config.get('ui_sound_volume', 70))}%")
        self.lbl_volume_value.setStyleSheet("color: #ccc; font-size: 12px;")
        vol_row.addWidget(self.lbl_volume_value)
        sounds_layout.addLayout(vol_row)

        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(int(config.get("ui_sound_volume", 70)))
        self.slider_volume.valueChanged.connect(self.on_volume_changed)
        self.slider_volume.sliderReleased.connect(self.on_volume_slider_released)
        sounds_layout.addWidget(self.slider_volume)

        self.chk_mute = ToggleSwitch(tr(self.lang, "sound_mute_checkbox"))
        self.chk_mute.set_accent(self.config.get("accent_color", "#00f2fe"))
        self.chk_mute.setChecked(self.config.get("ui_sounds_muted", False), animate=False)
        self.chk_mute.toggled.connect(self.on_mute_toggle)
        sounds_layout.addWidget(self.chk_mute)
        sounds_layout.addStretch()

        # ============ Tab: System (Autostart + Sprache) ============
        system_layout = QVBoxLayout(tab_system)
        system_layout.setContentsMargins(18, 18, 18, 18)
        system_layout.setSpacing(14)

        section_autostart = QLabel(tr(self.lang, "section_autostart"))
        section_autostart.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 1px; color: rgba(255,255,255,0.45); background: transparent;")
        system_layout.addWidget(section_autostart)

        self.chk_autostart = ToggleSwitch(tr(self.lang, "onboard_autostart_checkbox"))
        self.chk_autostart.set_accent(self.config.get("accent_color", "#00f2fe"))
        self.chk_autostart.setChecked(self.config.get("autostart_enabled", False), animate=False)
        self.chk_autostart.toggled.connect(self.on_autostart_toggle)
        system_layout.addWidget(self.chk_autostart)

        self.chk_open_on_connect = ToggleSwitch(tr(self.lang, "onboard_open_on_connect_checkbox"))
        self.chk_open_on_connect.set_accent(self.config.get("accent_color", "#00f2fe"))
        self.chk_open_on_connect.setChecked(self.config.get("open_on_gopro_connect", False), animate=False)
        self.chk_open_on_connect.toggled.connect(self.on_open_on_connect_toggle)
        system_layout.addWidget(self.chk_open_on_connect)

        divider_system = QFrame()
        divider_system.setFixedHeight(1)
        divider_system.setStyleSheet("background-color: rgba(255,255,255,0.08); border: none; margin-top: 6px; margin-bottom: 6px;")
        system_layout.addWidget(divider_system)

        section_lang = QLabel(tr(self.lang, "section_language"))
        section_lang.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 1px; color: rgba(255,255,255,0.45); background: transparent;")
        system_layout.addWidget(section_lang)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        self.btn_lang_es = QPushButton(LANGUAGE_NATIVE_NAMES["es"])
        self.btn_lang_es.setCheckable(True)
        self.btn_lang_en = QPushButton(LANGUAGE_NATIVE_NAMES["en"])
        self.btn_lang_en.setCheckable(True)
        self.btn_lang_de = QPushButton(LANGUAGE_NATIVE_NAMES["de"])
        self.btn_lang_de.setCheckable(True)
        self.btn_lang_es.setChecked(self.lang == "es")
        self.btn_lang_en.setChecked(self.lang == "en")
        self.btn_lang_de.setChecked(self.lang == "de")
        self.btn_lang_es.clicked.connect(lambda: self.set_language("es"))
        self.btn_lang_en.clicked.connect(lambda: self.set_language("en"))
        self.btn_lang_de.clicked.connect(lambda: self.set_language("de"))
        lang_row.addWidget(self.btn_lang_es)
        lang_row.addWidget(self.btn_lang_en)
        lang_row.addWidget(self.btn_lang_de)
        lang_row.addStretch()
        system_layout.addLayout(lang_row)
        system_layout.addStretch()

        btn_close = QPushButton(tr(self.lang, "close_btn"))
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    def showEvent(self, event):
        super().showEvent(event)
        # Zuverlaessiger als ein fest geratener QTimer-Delay vor dlg.exec():
        # showEvent() feuert garantiert erst, wenn der Dialog TATSAECHLICH
        # sichtbar auf dem Bildschirm ist und sein eigener (modaler)
        # Event-Loop laeuft - genau der Zeitpunkt, an dem der Sound
        # zuverlaessig komplett durchspielen kann, statt am Uebergang
        # abgeschnitten zu werden.
        if not self._settings_sound_played:
            self._settings_sound_played = True
            SoundManager.play_settings()

    def _bg_display_text(self):
        path = self.config.get("background_image", "")
        if path:
            return tr(self.lang, "bg_current", name=os.path.basename(path))
        return tr(self.lang, "bg_none")

    def _update_color_button(self):
        c = self.config.get("accent_color", "#00f2fe")
        self.btn_color.setStyleSheet(
            f"background-color: {c}; border-radius: 6px; border: 1px solid rgba(255,255,255,0.3);"
        )

    def pick_color(self):
        current = QColor(self.config.get("accent_color", "#00f2fe"))
        new_color = QColorDialog.getColor(current, self, tr(self.lang, "color_dialog_title"),
                                          QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if new_color.isValid():
            self.config["accent_color"] = new_color.name()
            self._update_color_button()
            self.on_apply()

    def pick_background(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr(self.lang, "choose_bg_title"), _safe_local_dir(),
            tr(self.lang, "image_filter"),
        )
        if path:
            self.config["background_image"] = path
            self.lbl_bg_path.setText(self._bg_display_text())
            self.on_apply()

    def clear_background(self):
        self.config["background_image"] = ""
        self.lbl_bg_path.setText(self._bg_display_text())
        self.on_apply()

    def on_blur_changed(self, value):
        self.config["background_blur"] = value
        self.lbl_blur_value.setText(f"{value} px")
        self.on_apply()

    def pick_folder(self):
        new_dir = QFileDialog.getExistingDirectory(
            self, tr(self.lang, "choose_target_dir_title"), _valid_start_dir(self.config.get("target_dir", "")),
        )
        if new_dir:
            new_dir = os.path.normpath(new_dir)
            self.lbl_folder.setText(new_dir)
            self.on_folder_change(new_dir)

    def on_autostart_toggle(self, state):
        enabled = bool(state)
        self.config["autostart_enabled"] = enabled
        if not enabled and self.chk_open_on_connect.isChecked():
            self.chk_open_on_connect.setChecked(False)
            self.config["open_on_gopro_connect"] = False
        save_config(self.config)
        set_autostart(enabled)

    def on_open_on_connect_toggle(self, state):
        self.config["open_on_gopro_connect"] = bool(state)
        save_config(self.config)
        if state and not self.chk_autostart.isChecked():
            # Necesita el inicio con Windows: activarlo tambien (y registrarlo).
            self.chk_autostart.setChecked(True)
            self.on_autostart_toggle(True)

    def on_sort_dates_toggle(self, state):
        self.config["sort_into_date_folders"] = bool(state)
        save_config(self.config)

    def pick_cache_dir(self):
        new_dir = QFileDialog.getExistingDirectory(
            self, tr(self.lang, "thumb_cache_title"), _valid_start_dir(ThumbnailLoaderThread.CACHE_DIR))
        if not new_dir:
            return
        new_dir = os.path.normpath(new_dir)
        moved = move_thumb_cache(ThumbnailLoaderThread.CACHE_DIR, new_dir)
        self.config["thumb_cache_dir"] = new_dir
        save_config(self.config)
        set_thumb_cache_dir(new_dir)
        self.lbl_cache.setText(new_dir)
        show_toast(self, tr(self.lang, "thumb_cache_moved", n=moved))

    def add_backup_dir(self):
        new_dir = QFileDialog.getExistingDirectory(
            self, tr(self.lang, "backup_choose_title"), _safe_local_dir(),
        )
        if not new_dir:
            return
        new_dir = os.path.normpath(new_dir)
        if _is_same_or_inside(new_dir, self.config.get("target_dir", "")):
            show_toast(self, tr(self.lang, "backup_same_as_target"))
            return
        dirs = list(self.config.get("backup_dirs", []) or [])
        if any(os.path.normcase(d) == os.path.normcase(new_dir) for d in dirs):
            return
        dirs.append(new_dir)
        self.config["backup_dirs"] = dirs
        save_config(self.config)
        self.list_backup.addItem(new_dir)

    def remove_backup_dir(self):
        row = self.list_backup.currentRow()
        dirs = list(self.config.get("backup_dirs", []) or [])
        if 0 <= row < len(dirs):
            dirs.pop(row)
            self.config["backup_dirs"] = dirs
            save_config(self.config)
            self.list_backup.takeItem(row)

    def backup_now(self):
        main = self.parent()
        if hasattr(main, "backup_from_menu"):
            main.backup_from_menu()
            # puede haber agregado un destino nuevo: refrescar la lista
            self.list_backup.clear()
            for d in self.config.get("backup_dirs", []) or []:
                self.list_backup.addItem(d)

    def on_backup_after_toggle(self, state):
        self.config["backup_after_sync"] = bool(state)
        save_config(self.config)

    def _on_check_updates_toggle(self, state):
        self.config["check_updates"] = bool(state)
        save_config(self.config)

    def on_volume_changed(self, value):
        self.config["ui_sound_volume"] = value
        save_config(self.config)
        self.lbl_volume_value.setText(f"{value}%")
        SoundManager.configure(value, self.config.get("ui_sounds_muted", False))

    def on_volume_slider_released(self):
        SoundManager.play_click()

    def on_mute_toggle(self, state):
        muted = bool(state)
        self.config["ui_sounds_muted"] = muted
        save_config(self.config)
        SoundManager.configure(self.config.get("ui_sound_volume", 70), muted)

    def set_language(self, new_lang):
        self.lang = new_lang
        self.btn_lang_es.setChecked(new_lang == "es")
        self.btn_lang_en.setChecked(new_lang == "en")
        self.btn_lang_de.setChecked(new_lang == "de")
        self.config["language"] = new_lang
        self.config["language_chosen"] = True
        save_config(self.config)
        self.on_language_change(new_lang)
