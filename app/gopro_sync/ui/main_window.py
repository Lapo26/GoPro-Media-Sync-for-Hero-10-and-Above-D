"""Ventana principal, pantalla de carga y bandeja del sistema."""
import os
import sys
from PySide6.QtCore import QEvent, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget
from ..env import (
    FONT_STACK,
    IS_WINDOWS,
    LAST_DEVICE_NAME,
    SUPPORTED_LANGUAGES,
    _safe_local_dir,
    asset_path,
    load_config,
    save_config,
)
from ..i18n import tr
from ..winshell import TaskbarProgress, apply_dark_titlebar, bring_to_front, set_autostart, show_in_explorer
from ..fileops import JOINED_PARTS
from ..videotools import find_chapter_groups, join_job, light_version_job, repair_job
from ..ui.graphics import (
    add_text_shadow,
    apply_popup_style,
    boot_step,
    cover_scaled_pixmap,
    hidpi_scaled_to_width,
    load_app_icon,
    make_blurred_pixmap,
    screen_dpr,
    set_nav_text,
    trim_transparent_margins,
)
from ..ui.widgets import ActivityNavButton, AvatarPreview, show_toast
from ..updates import UpdateCheckThread, UpdateDialog
from ..ui.thumbs import set_thumb_cache_dir
from ..ui.dialogs import BackupDialog, ConfirmQuitDialog, ImportConfirmDialog, JoinDialog, LanguageDialog
from ..ui.gallery import PictureGallery, VideoGallery
from ..ui.camera_page import CameraGalleryPage
from ..ui.sync_tab import SyncTabWidget
from ..ui.settings import OnboardingWizard, SettingsDialog

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        boot_step(10, "boot_ui")
        self.config = load_config()
        apply_popup_style(self.config.get("accent_color", "#00f2fe"))
        set_thumb_cache_dir(self.config.get("thumb_cache_dir", ""))
        self.lang = self.config.get("language", "de")
        self.setWindowTitle("GoPro Sync Pro")
        self.setWindowIcon(load_app_icon())
        self.resize(1000, 650)
        
        self.setObjectName("MainWindow")
        self.setStyleSheet(f"""
            QWidget#MainWindow {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #14151f, stop:0.5 #1c1e2b, stop:1 #262a3d);
            }}
            * {{ color: #ffffff; font-family: {FONT_STACK}; }}
            QToolTip {{
                background-color: #23252f;
                color: #eaeaf0;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 4px 2px 4px 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.18);
                min-height: 30px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.32);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

        # Eigenes Hintergrundbild (optional) - liegt als "freier" Kind-Widget
        # unterhalb von Sidebar/Seiten und wird per resizeEvent passend zur
        # Fenstergröße zugeschnitten (wie CSS "background-size: cover").
        # Ohne eigenes Bild bleibt es unsichtbar und man sieht den normalen
        # Verlaufshintergrund von oben.
        self.bg_label = QLabel(self)
        # Make sure the background label does not block mouse events and can be placed behind other widgets
        self.bg_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.bg_label.setScaledContents(True)
        self.bg_label.setStyleSheet("background: transparent;")
        # FIX (Freeze beim Setzen/Anzeigen eines eigenen Hintergrundbilds):
        # Es haengt bewusst KEIN live QGraphicsBlurEffect mehr am Label -
        # das wurde bei jedem einzelnen Repaint (Fenstergroesse, Dialoge,
        # Hover-Animationen, ...) komplett neu berechnet und hat dabei die
        # GUI-Thread jedes Mal fuer die Dauer des Weichzeichnens blockiert.
        # Stattdessen wird der Blur jetzt einmalig in apply_background()
        # ueber make_blurred_pixmap() vorberechnet (siehe dort).
        self._bg_source_pixmap = None
        self._bg_cache_key = None
        self.bg_label.hide()
        self.bg_label.lower()

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebarFrame")
        self.sidebar.setFixedWidth(230)
        self.sidebar.setStyleSheet("""
            QFrame#sidebarFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(18,19,28,0.85), stop:1 rgba(12,13,20,0.85));
                border-right: 1px solid rgba(255,255,255,0.08);
            }
            QLabel { border: none; }
        """)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(18, 26, 18, 20)
        sidebar_layout.setSpacing(4)
        
        app_logo = QLabel()
        app_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Kein Rahmen/Hintergrund/Frame auf dem Label selbst - falls der
        # "komische Rand" von einer Qt-Standardumrandung kam statt vom Bild.
        app_logo.setStyleSheet("background: transparent; border: none; padding: 0px; margin: 0px;")
        app_logo.setFrameShape(QFrame.Shape.NoFrame)
        app_logo.setContentsMargins(0, 0, 0, 0)
        logo_pix = QPixmap(asset_path("GoPro Logo.png"))
        if not logo_pix.isNull():
            # Transparente Ränder rund um das eigentliche Logo abschneiden,
            # damit kein unnötiger Leerraum/Rand mehr sichtbar ist.
            logo_pix = trim_transparent_margins(logo_pix)
            app_logo.setPixmap(hidpi_scaled_to_width(logo_pix, 140))
        sidebar_layout.addWidget(app_logo)

        app_subtitle = QLabel("SYNC PRO")
        app_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_subtitle.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 11px; font-weight: 700; "
            "letter-spacing: 4px; background: transparent; border: none; margin-top: 2px;"
        )
        sidebar_layout.addWidget(app_subtitle)
        sidebar_layout.addSpacing(20)

        # Profil-Zeile (Avatar + Name) - nur sichtbar, wenn beim Onboarding
        # (oder spaeter) ein Profilname/-bild gesetzt wurde.
        self.profile_row = QWidget()
        profile_row_layout = QHBoxLayout(self.profile_row)
        profile_row_layout.setContentsMargins(4, 6, 4, 6)
        profile_row_layout.setSpacing(10)
        self.profile_avatar = AvatarPreview(size=34, accent=self.config.get("accent_color", "#00f2fe"))
        self.profile_avatar.setCursor(Qt.CursorShape.ArrowCursor)
        self.profile_avatar.mousePressEvent = lambda event: None  # in der Sidebar nur Anzeige, nicht anklickbar
        self.profile_name_lbl = QLabel("")
        self.profile_name_lbl.setStyleSheet("color: rgba(255,255,255,0.8); font-size: 13px; font-weight: 700; background: transparent;")
        profile_row_layout.addWidget(self.profile_avatar)
        profile_row_layout.addWidget(self.profile_name_lbl, 1)
        sidebar_layout.addWidget(self.profile_row)
        self._refresh_profile_row()
        sidebar_layout.addSpacing(14)

        self.btn_style = """
            QPushButton { background-color: transparent; font-size: 14px; font-weight: 600; text-align: left;
                          padding: 12px 14px; border: none; border-radius: 10px; color: rgba(255,255,255,0.75); }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.08); color: #ffffff; }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:1 rgba(255,255,255,0.05));
                color: #0d0d12; font-weight: 800;
            }
        """

        self.btn_nav_sync = QPushButton("")
        set_nav_text(self.btn_nav_sync, self.lang, "nav_sync")
        self.btn_nav_sync.setCheckable(True)
        self.btn_nav_sync.setChecked(True)
        
        self.btn_nav_videos = QPushButton("")
        set_nav_text(self.btn_nav_videos, self.lang, "nav_videos")
        self.btn_nav_videos.setCheckable(True)
        
        self.btn_nav_pictures = QPushButton("")
        set_nav_text(self.btn_nav_pictures, self.lang, "nav_pictures")
        self.btn_nav_pictures.setCheckable(True)

        sidebar_layout.addWidget(self.btn_nav_sync)
        sidebar_layout.addWidget(self.btn_nav_videos)
        sidebar_layout.addWidget(self.btn_nav_pictures)
        self.btn_nav_activity = ActivityNavButton("")
        set_nav_text(self.btn_nav_activity, self.lang, "nav_activity")
        self.btn_nav_activity.setCheckable(True)
        sidebar_layout.addWidget(self.btn_nav_activity)
        sidebar_layout.addStretch()

        # Acciones siempre disponibles (con o sin camara conectada)
        action_style = (
            "QPushButton { background-color: transparent; font-size: 13px; font-weight: 600; "
            "text-align: left; padding: 9px 14px; color: rgba(255,255,255,0.75); border-radius: 10px; border: none; } "
            "QPushButton:hover { color: #fff; background-color: rgba(255,255,255,0.08); }"
        )
        self.btn_import = QPushButton("")
        set_nav_text(self.btn_import, self.lang, "nav_import")
        self.btn_import.setStyleSheet(action_style)
        self.btn_import.clicked.connect(self.import_from_folder)
        self.btn_backup = QPushButton("")
        set_nav_text(self.btn_backup, self.lang, "nav_backup")
        self.btn_backup.setStyleSheet(action_style)
        self.btn_backup.clicked.connect(self.backup_from_menu)
        sidebar_layout.addWidget(self.btn_import)
        sidebar_layout.addWidget(self.btn_backup)
        # Aparece solo si hay una version nueva en GitHub
        self.btn_update = QPushButton("")
        accent_u = self.config.get("accent_color", "#00f2fe")
        self.btn_update.setStyleSheet(
            f"QPushButton {{ background: {accent_u}; color: #0d0d12; font-size: 13px; font-weight: 800; text-align: left;"
            f" padding: 9px 14px; border-radius: 10px; border: none; margin-top: 4px; }}"
            f" QPushButton:hover {{ background: #ffffff; }}")
        self.btn_update.hide()
        self.btn_update.clicked.connect(self.open_update)
        self._update_info = None
        sidebar_layout.addWidget(self.btn_update)
        sidebar_layout.addSpacing(6)

        self.btn_settings = QPushButton("")
        set_nav_text(self.btn_settings, self.lang, "nav_settings")
        self.btn_settings.setStyleSheet(
            "QPushButton { background-color: rgba(255,255,255,0.05); font-size: 13px; font-weight: 600; "
            "text-align: left; padding: 10px 14px; color: rgba(255,255,255,0.6); border-radius: 10px; "
            "border: 1px solid rgba(255,255,255,0.08); } "
            "QPushButton:hover { color: #fff; background-color: rgba(255,255,255,0.12); }"
        )
        self.btn_settings.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(self.btn_settings)

        self.pages = QStackedWidget()
        boot_step(30, "boot_camera")
        self.sync_page = SyncTabWidget(self.get_current_theme_color, self.config)
        boot_step(75, "boot_galleries")
        self.videos_page = VideoGallery(self.sync_page.sync_target_dir, self.lang)
        self.pictures_page = PictureGallery(self.sync_page.sync_target_dir, self.lang)

        self.pages.addWidget(self.sync_page)
        self.pages.addWidget(self.videos_page)
        self.pages.addWidget(self.pictures_page)
        self.ACTIVITY_INDEX = self.pages.addWidget(self.sync_page.activity_page)
        self.camera_page = CameraGalleryPage(self.lang, self.config.get("accent_color", "#00f2fe"),
                                             lambda: self.sync_page.sync_target_dir)
        self.CAMERA_INDEX = self.pages.addWidget(self.camera_page)
        self.sync_page.camera_files_requested.connect(self.show_camera_files)
        self.camera_page.back_requested.connect(lambda: self.switch_page(0, self.btn_nav_sync))
        self.camera_page.sync_requested.connect(self._sync_camera_selection)
        self.sync_page.monitor_thread.connection_changed.connect(self._camera_connection_changed)
        self.sync_page.activity_requested.connect(self.show_activity)
        self.sync_page.monitor_thread.card_detected.connect(
            lambda root: show_toast(self, tr(self.lang, "card_detected", root=root)))
        self.sync_page.activity_progress.connect(
            lambda pct: self.btn_nav_activity.set_progress(pct, self.get_current_theme_color()))
        JOINED_PARTS.update(self.config.get("joined_parts") or [])
        self.taskbar = TaskbarProgress(self)
        self.sync_page.activity_progress.connect(self.taskbar.set)
        QTimer.singleShot(8000, self.check_updates)

        # Assistent nur bauen, wenn er tatsaechlich noch gebraucht wird -
        # spart bei jedem normalen Start (nach der ersten Einrichtung) das
        # unnoetige Aufbauen dieser zusaetzlichen Seite mit auf.
        self.onboarding_wizard = None
        if not self.config.get("onboarding_done", False):
            self.onboarding_wizard = OnboardingWizard(
                self.lang, self.config.get("accent_color", "#00f2fe"), self.sync_page.sync_target_dir,
                on_language_selected=self.choose_language
            )
            self.onboarding_wizard.finished_setup.connect(self.on_onboarding_finished)
            self.ONBOARDING_INDEX = self.pages.addWidget(self.onboarding_wizard)

        self.btn_nav_sync.clicked.connect(lambda: self.switch_page(0, self.btn_nav_sync))
        self.btn_nav_videos.clicked.connect(lambda: self.switch_page(1, self.btn_nav_videos))
        self.btn_nav_pictures.clicked.connect(lambda: self.switch_page(2, self.btn_nav_pictures))
        self.btn_nav_activity.clicked.connect(self.show_activity)

        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.pages)

        self.sync_page.folder_changed.connect(self.on_sync_folder_changed)
        self.sync_page.sync_finished.connect(self.on_sync_finished)

        # Erststart: Einrichtungsassistent zeigen, Sidebar/Navigation
        # solange ausblenden - "onboarding_done" wird erst beim Abschluss
        # (oder "Später einrichten") des Assistenten in der Config gesetzt.
        if not self.config.get("onboarding_done", False):
            self.sidebar.hide()
            self.pages.setCurrentIndex(self.ONBOARDING_INDEX)
        else:
            self.pages.setCurrentIndex(0)

        boot_step(95, "boot_done")
        self.update_sidebar_theme()
        # Ensure background is applied after layout is set up; schedule to run on next event loop iteration
        QTimer.singleShot(0, self.apply_background)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_bg_pixmap_geometry()

    def apply_background(self):
        """Liest background_image/background_blur aus der Config und zeigt
        entweder das eigene Hintergrundbild (weichgezeichnet) oder - falls
        keins gesetzt ist - lässt einfach den normalen Verlauf durchscheinen."""
        path = self.config.get("background_image", "")
        blur_val = int(self.config.get("background_blur", 0) or 0)

        if path and os.path.exists(path):
            # Nur neu laden/weichzeichnen, wenn sich Bild oder Blur-Wert
            # tatsaechlich geaendert haben - apply_background() kann auch
            # aus anderen Gruenden erneut aufgerufen werden (z.B. Theme-
            # Wechsel), ohne dass sich am Hintergrund selbst etwas aendert.
            cache_key = (path, blur_val)
            if cache_key != self._bg_cache_key:
                pix = QPixmap(path)
                if not pix.isNull():
                    # Der Weichzeichner wird hier EINMALIG (nicht bei jedem
                    # Repaint) auf einer verkleinerten Kopie berechnet - siehe
                    # make_blurred_pixmap() fuer den Hintergrund/Grund dieses
                    # Fixes (das war die Ursache fuer das Einfrieren).
                    self._bg_source_pixmap = make_blurred_pixmap(pix, blur_val)
                    self._bg_cache_key = cache_key
                else:
                    self._bg_source_pixmap = None
                    self._bg_cache_key = None

            if self._bg_source_pixmap is not None and not self._bg_source_pixmap.isNull():
                self.bg_label.show()
                # Ensure the label is behind other widgets and doesn't block input
                self.bg_label.lower()
                # Defer geometry update to ensure widget has correct size
                QTimer.singleShot(0, self._update_bg_pixmap_geometry)
                self.apply_text_shadows()
                return

        self._bg_source_pixmap = None
        self._bg_cache_key = None
        self.bg_label.hide()
        self.apply_text_shadows()

    def apply_text_shadows(self):
        """Schaltet Schlagschatten auf allen frei ueber dem Hintergrund
        "schwebenden" Text-/UI-Elementen ein bzw. aus - je nachdem, ob
        gerade ein eigenes Hintergrundbild aktiv ist. Ohne eigenes Bild
        (nur der normale Verlauf) braucht es keine Schatten, dort ist der
        Kontrast sowieso schon hoch genug.

        HINWEIS: Die Sidebar-Buttons (Sync/Videos/Bilder/Einstellungen)
        bekommen hier bewusst KEINEN Schatten - die haben schon eine
        eigene Hover-Glow-Animation (siehe GlowHoverAnimator), die
        denselben Effekt-Slot des Widgets braucht. Ein zusaetzlicher
        Schatten wuerde beim ersten Hover von der Glow-Animation
        ueberschrieben und danach wieder verschwinden. Sie haben aber
        ohnehin schon eine eigene Hintergrund-Pille, die genug Kontrast
        bietet.
        """
        has_bg = bool(self.config.get("background_image")) and os.path.exists(self.config.get("background_image", "") or "")

        sp = self.sync_page
        text_widgets = [
            sp.title_label, sp.placeholder, sp.stats_title,
            sp.lbl_battery_caption, sp.lbl_storage_caption,
            sp.stat_status, sp.lbl_store_detail, sp.chk_delete_after,
            sp.sync_popup,
            self.videos_page.title_lbl, self.videos_page.empty_lbl,
            self.pictures_page.title_lbl, self.pictures_page.empty_lbl,
        ]
        # Icons (WLAN/Bluetooth/GPS-Badges, SD-Karte) sind klein und
        # zeichnen nur duenne Formen - damit der Schatten dort ueberhaupt
        # sichtbar/kraeftig genug wirkt, brauchen sie einen noch staerkeren,
        # dichteren Schatten als normaler Fliesstext.
        icon_widgets = [sp.badge_wifi, sp.badge_bluetooth, sp.badge_gps, sp.sd_card_icon]

        for w in text_widgets:
            if has_bg:
                add_text_shadow(w, blur=18, dy=1, alpha=255)
            else:
                w.setGraphicsEffect(None)

        for w in icon_widgets:
            if has_bg:
                add_text_shadow(w, blur=22, dy=2, alpha=255)
            else:
                w.setGraphicsEffect(None)

    def _update_bg_pixmap_geometry(self):
        if self._bg_source_pixmap is None:
            return
        # If the window hasn't been laid out yet, try again shortly
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            QTimer.singleShot(50, self._update_bg_pixmap_geometry)
            return
        # Ensure the bg_label covers the full window
        self.bg_label.setGeometry(0, 0, w, h)
        # Use cover_scaled_pixmap to crop/scale the source pixmap to the label size
        cover = cover_scaled_pixmap(self._bg_source_pixmap, QSize(w, h), screen_dpr(self))
        if not cover.isNull():
            self.bg_label.setPixmap(cover)
            self.bg_label.lower()

    def on_sync_folder_changed(self, new_dir):
        self.videos_page.folder = new_dir
        self.pictures_page.folder = new_dir
        self.videos_page.refresh()
        self.pictures_page.refresh()

    def on_sync_finished(self):
        self.videos_page.refresh()
        self.pictures_page.refresh()

    def get_current_theme_color(self):
        return self.config.get("accent_color", "#00f2fe")

    def update_sidebar_theme(self):
        c = self.get_current_theme_color()
        style = self.btn_style % c
        self.btn_nav_sync.setStyleSheet(style)
        self.btn_nav_videos.setStyleSheet(style)
        self.btn_nav_pictures.setStyleSheet(style)
        self.btn_nav_activity.setStyleSheet(style)
        self.sync_page.apply_theme()
        if self.sync_page.gopro_connected:
            self.sync_page.stat_status.setStyleSheet(f"color: {c}; font-size: 15px; font-weight: 800; letter-spacing: 0.6px;")
            self.sync_page.bar_storage.set_value_and_color(self.sync_page.bar_storage._value, c)

    def open_settings(self):
        def _apply_changes():
            save_config(self.config)
            self.update_sidebar_theme()
            self.apply_background()

        dlg = SettingsDialog(self, self.config, _apply_changes, self.on_folder_change, self.on_language_change)
        dlg.exec()

    def on_folder_change(self, new_dir):
        self.config["target_dir"] = new_dir
        save_config(self.config)
        self.sync_page.sync_target_dir = new_dir
        self.sync_page.folder_changed.emit(new_dir)

    def on_language_change(self, new_lang):
        self.lang = new_lang
        self.sync_page.lang = new_lang
        self.retranslate_ui()

    def _go_to_sync_page(self):
        self.switch_page(0, self.btn_nav_sync)

    def import_from_folder(self):
        # 1) seleccion del origen
        folder = QFileDialog.getExistingDirectory(self, tr(self.lang, "import_title"), _safe_local_dir())
        if not folder:
            return
        # 2) confirmacion: resumen, y origen/destino cambiables
        dlg = ImportConfirmDialog(self, self.lang, os.path.normpath(folder), self.sync_page.sync_target_dir,
                                  self.config.get("accent_color", "#00f2fe"))
        if not dlg.exec():
            return
        # 3) importar
        self._go_to_sync_page()
        self.sync_page.start_import(dlg.source, dlg.target, only_paths=dlg.only_paths)

    def make_light_version(self, path):
        def done(ok, stats):
            outs = stats.get("outputs") or []
            if ok and outs:
                show_in_explorer(outs[0])
        self.show_activity()
        self.sync_page.start_tool(tr(self.lang, "light_title", name=os.path.basename(path)),
                                  [light_version_job(path, self.lang)], done)

    def repair_video(self, path):
        def done(ok, stats):
            outs = stats.get("outputs") or []
            if ok and outs:
                self.videos_page.refresh()
                show_in_explorer(outs[0])
        self.show_activity()
        self.sync_page.start_tool(tr(self.lang, "repair_title", name=os.path.basename(path)),
                                  [repair_job(path, self.lang)], done)

    def open_join_dialog(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            groups = find_chapter_groups(self.sync_page.sync_target_dir)
        finally:
            QApplication.restoreOverrideCursor()
        dlg = JoinDialog(self, self.lang, groups, self.config.get("accent_color", "#00f2fe"))
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen():
            self.join_groups(dlg.chosen(), dlg.chk_recycle.isChecked())

    def _remember_joined(self, group):
        for x in group["parts"]:
            try:
                JOINED_PARTS.add(f"{os.path.basename(x).lower()}|{os.path.getsize(x)}")
            except OSError:
                pass
        self.config["joined_parts"] = sorted(JOINED_PARTS)
        save_config(self.config)

    def join_groups(self, groups, recycle=False):
        jobs = [join_job(g, self.lang, recycle, on_joined=self._remember_joined) for g in groups]

        def done(ok, stats):
            if stats.get("copied"):
                self.videos_page.refresh()
        self.show_activity()
        self.sync_page.start_tool(tr(self.lang, "join_task_title", n=len(groups)), jobs, done)

    def check_updates(self):
        """Busca una version nueva en GitHub (solo en la app instalada)."""
        if not self.config.get("check_updates", True):
            return
        if not getattr(sys, "frozen", False) and not os.environ.get("GOPROSYNC_TEST_UPDATE"):
            return
        self._update_thread = UpdateCheckThread()
        self._update_thread.found.connect(self._on_update_found)
        self._update_thread.start()

    def _on_update_found(self, info):
        self._update_info = info
        self.btn_update.setText(tr(self.lang, "update_btn", v=info["version"]))
        self.btn_update.show()
        show_toast(self, tr(self.lang, "update_available", v=info["version"]))

    def open_update(self):
        if self._update_info:
            UpdateDialog(self, self.lang, self._update_info, self.config.get("accent_color", "#00f2fe")).exec()

    def backup_from_menu(self):
        """Respaldar a disco: dialogo con carpeta a respaldar + destino
        (cambiables) y confirmacion; la copia sigue en segundo plano."""
        dirs = list(self.config.get("backup_dirs") or [])
        last_src = self.config.get("backup_last_source") or ""
        source = last_src if last_src and os.path.isdir(last_src) else self.sync_page.sync_target_dir
        dest = self.config.get("backup_last_dest") or (dirs[0] if dirs else "")
        dlg = BackupDialog(self, self.lang, source, dest, self.config.get("accent_color", "#00f2fe"),
                           remember_default=self.config.get("backup_last_remember", True),
                           verify_default=self.config.get("backup_last_verify", False))
        if not dlg.exec() or not dlg.dest:
            return
        # La proxima vez el dialogo arranca con lo mismo que se eligio ahora.
        self.config["backup_last_source"] = dlg.source
        self.config["backup_last_dest"] = dlg.dest
        self.config["backup_last_remember"] = dlg.remember()
        self.config["backup_last_verify"] = dlg.chk_verify.isChecked()
        if dlg.remember() and not any(os.path.normcase(d) == os.path.normcase(dlg.dest) for d in dirs):
            dirs.append(dlg.dest)
            self.config["backup_dirs"] = dirs
        save_config(self.config)
        self.show_activity()
        self.sync_page.start_backup(dlg.source, [dlg.dest], verify=dlg.chk_verify.isChecked())

    def choose_language(self, new_lang):
        """Guarda el idioma elegido (asistente/dialogo) y traduce la app al momento."""
        if new_lang not in SUPPORTED_LANGUAGES:
            return
        self.config["language"] = new_lang
        self.config["language_chosen"] = True
        save_config(self.config)
        self.on_language_change(new_lang)

    def ask_language_if_needed(self):
        """Instalaciones anteriores (asistente ya hecho, sin idioma elegido):
        preguntar una sola vez."""
        if self.config.get("language_chosen") or not self.config.get("onboarding_done", False):
            return
        dlg = LanguageDialog(self, self.lang, self.config.get("accent_color", "#00f2fe"))
        dlg.exec()
        self.choose_language(dlg.chosen)

    def retranslate_ui(self):
        """Aktualisiert alle sichtbaren Texte in-place auf die aktuell
        gewaehlte Sprache - kein Neustart der App noetig."""
        set_nav_text(self.btn_nav_sync, self.lang, "nav_sync")
        set_nav_text(self.btn_nav_videos, self.lang, "nav_videos")
        set_nav_text(self.btn_nav_pictures, self.lang, "nav_pictures")
        set_nav_text(self.btn_import, self.lang, "nav_import")
        set_nav_text(self.btn_nav_activity, self.lang, "nav_activity")
        set_nav_text(self.btn_backup, self.lang, "nav_backup")
        set_nav_text(self.btn_settings, self.lang, "nav_settings")

        sp = self.sync_page
        sp.placeholder.setText(tr(self.lang, "waiting_connection"))
        sp.stats_title.setText(tr(self.lang, "device_info"))
        sp.lbl_battery_caption.setText(tr(self.lang, "battery_caption"))
        sp.lbl_storage_caption.setText(tr(self.lang, "storage_caption"))
        sp.badge_wifi.label_text = tr(self.lang, "badge_wifi")
        sp.badge_wifi.setToolTip(tr(self.lang, "tooltip_wifi"))
        sp.badge_bluetooth.label_text = tr(self.lang, "badge_bluetooth")
        sp.badge_bluetooth.setToolTip(tr(self.lang, "tooltip_bluetooth"))
        sp.badge_gps.label_text = tr(self.lang, "badge_gps")
        sp.badge_gps.setToolTip(tr(self.lang, "tooltip_gps"))
        for badge in (sp.badge_wifi, sp.badge_bluetooth, sp.badge_gps):
            badge.update()
        sp.chk_delete_after.setText(tr(self.lang, "delete_after_checkbox"))
        sp.btn_sync.set_labels(
            tr(self.lang, "sync_idle"), tr(self.lang, "menu_all"),
            tr(self.lang, "menu_pictures"), tr(self.lang, "menu_videos")
        )
        if sp.gopro_connected:
            sp.stat_status.setText(tr(self.lang, "connected"))
        else:
            sp.title_label.setText(tr(self.lang, "connect_gopro"))
            sp.stat_status.setText(tr(self.lang, "not_connected"))

        self.videos_page.lang = self.lang
        self.pictures_page.lang = self.lang
        self.videos_page.retranslate(tr(self.lang, "video_gallery_title"), tr(self.lang, "no_videos_yet"))
        self.pictures_page.retranslate(tr(self.lang, "picture_gallery_title"), tr(self.lang, "no_pictures_yet"))
        self.videos_page.refresh()
        self.pictures_page.refresh()

    def on_onboarding_finished(self, result):
        """Wird aufgerufen, wenn der Einrichtungsassistent abgeschlossen
        (oder uebersprungen) wurde - uebernimmt alle Antworten in die
        Config, richtet ggf. den Windows-Autostart ein und wechselt zur
        normalen Sync-Ansicht."""
        self.config["profile_name"] = result.get("profile_name", "")
        self.config["profile_picture"] = result.get("profile_picture", "")
        self.config["autostart_enabled"] = result.get("autostart_enabled", False)
        self.config["open_on_gopro_connect"] = result.get("open_on_gopro_connect", False)
        self.config["onboarding_done"] = True
        if result.get("language") in SUPPORTED_LANGUAGES:
            self.config["language"] = result["language"]
            self.config["language_chosen"] = True

        new_dir = result.get("target_dir")
        if new_dir:
            self.config["target_dir"] = new_dir
            self.sync_page.sync_target_dir = new_dir
            self.sync_page.config["target_dir"] = new_dir

        save_config(self.config)
        set_autostart(self.config["autostart_enabled"])

        self._refresh_profile_row()
        self.on_sync_folder_changed(self.sync_page.sync_target_dir)

        self.sidebar.show()
        self.pages.setCurrentIndex(0)
        self.btn_nav_sync.setChecked(True)

        # Jetzt (und nicht frueher) mit dem Ueberwachen der GoPro-Verbindung
        # anfangen - siehe Kommentar in main()/_reveal_main_window().
        if not self.sync_page.monitor_thread.isRunning():
            self.sync_page.start_monitoring()

    def _refresh_profile_row(self):
        name = self.config.get("profile_name", "").strip()
        pic = self.config.get("profile_picture", "")
        if not name and not pic:
            self.profile_row.hide()
            return
        self.profile_row.show()
        self.profile_avatar.set_accent(self.config.get("accent_color", "#00f2fe"))
        self.profile_avatar.set_initial(name)
        self.profile_avatar.set_image_path(pic)
        self.profile_name_lbl.setText(name)

    def show_from_background_on_connect(self, connected, model):
        """Nur relevant, wenn die App per Autostart im Hintergrund lief
        (siehe main(): open_on_connect) - zeigt das Fenster erst, sobald
        tatsaechlich eine GoPro erkannt wird."""
        if not connected:
            return
        try:
            self.sync_page.monitor_thread.connection_changed.disconnect(self.show_from_background_on_connect)
        except Exception:
            pass
        bring_to_front(self)

    def switch_page(self, idx, btn):
        self.pages.setCurrentIndex(idx)
        self.btn_nav_sync.setChecked(False)
        self.btn_nav_videos.setChecked(False)
        self.btn_nav_pictures.setChecked(False)
        self.btn_nav_activity.setChecked(False)
        btn.setChecked(True)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            page = self.pages.currentWidget() if hasattr(self, "pages") else None
            if hasattr(page, "soft_refresh"):
                QTimer.singleShot(200, page.soft_refresh)

    def show_camera_files(self):
        self.pages.setCurrentIndex(self.CAMERA_INDEX)
        self.camera_page.open_page(LAST_DEVICE_NAME.get("name") or "")

    def _sync_camera_selection(self, keys):
        self.switch_page(0, self.btn_nav_sync)
        self.sync_page.start_sync("all", only_keys=keys)

    def _camera_connection_changed(self, connected, model):
        if not connected and self.pages.currentIndex() == self.CAMERA_INDEX:
            self.camera_page.close_page()

    def show_activity(self):
        self.switch_page(self.ACTIVITY_INDEX, self.btn_nav_activity)

    def showEvent(self, event):
        if IS_WINDOWS:
            apply_dark_titlebar(int(self.winId()))
        super().showEvent(event)
        tb = getattr(self, "taskbar", None)
        if tb is not None and tb.last is not None:
            QTimer.singleShot(600, tb.apply)

    def closeEvent(self, event):
        # Con inicio automatico (icono en la bandeja), cerrar la ventana la
        # oculta y la app sigue vigilando la camara junto al reloj.
        tray = getattr(self, "tray", None)
        if tray is not None and not getattr(self, "_really_quit", False) and self.config.get("autostart_enabled", False):
            event.ignore()
            self.hide()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                tray.showMessage("GoPro Sync", tr(self.lang, "tray_hidden_msg"),
                                 QSystemTrayIcon.MessageIcon.Information, 5000)
            return
        if not getattr(self, "_quit_confirmed", False) and not self._stop_tasks_for_quit():
            event.ignore()
            self._really_quit = False
            return
        self.camera_page.close_workers()
        monitor = getattr(self.sync_page, "monitor_thread", None)
        if monitor is not None:
            monitor.stop()
            monitor.wait(3000)
        super().closeEvent(event)

    def _stop_tasks_for_quit(self):
        """Si hay una copia en curso, pregunta y la cancela prolija (cortar el
        proceso a mitad de una lectura deja a la camara trabada). Devuelve
        False si el usuario prefiere seguir copiando."""
        sp = self.sync_page
        if not sp._busy():
            return True
        if self.isHidden():
            self.showNormal()
            bring_to_front(self)
        if ConfirmQuitDialog(self, self.lang).exec() != QDialog.DialogCode.Accepted:
            return False
        sp._import_queue.clear()
        for attr in ("sync_worker", "backup_worker", "tool_worker"):
            w = getattr(sp, attr, None)
            if w is not None and w.isRunning():
                w.cancel()
        for attr in ("sync_worker", "backup_worker", "tool_worker"):
            w = getattr(sp, attr, None)
            if w is not None and w.isRunning():
                w.wait(15000)
        self._quit_confirmed = True
        return True

    def on_session_end(self, *_):
        """Windows se apaga o cierra sesion: cancelar las copias prolijo, sin
        preguntar (Windows da unos pocos segundos)."""
        sp = self.sync_page
        sp._import_queue.clear()
        for attr in ("sync_worker", "backup_worker", "tool_worker"):
            w = getattr(sp, attr, None)
            if w is not None and w.isRunning():
                w.cancel()
        for attr in ("sync_worker", "backup_worker", "tool_worker"):
            w = getattr(sp, attr, None)
            if w is not None and w.isRunning():
                w.wait(4000)
        self.camera_page.close_workers()
        self._quit_confirmed = True
        self._really_quit = True

    def quit_app(self):
        """Salir de verdad (menu de la bandeja)."""
        if not self._stop_tasks_for_quit():
            return
        self._really_quit = True
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.hide()
        self.close()
        QApplication.instance().quit()


# --- [ ANIMATED SPLASH SCREEN ] ---

class AnimatedSplash(QWidget):
    def __init__(self, logo_path):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(560, 360)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.logo_pix = QPixmap(logo_path)
        self._scaled_logo = None   # se genera una vez, a la escala de la pantalla
        self.progress = 0.0
        self.status = ""
        self.angle = 0
        self.timer = QTimer(self)   # (sin spinner: el timer ya no se usa para animar)

    def set_status(self, pct, text):
        self.progress = max(self.progress, float(pct))
        self.status = text
        self.update()

    def _update_spin(self):
        self.angle = (self.angle + 6) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 15, 15)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor("#1a1b26"))
        grad.setColorAt(1, QColor("#2d3340"))
        painter.fillPath(path, grad)
        
        if not self.logo_pix.isNull():
            dpr = screen_dpr(self)
            if self._scaled_logo is None or self._scaled_logo.devicePixelRatio() != dpr:
                self._scaled_logo = hidpi_scaled_to_width(self.logo_pix, 260, dpr)
            size = self._scaled_logo.deviceIndependentSize()
            x = int((self.width() - size.width()) // 2)
            y = int((self.height() - size.height()) // 2 - 20)
            painter.drawPixmap(x, y, self._scaled_logo)
            
        # Barra de progreso del arranque + texto de la etapa
        if self.progress > 0:
            bw, bh = 260, 4
            bx, by = (self.width() - bw) / 2, self.height() - 108
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 30))
            painter.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)
            painter.setBrush(QColor(0, 242, 254))
            painter.drawRoundedRect(QRectF(bx, by, bw * min(100.0, self.progress) / 100.0, bh), 2, 2)
        if self.status:
            painter.setPen(QColor(255, 255, 255, 150))
            f = painter.font()
            f.setPixelSize(12)
            painter.setFont(f)
            painter.drawText(QRectF(0, self.height() - 96, self.width(), 20), Qt.AlignmentFlag.AlignCenter, self.status)


def setup_tray(app, window, lang):
    """Icono junto al reloj: clic = abrir; menu: Abrir / Salir. Muestra el
    progreso de la copia en curso al pasar el mouse."""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(load_app_icon(), app)
    tray.setToolTip("GoPro Sync")
    menu = QMenu()
    menu.addAction(tr(lang, "tray_open"), lambda: bring_to_front(window))
    menu.addSeparator()
    menu.addAction(tr(lang, "tray_quit"), window.quit_app)
    tray.setContextMenu(menu)
    tray._menu = menu   # mantener referencia

    def activated(reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            bring_to_front(window)
    tray.activated.connect(activated)

    def progress(pct):
        if pct is None:
            tray.setToolTip("GoPro Sync")
        else:
            cur = window.sync_page._current_task or window.sync_page._backup_task
            kind = cur[0] if cur else ""
            task = tr(lang, "busy_syncing" if kind == "camera" else ("busy_backup" if kind == "backup" else "busy_importing"))
            tray.setToolTip(tr(lang, "tray_tooltip_busy", task=task, pct=int(pct)))
    window.sync_page.activity_progress.connect(progress)
    tray.show()
    window.tray = tray
    app.setQuitOnLastWindowClosed(False)   # cerrar la ventana no cierra la app si hay bandeja
    return tray
