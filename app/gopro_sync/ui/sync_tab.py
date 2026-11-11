"""Pantalla Camara: estado, sincronizacion y carriles de tareas."""
import os
import re
import shutil
import traceback
from PySide6.QtCore import QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from ..env import (
    CONFIG_DIR,
    GOPRO_MODELS,
    LAST_DEVICE_NAME,
    _safe_local_dir,
    _valid_start_dir,
    asset_path,
    load_config,
    save_config,
)
from ..i18n import short_path, tr
from ..sound import SoundManager
from ..fileops import _is_same_or_inside
from ..camera import DeviceMonitorThread
from ..workers import BackupWorkerThread, SyncWorkerThread
from ..videotools import ToolJobThread, ffmpeg_exe
from ..ui.graphics import (
    add_drop_shadow,
    boot_step,
    find_logo_path,
    hidpi_scaled_to_width,
    screen_dpr,
    trim_transparent_margins,
)
from ..ui.widgets import (
    CameraImageView,
    ConnectivityBadge,
    RoundedVideoContainer,
    SDCardIcon,
    SyncProgressButton,
    ToggleSwitch,
    VideoFrameView,
    WaveProgressBar,
    show_toast,
)
from ..ui.activity import ActivityPage, SyncProgressPanel

# --- [ HAUPT-SYNC-TAB ] ---

class SyncTabWidget(QWidget):
    activity_requested = Signal()
    camera_files_requested = Signal()
    activity_progress = Signal(object)   # % total de la copia en curso, o None si no hay
    folder_changed = Signal(str)
    sync_finished = Signal()

    def __init__(self, get_theme_color_cb, config=None):
        super().__init__()
        self.get_theme_color = get_theme_color_cb
        self.gopro_connected = False
        self._current_model = None
        # WICHTIG: Nutzt (falls uebergeben) DIESELBE Config-Instanz wie das
        # MainWindow, statt sie separat neu von der Platte zu laden. Vorher
        # existierten zwei unabhaengige Config-Objekte (eins im MainWindow,
        # eins hier), die nach dem Start auseinanderlaufen konnten - z.B.
        # aenderte der Einstellungen-Dialog nur die Kopie des MainWindow,
        # wodurch ein gerade geaenderter Zielordner (z.B. von E: auf C:)
        # hier immer noch auf den alten Wert zeigte, bis die App neu
        # gestartet wurde. Durch das gemeinsame Objekt ist jede Aenderung
        # sofort auf beiden Seiten sichtbar.
        self.config = config if config is not None else load_config()
        self.lang = self.config.get("language", "de")
        self.sync_target_dir = os.path.normpath(self.config.get("target_dir", os.path.expanduser("~/Videos/GoProSync")))
        self.init_ui()
        self.update_ui_state(False)

        self.monitor_thread = DeviceMonitorThread(self)
        self.monitor_thread.connection_changed.connect(self.update_ui_state)
        self.monitor_thread.status_ready.connect(self.apply_status)
        # HINWEIS: Thread wird hier NICHT mehr gestartet. Wird von main() aufgerufen,
        # wenn das Fenster sichtbar ist.

    def start_monitoring(self):
        """Wird aufgerufen, sobald die UI vollständig geladen und sichtbar ist."""
        self.monitor_thread.start()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(25)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(24)

        # Eigene Spalte fuer Kopfbereich (Titel-Text / Modell-Logo) + Video,
        # damit das Logo exakt ueber der Videobreite zentriert ist (nicht
        # ueber die volle Fensterbreite inkl. Device-Info-Panel rechts).
        media_column = QVBoxLayout()
        media_column.setSpacing(14)

        # Kopfbereich OBERHALB des Videos (bewusst KEIN Overlay mehr auf dem
        # Video selbst) - zeigt entweder den Text-Titel (z.B. "Connect
        # GoPro" bzw. den Modellnamen als Fallback) oder, sobald ein Geraet
        # mit vorhandener Logo-Datei verbunden ist, das grosse Modell-Logo.
        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        media_column.addWidget(self.title_label)

        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.hide()
        media_column.addWidget(self.logo_label)

        self.media_wrapper = QWidget()
        # FIX: Video-Container deutlich größer und quadratisch gemacht
        self.media_wrapper.setFixedSize(420, 420)
        add_drop_shadow(self.media_wrapper, 30, 10, 120) 

        media_wrapper_layout = QVBoxLayout(self.media_wrapper)
        media_wrapper_layout.setContentsMargins(0, 0, 0, 0)

        self.media_stack = QStackedWidget()
        
        self.placeholder = QLabel(tr(self.lang, "waiting_connection"))
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("""
            QLabel {
                font-size: 18px; font-weight: 800; color: #ffffff;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(20,20,26,0.55), stop:1 rgba(20,20,26,0.4));
                border: 1px dashed rgba(255,255,255,0.28);
                border-radius: 24px;
            }
        """)
        self.media_stack.addWidget(self.placeholder)

        # Use our RoundedVideoContainer which now renders via QGraphicsVideoItem
        self.rounded_video = RoundedVideoContainer(radius=24)
        self.media_stack.addWidget(self.rounded_video)

        # 360-Grad-Vorschau (fuer GoPro MAX / MAX 2) - dasselbe Widget wie im
        # Video-Player, nur mit abgerundeten Ecken passend zum Container.
        self.pan_preview = VideoFrameView(radius=24)
        self.pan_preview.set_mode("360")
        self.media_stack.addWidget(self.pan_preview)

        # 3: foto/ilustracion para modelos sin video propio (HERO5...)
        self.camera_image = CameraImageView(radius=24)
        self.media_stack.addWidget(self.camera_image)

        media_wrapper_layout.addWidget(self.media_stack)

        media_column.addWidget(self.media_wrapper)

        self.btn_camera_photo = QPushButton(tr(self.lang, "camera_photo_btn"))
        self.btn_camera_photo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_camera_photo.setStyleSheet(
            "QPushButton { background: transparent; color: rgba(255,255,255,0.55); border: none; font-size: 12px; padding: 4px; }"
            " QPushButton:hover { color: #ffffff; }")
        self.btn_camera_photo.clicked.connect(self.choose_camera_photo)
        self.btn_camera_photo.hide()
        media_column.addWidget(self.btn_camera_photo, alignment=Qt.AlignmentFlag.AlignCenter)
        content_layout.addLayout(media_column)

        boot_step(45, "boot_camera")
        self.connect_player = QMediaPlayer(self)
        # Sin salida de audio: el video de conexion se reproduce mudo y crear un
        # QAudioOutput costaba ~0,25 s al arrancar.
        # IMPORTANT: setVideoOutput to the QGraphicsVideoItem
        self.connect_player.setVideoOutput(self.rounded_video.video_item)
        # Zweiter Ausgang (QVideoSink) fuer die 360-Grad-Vorschau - wird nur
        # aktiv genutzt (per setVideoOutput), wenn ein MAX/MAX2 verbunden ist.
        self.connect_sink = QVideoSink(self)
        self.connect_sink.videoFrameChanged.connect(self._on_connect_sink_frame)

        # Freeze-Frame-Fix (flaches, nicht-360°-Video): der QGraphicsVideoItem
        # hat einen eigenen internen QVideoSink, ueber den jeder gerenderte
        # Frame laeuft. Wir haengen uns per videoSink() zusaetzlich daran, um
        # IMMER den zuletzt gezeigten Frame als QPixmap zwischenzuspeichern -
        # ganz ohne einen zweiten Videoausgang zu brauchen. Sobald wir kurz
        # vorm Ende pausieren (siehe _on_video_position_changed), legen wir
        # genau dieses letzte Bild als Screenshot ueber das Video, damit ein
        # eventuell grauer/leerer Frame des Player-Backends beim Pausieren
        # verdeckt wird.
        self._last_flat_frame = None
        try:
            self.rounded_video.video_item.videoSink().videoFrameChanged.connect(self._on_flat_video_frame)
        except Exception:
            pass
        
        clip_path = asset_path("GoPro Hero 11 .mp4")
        if os.path.exists(clip_path):
            self.connect_player.setSource(QUrl.fromLocalFile(clip_path))
            
        self.connect_player.positionChanged.connect(self._on_video_position_changed)

        stats_widget = QWidget()
        stats_widget.setObjectName("deviceInfoCard")
        stats_widget.setStyleSheet("""
            QWidget#deviceInfoCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(18,19,26,0.68), stop:1 rgba(18,19,26,0.52));
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 18px;
            }
        """)
        stats_widget.setFixedWidth(280)
        add_drop_shadow(stats_widget, 30, 8, 90)
        stats_layout = QVBoxLayout(stats_widget)
        stats_layout.setContentsMargins(22, 20, 22, 22)
        stats_layout.setSpacing(12)

        self.stats_title = stats_title = QLabel(tr(self.lang, "device_info"))
        stats_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats_title.setStyleSheet(
            "font-size: 13px; font-weight: 800; letter-spacing: 2.5px; color: rgba(255,255,255,0.6); "
            "border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 12px; background: transparent;"
        )
        stats_layout.addWidget(stats_title)
        stats_layout.addSpacing(2)

        self.stat_status = QLabel()
        self.stat_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats_layout.addWidget(self.stat_status)

        # --- Platzhalter: WLAN / Bluetooth / GPS ---
        # (Werden aktuell nicht von der Kamera ausgelesen, siehe Kommentar
        # bei der Klasse ConnectivityBadge weiter oben.)
        conn_row = QHBoxLayout()
        conn_row.setContentsMargins(0, 2, 0, 0)
        conn_row.setSpacing(2)
        conn_row.addStretch()
        self.badge_wifi = ConnectivityBadge("wifi", tr(self.lang, "badge_wifi"), tr(self.lang, "tooltip_wifi"))
        self.badge_bluetooth = ConnectivityBadge("bluetooth", tr(self.lang, "badge_bluetooth"), tr(self.lang, "tooltip_bluetooth"))
        self.badge_gps = ConnectivityBadge("gps", tr(self.lang, "badge_gps"), tr(self.lang, "tooltip_gps"))
        conn_row.addWidget(self.badge_wifi)
        conn_row.addWidget(self.badge_bluetooth)
        conn_row.addWidget(self.badge_gps)
        conn_row.addStretch()
        stats_layout.addLayout(conn_row)
        stats_layout.addSpacing(2)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: rgba(255,255,255,0.08); border: none;")
        stats_layout.addWidget(divider)
        stats_layout.addSpacing(2)

        self.lbl_battery_caption = lbl_battery_caption = QLabel(tr(self.lang, "battery_caption"))
        lbl_battery_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_battery_caption.setStyleSheet(
            "font-size: 11px; font-weight: 800; letter-spacing: 1.2px; color: rgba(255,255,255,0.5); background: transparent;"
        )
        self.bar_battery = WaveProgressBar(show_battery_nub=True)
        stats_layout.addWidget(lbl_battery_caption)
        stats_layout.addWidget(self.bar_battery)
        stats_layout.addSpacing(6)

        storage_caption_row = QHBoxLayout()
        storage_caption_row.setSpacing(6)
        storage_caption_row.addStretch()
        self.sd_card_icon = SDCardIcon(color_hex="#ffffff", alpha=140)
        storage_caption_row.addWidget(self.sd_card_icon)
        self.lbl_storage_caption = lbl_storage_caption = QLabel(tr(self.lang, "storage_caption"))
        lbl_storage_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_storage_caption.setStyleSheet(
            "font-size: 11px; font-weight: 800; letter-spacing: 1.2px; color: rgba(255,255,255,0.5); background: transparent;"
        )
        storage_caption_row.addWidget(lbl_storage_caption)
        storage_caption_row.addStretch()
        self.bar_storage = WaveProgressBar(show_battery_nub=False)
        self.lbl_store_detail = QLabel("")
        self.lbl_store_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_store_detail.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 12px; font-weight: 600; background: transparent; margin-top: 2px;")
        stats_layout.addLayout(storage_caption_row)
        stats_layout.addWidget(self.bar_storage)
        stats_layout.addWidget(self.lbl_store_detail)
        
        content_layout.addWidget(stats_widget)
        layout.addLayout(content_layout)

        self.chk_delete_after = ToggleSwitch(tr(self.lang, "delete_after_checkbox"))
        self.chk_delete_after.set_accent(self.get_theme_color())
        self.chk_delete_after.setStyleSheet("color: rgba(255,255,255,0.6);")
        self.chk_delete_after.label.setMinimumWidth(340)
        self.chk_delete_after.setChecked(self.config.get("delete_after_sync", False), animate=False)
        self.chk_delete_after.toggled.connect(self.on_delete_toggle)
        layout.addWidget(self.chk_delete_after, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_sync = SyncProgressButton()
        self.btn_sync.set_labels(
            tr(self.lang, "sync_idle"), tr(self.lang, "menu_all"),
            tr(self.lang, "menu_pictures"), tr(self.lang, "menu_videos")
        )
        add_drop_shadow(self.btn_sync, 35, 8, 110, self.get_theme_color())
        self.btn_sync.clicked.connect(self.start_sync)
        self.btn_sync.mode_chosen.connect(self.start_sync)
        self.btn_sync.progress_requested.connect(lambda: self.activity_requested.emit())
        layout.addWidget(self.btn_sync, alignment=Qt.AlignmentFlag.AlignCenter)

        # Ver los archivos de la camara (con su estado) y elegir cuales sincronizar
        self.btn_pick_files = QPushButton(tr(self.lang, "cam_view_btn"))
        self.btn_pick_files.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pick_files.setStyleSheet(
            "QPushButton { background: transparent; color: rgba(255,255,255,0.7); border: none; font-size: 13px;"
            " font-weight: 600; padding: 4px; } QPushButton:hover { color: #ffffff; }")
        self.btn_pick_files.clicked.connect(self.pick_camera_files)
        self.btn_pick_files.hide()
        layout.addWidget(self.btn_pick_files, alignment=Qt.AlignmentFlag.AlignCenter)

        # Progreso: el panel completo vive en la seccion Actividad; aqui solo
        # un enlace mientras copia / con el ultimo resultado.
        self.progress_panel = SyncProgressPanel(self.lang, self.get_theme_color())
        self.progress_panel.cancel_requested.connect(self.cancel_sync)
        boot_step(55, "boot_activity")
        self.activity_page = ActivityPage(self.lang, self.get_theme_color())
        self.activity_page.set_panel(self.progress_panel)
        # El respaldo tiene su propio carril: corre en paralelo a la sincronizacion.
        self.backup_panel = SyncProgressPanel(self.lang, self.get_theme_color())
        self.backup_panel.cancel_requested.connect(self.cancel_backup)
        self.backup_panel.hide()
        self.activity_page.set_panel(self.backup_panel)
        # Herramientas (unir partes, version liviana, reparar): otro carril mas.
        self.tools_panel = SyncProgressPanel(self.lang, self.get_theme_color())
        self.tools_panel.cancel_requested.connect(self.cancel_tools)
        self.tools_panel.hide()
        self.activity_page.set_panel(self.tools_panel)
        self.activity_page.remove_queued.connect(self.remove_queued)
        self.lnk_activity = QPushButton(tr(self.lang, "activity_view"))
        self.lnk_activity.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lnk_activity.setStyleSheet(
            "QPushButton { background: transparent; color: rgba(255,255,255,0.6); border: none; font-size: 12px; }"
            " QPushButton:hover { color: #ffffff; }")
        self.lnk_activity.clicked.connect(lambda: self.activity_requested.emit())
        self.lnk_activity.hide()
        self._camera_task_running = False
        layout.addWidget(self.lnk_activity, alignment=Qt.AlignmentFlag.AlignCenter)
        self._import_queue = []      # tareas en cola: ("import", carpeta, destino) o ("camera", modo)
        self._current_task = None    # la tarea que se esta copiando ahora
        self._current_title = ""
        self._backup_task = None     # respaldo en curso (carril aparte)
        self._backup_title = ""
        self._tool_task = None       # herramienta en curso (tercer carril)
        self._tool_title = ""
        self._lane_pct = {"copy": None, "backup": None, "tool": None}

        self.sync_popup = QLabel("")
        self.sync_popup.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sync_popup.setStyleSheet("""
            QLabel {
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 12px;
                padding: 10px 18px;
                color: rgba(255,255,255,0.85);
                font-size: 13px;
                font-weight: 500;
            }
        """)
        self.sync_popup.setFixedWidth(420)
        self.sync_popup.hide()
        layout.addWidget(self.sync_popup, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()

    def apply_theme(self):
        try:
            self._apply_theme()
        except Exception:
            print("=== FEHLER in apply_theme ===")
            traceback.print_exc()

    def _apply_theme(self):
        c = self.get_theme_color()
        self.btn_sync.set_theme_color(c)
        add_drop_shadow(self.btn_sync, 35, 8, 110, c)
        if self.gopro_connected:
            self.stat_status.setStyleSheet(f"color: {c}; font-size: 15px; font-weight: 800; letter-spacing: 0.6px;")
            self.badge_wifi.set_active(True, c)
            self.badge_bluetooth.set_active(True, c)
            self.badge_gps.set_active(True, c)
        else:
            self.stat_status.setStyleSheet("color: #ff5c6c; font-size: 15px; font-weight: 800; letter-spacing: 0.6px;")

    def _on_video_position_changed(self, pos):
        dur = self.connect_player.duration()
        if dur > 0 and pos >= dur - 50:
            # Erst den echten letzten Frame als Screenshot ueberlegen, DANN
            # erst pausieren - so ist es egal, ob das Player-Backend beim
            # Pausieren so kurz vorm Ende manchmal einen grauen/leeren Frame
            # zeigt, denn der ist dann schon verdeckt.
            if self._last_flat_frame is not None and not self._last_flat_frame.isNull():
                self.rounded_video.show_freeze_frame(self._last_flat_frame)
            self.connect_player.pause()

    def _on_flat_video_frame(self, frame):
        """Haelt laufend den zuletzt gerenderten Frame des flachen (nicht-
        360°) Verbindungs-Videos als QPixmap fest - siehe Kommentar bei
        self._last_flat_frame weiter oben."""
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        self._last_flat_frame = QPixmap.fromImage(img)

    def _on_connect_sink_frame(self, frame):
        if not frame.isValid():
            return
        img = frame.toImage()
        if not img.isNull():
            self.pan_preview.set_frame(img)

    def update_ui_state(self, connected, model=""):
        try:
            self._update_ui_state(connected, model)
        except Exception:
            print("=== FEHLER in update_ui_state (Verbindungsstatus) ===")
            traceback.print_exc()

    def _update_ui_state(self, connected, model=""):
        self.gopro_connected = connected
        c = self.get_theme_color()
        if connected:
            profile = GOPRO_MODELS.get(model, GOPRO_MODELS["HERO11"])

            # Video nur wechseln, wenn sich das Modell tatsaechlich geaendert
            # hat (spart unnoetiges Neuladen bei jedem Status-Update).
            if model != self._current_model:
                self._current_model = model
                clip_path = asset_path(profile["video"])
                self.connect_player.stop()
                self.rounded_video.hide_freeze_frame()
                self._last_flat_frame = None
                # 360-Grad-Kameras (MAX / MAX 2): Ausgang auf die Pan-Vorschau
                # umleiten statt auf das normale (flache) Video-Element.
                if profile.get("is_360"):
                    self.connect_player.setVideoOutput(self.connect_sink)
                    self.pan_preview.clear_frame()
                else:
                    self.connect_player.setVideoOutput(self.rounded_video.video_item)
                self._has_model_video = os.path.exists(clip_path)
                if self._has_model_video:
                    self.connect_player.setSource(QUrl.fromLocalFile(clip_path))
                else:
                    # Sin video del modelo: mostrar su foto (la del usuario o
                    # la ilustracion incluida) en vez de un recuadro vacio.
                    self.connect_player.setSource(QUrl())
                    self.camera_image.set_image(self._camera_image_path(model))

            # Modell-Logo (PNG) statt Text-Titel anzeigen, sofern fuer das
            # erkannte Modell eine Logo-Datei im assets-Ordner existiert.
            # Fehlt sie (z.B. HERO9), faellt es automatisch auf den
            # normalen Text-Titel zurueck.
            logo_path = find_logo_path(model)
            logo_pix = QPixmap(logo_path) if logo_path else QPixmap()
            if logo_path and logo_pix.isNull():
                print(f"[Logo] Datei gefunden, konnte aber nicht als Bild geladen werden: {logo_path}")
            if not logo_pix.isNull():
                logo_pix = trim_transparent_margins(logo_pix)
                # Gross ueber dem Video (wie im Screenshot markiert) -
                # Breite orientiert sich am 420px breiten Video-Container.
                scaled_logo = hidpi_scaled_to_width(logo_pix, 360, screen_dpr(self))
                self.logo_label.setPixmap(scaled_logo)
                self.logo_label.show()
                self.title_label.hide()
            else:
                self.logo_label.clear()
                self.logo_label.hide()
                self.title_label.setText(profile["display"])
                self.title_label.setStyleSheet("font-size: 34px; font-weight: 800; letter-spacing: 3px; color: #ffffff;")
                self.title_label.show()
            if not self.btn_sync.is_syncing:
                self.btn_sync.set_idle(True)
            self.btn_pick_files.show()
            if not getattr(self, "_has_model_video", True):
                self.media_stack.setCurrentIndex(3)
                self.btn_camera_photo.show()
            else:
                self.media_stack.setCurrentIndex(2 if profile.get("is_360") else 1)
                self.btn_camera_photo.hide()

            self.rounded_video.hide_freeze_frame()
            self.connect_player.setPosition(0)
            self.connect_player.play()
            
            self.stat_status.setText(tr(self.lang, "connected"))
            self.stat_status.setStyleSheet(f"color: {c}; font-size: 15px; font-weight: 800; letter-spacing: 0.6px;")
            self.lbl_store_detail.setText(tr(self.lang, "reading_status"))
            self.badge_wifi.set_active(True, c)
            self.badge_bluetooth.set_active(True, c)
            self.badge_gps.set_active(True, c)
        else:
            self._current_model = None
            self.logo_label.clear()
            self.logo_label.hide()
            self.title_label.setText(tr(self.lang, "connect_gopro"))
            self.title_label.setStyleSheet("font-size: 26px; font-weight: 800; color: #ffffff;")
            self.title_label.show()
            if not self.btn_sync.is_syncing:
                self.btn_sync.set_idle(False)
            self.btn_pick_files.hide()
            self.connect_player.stop()
            self.rounded_video.hide_freeze_frame()
            self.media_stack.setCurrentIndex(0)
            self.btn_camera_photo.hide()
            self.stat_status.setText(tr(self.lang, "not_connected"))
            self.stat_status.setStyleSheet("color: #ff5c6c; font-size: 15px; font-weight: 800; letter-spacing: 0.6px;")
            self.badge_wifi.set_active(False)
            self.badge_bluetooth.set_active(False)
            self.badge_gps.set_active(False)
            self.bar_battery.set_value_and_color(-1, "#333")
            self.bar_storage.set_value_and_color(0, "#333")
            self.lbl_store_detail.setText("")

    def _camera_image_path(self, model):
        custom = (self.config.get("camera_images") or {}).get(model or "")
        if custom and os.path.exists(custom):
            return custom
        return asset_path("camera_generic.png")

    def choose_camera_photo(self):
        """Foto propia de la camara para el modelo conectado (se copia a la
        carpeta de configuracion, asi no depende de donde estaba el archivo)."""
        model = self._current_model
        if not model:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr(self.lang, "camera_photo_title"), _safe_local_dir(), "*.png *.jpg *.jpeg *.bmp *.webp")
        if not path:
            return
        try:
            dest_dir = os.path.join(CONFIG_DIR, "camera_images")
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, re.sub(r"[^A-Za-z0-9]+", "_", model) + os.path.splitext(path)[1].lower())
            shutil.copy2(path, dest)
        except Exception:
            dest = path
        images = dict(self.config.get("camera_images") or {})
        images[model] = dest
        self.config["camera_images"] = images
        save_config(self.config)
        self.camera_image.set_image(dest)

    def choose_folder(self):
        new_dir = QFileDialog.getExistingDirectory(
            self, tr(self.lang, "choose_target_dir_title"), _valid_start_dir(self.sync_target_dir),
        )
        if new_dir:
            self.sync_target_dir = os.path.normpath(new_dir)
            self.config["target_dir"] = self.sync_target_dir
            save_config(self.config)
            self.folder_changed.emit(self.sync_target_dir)

    def on_delete_toggle(self, state):
        self.config["delete_after_sync"] = bool(state)
        save_config(self.config)

    def _busy(self):
        return self._copy_busy() or self._backup_busy() or self._tool_busy()

    def _tool_busy(self):
        w = getattr(self, "tool_worker", None)
        return w is not None and w.isRunning()

    def _copy_busy(self):
        w = getattr(self, "sync_worker", None)
        return w is not None and w.isRunning()

    def _backup_busy(self):
        w = getattr(self, "backup_worker", None)
        return w is not None and w.isRunning()

    @staticmethod
    def _lane(task):
        return task[0] if task[0] in ("backup", "tool") else "copy"

    def _set_lane_pct(self, lane, pct):
        """Progreso de la barra lateral/barra de tareas: promedio de lo que corre."""
        self._lane_pct[lane] = pct
        vals = [v for v in self._lane_pct.values() if v is not None]
        self.activity_progress.emit(sum(vals) / len(vals) if vals else None)

    def _pop_next(self, lane):
        for i, t in enumerate(self._import_queue):
            if self._lane(t) == lane:
                self._import_queue.pop(i)
                self._refresh_queue()
                return t
        return None

    def _queue_labels(self):
        out = []
        for task in self._import_queue:
            if task[0] == "camera":
                text = tr(self.lang, "queue_camera_long")
                out.append((text, text))
            elif task[0] == "backup":
                out.append((tr(self.lang, "queue_backup_item", dest=short_path(task[2][0])),
                            f"{task[1]}  →  {', '.join(task[2])}"))
            elif task[0] == "tool":
                out.append((task[1], task[1]))
            else:
                out.append((tr(self.lang, "queue_import_item", folder=short_path(task[1])), f"{task[1]}  →  {task[2]}"))
        return out

    @staticmethod
    def _task_key(task):
        if task[0] == "camera":
            return ("camera",)
        if task[0] == "tool":
            return ("tool", task[1])
        if task[0] == "backup":
            return ("backup", os.path.normcase(os.path.abspath(task[1])),
                    tuple(os.path.normcase(os.path.abspath(d)) for d in task[2]))
        return ("import", os.path.normcase(os.path.abspath(task[1])), os.path.normcase(os.path.abspath(task[2])))

    def _try_enqueue(self, task):
        """Agrega a la cola si no es lo mismo que lo que ya se copia o espera."""
        key = self._task_key(task)
        if any(cur is not None and self._task_key(cur) == key
               for cur in (self._current_task, self._backup_task, self._tool_task)):
            show_toast(self, tr(self.lang, "queue_running_same"))
            return False
        if any(self._task_key(t) == key for t in self._import_queue):
            show_toast(self, tr(self.lang, "queue_duplicate"))
            return False
        self._import_queue.append(task)
        self._refresh_queue()
        return True

    def remove_queued(self, idx):
        """Quita una tarea en espera (la copia en curso no se toca)."""
        if 0 <= idx < len(self._import_queue):
            self._import_queue.pop(idx)
            self._refresh_queue()

    def _refresh_queue(self):
        self.progress_panel.set_queue(sum(1 for t in self._import_queue if self._lane(t) == "copy"))
        self.backup_panel.set_queue(sum(1 for t in self._import_queue if self._lane(t) == "backup"))
        self.tools_panel.set_queue(sum(1 for t in self._import_queue if self._lane(t) == "tool"))
        self.activity_page.set_queue(self._queue_labels())

    def _open_progress(self, title):
        self.sync_popup.hide()
        self._current_title = title
        self._set_lane_pct("copy", 0.0)
        self.progress_panel.start(title, self.sync_worker,
                                  queued=sum(1 for t in self._import_queue if self._lane(t) == "copy"))
        self.activity_page.set_running(True)
        self.lnk_activity.setText(tr(self.lang, "activity_view"))
        self._refresh_queue()
        self.lnk_activity.setVisible(self._camera_task_running)

    def show_progress(self):
        self.progress_panel.show()

    def cancel_sync(self):
        """Cancelar: detiene la copia en curso y vacia su cola (los respaldos siguen)."""
        self._import_queue[:] = [t for t in self._import_queue if self._lane(t) != "copy"]
        self._refresh_queue()
        worker = getattr(self, "sync_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()

    def cancel_tools(self):
        self._import_queue[:] = [t for t in self._import_queue if self._lane(t) != "tool"]
        self._refresh_queue()
        worker = getattr(self, "tool_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()

    def start_tool(self, title, jobs, done_cb=None):
        """Herramienta en segundo plano (Actividad): progreso, Cancelar y cola."""
        if not ffmpeg_exe():
            show_toast(self, tr(self.lang, "ffmpeg_missing"))
            return
        task = ("tool", title, jobs, done_cb)
        if self._tool_busy():
            if self._try_enqueue(task):
                show_toast(self, tr(self.lang, "tool_queued"))
            return
        self.tool_worker = ToolJobThread(jobs, self.lang, self)
        self.tool_worker.progress.connect(self.on_tool_progress)
        self.tool_worker.finished_sync.connect(lambda ok, msg, st, cb=done_cb: self.on_tool_done(ok, msg, st, cb))
        self._tool_task = task
        self._tool_title = title
        self._set_lane_pct("tool", 0.0)
        self.tools_panel.start(title, self.tool_worker,
                               queued=sum(1 for t in self._import_queue if self._lane(t) == "tool"))
        self.activity_page.set_running(True)
        self.tool_worker.start()

    @Slot(int, int, str, float)
    def on_tool_progress(self, current, total, name, file_pct):
        if self.tools_panel.sizes:
            self._set_lane_pct("tool", self.tools_panel.total_percent(current, file_pct))

    def on_tool_done(self, success, message, stats, done_cb=None):
        self._tool_task = None
        self._set_lane_pct("tool", None)
        self.activity_page.add_history(self._tool_title, message, stats)
        self.activity_page.set_running(self._current_task is not None or self._backup_task is not None)
        self.tools_panel.hide()
        if done_cb:
            try:
                done_cb(success, stats)
            except Exception:
                traceback.print_exc()
        task = self._pop_next("tool") if not stats.get("cancelled") else None
        if task is not None:
            QTimer.singleShot(400, lambda: self.start_tool(task[1], task[2], task[3]))
        elif stats.get("copied", 0) > 0:
            SoundManager.play_finish()

    def cancel_backup(self):
        """Cancelar el respaldo en curso y los respaldos en cola."""
        self._import_queue[:] = [t for t in self._import_queue if self._lane(t) != "backup"]
        self._refresh_queue()
        worker = getattr(self, "backup_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()

    def start_import(self, folder, target=None, only_paths=None):
        """Importa desde una carpeta o tarjeta elegida a mano (no hace falta
        tener la camara conectada). 'target' permite otro destino solo para
        esta importacion."""
        target = target or self.sync_target_dir
        if self._copy_busy():
            # Ya hay algo copiandose: se agrega a la cola y empieza solo al terminar.
            if self._try_enqueue(("import", folder, target, only_paths)):
                show_toast(self, tr(self.lang, "import_queued", folder=folder))
            return
        if _is_same_or_inside(folder, target) or _is_same_or_inside(target, folder):
            show_toast(self, tr(self.lang, "import_same_as_target"))
            return
        self.sync_popup.setText(tr(self.lang, "import_searching", folder=folder))
        self.sync_popup.show()
        self.sync_worker = SyncWorkerThread(
            target, False, "all", self.lang,
            self.config.get("sort_into_date_folders", True), self, import_dir=folder, only_paths=only_paths
        )
        self.sync_worker.progress.connect(self.on_sync_progress)
        self.sync_worker.finished_sync.connect(self.on_sync_done)
        self._camera_task_running = False
        self._current_task = ("import", folder, target, only_paths)
        self._open_progress(tr(self.lang, "sync_title_import_from", folder=short_path(folder)))
        self.sync_worker.start()

    def pick_camera_files(self):
        if self.gopro_connected:
            self.camera_files_requested.emit()

    def start_sync(self, mode="all", only_keys=None):
        if not self.gopro_connected:
            return
        if self._copy_busy():
            # Antes no hacia nada: ahora la sincronizacion de la camara se encola.
            if self._try_enqueue(("camera", mode, only_keys)):
                show_toast(self, tr(self.lang, "camera_sync_queued"))
            self.activity_requested.emit()
            return

        self.btn_sync.start_sync_visual()
        search_msg = {
            "all": tr(self.lang, "search_all"),
            "pictures": tr(self.lang, "search_pictures"),
            "videos": tr(self.lang, "search_videos"),
        }.get(mode, tr(self.lang, "search_generic"))
        self.sync_popup.setText(search_msg)
        self.sync_popup.show()

        delete_after = self.chk_delete_after.isChecked()
        self.sync_worker = SyncWorkerThread(
            self.sync_target_dir, delete_after, mode, self.lang,
            self.config.get("sort_into_date_folders", True), self, only_keys=only_keys
        )
        self.sync_worker.progress.connect(self.on_sync_progress)
        self.sync_worker.finished_sync.connect(self.on_sync_done)
        device = LAST_DEVICE_NAME.get("name") or GOPRO_MODELS.get(self._current_model or "", {}).get("display", "GoPro")
        self.btn_sync.busy_label = tr(self.lang, "busy_syncing")
        self._camera_task_running = True
        self._current_task = ("camera", mode)
        self._open_progress(tr(self.lang, "sync_title_camera", device=device))
        self.sync_worker.start()

    @Slot(int, int, str, float)
    def on_sync_progress(self, current, total, name, file_pct):
        if not name.startswith("__CLEANUP__") and self.progress_panel.sizes:
            total_pct = self.progress_panel.total_percent(current, file_pct)
            self._set_lane_pct("copy", total_pct)
            if not self._camera_task_running:
                return   # importando una carpeta: el boton de la camara sigue libre
            # El boton muestra el progreso TOTAL (no el del archivo, que vuelve a 0 con cada uno)
            # sin animacion: llegan muchas actualizaciones por segundo y la
            # animacion reiniciada en cada una dejaba el boton atrasado
            self.btn_sync.set_progress(self.progress_panel.total_percent(current, file_pct), animate=False)
            return
        # Bei 0% (= neue Datei faengt gerade an) den Balken HART zuruecksetzen
        # statt von der vorherigen Datei aus "runter" zu animieren - jede
        # Datei bekommt so sichtbar ihren eigenen, frischen Balken. Alle
        # weiteren Updates derselben Datei werden weich animiert.
        self.btn_sync.set_progress(file_pct, animate=(file_pct > 0.0))

    @Slot(bool, str, dict)
    def on_sync_done(self, success, message, stats):
        self.btn_sync.set_idle(self.gopro_connected)
        was_camera = self._camera_task_running
        self._camera_task_running = False
        self._current_task = None
        self._set_lane_pct("copy", None)
        self.activity_page.add_history(self._current_title, message, stats)
        self.activity_page.set_running(self._backup_task is not None or self._tool_task is not None)
        self.progress_panel.hide()
        self.lnk_activity.setText(f"{message}   ·   {tr(self.lang, 'activity_view')}")
        self.lnk_activity.setVisible(was_camera)
        # Si hay tareas en cola, sigue con la proxima.
        task = self._pop_next("copy") if not stats.get("cancelled") else None
        if task is not None:
            if stats.get("copied", 0) > 0:
                self.sync_finished.emit()
            if task[0] == "camera":
                QTimer.singleShot(400, lambda: self.start_sync(task[1], only_keys=task[2] if len(task) > 2 else None))
            else:
                QTimer.singleShot(400, lambda: self.start_import(task[1], task[2], task[3] if len(task) > 3 else None))
            return
        if stats.get("copied", 0) > 0:
            SoundManager.play_finish()
        self.sync_finished.emit()
        if (stats.get("copied", 0) > 0 and self.config.get("backup_after_sync", True)
                and self.config.get("backup_dirs")):
            # Mostrar unos segundos el resultado del sync antes del respaldo.
            QTimer.singleShot(2500, self.start_backup)

    def start_backup(self, source=None, dests=None, verify=False):
        """Respalda la biblioteca (u otra carpeta) a los discos elegidos, en
        segundo plano: se ve en Actividad, se puede cancelar y se encola si
        ya hay otra copia en curso."""
        source = source or self.sync_target_dir
        dests = [d for d in (dests if dests is not None else (self.config.get("backup_dirs") or [])) if d]
        if not dests:
            self.sync_popup.setText(tr(self.lang, "backup_none"))
            self.sync_popup.show()
            QTimer.singleShot(5000, self._hide_sync_popup)
            return
        task = ("backup", source, tuple(dests), verify)
        if self._backup_busy():
            if self._try_enqueue(task):
                show_toast(self, tr(self.lang, "backup_queued"))
            return
        self.backup_worker = BackupWorkerThread(source, dests, self.lang, self, verify=verify)
        self.backup_worker.progress.connect(self.on_backup_progress)
        self.backup_worker.finished_sync.connect(self.on_backup_done)
        self._backup_task = task
        dest_label = short_path(dests[0]) + (f" (+{len(dests) - 1})" if len(dests) > 1 else "")
        self._backup_title = tr(self.lang, "backup_title", dest=dest_label)
        self._set_lane_pct("backup", 0.0)
        self.backup_panel.start(self._backup_title, self.backup_worker,
                                queued=sum(1 for t in self._import_queue if self._lane(t) == "backup"))
        self.activity_page.set_running(True)
        self.backup_worker.start()

    @Slot(int, int, str, float)
    def on_backup_progress(self, current, total, name, file_pct):
        if not name.startswith("__CLEANUP__") and self.backup_panel.sizes:
            self._set_lane_pct("backup", self.backup_panel.total_percent(current, file_pct))

    @Slot(bool, str, dict)
    def on_backup_done(self, success, message, stats):
        self._backup_task = None
        self._set_lane_pct("backup", None)
        self.activity_page.add_history(self._backup_title, message, stats)
        self.activity_page.set_running(self._current_task is not None or self._tool_task is not None)
        self.backup_panel.hide()
        task = self._pop_next("backup") if not stats.get("cancelled") else None
        if task is not None:
            QTimer.singleShot(400, lambda: self.start_backup(task[1], list(task[2]), task[3] if len(task) > 3 else False))
            return
        if stats.get("copied", 0) > 0:
            SoundManager.play_finish()

    def _hide_sync_popup(self):
        busy = any(getattr(self, attr, None) is not None and getattr(self, attr).isRunning()
                   for attr in ("sync_worker", "backup_worker"))
        if not busy:
            self.sync_popup.hide()

    @Slot(dict)
    def apply_status(self, status):
        try:
            self._apply_status(status)
        except Exception:
            print("=== FEHLER in apply_status (Akku/Speicher-Update) ===")
            traceback.print_exc()

    def _apply_status(self, status):
        if not self.gopro_connected: return
        bat = status["battery"]
        store = status["storage_pct"]
        if bat is not None:
            self.bar_battery.set_value_and_color(bat, "#00cc66" if bat >= 20 else "#ff4c4c")
        if store is not None:
            c = self.get_theme_color()
            self.bar_storage.set_value_and_color(store, c)
            self.lbl_store_detail.setText(tr(self.lang, "free_suffix", free=status['free_str']))
