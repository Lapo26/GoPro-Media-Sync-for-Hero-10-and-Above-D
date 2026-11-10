"""GoPro Sync Pro - Vollversion mit perfekten Video-Rändern und Timing-Fix."""

# Eigenes Hintergrundbild (optional) - liegt als "freier" Kind-Widget unterhalb von Sidebar/Seiten
# und wird per resizeEvent passend zur Fenstergröße zugeschnitten (wie CSS "background-size: cover").
# (Diese beiden Sätze stammen aus dem bereitgestellten Quelltext / Dokument.)

import sys
import os
import re
import random
import time
import json
import subprocess
import math
# urllib y asyncio se importan donde se usan (BLE / API HTTP): ~0,3 s menos al arrancar
import ctypes
import traceback
import struct
import shutil
import wave
import array
import io
import tempfile
import hashlib
import threading
import queue
from datetime import datetime, timedelta, timezone
try:
    import winreg  # nur unter Windows verfuegbar (fuer Autostart-Eintrag)
except ImportError:
    winreg = None
try:
    import winsound  # nur unter Windows verfuegbar (fuer den Hover-Sound)
except ImportError:
    winsound = None

# PySide6
from PySide6.QtCore import (Qt, QTimer, QThread, Signal, Slot, QRectF, QPointF, QPoint, QRect, QUrl,
                             QPropertyAnimation, QEasingCurve, Property, QEvent, QObject, QSize,
                             QStandardPaths, QParallelAnimationGroup, QDate)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QGraphicsDropShadowEffect, QDialog,
                               QStackedWidget, QFrame, QFileDialog, QTabWidget,
                               QGridLayout, QScrollArea, QColorDialog, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QSlider, QGraphicsBlurEffect, QMenu, QAbstractButton,
                               QGraphicsOpacityEffect, QLineEdit, QStackedLayout, QListWidget, QDateEdit, QProgressBar, QSystemTrayIcon,
                               QAbstractScrollArea, QToolTip, QTreeWidget, QTreeWidgetItem, QHeaderView)
from PySide6.QtGui import (QGuiApplication, QPixmap, QPainter, QColor, QPainterPath, QImage, 
                           QLinearGradient, QFont, QKeySequence, QShortcut, QRegion, QPen, QIcon, QImageReader)
# Multimedia
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QSoundEffect, QVideoSink, QMediaMetaData
from PySide6.QtMultimediaWidgets import QVideoWidget, QGraphicsVideoItem

# Optional: OpenCV for Video Thumbnails
# Silencia los mensajes de FFmpeg en la consola ("moov atom not found"...)
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
# OpenCV se carga recien al generar la primera miniatura de video (tarda
# ~1 s en importarse y no hace falta para arrancar).
cv2 = None
_HAS_CV2 = True


def _load_cv2():
    global cv2, _HAS_CV2
    if cv2 is None and _HAS_CV2:
        try:
            import cv2 as _cv2
            cv2 = _cv2
        except Exception:
            _HAS_CV2 = False
    return cv2

# Optional: bleak (Bluetooth LE) für das Auslesen des Akkustands per BLE,
# UNABHÄNGIG vom USB-Verbindungsmodus der Kamera (siehe read_battery_via_ble
# weiter unten für die ausführliche Erklärung, warum das der beste Weg ist).
# Ohne installiertes "bleak"-Paket (pip install bleak) wird dieser Weg
# einfach übersprungen und die alten Methoden greifen weiter.
# bleak (Bluetooth) tambien se carga recien cuando se lee la bateria por BLE.
BleakScanner = BleakClient = None
_HAS_BLEAK = True


def _load_bleak():
    global BleakScanner, BleakClient, _HAS_BLEAK
    if BleakScanner is None and _HAS_BLEAK:
        try:
            from bleak import BleakScanner as _S, BleakClient as _C
            BleakScanner, BleakClient = _S, _C
        except Exception:
            _HAS_BLEAK = False
    return _HAS_BLEAK

def minimize_own_console():
    """Minimiert (statt zu verstecken) das Konsolenfenster, in dem dieses
    Skript gestartet wurde - z.B. wenn die App per Doppelklick auf eine
    .bat-Datei oder direkt mit "python main.py" (statt "pythonw main.py")
    gestartet wird. So bleibt die Konsole fuer Log-Ausgaben/Debugging
    erreichbar (Taskleiste), stoert aber nicht optisch beim Start.
    Auf Nicht-Windows-Systemen oder falls kein Konsolenfenster existiert
    (z.B. als .exe ohne Konsole gebaut), passiert einfach nichts."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            SW_MINIMIZE = 6
            user32.ShowWindow(hwnd, SW_MINIMIZE)
    except Exception:
        pass


# Version de la app. La actualiza release.ps1 (y el workflow de release al compilar).
APP_VERSION = "1.3.1"

CONFIG_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "GoProSyncPro")
CONFIG_FILE = os.path.join(CONFIG_DIR, "sync_config.json")
# UNA sola familia: con varias (o con emojis en los textos) Qt revisa las ~500
# familias de Windows buscando reemplazos y el arranque tardaba 1,5-1,9 s mas.
FONT_STACK = "'Century Gothic'"

# --- [ UNTERSTUETZTE GOPRO-MODELLE ] ---
#
# Jedes unterstuetzte Modell hat einen MTP-Namensteil (wie es im Windows-
# Explorer unter "Dieser PC" auftaucht, z.B. "HERO11 Black"), eine Anzeige-
# Ueberschrift fuer die Sync-Seite und ein Video aus dem assets-Ordner, das
# beim Verbinden abgespielt wird. Neue Modelle koennen einfach hier ergaenzt
# werden, ohne den restlichen Code anzufassen.
GOPRO_MODELS = {
    "MISSION 1 PRO": {"display": "MISSION 1 PRO", "video": "GoPro Mission 1 Pro.mp4"},
    "HERO13": {"display": "HERO 13 BLACK", "video": "GoPro Hero 13.mp4"},
    "HERO12": {"display": "HERO 12 BLACK", "video": "GoPro Hero 12.mp4"},
    "HERO11": {"display": "HERO 11 BLACK", "video": "GoPro Hero 11 .mp4"},
    "HERO10": {"display": "HERO 10 BLACK", "video": "GoPro Hero 10.mp4"},
    "HERO9":  {"display": "HERO 9 BLACK",  "video": "GoPro Hero 9.mp4"},
    # HERO5 (Black y Session): por USB aparece en "Este equipo" como
    # "HERO5 Black" / "HERO5 Session" (siempre MTP, no tiene ajuste de modo
    # USB). "mvhd_local": la HERO5 guarda la fecha del MP4 en hora LOCAL y
    # no en UTC como las camaras nuevas - ver get_media_capture_date().
    "HERO5":  {"display": "HERO 5 BLACK",  "video": "GoPro Hero 5.mp4", "mvhd_local": True},
    # Tarjeta microSD de una GoPro en un lector de tarjetas, cuyo modelo no
    # se pudo leer de MISC\version.txt.
    "SD":     {"display": "GOPRO SD",      "video": "GoPro SD.mp4"},
    # 360-Grad-Kameras: "is_360" sorgt dafuer, dass das Verbindungs-Vorschau-
    # video NICHT flach, sondern im 360-Grad-Pan-Viewer (Ziehen zum Umschauen,
    # Mausrad zum Zoomen) angezeigt wird - siehe VideoFrameView weiter unten.
    "MAX 2": {"display": "GOPRO MAX 2", "video": "GoPro Max 2.mp4", "is_360": True},
    "MAX":   {"display": "GOPRO MAX",   "video": "GoPro Max.mp4",   "is_360": True},
}
# Reihenfolge, in der die Namensteile geprueft werden. "MISSION 1 PRO" zuerst,
# damit es (falls der MTP-Name z.B. auch "PRO" enthaelt) nicht versehentlich
# von einem anderen, kuerzeren Muster geschluckt wird. "MAX 2" steht bewusst
# VOR "MAX", sonst wuerde eine angeschlossene MAX 2 faelschlich schon als
# normale MAX erkannt (das kuerzere Muster wuerde zuerst zuschlagen).
GOPRO_MODEL_ORDER = ["MISSION 1 PRO", "HERO13", "HERO12", "HERO11", "HERO10", "HERO9", "HERO5", "MAX 2", "MAX"]

# Schriftzug-PNGs (GoPro-Font) fuers Ueberlagern des Verbindungs-Videos,
# je erkanntem Modell. Fehlt eine Datei, faellt automatisch auf den
# normalen Text-Titel zurueck (siehe SyncTabWidget._load_model_logo).
LOGO_FILENAMES = {
    "MISSION 1 PRO": "Mission 1 Pro Logo.png",
    "HERO13": "Hero 13 Logo.png",
    "HERO12": "Hero 12 Logo.png",
    "HERO11": "Hero 11 Logo.png",
    "HERO10": "Hero 10 Logo.png",
    "MAX 2": "Max 2 Logo.png",
    "MAX": "Max Logo.png",
}
# Fuer die PowerShell-Skripte (Akku/Speicher lesen, Dateien auflisten/kopieren):
# ein Regex-Muster, das JEDES unterstuetzte Modell matcht, z.B. "HERO11|HERO9".
GOPRO_MODEL_MATCH_PATTERN = "|".join(GOPRO_MODEL_ORDER)

# Globaler Cache für den API-Akkuwert
GLOBAL_CACHED_BATTERY = None

# Fuente activa detectada por DeviceMonitorThread:
#   model   -> clave de GOPRO_MODELS ("HERO5", "HERO11", "SD", ...) o None
#   sd_root -> letra de unidad (p.ej. "E:\\") si la fuente es una tarjeta SD
#              en un lector; None si la camara esta conectada por USB/MTP.
ACTIVE_SOURCE = {"model": None, "sd_root": None}
# Nombre real de la camara tal como la ve Windows / la tarjeta, p. ej. "HERO5 Black".
LAST_DEVICE_NAME = {"name": None}
# Activo mientras un SyncWorkerThread esta copiando (pausa las consultas de estado).
SYNC_IN_PROGRESS = threading.Event()
# Galeria de la camara abierta: no pedir el estado completo (compite con las miniaturas)
CAMERA_BROWSING = threading.Event()

def _safe_local_dir():
    """Liefert einen garantiert lokalen, existierenden Ordner (Bilder- oder
    Home-Verzeichnis) als Startordner für Dateidialoge.

    WICHTIG - Hintergrund des Fixes: Wird QFileDialog.getOpenFileName() mit
    "" als Startordner aufgerufen, merkt sich der NATIVE Windows-Dialog
    intern trotzdem den zuletzt besuchten Ordner. Da diese App staendig mit
    MTP-Pfaden der GoPro arbeitet (z.B. "Dieser PC\\HERO11 Black\\..."),
    kann es passieren, dass genau so ein Pfad als "zuletzt besucht" hinter-
    legt ist. Ist die GoPro dann gerade NICHT verbunden, versucht der native
    Explorer-Dialog beim Oeffnen trotzdem, das Geraet zu kontaktieren -
    und haengt sich dabei komplett auf (modal -> friert die ganze App ein,
    OHNE dass ueberhaupt ein Fenster erscheint).
    Der Fix: wir geben explizit IMMER einen garantiert lokalen, existierenden
    Ordner mit, damit der Dialog gar nicht erst versucht, zu einem toten
    MTP-Pfad zurueckzuspringen."""
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    if pictures and os.path.isdir(pictures):
        return pictures
    home = os.path.expanduser("~")
    return home if os.path.isdir(home) else ""


def _valid_start_dir(path):
    """Wie _safe_local_dir(), nur dass hier ein WUNSCH-Ordner (z.B. der
    zuletzt gewaehlte Sync-Zielordner) mitgegeben wird: ist dieser Ordner
    tatsaechlich noch vorhanden, wird er verwendet, ansonsten faellt es auf
    _safe_local_dir() zurueck. Genau dieselbe Ursache wie beim Wallpaper-
    Auswahl-Freeze: zeigt 'path' auf einen mittlerweile ungueltigen Ort
    (z.B. eine getrennte GoPro/MTP-Verbindung, ein entferntes Netzlaufwerk
    oder ein geloeschter USB-Stick-Pfad), wuerde der native Windows-Dialog
    versuchen dorthin zurueckzuspringen und sich dabei aufhaengen."""
    if path and os.path.isdir(path):
        return path
    return _safe_local_dir()


def _default_target_dir():
    """Windows 'Bilder'-Bibliothek + 'GoPro' - genau wie es die alte
    GoPro Quik-Desktop-App als Standard genutzt hat. QStandardPaths
    findet den echten Pfad auch dann korrekt, wenn der Nutzer seinen
    Bilder-Ordner z.B. auf ein anderes Laufwerk oder in OneDrive
    verschoben/umgeleitet hat."""
    pictures = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    if not pictures:
        pictures = os.path.expanduser("~/Pictures")
    return os.path.join(pictures, "GoPro")


def _system_language():
    """Idioma inicial segun el idioma de Windows: espanol, aleman o, para
    cualquier otro, ingles."""
    try:
        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return {0x0A: "es", 0x07: "de"}.get(primary, "en")
    except Exception:
        return "en"


SUPPORTED_LANGUAGES = ("es", "en", "de")

DEFAULT_CONFIG = {
    "target_dir": _default_target_dir(),
    "accent_color": "#00f2fe",
    "delete_after_sync": False,
    "background_image": "",   # Pfad zu einem eigenen Hintergrundbild, leer = Standard-Verlauf
    "background_blur": 0,     # Weichzeichner-Stärke (px) fürs Hintergrundbild
    "language": _system_language(),  # "es", "en" o "de" - ver TRANSLATIONS
    "language_chosen": False,  # True cuando el usuario eligio idioma (asistente o dialogo)
    "sort_into_date_folders": True,  # Dateien beim Sync in YYYY-MM-DD-Unterordner einsortieren
    "camera_images": {},        # foto propia por modelo, p. ej. {"HERO5": "...\\HERO5.jpg"}
    "thumb_cache_dir": "",      # vacio = %APPDATA%\\GoProSyncPro\\thumb_cache
    "backup_dirs": [],          # Respaldos: carpetas en discos externos que reciben una copia del destino
    "backup_after_sync": True,  # Respaldar automaticamente al terminar cada sincronizacion
    "ui_sound_volume": 70,    # 0-100, Lautstaerke fuer Boot/Klick/Fertig-Sounds
    "ui_sounds_muted": False,
    "profile_name": "",
    "profile_picture": "",
    "onboarding_done": False,
    "autostart_enabled": False,
    "open_on_gopro_connect": False,
}

### --- [ ÜBERSETZUNGEN / TRANSLATIONS ] -----------------------------------
#
# Simples, zentrales Wörterbuch statt eines schweren i18n-Frameworks - passt
# gut zur Groesse/dem Stil dieses Tools. tr(lang, key, **kwargs) holt den
# Text in der gewuenschten Sprache (Fallback: Deutsch, dann der Key selbst)
# und formatiert ihn optional mit den uebergebenen Werten.

TRANSLATIONS = {
    "de": {
        "nav_sync": "📷 Kamera",
        "nav_videos": "🎬 Videos",
        "nav_pictures": "📸 Bilder",
        "nav_settings": "⚙️  Einstellungen",
        "waiting_connection": "🔌\nWarte auf Verbindung...",
        "connect_gopro": "GoPro anschließen",
        "device_info": "GERÄTE-INFO",
        "battery_caption": "⚡  AKKUSTAND",
        "storage_caption": "SPEICHERPLATZ",
        "badge_wifi": "WLAN",
        "badge_bluetooth": "BLUETOOTH",
        "badge_gps": "GPS",
        "tooltip_wifi": "WLAN",
        "tooltip_bluetooth": "Bluetooth",
        "tooltip_gps": "GPS",
        "connected": "🟢 Verbunden",
        "not_connected": "🔴 Nicht verbunden",
        "reading_status": "Lese Status...",
        "free_suffix": "{free} frei",
        "delete_after_checkbox": "Nach der Übertragung von der GoPro löschen",
        "sync_idle": "Sync starten",
        "menu_all": "🔄  Alles synchronisieren",
        "menu_pictures": "🖼️  Nur Bilder synchronisieren",
        "menu_videos": "🎬  Nur Videos synchronisieren",
        "search_all": "🔎 Suche nach Bildern und Videos auf der GoPro...",
        "search_pictures": "🔎 Suche nach Bildern auf der GoPro...",
        "search_videos": "🔎 Suche nach Videos auf der GoPro...",
        "search_generic": "🔎 Suche nach Dateien auf der GoPro...",
        "transferring": "⬆️ Übertrage {current} von {total} Dateien\n{name}",
        "cleaning_up": "Räume auf – lösche Originale von der GoPro...",
        "cleaning_up_popup": "🧹 {name}",
        "ps_start_failed": "Konnte PowerShell nicht starten: {e}",
        "sync_failed_detail": "Sync fehlgeschlagen: {detail}",
        "sd_not_readable": "SD-Karte {root} konnte nicht gelesen werden.",
        "cam_page_title": "Auf der Kamera",
        "cam_back": "Zurück",
        "cam_badge_new": "NEU",
        "cam_preview_loading": "Vorschau wird von der Kamera geholt…",
        "cam_preview_failed": "Die Vorschau konnte nicht von der Kamera geholt werden.",
        "cam_view_btn": "Dateien der Kamera ansehen",
        "sync_already_suffix": " {n} waren schon da.",
        "import_pick_summary": "{total} Dateien · {new} neu",
        "import_pick_btn": "Dateien auswählen…",
        "import_pick_title": "Auswählen, was importiert wird",
        "import_picked": "{n} zum Import ausgewählt ({size})",
        "import_pick_use": "Auswahl übernehmen",
        "import_pick_hint": "Auf eine Miniatur klicken, um sie aus- oder abzuwählen.",
        "err_stalled": "Konnte nicht gelesen werden: keine Antwort bei dieser Datei (evtl. beschädigt). Übersprungen.",
        "err_empty_file": "Leere Datei (0 Byte), vermutlich eine abgebrochene Aufnahme: übersprungen.",
        "err_card_unresponsive": "Die Karte reagiert nicht mehr. Bitte prüfen (oder anderen Leser probieren) und erneut importieren: Bereits Kopiertes bleibt erhalten.",
        "backup_title": "Backup nach {dest}",
        "backup_queued": "Backup in der Warteschlange: startet, sobald die laufende Kopie fertig ist.",
        "queue_backup_item": "Backup nach {dest}",
        "busy_backup": "Backup läuft",
        "quit_busy_title": "Kopiervorgang läuft",
        "quit_busy_question": "Es wird gerade kopiert. Trotzdem beenden?",
        "quit_busy_detail": "Beim Beenden wird abgebrochen: Bereits Kopiertes bleibt erhalten, die halb kopierte Datei wird verworfen. Der nächste Sync macht dort weiter.",
        "quit_busy_keep": "Weiter kopieren",
        "quit_busy_quit": "Abbrechen und beenden",
        "cam_empty_file": "{name}: leere Datei (0 Byte), vermutlich eine abgebrochene Aufnahme",
        "cam_preview_too_big": "Dieses Video hat {size}: zum Ansehen bitte zuerst synchronisieren.",
        "cam_not_responding": "Die Kamera reagiert nicht. Bitte ab- und wieder anstecken (oder aus- und einschalten).",
        "cam_pick_btn": "Dateien auswählen…",
        "cam_files_title": "Dateien auf der Kamera",
        "cam_reading": "Kamera wird gelesen…",
        "cam_col_name": "Datei",
        "cam_col_date": "Aufnahmedatum",
        "cam_col_size": "Größe",
        "cam_col_status": "Status",
        "cam_status_synced": "Synchronisiert",
        "cam_status_new": "Neu",
        "cam_only_new": "Nur neue anzeigen",
        "cam_select_new": "Neue auswählen",
        "cam_select_none": "Keine",
        "cam_summary": "{total} Dateien auf der Kamera · {new} neu",
        "cam_selection": "{n} ausgewählt · {size}",
        "cam_sync_selected": "Auswahl synchronisieren",
        "cam_list_error": "Die Dateiliste der Kamera konnte nicht gelesen werden.",
        "ctx_delete": "Löschen…",
        "delete_title": "Datei löschen",
        "delete_question": "„{name}“ löschen?",
        "delete_detail": "Sie wird in den Papierkorb verschoben und kann von dort wiederhergestellt werden.",
        "delete_btn": "Löschen",
        "deleted_ok": "In den Papierkorb verschoben: {name}",
        "delete_failed": "{name} konnte nicht gelöscht werden.",
        "tray_open": "GoPro Sync öffnen",
        "tray_quit": "Beenden",
        "tray_hidden_msg": "GoPro Sync läuft neben der Uhr weiter. Zum Beenden: Rechtsklick auf das Symbol → Beenden.",
        "tray_tooltip_busy": "GoPro Sync — {task} {pct}%",
        "boot_ui": "Oberfläche wird vorbereitet…",
        "boot_camera": "Kamera wird geladen…",
        "boot_activity": "Schriften werden geladen…",
        "boot_galleries": "Galerien werden vorbereitet…",
        "boot_done": "Fertig",
        "card_detected": "GoPro-Karte auf {root} erkannt — „SD / Ordner importieren“ zum Kopieren verwenden.",
        "ctx_copy_path": "Pfad kopieren",
        "ctx_show_in_explorer": "Im Explorer anzeigen",
        "ctx_open": "Öffnen",
        "ctx_info": "Dateiinformationen",
        "path_copied": "Pfad kopiert",
        "info_title": "Dateiinformationen",
        "info_file": "DATEI",
        "info_media": "INHALT",
        "info_name": "Name",
        "info_path": "Pfad",
        "info_size": "Größe",
        "info_created": "Erstellt",
        "info_modified": "Geändert",
        "info_camera": "Kamera",
        "info_firmware": "Firmware",
        "info_taken": "Aufnahmedatum",
        "info_resolution": "Auflösung",
        "info_exposure": "Belichtung",
        "info_aperture": "Blende",
        "info_iso": "ISO",
        "info_focal": "Brennweite",
        "info_gps": "Ort (GPS)",
        "info_duration": "Dauer",
        "info_fps": "Bilder pro Sekunde",
        "info_codec": "Video-Codec",
        "info_audio": "Audio",
        "info_damaged": "Zustand",
        "info_none": "Keine eingebetteten Metadaten",
        "history_clear": "Verlauf löschen",
        "queue_duplicate": "Das ist bereits in der Warteschlange.",
        "queue_running_same": "Das wird gerade schon kopiert.",
        "activity_queue": "WARTESCHLANGE",
        "queue_remove": "Entfernen",
        "queue_import_item": "Importieren aus {folder}",
        "queue_camera_long": "Kamera synchronisieren",
        "err_gone": "Die Datei ist nicht mehr da (Karte oder Laufwerk getrennt?)",
        "err_device": "Fehler beim Lesen des Geräts (Karte getrennt oder beschädigt?)",
        "err_locked": "Lesen nicht möglich: Datei wird verwendet oder Zugriff verweigert",
        "err_disk_full": "Nicht genug Speicherplatz auf dem Ziellaufwerk",
        "nav_activity": "📋  Aktivität",
        "activity_title": "Aktivität",
        "activity_current": "LÄUFT",
        "activity_history": "VERLAUF",
        "activity_idle": "Gerade wird nichts kopiert.",
        "activity_empty": "In dieser Sitzung gibt es noch keine Aktivität.",
        "activity_errors": "⚠ {n} mit Fehler",
        "activity_show_errors": "Details anzeigen",
        "activity_hide_errors": "Ausblenden",
        "activity_view": "Details unter Aktivität →",
        "camera_sync_queued": "Kamera-Sync zur Warteschlange hinzugefügt.",
        "queue_camera_item": "Kamera",
        "err_timeout": "Die Kamera hat zu lange nicht geantwortet",
        "err_no_response": "Die Kamera hat nicht geantwortet (getrennt?)",
        "err_camera_error": "Die Kamera meldete einen Fehler: {e}",
        "err_unknown": "Unbekannter Fehler",
        "err_size_mismatch": "Die kopierte Datei hat nicht die erwartete Größe",
        "busy_syncing": "Synchronisiere",
        "busy_importing": "Importiere",
        "sync_title_import_from": "Importiere aus {folder}",
        "sync_queue": "In Warteschlange: {n}",
        "import_queued": "Zur Warteschlange hinzugefügt: {folder}",
        "sync_title_camera": "Synchronisiere {device}",
        "sync_title_import": "Bilder und Videos importieren",
        "sync_phase_searching": "Suche Bilder und Videos…",
        "sync_phase_copying": "Kopiere neue Dateien",
        "sync_files_progress": "{current} von {total} Dateien · {done} von {size}",
        "sync_speed_eta": "{speed}/s · noch ~{eta}",
        "sync_current_file": "Aktuelle Datei",
        "sync_cancelling": "Wird abgebrochen… (aktuelle Datei wird beendet)",
        "sync_cancelled_msg": "Abgebrochen: {copied} von {total} Dateien fertig. Auf der Kamera wurde nichts gelöscht.",
        "video_damaged": "Video beschädigt",
        "backup_dialog_title": "Auf Laufwerk sichern",
        "backup_from": "Zu sichernder Ordner",
        "backup_to": "Backup-Ziel",
        "backup_choose_source_title": "Zu sichernden Ordner wählen",
        "backup_summary": "{total} Dateien ({size}) im Ordner",
        "backup_pending": "{n} zu kopieren ({size}) · {skipped} bereits gesichert",
        "backup_up_to_date": "Das Backup ist bereits aktuell.",
        "backup_dest_missing": "Wähle ein Ziel (z. B. einen Ordner auf deiner externen Festplatte).",
        "backup_remember": "Dieses Ziel für automatische Backups verwenden",
        "backup_start_btn": "Sichern",
        "backup_progress": "Kopiere {current} von {total}",
        "backup_cancelled": "Backup abgebrochen: {copied} Dateien kopiert.",
        "close_btn2": "Schließen",
        "import_confirm_title": "Bilder und Videos importieren",
        "import_from": "Von",
        "import_to": "Nach",
        "import_change": "Ändern…",
        "import_scanning": "Wird analysiert…",
        "import_summary": "{photos} Bilder und {videos} Videos ({size})",
        "import_new_summary": "{new} neu · {existing} bereits importiert",
        "import_nothing_new": "Bereits alles importiert: alle {n} Dateien sind schon im Zielordner (gleicher Name und Größe).",
        "import_empty": "In diesem Ordner wurden keine Bilder oder Videos gefunden.",
        "import_btn": "Importieren",
        "cancel_btn": "Abbrechen",
        "nav_import": "📂  SD / Ordner importieren",
        "nav_backup": "💾  Auf Laufwerk sichern",
        "import_title": "SD-Karte oder Ordner zum Importieren wählen",
        "import_same_as_target": "Du kannst nicht aus dem Zielordner selbst importieren.",
        "import_searching": "🔎 Suche Bilder und Videos in {folder}...",
        "busy_wait": "Warte, bis der laufende Vorgang fertig ist.",
        "date_filter_btn": "📅  {label}",
        "date_all": "Alle Daten",
        "date_today": "Heute",
        "date_7": "Letzte 7 Tage",
        "date_30": "Letzte 30 Tage",
        "date_this_year": "Dieses Jahr",
        "date_custom": "Zeitraum wählen…",
        "date_range_title": "Nach Datum filtern",
        "date_from": "Von",
        "date_to": "Bis",
        "date_apply": "Anwenden",
        "date_no_results": "Keine Dateien in diesem Zeitraum.",
        "camera_photo_btn": "🖼  Kamerafoto ändern",
        "camera_photo_title": "Foto deiner Kamera wählen",
        "thumb_cache_label": "Vorschaubild-Cache",
        "thumb_cache_btn": "Ort ändern",
        "thumb_cache_hint": "Hier werden die Galerie-Vorschaubilder gespeichert. Vorhandene werden beim Ändern mitverschoben.",
        "thumb_cache_title": "Ordner für den Vorschaubild-Cache wählen",
        "thumb_cache_moved": "Cache verschoben: {n} Vorschaubilder",
        "section_backup": "BACKUP AUF EXTERNE LAUFWERKE",
        "backup_hint": "Der Zielordner wird zusätzlich in diese Ordner kopiert (nur neue/geänderte Dateien, Datumsangaben bleiben erhalten). Im Backup wird nie etwas gelöscht. Nicht angeschlossene Laufwerke werden übersprungen.",
        "backup_add_btn": "Hinzufügen",
        "backup_remove_btn": "Entfernen",
        "backup_now_btn": "Jetzt sichern",
        "backup_after_sync_checkbox": "Nach jedem Sync automatisch sichern",
        "backup_choose_title": "Backup-Ordner wählen",
        "backup_same_as_target": "Der Backup-Ordner darf nicht der Zielordner (oder ein Ordner darin) sein.",
        "backup_running": "💾 Backup {current}/{total}: {name}",
        "backup_scanning": "💾 Backup: vergleiche Dateien...",
        "backup_done": "💾 Backup fertig: {copied} kopiert, {skipped} schon vorhanden.",
        "backup_missing_suffix": " Nicht verfügbar: {dirs}.",
        "backup_errors_suffix": " {errors} Fehler.",
        "backup_none": "Kein Backup-Ordner konfiguriert.",
        "sync_failed_no_response": "Sync fehlgeschlagen: Keine Rückmeldung von der Kamera.",
        "gopro_not_found": "GoPro wurde nicht gefunden. Bitte Verbindung prüfen.",
        "no_device_list": "Konnte nicht auf die Geräteliste zugreifen.",
        "unknown_error": "Unbekannter Fehler.",
        "no_new_media": "Keine neuen Bilder oder Videos gefunden.",
        "no_new_pictures": "Keine neuen Bilder gefunden.",
        "no_new_videos": "Keine neuen Videos gefunden.",
        "sync_result": "✅ {copied} von {total} Dateien übertragen.",
        "sync_deleted_suffix": " {deleted} auf der GoPro gelöscht.",
        "sync_errors_suffix": " ⚠️ {errors} Fehler.",
        "video_gallery_title": "🎬 Videos",
        "picture_gallery_title": "📸 Bilder",
        "no_videos_yet": "Noch keine Videos synchronisiert.",
        "no_pictures_yet": "Noch keine Bilder synchronisiert.",
        "gallery_sort_newest": "Neueste zuerst",
        "gallery_sort_oldest": "Älteste zuerst",
        "gallery_today": "Heute",
        "gallery_yesterday": "Gestern",
        "saved_in_gallery": "✅ In Galerie gespeichert",
        "view_flat": "Normal",
        "view_360": "360°",
        "settings_window_title": "Einstellungen",
        "settings_header": "⚙️  Einstellungen",
        "tab_design": "🎨 Design",
        "tab_folder": "📁 Speicherort",
        "tab_sounds": "🔊 Sounds",
        "tab_system": "🖥️ System",
        "section_design": "🎨  Design",
        "accent_color": "Akzentfarbe",
        "custom_background": "Eigenes Hintergrundbild",
        "choose_image_btn": "🖼️ Bild wählen…",
        "remove_btn": "Entfernen",
        "bg_current": "Aktuell: {name}",
        "bg_none": "Kein eigenes Bild gesetzt – Standard-Verlauf ist aktiv.",
        "blur_label": "Weichzeichnung (Hintergrund)",
        "blur_hint": "Damit lassen sich die Bedienelemente optisch vom Hintergrundbild abheben.",
        "section_folder": "📁  Speicherort",
        "choose_folder_btn": "Ordner wählen",
        "folder_hint": "Hierhin werden synchronisierte Bilder und Videos gespeichert.",
        "sort_date_folders_checkbox": "Nach Datum und Kamera sortieren, wie Quik (JJJJ-MM-TT\\HERO5 Black 1)",
        "sort_date_folders_hint": "Nutzt das Aufnahmedatum aus den Metadaten der Datei (EXIF/Video), nicht das Kopierdatum.",
        "section_sounds": "🔊  UI-Sounds",
        "sound_volume_label": "Lautstärke",
        "sound_mute_checkbox": "UI-Sounds stummschalten",
        "section_autostart": "🚀  Autostart",
        "section_language": "🌐  Sprache",
        "language_de": "Deutsch",
        "language_en": "English",
        "language_es": "Español",
        "close_btn": "Schließen",
        "color_dialog_title": "Theme-Farbe wählen",
        "choose_target_dir_title": "Zielordner wählen",
        "choose_bg_title": "Hintergrundbild wählen",
        "image_filter": "Bilder (*.png *.jpg *.jpeg *.bmp *.webp)",
        # --- Einrichtungsassistent (erster Start) ---
        "onboard_step_of": "Schritt {current} von {total}",
        "onboard_language_title": "Sprache wählen",
        "onboard_language_sub": "Elige tu idioma · Choose your language",
        "onboard_welcome_title": "Willkommen bei GoPro Sync Pro! 👋",
        "onboard_welcome_sub": "Bevor's losgeht, ein paar kurze Fragen zur Einrichtung.",
        "onboard_name_label": "Wie dürfen wir dich nennen?",
        "onboard_name_placeholder": "Dein Name",
        "onboard_avatar_hint": "Klicke auf den Kreis, um ein Profilbild auszuwählen (optional).",
        "onboard_path_title": "Wo sollen deine Bilder & Videos landen?",
        "onboard_path_sub": "Du kannst das jederzeit später in den Einstellungen ändern.",
        "onboard_autostart_title": "Automatischer Start",
        "onboard_autostart_checkbox": "GoPro Sync Pro beim Windows-Start automatisch starten",
        "onboard_open_on_connect_checkbox": "App automatisch öffnen, sobald eine GoPro angeschlossen wird",
        "onboard_open_on_connect_hint": "Läuft dann unauffällig im Hintergrund und erscheint erst, wenn du deine Kamera anschließt.",
        "onboard_back_btn": "Zurück",
        "onboard_next_btn": "Weiter",
        "onboard_finish_btn": "Los geht's! 🚀",
        "onboard_skip_btn": "Später einrichten",
    },
    "en": {
        "nav_sync": "📷 Camera",
        "nav_videos": "🎬 Videos",
        "nav_pictures": "📸 Pictures",
        "nav_settings": "⚙️  Settings",
        "waiting_connection": "🔌\nWaiting for connection...",
        "connect_gopro": "Connect GoPro",
        "device_info": "DEVICE INFO",
        "battery_caption": "⚡  BATTERY",
        "storage_caption": "STORAGE",
        "badge_wifi": "WIFI",
        "badge_bluetooth": "BLUETOOTH",
        "badge_gps": "GPS",
        "tooltip_wifi": "WiFi",
        "tooltip_bluetooth": "Bluetooth",
        "tooltip_gps": "GPS",
        "connected": "🟢 Connected",
        "not_connected": "🔴 Not connected",
        "reading_status": "Reading status...",
        "free_suffix": "{free} free",
        "delete_after_checkbox": "Delete from GoPro after transfer",
        "sync_idle": "Start sync",
        "menu_all": "🔄  Sync all",
        "menu_pictures": "🖼️  Sync pictures only",
        "menu_videos": "🎬  Sync videos only",
        "search_all": "🔎 Searching for pictures and videos on the GoPro...",
        "search_pictures": "🔎 Searching for pictures on the GoPro...",
        "search_videos": "🔎 Searching for videos on the GoPro...",
        "search_generic": "🔎 Searching for files on the GoPro...",
        "transferring": "⬆️ Transferring {current} of {total} files\n{name}",
        "cleaning_up": "Cleaning up – deleting originals from the GoPro...",
        "cleaning_up_popup": "🧹 {name}",
        "ps_start_failed": "Could not start PowerShell: {e}",
        "sync_failed_detail": "Sync failed: {detail}",
        "sd_not_readable": "Could not read SD card {root}.",
        "cam_page_title": "On the camera",
        "cam_back": "Back",
        "cam_badge_new": "NEW",
        "cam_preview_loading": "Getting the preview from the camera…",
        "cam_preview_failed": "Could not get the preview from the camera.",
        "cam_view_btn": "View camera files",
        "sync_already_suffix": " {n} were already there.",
        "import_pick_summary": "{total} files · {new} new",
        "import_pick_btn": "Choose files…",
        "import_pick_title": "Choose what to import",
        "import_picked": "{n} chosen to import ({size})",
        "import_pick_use": "Use selection",
        "import_pick_hint": "Click a thumbnail to select or deselect it.",
        "err_stalled": "Could not read: no response for this file (it may be damaged). Skipped.",
        "err_empty_file": "Empty file (0 bytes), probably an interrupted recording: skipped.",
        "err_card_unresponsive": "The card stopped responding. Check it (or try another reader) and import again: everything already copied is kept.",
        "backup_title": "Backup to {dest}",
        "backup_queued": "Backup queued: it starts when the current copy finishes.",
        "queue_backup_item": "Backup to {dest}",
        "busy_backup": "Backing up",
        "quit_busy_title": "Copy in progress",
        "quit_busy_question": "A copy is in progress. Quit anyway?",
        "quit_busy_detail": "Quitting now cancels it: everything already copied is kept and the half-copied file is discarded. The next sync picks up where it left off.",
        "quit_busy_keep": "Keep copying",
        "quit_busy_quit": "Cancel and quit",
        "cam_empty_file": "{name}: empty file (0 bytes), probably an interrupted recording",
        "cam_preview_too_big": "This video is {size}: sync it first to watch it.",
        "cam_not_responding": "The camera is not responding. Unplug it and plug it back in (or turn it off and on).",
        "cam_pick_btn": "Choose files…",
        "cam_files_title": "Files on the camera",
        "cam_reading": "Reading the camera…",
        "cam_col_name": "File",
        "cam_col_date": "Date taken",
        "cam_col_size": "Size",
        "cam_col_status": "Status",
        "cam_status_synced": "Synced",
        "cam_status_new": "New",
        "cam_only_new": "Show only new files",
        "cam_select_new": "Select new",
        "cam_select_none": "None",
        "cam_summary": "{total} files on the camera · {new} new",
        "cam_selection": "{n} selected · {size}",
        "cam_sync_selected": "Sync selected",
        "cam_list_error": "Could not read the camera's file list.",
        "ctx_delete": "Delete…",
        "delete_title": "Delete file",
        "delete_question": "Delete “{name}”?",
        "delete_detail": "It will be moved to the Recycle Bin, so you can restore it from there.",
        "delete_btn": "Delete",
        "deleted_ok": "Moved to the Recycle Bin: {name}",
        "delete_failed": "Could not delete {name}.",
        "tray_open": "Open GoPro Sync",
        "tray_quit": "Quit",
        "tray_hidden_msg": "GoPro Sync keeps running next to the clock. To quit: right-click the icon → Quit.",
        "tray_tooltip_busy": "GoPro Sync — {task} {pct}%",
        "boot_ui": "Preparing the interface…",
        "boot_camera": "Loading the camera…",
        "boot_activity": "Loading fonts…",
        "boot_galleries": "Preparing galleries…",
        "boot_done": "Ready",
        "card_detected": "GoPro card detected on {root} — use “Import SD / folder” to copy it.",
        "ctx_copy_path": "Copy path",
        "ctx_show_in_explorer": "Show in Explorer",
        "ctx_open": "Open",
        "ctx_info": "File information",
        "path_copied": "Path copied",
        "info_title": "File information",
        "info_file": "FILE",
        "info_media": "CONTENT",
        "info_name": "Name",
        "info_path": "Path",
        "info_size": "Size",
        "info_created": "Created",
        "info_modified": "Modified",
        "info_camera": "Camera",
        "info_firmware": "Firmware",
        "info_taken": "Date taken",
        "info_resolution": "Resolution",
        "info_exposure": "Exposure",
        "info_aperture": "Aperture",
        "info_iso": "ISO",
        "info_focal": "Focal length",
        "info_gps": "Location (GPS)",
        "info_duration": "Duration",
        "info_fps": "Frames per second",
        "info_codec": "Video codec",
        "info_audio": "Audio",
        "info_damaged": "Status",
        "info_none": "No embedded metadata",
        "history_clear": "Clear history",
        "queue_duplicate": "That is already in the queue.",
        "queue_running_same": "That is already being copied.",
        "activity_queue": "QUEUED",
        "queue_remove": "Remove",
        "queue_import_item": "Import from {folder}",
        "queue_camera_long": "Sync the camera",
        "err_gone": "The file is gone (was the card or drive disconnected?)",
        "err_device": "Error reading the device (card disconnected or damaged?)",
        "err_locked": "Could not read: the file is in use or access is denied",
        "err_disk_full": "Not enough space on the target drive",
        "nav_activity": "📋  Activity",
        "activity_title": "Activity",
        "activity_current": "IN PROGRESS",
        "activity_history": "HISTORY",
        "activity_idle": "Nothing is being copied right now.",
        "activity_empty": "No activity yet in this session.",
        "activity_errors": "⚠ {n} with errors",
        "activity_show_errors": "Show details",
        "activity_hide_errors": "Hide",
        "activity_view": "See details in Activity →",
        "camera_sync_queued": "Camera sync added to the queue.",
        "queue_camera_item": "Camera",
        "err_timeout": "The camera took too long to respond",
        "err_no_response": "The camera did not respond (disconnected?)",
        "err_camera_error": "The camera returned an error: {e}",
        "err_unknown": "Unknown error",
        "err_size_mismatch": "The copied file does not have the expected size",
        "busy_syncing": "Syncing",
        "busy_importing": "Importing",
        "sync_title_import_from": "Importing from {folder}",
        "sync_queue": "Queued: {n}",
        "import_queued": "Added to the queue: {folder}",
        "sync_title_camera": "Syncing {device}",
        "sync_title_import": "Importing pictures and videos",
        "sync_phase_searching": "Looking for pictures and videos…",
        "sync_phase_copying": "Copying new files",
        "sync_files_progress": "{current} of {total} files · {done} of {size}",
        "sync_speed_eta": "{speed}/s · ~{eta} left",
        "sync_current_file": "Current file",
        "sync_cancelling": "Cancelling… (finishing the current file)",
        "sync_cancelled_msg": "Cancelled: {copied} of {total} files done. Nothing was deleted from the camera.",
        "video_damaged": "Damaged video",
        "backup_dialog_title": "Back up to drive",
        "backup_from": "Folder to back up",
        "backup_to": "Backup destination",
        "backup_choose_source_title": "Choose folder to back up",
        "backup_summary": "{total} files ({size}) in the folder",
        "backup_pending": "{n} to copy ({size}) · {skipped} already backed up",
        "backup_up_to_date": "The backup is already up to date.",
        "backup_dest_missing": "Choose a destination (for example, a folder on your external drive).",
        "backup_remember": "Use this destination for automatic backups",
        "backup_start_btn": "Back up",
        "backup_progress": "Copying {current} of {total}",
        "backup_cancelled": "Backup cancelled: {copied} files copied.",
        "close_btn2": "Close",
        "import_confirm_title": "Import pictures and videos",
        "import_from": "From",
        "import_to": "To",
        "import_change": "Change…",
        "import_scanning": "Scanning…",
        "import_summary": "{photos} pictures and {videos} videos ({size})",
        "import_new_summary": "{new} new · {existing} already imported",
        "import_nothing_new": "Everything is already imported: all {n} files are already in the target folder (same name and size).",
        "import_empty": "No pictures or videos found in this folder.",
        "import_btn": "Import",
        "cancel_btn": "Cancel",
        "nav_import": "📂  Import SD / folder",
        "nav_backup": "💾  Back up to drive",
        "import_title": "Choose an SD card or folder to import",
        "import_same_as_target": "You can't import from the target folder itself.",
        "import_searching": "🔎 Looking for pictures and videos in {folder}...",
        "busy_wait": "Wait for the current operation to finish.",
        "date_filter_btn": "📅  {label}",
        "date_all": "All dates",
        "date_today": "Today",
        "date_7": "Last 7 days",
        "date_30": "Last 30 days",
        "date_this_year": "This year",
        "date_custom": "Choose range…",
        "date_range_title": "Filter by date",
        "date_from": "From",
        "date_to": "To",
        "date_apply": "Apply",
        "date_no_results": "No files in those dates.",
        "camera_photo_btn": "🖼  Change camera photo",
        "camera_photo_title": "Choose a photo of your camera",
        "thumb_cache_label": "Thumbnail cache",
        "thumb_cache_btn": "Change location",
        "thumb_cache_hint": "Gallery thumbnails are stored here. Existing ones are moved when you change it.",
        "thumb_cache_title": "Choose a folder for the thumbnail cache",
        "thumb_cache_moved": "Cache moved: {n} thumbnails",
        "section_backup": "BACKUP TO EXTERNAL DRIVES",
        "backup_hint": "The target folder is also copied to these folders (new/changed files only, dates preserved). Nothing is ever deleted in a backup. Drives that are not plugged in are skipped.",
        "backup_add_btn": "Add",
        "backup_remove_btn": "Remove",
        "backup_now_btn": "Back up now",
        "backup_after_sync_checkbox": "Back up automatically after every sync",
        "backup_choose_title": "Choose backup folder",
        "backup_same_as_target": "The backup folder can't be the target folder (or a folder inside it).",
        "backup_running": "💾 Backup {current}/{total}: {name}",
        "backup_scanning": "💾 Backup: comparing files...",
        "backup_done": "💾 Backup done: {copied} copied, {skipped} already there.",
        "backup_missing_suffix": " Not available: {dirs}.",
        "backup_errors_suffix": " {errors} errors.",
        "backup_none": "No backup folder configured.",
        "sync_failed_no_response": "Sync failed: No response from the camera.",
        "gopro_not_found": "GoPro was not found. Please check the connection.",
        "no_device_list": "Could not access the device list.",
        "unknown_error": "Unknown error.",
        "no_new_media": "No new pictures or videos found.",
        "no_new_pictures": "No new pictures found.",
        "no_new_videos": "No new videos found.",
        "sync_result": "✅ {copied} of {total} files transferred.",
        "sync_deleted_suffix": " {deleted} deleted from the GoPro.",
        "sync_errors_suffix": " ⚠️ {errors} errors.",
        "video_gallery_title": "🎬 Videos",
        "picture_gallery_title": "📸 Pictures",
        "no_videos_yet": "No videos synced yet.",
        "no_pictures_yet": "No pictures synced yet.",
        "gallery_sort_newest": "Newest first",
        "gallery_sort_oldest": "Oldest first",
        "gallery_today": "Today",
        "gallery_yesterday": "Yesterday",
        "saved_in_gallery": "✅ Saved in Gallery",
        "view_flat": "Normal",
        "view_360": "360°",
        "settings_window_title": "Settings",
        "settings_header": "⚙️  Settings",
        "tab_design": "🎨 Design",
        "tab_folder": "📁 Location",
        "tab_sounds": "🔊 Sounds",
        "tab_system": "🖥️ System",
        "section_design": "🎨  Design",
        "accent_color": "Accent color",
        "custom_background": "Custom background image",
        "choose_image_btn": "🖼️ Choose image…",
        "remove_btn": "Remove",
        "bg_current": "Current: {name}",
        "bg_none": "No custom image set – default gradient is active.",
        "blur_label": "Blur (background)",
        "blur_hint": "This helps the controls visually stand out from the background image.",
        "section_folder": "📁  Save location",
        "choose_folder_btn": "Choose folder",
        "folder_hint": "Synced pictures and videos are saved here.",
        "sort_date_folders_checkbox": "Sort by date and camera, like Quik (YYYY-MM-DD\\HERO5 Black 1)",
        "sort_date_folders_hint": "Uses the capture date from the file's own metadata (EXIF/video), not the copy date.",
        "section_sounds": "🔊  UI sounds",
        "sound_volume_label": "Volume",
        "sound_mute_checkbox": "Mute UI sounds",
        "section_autostart": "🚀  Autostart",
        "section_language": "🌐  Language",
        "language_de": "Deutsch",
        "language_en": "English",
        "language_es": "Español",
        "close_btn": "Close",
        "color_dialog_title": "Choose theme color",
        "choose_target_dir_title": "Choose target folder",
        "choose_bg_title": "Choose background image",
        "image_filter": "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        # --- Onboarding wizard (first launch) ---
        "onboard_step_of": "Step {current} of {total}",
        "onboard_language_title": "Choose your language",
        "onboard_language_sub": "Elige tu idioma · Sprache wählen",
        "onboard_welcome_title": "Welcome to GoPro Sync Pro! 👋",
        "onboard_welcome_sub": "Just a few quick questions before we get started.",
        "onboard_name_label": "What should we call you?",
        "onboard_name_placeholder": "Your name",
        "onboard_avatar_hint": "Click the circle to choose a profile picture (optional).",
        "onboard_path_title": "Where should your pictures & videos go?",
        "onboard_path_sub": "You can change this anytime later in Settings.",
        "onboard_autostart_title": "Automatic start",
        "onboard_autostart_checkbox": "Start GoPro Sync Pro automatically with Windows",
        "onboard_open_on_connect_checkbox": "Open the app automatically when a GoPro is connected",
        "onboard_open_on_connect_hint": "Runs quietly in the background and only appears once you plug in your camera.",
        "onboard_back_btn": "Back",
        "onboard_next_btn": "Next",
        "onboard_finish_btn": "Let's go! 🚀",
        "onboard_skip_btn": "Set up later",
    },
    "es": {
        "nav_sync": "📷 Cámara",
        "nav_videos": "🎬 Videos",
        "nav_pictures": "📸 Fotos",
        "nav_settings": "⚙️  Ajustes",
        "waiting_connection": "🔌\nEsperando conexión...",
        "connect_gopro": "Conecta tu GoPro",
        "device_info": "INFO DEL DISPOSITIVO",
        "battery_caption": "⚡  BATERÍA",
        "storage_caption": "ALMACENAMIENTO",
        "badge_wifi": "WIFI",
        "badge_bluetooth": "BLUETOOTH",
        "badge_gps": "GPS",
        "tooltip_wifi": "WiFi",
        "tooltip_bluetooth": "Bluetooth",
        "tooltip_gps": "GPS",
        "connected": "🟢 Conectada",
        "not_connected": "🔴 No conectada",
        "reading_status": "Leyendo estado...",
        "free_suffix": "{free} libres",
        "delete_after_checkbox": "Borrar de la GoPro después de copiar",
        "sync_idle": "Sincronizar",
        "menu_all": "🔄  Sincronizar todo",
        "menu_pictures": "🖼️  Solo fotos",
        "menu_videos": "🎬  Solo videos",
        "search_all": "🔎 Buscando fotos y videos en la GoPro...",
        "search_pictures": "🔎 Buscando fotos en la GoPro...",
        "search_videos": "🔎 Buscando videos en la GoPro...",
        "search_generic": "🔎 Buscando archivos en la GoPro...",
        "transferring": "⬆️ Copiando {current} de {total} archivos\n{name}",
        "cleaning_up": "Limpiando: borrando originales de la GoPro...",
        "cleaning_up_popup": "🧹 {name}",
        "ps_start_failed": "No se pudo iniciar PowerShell: {e}",
        "sync_failed_detail": "Falló la sincronización: {detail}",
        "sd_not_readable": "No se pudo leer la tarjeta SD {root}.",
        "cam_page_title": "En la cámara",
        "cam_back": "Volver",
        "cam_badge_new": "NUEVO",
        "cam_preview_loading": "Trayendo la vista previa desde la cámara…",
        "cam_preview_failed": "No se pudo traer la vista previa de la cámara.",
        "cam_view_btn": "Ver archivos de la cámara",
        "sync_already_suffix": " {n} ya estaban.",
        "import_pick_summary": "{total} archivos · {new} nuevos",
        "import_pick_btn": "Elegir archivos…",
        "import_pick_title": "Elegir qué importar",
        "import_picked": "{n} elegidos para importar ({size})",
        "import_pick_use": "Usar selección",
        "import_pick_hint": "Tocá una miniatura para elegirla o quitarla.",
        "err_stalled": "No se pudo leer: no responde con este archivo (puede estar dañado). Se saltó.",
        "err_empty_file": "Archivo vacío (0 bytes), probablemente una grabación cortada: se omitió.",
        "err_card_unresponsive": "La tarjeta dejó de responder. Revisala (o probá con otro lector) y volvé a importar: lo ya copiado queda guardado.",
        "backup_title": "Respaldo a {dest}",
        "backup_queued": "Respaldo en cola: empieza cuando termine lo que se está copiando.",
        "queue_backup_item": "Respaldo a {dest}",
        "busy_backup": "Respaldando",
        "quit_busy_title": "Copia en curso",
        "quit_busy_question": "Hay una copia en curso. ¿Salir igual?",
        "quit_busy_detail": "Si salís ahora se cancela: lo que ya se copió queda guardado y el archivo que estaba a medias se descarta. La próxima vez que sincronices sigue desde donde quedó.",
        "quit_busy_keep": "Seguir copiando",
        "quit_busy_quit": "Cancelar y salir",
        "cam_empty_file": "{name}: archivo vacío (0 bytes), probablemente una grabación que se cortó",
        "cam_preview_too_big": "Este video pesa {size}: para verlo, sincronizalo primero.",
        "cam_not_responding": "La cámara no responde. Desconectala y volvé a conectarla (o apagala y prendela).",
        "cam_pick_btn": "Elegir archivos…",
        "cam_files_title": "Archivos en la cámara",
        "cam_reading": "Leyendo la cámara…",
        "cam_col_name": "Archivo",
        "cam_col_date": "Fecha de captura",
        "cam_col_size": "Tamaño",
        "cam_col_status": "Estado",
        "cam_status_synced": "Sincronizado",
        "cam_status_new": "Nuevo",
        "cam_only_new": "Mostrar solo los nuevos",
        "cam_select_new": "Seleccionar nuevos",
        "cam_select_none": "Ninguno",
        "cam_summary": "{total} archivos en la cámara · {new} nuevos",
        "cam_selection": "{n} seleccionados · {size}",
        "cam_sync_selected": "Sincronizar seleccionados",
        "cam_list_error": "No se pudo leer la lista de archivos de la cámara.",
        "ctx_delete": "Eliminar…",
        "delete_title": "Eliminar archivo",
        "delete_question": "¿Eliminar «{name}»?",
        "delete_detail": "Se moverá a la Papelera de reciclaje: si te arrepientes, lo recuperas desde ahí.",
        "delete_btn": "Eliminar",
        "deleted_ok": "Movido a la Papelera: {name}",
        "delete_failed": "No se pudo eliminar {name}.",
        "tray_open": "Abrir GoPro Sync",
        "tray_quit": "Salir",
        "tray_hidden_msg": "GoPro Sync sigue funcionando junto al reloj. Para salir: clic derecho en el ícono → Salir.",
        "tray_tooltip_busy": "GoPro Sync — {task} {pct}%",
        "boot_ui": "Preparando la interfaz…",
        "boot_camera": "Cargando la cámara…",
        "boot_activity": "Cargando fuentes…",
        "boot_galleries": "Preparando galerías…",
        "boot_done": "Listo",
        "card_detected": "Tarjeta GoPro detectada en {root} — usá «Importar SD / carpeta» para copiarla.",
        "ctx_copy_path": "Copiar ruta",
        "ctx_show_in_explorer": "Mostrar en el Explorador",
        "ctx_open": "Abrir",
        "ctx_info": "Información del archivo",
        "path_copied": "Ruta copiada",
        "info_title": "Información del archivo",
        "info_file": "ARCHIVO",
        "info_media": "CONTENIDO",
        "info_name": "Nombre",
        "info_path": "Ruta",
        "info_size": "Tamaño",
        "info_created": "Creado",
        "info_modified": "Modificado",
        "info_camera": "Cámara",
        "info_firmware": "Firmware",
        "info_taken": "Fecha de captura",
        "info_resolution": "Resolución",
        "info_exposure": "Exposición",
        "info_aperture": "Apertura",
        "info_iso": "ISO",
        "info_focal": "Distancia focal",
        "info_gps": "Ubicación (GPS)",
        "info_duration": "Duración",
        "info_fps": "Cuadros por segundo",
        "info_codec": "Códec de video",
        "info_audio": "Audio",
        "info_damaged": "Estado",
        "info_none": "Sin metadatos internos",
        "history_clear": "Borrar historial",
        "queue_duplicate": "Eso ya está en la cola.",
        "queue_running_same": "Eso ya se está copiando ahora.",
        "activity_queue": "EN COLA",
        "queue_remove": "Quitar",
        "queue_import_item": "Importar desde {folder}",
        "queue_camera_long": "Sincronizar la cámara",
        "err_gone": "El archivo ya no está (¿se desconectó la tarjeta o el disco?)",
        "err_device": "Error al leer el dispositivo (¿tarjeta desconectada o dañada?)",
        "err_locked": "No se pudo leer: el archivo está en uso o sin permiso",
        "err_disk_full": "No hay espacio suficiente en el disco de destino",
        "nav_activity": "📋  Actividad",
        "activity_title": "Actividad",
        "activity_current": "EN CURSO",
        "activity_history": "HISTORIAL",
        "activity_idle": "No hay ninguna copia en curso.",
        "activity_empty": "Todavía no hay actividad en esta sesión.",
        "activity_errors": "⚠ {n} con error",
        "activity_show_errors": "Ver detalles",
        "activity_hide_errors": "Ocultar",
        "activity_view": "Ver detalles en Actividad →",
        "camera_sync_queued": "Sincronización de la cámara agregada a la cola.",
        "queue_camera_item": "Cámara",
        "err_timeout": "La cámara tardó demasiado en responder",
        "err_no_response": "La cámara no respondió (¿se desconectó?)",
        "err_camera_error": "La cámara devolvió un error: {e}",
        "err_unknown": "Error desconocido",
        "err_size_mismatch": "El archivo copiado no tiene el tamaño esperado",
        "busy_syncing": "Sincronizando",
        "busy_importing": "Importando",
        "sync_title_import_from": "Importando desde {folder}",
        "sync_queue": "En cola: {n}",
        "import_queued": "Agregado a la cola: {folder}",
        "sync_title_camera": "Sincronizando {device}",
        "sync_title_import": "Importando fotos y videos",
        "sync_phase_searching": "Buscando fotos y videos…",
        "sync_phase_copying": "Copiando archivos nuevos",
        "sync_files_progress": "{current} de {total} archivos · {done} de {size}",
        "sync_speed_eta": "{speed}/s · quedan ~{eta}",
        "sync_current_file": "Archivo actual",
        "sync_cancelling": "Cancelando… (terminando el archivo en curso)",
        "sync_cancelled_msg": "Cancelado: {copied} de {total} archivos listos. No se borró nada de la cámara.",
        "video_damaged": "Video dañado",
        "backup_dialog_title": "Respaldar a disco",
        "backup_from": "Carpeta a respaldar",
        "backup_to": "Destino del respaldo",
        "backup_choose_source_title": "Elegir carpeta a respaldar",
        "backup_summary": "{total} archivos ({size}) en la carpeta",
        "backup_pending": "{n} para copiar ({size}) · {skipped} ya respaldados",
        "backup_up_to_date": "El respaldo ya está al día.",
        "backup_dest_missing": "Elige un destino (por ejemplo, una carpeta en tu disco externo).",
        "backup_remember": "Usar este destino en los respaldos automáticos",
        "backup_start_btn": "Respaldar",
        "backup_progress": "Copiando {current} de {total}",
        "backup_cancelled": "Respaldo cancelado: {copied} archivos copiados.",
        "close_btn2": "Cerrar",
        "import_confirm_title": "Importar fotos y videos",
        "import_from": "Desde",
        "import_to": "Hacia",
        "import_change": "Cambiar…",
        "import_scanning": "Analizando…",
        "import_summary": "{photos} fotos y {videos} videos ({size})",
        "import_new_summary": "{new} nuevos · {existing} ya importados",
        "import_nothing_new": "Ya está todo importado: los {n} archivos ya están en la carpeta de destino (mismo nombre y tamaño).",
        "import_empty": "No se encontraron fotos ni videos en esta carpeta.",
        "import_btn": "Importar",
        "cancel_btn": "Cancelar",
        "nav_import": "📂  Importar SD / carpeta",
        "nav_backup": "💾  Respaldar a disco",
        "import_title": "Elegir tarjeta SD o carpeta para importar",
        "import_same_as_target": "No puedes importar desde la propia carpeta de destino.",
        "import_searching": "🔎 Buscando fotos y videos en {folder}...",
        "busy_wait": "Espera a que termine la operación en curso.",
        "date_filter_btn": "📅  {label}",
        "date_all": "Todas las fechas",
        "date_today": "Hoy",
        "date_7": "Últimos 7 días",
        "date_30": "Últimos 30 días",
        "date_this_year": "Este año",
        "date_custom": "Elegir rango…",
        "date_range_title": "Filtrar por fecha",
        "date_from": "Desde",
        "date_to": "Hasta",
        "date_apply": "Aplicar",
        "date_no_results": "No hay archivos en esas fechas.",
        "camera_photo_btn": "🖼  Cambiar foto de la cámara",
        "camera_photo_title": "Elegir una foto de tu cámara",
        "thumb_cache_label": "Caché de miniaturas",
        "thumb_cache_btn": "Cambiar ubicación",
        "thumb_cache_hint": "Aquí se guardan las miniaturas de la galería. Al cambiarla, las existentes se mueven al nuevo lugar.",
        "thumb_cache_title": "Elegir carpeta para la caché de miniaturas",
        "thumb_cache_moved": "Caché movida: {n} miniaturas",
        "section_backup": "RESPALDO EN DISCOS EXTERNOS",
        "backup_hint": "La carpeta de destino se copia también a estas carpetas (solo archivos nuevos o modificados, conservando las fechas). En el respaldo nunca se borra nada. Los discos que no estén conectados se saltan.",
        "backup_add_btn": "Agregar",
        "backup_remove_btn": "Quitar",
        "backup_now_btn": "Respaldar ahora",
        "backup_after_sync_checkbox": "Respaldar automáticamente después de cada sincronización",
        "backup_choose_title": "Elegir carpeta de respaldo",
        "backup_same_as_target": "La carpeta de respaldo no puede ser la carpeta de destino (ni estar dentro de ella).",
        "backup_running": "💾 Respaldo {current}/{total}: {name}",
        "backup_scanning": "💾 Respaldo: comparando archivos...",
        "backup_done": "💾 Respaldo listo: {copied} copiados, {skipped} ya estaban.",
        "backup_missing_suffix": " No disponible: {dirs}.",
        "backup_errors_suffix": " {errors} errores.",
        "backup_none": "No hay ninguna carpeta de respaldo configurada.",
        "sync_failed_no_response": "Falló la sincronización: la cámara no responde.",
        "gopro_not_found": "No se encontró la GoPro. Revisa la conexión.",
        "no_device_list": "No se pudo acceder a la lista de dispositivos.",
        "unknown_error": "Error desconocido.",
        "no_new_media": "No hay fotos ni videos nuevos.",
        "no_new_pictures": "No hay fotos nuevas.",
        "no_new_videos": "No hay videos nuevos.",
        "sync_result": "✅ {copied} de {total} archivos copiados.",
        "sync_deleted_suffix": " {deleted} borrados de la GoPro.",
        "sync_errors_suffix": " ⚠️ {errors} errores.",
        "video_gallery_title": "🎬 Videos",
        "picture_gallery_title": "📸 Fotos",
        "no_videos_yet": "Todavía no hay videos sincronizados.",
        "no_pictures_yet": "Todavía no hay fotos sincronizadas.",
        "gallery_sort_newest": "Más recientes primero",
        "gallery_sort_oldest": "Más antiguos primero",
        "gallery_today": "Hoy",
        "gallery_yesterday": "Ayer",
        "saved_in_gallery": "✅ Guardado en la galería",
        "view_flat": "Normal",
        "view_360": "360°",
        "settings_window_title": "Ajustes",
        "settings_header": "⚙️  Ajustes",
        "tab_design": "🎨 Diseño",
        "tab_folder": "📁 Ubicación",
        "tab_sounds": "🔊 Sonidos",
        "tab_system": "🖥️ Sistema",
        "section_design": "🎨  Diseño",
        "accent_color": "Color de acento",
        "custom_background": "Imagen de fondo propia",
        "choose_image_btn": "🖼️ Elegir imagen…",
        "remove_btn": "Quitar",
        "bg_current": "Actual: {name}",
        "bg_none": "Sin imagen propia: se usa el degradado predeterminado.",
        "blur_label": "Desenfoque (fondo)",
        "blur_hint": "Ayuda a que los controles se distingan mejor sobre la imagen de fondo.",
        "section_folder": "📁  Ubicación",
        "choose_folder_btn": "Elegir carpeta",
        "folder_hint": "Aquí se guardan las fotos y videos sincronizados.",
        "sort_date_folders_checkbox": "Ordenar por fecha y cámara, como Quik (AAAA-MM-DD\\HERO5 Black 1)",
        "sort_date_folders_hint": "Usa la fecha de captura guardada en el propio archivo (EXIF/video), no la fecha de copia.",
        "section_sounds": "🔊  Sonidos de la interfaz",
        "sound_volume_label": "Volumen",
        "sound_mute_checkbox": "Silenciar sonidos de la interfaz",
        "section_autostart": "🚀  Inicio automático",
        "section_language": "🌐  Idioma",
        "language_de": "Deutsch",
        "language_en": "English",
        "language_es": "Español",
        "close_btn": "Cerrar",
        "color_dialog_title": "Elegir color del tema",
        "choose_target_dir_title": "Elegir carpeta de destino",
        "choose_bg_title": "Elegir imagen de fondo",
        "image_filter": "Imágenes (*.png *.jpg *.jpeg *.bmp *.webp)",
        "onboard_step_of": "Paso {current} de {total}",
        "onboard_language_title": "Elige tu idioma",
        "onboard_language_sub": "Choose your language · Sprache wählen",
        "onboard_welcome_title": "¡Bienvenido a GoPro Sync Pro! 👋",
        "onboard_welcome_sub": "Solo unas preguntas rápidas antes de empezar.",
        "onboard_name_label": "¿Cómo te llamamos?",
        "onboard_name_placeholder": "Tu nombre",
        "onboard_avatar_hint": "Haz clic en el círculo para elegir una foto de perfil (opcional).",
        "onboard_path_title": "¿Dónde guardamos tus fotos y videos?",
        "onboard_path_sub": "Puedes cambiarlo cuando quieras en Ajustes.",
        "onboard_autostart_title": "Inicio automático",
        "onboard_autostart_checkbox": "Iniciar GoPro Sync Pro con Windows",
        "onboard_open_on_connect_checkbox": "Abrir la app automáticamente al conectar una GoPro",
        "onboard_open_on_connect_hint": "Se queda en segundo plano y solo aparece cuando conectas la cámara.",
        "onboard_back_btn": "Atrás",
        "onboard_next_btn": "Siguiente",
        "onboard_finish_btn": "¡Vamos! 🚀",
        "onboard_skip_btn": "Configurar más tarde",
    },
}


_EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")


def strip_emoji(text):
    """Quita emojis/simbolos que Century Gothic no tiene: dibujarlos obligaba a
    Qt a buscar en todas las fuentes de Windows (lento al arrancar)."""
    if not text:
        return text
    out = _EMOJI_RE.sub("", text)
    if out == text:
        return text
    out = "\n".join(line.strip() for line in out.split("\n")).strip("\n ")
    # Los sufijos (" ⚠️ 2 errores.") se pegan a otra frase: conservar su espacio inicial.
    return " " + out if text[:1] == " " and out else out


def tr(lang, key, **kwargs):
    table = TRANSLATIONS.get(lang, TRANSLATIONS["de"])
    text = strip_emoji(table.get(key, TRANSLATIONS["de"].get(key, key)))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


_EMOJI_ICON_CACHE = {}


def emoji_icon(ch, px=18):
    """Emoji dibujado como icono con UNA sola fuente (Segoe UI Emoji): unos ms,
    sin la busqueda de fuentes de reserva."""
    try:
        dpr = max(1.0, QGuiApplication.primaryScreen().devicePixelRatio())
    except Exception:
        dpr = 1.0
    key = (ch, px, dpr)
    if key not in _EMOJI_ICON_CACHE:
        pm = QPixmap(int(px * dpr), int(px * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        f = QFont("Segoe UI Emoji")
        f.setPixelSize(int(px * 0.85))
        painter.setFont(f)
        painter.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, ch)
        painter.end()
        _EMOJI_ICON_CACHE[key] = QIcon(pm)
    return _EMOJI_ICON_CACHE[key]


NAV_ICONS = {"nav_sync": "\U0001F4F7", "nav_videos": "\U0001F3AC", "nav_pictures": "\U0001F4F8",
             "nav_activity": "\U0001F4CB", "nav_import": "\U0001F4C2", "nav_backup": "\U0001F4BE",
             "nav_settings": "\u2699"}


def set_nav_text(button, lang, key):
    """Texto del menu lateral + su emoji como icono."""
    button.setText(tr(lang, key))
    ch = NAV_ICONS.get(key)
    if ch:
        button.setIcon(emoji_icon(ch, 18))
        button.setIconSize(QSize(18, 18))


def _read_config_file(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config no es un objeto")
    return data


def load_config():
    """Lee la configuracion de %APPDATA%\GoProSyncPro. Nunca la pierde:
    si el archivo principal esta danado (corte de luz a mitad de guardar...)
    se usa la copia de respaldo .bak, y el archivo danado se conserva aparte
    en vez de pisarlo con valores por defecto."""
    backup = CONFIG_FILE + ".bak"
    for path in (CONFIG_FILE, backup):
        if os.path.exists(path):
            try:
                loaded = _read_config_file(path)
                merged = dict(DEFAULT_CONFIG)
                merged.update(loaded)      # se conservan TODAS las claves existentes
                if path == backup:
                    print("[Config] Archivo principal danado: se recupero la copia de respaldo.")
                return merged
            except Exception:
                continue
    if os.path.exists(CONFIG_FILE):
        try:
            os.replace(CONFIG_FILE, CONFIG_FILE + datetime.now().strftime(".danado-%Y%m%d-%H%M%S"))
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config_dict):
    """Guardado seguro: escribe a un .tmp y lo reemplaza de una vez (nunca
    queda un archivo a medias) y antes guarda la version anterior en .bak."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(CONFIG_FILE):
            try:
                _read_config_file(CONFIG_FILE)          # solo respaldar si esta sano
                shutil.copy2(CONFIG_FILE, CONFIG_FILE + ".bak")
            except Exception:
                pass
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        # Guardar la configuracion nunca debe cerrar la app.
        print(f"No se pudo guardar la configuracion: {e}")


def asset_path(filename):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", filename)


_APP_ICON = None


def load_app_icon():
    global _APP_ICON
    if _APP_ICON is not None:
        return _APP_ICON
    _APP_ICON = _load_app_icon_uncached()
    return _APP_ICON


def _load_app_icon_uncached():
    """Laedt das App-Icon aus dem assets-Ordner. Bevorzugt wird
    'app_icon.ico' (Windows nutzt ohnehin intern .ico-Dateien fuer
    Titelleiste/Taskleiste und diese enthalten meist mehrere Groessen fuer
    scharfe Darstellung), 'GoPro Sync.png' dient als Fallback, falls die
    .ico-Datei mal fehlen sollte. Gibt ein (ggf. leeres) QIcon zurueck -
    ist keine der beiden Dateien vorhanden, bleibt einfach das Standard-
    Icon von Qt/Windows sichtbar."""
    ico_path = asset_path("app_icon.ico")
    if os.path.exists(ico_path):
        return QIcon(ico_path)
    png_path = asset_path("GoPro Sync.png")
    if os.path.exists(png_path):
        return QIcon(png_path)
    return QIcon()


def find_logo_path(model):
    """Sucht die Modell-Logo-Datei (siehe LOGO_FILENAMES) im assets-Ordner.
    Ist zusaetzlich tolerant gegenueber Gross-/Kleinschreibung, falls der
    tatsaechliche Dateiname im Ordner nicht exakt so geschrieben ist wie
    in LOGO_FILENAMES hinterlegt (z.B. "hero 11 logo.png" statt
    "Hero 11 Logo.png"). Gibt None zurueck, wenn nichts gefunden wurde -
    dann greift der Text-Titel als Fallback (siehe _update_ui_state)."""
    filename = LOGO_FILENAMES.get(model)
    if not filename:
        return None
    exact = asset_path(filename)
    if os.path.exists(exact):
        return exact
    assets_dir = os.path.dirname(exact)
    try:
        for f in os.listdir(assets_dir):
            if f.lower() == filename.lower():
                return os.path.join(assets_dir, f)
    except Exception:
        pass
    print(f"[Logo] Keine Logo-Datei gefunden fuer Modell '{model}' - erwartet "
          f"'{filename}' im Ordner '{assets_dir}'. Es wird der Text-Titel angezeigt.")
    return None


def _find_sound_asset(base_name):
    """Sucht eine Sound-Datei im assets-Ordner. WAV wird bewusst zuerst
    gesucht (das Sound-System laeuft jetzt komplett auf WAV/QSoundEffect
    um) - andere Formate bleiben als Fallback moeglich."""
    for ext in (".wav", ".mp3", ".ogg", ".m4a", ".flac"):
        p = asset_path(base_name + ext)
        if os.path.exists(p):
            return p
    return None


class SoundManager:
    """Zentrale, statische Klasse fuers Abspielen ALLER UI-Sounds (WAV).

    WICHTIG - zwei getrennte Systeme, bewusst NICHT gemischt:

    1) LANGE Einzel-Sounds (boot/finish/settings) laufen ueber QMediaPlayer
       + QAudioOutput - eine vollstaendig unabhaengige Wiedergabe-Pipeline
       pro Sound. Grund: Ueber QSoundEffect (egal ob mit vielen einzelnen
       Instanzen oder einem geteilten Kanal) kam es dazu, dass z.B. der
       laengere settings.wav-Sound das komplette Audio-System so lange
       blockiert hat, dass mehrere nachfolgende Hover-Sounds komplett
       ausblieben ("settings.wav spielt noch bei den naechsten 4 Hovers
       durch") - QSoundEffect ist von Qt explizit fuer sehr KURZE Sounds
       gedacht, nicht fuer mehrsekuendige Clips.

    2) KURZE, haeufig wiederholte Sounds (Hover-Taps, Button-Klick, Toggle
       An/Aus) laufen weiterhin ueber QSoundEffect - dafuer ist die Klasse
       gemacht (niedrige Latenz, sauber neu startbar). Jeder dieser Sounds
       hat einen kleinen Pool eigener QSoundEffect-Instanzen (statt nur
       einer gemeinsamen), damit schnell aufeinanderfolgende Ausloesungen
       (z.B. zuegiges Ueberfahren mehrerer Buttons) sich nicht gegenseitig
       abschneiden.

    Erwartete Dateien im assets-Ordner (alle .wav):
      boot.wav                  - einmal beim Programmstart
      finish.wav                - wenn ein Sync mit uebertragenen Dateien fertig ist
      button.wav                - Klick auf einen Button/Toggle/Menüeintrag
      tap_01.wav ... tap_05.wav - beim Hovern ueber einen Button (zufaellig)
      toggle_on.wav             - ein Schalter wird eingeschaltet
      toggle_off.wav            - ein Schalter wird ausgeschaltet
      settings.wav              - beim Oeffnen der Einstellungen

    Lautstaerke (0.0-1.0) und Stummschaltung gelten global fuer alle Sounds
    und werden ueber configure() (siehe Einstellungen -> Sounds) gesetzt
    bzw. beim Programmstart aus der Config geladen.
    """
    # --- lange Einzel-Sounds (QMediaPlayer) ---
    _long_player = None
    _long_audio = None

    # --- kurze, oft wiederholte Sounds (QSoundEffect-Pools) ---
    _pools = {}          # Name -> Liste von QSoundEffect
    _pool_index = {}     # Name -> naechster Index im Pool (round robin)
    _tap_pool = []
    _tap_index = 0
    _tap_scaled_paths = []  # Liste temporaerer WAV-Dateipfade (lautstaerke-skalierte
                             # Kopien von tap_01-05.wav) - winsound kennt selbst keine
                             # Lautstaerke pro Aufruf, daher werden diese Kopien einmal
                             # pro Lautstaerke-Aenderung geschrieben und dann ganz normal
                             # ueber SND_FILENAME abgespielt (siehe play_hover() /
                             # _rebuild_tap_scaled() weiter unten).
    _tap_scaled_volume = None
    _tap_scaled_dir = None
    POOL_SIZE = 1

    _volume = 0.7   # 0.0 - 1.0
    _muted = False

    @classmethod
    def configure(cls, volume_percent, muted):
        cls._volume = max(0.0, min(1.0, volume_percent / 100.0))
        cls._muted = bool(muted)
        if cls._long_audio is not None:
            cls._long_audio.setVolume(cls._volume)
        for pool in cls._pools.values():
            for eff in pool:
                eff.setVolume(cls._volume)
        # Hover-Sounds laufen ueber winsound.PlaySound() (siehe play_hover()),
        # das selbst KEINE Lautstaerke pro Aufruf kennt. Deshalb werden die
        # tap_01-05.wav-Rohdaten hier direkt auf die eingestellte Lautstaerke
        # herunterskaliert und als fertig leiser gemachte WAV-Bytes im
        # Speicher zwischengespeichert (siehe _rebuild_tap_scaled()) - so
        # steuert der vorhandene "UI-Sounds"-Regler jetzt auch die Hover-Taps.
        cls._rebuild_tap_scaled()

    @classmethod
    def preload_all(cls):
        """Baut alle Pools/Player schon beim Programmstart auf, statt erst
        beim ersten Gebrauch - gibt Qt Zeit, das Audiogeraet im Hintergrund
        zu oeffnen, bevor der erste echte Sound gebraucht wird."""
        cls._ensure_pool("button")
        cls._ensure_tap_pool()
        # Solo preparar el reproductor si hay sonidos largos (hoy no hay .wav en
        # assets: crearlo igual costaba ~0,3 s al arrancar).
        if cls._long_audio is None and any(_find_sound_asset(n) for n in ("boot", "finish", "settings")):
            cls._long_player = QMediaPlayer()
            cls._long_audio = QAudioOutput()
            cls._long_audio.setVolume(cls._volume)
            cls._long_player.setAudioOutput(cls._long_audio)

    # ---------- lange Einzel-Sounds ----------

    @classmethod
    def _play_long(cls, name):
        if cls._muted:
            return
        path = _find_sound_asset(name)
        if not path:
            return
        try:
            if cls._long_player is None:
                cls._long_player = QMediaPlayer()
                cls._long_audio = QAudioOutput()
                cls._long_audio.setVolume(cls._volume)
                cls._long_player.setAudioOutput(cls._long_audio)
            cls._long_player.stop()
            cls._long_player.setSource(QUrl.fromLocalFile(path))
            cls._long_player.play()
        except Exception:
            pass

    @classmethod
    def play_boot(cls):
        cls._play_long("boot")

    @classmethod
    def play_finish(cls):
        cls._play_long("finish")

    @classmethod
    def play_settings(cls):
        cls._play_long("settings")

    # ---------- kurze, oft wiederholte Sounds ----------

    @classmethod
    def _ensure_pool(cls, name):
        if name in cls._pools:
            return
        path = _find_sound_asset(name)
        pool = []
        if path:
            for _ in range(cls.POOL_SIZE):
                eff = QSoundEffect()
                eff.setSource(QUrl.fromLocalFile(path))
                eff.setVolume(cls._volume)
                pool.append(eff)
        cls._pools[name] = pool
        cls._pool_index[name] = 0

    _fade_anims = []  # haelt laufende Fade-Ins am Leben (sonst GC durch Python)

    @classmethod
    def _play_with_fade_in(cls, effect):
        """Spielt einen QSoundEffect NICHT direkt auf Zielwolume ab, sondern
        blendet die Lautstaerke in ca. 20ms von 0 auf den Zielwert ein.
        Grund: Das anhaltende Knistern trat bei JEDEM Hover auf, unabhaengig
        von Vorladen/Cooldown/Kanal-Architektur - das deutet stark darauf
        hin, dass die WAV-Datei selbst nicht exakt bei einem Nulldurchgang
        beginnt (ein sehr verbreitetes Phaenomen bei kurzen, zugeschnittenen
        UI-Sounds). Ein kurzes Einblenden ueberdeckt genau diesen kleinen
        Sprung am Anfang der Wellenform, ohne dass man die Verzoegerung
        hoert (20ms sind praktisch unbemerkbar)."""
        try:
            effect.setVolume(0.0)
            effect.play()
            anim = QPropertyAnimation(effect, b"volume")
            anim.setDuration(20)
            anim.setStartValue(0.0)
            anim.setEndValue(cls._volume)

            def _cleanup():
                if anim in cls._fade_anims:
                    cls._fade_anims.remove(anim)

            anim.finished.connect(_cleanup)
            cls._fade_anims.append(anim)
            anim.start()
        except Exception:
            pass

    @classmethod
    def _play_pooled(cls, name):
        if cls._muted:
            return
        cls._ensure_pool(name)
        pool = cls._pools.get(name)
        if not pool:
            return
        idx = cls._pool_index[name]
        cls._pool_index[name] = (idx + 1) % len(pool)
        cls._play_with_fade_in(pool[idx])

    @classmethod
    def play_click(cls):
        """Klick auf einen Button/ein Menue - Datei: button.wav"""
        cls._play_pooled("button")

    @classmethod
    def play_toggle(cls, is_on):
        # Laeuft wie boot/finish/settings ueber _play_long() (QMediaPlayer),
        # NICHT ueber den QSoundEffect-Pool - toggle_on/off.wav zeigten das
        # gleiche Nachspiel-Problem wie settings.wav, wenn sie ueber
        # QSoundEffect liefen (vermutlich weil sie fuer QSoundEffect
        # ebenfalls "zu lang" sind - siehe Klassen-Docstring).
        cls._play_long("toggle_on" if is_on else "toggle_off")

    @classmethod
    def _ensure_tap_pool(cls):
        if cls._tap_pool:
            return
        for i in range(1, 6):
            path = _find_sound_asset(f"tap_0{i}")
            if path:
                cls._tap_pool.append(path)
        cls._rebuild_tap_scaled()

    @classmethod
    def _ensure_tap_scaled_dir(cls):
        if cls._tap_scaled_dir and os.path.isdir(cls._tap_scaled_dir):
            return cls._tap_scaled_dir
        try:
            cls._tap_scaled_dir = tempfile.mkdtemp(prefix="goprosync_taps_")
        except Exception as e:
            print(f"[Hover-Sound] Konnte kein Temp-Verzeichnis anlegen: {e}")
            cls._tap_scaled_dir = None
        return cls._tap_scaled_dir

    @classmethod
    def _write_scaled_wav(cls, src_path, factor, out_path):
        """Liest eine WAV-Datei ein und schreibt eine auf 'factor' (0.0-1.0)
        herunterskalierte Kopie nach 'out_path' (gueltige, vollstaendige
        WAV-Datei inkl. Header). Nur 16-bit-PCM wird tatsaechlich skaliert;
        andere Formate werden 1:1 (Originallautstaerke) kopiert, statt den
        Sound kaputt zu machen. Gibt True bei Erfolg zurueck."""
        try:
            with wave.open(src_path, "rb") as wf:
                params = wf.getparams()
                raw = wf.readframes(wf.getnframes())
            if params.sampwidth == 2 and factor < 0.999:
                samples = array.array("h")
                samples.frombytes(raw)
                if sys.byteorder == "big":
                    samples.byteswap()
                for i in range(len(samples)):
                    samples[i] = int(samples[i] * factor)
                if sys.byteorder == "big":
                    samples.byteswap()
                raw = samples.tobytes()
            with wave.open(out_path, "wb") as out:
                out.setparams(params)
                out.writeframes(raw)
            return True
        except Exception as e:
            print(f"[Hover-Sound] Skalieren fehlgeschlagen fuer {src_path}: {e}")
            return False

    @classmethod
    def _rebuild_tap_scaled(cls):
        """Schreibt fuer jede geladene tap_0X.wav eine auf die aktuelle
        UI-Sounds-Lautstaerke herunterskalierte Kopie als temporaere WAV-
        Datei (siehe configure()). Wird u.a. bei jeder Aenderung des
        Lautstaerke-Reglers aufgerufen - die tap-Dateien sind winzig, das
        Neuschreiben kostet praktisch nichts. Schlaegt das Schreiben fehl
        (z.B. kein Temp-Verzeichnis verfuegbar), wird pro Datei einfach der
        Originalpfad als Fallback eingetragen, damit der Hover-Sound in
        jedem Fall hoerbar bleibt (nur eben ohne Lautstaerke-Anpassung)."""
        if not cls._tap_pool:
            return
        tap_dir = cls._ensure_tap_scaled_dir()
        paths = []
        for i, src in enumerate(cls._tap_pool):
            if tap_dir:
                out_path = os.path.join(tap_dir, f"tap_0{i+1}_scaled.wav")
                if cls._write_scaled_wav(src, cls._volume, out_path):
                    paths.append(out_path)
                    continue
            paths.append(src)
        cls._tap_scaled_paths = paths
        cls._tap_scaled_volume = cls._volume

    @classmethod
    def play_hover(cls):
        """Zufaelliger Hover-Sound (tap_01.wav ... tap_05.wav), wenn die
        Maus ueber einen Button faehrt.

        WICHTIG: Laeuft NICHT ueber QSoundEffect/QMediaPlayer (Qt Multimedia),
        sondern ueber die deutlich primitivere winsound.PlaySound()-API von
        Windows. Grund: Bei Hover - wo staendig, schnell hintereinander und
        aus vielen verschiedenen Stellen im Code heraus ausgeloest wird -
        kam es ueber Qt Multimedia reproduzierbar zu Verzoegerungen und
        ausbleibenden Sounds, egal ob mit einzelnen Instanzen, einem
        geteilten Kanal oder kleineren Pools. winsound ist ein sehr duenner
        Wrapper um die klassische Windows-Audio-API (WinMM) und dafuer
        bekannt, kurze WAV-Sounds zuverlaessiger/verzoegerungsfrei
        abzuspielen als die WASAPI-basierten Qt-Multimedia-Klassen.

        Da winsound selbst keine Lautstaerke pro Aufruf kennt, wird hier
        NICHT die Originaldatei abgespielt, sondern eine bereits auf die
        aktuelle UI-Sounds-Lautstaerke herunterskalierte Kopie, die als
        temporaere WAV-Datei auf der Platte liegt (siehe
        _rebuild_tap_scaled()) - abgespielt wird sie ganz normal ueber
        denselben SND_FILENAME-Weg wie vorher, nur mit einem anderen Pfad.
        So steuert derselbe Regler wie bei den anderen Sounds jetzt auch
        die Taps, ohne den bisher bewaehrten Abspielweg zu aendern.
        """
        if cls._muted or winsound is None:
            return
        cls._ensure_tap_pool()
        if not cls._tap_pool:
            return
        if cls._tap_scaled_volume != cls._volume or len(cls._tap_scaled_paths) != len(cls._tap_pool):
            cls._rebuild_tap_scaled()
        idx = random.randrange(len(cls._tap_pool))
        play_path = cls._tap_scaled_paths[idx] if idx < len(cls._tap_scaled_paths) else cls._tap_pool[idx]
        try:
            # SND_ASYNC: blockiert die Oberflaeche nicht.
            # SND_NODEFAULT: falls die Datei nicht geladen werden kann,
            # lieber gar keinen Sound spielen statt des Windows-Standard-
            # "Ding" als Ersatz.
            winsound.PlaySound(play_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception as e:
            print(f"[Hover-Sound] Abspielen fehlgeschlagen ({play_path}): {e}")


class ClickSoundFilter(QObject):
    """App-weiter Event-Filter (nach demselben Prinzip wie GlowHoverAnimator
    weiter unten): spielt automatisch button.wav bei JEDEM Klick UND einen
    zufaelligen Hover-Sound (tap_01-05.wav) beim Drueberfahren mit der Maus
    auf einen QPushButton/QRadioButton/QToolButton irgendwo in der App ab -
    neue Buttons muessen dafuer nicht einzeln verdrahtet werden. (Toggle-
    Schalter haben ihre eigenen toggle_on/off-Sounds, siehe ToggleSwitch.)"""
    def eventFilter(self, obj, event):
        if isinstance(obj, QAbstractButton) and obj.isEnabled():
            if event.type() == QEvent.Type.MouseButtonPress:
                SoundManager.play_click()
            elif event.type() == QEvent.Type.Enter:
                SoundManager.play_hover()
        return False

def add_text_shadow(widget, blur=16, dx=0, dy=1, alpha=255):
    """Fuegt einem Text-/UI-Element (QLabel, QCheckBox, custom Widgets wie
    ConnectivityBadge/SDCardIcon, ...) einen kraeftigen, eng anliegenden
    dunklen "Halo" hinzu (kleiner Blur-Radius, fast kein Versatz, volle
    Deckkraft) - das wirkt auf unruhigen Fotohintergruenden wie ein
    Text-Outline und sorgt fuer deutlich mehr Kontrast als ein weicher,
    versetzter Streuschatten. Wird zentral von MainWindow.apply_text_shadows()
    ein- bzw. ausgeschaltet, je nachdem ob gerade ein eigenes Hintergrund-
    bild aktiv ist."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(dx, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


def add_drop_shadow(widget, blur_radius=25, offset_y=5, alpha=100, color_hex="#000000"):
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(blur_radius)
    c = QColor(color_hex)
    c.setAlpha(alpha)
    shadow.setColor(c)
    shadow.setOffset(0, offset_y)
    widget.setGraphicsEffect(shadow)


def screen_dpr(widget=None):
    """Escala de pantalla (1.0 = 100 %, 2.0 = 200 %). Con Windows escalado
    las imagenes deben generarse a resolucion real (logica x escala); si no,
    Qt las estira y se ven borrosas."""
    try:
        if widget is not None:
            return max(1.0, float(widget.devicePixelRatioF()))
        screens = QGuiApplication.screens()
        return max([1.0] + [float(sc.devicePixelRatio()) for sc in screens])
    except Exception:
        return 1.0


def hidpi_scaled_to_width(pixmap, logical_width, dpr=None):
    """Como QPixmap.scaledToWidth, pero nitido en pantallas escaladas."""
    dpr = dpr or screen_dpr()
    out = pixmap.scaledToWidth(max(1, int(round(logical_width * dpr))), Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def cover_scaled_pixmap(pixmap, target_size, dpr=1.0):
    """Skaliert ein QPixmap wie CSS 'background-size: cover': füllt die
    Zielgröße komplett aus (kein Verzerren), überschüssige Ränder werden
    mittig abgeschnitten."""
    if pixmap is None or pixmap.isNull() or target_size.width() <= 0 or target_size.height() <= 0:
        return QPixmap()
    tw, th = int(round(target_size.width() * dpr)), int(round(target_size.height() * dpr))
    scaled = pixmap.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - tw) // 2)
    y = max(0, (scaled.height() - th) // 2)
    out = scaled.copy(x, y, tw, th)
    out.setDevicePixelRatio(dpr)
    return out


def make_blurred_pixmap(pixmap, radius, max_dim=900):
    """Erzeugt EINMALIG eine weichgezeichnete Kopie von 'pixmap' (statt den
    Weichzeichner live als QGraphicsEffect auf dem sichtbaren, vollen
    Fenster-Label zu belassen).

    WICHTIG - Hintergrund des Fixes: Ein QGraphicsBlurEffect, das per
    setGraphicsEffect() direkt am (fenstergroßen!) Hintergrund-Label haengt,
    wird von Qt bei JEDEM einzelnen Repaint neu berechnet - also bei jeder
    Fenstergroessenaenderung, jedem Öffnen eines Dialogs, jeder Hover-Glow-
    Animation eines Buttons usw. Ein Weichzeichner-Durchlauf ueber ein
    komplettes (ggf. hochaufloesendes) Foto in Fenstergroesse ist auf dem
    GUI-Thread aber vergleichsweise teuer - genau DAS hat die Oberflaeche
    beim Setzen/Anzeigen eines eigenen Hintergrundbilds (v.a. mit Weich-
    zeichner > 0) immer wieder komplett einfrieren lassen.

    Der Fix: Wir berechnen den Weichzeichner nur EINMAL (wenn sich Bild
    oder Blur-Wert aendern) auf einer verkleinerten Kopie (max_dim) und
    liefern ein fertiges, bereits weichgezeichnetes QPixmap zurueck. Das
    Zuschneiden/Hochskalieren auf die aktuelle Fenstergroesse (siehe
    cover_scaled_pixmap) bleibt danach ein reines - guenstiges - Skalieren
    ohne erneuten Weichzeichner-Durchlauf."""
    if pixmap is None or pixmap.isNull():
        return QPixmap()
    if radius <= 0:
        return pixmap

    # Zuerst verkleinern: der Weichzeichner-Aufwand haengt stark von der
    # Pixelanzahl ab, und ein verwaschener Hintergrund verliert durch das
    # spaetere Wieder-Hochskalieren keine sichtbaren Details.
    small = pixmap
    if max(pixmap.width(), pixmap.height()) > max_dim:
        small = pixmap.scaled(max_dim, max_dim, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    blur = QGraphicsBlurEffect()
    blur.setBlurRadius(radius)
    item.setGraphicsEffect(blur)
    scene.addItem(item)

    result = QPixmap(small.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scene.render(painter, QRectF(0, 0, small.width(), small.height()),
                 QRectF(0, 0, small.width(), small.height()))
    painter.end()
    return result


def trim_transparent_margins(pixmap):
    """Schneidet komplett transparente Ränder rund um ein Logo/PNG ab, damit
    kein unnötiger Leerraum (der wie ein "Rand" wirken kann) übrig bleibt.
    Hat das Bild keinen Alphakanal, wird es unverändert zurückgegeben."""
    if pixmap.isNull():
        return pixmap
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    if not image.hasAlphaChannel():
        return pixmap

    w, h = image.width(), image.height()
    if w * h > 2_000_000:
        # Sehr große Bilder ueberspringen wir hier - das ist fuer ein
        # kleines Sidebar-Logo ohnehin nicht der erwartete Anwendungsfall
        # und wuerde die Pixel-fuer-Pixel-Suche unnoetig verlangsamen.
        return pixmap
    # Buscar cada borde desde afuera y PARAR en el primer pixel visible
    # (antes se recorria la imagen entera en Python: ~1,7 s con el logo).
    def opaque(x, y):
        return (image.pixel(x, y) >> 24) & 0xFF > 8   # Alpha > ~3%

    def row_has(y):
        return any(opaque(x, y) for x in range(w))

    def col_has(x, y0, y1):
        return any(opaque(x, y) for y in range(y0, y1 + 1))

    min_y = next((y for y in range(h) if row_has(y)), None)
    if min_y is None:
        return pixmap  # komplett transparentes Bild - nichts zu tun
    max_y = next(y for y in range(h - 1, -1, -1) if row_has(y))
    min_x = next(x for x in range(w) if col_has(x, min_y, max_y))
    max_x = next(x for x in range(w - 1, -1, -1) if col_has(x, min_y, max_y))

    cropped = image.copy(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
    return QPixmap.fromImage(cropped)


# --- [ ANIMATIONEN & THEME ] ---

class GlowHoverAnimator(QObject):
    def __init__(self, get_color_callback):
        super().__init__()
        self.get_color = get_color_callback

    def eventFilter(self, obj, event):
        if isinstance(obj, QPushButton) and obj.isEnabled():
            if event.type() == QEvent.Type.Enter:
                self._animate_glow(obj, True)
            elif event.type() == QEvent.Type.Leave:
                self._animate_glow(obj, False)
        return False

    def _animate_glow(self, btn, is_hover):
        effect = btn.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(btn)
            effect.setOffset(0, 0)
            btn.setGraphicsEffect(effect)
        
        anim = QPropertyAnimation(effect, b"color", btn)
        anim.setDuration(200)
        anim.setStartValue(effect.color())
        
        theme_color = QColor(self.get_color())
        theme_color.setAlpha(180)
        target_color = theme_color if is_hover else QColor(0, 0, 0, 0)
        anim.setEndValue(target_color)
        
        blur_anim = QPropertyAnimation(effect, b"blurRadius", btn)
        blur_anim.setDuration(200)
        blur_anim.setStartValue(effect.blurRadius())
        blur_anim.setEndValue(30 if is_hover else 0)

        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        blur_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        btn._glow_anim = anim 
        btn._blur_anim = blur_anim


# --- [ GOPRO STATUS & API SWITCH ] ---

def _hidden_startupinfo():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


# --- [ NATIVEN WINDOWS-KOPIERDIALOG FUER ECHTEN FORTSCHRITT AUSLESEN ] ---
#
# Hintergrund (siehe Screenshot/vorheriger Chat): Bei MTP-Kopien von der
# GoPro legt Windows die Zieldatei sofort in voller Endgroesse an und
# fuellt sie erst danach mit echten Daten. os.path.getsize() zeigt deshalb
# quasi sofort die volle Zielgroesse -> unser Balken sprang praktisch
# sofort auf ~100%, waehrend Windows' eigener Kopierdialog (der im
# Screenshot sichtbare "Kopieren..."-Dialog mit "Kopieren von ...") noch
# bei z.B. 65% stand.
#
# Loesung: Wir suchen genau dieses native Dialogfenster per WinAPI
# (EnumWindows) und lesen dessen eingebettetes Fortschrittsbalken-
# Steuerelement (Klasse "msctls_progress32") direkt per SendMessage aus
# (PBM_GETPOS / PBM_GETRANGE) - das ist exakt derselbe Wert, den der
# Dialog selbst anzeigt, also 1:1 synchron mit Windows.
#
# Damit wir dabei NICHT versehentlich einen voellig anderen, zufaellig
# offenen Dialog mit Fortschrittsbalken erwischen (z.B. ein Windows-Update
# o.ae.), pruefen wir zusaetzlich, ob irgendein Kind-Steuerelement des
# Dialogs den Dateinamen der gerade kopierten Datei im Text stehen hat
# (genau die Zeile "Kopieren von \"GX010019.MP4\"" aus dem Screenshot).

PBM_GETPOS = 0x0408
PBM_GETRANGE = 0x0407

if os.name == "nt":
    import ctypes.wintypes as _wintypes
    _user32 = ctypes.windll.user32
    _EnumWindowsProc = ctypes.WINFUNCTYPE(_wintypes.BOOL, _wintypes.HWND, _wintypes.LPARAM)
    _EnumChildProc = ctypes.WINFUNCTYPE(_wintypes.BOOL, _wintypes.HWND, _wintypes.LPARAM)
else:
    _user32 = None


def _get_window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_window_text(hwnd):
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _find_copy_dialog_hwnd():
    """Sucht das sichtbare, oberste native Windows-Kopierdialogfenster
    (egal ob klassischer "#32770"-Dialog oder der modernere Explorer-
    Operationsdialog "OperationStatusWindow") und gibt dessen HWND zurueck
    - oder None, falls (noch) keins offen ist."""
    if _user32 is None:
        return None

    candidate_classes = ("#32770", "OperationStatusWindow")
    found = {"hwnd": None}

    def _enum_top(hwnd, lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            if _get_window_class(hwnd) not in candidate_classes:
                return True
            found["hwnd"] = hwnd
            return False
        except Exception:
            return True

    try:
        _user32.EnumWindows(_EnumWindowsProc(_enum_top), 0)
    except Exception:
        return None

    return found["hwnd"]


def _find_progressbar_child(dialog_hwnd):
    """Sucht innerhalb eines bereits bekannten Dialogfensters das
    eingebettete klassische Fortschrittsbalken-Steuerelement (Klasse
    "msctls_progress32"). Gibt None zurueck, falls keins gefunden wird
    (z.B. weil der Dialog den Balken rein per DirectUI/UI Automation
    ohne eigenes Win32-Steuerelement rendert - siehe _read_progress_via_uia
    als Ausweichmoeglichkeit dafuer)."""
    if _user32 is None or dialog_hwnd is None:
        return None

    found = {"progressbar": None}

    def _enum_child(hwnd, lparam):
        if _get_window_class(hwnd) == "msctls_progress32":
            found["progressbar"] = hwnd
            return False
        return True

    try:
        _user32.EnumChildWindows(dialog_hwnd, _EnumChildProc(_enum_child), 0)
    except Exception:
        return None

    return found["progressbar"]


def _read_progressbar_percent(hwnd):
    """Liest den aktuellen Stand (0.0-100.0) des gefundenen nativen
    Fortschrittsbalkens aus. Gibt None zurueck, falls das Fenster
    zwischenzeitlich verschwunden ist (Kopiervorgang fertig/Dialog zu)."""
    try:
        if not _user32.IsWindow(hwnd):
            return None
        pos = _user32.SendMessageW(hwnd, PBM_GETPOS, 0, 0)
        lo = _user32.SendMessageW(hwnd, PBM_GETRANGE, 1, 0)
        hi = _user32.SendMessageW(hwnd, PBM_GETRANGE, 0, 0)
        if hi <= lo:
            return None
        pct = ((pos - lo) / (hi - lo)) * 100.0
        return max(0.0, min(100.0, pct))
    except Exception:
        return None


# --- Zusaetzliche Absicherung per UI Automation (optional) ----------------
#
# Falls der Kopierdialog auf einem System den Fortschrittsbalken NICHT als
# klassisches Win32-Steuerelement (msctls_progress32) rendert, sondern rein
# ueber DirectUI/UI Automation (kein SendMessage moeglich), greift die obige
# Methode ins Leere. Als zweite, robustere Ebene wird deshalb - NUR falls
# das optionale Paket "pywinauto" installiert ist (pip install pywinauto) -
# zusaetzlich per UI Automation nach einem ProgressBar-Element in genau
# diesem Dialogfenster gesucht. Ist pywinauto nicht installiert, wird dieser
# Weg einfach uebersprungen (kein Fehler, keine Pflicht-Abhaengigkeit).
try:
    from pywinauto.uia_element_info import UIAElementInfo as _UIAElementInfo
    from pywinauto.controls.uia_wrapper import UIAWrapper as _UIAWrapper
    _HAS_PYWINAUTO = True
except Exception:
    _HAS_PYWINAUTO = False


def _read_progress_via_uia(dialog_hwnd):
    """Sucht innerhalb des Dialogfensters "dialog_hwnd" per UI Automation
    ein ProgressBar-Element und liest dessen aktuellen Wert (0.0-100.0)
    aus. Gibt None zurueck, wenn pywinauto fehlt, kein passendes Element
    gefunden wurde, oder das Fenster nicht mehr existiert."""
    if not _HAS_PYWINAUTO or _user32 is None:
        return None
    try:
        if not _user32.IsWindow(dialog_hwnd):
            return None
        root = _UIAWrapper(_UIAElementInfo(dialog_hwnd))
        for desc in root.descendants(control_type="ProgressBar"):
            try:
                rv = desc.iface_range_value
                lo = rv.CurrentMinimum
                hi = rv.CurrentMaximum
                val = rv.CurrentValue
                if hi > lo:
                    return max(0.0, min(100.0, ((val - lo) / (hi - lo)) * 100.0))
            except Exception:
                continue
    except Exception:
        return None
    return None


# Auf Wunsch: den nativen Windows-Kopierdialog verstecken, waehrend unser
# eigener Fortschrittsbalken (der ja denselben Wert anzeigt) laeuft. Das
# Auslesen per SendMessage/UI Automation funktioniert unabhaengig davon,
# ob das Fenster sichtbar ist - ein Fensterhandle bleibt technisch
# ansprechbar, auch wenn es per SW_HIDE ausgeblendet wurde. Ueber diesen
# Schalter laesst sich das Verhalten zentral ein-/ausschalten, falls es auf
# einem System doch mal Probleme machen sollte (inoffizielles Verhalten,
# von Microsoft nicht dokumentiert/unterstuetzt).
HIDE_NATIVE_COPY_DIALOG = True
SW_HIDE = 0


def _hide_native_copy_dialog(hwnd):
    """Blendet das native Kopierdialogfenster aus. Wird bei JEDEM Poll-Tick
    erneut aufgerufen (nicht nur einmal), weil manche Windows-Versionen den
    Dialog von sich aus zwischendurch wieder kurz in den Vordergrund holen
    (z.B. beim Aktualisieren der "verbleibende Zeit"-Anzeige) - ein einmaliges
    Verstecken wuerde dann nicht zuverlaessig halten."""
    if _user32 is None or hwnd is None:
        return
    try:
        if _user32.IsWindow(hwnd) and _user32.IsWindowVisible(hwnd):
            _user32.ShowWindow(hwnd, SW_HIDE)
    except Exception:
        pass


class _DeviceNameProbe:
    """Un unico proceso PowerShell que queda abierto y devuelve los nombres
    de "Este equipo" cada vez que se le pide. Antes se lanzaba un PowerShell
    nuevo cada 0,8 s (cientos de ms de CPU y un proceso por vuelta mientras
    la app estuviera abierta). Si el proceso se cuelga (MTP bloqueado) o
    muere, se reinicia solo."""
    _SCRIPT = r'''
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
$ErrorActionPreference = "SilentlyContinue"
$shell = New-Object -ComObject Shell.Application
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($line -eq $null -or $line -eq "quit") { break }
    foreach ($it in $shell.Namespace(17).Items()) { [Console]::Out.WriteLine($it.Name) }
    [Console]::Out.WriteLine("<<END>>")
    [Console]::Out.Flush()
}
'''

    def __init__(self):
        self._proc = None
        self._lines = None
        self._lock = threading.Lock()

    def _start(self):
        self._proc = subprocess.Popen(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', self._SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            startupinfo=_hidden_startupinfo()
        )
        self._lines = queue.Queue()
        proc, lines = self._proc, self._lines

        def reader():
            try:
                for ln in proc.stdout:
                    lines.put(ln.rstrip("\r\n"))
            except Exception:
                pass
            lines.put(None)
        threading.Thread(target=reader, daemon=True).start()

    def _kill(self):
        try:
            if self._proc:
                self._proc.kill()
        except Exception:
            pass
        self._proc = None

    def names(self, timeout=10.0):
        with self._lock:
            try:
                if self._proc is None or self._proc.poll() is not None:
                    self._start()
                self._proc.stdin.write("list\n")
                self._proc.stdin.flush()
                out = []
                deadline = time.monotonic() + timeout
                while True:
                    ln = self._lines.get(timeout=max(0.1, deadline - time.monotonic()))
                    if ln is None:
                        raise RuntimeError("probe terminated")
                    if ln == "<<END>>":
                        return "\n".join(out)
                    out.append(ln)
            except Exception:
                self._kill()
                return ""

    def close(self):
        with self._lock:
            try:
                if self._proc and self._proc.poll() is None:
                    self._proc.stdin.write("quit\n")
                    self._proc.stdin.flush()
            except Exception:
                pass
            self._kill()


_DEVICE_PROBE = _DeviceNameProbe()
import atexit
atexit.register(_DEVICE_PROBE.close)


def detect_connected_gopro_model():
    """Prueft, welche der unterstuetzten GoPro-Modelle (siehe GOPRO_MODELS)
    gerade per MTP am PC haengt. Gibt den Modell-Key (z.B. "HERO11") zurueck
    oder None, falls keine unterstuetzte GoPro gefunden wurde."""
    try:
        names = _DEVICE_PROBE.names()
        for line in names.splitlines():
            if any(model in line for model in GOPRO_MODEL_ORDER):
                LAST_DEVICE_NAME["name"] = line.strip()
                break
        for model in GOPRO_MODEL_ORDER:
            if model in names:
                return model
    except Exception:
        pass
    return None

_GOPRO_DCIM_SUBDIR_RE = re.compile(r"^\d{3}GOPRO$", re.IGNORECASE)


def _model_from_version_txt(root):
    """Las GoPro escriben MISC\\version.txt en la tarjeta, con una linea tipo
    "camera type":"HERO5 Black". Devuelve la clave de GOPRO_MODELS o None."""
    try:
        with open(os.path.join(root, "MISC", "version.txt"), "r", encoding="utf-8", errors="replace") as f:
            text = f.read(4096)
    except Exception:
        return None
    m = re.search(r'"camera type"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
    if not m:
        return None
    LAST_DEVICE_NAME["name"] = m.group(1).strip()
    cam = m.group(1).upper()
    for model in GOPRO_MODEL_ORDER:
        if model in cam:
            return model
    return None


def _find_gopro_card_root(folder):
    """Si 'folder' esta dentro de una tarjeta GoPro (DCIM, 100GOPRO...),
    devuelve la raiz de la tarjeta (donde esta MISC\\version.txt)."""
    cur = os.path.abspath(folder)
    for _ in range(4):
        if os.path.isfile(os.path.join(cur, "MISC", "version.txt")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def find_gopro_sd_card():
    """Busca una tarjeta de GoPro montada como unidad normal (lector de
    tarjetas, o camara en modo almacenamiento masivo): una unidad con
    DCIM\\100GOPRO, 101GOPRO, ... Devuelve (raiz, clave_modelo) o (None, None).

    Solo mira unidades extraibles/fijas que NO sean la del sistema, y
    suprime el dialogo "No hay disco en la unidad" de los lectores vacios."""
    if os.name != "nt":
        return None, None
    try:
        kernel32 = ctypes.windll.kernel32
        SEM_FAILCRITICALERRORS = 0x0001
        old_mode = kernel32.SetErrorMode(SEM_FAILCRITICALERRORS)
    except Exception:
        kernel32, old_mode = None, None
    try:
        system_drive = (os.getenv("SystemDrive") or "C:").upper()
        bitmask = kernel32.GetLogicalDrives() if kernel32 else 0
        for i in range(26):
            if not (bitmask >> i) & 1:
                continue
            letter = chr(ord("A") + i)
            if letter in ("A", "B") or f"{letter}:" == system_drive:
                continue
            root = f"{letter}:\\"
            try:
                drive_type = kernel32.GetDriveTypeW(root)
            except Exception:
                drive_type = 0
            # Solo unidades EXTRAIBLES (lector de tarjetas): una carpeta DCIM
            # copiada en un disco fijo (p. ej. D:\\DCIM) no es una tarjeta.
            if drive_type != 2:
                continue
            dcim = os.path.join(root, "DCIM")
            try:
                if not os.path.isdir(dcim):
                    continue
                if not any(_GOPRO_DCIM_SUBDIR_RE.match(e) and os.path.isdir(os.path.join(dcim, e))
                           for e in os.listdir(dcim)):
                    continue
            except OSError:
                continue
            return root, (_model_from_version_txt(root) or "SD")
    finally:
        if kernel32 is not None and old_mode is not None:
            try:
                kernel32.SetErrorMode(old_mode)
            except Exception:
                pass
    return None, None


def detect_connected_source():
    """La seccion Camara es SOLO para la GoPro conectada por USB (MTP). Las
    tarjetas SD se importan desde "Importar SD / carpeta".
    Devuelve (clave_modelo o None, None)."""
    return detect_connected_gopro_model(), None


def check_gopro_connected():
    """Rueckwaerts-kompatibler bool-Check: True sobald irgendeine
    unterstuetzte GoPro erkannt wird (siehe detect_connected_gopro_model)."""
    return detect_connected_gopro_model() is not None

def parse_battery_percent(raw):
    if not raw: return None
    match = re.search(r'(\d{1,3})\s*%?', str(raw))
    if match: return max(0, min(100, int(match.group(1))))
    return None

def parse_size_to_bytes(raw):
    if not raw: return None
    match = re.match(r'^\s*([\d.,]+)\s*([A-Za-zµ]+)?', str(raw))
    if not match: return None
    num_str, unit = match.group(1), (match.group(2) or "").lower()
    if ',' in num_str and '.' in num_str: num_str = num_str.replace('.', '').replace(',', '.')
    elif ',' in num_str: num_str = num_str.replace(',', '.')
    try: num = float(num_str)
    except Exception: return None
    if unit.startswith('t'): mult = 1024 ** 4
    elif unit.startswith('g'): mult = 1024 ** 3
    elif unit.startswith('m'): mult = 1024 ** 2
    elif unit.startswith('k'): mult = 1024
    else: mult = 1
    return num * mult

_LAST_API_ATTEMPT = 0.0


def attempt_api_battery_and_force_mtp():
    import urllib.request
    """Solo para HERO9 y posteriores conectadas en modo "GoPro Connect": las
    pasa a MTP via Open GoPro. Antes se intentaba en CADA vuelta del bucle
    contra una IP fija (172.29.171.51, la de una camara concreta), lo que con
    la camara desconectada bloqueaba el bucle hasta 5 s cada vez. Ahora: como
    mucho cada 15 s y solo si existe la red USB de una GoPro (172.2x.x.x),
    usando la IP de la camara que corresponde a esa red."""
    global GLOBAL_CACHED_BATTERY, _LAST_API_ATTEMPT
    if time.monotonic() - _LAST_API_ATTEMPT < 15:
        return
    _LAST_API_ATTEMPT = time.monotonic()
    candidates = _get_usb_subnet_candidates()
    if not candidates:
        return
    parts = candidates[0].split(".")
    try:
        base_url = f"http://{parts[0]}.{parts[1]}.{parts[2]}.51:8080"
        # Timeout erhöht auf 3.0 Sekunden
        req_state = urllib.request.Request(f"{base_url}/gopro/camera/state", method="GET")
        with urllib.request.urlopen(req_state, timeout=3.0) as response:
            data = json.loads(response.read().decode('utf-8'))
            bat = data.get('status', {}).get('70')
            if bat is not None:
                GLOBAL_CACHED_BATTERY = int(bat)
                
        req_mtp = urllib.request.Request(f"{base_url}/gopro/camera/setting?setting=112&option=1", method="GET")
        urllib.request.urlopen(req_mtp, timeout=2.0)
    except Exception:
        pass

# --- [ ADDED FROM Code 1: BLE + OPEN GOPRO + DIAG LOG ] ---
BLE_BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"  # Standard-BLE "Battery Level"
# En %APPDATA% y no junto al programa: instalado en "Archivos de programa" no se puede escribir ahi.
_DIAG_LOG_PATH = os.path.join(CONFIG_DIR, "gopro_akku_diagnose.txt")

async def _ble_read_battery_async(name_match="HERO11", timeout=6.0):
    device = await BleakScanner.find_device_by_filter(
        lambda d, adv: d.name and name_match.lower() in d.name.lower(),
        timeout=timeout,
    )
    if device is None:
        # Manche Kameras werben unter "GoPro XXXX" statt dem Modellnamen -
        # als zweiten Versuch danach suchen.
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: d.name and "gopro" in d.name.lower(),
            timeout=timeout,
        )
    if device is None:
        return None
    async with BleakClient(device, timeout=8.0) as client:
        value = await client.read_gatt_char(BLE_BATTERY_LEVEL_UUID)
        if value:
            return int(value[0])
    return None

_WPD = None


def _wpd_modules():
    """Carga (una vez) las interfaces de Windows Portable Devices via comtypes."""
    global _WPD
    if _WPD is None:
        import comtypes.client
        comtypes.client.GetModule("portabledeviceapi.dll")
        comtypes.client.GetModule("portabledevicetypes.dll")
        from comtypes.gen import PortableDeviceApiLib, PortableDeviceTypesLib
        _WPD = (PortableDeviceApiLib, PortableDeviceTypesLib)
    return _WPD


def read_battery_via_wpd(name_match):
    """Nivel de bateria via MTP estandar (WPD_DEVICE_POWER_LEVEL), como lo ve
    Windows. Funciona con la HERO5 (que no lo da por la Shell ni por
    Bluetooth) y es instantaneo. Devuelve 0-100 o None."""
    if os.name != "nt" or not name_match:
        return None
    try:
        import comtypes
        import comtypes.client
        from comtypes import GUID
        from ctypes import POINTER, byref, c_ulong, cast
    except Exception:
        return None
    comtypes.CoInitialize()   # este codigo corre en un hilo de fondo
    try:
        api, types = _wpd_modules()
        props_guid = GUID("{26D4979A-E643-4626-9E2B-736DC0C92FDC}")   # WPD_DEVICE_PROPERTIES_V1

        def pkey(pid):
            k = api._tagpropertykey()
            k.fmtid = props_guid
            k.pid = pid
            return k

        mgr = comtypes.client.CreateObject(GUID("{0af10cec-2ecd-4b92-9581-34f6ae0637f3}"),
                                           interface=api.IPortableDeviceManager)
        mgr.RefreshDeviceList()
        cnt = c_ulong(0)
        mgr._IPortableDeviceManager__com_GetDevices(None, byref(cnt))
        arr = (comtypes.c_wchar_p * cnt.value)()
        mgr._IPortableDeviceManager__com_GetDevices(cast(arr, POINTER(comtypes.c_wchar_p)), byref(cnt))
        for dev_id in list(arr):
            dev = comtypes.client.CreateObject(GUID("{728a21c5-3d9e-48d7-9810-864848f0f404}"),
                                               interface=api.IPortableDevice)
            client = comtypes.client.CreateObject(GUID("{0c15d503-d017-47ce-9016-7b3f978721cc}"),
                                                  interface=types.IPortableDeviceValues)
            try:
                dev.Open(dev_id, client)
                vals = dev.Content().Properties().GetValues("DEVICE", None)
                name = vals.GetStringValue(byref(pkey(12)))          # WPD_DEVICE_FRIENDLY_NAME
                if name_match.lower() in (name or "").lower():
                    level = vals.GetUnsignedIntegerValue(byref(pkey(4)))   # WPD_DEVICE_POWER_LEVEL
                    return max(0, min(100, int(level)))
            except Exception:
                pass
            finally:
                try:
                    dev.Close()
                except Exception:
                    pass
    except Exception as e:
        print("[WPD] no se pudo leer la bateria:", e)
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass
    return None


def read_battery_via_ble(model=None):
    import asyncio
    """Liest den Akkustand per Bluetooth LE (siehe Erklärung oben). Läuft
    synchron in einem eigenen kleinen Event-Loop - wird ausschließlich im
    DeviceMonitorThread (Hintergrund-Thread) aufgerufen, blockiert also nie
    die Oberfläche.

    BUGFIX: Vorher wurde hier IMMER der Default "HERO11" an
    _ble_read_battery_async() durchgereicht, unabhängig davon, welches
    Modell tatsächlich per MTP erkannt wurde. Für eine angeschlossene
    Hero 9 bedeutete das: der erste (gezielte) Scan-Versuch suchte nach
    einem Geraet mit "HERO11" im Namen, lief garantiert ins Leere, und erst
    der generische Fallback-Scan (irgendein Geraet mit "gopro" im Namen)
    konnte ueberhaupt etwas finden - das kostet unnoetig Zeit (bis zu
    ~12 statt ~6 Sekunden) und macht den Ansatz unnoetig fehleranfaelliger.
    Jetzt wird das tatsaechlich erkannte Modell (z.B. "HERO9") als
    name_match uebergeben."""
    if not _load_bleak():
        return None
    try:
        return asyncio.run(_ble_read_battery_async(name_match=model or "HERO11"))
    except Exception:
        return None

def _get_usb_subnet_candidates():
    candidates = []
    try:
        result = subprocess.run(['ipconfig'], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 startupinfo=_hidden_startupinfo(), timeout=5)
        for m in re.finditer(r'IPv4[^:]*:\s*([\d.]+)', result.stdout or ""):
            ip = m.group(1)
            if re.match(r'^172\.2\d\.', ip):
                candidates.append(ip)
    except Exception:
        pass
    return candidates

def read_battery_via_open_gopro(timeout=1.2):
    import urllib.request
    for local_ip in _get_usb_subnet_candidates():
        parts = local_ip.split(".")
        if len(parts) != 4:
            continue
        camera_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.51"
        base = f"http://{camera_ip}:8080"
        try:
            with urllib.request.urlopen(f"{base}/gopro/camera/state", timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pct = data.get("status", {}).get("70")
            if pct is not None:
                return max(0, min(100, int(pct)))
        except Exception:
            continue
    return None

def _fmt_gb(num_bytes):
    return f"{num_bytes / (1024 ** 3):.1f} GB"


def read_gopro_status(write_diag=True, model=None, sd_root=None):
    # Tarjeta SD en lector: el espacio se lee directo del sistema de archivos
    # y no hay bateria que consultar.
    if sd_root:
        try:
            usage = shutil.disk_usage(sd_root)
            pct = int(round((usage.used / usage.total) * 100)) if usage.total else None
            return {"battery": None, "storage_pct": pct,
                    "capacity_str": _fmt_gb(usage.total), "free_str": _fmt_gb(usage.free)}
        except Exception:
            return {"battery": None, "storage_pct": None, "capacity_str": None, "free_str": None}
    return _read_gopro_status_mtp(write_diag=write_diag, model=model)


def _read_gopro_status_mtp(write_diag=True, model=None):
    """Liest Akkustand und Speicherbelegung der GoPro direkt vom Gerät.

    Reihenfolge:
    1. Bluetooth LE (read_battery_via_ble) - funktioniert bei aktivem
       Bluetooth IMMER, unabhängig vom USB-Modus, OHNE die MTP-
       Dateiübertragung zu stören. Das ist der bevorzugte Weg.
    2. Open GoPro HTTP über USB (nur falls Kamera zufällig im
       Wired-Control-Modus steht).
    3. Windows-Explorer-Detailspalten per MTP (alter Fallback, siehe unten).

    Die Speicherbelegung kommt weiterhin über die MTP-Spalten, da das
    zuverlässig funktioniert und mit dem normalen Dateizugriffsmodus
    kompatibel ist.
    """
    battery_pct = None
    storage_pct = None
    capacity_str = None
    free_str = None

    # La HERO5 no expone el servicio BLE estandar de bateria ni la API
    # Open GoPro por USB: probar ambos solo costaria ~12 s por consulta.
    # Para ella se usan directamente las columnas MTP (paso 3).
    legacy_model = model == "HERO5"

    # 0) MTP estandar via WPD (instantaneo; la HERO5 solo la informa por aqui)
    try:
        battery_pct = read_battery_via_wpd(model)
    except Exception:
        battery_pct = None

    # 1) Bluetooth LE (bevorzugt - kein Moduswechsel nötig)
    if battery_pct is None:
        try:
            battery_pct = None if legacy_model else read_battery_via_ble(model=model)
        except Exception:
            battery_pct = None

    # 2) Open GoPro HTTP über USB (nur falls Kamera zufällig im Wired-Control-Modus ist)
    if battery_pct is None and not legacy_model:
        try:
            battery_pct = read_battery_via_open_gopro()
        except Exception:
            pass

    try:
        ps_script = r'''
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
$ErrorActionPreference = "SilentlyContinue"
$s = New-Object -ComObject Shell.Application
$ns17 = $s.Namespace(17)
if (!$ns17) { Write-Output "{}"; exit }
$gopro = $ns17.Items() | Where-Object {$_.Name -match "__GOPRO_PATTERN__"} | Select-Object -First 1
if (!$gopro) { Write-Output "{}"; exit }

function Get-DetailValue($folder, $item, [string[]]$keywords) {
    for ($i = 0; $i -le 320; $i++) {
        $header = $folder.GetDetailsOf($folder.Items, $i)
        if ([string]::IsNullOrWhiteSpace($header)) { continue }
        $h = $header.ToLower()
        foreach ($k in $keywords) {
            if ($h.Contains($k)) {
                $val = $folder.GetDetailsOf($item, $i)
                if (![string]::IsNullOrWhiteSpace($val)) { return $val }
            }
        }
    }
    return $null
}

$battery = $null
$propCandidates = @(
    "System.DeviceCapabilities.DeviceBatteryLevel",
    "System.Battery.ChargeLevel",
    "System.Battery.PercentRemaining",
    "System.PercentFull"
)
foreach ($p in $propCandidates) {
    try {
        $v = $gopro.ExtendedProperty($p)
        if ($v -ne $null -and "$v" -ne "") { $battery = "$v"; break }
    } catch {}
}
if (!$battery) {
    $battery = Get-DetailValue $ns17 $gopro @("akku", "battery", "batterie", "ladezustand", "charge", "batería", "bateria", "carga")
}

$capacity = $null
$free = $null
$vol = $gopro.GetFolder.Items() | Select-Object -First 1
if ($vol) {
    $goproFolder = $gopro.GetFolder
    $capacity = Get-DetailValue $goproFolder $vol @("gesamtgroesse", "gesamtgröße", "total size", "kapazitaet", "kapazität", "capacity", "tamaño total", "capacidad")
    $free = Get-DetailValue $goproFolder $vol @("frei", "free space", "verfuegbar", "verfügbar", "espacio libre", "espacio disponible")
}

$diag = @()
if (!$battery) {
    for ($i = 0; $i -le 320; $i++) {
        $header = $ns17.GetDetailsOf($ns17.Items, $i)
        if ([string]::IsNullOrWhiteSpace($header)) { continue }
        $val = $ns17.GetDetailsOf($gopro, $i)
        if (![string]::IsNullOrWhiteSpace($val)) { $diag += "$header=$val" }
    }
}

$result = [PSCustomObject]@{ battery = $battery; capacity = $capacity; free = $free; diag = ($diag -join "; ") }
Write-Output ($result | ConvertTo-Json -Compress)
'''
        ps_script = ps_script.replace("__GOPRO_PATTERN__", GOPRO_MODEL_MATCH_PATTERN)
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            startupinfo=_hidden_startupinfo(), timeout=20
        )
        raw = (result.stdout or "").strip()
        data = json.loads(raw) if raw else {}

        # 3) MTP-Spalten - nur als letzter Fallback für den Akku verwenden
        if battery_pct is None:
            battery_pct = parse_battery_percent(data.get("battery"))

        capacity_str = data.get("capacity")
        free_str = data.get("free")

        if battery_pct is None and write_diag:
            diag = data.get("diag")
            try:
                with open(_DIAG_LOG_PATH, "w", encoding="utf-8") as f:
                    if diag:
                        f.write("Akku-Wert nicht gefunden (weder per Bluetooth LE, Open-GoPro-HTTP "
                                "noch MTP-Spalten). Vom Gerät per MTP gemeldete Spalten:\n\n")
                        f.write(diag.replace("; ", "\n"))
                    else:
                        f.write("Akku-Wert nicht gefunden.\n\n"
                                "- Bluetooth LE: pruefen, ob 'bleak' installiert ist (pip install bleak), "
                                "Bluetooth am PC aktiv ist und die Kamera in Reichweite/gekoppelt ist.\n"
                                "- Open GoPro HTTP: nur relevant, wenn die Kamera auf 'GoPro-Verbindung' "
                                "statt 'MTP' steht.\n"
                                "- MTP-Spalten: liefern auf manchen Firmware-Staenden schlicht keinen Wert.")
            except Exception:
                pass

        cap_bytes = parse_size_to_bytes(capacity_str)
        free_bytes = parse_size_to_bytes(free_str)
        if cap_bytes and free_bytes is not None and cap_bytes > 0:
            used_bytes = max(0, cap_bytes - free_bytes)
            storage_pct = int(round((used_bytes / cap_bytes) * 100))

    except Exception as e:
        print("Status-Lesefehler:", e)

    return {
        "battery": battery_pct,
        "storage_pct": storage_pct,
        "capacity_str": capacity_str,
        "free_str": free_str,
    }

# --- [ DeviceMonitorThread remains using attempt_api_battery_and_force_mtp if desired ] ---

class DeviceMonitorThread(QThread):
    # connected (bool), Modell-Key wie "HERO11"/"HERO9" (leerer String, wenn
    # nicht verbunden) - siehe GOPRO_MODELS weiter oben.
    connection_changed = Signal(bool, str)
    status_ready = Signal(dict)
    card_detected = Signal(str)   # tarjeta GoPro insertada en un lector (raiz)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._connected = False
        self._connected_model = (None, None)

    def stop(self): self._running = False

    def run(self):
        cycle = 0
        while self._running:
            if not self._connected:
                attempt_api_battery_and_force_mtp()

            model, sd_root = detect_connected_source()
            connected = model is not None
            # Aviso de tarjeta GoPro insertada (cada ~4 s, solo al aparecer)
            if cycle % 5 == 0:
                card_root, _card_model = find_gopro_sd_card()
                if card_root and card_root != getattr(self, "_last_card", None):
                    self.card_detected.emit(card_root)
                self._last_card = card_root
            source_key = (model, sd_root)
            if source_key != self._connected_model:
                self._connected_model = source_key
                self._connected = connected
                ACTIVE_SOURCE["model"] = model
                ACTIVE_SOURCE["sd_root"] = sd_root
                self.connection_changed.emit(connected, model or "")
                if connected:
                    if self._running: self.status_ready.emit(read_gopro_status(model=model, sd_root=sd_root))
                    cycle = 0
            elif connected and not SYNC_IN_PROGRESS.is_set() and not CAMERA_BROWSING.is_set():
                # Antes cada ~4 s: un PowerShell que recorre cientos de
                # columnas MTP. El espacio libre no cambia tan rapido, y
                # durante una sincronizacion solo competiria con la copia.
                cycle += 1
                if cycle >= 38:
                    cycle = 0
                    if self._running: self.status_ready.emit(read_gopro_status(model=model, sd_root=sd_root))
            # War vorher 20x100ms = 2 Sekunden zwischen den Pruefungen - das
            # Erkennen eines Trennens der GoPro konnte sich dadurch spuerbar
            # verzoegern. Jetzt 0.8 Sekunden, damit ein Verbindungsabbruch
            # deutlich schneller bemerkt wird, ohne PowerShell im Sekunden-
            # takt am Laufen zu halten.
            for _ in range(8):
                if not self._running: break
                self.msleep(100)


# --- [ SYNC: DATEIEN VON DER GOPRO AUF DEN PC KOPIEREN ] ---
#
# Die GoPro haengt per MTP (Media Transfer Protocol) am PC und taucht deshalb
# NICHT als normales Laufwerk mit Buchstaben auf. Ein einfaches os.walk /
# shutil.copy funktioniert daher nicht. Stattdessen wird - genau wie beim
# Auslesen von Akku/Speicher weiter oben - die Windows-Shell (Shell.Application
# COM-Objekt) benutzt, um die Dateien auf dem Geraet zu finden und per
# CopyHere() in den Zielordner zu kopieren.

SYNC_MEDIA_EXTENSIONS = [".mp4", ".jpg", ".jpeg", ".png", ".gpr"]

# Welche Dateiendungen fuer welchen Sync-Modus (Dropdown am Sync-Button)
# beruecksichtigt werden.
MEDIA_EXTENSIONS_BY_MODE = {
    "all": [".mp4", ".jpg", ".jpeg", ".png", ".gpr"],     # .gpr = foto RAW (HERO5 Black y posteriores)
    "pictures": [".jpg", ".jpeg", ".png", ".gpr"],
    "videos": [".mp4"],
}

def _ps_str(s):
    """Erzeugt ein sicheres, single-quoted PowerShell-Stringliteral (keine
    Variablen-Interpolation, nur ' muss verdoppelt werden)."""
    return "'" + str(s).replace("'", "''") + "'"


def _ps_str_array(items):
    return "@(" + ", ".join(_ps_str(i) for i in items) + ")"


# --- PHASE 1: Nur auflisten (schnell, KEIN CopyHere) -----------------------
#
# Frueher wurde in EINEM einzigen, lange laufenden PowerShell-Prozess sowohl
# der komplette Ordnerbaum der GoPro durchsucht ALS AUCH direkt in derselben
# Schleife jede Datei per CopyHere() kopiert. Bei vielen Dateien (bzw. wenn
# die MTP-Verbindung kurz ins Stocken kommt) blieb dieser eine Prozess dann
# haengen - ohne Fehlermeldung, weil `$ErrorActionPreference = "SilentlyContinue"`
# alles verschluckt. Das sah fuer den Nutzer so aus, als wuerde "der Sync
# nicht mehr funktionieren".
#
# Jetzt (nach Vorschlag von Gemini, hier sauber zu Ende gebaut): PowerShell
# wird nur noch als "dummer Ausfuehrer" benutzt:
#   1) EIN kurzer Befehl listet nur Namen/Groessen/Ordnerpfade auf (billig).
#   2) Python vergleicht das komplett selbst mit dem Zielordner (Duplikat-
#      Check), OHNE nochmal PowerShell/COM anzufassen.
#   3) Fuer jede tatsaechlich fehlende Datei startet Python einen neuen,
#      winzigen, isolierten PowerShell-Prozess, der NUR diese eine Datei
#      per CopyHere() ueberträgt. Haengt sich einer dieser Mini-Prozesse
#      auf, betrifft das nur diese eine Datei (Timeout) statt den ganzen Sync.
#
# WICHTIG: Das Akku/Speicher-Auslesen (read_gopro_status, DeviceMonitorThread,
# attempt_api_battery_and_force_mtp) wird hier bewusst NICHT angefasst -
# genau das war bei Geminis eigenem Versuch kaputtgegangen.

def build_list_script(extensions=None):
    ext_list_literal = _ps_str_array(extensions or SYNC_MEDIA_EXTENSIONS)
    script = r'''
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
$ErrorActionPreference = "SilentlyContinue"
$exts = ''' + ext_list_literal + r'''

$shell = New-Object -ComObject Shell.Application
$ns17 = $shell.Namespace(17)
if (!$ns17) { Write-Output 'RESULT:{"error":"no_namespace"}'; exit }
$gopro = $ns17.Items() | Where-Object {$_.Name -match "__GOPRO_PATTERN__"} | Select-Object -First 1
if (!$gopro) { Write-Output 'RESULT:{"error":"not_found"}'; exit }

$results = New-Object System.Collections.ArrayList
$queue = New-Object System.Collections.ArrayList
[void]$queue.Add(@{ folder = $gopro.GetFolder; path = @() })

while ($queue.Count -gt 0) {
    $entry = $queue[0]
    $queue.RemoveAt(0)
    $currentFolder = $entry.folder
    $currentPath = $entry.path

    foreach ($item in $currentFolder.Items()) {
        $subFolder = $null
        try { $subFolder = $item.GetFolder } catch { $subFolder = $null }

        if ($subFolder -ne $null) {
            $newPath = @($currentPath) + $item.Name
            [void]$queue.Add(@{ folder = $subFolder; path = $newPath })
        } else {
            $lname = $item.Name.ToLower()
            $matched = $false
            foreach ($ext in $exts) {
                if ($lname.EndsWith($ext)) { $matched = $true; break }
            }
            if ($matched) {
                $size = 0
                try { $size = [int64]$item.ExtendedProperty("System.Size") } catch {}
                $dt = ""
                try { $d = $item.ExtendedProperty("System.DateCreated"); if ($d) { $dt = ([datetime]$d).ToString("yyyy-MM-ddTHH:mm:ss") } } catch {}
                [void]$results.Add([PSCustomObject]@{ name = $item.Name; size = $size; path = ($currentPath -join "|"); dt = $dt })
            }
        }
    }
}

Write-Output ("RESULT:" + ($results | ConvertTo-Json -Compress -Depth 6))
'''
    return script.replace("__GOPRO_PATTERN__", GOPRO_MODEL_MATCH_PATTERN)


# --- PHASE 3: Genau eine Datei isoliert kopieren ----------------------------

def build_single_copy_script(path_components, file_name, target_dir, dest_name):
    target_dir_escaped = os.path.normpath(target_dir).replace('"', '""')
    path_array_literal = _ps_str_array(path_components)

    return f'''
try {{ [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) }} catch {{}}
$ErrorActionPreference = "SilentlyContinue"
$targetDir = "{target_dir_escaped}"
$destName = {_ps_str(dest_name)}
$fileName = {_ps_str(file_name)}
$pathParts = {path_array_literal}

$shell = New-Object -ComObject Shell.Application
$ns17 = $shell.Namespace(17)
if (!$ns17) {{ Write-Output 'RESULT:{{"error":"no_namespace"}}'; exit }}
$gopro = $ns17.Items() | Where-Object {{$_.Name -match "{GOPRO_MODEL_MATCH_PATTERN}"}} | Select-Object -First 1
if (!$gopro) {{ Write-Output 'RESULT:{{"error":"not_found"}}'; exit }}

$currentFolder = $gopro.GetFolder
foreach ($part in $pathParts) {{
    $next = $null
    foreach ($it in $currentFolder.Items()) {{
        if ($it.Name -eq $part) {{
            try {{ $next = $it.GetFolder }} catch {{ $next = $null }}
            break
        }}
    }}
    if (!$next) {{ Write-Output 'RESULT:{{"error":"path_not_found"}}'; exit }}
    $currentFolder = $next
}}

$fileItem = $null
foreach ($it in $currentFolder.Items()) {{
    if ($it.Name -eq $fileName) {{ $fileItem = $it; break }}
}}
if (!$fileItem) {{ Write-Output 'RESULT:{{"error":"file_not_found"}}'; exit }}

if (!(Test-Path $targetDir)) {{ New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }}
$destFolder = $shell.Namespace($targetDir)
if (!$destFolder) {{ Write-Output 'RESULT:{{"error":"no_dest"}}'; exit }}

$destPath = Join-Path $targetDir $destName
$destFolder.CopyHere($fileItem, 4 + 16 + 512 + 1024)

$timeout = 0
$lastSize = -1
$stableCount = 0
while ($timeout -lt 3600) {{
    Start-Sleep -Milliseconds 400
    if (Test-Path $destPath) {{
        $curSize = (Get-Item $destPath).Length
        # WICHTIG: Hier bewusst KEINE "PROGRESS:"-Zeile mehr ausgeben.
        # Python verlaesst sich fuer den Fortschritt nicht mehr auf diese
        # PowerShell-Pipe (siehe _run_copy_with_progress) - "powershell.exe"
        # puffert seine Standardausgabe beim Umleiten in eine Pipe intern
        # in Bloecken, ganz unabhaengig von [Console]::Out.Flush(). Dadurch
        # kamen die Fortschritts-Zeilen bei Python immer nur verzoegert/
        # gebuendelt an. Python liest die Zielgroesse jetzt stattdessen
        # SELBST direkt von der Platte (os.path.getsize), waehrend dieses
        # Skript hier nur noch (voellig unabhaengig davon) auf Stabilitaet
        # der Dateigroesse wartet, um zu wissen, wann kopiert wurde.
        if ($curSize -eq $lastSize -and $curSize -gt 0) {{
            # Groesse muss ZWEIMAL hintereinander gleich sein, bevor wir
            # "fertig" annehmen - verhindert, dass ein kurzer Stocker bei
            # grossen Dateien faelschlich als "abgeschlossen" gewertet wird.
            $stableCount++
            if ($stableCount -ge 2) {{ break }}
        }} else {{
            $stableCount = 0
        }}
        $lastSize = $curSize
    }}
    $timeout++
}}

$ok = Test-Path $destPath
$result = [PSCustomObject]@{{ ok = $ok }}
Write-Output ("RESULT:" + ($result | ConvertTo-Json -Compress))
'''


# --- PHASE 4: Nach ERFOLGREICHEM Kopieren ALLER Dateien - einmalig
# gesammelt von der GoPro loeschen -----------------------------------------
#
# Vorher wurde jede Datei direkt nach ihrem eigenen Kopiervorgang geloescht
# -> Windows zeigt beim Loeschen von einem MTP-Geraet (kein Papierkorb) pro
# Datei einen eigenen "Wirklich endgueltig loeschen?"-Dialog, den man dann
# bei z.B. 200 Fotos 200x einzeln wegklicken musste.
#
# Jetzt: waehrend des Kopierens wird NICHTS geloescht. Erst wenn ALLE
# Dateien uebertragen sind, baut Python eine Liste aller erfolgreich
# kopierten Dateien (gruppiert nach Kamera-Unterordner) und uebergibt sie
# in EINEM Rutsch hierher. FolderItems.Filter() waehlt alle betroffenen
# Dateien in einem Ordner auf einmal aus, InvokeVerbEx("delete") loescht
# diese Auswahl als EINE Operation -> nur noch EIN Bestaetigungsdialog fuer
# die komplette Auswahl (pro betroffenem Kamera-Unterordner), statt einer
# pro Datei. Falls Filter/InvokeVerbEx auf einem System nicht verfuegbar
# sein sollte, faellt das Skript defensiv auf die alte Einzel-Verb-Methode
# zurueck, damit zumindest ueberhaupt geloescht wird.

def build_batch_delete_script(groups):
    """groups: Liste von {"path": [...Ordnerpfad-Teile...], "names": [...Dateinamen...]}"""
    groups_literal = "@(" + ", ".join(
        "@{ path = " + _ps_str_array(g["path"]) + "; names = " + _ps_str_array(g["names"]) + " }"
        for g in groups
    ) + ")"

    return f'''
try {{ [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) }} catch {{}}
$ErrorActionPreference = "SilentlyContinue"
$groups = {groups_literal}

$shell = New-Object -ComObject Shell.Application
$ns17 = $shell.Namespace(17)
if (!$ns17) {{ Write-Output 'RESULT:{{"deleted":0,"error":"no_namespace"}}'; exit }}
$gopro = $ns17.Items() | Where-Object {{$_.Name -match "{GOPRO_MODEL_MATCH_PATTERN}"}} | Select-Object -First 1
if (!$gopro) {{ Write-Output 'RESULT:{{"deleted":0,"error":"not_found"}}'; exit }}

$totalDeleted = 0
foreach ($grp in $groups) {{
    $currentFolder = $gopro.GetFolder
    $ok = $true
    foreach ($part in $grp.path) {{
        $next = $null
        foreach ($it in $currentFolder.Items()) {{
            if ($it.Name -eq $part) {{
                try {{ $next = $it.GetFolder }} catch {{ $next = $null }}
                break
            }}
        }}
        if (!$next) {{ $ok = $false; break }}
        $currentFolder = $next
    }}
    if (!$ok) {{ continue }}

    $namesInGroup = $grp.names
    $wantedCount = $namesInGroup.Count
    $batchWorked = $false

    # --- Versuch 1: FolderItems.Filter() -----------------------------------
    # Schnell, aber offenbar auf manchen Systemen/MTP-Geraeten nicht
    # zuverlaessig, wenn mehrere Dateinamen per Semikolon zusammengefuegt
    # werden - dann matcht $subset teilweise oder gar nicht. VORHER wurde
    # hier schon "$subset.Count -gt 0" als voller Erfolg gewertet, obwohl
    # z.B. nur 1 von 20 Dateien getroffen wurde. Die restlichen 19 landeten
    # dann unbemerkt im allerletzten Einzel-Fallback ganz unten
    # -> genau DAS war vermutlich der Grund fuer "20 Dateien = 20 Prompts".
    # Jetzt wird nur noch als Erfolg gewertet, wenn WIRKLICH alle erwarteten
    # Dateien getroffen wurden.
    try {{
        $filterSpec = [string]::Join(";", $namesInGroup)
        $allItems = $currentFolder.Items()
        $subset = $allItems.Filter(64, $filterSpec)
        if ($subset -ne $null -and $subset.Count -eq $wantedCount) {{
            $subset.InvokeVerbEx("delete")
            $totalDeleted += $wantedCount
            $batchWorked = $true
        }}
    }} catch {{}}

    # --- Versuch 2: echte Mehrfachauswahl ueber ein kurz geoeffnetes
    # Explorer-Fenster - genau wie beim manuellen Markieren mehrerer
    # Dateien im Explorer (Strg-Klick / Filter-Box) und dann Entf. Das
    # erzeugt garantiert nur EINEN Bestaetigungsdialog fuer die komplette
    # Auswahl, unabhaengig davon, ob Filter() oben zuverlaessig war. Das
    # Fenster wird dafuer kurz geoeffnet und danach sofort wieder
    # geschlossen. -----------------------------------------------------------
    if (!$batchWorked) {{
        try {{
            $folderPath = $currentFolder.Self.Path
            $tempExplorer = New-Object -ComObject Shell.Application
            $tempExplorer.Explore($folderPath)
            $targetWindow = $null
            for ($try = 0; $try -lt 15 -and $targetWindow -eq $null; $try++) {{
                Start-Sleep -Milliseconds 200
                foreach ($win in $tempExplorer.Windows()) {{
                    try {{
                        if ($win.Document.Folder.Self.Path -eq $folderPath) {{ $targetWindow = $win }}
                    }} catch {{}}
                }}
            }}
            if ($targetWindow -ne $null) {{
                $doc = $targetWindow.Document
                $matched = 0
                foreach ($n in $namesInGroup) {{
                    $cand = $null
                    foreach ($it in $currentFolder.Items()) {{
                        if ($it.Name -eq $n) {{ $cand = $it; break }}
                    }}
                    if ($cand) {{
                        # 9 = Auswahl hinzufuegen (1), ohne ins Bild zu scrollen (8)
                        $doc.SelectItem($cand, 9)
                        $matched += 1
                    }}
                }}
                if ($matched -gt 0) {{
                    $sel = $doc.SelectedItems()
                    if ($sel -ne $null -and $sel.Count -gt 0) {{
                        $sel.InvokeVerbEx("delete")
                        $totalDeleted += $sel.Count
                        $batchWorked = $true
                    }}
                }}
                try {{ $targetWindow.Quit() }} catch {{}}
            }}
        }} catch {{}}
    }}

    if (!$batchWorked) {{
        # Letzter Ausweg: sollte auch die Explorer-Auswahl auf diesem
        # System nicht funktionieren, wenigstens einzeln loeschen (mit
        # Prompt je Datei) - besser als gar nicht zu loeschen.
        foreach ($n in $namesInGroup) {{
            $it = $null
            foreach ($cand in $currentFolder.Items()) {{
                if ($cand.Name -eq $n) {{ $it = $cand; break }}
            }}
            if ($it) {{
                try {{
                    foreach ($verb in $it.Verbs()) {{
                        if ($verb.Name -match "[Ll]öschen" -or $verb.Name -match "[Dd]elete") {{
                            $verb.DoIt()
                            $totalDeleted += 1
                            break
                        }}
                    }}
                }} catch {{}}
            }}
        }}
    }}
}}

$result = [PSCustomObject]@{{ deleted = $totalDeleted }}
Write-Output ("RESULT:" + ($result | ConvertTo-Json -Compress))
'''


def _extract_result_json(stdout_text):
    """Nimmt die LETZTE Zeile, die mit RESULT: beginnt (falls PowerShell
    vorher noch andere Ausgaben/Warnungen schreibt) und parst sie als JSON."""
    result_line = None
    for line in (stdout_text or "").splitlines():
        line = line.strip()
        if line.startswith("RESULT:"):
            result_line = line
    if result_line is None:
        return None
    try:
        return json.loads(result_line.split(":", 1)[1])
    except Exception:
        return None


# --- [ AUFNAHMEDATUM AUS DATEI-METADATEN LESEN ] --------------------------
#
# Fuer die Datums-Ordner (YYYY-MM-DD) beim Sync brauchen wir das ECHTE
# Aufnahmedatum der Datei, nicht das Kopierdatum. Das steckt in den
# Metadaten der Datei selbst:
#  - JPEG: im EXIF-Block (Tag "DateTimeOriginal")
#  - MP4:  in der "mvhd"-Box im "moov"-Container (Sekunden seit 1.1.1904)
#
# Beides wird hier bewusst OHNE Zusatz-Bibliothek (kein Pillow/ffprobe)
# geparst, passend zum Rest dieses Tools, das schon auf moeglichst wenige
# Abhaengigkeiten setzt. Bei Videos wird dabei NICHT die komplette (oft
# mehrere GB grosse) Datei eingelesen, sondern nur die kleinen Metadaten-
# Boxen - die eigentlichen Bilddaten ("mdat") werden nur ueberSPRUNGEN
# (geseekt), nie gelesen.

def _read_jpeg_exif_datetime(path):
    """Liest DateTimeOriginal (0x9003) bzw. ersatzweise DateTime (0x0132)
    aus dem EXIF-Block eines JPEGs. Gibt ein datetime-Objekt oder None
    zurueck (z.B. wenn die Datei kein EXIF hat oder kein JPEG ist)."""
    try:
        with open(path, "rb") as f:
            data = f.read(131072)  # EXIF sitzt immer nahe am Dateianfang

        if data[0:2] != b'\xff\xd8':
            return None

        pos = 2
        exif_data = None
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                break
            marker = data[pos + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            if marker == 0xDA:  # Start of Scan - danach kommt kein EXIF mehr
                break
            seg_len = (data[pos + 2] << 8) | data[pos + 3]
            if marker == 0xE1 and data[pos + 4:pos + 10] == b'Exif\x00\x00':
                exif_data = data[pos + 10: pos + 2 + seg_len]
                break
            pos += 2 + seg_len

        if not exif_data or len(exif_data) < 8:
            return None

        byte_order = exif_data[0:2]
        if byte_order == b'II':
            endian = '<'
        elif byte_order == b'MM':
            endian = '>'
        else:
            return None

        def read_ifd(offset):
            entries = {}
            if offset + 2 > len(exif_data):
                return entries
            count = struct.unpack(endian + 'H', exif_data[offset:offset + 2])[0]
            p = offset + 2
            type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
            for _ in range(count):
                if p + 12 > len(exif_data):
                    break
                tag, typ, cnt = struct.unpack(endian + 'HHI', exif_data[p:p + 8])
                raw_field = exif_data[p + 8:p + 12]
                size = type_sizes.get(typ, 1) * cnt
                if size <= 4:
                    raw = raw_field[:size]
                else:
                    off = struct.unpack(endian + 'I', raw_field)[0]
                    raw = exif_data[off:off + size]
                entries[tag] = raw
                p += 12
            return entries

        ifd0_offset = struct.unpack(endian + 'I', exif_data[4:8])[0]
        ifd0 = read_ifd(ifd0_offset)

        exif_sub = {}
        if 0x8769 in ifd0:
            sub_offset = struct.unpack(endian + 'I', ifd0[0x8769][:4])[0]
            exif_sub = read_ifd(sub_offset)

        for tag_set, tag in ((exif_sub, 0x9003), (exif_sub, 0x9004), (ifd0, 0x0132)):
            if tag in tag_set:
                text = tag_set[tag].split(b'\x00')[0].decode('ascii', errors='ignore').strip()
                try:
                    return datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
                except Exception:
                    continue
        return None
    except Exception:
        return None


def _read_mp4_creation_time(path, assume_local=False):
    """Liest das Aufnahmedatum aus der 'mvhd'-Box im 'moov'-Container eines
    MP4/MOV. Liest dabei NUR die (kleinen) Metadaten-Boxen von der Platte,
    ueberspringt aber die riesige 'mdat'-Box per seek() statt sie zu lesen -
    funktioniert daher auch bei mehrere GB grossen GoPro-Videos schnell."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            f.seek(0)

            pos = 0
            moov_data = None
            while pos + 8 <= file_size:
                f.seek(pos)
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size = struct.unpack(">I", header[0:4])[0]
                box_type = header[4:8]
                header_len = 8
                if box_size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        break
                    box_size = struct.unpack(">Q", ext)[0]
                    header_len = 16
                elif box_size == 0:
                    box_size = file_size - pos
                if box_size < header_len:
                    break
                if box_type == b'moov':
                    f.seek(pos)
                    # Sicherheitsobergrenze - 'moov' ist bei GoPro-Dateien
                    # normalerweise nur einige KB bis wenige MB gross.
                    moov_data = f.read(min(box_size, 16 * 1024 * 1024))
                    break
                pos += box_size

        if not moov_data:
            return None

        mpos = 8
        mn = len(moov_data)
        while mpos + 8 <= mn:
            b_size = struct.unpack(">I", moov_data[mpos:mpos + 4])[0]
            b_type = moov_data[mpos + 4:mpos + 8]
            if b_size < 8:
                break
            if b_type == b'mvhd':
                version = moov_data[mpos + 8]
                if version == 1:
                    creation_time = struct.unpack(">Q", moov_data[mpos + 12:mpos + 20])[0]
                else:
                    creation_time = struct.unpack(">I", moov_data[mpos + 12:mpos + 16])[0]
                if not creation_time:
                    return None
                # WICHTIG: Der mvhd-Zeitstempel ist laut MP4/QuickTime-
                # Spezifikation IMMER in UTC gespeichert, unabhaengig davon,
                # in welcher Zeitzone die GoPro tatsaechlich aufgenommen hat.
                # Frueher wurde dieser Wert direkt als Lokalzeit verwendet -
                # dadurch konnten z.B. abends/nachts aufgenommene Videos
                # (UTC ist in Deutschland 1-2h "in der Zukunft") beim
                # Zurueckrechnen auf den falschen, vorherigen Kalendertag
                # fallen und landeten so faelschlich im Datumsordner des
                # Vortags (oder sogar Vorvortags bei Aufnahmen kurz nach
                # Mitternacht). Fix: Zeitstempel explizit als UTC markieren
                # und dann in die lokale Zeitzone des Systems umrechnen,
                # BEVOR daraus der Kalendertag fuer den Zielordner bestimmt wird.
                if assume_local:
                    # HERO5 y anteriores: el valor ya es la hora LOCAL de
                    # la camara (no UTC); convertirlo correria la fecha.
                    dt = datetime(1904, 1, 1) + timedelta(seconds=creation_time)
                else:
                    dt_utc = datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=creation_time)
                    dt = dt_utc.astimezone().replace(tzinfo=None)
                # Grobe Plausibilitaetspruefung - manche Kameras/Schnitt-
                # programme schreiben hier unplausible Werte (0, Jahr 1904,
                # oder Datum in der Zukunft durch falsch gestellte Uhr).
                if dt.year < 1990 or dt > datetime.now() + timedelta(days=1):
                    return None
                return dt
            mpos += b_size
        return None
    except Exception:
        return None


def get_media_capture_date(path, mvhd_local=False):
    """Liefert das beste verfuegbare 'Aufnahmedatum' fuer eine Datei:
    zuerst aus den echten Metadaten (EXIF/MP4), sonst als Rueckfallebene
    das Datei-Aenderungsdatum auf der Platte."""
    ext = os.path.splitext(path)[1].lower()
    dt = None
    if ext in (".jpg", ".jpeg"):
        dt = _read_jpeg_exif_datetime(path)
    elif ext == ".mp4":
        dt = _read_mp4_creation_time(path, assume_local=mvhd_local)

    if dt is None:
        try:
            dt = datetime.fromtimestamp(os.path.getmtime(path))
        except Exception:
            dt = datetime.now()
    return dt


# --- [ VIDEOS DANADOS ] ---

def is_broken_mp4(path):
    """True si el MP4 no tiene la caja 'moov' (indice): video cortado por una
    copia interrumpida o una grabacion que no cerro bien. No se puede
    reproducir. Solo lee los encabezados de las cajas (unos bytes)."""
    if not path.lower().endswith(".mp4"):
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            pos = 0
            while pos + 8 <= size:
                f.seek(pos)
                h = f.read(8)
                n = struct.unpack(">I", h[:4])[0]
                if h[4:8] == b"moov":
                    return False
                if n == 1:
                    n = struct.unpack(">Q", f.read(8))[0]
                elif n == 0:
                    n = size - pos
                if n < 8:
                    break
                pos += n
        return True
    except Exception:
        return False


def replaceable_broken_copy(existing_path, new_size):
    """Un archivo ya existente con el mismo nombre que esta danado y es mas
    chico que el original: se reemplaza por la copia buena (no se crea _1)."""
    try:
        return os.path.getsize(existing_path) < new_size and is_broken_mp4(existing_path)
    except OSError:
        return False


# --- [ DETECCION DE DUPLICADOS ] ---

_DUP_SUFFIX_RE = re.compile(r"^(.*)_(\d+)(\.[^.]+)$")


def index_existing(index, name, value):
    """Registra un archivo del destino para detectar duplicados. Clave sin
    distinguir mayusculas (como Windows). Si se llama "GOPR0001_1.MP4" (copia
    renombrada por choque de nombres) se registra TAMBIEN como "gopr0001.mp4",
    asi la proxima sincronizacion lo reconoce y no crea "_2", "_3"..."""
    low = name.lower()
    index.setdefault(low, []).append(value)
    m = _DUP_SUFFIX_RE.match(low)
    if m:
        index.setdefault(m.group(1) + m.group(3), []).append(value)


def existing_candidates(index, name):
    return index.get(name.lower(), [])


# --- [ CARPETAS ESTILO QUIK ] ---

def quik_camera_folder(target_dir, device_name, existing_paths=()):
    """GoPro Quik guardaba en FECHA\\<camara> <n>\\archivo, p. ej.
    2017-07-11\\HERO5 Black 1\\GOPR0007.JPG. Reutiliza el numero que ya
    exista en la biblioteca para esa camara; si no hay, usa 1."""
    if not device_name:
        return None
    pat = re.compile(r"^" + re.escape(device_name) + r" (\d+)$", re.IGNORECASE)
    counts = {}
    for path in existing_paths:
        m = pat.match(os.path.basename(os.path.dirname(path)))
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    num = max(counts, key=counts.get) if counts else "1"
    return f"{device_name} {num}"


# --- [ COPIA CON ATRIBUTOS (SD y respaldos) ] ---

_COPY_CHUNK = 4 * 1024 * 1024


def _copy_creation_time(src, dst):
    """shutil.copystat no copia la fecha de CREACION en Windows; se hace
    aqui con SetFileTime para que el archivo quede identico al original."""
    if os.name != "nt":
        return
    try:
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        st = os.stat(src)
        ctime = getattr(st, "st_birthtime", None) or st.st_ctime
        ft_val = int(ctime * 10_000_000) + 116444736000000000
        ft = wintypes.FILETIME(ft_val & 0xFFFFFFFF, ft_val >> 32)
        GENERIC_WRITE, OPEN_EXISTING, FILE_SHARE_ALL = 0x40000000, 3, 0x7
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        h = k32.CreateFileW(str(dst), GENERIC_WRITE, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None)
        if not h or h == ctypes.c_void_p(-1).value:
            return
        try:
            k32.SetFileTime(wintypes.HANDLE(h), ctypes.byref(ft), None, None)
        finally:
            k32.CloseHandle(wintypes.HANDLE(h))
    except Exception:
        pass


def set_file_times(path, dt):
    """Pone fecha de modificacion, acceso y CREACION = dt (fecha de captura de
    la camara). Al copiar por USB, Windows dejaba la fecha del momento de la
    copia; Quik usaba la de captura."""
    try:
        ts = dt.timestamp()
        os.utime(path, (ts, ts))
    except Exception:
        return
    if os.name != "nt":
        return
    try:
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        ft_val = int(ts * 10_000_000) + 116444736000000000
        ft = wintypes.FILETIME(ft_val & 0xFFFFFFFF, ft_val >> 32)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        h = k32.CreateFileW(str(path), 0x40000000, 0x7, None, 3, 0, None)
        if h and h != ctypes.c_void_p(-1).value:
            try:
                k32.SetFileTime(wintypes.HANDLE(h), ctypes.byref(ft), None, None)
            finally:
                k32.CloseHandle(wintypes.HANDLE(h))
    except Exception:
        pass


def _parse_camera_dt(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S") if text else None
    except Exception:
        return None


def _remove_stale_part(path):
    """Restos de copias cortadas (corte de luz, tarjeta desconectada...)."""
    try:
        os.remove(path)
        print(f"[Sync] Borrado resto de copia incompleta: {path}")
    except OSError:
        pass


class _CopyCancelled(Exception):
    pass


class ReadStalled(TimeoutError):
    """La lectura no avanzo en el tiempo limite (sector danado, tarjeta trabada)."""


def call_with_stall_guard(fn, stall_s=60, should_cancel=None):
    """Corre fn(beat) en un hilo aparte; fn llama beat() cada vez que avanza.
    Si pasan stall_s segundos sin avanzar lanza ReadStalled: una lectura de
    Windows sobre un sector danado puede quedar bloqueada minutos y no se
    puede interrumpir, asi que ese hilo se abandona y la copia sigue con el
    proximo archivo. Cancelar tambien responde al instante."""
    box = {"t": time.monotonic(), "done": False, "res": None, "exc": None}

    def beat():
        box["t"] = time.monotonic()

    def target():
        try:
            box["res"] = fn(beat)
        except BaseException as e:      # se re-lanza en el hilo que espera
            box["exc"] = e
        finally:
            box["done"] = True

    th = threading.Thread(target=target, daemon=True)
    th.start()
    while not box["done"]:
        th.join(0.2)
        if box["done"]:
            break
        if should_cancel is not None and should_cancel():
            raise _CopyCancelled()
        if time.monotonic() - box["t"] > stall_s:
            raise ReadStalled("stalled")
    if box["exc"] is not None:
        raise box["exc"]
    return box["res"]


def copy_file_preserving(src, dst, size=None, progress_cb=None, should_cancel=None, errors_out=None, stall_s=60):
    """Copia vigilada: si el origen deja de responder (archivo danado en la
    tarjeta) se abandona ese archivo en vez de colgar toda la copia."""
    abandon = {"v": False}

    def cancel():
        return abandon["v"] or (should_cancel is not None and should_cancel())
    try:
        return call_with_stall_guard(
            lambda beat: _copy_file_preserving_impl(src, dst, size, progress_cb, cancel, errors_out, beat),
            stall_s, should_cancel)
    except _CopyCancelled:
        abandon["v"] = True
        return False
    except ReadStalled as e:
        abandon["v"] = True
        print(f"[Copia] Sin respuesta leyendo {src}: se saltea")
        if errors_out is not None:
            errors_out.append(e)
        return False


def _copy_file_preserving_impl(src, dst, size=None, progress_cb=None, should_cancel=None, errors_out=None,
                               heartbeat=None):
    """Copia por bloques a "<dst>.part" y renombra al final (nunca queda un
    archivo a medias con el nombre definitivo), verifica el tamano y copia
    los atributos: fechas de modificacion/acceso/creacion y flags."""
    if size is None:
        size = os.path.getsize(src)
    tmp = dst + ".part"
    copied = 0
    last_pct = -1.0
    try:
        with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
            while True:
                if should_cancel is not None and should_cancel():
                    raise _CopyCancelled()
                buf = fsrc.read(_COPY_CHUNK)
                if not buf:
                    break
                fdst.write(buf)
                copied += len(buf)
                if heartbeat is not None:
                    heartbeat()
                if progress_cb and size > 0:
                    pct = min(100.0, copied * 100.0 / size)
                    if pct - last_pct >= 0.5:
                        progress_cb(pct)
                        last_pct = pct
            fdst.flush()
            os.fsync(fdst.fileno())       # a disco ANTES de renombrar: un corte de luz no deja un archivo vacio
        if os.path.getsize(tmp) != size:
            raise IOError("size_mismatch")
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
        _copy_creation_time(src, dst)
        return True
    except Exception as e:
        if not isinstance(e, _CopyCancelled):
            print(f"[Copia] Error copiando {src} -> {dst}: {e}")
            if errors_out is not None:
                errors_out.append(e)
        # Borrar el .part a medias (con reintentos: el antivirus o el disco
        # pueden tenerlo abierto un instante).
        for _ in range(10):
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
                break
            except Exception:
                time.sleep(0.2)
        return False


def _is_same_or_inside(path, folder):
    try:
        path = os.path.normcase(os.path.abspath(path))
        folder = os.path.normcase(os.path.abspath(folder))
        return path == folder or path.startswith(folder.rstrip("\\/") + os.sep)
    except Exception:
        return False


class BackupWorkerThread(QThread):
    """Respaldo incremental del destino local a una o varias carpetas
    (tipicamente discos externos): copia solo lo nuevo o cambiado (tamano o
    fecha distintos), conserva los atributos y NUNCA borra nada en el
    respaldo, asi un borrado accidental en el PC no se propaga."""
    progress = Signal(int, int, str, float)
    finished_backup = Signal(bool, str, dict)
    # Misma interfaz que SyncWorkerThread: asi corre en Actividad como
    # cualquier copia (progreso total, cancelar, cola, barra de tareas).
    finished_sync = Signal(bool, str, dict)
    plan = Signal(object)

    def __init__(self, source_dir, backup_dirs, lang="de", parent=None):
        super().__init__(parent)
        self.source_dir = source_dir
        self.backup_dirs = [d for d in (backup_dirs or []) if d]
        self.lang = lang
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @staticmethod
    def _dest_available(dest):
        # Si la carpeta no existe pero su unidad si (disco conectado), se crea.
        if os.path.isdir(dest):
            return True
        drive = os.path.splitdrive(os.path.abspath(dest))[0]
        if drive and os.path.isdir(drive + os.sep):
            try:
                os.makedirs(dest, exist_ok=True)
                return True
            except Exception:
                return False
        return False

    def _finish(self, ok, message, info):
        self.finished_backup.emit(ok, message, info)
        self.finished_sync.emit(ok, message, info)

    def run(self):
        if not self.backup_dirs:
            self._finish(False, tr(self.lang, "backup_none"), {})
            return

        files = []
        for root, _dirs, names in os.walk(self.source_dir):
            if self._cancel:
                break
            for n in names:
                if n.endswith(".part"):
                    continue
                full = os.path.join(root, n)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                files.append((full, os.path.relpath(full, self.source_dir), st.st_size, st.st_mtime))

        skipped = 0
        missing = []
        pending = []          # (src, rel, size, dst) de TODOS los destinos
        for dest_root in self.backup_dirs:
            if self._cancel:
                break
            if _is_same_or_inside(dest_root, self.source_dir) or not self._dest_available(dest_root):
                missing.append(dest_root)
                continue
            for src, rel, size, mtime in files:
                dst = os.path.join(dest_root, rel)
                try:
                    dst_st = os.stat(dst)
                    # Tolerancia de 2 s: FAT/exFAT guarda la fecha con esa precision.
                    if dst_st.st_size == size and abs(dst_st.st_mtime - mtime) <= 2:
                        skipped += 1
                        continue
                except OSError:
                    pass
                pending.append((src, rel, size, dst))
        self.plan.emit([size for _s, _r, size, _d in pending])

        copied = errors = 0
        error_list = []
        total = len(pending)
        for idx, (src, rel, size, dst) in enumerate(pending, start=1):
            if self._cancel:
                break
            name = os.path.basename(rel)
            self.progress.emit(idx, total, name, 0.0)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
            except Exception as e:
                errors += 1
                error_list.append((name, str(e)))
                continue

            def cb(pct, idx=idx, total=total, name=name):
                self.progress.emit(idx, total, name, pct)

            errs = []
            if copy_file_preserving(src, dst, size, cb, should_cancel=lambda: self._cancel, errors_out=errs):
                copied += 1
                self.progress.emit(idx, total, name, 100.0)
            elif not self._cancel:
                errors += 1
                error_list.append((name, str(errs[0]) if errs else tr(self.lang, "err_unknown")))

        info = {"copied": copied, "skipped": skipped, "errors": errors, "missing": missing,
                "error_list": error_list, "total": total}
        if self._cancel:
            info["cancelled"] = True
            self._finish(False, tr(self.lang, "backup_cancelled", copied=copied), info)
            return
        msg = tr(self.lang, "backup_done", copied=copied, skipped=skipped)
        if missing:
            msg += tr(self.lang, "backup_missing_suffix", dirs=", ".join(missing))
        if errors:
            msg += tr(self.lang, "backup_errors_suffix", errors=errors)
        self._finish(errors == 0 and not missing, msg, info)


class SyncWorkerThread(QThread):
    progress = Signal(int, int, str, float)
    finished_sync = Signal(bool, str, dict)
    plan = Signal(object)        # lista con el tamano de cada archivo (para el progreso total)

    def __init__(self, target_dir, delete_after, sync_mode="all", lang="de", sort_into_date_folders=True, parent=None,
                 import_dir=None, only_keys=None, only_paths=None):
        super().__init__(parent)
        # Sincronizar solo estos archivos de la camara ("carpeta|nombre"), elegidos
        # en "Elegir archivos..."; None = todos.
        self.only_keys = set(only_keys) if only_keys else None
        self.target_dir = target_dir
        self.delete_after = delete_after
        self.sync_mode = sync_mode if sync_mode in MEDIA_EXTENSIONS_BY_MODE else "all"
        self.lang = lang
        self.sort_into_date_folders = sort_into_date_folders
        # Se fija la fuente al iniciar, para que no cambie a mitad del sync.
        self.model = ACTIVE_SOURCE.get("model")
        self.sd_root = ACTIVE_SOURCE.get("sd_root")
        self.mvhd_local = bool(GOPRO_MODELS.get(self.model or "", {}).get("mvhd_local"))
        self.device_name = LAST_DEVICE_NAME.get("name")
        self._cancel = False
        self._wpd = None          # conexion WPD con la camara (None = sin probar, False = no disponible)
        self.error_list = []     # [(archivo, motivo)] para mostrar en Actividad
        # Importacion manual (menu "Importar SD / carpeta"): fuente elegida por
        # el usuario; nunca se borra nada del origen.
        self.import_dir = import_dir
        # Importar solo estos archivos (elegidos en "Elegir archivos...").
        self.only_paths = {os.path.normcase(os.path.abspath(x)) for x in only_paths} if only_paths else None
        if import_dir:
            self.sd_root = import_dir
            self.delete_after = False
            gopro_root = _find_gopro_card_root(import_dir)
            self.model = _model_from_version_txt(gopro_root) if gopro_root else None
            self.device_name = LAST_DEVICE_NAME.get("name") if self.model else None
            self.mvhd_local = bool(GOPRO_MODELS.get(self.model or "", {}).get("mvhd_local"))

    # ------------------------------------------------------------------
    # Sincronizacion desde TARJETA SD (lector de tarjetas / unidad normal)
    # ------------------------------------------------------------------
    # Igual que hacia GoPro Quik for desktop: copia directa por sistema de
    # archivos (mucho mas rapida y fiable que MTP), conserva los atributos
    # de cada archivo (fecha de modificacion, de acceso y de CREACION) y lo
    # deja en una carpeta YYYY-MM-DD segun la fecha de captura.

    def _list_sd_media(self, sd_root, extensions):
        if self.import_dir:
            return self._list_any_folder(sd_root, extensions)
        dcim = os.path.join(sd_root, "DCIM")
        exts = tuple(e.lower() for e in extensions)
        found = []
        try:
            subdirs = sorted(e for e in os.listdir(dcim)
                             if _GOPRO_DCIM_SUBDIR_RE.match(e) and os.path.isdir(os.path.join(dcim, e)))
        except OSError:
            return None
        for sub in subdirs:
            folder = os.path.join(dcim, sub)
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for n in names:
                if n.startswith(".") or not n.lower().endswith(exts):
                    continue
                full = os.path.join(folder, n)
                try:
                    if not os.path.isfile(full):
                        continue
                    size = os.path.getsize(full)
                except OSError:
                    continue
                found.append((full, n, size))
        return found

    def cancel(self):
        """Pide cancelar: se descarta el archivo en curso y no se borra nada
        de la camara/tarjeta."""
        self._cancel = True

    def _cancelled_result(self, copied, total):
        self.finished_sync.emit(False, tr(self.lang, "sync_cancelled_msg", copied=copied, total=total),
                                {"total": total, "copied": copied, "deleted": 0, "errors": len(self.error_list),
                                 "cancelled": True, "error_list": list(self.error_list)})

    def _err(self, name, reason):
        self.error_list.append((name, reason))

    def _reason_from_exception(self, e):
        """Motivo entendible para el usuario a partir de la excepcion."""
        winerr = getattr(e, "winerror", None)
        errno_ = getattr(e, "errno", None)
        if isinstance(e, ReadStalled):
            return tr(self.lang, "err_stalled")
        if str(e) == "size_mismatch":
            return tr(self.lang, "err_size_mismatch")
        if isinstance(e, FileNotFoundError) or winerr in (2, 3):
            return tr(self.lang, "err_gone")
        if winerr in (112,) or errno_ == 28:
            return tr(self.lang, "err_disk_full")
        if winerr in (5, 21, 23, 31, 55, 483, 1006, 1117, 1167) or errno_ == 5:
            return tr(self.lang, "err_device")
        if isinstance(e, PermissionError) or winerr in (32, 33):
            return tr(self.lang, "err_locked")
        return str(e) or tr(self.lang, "err_unknown")

    def _copy_file_with_progress(self, src, dst, idx, total, name, size):
        def cb(pct):
            self.progress.emit(idx, total, name, pct)
        errs = []
        ok = copy_file_preserving(src, dst, size, cb, should_cancel=lambda: self._cancel, errors_out=errs)
        self._last_stalled = bool(not ok and errs and isinstance(errs[-1], ReadStalled))
        if not ok and errs:
            self._err(name, self._reason_from_exception(errs[-1]))
        return ok

    @staticmethod
    def _already_there(existing_index, name, size):
        """Ya hay una copia con el mismo nombre y tamano en el destino."""
        for existing_path in existing_candidates(existing_index, name):
            try:
                if os.path.getsize(existing_path) == size:
                    return True
            except Exception:
                pass
        return False

    def _result_message(self, copied, total, already, deleted, errors):
        """Mensaje final: cuenta solo lo que habia que copiar; lo que ya estaba va aparte."""
        if total == 0 and not errors:
            msg = tr(self.lang, "no_new_media")
        else:
            msg = tr(self.lang, "sync_result", copied=copied, total=total)
        if already:
            msg += tr(self.lang, "sync_already_suffix", n=already)
        if self.delete_after:
            msg += tr(self.lang, "sync_deleted_suffix", deleted=deleted)
        if errors:
            msg += tr(self.lang, "sync_errors_suffix", errors=errors)
        return msg

    def _import_one(self, src, name, size, idx, total, existing_index, cam_folder):
        """Importa un archivo. Devuelve "ok", "already", "empty", "error" o "cancel"."""
        for existing_path in existing_candidates(existing_index, name):
            try:
                if os.path.getsize(existing_path) == size:
                    self.progress.emit(idx, total, name, 100.0)
                    return "already"
            except Exception:
                pass
        if size == 0:
            # Grabacion cortada (0 bytes): no tiene nada que copiar.
            self._err(name, tr(self.lang, "err_empty_file"))
            return "empty"

        dest_dir = self.target_dir
        if self.sort_into_date_folders:
            try:
                # Leer la fecha tambien puede colgarse con un archivo danado.
                dt = call_with_stall_guard(lambda beat: self._sd_capture_date(src), 30, lambda: self._cancel)
            except ReadStalled:
                self._last_stalled = True
                self._err(name, tr(self.lang, "err_stalled"))
                return "error"
            dest_dir = os.path.join(self.target_dir, dt.strftime("%Y-%m-%d"))
            if cam_folder:
                dest_dir = os.path.join(dest_dir, cam_folder)
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception:
            dest_dir = self.target_dir

        dest = os.path.join(dest_dir, name)
        if os.path.exists(dest) and replaceable_broken_copy(dest, size):
            print(f"[Sync] Reemplazando copia danada: {dest}")
        elif os.path.exists(dest):
            base, ext = os.path.splitext(name)
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(dest_dir, f"{base}_{counter}{ext}")
                counter += 1

        ok = self._copy_file_with_progress(src, dest, idx, total, name, size)
        if self._cancel and not ok:
            return "cancel"
        if ok:
            index_existing(existing_index, os.path.basename(dest), dest)
            self.progress.emit(idx, total, name, 100.0)
            return "ok"
        return "error"

    def _sd_capture_date(self, path):
        """Fecha para la carpeta. La GoPro pone la fecha de modificacion del
        archivo en la tarjeta con su reloj local (la misma que usaba Quik),
        asi que es la referencia principal; EXIF/MP4 solo si falta."""
        ext = os.path.splitext(path)[1].lower()
        dt = None
        if ext in (".jpg", ".jpeg"):
            dt = _read_jpeg_exif_datetime(path)
        if dt is None:
            try:
                dt = datetime.fromtimestamp(os.path.getmtime(path))
                if dt.year < 2000:   # reloj de la camara sin configurar
                    dt = None
            except Exception:
                dt = None
        if dt is None:
            dt = get_media_capture_date(path, mvhd_local=self.mvhd_local)
        return dt

    def _list_any_folder(self, root_dir, extensions):
        """Importacion manual: busca medios en toda la carpeta (tarjeta SD,
        DCIM, o una carpeta cualquiera), sin exigir la estructura de GoPro."""
        exts = tuple(e.lower() for e in extensions)
        found = []
        if not os.path.isdir(root_dir):
            return None
        for root, dirs, names in os.walk(root_dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith((".", "$")) and d.upper() != "SYSTEM VOLUME INFORMATION")
            for n in sorted(names):
                if n.startswith(".") or not n.lower().endswith(exts):
                    continue
                full = os.path.join(root, n)
                try:
                    found.append((full, n, os.path.getsize(full)))
                except OSError:
                    continue
        return found

    @staticmethod
    def _sd_aux_files(src):
        """Archivos auxiliares de un video en la tarjeta: .LRV (proxy de baja
        resolucion) y .THM (miniatura). Los capitulos GP01xxxx usan GL01xxxx.LRV."""
        folder, fname = os.path.split(src)
        base = os.path.splitext(fname)[0]
        candidates = [base + ".LRV", base + ".THM"]
        if base.upper().startswith("GP") and len(base) > 2:
            candidates.append("GL" + base[2:] + ".LRV")
        try:
            present = {n.upper(): n for n in os.listdir(folder)}
        except OSError:
            return []
        return [os.path.join(folder, present[c.upper()]) for c in candidates if c.upper() in present]

    def _run_from_sd(self, sd_root):
        extensions = MEDIA_EXTENSIONS_BY_MODE[self.sync_mode]
        items = self._list_sd_media(sd_root, extensions)
        if items is not None and self.only_paths is not None:
            items = [it for it in items if os.path.normcase(os.path.abspath(it[0])) in self.only_paths]
        if items is None:
            self.finished_sync.emit(False, tr(self.lang, "sd_not_readable", root=sd_root), {})
            return
        total = len(items)
        if total == 0:
            empty_msg = {
                "all": tr(self.lang, "no_new_media"),
                "pictures": tr(self.lang, "no_new_pictures"),
                "videos": tr(self.lang, "no_new_videos"),
            }[self.sync_mode]
            self.finished_sync.emit(True, empty_msg, {"total": 0, "copied": 0, "deleted": 0, "errors": 0})
            return

        try:
            os.makedirs(self.target_dir, exist_ok=True)
        except Exception:
            pass

        existing_index = {}
        for root, _dirs, files in os.walk(self.target_dir):
            for f in files:
                if f.endswith(".part"):
                    _remove_stale_part(os.path.join(root, f))
                    continue
                index_existing(existing_index, f, os.path.join(root, f))

        # Contar solo lo que hay que copiar: lo que ya esta no entra en el
        # progreso ("1 de 15", no "1 de 177").
        already = 0
        to_delete = []
        pending = []
        for it in items:
            if self._already_there(existing_index, it[1], it[2]):
                already += 1
                if self.delete_after:
                    to_delete.append(it[0])
            else:
                pending.append(it)
        items = pending
        total = len(items)
        self.plan.emit([sz for _s, _n, sz in items])
        copied = errors = deleted = 0
        cam_folder = quik_camera_folder(self.target_dir, self.device_name,
                                        [p for paths in existing_index.values() for p in paths])

        stalls = 0
        for idx, (src, name, size) in enumerate(items, start=1):
            if self._cancel:
                break
            self.progress.emit(idx, total, name, 0.0)
            try:
                result = self._import_one(src, name, size, idx, total, existing_index, cam_folder)
            except _CopyCancelled:
                break
            except Exception as e:
                # Un archivo con problemas no corta la importacion: se anota y se sigue.
                traceback.print_exc()
                self._err(name, self._reason_from_exception(e))
                result = "error"
            if result == "cancel":
                break
            if result in ("ok", "already"):
                copied += 1
                stalls = 0
                if self.delete_after:
                    to_delete.append(src)
            elif result == "error":
                errors += 1
                stalls = stalls + 1 if getattr(self, "_last_stalled", False) else 0
                self._last_stalled = False
                if stalls >= 3:
                    # Tres archivos seguidos sin respuesta: la tarjeta esta trabada.
                    self.finished_sync.emit(False, tr(self.lang, "err_card_unresponsive"),
                                            {"total": total, "copied": copied, "deleted": 0, "errors": errors,
                                             "error_list": list(self.error_list)})
                    return
            elif result == "empty":
                errors += 1

        # Borrar de la tarjeta SOLO lo que ya esta copiado y verificado (mismo
        # tamano), junto con sus .LRV/.THM, como hacia Quik.
        if self._cancel:
            self._cancelled_result(copied, total)
            return
        if self.delete_after and to_delete:
            self.progress.emit(total, total, "__CLEANUP__" + tr(self.lang, "cleaning_up"), 100.0)
            for src in to_delete:
                aux_files = self._sd_aux_files(src) if src.lower().endswith(".mp4") else []
                try:
                    os.remove(src)
                    deleted += 1
                except Exception as e:
                    print(f"[SD] No se pudo borrar {src}: {e}")
                    continue
                for aux in aux_files:
                    try:
                        os.remove(aux)
                    except Exception:
                        pass

        result_data = {"total": total, "copied": copied, "deleted": deleted, "errors": errors,
                       "already": already, "error_list": list(self.error_list)}
        msg = self._result_message(copied, total, already, deleted, errors)
        self.finished_sync.emit(errors == 0, msg, result_data)

    def _copy_via_shell(self, path_components, name, dest_name, idx, total, size, dest_path):
        copy_script = build_single_copy_script(path_components, name, self.target_dir, dest_name)
        return self._run_copy_with_progress(copy_script, idx, total, name, size, dest_path)

    def _close_wpd(self):
        if self._wpd:
            self._wpd.close()
        self._wpd = None

    def _copy_via_wpd(self, name, folder, idx, total, size, dest_path):
        """Copia un archivo de la camara por WPD. Devuelve el resultado
        ({"ok": True} / {"error": ...}) o None para usar la copia del Explorador."""
        if self._wpd is False:
            return None
        if self._wpd is None:
            cam = WpdCameraSession("HERO")
            if not cam.open(stop=lambda: self._cancel):
                cam.close()
                self._wpd = False
                print("[Sync] WPD no disponible: se usa la copia del Explorador")
                return None
            self._wpd = cam
        last = {"pct": -1.0, "t": 0.0}

        def progress(done):
            if size > 0:
                pct = min(100.0, done * 100.0 / size)
                now = time.monotonic()
                if pct - last["pct"] >= 0.5 or now - last["t"] >= 0.3:
                    last["pct"], last["t"] = pct, now
                    self.progress.emit(idx, total, name, pct)
        try:
            ok = self._wpd.copy_to(name, dest_path, folder=folder, progress=progress, cancel=lambda: self._cancel)
        except WpdError as e:
            print(f"[Sync] WPD: {name}: {e} -> copia del Explorador")
            return None
        except Exception as e:
            print(f"[Sync] WPD: {name}: {e}")
            return {"error": "timeout" if _wpd_is_timeout(e) else str(e)}
        if not ok:
            return {"error": "cancelled"}
        return {"ok": True}

    def _run_copy_with_progress(self, copy_script, idx, total, name, expected_size, dest_path):
        """Startet das Kopier-Skript im Hintergrund und ermittelt den
        Fortschritt NICHT mehr aus dessen (gepufferter) PowerShell-Pipe,
        sondern liest die Groesse der Zieldatei direkt selbst von der
        Platte (os.path.getsize) - komplett unabhaengig davon, wann/ob
        PowerShell seine Ausgabe tatsaechlich flusht.

        Hintergrund: "powershell.exe" puffert seine Standardausgabe beim
        Umleiten in eine Pipe intern in Bloecken. Egal wie viele
        Flush-Aufrufe man im PowerShell-Skript einbaut - die Zeilen kamen
        bei Python trotzdem oft erst verzoegert oder gebuendelt an, wodurch
        der Fortschrittsbalken nicht synchron zum tatsaechlichen
        Kopiervorgang lief. Die Dateigroesse auf der Platte aendert sich
        dagegen in Echtzeit, ganz ohne Umweg ueber PowerShells Ausgabe-Puffer
        - das ist exakt der Wert, den auch der native Windows-Kopierdialog
        anzeigt. Gibt (result_dict_oder_None, timed_out) zurueck."""
        try:
            proc = subprocess.Popen(
                ['powershell', '-NoProfile', '-Command', copy_script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                startupinfo=_hidden_startupinfo()
            )
        except Exception:
            return None, False

        start_time = time.monotonic()
        # Gleicher grosszuegiger Gesamt-Timeout wie vorher - schuetzt
        # weiterhin vor einem komplett haengenden USB/MTP-Zustand.
        hard_timeout_seconds = 2000
        last_emitted_pct = -1.0
        # Kurzes Poll-Intervall (100ms) fuer einen fluessig wirkenden,
        # wirklich live mitlaufenden Balken.
        poll_interval = 0.1

        # HWND des Kopierdialogs bzw. seines Fortschrittsbalkens - werden
        # einmal gefunden und dann bis Kopierende wiederverwendet (kein
        # staendiges erneutes EnumWindows noetig).
        native_dialog_hwnd = None
        native_progressbar_hwnd = None
        # Solange wir den nativen Dialog noch nicht gefunden haben, suchen
        # wir hoechstens ein paar Sekunden danach - taucht er in dieser Zeit
        # nicht auf (z.B. sehr kleine Datei, die "durchrutscht", oder ein
        # System, auf dem der Dialog aus welchem Grund auch immer nicht
        # erscheint), faellt der Code auf die alte, ungenaue Dateigroessen-
        # Schaetzung zurueck, statt gar keinen Fortschritt mehr zu zeigen.
        give_up_native_search_after = 4.0

        while True:
            if proc.poll() is not None:
                break
            if self._cancel:
                # Cancelar: cortar la copia en curso y descartar el archivo a medias.
                try:
                    proc.kill()
                except Exception:
                    pass
                for _ in range(20):
                    try:
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                        break
                    except OSError:
                        time.sleep(0.1)
                return None, False

            pct = None

            # Schritt 1: Dialogfenster suchen/wiederverwenden.
            if native_dialog_hwnd is not None and not _user32.IsWindow(native_dialog_hwnd):
                native_dialog_hwnd = None
                native_progressbar_hwnd = None

            if native_dialog_hwnd is None and (time.monotonic() - start_time) <= give_up_native_search_after:
                native_dialog_hwnd = _find_copy_dialog_hwnd()

            if HIDE_NATIVE_COPY_DIALOG and native_dialog_hwnd is not None:
                _hide_native_copy_dialog(native_dialog_hwnd)

            # Schritt 2: falls Dialog gefunden, echten Fortschritt auslesen -
            # zuerst per klassischem Steuerelement (schnell, kein Zusatz-
            # paket noetig), sonst per UI Automation (falls "pywinauto"
            # installiert ist).
            if native_dialog_hwnd is not None:
                if native_progressbar_hwnd is None:
                    native_progressbar_hwnd = _find_progressbar_child(native_dialog_hwnd)

                if native_progressbar_hwnd is not None:
                    pct = _read_progressbar_percent(native_progressbar_hwnd)
                    if pct is None:
                        native_progressbar_hwnd = None

                if pct is None and native_progressbar_hwnd is None:
                    pct = _read_progress_via_uia(native_dialog_hwnd)

            if pct is None:
                # Fallback: alte, ungenaue Methode ueber die Dateigroesse auf
                # der Platte. Liefert bei MTP-Kopien (Vorallokierung) zwar
                # frueh hohe Werte, ist aber immer noch besser als gar kein
                # Fortschritt, falls der native Dialog nicht gefunden wurde.
                try:
                    if expected_size > 0 and os.path.exists(dest_path):
                        cur_bytes = os.path.getsize(dest_path)
                        pct = max(0.0, min(100.0, (cur_bytes / expected_size) * 100.0))
                except Exception:
                    pct = None

            if pct is not None and pct != last_emitted_pct:
                self.progress.emit(idx, total, name, pct)
                last_emitted_pct = pct

            if time.monotonic() - start_time > hard_timeout_seconds:
                try:
                    proc.kill()
                except Exception:
                    pass
                return None, True

            time.sleep(poll_interval)

        # Prozess ist fertig - jetzt (und erst jetzt) die komplette
        # gepufferte Ausgabe in einem Rutsch einlesen, um die finale
        # "RESULT:{...}"-Zeile zu bekommen. Fuer den Fortschritt selbst
        # wurde diese Pipe oben bewusst nicht mehr gebraucht.
        try:
            stdout_text = proc.stdout.read() or ""
        except Exception:
            stdout_text = ""

        result_line = None
        for line in stdout_text.splitlines():
            line = line.strip()
            if line.startswith("RESULT:"):
                result_line = line

        if result_line is None:
            return None, False
        try:
            return json.loads(result_line.split(":", 1)[1]), False
        except Exception:
            return None, False

    def run(self):
        SYNC_IN_PROGRESS.set()
        try:
            self._run()
        finally:
            SYNC_IN_PROGRESS.clear()

    def _run(self):
        if self.sd_root:
            try:
                self._run_from_sd(self.sd_root)
            except Exception as e:
                traceback.print_exc()
                self.finished_sync.emit(False, tr(self.lang, "sync_failed_detail", detail=str(e)[:300]), {})
            return
        # --- Phase 1: einmalig und schnell auflisten ---
        extensions = MEDIA_EXTENSIONS_BY_MODE[self.sync_mode]
        list_script = build_list_script(extensions)
        try:
            list_proc = subprocess.run(
                ['powershell', '-NoProfile', '-Command', list_script],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                startupinfo=_hidden_startupinfo(), timeout=40
            )
        except Exception as e:
            self.finished_sync.emit(False, tr(self.lang, "ps_start_failed", e=e), {})
            return

        payload = _extract_result_json(list_proc.stdout)
        if payload is None:
            detail = (list_proc.stdout or list_proc.stderr or "").strip()[:300]
            if detail:
                self.finished_sync.emit(False, tr(self.lang, "sync_failed_detail", detail=detail), {})
            else:
                self.finished_sync.emit(False, tr(self.lang, "sync_failed_no_response"), {})
            return

        if isinstance(payload, dict) and "error" in payload:
            messages = {
                "not_found": tr(self.lang, "gopro_not_found"),
                "no_namespace": tr(self.lang, "no_device_list"),
            }
            self.finished_sync.emit(False, messages.get(payload["error"], tr(self.lang, "unknown_error")), {})
            return

        # PowerShell/ConvertTo-Json liefert bei genau einem Treffer ein
        # einzelnes Objekt statt eines Arrays - hier vereinheitlichen.
        if isinstance(payload, dict):
            items = [payload]
        else:
            items = payload or []
        if self.only_keys is not None:
            items = [e for e in items if camera_item_key(e) in self.only_keys]

        total = len(items)

        if total == 0:
            empty_msg = {
                "all": tr(self.lang, "no_new_media"),
                "pictures": tr(self.lang, "no_new_pictures"),
                "videos": tr(self.lang, "no_new_videos"),
            }[self.sync_mode]
            self.finished_sync.emit(True, empty_msg, {"total": 0, "copied": 0, "deleted": 0, "errors": 0})
            return

        try:
            os.makedirs(self.target_dir, exist_ok=True)
        except Exception:
            pass

        copied = 0
        errors = 0
        # Sammelt (Ordnerpfad, Dateiname) fuer alles, was in DIESEM Durchlauf
        # frisch kopiert wurde - wird erst NACH der kompletten Kopierschleife
        # in einem Rutsch von der Kamera geloescht (siehe Phase 4 unten).
        to_delete_by_folder = {}

        # Einmalig ALLE bereits lokal vorhandenen Dateien erfassen (auch in
        # Datums-Unterordnern von frueheren Syncs) - so lassen sich schon
        # synchronisierte Dateien per Name+Groesse erkennen.
        existing_index = {}
        for root, _dirs, files in os.walk(self.target_dir):
            for f in files:
                if f.endswith(".part"):
                    _remove_stale_part(os.path.join(root, f))
                    continue
                index_existing(existing_index, f, os.path.join(root, f))
        cam_folder = quik_camera_folder(self.target_dir, self.device_name,
                                        [p for paths in existing_index.values() for p in paths])

        # Contar solo lo que hay que copiar ("1 de 15", no "1 de 177"); lo que
        # ya esta se borra de la camara igual si esta activado "borrar despues".
        already = 0
        pending = []
        for e in items:
            nm, sz = e.get("name") or "", e.get("size") or 0
            if sz and self._already_there(existing_index, nm, sz):
                already += 1
                if self.delete_after:
                    folder = tuple(x for x in (e.get("path") or "").split("|") if x)
                    to_delete_by_folder.setdefault(folder, []).append(nm)
            else:
                pending.append(e)
        items = pending
        total = len(items)
        self.plan.emit([int(e.get("size") or 0) for e in items])

        # --- Phase 2 + 3: pro Datei einzeln vergleichen und ggf. kopieren ---
        for idx, entry in enumerate(items, start=1):
            if self._cancel:
                break
            name = entry.get("name") or ""
            size = entry.get("size") or 0
            path_str = entry.get("path") or ""
            path_components = [p for p in path_str.split("|") if p]
            folder_key = tuple(path_components)

            # 0.0 = Fortschrittsbalken springt fuer JEDE Datei neu auf 0%
            # zurueck, statt (wie vorher) einfach ueber current/total ALLER
            # Dateien hinweg langsam weiterzuwachsen.
            self.progress.emit(idx, total, name, 0.0)

            dest_name = name
            skip_copy = False

            # Ist diese Datei (gleicher Name + gleiche Groesse) schon
            # IRGENDWO im Zielordner vorhanden - egal ob direkt im Root
            # oder in einem Datums-Unterordner von einem frueheren Sync?
            for existing_path in existing_candidates(existing_index, name):
                try:
                    if size and os.path.getsize(existing_path) == size:
                        skip_copy = True
                        break
                except Exception:
                    pass

            if not skip_copy and size == 0:
                # Archivo vacio en la camara (grabacion cortada): pedirlo por MTP
                # puede dejar la camara colgada, y no hay nada que copiar.
                errors += 1
                self._err(name, tr(self.lang, "err_empty_file"))
                continue
            if skip_copy:
                copied += 1
                self.progress.emit(idx, total, name, 100.0)
                if self.delete_after:
                    # Datei ist zwar schon lokal vorhanden (Duplikat-Check),
                    # war aber deshalb noch nicht in dieser Liste - sie
                    # steht ja trotzdem noch auf der Kamera und soll bei
                    # aktiviertem "Nach Uebertragung loeschen" ebenfalls
                    # entfernt werden. Vorher wurde das hier komplett
                    # uebersprungen ("continue"), wodurch bereits
                    # synchronisierte Dateien NIE geloescht wurden, selbst
                    # wenn der Schalter an war.
                    to_delete_by_folder.setdefault(folder_key, []).append(name)
                continue

            # Kollisionsfreien Namen fuer den (immer flachen) Zwischen-Copy
            # ins Root des Zielordners waehlen - die Datei wird direkt im
            # Anschluss anhand ihres Aufnahmedatums in einen YYYY-MM-DD-
            # Unterordner verschoben (siehe unten).
            root_dest_path = os.path.join(self.target_dir, dest_name)
            if os.path.exists(root_dest_path):
                base, ext = os.path.splitext(name)
                counter = 1
                while True:
                    candidate = f"{base}_{counter}{ext}"
                    candidate_path = os.path.join(self.target_dir, candidate)
                    if not os.path.exists(candidate_path):
                        dest_name = candidate
                        root_dest_path = candidate_path
                        break
                    counter += 1

            # Primero por WPD: directo, sin la ventana "Copiando..." de Windows
            # (el Explorador la muestra siempre con la camara) y con progreso real.
            copy_result = self._copy_via_wpd(name, path_components[-1] if path_components else None,
                                             idx, total, size, root_dest_path)
            if self._cancel:
                break
            if copy_result is not None:
                timed_out = False
            else:
                copy_result, timed_out = self._copy_via_shell(path_components, name, dest_name, idx, total, size,
                                                              root_dest_path)
            if self._cancel:
                break
            if timed_out:
                # Sollte dank des hohen Timeouts praktisch nie mehr passieren -
                # falls doch (z.B. USB-Verbindung haengt komplett), zaehlt die
                # Datei als Fehler, der Sync laeuft aber mit der naechsten
                # Datei weiter statt komplett abzubrechen.
                errors += 1
                self._err(name, tr(self.lang, "err_timeout"))
                continue
            if copy_result is None:
                errors += 1
                self._err(name, tr(self.lang, "err_no_response"))
                continue

            # Balken dieser Datei sicher auf 100% abschliessen, auch falls
            # die letzte PROGRESS-Zeile knapp unter der erwarteten Groesse
            # lag (z.B. minimal abweichende Dateisystem-Metadaten).
            self.progress.emit(idx, total, name, 100.0)
            if isinstance(copy_result, dict) and copy_result.get("ok"):
                copied += 1
                final_path = root_dest_path
                camera_dt = _parse_camera_dt(entry.get("dt"))
                if camera_dt is not None:
                    set_file_times(root_dest_path, camera_dt)

                if self.sort_into_date_folders and os.path.exists(root_dest_path):
                    try:
                        capture_dt = camera_dt or get_media_capture_date(root_dest_path, mvhd_local=self.mvhd_local)
                        date_folder = capture_dt.strftime("%Y-%m-%d")
                        dated_dir = os.path.join(self.target_dir, date_folder)
                        if cam_folder:
                            dated_dir = os.path.join(dated_dir, cam_folder)
                        os.makedirs(dated_dir, exist_ok=True)
                        target_path = os.path.join(dated_dir, dest_name)
                        if os.path.exists(target_path) and replaceable_broken_copy(target_path, size):
                            print(f"[Sync] Reemplazando copia danada: {target_path}")
                            os.replace(root_dest_path, target_path)
                            final_path = target_path
                            root_dest_path = None
                        elif os.path.exists(target_path):
                            # Sehr seltener Kollisionsfall (z.B. gleicher
                            # Dateiname taucht am selben Tag zweimal auf).
                            base, ext = os.path.splitext(dest_name)
                            counter = 1
                            while os.path.exists(target_path):
                                target_path = os.path.join(dated_dir, f"{base}_{counter}{ext}")
                                counter += 1
                        if root_dest_path:
                            shutil.move(root_dest_path, target_path)
                        final_path = target_path
                    except Exception:
                        # Verschieben fehlgeschlagen (z.B. Rechteproblem) -
                        # Datei bleibt einfach flach im Zielordner-Root
                        # liegen, statt den Sync abzubrechen.
                        final_path = root_dest_path or final_path

                # Damit ein und dieselbe Datei (z.B. bei zwei identisch
                # benannten Eintraegen im selben Sync-Lauf) nicht doppelt
                # heruntergeladen wird, den Index sofort aktualisieren.
                index_existing(existing_index, os.path.basename(final_path), final_path)

                if self.delete_after:
                    to_delete_by_folder.setdefault(folder_key, []).append(name)
            else:
                errors += 1
                err = copy_result.get("error") if isinstance(copy_result, dict) else None
                self._err(name, tr(self.lang, "err_camera_error", e=err) if err else tr(self.lang, "err_unknown"))

        # --- Phase 4: JETZT ERST, nach Abschluss ALLER Kopien, gesammelt
        # von der Kamera loeschen (ein Bestaetigungsdialog pro Unterordner
        # statt einer pro Datei) ---
        self._close_wpd()
        deleted = 0
        if self._cancel:
            self._cancelled_result(copied, total)
            return
        if self.delete_after and to_delete_by_folder:
            self.progress.emit(total, total, "__CLEANUP__" + tr(self.lang, "cleaning_up"), 100.0)
            groups = [{"path": list(folder), "names": names} for folder, names in to_delete_by_folder.items()]
            delete_script = build_batch_delete_script(groups)

            # Kurze Verschnaufpause fuer die Kamera: direkt nach einer
            # groesseren Kopie (v.a. bei vielen/grossen Videos) antwortet
            # der MTP-Responder der GoPro manchmal noch kurz nicht richtig
            # ("Ein an das System angeschlossenes Geraet funktioniert
            # nicht.") - eine kleine Pause davor macht das zuverlaessiger.
            time.sleep(1.5)

            # Bis zu 2 Versuche: MTP-Verbindungen zu GoPro-Kameras sind
            # unter Windows bekanntermassen gelegentlich flackerig gerade
            # beim Loeschen - ein zweiter Versuch nach kurzer Pause behebt
            # das haeufig, ohne dass man den ganzen Sync neu anstossen muss.
            for attempt in range(2):
                try:
                    delete_proc = subprocess.run(
                        ['powershell', '-NoProfile', '-Command', delete_script],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        # WICHTIG: HIER bewusst OHNE _hidden_startupinfo()!
                        # Das Loeschen ueber die Shell (InvokeVerbEx) zeigt
                        # einen nativen Windows-Bestaetigungsdialog ("X
                        # Elemente endgueltig loeschen?"). Lief das
                        # PowerShell-Fenster versteckt, bekam dieser Dialog
                        # offenbar keinen richtigen Fokus/keine Sichtbarkeit
                        # auf dem Desktop - das Skript blieb dann unsichtbar
                        # haengen. Ohne verstecktes Fenster bekommt der
                        # Dialog normale Sichtbarkeit/Fokus.
                        timeout=1800
                    )
                    delete_result = _extract_result_json(delete_proc.stdout)
                    if isinstance(delete_result, dict):
                        deleted = delete_result.get("deleted", 0) or 0
                    if deleted > 0:
                        break
                except Exception:
                    pass
                if attempt == 0 and deleted == 0:
                    time.sleep(2.0)

        result_data = {"total": total, "copied": copied, "deleted": deleted, "errors": errors,
                       "already": already, "error_list": list(self.error_list)}
        msg = self._result_message(copied, total, already, deleted, errors)

        self.finished_sync.emit(errors == 0, msg, result_data)


# --- [ UI KOMPONENTEN ] ---

class CameraImageView(QWidget):
    """Foto/ilustracion de la camara (modelos sin video propio, p. ej. HERO5),
    recortada tipo "cover" con esquinas redondeadas y nitida en HiDPI."""

    def __init__(self, radius=24, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._source = QPixmap()
        self._scaled = None

    def set_image(self, path):
        self._source = QPixmap(path) if path and os.path.exists(path) else QPixmap()
        self._scaled = None
        self.update()

    def resizeEvent(self, event):
        self._scaled = None
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor(20, 20, 26))
        if not self._source.isNull():
            if self._scaled is None:
                self._scaled = cover_scaled_pixmap(self._source, self.size(), screen_dpr(self))
            painter.drawPixmap(0, 0, self._scaled)


class RoundedVideoContainer(QWidget):
    """
    Neue Implementierung: rendert Video über QGraphicsVideoItem in einer QGraphicsView.
    Die View bekommt eine abgerundete Maske, sodass das Video exakt runde Ecken hat.
    """
    def __init__(self, parent=None, radius=20):
        super().__init__(parent)
        self.radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setContentsMargins(0, 0, 0, 0)

        # GraphicsView / Scene / VideoItem
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setStyleSheet("background: transparent; border: none;")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

        # QGraphicsVideoItem zum Rendern des Videos
        self.video_item = QGraphicsVideoItem()
        self.video_item.setSize(self.size())
        self.scene.addItem(self.video_item)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        # --- Freeze-Frame-Overlay -------------------------------------------
        # Liegt UEBER der View und zeigt (wenn aktiviert) einen echten
        # Screenshot des letzten Videoframes. Damit ist es egal, ob das
        # Player-Backend beim Pausieren ganz am Ende manchmal kurz einen
        # grauen/leeren Frame anzeigt - der wird dann einfach verdeckt.
        self.freeze_label = QLabel(self)
        self.freeze_label.setScaledContents(True)
        self.freeze_label.setStyleSheet("background: transparent; border: none;")
        self.freeze_label.hide()

    def show_freeze_frame(self, pixmap):
        """Legt einen Screenshot ueber das Video (siehe Klassenkommentar)."""
        if pixmap is None or pixmap.isNull():
            return
        self.freeze_label.setPixmap(pixmap)
        self.freeze_label.setGeometry(0, 0, self.width(), self.height())
        try:
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width(), self.height(), self.radius, self.radius)
            self.freeze_label.setMask(QRegion(path.toFillPolygon().toPolygon()))
        except Exception:
            pass
        self.freeze_label.raise_()
        self.freeze_label.show()

    def hide_freeze_frame(self):
        self.freeze_label.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()

        # View und Scene anpassen
        self.view.setGeometry(0, 0, w, h)
        self.scene.setSceneRect(0, 0, w, h)

        # VideoItem auf die Scene-Größe setzen (Video füllt den Bereich)
        try:
            self.video_item.setSize(self.scene.sceneRect().size())
            self.video_item.setPos(0, 0)
        except Exception:
            pass

        # Abgerundete Maske für die gesamte View/Widget
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, self.radius, self.radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        try:
            self.setMask(region)
        except Exception:
            pass
        try:
            self.view.viewport().setMask(region)
        except Exception:
            pass
        if self.freeze_label.isVisible():
            self.freeze_label.setGeometry(0, 0, w, h)
            try:
                self.freeze_label.setMask(region)
            except Exception:
                pass

    def paintEvent(self, event):
        # Leichtes Hintergrund-Fill, damit die Ecken sauber aussehen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        painter.fillPath(path, QColor(20, 20, 30))
        painter.end()


class VideoFrameView(QWidget):
    """Zeigt Videoframes an, die per QVideoSink hereinkommen - entweder ganz
    normal ("flat", flächenfüllend/letterboxed) oder als 360-Grad-Pan-Ansicht
    ("360"): dabei wird nur ein 16:9-Ausschnitt aus dem equirektangulären
    Frame gezeigt, durch den man per Ziehen (Maus) waagerecht (umlaufend,
    da ein 360-Video horizontal nahtlos ist) und senkrecht (begrenzt) scrollen
    kann. Mausrad = Hineinzoomen (schmaleres Sichtfeld, mehr Spielraum zum
    senkrechten Schwenken).

    Wird sowohl im normalen Video-Player als auch im Verbindungs-Vorschau-
    Bereich (fuer GoPro MAX / MAX 2) verwendet - EIN Widget, zwei Modi.
    """

    ZOOM_MIN = 1.0
    ZOOM_MAX = 4.0

    def __init__(self, radius=0, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.mode = "flat"          # "flat" oder "360"
        self._frame = None          # aktuelles QImage (volle Aufloesung)
        self._pan_x = 0.5           # 0..1, umlaufend (wrap)
        self._pan_y = 0.5           # 0..1, wird je nach Zoom geclamped
        self._zoom = self.ZOOM_MIN
        self._dragging = False
        self._drag_last = QPointF()
        self.setMinimumSize(120, 68)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet("background: transparent;")

    # -- oeffentliche API -------------------------------------------------
    def set_mode(self, mode):
        if mode not in ("flat", "360"):
            return
        self.mode = mode
        self.setCursor(Qt.CursorShape.OpenHandCursor if mode == "360" else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_frame(self, qimage):
        if qimage is None or qimage.isNull():
            return
        self._frame = qimage
        self.update()

    def clear_frame(self):
        self._frame = None
        self.update()

    def capture_native(self):
        """Gibt das aktuell sichtbare Bild als QImage in voller Aufloesung
        zurueck (fuer den 'Als Foto speichern'-Button) - im 360-Modus genau
        der Ausschnitt, der gerade zu sehen ist, sonst der volle Frame."""
        if self._frame is None:
            return None
        if self.mode != "360":
            return QImage(self._frame)
        img_w, img_h = self._frame.width(), self._frame.height()
        crop_w, crop_h, left, top = self._crop_geometry(img_w, img_h)
        return self._composite_crop(self._frame, left, top, crop_w, crop_h)

    # -- interne Geometrie --------------------------------------------------
    def _crop_geometry(self, img_w, img_h):
        """Berechnet Breite/Hoehe/linke-obere-Ecke (in Bildpixeln) des
        aktuell sichtbaren Ausschnitts, passend zum Seitenverhaeltnis des
        Widgets (im Player standardmaessig 16:9)."""
        wgt_w = max(1, self.width())
        wgt_h = max(1, self.height())
        aspect = wgt_w / wgt_h

        # Bei ZOOM_MIN soll die volle Bildhoehe sichtbar sein (kompletter
        # Schwenkbereich oben/unten nur beim Reinzoomen).
        crop_h = img_h / self._zoom
        crop_w = crop_h * aspect
        if crop_w > img_w:
            crop_w = img_w
            crop_h = crop_w / aspect
        crop_h = min(crop_h, img_h)

        center_x = self._pan_x * img_w
        # vertikaler Schwenkbereich clampen, damit der Ausschnitt nie ueber
        # den oberen/unteren Bildrand hinausragt
        min_center_y = crop_h / 2
        max_center_y = img_h - crop_h / 2
        center_y = min_center_y + self._pan_y * max(0.0, max_center_y - min_center_y)
        center_y = max(min_center_y, min(max_center_y, center_y))

        left = center_x - crop_w / 2
        top = center_y - crop_h / 2
        return crop_w, crop_h, left, top

    def _composite_crop(self, img, left, top, crop_w, crop_h):
        """Schneidet ein crop_w x crop_h grosses Stueck aus img aus, beginnend
        bei (left, top) - left darf negativ sein oder ueber img.width()
        hinausgehen, dann wird nahtlos vom jeweils anderen Bildrand
        weitergezeichnet (die 360-Aufnahme ist horizontal umlaufend)."""
        img_w, img_h = img.width(), img.height()
        out = QImage(max(1, round(crop_w)), max(1, round(crop_h)), QImage.Format.Format_ARGB32)
        out.fill(Qt.GlobalColor.black)
        painter = QPainter(out)
        left_wrapped = left % img_w
        remaining = crop_w
        x_cursor = 0.0
        src_x = left_wrapped
        # In maximal 2 Stuecken zeichnen (Wrap-Around an der 0/img_w-Naht)
        for _ in range(2):
            if remaining <= 0:
                break
            seg_w = min(remaining, img_w - src_x)
            src_rect = QRectF(src_x, top, seg_w, crop_h)
            dst_rect = QRectF(x_cursor, 0, seg_w, crop_h)
            painter.drawImage(dst_rect, img, src_rect)
            x_cursor += seg_w
            remaining -= seg_w
            src_x = 0.0
        painter.end()
        return out

    # -- Zeichnen -------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.radius > 0:
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width(), self.height(), self.radius, self.radius)
            try:
                self.setMask(QRegion(path.toFillPolygon().toPolygon()))
            except Exception:
                pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(10, 10, 14))

        if self._frame is not None:
            if self.mode == "flat":
                dpr = screen_dpr(self)
                scaled = self._frame.scaled(int(rect.width() * dpr), int(rect.height() * dpr),
                                             Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                sz = scaled.deviceIndependentSize()
                x = (rect.width() - sz.width()) / 2
                y = (rect.height() - sz.height()) / 2
                painter.drawImage(QPointF(x, y), scaled)
            else:
                img_w, img_h = self._frame.width(), self._frame.height()
                crop_w, crop_h, left, top = self._crop_geometry(img_w, img_h)
                cropped = self._composite_crop(self._frame, left, top, crop_w, crop_h)
                painter.drawImage(rect, cropped)

        painter.end()

    # -- Maus-Interaktion (nur 360-Modus) --------------------------------
    def mousePressEvent(self, event):
        if self.mode == "360" and self._frame is not None:
            self._dragging = True
            self._drag_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self.mode == "360" and self._frame is not None:
            img_w, img_h = self._frame.width(), self._frame.height()
            crop_w, crop_h, _left, _top = self._crop_geometry(img_w, img_h)
            pos = event.position()
            dx = pos.x() - self._drag_last.x()
            dy = pos.y() - self._drag_last.y()
            self._drag_last = pos
            wgt_w = max(1, self.width())
            wgt_h = max(1, self.height())
            self._pan_x = (self._pan_x - (dx / wgt_w) * (crop_w / img_w)) % 1.0
            min_center_y = crop_h / 2
            max_center_y = img_h - crop_h / 2
            span = max(1.0, max_center_y - min_center_y)
            self._pan_y = min(1.0, max(0.0, self._pan_y - dy / wgt_h * (crop_h / span)))
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        if self.mode == "360":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self.mode == "360" and self._frame is not None:
            factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
            self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom * factor))
            self.update()
            event.accept()
        else:
            super().wheelEvent(event)


class Toast(QLabel):
    """Kleine, sich selbst wieder ausblendende Benachrichtigung (z.B. fuer
    'In Galerie gespeichert'), die oben ueber einem Parent-Widget schwebt."""
    def __init__(self, parent, text):
        super().__init__(text, parent)
        self.setStyleSheet("""
            QLabel {
                background: rgba(20, 20, 26, 0.92);
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                padding: 10px 18px;
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.14);
            }
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.adjustSize()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)

    def show_animated(self, duration_ms=1800):
        parent = self.parentWidget()
        if parent is not None:
            x = (parent.width() - self.width()) // 2
            y = parent.height() - self.height() - 28
            self.move(max(0, x), max(0, y))
        self.show()
        self.raise_()
        fade_in = QPropertyAnimation(self._effect, b"opacity", self)
        fade_in.setDuration(180)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_in_ref = fade_in

        def start_fade_out():
            fade_out = QPropertyAnimation(self._effect, b"opacity", self)
            fade_out.setDuration(400)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.finished.connect(self.deleteLater)
            fade_out.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._fade_out_ref = fade_out

        QTimer.singleShot(duration_ms, start_fade_out)


def show_toast(parent_widget, text):
    """Zeigt eine kurze 'Toast'-Benachrichtigung ueber parent_widget an."""
    try:
        toast = Toast(parent_widget, text)
        toast.show_animated()
    except Exception:
        traceback.print_exc()


def save_captured_photo(gallery_dir, qimage):
    """Speichert ein aufgenommenes Video-Frame (QImage) als PNG im Gallery-
    Zielordner (derselbe Ordner, den auch die Bilder-Galerie durchsucht -
    das Foto taucht also automatisch dort mit auf). Gibt den Zielpfad
    zurueck oder None bei einem Fehler."""
    try:
        os.makedirs(gallery_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"Frame_{stamp}.png"
        dest = os.path.join(gallery_dir, filename)
        counter = 1
        while os.path.exists(dest):
            filename = f"Frame_{stamp}_{counter}.png"
            dest = os.path.join(gallery_dir, filename)
            counter += 1
        if qimage.save(dest, "PNG"):
            return dest
        return None
    except Exception:
        traceback.print_exc()
        return None


class WaveProgressBar(QWidget):
    def __init__(self, show_battery_nub=False, parent=None):
        super().__init__(parent)
        self.show_battery_nub = show_battery_nub
        self.setFixedHeight(26)
        self.setMinimumWidth(70)
        self._value = -1
        self._display_value = 0.0
        self._color = QColor("#00cc66")
        
        self._anim = QPropertyAnimation(self, b"displayValue", self)
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self._wave_phase = 0.0
        self._wave_timer = QTimer(self)
        self._wave_timer.timeout.connect(self._update_wave)
        self._wave_timer.start(30)

    def _update_wave(self):
        self._wave_phase += 0.15
        if self._value >= 0:
            self.update()

    def _get_display_value(self): return self._display_value
    def _set_display_value(self, v):
        self._display_value = v
        self.update()
    displayValue = Property(float, _get_display_value, _set_display_value)

    def set_value_and_color(self, value, hex_color):
        try: val = int(value)
        except Exception: val = -1
        self._value = val
        self._color = QColor(hex_color)
        
        target = float(max(2, min(100, val))) if val >= 0 else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._display_value)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        
        if self.show_battery_nub:
            nub_w = max(4, h * 0.16)
            body_rect = QRectF(1, 2, w - nub_w - 3, h - 4)
            nub_rect = QRectF(body_rect.right() + 2, h * 0.28, nub_w, h * 0.44)
            painter.setPen(Qt.PenStyle.NoPen)
            nub_path = QPainterPath()
            nub_path.addRoundedRect(nub_rect, 2, 2)
            painter.fillPath(nub_path, QColor("#40404c"))
        else:
            body_rect = QRectF(1, 2, w - 2, h - 4)

        body_path = QPainterPath()
        body_path.addRoundedRect(body_rect, 7, 7)
        painter.fillPath(body_path, QColor(255, 255, 255, 15))
        painter.setPen(QColor(255, 255, 255, 30))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(body_path)

        if self._value < 0:
            painter.setPen(QColor("#ffffff")) 
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(body_rect, Qt.AlignmentFlag.AlignCenter, "--")
            return

        pad = 3
        inner = QRectF(body_rect.left() + pad, body_rect.top() + pad,
                        body_rect.width() - 2 * pad, body_rect.height() - 2 * pad)
        
        fill_w = max(0.0, inner.width() * (self._display_value / 100.0))
        
        painter.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(inner, 5, 5)
        painter.setClipPath(clip_path)

        wave_path = QPainterPath()
        wave_path.moveTo(inner.left(), inner.bottom())
        
        amplitude = 2.5
        for y in range(int(inner.height()), -1, -1):
            x = inner.left() + fill_w + math.sin((y * 0.3) + self._wave_phase) * amplitude
            wave_path.lineTo(x, inner.top() + y)
            
        wave_path.lineTo(inner.left(), inner.top())
        wave_path.closeSubpath()

        grad = QLinearGradient(inner.left(), 0, inner.left() + fill_w, 0)
        grad.setColorAt(0.0, self._color.darker(130))
        grad.setColorAt(1.0, self._color)
        
        painter.fillPath(wave_path, grad)
        painter.restore()

        # Draw percentage text centered over the bar
        try:
            pct_text = f"{int(round(self._display_value))}%"
        except Exception:
            pct_text = "--"
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, pct_text)


class SyncProgressButton(QWidget):
    """
    Kombinierter Button + Fortschrittsanzeige fuer den Sync.
    Im Ruhezustand sieht er aus wie ein normaler Button ("Sync starten").
    Waehrend der Uebertragung fuellt er sich wie die Akku/Speicher-Wellen-Bars
    (gleiche Wellen-Animation, siehe WaveProgressBar) und zeigt den Fortschritt in %.

    Zusaetzlich: rechter Bereich ist ein Dropdown-Pfeil, der ein Menu mit
    "Alles / Nur Bilder / Nur Videos synchronisieren" oeffnet (mode_chosen).
    Klick auf den restlichen (linken) Bereich des Buttons loest weiterhin
    ganz normal `clicked` aus (= "alles synchronisieren", bisheriges Verhalten).
    """
    clicked = Signal()
    mode_chosen = Signal(str)
    progress_requested = Signal()   # clic mientras sincroniza: reabrir la ventana de progreso

    CHEVRON_WIDTH = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(250, 55)
        self._clickable = False
        self._is_syncing = False
        self._idle_text = "Sync starten"
        self._menu_labels = {"all": "", "pictures": "", "videos": ""}   # los pone set_labels() con tr()
        self._accent_color = QColor("#00f2fe")

        self._display_progress = 0.0
        self._progress_anim = QPropertyAnimation(self, b"displayProgress", self)
        self._progress_anim.setDuration(350)
        self._progress_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._wave_phase = 0.0
        self._wave_timer = QTimer(self)
        self._wave_timer.timeout.connect(self._tick_wave)
        self._wave_timer.start(30)

        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _tick_wave(self):
        self._wave_phase += 0.15
        if self._is_syncing:
            self.update()

    def _get_display_progress(self): return self._display_progress
    def _set_display_progress(self, v):
        self._display_progress = v
        self.update()
    displayProgress = Property(float, _get_display_progress, _set_display_progress)

    def set_labels(self, idle_text, menu_all, menu_pictures, menu_videos):
        self._idle_text = idle_text
        self._menu_labels = {"all": menu_all, "pictures": menu_pictures, "videos": menu_videos}
        self.update()

    def set_theme_color(self, hex_color):
        self._accent_color = QColor(hex_color)
        self.update()

    @property
    def is_syncing(self):
        return self._is_syncing

    def set_idle(self, clickable):
        """Ruhezustand: entweder normaler klickbarer Button oder deaktiviert (kein GoPro verbunden)."""
        self._is_syncing = False
        self._clickable = clickable
        self._display_progress = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        self.update()

    def start_sync_visual(self):
        self._is_syncing = True
        self._clickable = False
        self._display_progress = 0.0
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_progress(self, percent, animate=True):
        """percent: 0-100, der Fortschritt der GERADE uebertragenen
        Einzeldatei (nicht mehr current/total ueber ALLE Dateien wie
        vorher) - so springt der Balken fuer jede neue Datei zurueck auf 0%
        und waechst dann synchron mit dem echten Kopierfortschritt, genau
        wie beim nativen Windows-Kopierdialog. animate=False setzt den
        Balken sofort (ohne die 350ms-Animation) - fuer den harten Reset
        auf 0%, wenn eine neue Datei beginnt."""
        pct = max(0.0, min(100.0, percent))
        if not animate:
            self._progress_anim.stop()
            self._display_progress = pct
            self.update()
            return
        self._progress_anim.stop()
        self._progress_anim.setStartValue(self._display_progress)
        self._progress_anim.setEndValue(pct)
        self._progress_anim.start()

    def _show_mode_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: #23232b;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                color: #ffffff;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 13px;
            }}
            QMenu::item:selected {{
                background-color: {self._accent_color.name()};
                color: #0d0d12;
            }}
        """)
        action_all = menu.addAction(self._menu_labels["all"])
        action_pics = menu.addAction(self._menu_labels["pictures"])
        action_vids = menu.addAction(self._menu_labels["videos"])
        chosen = menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
        if chosen is not None:
            SoundManager.play_click()
        if chosen == action_all:
            self.mode_chosen.emit("all")
        elif chosen == action_pics:
            self.mode_chosen.emit("pictures")
        elif chosen == action_vids:
            self.mode_chosen.emit("videos")

    def mousePressEvent(self, event):
        if self._is_syncing:
            self.progress_requested.emit()
        elif self._clickable:
            if event.position().x() >= self.width() - self.CHEVRON_WIDTH:
                SoundManager.play_click()
                self._show_mode_menu()
            else:
                SoundManager.play_click()
                self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        rect = QRectF(0, 0, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)

        if self._is_syncing:
            painter.fillPath(path, QColor(255, 255, 255, 15))
            painter.setPen(QColor(255, 255, 255, 30))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

            pad = 3
            inner = QRectF(rect.left() + pad, rect.top() + pad,
                            rect.width() - 2 * pad, rect.height() - 2 * pad)
            fill_w = max(0.0, inner.width() * (self._display_progress / 100.0))

            painter.save()
            clip_path = QPainterPath()
            clip_path.addRoundedRect(inner, 8, 8)
            painter.setClipPath(clip_path)

            def build_wave_path(phase, amplitude, freq, x_offset=0.0):
                """Baut eine weiche Wellen-Kontur per quadratischer Bezier-
                Interpolation zwischen Stuetzpunkten (statt harter
                Geradenstuecke) - wirkt deutlich fluessiger/glatter."""
                step = 4  # Abstand zwischen Stuetzpunkten in px
                points = []
                y = int(inner.height())
                while y >= 0:
                    x = inner.left() + fill_w + x_offset + math.sin((y * freq) + phase) * amplitude
                    points.append((x, inner.top() + y))
                    y -= step
                points.append((inner.left() + fill_w + x_offset + math.sin(phase) * amplitude, inner.top()))

                wp = QPainterPath()
                wp.moveTo(inner.left(), inner.bottom())
                wp.lineTo(*points[0])
                for i in range(1, len(points)):
                    prev_pt = points[i - 1]
                    cur_pt = points[i]
                    mid_x = (prev_pt[0] + cur_pt[0]) / 2
                    mid_y = (prev_pt[1] + cur_pt[1]) / 2
                    wp.quadTo(prev_pt[0], prev_pt[1], mid_x, mid_y)
                wp.lineTo(points[-1][0], points[-1][1])
                wp.lineTo(inner.left(), inner.top())
                wp.closeSubpath()
                return wp

            # Hintere Welle: leicht phasenversetzt, transparenter und etwas
            # groessere Amplitude - erzeugt den Eindruck von Tiefe (wie zwei
            # uebereinanderliegende Wasserschichten).
            back_wave = build_wave_path(self._wave_phase * 0.6 + 1.6, amplitude=3.2, freq=0.16, x_offset=-2)
            back_color = QColor(self._accent_color)
            back_color.setAlpha(90)
            painter.fillPath(back_wave, back_color)

            # Vordere (Haupt-)Welle: ruhigere, sanftere Frequenz als vorher.
            front_wave = build_wave_path(self._wave_phase, amplitude=2.2, freq=0.16)
            grad = QLinearGradient(inner.left(), 0, inner.left() + fill_w, 0)
            grad.setColorAt(0.0, self._accent_color.darker(130))
            grad.setColorAt(1.0, self._accent_color)
            painter.fillPath(front_wave, grad)
            painter.restore()

            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
            pct_text = f"{int(round(self._display_progress))}%"
            if getattr(self, "busy_label", ""):
                pct_text = f"{self.busy_label}  {pct_text}"
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, pct_text)
        else:
            if self._clickable:
                grad = QLinearGradient(0, 0, w, h)
                grad.setColorAt(0.0, self._accent_color)
                grad.setColorAt(1.0, self._accent_color.lighter(160))
                painter.fillPath(path, grad)
                painter.setPen(QColor("#0d0d12"))
            else:
                painter.fillPath(path, QColor(255, 255, 255, 12))
                painter.setPen(QColor(255, 255, 255, 90))
            painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))

            if self._clickable:
                text_rect = QRectF(0, 0, w - self.CHEVRON_WIDTH, h)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._idle_text)

                divider_x = w - self.CHEVRON_WIDTH
                painter.setPen(QColor(13, 13, 18, 70))
                painter.drawLine(QPointF(divider_x, h * 0.22), QPointF(divider_x, h * 0.78))

                # Kleiner Abwaerts-Pfeil (Dropdown-Indikator)
                cx = divider_x + self.CHEVRON_WIDTH / 2
                cy = h / 2
                caret = QPainterPath()
                caret.moveTo(cx - 5, cy - 2.5)
                caret.lineTo(cx, cy + 3.5)
                caret.lineTo(cx + 5, cy - 2.5)
                pen = QPen(QColor(13, 13, 18, 200), 2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(caret)
            else:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._idle_text)


# --- [ THUMBNAILS & GALERIEN ] ---

THUMB_W, THUMB_H = 220, 150

def make_cover_thumbnail(image, w=THUMB_W, h=THUMB_H, radius=12, dpr=1.0):
    """Schneidet/skaliert ein QImage auf ein rundes Vorschau-Thumbnail.
    Arbeitet bewusst mit QImage statt QPixmap: QImage ist (anders als
    QPixmap) auch in einem Hintergrund-Thread sicher nutzbar - das macht
    sich ThumbnailLoaderThread unten zunutze, damit das Erzeugen der
    Vorschaubilder die Oberflaeche nicht mehr blockiert."""
    if image is None or image.isNull():
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#222228"))

    # Se genera a resolucion real de pantalla (w x dpr) para que se vea nitida
    # con Windows escalado; el QImage lleva su devicePixelRatio.
    pw, ph = int(round(w * dpr)), int(round(h * dpr))
    scaled = image.scaled(pw, ph, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - pw) // 2)
    y = max(0, (scaled.height() - ph) // 2)
    cropped = scaled.copy(x, y, pw, ph)
    cropped.setDevicePixelRatio(dpr)

    result = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
    result.setDevicePixelRatio(dpr)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    painter.setClipPath(path)
    painter.drawImage(0, 0, cropped)
    painter.end()
    return result

def draw_play_badge(image):
    result = QImage(image)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    size = result.deviceIndependentSize()   # coordenadas logicas (imagen HiDPI)
    cx, cy = size.width() / 2, size.height() / 2
    r = min(size.width(), size.height()) * 0.16
    painter.setBrush(QColor(0, 0, 0, 130))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    
    triangle = QPainterPath()
    triangle.moveTo(cx - r * 0.35, cy - r * 0.55)
    triangle.lineTo(cx - r * 0.35, cy + r * 0.55)
    triangle.lineTo(cx + r * 0.6, cy)
    triangle.closeSubpath()
    painter.setBrush(QColor(255, 255, 255, 235))
    painter.drawPath(triangle)
    painter.end()
    return result


DEFAULT_THUMB_CACHE_DIR = os.path.join(CONFIG_DIR, "thumb_cache")


def set_thumb_cache_dir(path):
    ThumbnailLoaderThread.CACHE_DIR = path or DEFAULT_THUMB_CACHE_DIR


def move_thumb_cache(old_dir, new_dir):
    """Mueve las miniaturas existentes a la nueva ubicacion. Devuelve cuantas."""
    moved = 0
    os.makedirs(new_dir, exist_ok=True)
    if os.path.isdir(old_dir) and os.path.normcase(os.path.abspath(old_dir)) != os.path.normcase(os.path.abspath(new_dir)):
        for name in os.listdir(old_dir):
            src = os.path.join(old_dir, name)
            if os.path.isfile(src) and name.endswith(".jpg"):
                try:
                    shutil.move(src, os.path.join(new_dir, name))
                    moved += 1
                except Exception:
                    pass
        try:
            os.rmdir(old_dir)
        except OSError:
            pass
    return moved


# Un solo hilo lee del disco a la vez: en discos mecanicos (HDD), que las
# fotos y los videos se lean en paralelo hace que el cabezal salte sin parar.
_DISK_LOCK = threading.Lock()


def _trim_black_bars(img):
    """La miniatura EXIF de GoPro es 4:3 con bandas negras arriba/abajo
    (la foto es mas panoramica): se recortan."""
    h, w = img.height(), img.width()

    def dark(y):
        return all(QColor(img.pixel(x, y)).lightness() < 18 for x in range(0, w, max(1, w // 16)))
    top = 0
    while top < h // 3 and dark(top):
        top += 1
    bottom = h - 1
    while bottom > h * 2 // 3 and dark(bottom):
        bottom -= 1
    if top == 0 and bottom == h - 1:
        return img
    return img.copy(0, top, w, bottom - top + 1)


def read_exif_thumbnail(path, max_bytes=64 * 1024):
    """Miniatura JPEG incrustada en el EXIF (las GoPro la guardan). Solo lee
    el principio del archivo (unos KB), no la foto entera de varios MB."""
    try:
        with open(path, "rb") as f:
            head = f.read(max_bytes)
        if head[:2] != b"\xff\xd8":
            return None
        pos = 2
        while pos + 4 <= len(head):
            if head[pos] != 0xFF:
                return None
            marker = head[pos + 1]
            seg_len = struct.unpack(">H", head[pos + 2:pos + 4])[0]
            if marker == 0xE1 and head[pos + 4:pos + 10] == b"Exif\x00\x00":
                tiff = head[pos + 10:pos + 2 + seg_len]
                bo = "<" if tiff[:2] == b"II" else ">"
                ifd0 = struct.unpack(bo + "I", tiff[4:8])[0]
                n0 = struct.unpack(bo + "H", tiff[ifd0:ifd0 + 2])[0]
                ifd1 = struct.unpack(bo + "I", tiff[ifd0 + 2 + 12 * n0:ifd0 + 6 + 12 * n0])[0]
                if not ifd1:
                    return None
                n1 = struct.unpack(bo + "H", tiff[ifd1:ifd1 + 2])[0]
                off = length = None
                for i in range(n1):
                    e = tiff[ifd1 + 2 + 12 * i: ifd1 + 14 + 12 * i]
                    tag = struct.unpack(bo + "H", e[:2])[0]
                    val = struct.unpack(bo + "I", e[8:12])[0]
                    if tag == 0x0201:
                        off = val
                    elif tag == 0x0202:
                        length = val
                if off and length:
                    img = QImage.fromData(tiff[off:off + length])
                    return None if img.isNull() else _trim_black_bars(img)
                return None
            if marker in (0xDA, 0xD9):
                return None
            pos += 2 + seg_len
    except Exception:
        return None
    return None


class ThumbnailLoaderThread(QThread):
    """Erzeugt die Vorschaubilder fuer eine Galerie (Videos ODER Bilder) in
    einem Hintergrund-Thread, statt die Oberflaeche waehrend dessen
    einzufrieren - bei vielen Dateien (v.a. Videos, wo jeweils der erste
    Frame per OpenCV dekodiert werden muss) machte das bisher spuerbar
    lange nichts mehr reagierte ("15 Sekunden nach dem Einrichtungsassistenten").
    Nutzt durchgehend QImage (nicht QPixmap) - das ist die einzige Qt-
    Bildklasse, die auch ausserhalb des GUI-Threads sicher ist."""
    thumbnail_ready = Signal(str, QImage)
    finished_loading = Signal()

    def __init__(self, files, kind, parent=None, meta=None):
        super().__init__(parent)
        self.files = files
        self.meta = meta or {}     # ruta -> (tamano, fecha) ya conocidos por el listado
        self.kind = kind  # "video" oder "picture"
        self.dpr = screen_dpr()  # se lee en el hilo de la UI
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    # Cache en disco de las miniaturas: antes se regeneraban TODAS en cada
    # apertura de la galeria (decodificando cada JPG de 12 MP completo y el
    # primer fotograma de cada video). Ahora se guarda una version reducida
    # (clave = ruta + tamano + fecha) y las siguientes cargas son casi
    # instantaneas.
    CACHE_DIR = DEFAULT_THUMB_CACHE_DIR   # se cambia con set_thumb_cache_dir()
    CACHE_W, CACHE_H = THUMB_W * 2, THUMB_H * 2   # 2x: nitidas hasta 200 % de escala

    def _cache_path(self, path):
        known = self.meta.get(path) if hasattr(self, "meta") else None
        if known is not None:
            size, mtime = known            # del listado de la carpeta: sin consultar el disco
        else:
            try:
                st = os.stat(path)
            except OSError:
                return None
            size, mtime = st.st_size, st.st_mtime
        key = f"{os.path.normcase(path)}|{size}|{int(mtime)}"
        return os.path.join(self.CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".jpg")

    def _load_cached(self, path):
        cache_file = self._cache_path(path)
        if cache_file and os.path.exists(cache_file):
            img = QImage(cache_file)
            if not img.isNull():
                return img
        return None

    def _damaged_image(self):
        img = QImage(self.CACHE_W, self.CACHE_H, QImage.Format.Format_RGB32)
        img.fill(QColor("#2a1e22"))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor("#ff8a8a"))
        f = QFont()
        f.setPixelSize(int(self.CACHE_H * 0.12))
        f.setBold(True)
        p.setFont(f)
        p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, tr(getattr(self, "lang", "es"), "video_damaged"))
        p.end()
        return img

    def _load_raw(self, path):
        cache_file = self._cache_path(path)
        if self.kind == "video" and is_broken_mp4(path):
            return self._damaged_image()   # sin cache: si se repara, se regenera
        if self.kind == "video":
            raw = self._read_video_frame(path)
        else:
            # QImageReader con setScaledSize decodifica el JPEG directamente
            # a menor resolucion: mucho mas rapido que cargarlo entero.
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > self.CACHE_W * 2:
                factor = max(self.CACHE_W / size.width(), self.CACHE_H / size.height())
                reader.setScaledSize(QSize(max(1, int(size.width() * factor * 1.5)),
                                           max(1, int(size.height() * factor * 1.5))))
            raw = reader.read()
        if raw is not None and not raw.isNull():
            raw = raw.scaled(self.CACHE_W, self.CACHE_H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            if cache_file:
                try:
                    os.makedirs(self.CACHE_DIR, exist_ok=True)
                    raw.save(cache_file, "JPG", 85)
                except Exception:
                    pass
        return raw

    def _emit(self, path, raw):
        cover = make_cover_thumbnail(raw, THUMB_W, THUMB_H, dpr=self.dpr)
        if self.kind == "video":
            cover = draw_play_badge(cover)
        if not self._stop_requested:
            self.thumbnail_ready.emit(path, cover)

    def run(self):
        # 1) Lo que ya esta en cache, y para las fotos nuevas la miniatura EXIF
        #    (lee pocos KB): la galeria se llena en segundos.
        # 2) Despues, la version nitida de lo que faltaba (se guarda en cache);
        #    la miniatura se reemplaza en su lugar.
        pending = []
        # Lo que ya esta en cache se carga EN PARALELO (4 a la vez; leer y
        # decodificar JPEG libera el GIL): antes era de a uno.
        from concurrent.futures import ThreadPoolExecutor

        def load(path):
            try:
                img = self._load_cached(path)
                if img is None:
                    return path, None
                cover = make_cover_thumbnail(img, THUMB_W, THUMB_H, dpr=self.dpr)
                if self.kind == "video":
                    cover = draw_play_badge(cover)
                return path, cover
            except Exception:
                return path, None

        not_cached = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            chunk = 64
            for i in range(0, len(self.files), chunk):
                if self._stop_requested:
                    return
                for path, cover in pool.map(load, self.files[i:i + chunk]):
                    if cover is None:
                        not_cached.append(path)
                    elif not self._stop_requested:
                        self.thumbnail_ready.emit(path, cover)
        for path in not_cached:
            if self._stop_requested:
                return
            try:
                img = None
                if img is None and self.kind != "video":
                    # sin _DISK_LOCK: son pocos KB y no debe esperar a que
                    # el hilo de videos termine de leer un video entero
                    img = read_exif_thumbnail(path)
                    pending.append(path)
                elif img is None:
                    pending.append(path)
                    continue
                self._emit(path, img)
            except Exception:
                continue
        for path in pending:
            if self._stop_requested:
                return
            try:
                with _DISK_LOCK:
                    raw = self._load_raw(path)
                self._emit(path, raw)
            except Exception:
                continue
        self.finished_loading.emit()

    def _read_video_frame(self, path):
        if _load_cv2() is None:
            return None
        try:
            cap = cv2.VideoCapture(path)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return None
            h, w, ch = frame.shape
            bytes_per_line = ch * w
            return QImage(frame.data, w, h, bytes_per_line, QImage.Format.Format_BGR888).copy()
        except Exception:
            return None

class VideoPlayerDialog(QDialog):
    """Eigener Video-Player mit richtiger Steuerleiste (Play/Pause, Zeit-
    Anzeige, Sucher-Slider, Lautstaerke) - oeffnet sich beim Anklicken eines
    Videos in der Video-Galerie.

    WICHTIG (Performance): normale Videos laufen ueber das ganz normale,
    hardwarebeschleunigte QVideoWidget - genau wie vorher. Die CPU-seitige
    Frame-Konvertierung (QVideoSink -> QImage) wird NUR eingeschaltet,
    waehrend der 360-Grad-Modus tatsaechlich aktiv ist (das ist unvermeidbar,
    weil wir dafuer selbst in die Pixel eingreifen muessen, um den Ausschnitt
    zu berechnen). Fuer den 📷-Knopf im normalen Modus wird nicht dauerhaft
    mitgeschnitten, sondern nur genau EIN Frame kurz abgegriffen, wenn
    tatsaechlich auf den Knopf gedrueckt wird.

    Bei equirektangulaeren Videos (Seitenverhaeltnis ~2:1) wird automatisch
    (anhand der Aufloesung aus den Metadaten, ohne einen Frame dekodieren zu
    muessen) in den 360-Modus gewechselt; der 🌐-Knopf schaltet jederzeit
    manuell um."""
    def __init__(self, path, gallery_dir=None, lang="de", parent=None):
        super().__init__(parent)
        self.gallery_dir = gallery_dir
        self.lang = lang
        self._auto_mode_detected = False
        self.view_mode = "flat"
        self._capture_sink = None
        self.setWindowTitle(os.path.basename(path))
        self.resize(960, 600)
        self.setStyleSheet(f"QDialog {{ background: #000; }} * {{ color: #fff; font-family: {FONT_STACK}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.video_widget = QVideoWidget()      # normale Wiedergabe (hardwarebeschleunigt)
        self.pan_view = VideoFrameView(radius=0)  # nur fuer 360-Modus
        self.stack.addWidget(self.video_widget)
        self.stack.addWidget(self.pan_view)
        layout.addWidget(self.stack, 1)

        controls = QWidget()
        controls.setStyleSheet("background: #15151c;")
        c_layout = QHBoxLayout(controls)
        c_layout.setContentsMargins(16, 10, 16, 10)
        c_layout.setSpacing(12)

        def _round_btn(text, tooltip=""):
            btn = QPushButton(text)
            btn.setFixedSize(38, 38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tooltip)
            btn.setStyleSheet(
                "QPushButton { background: rgba(255,255,255,0.1); border-radius: 19px; font-size: 15px; border: none; } "
                "QPushButton:hover { background: rgba(255,255,255,0.2); } "
                "QPushButton:checked { background: rgba(0,242,254,0.35); }"
            )
            return btn

        self.btn_play = _round_btn("⏸")
        self.btn_play.clicked.connect(self.toggle_play)
        c_layout.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.7); background: transparent;")
        c_layout.addWidget(self.lbl_time)

        self.slider_seek = QSlider(Qt.Orientation.Horizontal)
        self.slider_seek.setRange(0, 0)
        self.slider_seek.sliderMoved.connect(self.on_seek)
        c_layout.addWidget(self.slider_seek, 1)

        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet("background: transparent;")
        c_layout.addWidget(vol_icon)
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setFixedWidth(110)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(80)
        self.slider_volume.valueChanged.connect(self.on_volume)
        c_layout.addWidget(self.slider_volume)

        self.btn_360 = _round_btn("🌐", tr(self.lang, "view_360"))
        self.btn_360.setCheckable(True)
        self.btn_360.clicked.connect(self.toggle_360_mode)
        c_layout.addWidget(self.btn_360)

        self.btn_capture = _round_btn("📷", "Frame speichern")
        self.btn_capture.clicked.connect(self.capture_frame)
        c_layout.addWidget(self.btn_capture)

        layout.addWidget(controls)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)

        self.pan_sink = QVideoSink(self)
        self.pan_sink.videoFrameChanged.connect(self._on_pan_frame)

        # Standardmaessig ganz normal auf das Video-Widget ausgeben -
        # exakt wie vor dem 360-Feature, also wieder mit voller Performance.
        self.player.setVideoOutput(self.video_widget)

        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.metaDataChanged.connect(self._on_metadata_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.play()

    def _on_metadata_changed(self):
        # Erkennt 360-Grad-Videos anhand der Aufloesung aus den Metadaten -
        # OHNE dafuer einen einzigen Frame dekodieren zu muessen. Deutlich
        # billiger als das dauerhafte Mitschneiden ueber einen QVideoSink.
        if self._auto_mode_detected:
            return
        try:
            meta = self.player.metaData()
            size = meta.value(QMediaMetaData.Key.Resolution)
        except Exception:
            return
        if not size or not size.isValid() or size.height() <= 0:
            return
        self._auto_mode_detected = True
        ratio = size.width() / size.height()
        # equirektangulaere 360-Videos sind ca. 2:1
        if 1.85 <= ratio <= 2.15:
            self.set_360_mode(True)

    def set_360_mode(self, enabled):
        self.view_mode = "360" if enabled else "flat"
        self.btn_360.setChecked(enabled)
        if enabled:
            self.pan_view.set_mode("360")
            self.stack.setCurrentWidget(self.pan_view)
            self.player.setVideoOutput(self.pan_sink)
        else:
            self.stack.setCurrentWidget(self.video_widget)
            self.player.setVideoOutput(self.video_widget)

    def toggle_360_mode(self):
        SoundManager.play_click()
        self.set_360_mode(self.btn_360.isChecked())

    def _on_pan_frame(self, frame):
        # Wird NUR aufgerufen, waehrend der 360-Modus aktiv ist (siehe
        # set_360_mode) - im normalen Modus laeuft ueberhaupt kein Sink mit.
        if not frame.isValid():
            return
        img = frame.toImage()
        if not img.isNull():
            self.pan_view.set_frame(img)

    def capture_frame(self):
        SoundManager.play_click()
        if self.view_mode == "360":
            img = self.pan_view.capture_native()
            self._finish_capture(img)
            return
        # Normaler Modus: es haengt bewusst kein Sink permanent mit (Performance!),
        # daher hier kurz einen Sink andocken, GENAU EIN Frame abgreifen und
        # danach sofort wieder zurueck auf das normale Video-Widget schalten.
        if self._capture_sink is None:
            self._capture_sink = QVideoSink(self)

        def _on_single_frame(frame):
            try:
                self._capture_sink.videoFrameChanged.disconnect(_on_single_frame)
            except Exception:
                pass
            self.player.setVideoOutput(self.video_widget)
            img = None
            if frame.isValid():
                candidate = frame.toImage()
                if not candidate.isNull():
                    img = candidate
            self._finish_capture(img)

        self._capture_sink.videoFrameChanged.connect(_on_single_frame)
        self.player.setVideoOutput(self._capture_sink)

    def _finish_capture(self, img):
        if img is None or img.isNull():
            return
        target_dir = self.gallery_dir or os.path.expanduser("~/Pictures")
        saved_path = save_captured_photo(target_dir, img)
        if saved_path:
            show_toast(self, tr(self.lang, "saved_in_gallery"))

    def toggle_play(self):
        SoundManager.play_click()
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶")
        else:
            self.player.play()
            self.btn_play.setText("⏸")

    def on_seek(self, value):
        self.player.setPosition(value)

    def on_volume(self, value):
        self.audio.setVolume(value / 100.0)

    def on_position_changed(self, pos):
        if not self.slider_seek.isSliderDown():
            self.slider_seek.setValue(pos)
        self._update_time_label(pos, self.player.duration())

    def on_duration_changed(self, dur):
        self.slider_seek.setRange(0, dur)
        self._update_time_label(self.player.position(), dur)

    def _update_time_label(self, pos, dur):
        def fmt(ms):
            s = max(0, ms) // 1000
            return f"{s // 60:02d}:{s % 60:02d}"
        self.lbl_time.setText(f"{fmt(pos)} / {fmt(dur)}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.toggle_play()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.player.stop()
        super().closeEvent(event)


class ZoomableGraphicsView(QGraphicsView):
    """QGraphicsView mit Mausrad-Zoom (Verschieben durch Ziehen ist ueber
    ScrollHandDrag bereits eingebaut)."""
    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class ImageViewerDialog(QDialog):
    """Vollbild-Bildbetrachter mit Zoom (Mausrad) und Verschieben (Ziehen) -
    oeffnet sich beim Anklicken eines Bildes in der Bilder-Galerie.
    Doppelklick setzt den Zoom wieder auf "Bild einpassen" zurueck."""
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(os.path.basename(path))
        self.resize(1000, 700)
        self.setStyleSheet("background: #000;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = ZoomableGraphicsView()
        self.view.setStyleSheet("border: none; background: #000;")
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scene = QGraphicsScene(self)
        pix = QPixmap(path)
        self.pixmap_item = scene.addPixmap(pix)
        scene.setSceneRect(QRectF(pix.rect()))
        self.view.setScene(scene)
        self.view.mouseDoubleClickEvent = lambda event: self._fit_to_view()
        layout.addWidget(self.view)

        QTimer.singleShot(0, self._fit_to_view)

    def _fit_to_view(self):
        self.view.resetTransform()
        self.view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)


class ClickableThumbnail(QLabel):
    def __init__(self, pixmap, on_click, tooltip="", parent=None, on_context=None):
        super().__init__(parent)
        self._on_context = on_context
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.deviceIndependentSize().toSize())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip: self.setToolTip(tooltip)
        self._on_click = on_click
        # El estilo (borde y hover) lo define la galeria UNA vez para todas
        # las miniaturas: con miles, un setStyleSheet por miniatura era lento.
        # Sin QGraphicsDropShadowEffect: con miles de miniaturas cada sombra
        # se recalculaba en cada repintado y volvia lenta toda la galeria.

    def mousePressEvent(self, event):
        # Solo el clic izquierdo abre (el derecho muestra el menu contextual)
        if event.button() == Qt.MouseButton.LeftButton and self._on_click:
            self._on_click()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        if self._on_context:
            self._on_context(event.globalPos())


# --- [ DATUMS-TRENNER FUER DIE GALERIE ] ---
#
# Wandelt ein "YYYY-MM-DD"-Datum in eine gut lesbare Ueberschrift um
# ("Heute", "Gestern" oder z.B. "Montag, 14. Juli 2025" / "Monday, July 14,
# 2025"), fuer die Trennzeilen zwischen den einzelnen Tagen in der Video-
# bzw. Bilder-Galerie.
_WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
              "August", "September", "Oktober", "November", "Dezember"]
_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
              "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]


def format_gallery_date_header(date_str, lang="de"):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return date_str
    today = datetime.now().date()
    if dt.date() == today:
        return tr(lang, "gallery_today")
    if dt.date() == today - timedelta(days=1):
        return tr(lang, "gallery_yesterday")
    if lang == "de":
        return f"{_WEEKDAYS_DE[dt.weekday()]}, {dt.day}. {_MONTHS_DE[dt.month - 1]} {dt.year}"
    if lang == "es":
        return f"{_WEEKDAYS_ES[dt.weekday()].capitalize()}, {dt.day} de {_MONTHS_ES[dt.month - 1]} de {dt.year}"
    return f"{_WEEKDAYS_EN[dt.weekday()]}, {_MONTHS_EN[dt.month - 1]} {dt.day}, {dt.year}"


# --- [ GALERIA VIRTUALIZADA ] ---
#
# Antes: un QLabel + QPixmap por cada archivo (2.700 widgets, ~1,6 GB de RAM a
# 200 %, la interfaz se trababa al cargar y ~1 s al redimensionar). Ahora una
# sola vista dibuja SOLO las filas visibles y pide las miniaturas de lo visible
# (+1 pantalla de margen); guarda las ultimas ~240 en memoria.

class _ThumbIO:
    """Lectura/generacion de miniaturas (cache en disco), sin QThread: reutiliza
    la logica de ThumbnailLoaderThread."""
    CACHE_W, CACHE_H = ThumbnailLoaderThread.CACHE_W, ThumbnailLoaderThread.CACHE_H

    @property
    def CACHE_DIR(self):
        return ThumbnailLoaderThread.CACHE_DIR

    def __init__(self, kind, meta, lang):
        self.kind, self.meta, self.lang = kind, meta or {}, lang

    _cache_path = ThumbnailLoaderThread._cache_path
    _load_cached = ThumbnailLoaderThread._load_cached
    _load_raw = ThumbnailLoaderThread._load_raw
    _read_video_frame = ThumbnailLoaderThread._read_video_frame
    _damaged_image = ThumbnailLoaderThread._damaged_image


class ThumbProvider(QObject):
    """Carga miniaturas bajo demanda en hilos: primero la cache (rapido); si no
    esta, muestra la miniatura EXIF y genera la nitida (con _DISK_LOCK para no
    castigar el disco mecanico)."""
    ready = Signal(str, QImage)

    def __init__(self, kind, meta, lang, parent=None):
        super().__init__(parent)
        from concurrent.futures import ThreadPoolExecutor
        self.io = _ThumbIO(kind, meta, lang)
        self.kind = kind
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.inflight = set()
        self.generation = 0
        self.dpr = screen_dpr()

    def request(self, paths):
        gen = self.generation
        for path in paths:
            if path not in self.inflight:
                self.inflight.add(path)
                self.pool.submit(self._load, path, gen)

    def _cover(self, img):
        cover = make_cover_thumbnail(img, THUMB_W, THUMB_H, dpr=self.dpr)
        return draw_play_badge(cover) if self.kind == "video" else cover

    def _load(self, path, gen):
        try:
            if gen != self.generation:          # ya se scrolleo a otro lado
                self.inflight.discard(path)
                return
            if self.kind == "video" and is_broken_mp4(path):
                # sin el icono de reproducir: un video danado no se puede abrir
                self.ready.emit(path, make_cover_thumbnail(self.io._damaged_image(), THUMB_W, THUMB_H, dpr=self.dpr))
                return
            img = self.io._load_cached(path)
            if img is None:
                if self.kind != "video":
                    quick = read_exif_thumbnail(path)
                    if quick is not None and gen == self.generation:
                        self.ready.emit(path + "\0quick", self._cover(quick))
                if gen != self.generation:
                    self.inflight.discard(path)
                    return
                with _DISK_LOCK:
                    img = self.io._load_raw(path)   # genera y guarda en cache
            self.ready.emit(path, self._cover(img) if img is not None and not img.isNull() else QImage())
        except Exception:
            self.ready.emit(path, QImage())

    def shutdown(self):
        self.generation += 1
        try:
            self.pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


class ThumbGridView(QAbstractScrollArea):
    """Cuadricula virtual: encabezados de fecha + miniaturas, dibujando solo lo
    visible. Senales: activated(ruta) con clic izquierdo, context(ruta, pos)."""
    activated = Signal(str)
    context = Signal(str, QPoint)
    selection_changed = Signal()
    SP, HEADER_H = 18, 40
    ROW_H = THUMB_H + 18
    LRU_CAP = 240

    def __init__(self, parent=None):
        super().__init__(parent)
        from collections import OrderedDict
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QAbstractScrollArea { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.viewport().setMouseTracking(True)
        self.files, self.dates, self.rows, self.row_ys = [], [], [], []
        self.total_h = 0
        self.cols = 3
        self.lang = "es"
        self.lru = OrderedDict()
        self.missing = set()
        self.provider = None
        self.hover = None
        self._press = None
        # Galeria de la camara: estado por elemento ("synced"/"new"), seleccion
        # con un circulo en la esquina y textos de ayuda propios.
        self.status = {}
        self.selectable = False
        self.selected = set()
        self.tooltips = {}
        self.accent = QColor("#00f2fe")
        self.header_font = QFont("Century Gothic")
        self.header_font.setPixelSize(13)
        self.header_font.setBold(True)
        self.header_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
        self.verticalScrollBar().valueChanged.connect(self._schedule_request)
        self._req_timer = QTimer(self)
        self._req_timer.setSingleShot(True)
        self._req_timer.setInterval(0)
        self._req_timer.timeout.connect(self._request_visible)
        # Animacion de carga ("shimmer") en los recuadros que esperan miniatura:
        # 20 cuadros/s, redibuja solo esos recuadros y se apaga sola.
        self._shimmer_phase = 0.0
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.setInterval(50)
        self._shimmer_timer.timeout.connect(self._shimmer_tick)

    def _pending_rects(self):
        off = self.verticalScrollBar().value()
        x0 = self._x0()
        rects = []
        for y, kind, payload in self._visible_rows():
            if kind != "t":
                continue
            for c, path in enumerate(payload):
                if path not in self.lru and path not in self.missing:
                    rects.append(QRect(x0 + c * (THUMB_W + self.SP), y - off, THUMB_W, THUMB_H))
        return rects

    def _shimmer_tick(self):
        rects = self._pending_rects()
        if not rects or not self.isVisible():
            self._shimmer_timer.stop()
            self.viewport().update()
            return
        self._shimmer_phase = (self._shimmer_phase + 0.06) % 1.6
        for r in rects:
            self.viewport().update(r)

    def _ensure_shimmer(self):
        if not self._shimmer_timer.isActive() and self._pending_rects():
            self._shimmer_timer.start()

    # ---- datos
    def set_items(self, files, dates, meta, kind, lang, provider=None, keep_cache=False):
        if self.provider is not None and self.provider is not provider:
            self.provider.shutdown()
            try:
                self.provider.ready.disconnect(self._on_ready)
            except Exception:
                pass
        same = provider is not None and provider is self.provider
        self.files, self.dates, self.lang = files, dates, lang
        if not keep_cache:
            self.lru.clear()
            self.missing.clear()
        self.hover = None
        if not same:
            self.provider = provider or ThumbProvider(kind, meta, lang, self)
            self.provider.ready.connect(self._on_ready)
        self.verticalScrollBar().setValue(0)
        self._relayout()

    def remove_path(self, path):
        """Quita un archivo de la cuadricula (tras eliminarlo) manteniendo la
        posicion del scroll."""
        if path not in self.files:
            return
        i = self.files.index(path)
        del self.files[i]
        del self.dates[i]
        self.lru.pop(path, None)
        if self.hover == path:
            self.hover = None
        pos = self.verticalScrollBar().value()
        self._relayout()
        self.verticalScrollBar().setValue(min(pos, self.verticalScrollBar().maximum()))

    def loaded_count(self):
        return len(self.lru)

    def _columns(self):
        return max(1, (self.viewport().width() + self.SP) // (THUMB_W + self.SP))

    def _x0(self):
        used = self.cols * (THUMB_W + self.SP) - self.SP
        return max(0, (self.viewport().width() - used) // 2)

    def _relayout(self):
        self.cols = self._columns()
        rows, y, cur, row = [], 0, None, None
        for path, d in zip(self.files, self.dates):
            if d != cur:
                cur = d
                rows.append((y, "h", format_gallery_date_header(d, self.lang)))
                y += self.HEADER_H
                row = None
            if row is None or len(row) >= self.cols:
                row = []
                rows.append((y, "t", row))
                y += self.ROW_H
            row.append(path)
        self.rows, self.total_h = rows, y
        self.row_ys = [r[0] for r in rows]
        self._update_scrollbar()
        self._schedule_request()
        self.viewport().update()

    def _update_scrollbar(self):
        sb = self.verticalScrollBar()
        sb.setRange(0, max(0, self.total_h - self.viewport().height()))
        sb.setPageStep(max(1, self.viewport().height()))
        sb.setSingleStep(60)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.files and self._columns() != self.cols:
            self._relayout()          # solo reacomoda: las miniaturas en memoria se reutilizan
        else:
            self._update_scrollbar()
            self._schedule_request()

    def _visible_rows(self, margin=0):
        import bisect
        top = self.verticalScrollBar().value() - margin
        bottom = self.verticalScrollBar().value() + self.viewport().height() + margin
        i = max(0, bisect.bisect_right(self.row_ys, top) - 1)
        out = []
        while i < len(self.rows) and self.rows[i][0] < bottom:
            out.append(self.rows[i])
            i += 1
        return out

    # ---- pedidos de miniaturas (primero lo visible, despues el margen)
    def _schedule_request(self, *_):
        self._req_timer.start()

    def _request_visible(self):
        if self.provider is None:
            return
        self.provider.generation += 1         # descarta pedidos viejos que no empezaron
        self.provider.inflight.clear()
        visible = [p for _, k, pl in self._visible_rows() if k == "t" for p in pl]
        margin = [p for _, k, pl in self._visible_rows(self.viewport().height()) if k == "t" for p in pl]
        want, seen = [], set()
        for path in visible + margin:
            if path not in seen and path not in self.lru and path not in self.missing:
                seen.add(path)
                want.append(path)
        self.provider.request(want)
        self._ensure_shimmer()

    def _on_ready(self, key, img):
        quick = key.endswith("\0quick")
        path = key[:-6] if quick else key
        if not quick:
            self.provider.inflight.discard(path)
        if img.isNull():
            if not quick:
                self.missing.add(path)
            return
        if quick and path in self.lru:
            return
        self.lru[path] = QPixmap.fromImage(img)
        self.lru.move_to_end(path)
        while len(self.lru) > self.LRU_CAP:
            self.lru.popitem(last=False)
        self.viewport().update()

    # ---- dibujo
    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        off = self.verticalScrollBar().value()
        x0 = self._x0()
        for y, kind, payload in self._visible_rows():
            yy = y - off
            if kind == "h":
                painter.setFont(self.header_font)
                painter.setPen(QColor(255, 255, 255, 140))
                painter.drawText(QRect(x0, yy + 6, self.viewport().width() - x0, self.HEADER_H - 6),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, payload)
                continue
            for c, path in enumerate(payload):
                r = QRectF(x0 + c * (THUMB_W + self.SP), yy, THUMB_W, THUMB_H)
                pix = self.lru.get(path)
                clip = QPainterPath()
                clip.addRoundedRect(r, 12, 12)
                if pix is None:
                    painter.fillPath(clip, QColor(255, 255, 255, 12))
                    if path not in self.missing:
                        # banda de luz que cruza el recuadro mientras carga
                        cx = r.x() + (self._shimmer_phase - 0.3) * r.width()
                        g = QLinearGradient(cx - 90, 0, cx + 90, 0)
                        g.setColorAt(0.0, QColor(255, 255, 255, 0))
                        g.setColorAt(0.5, QColor(255, 255, 255, 42))
                        g.setColorAt(1.0, QColor(255, 255, 255, 0))
                        painter.fillPath(clip, g)
                else:
                    painter.drawPixmap(int(r.x()), int(r.y()), pix)   # ya viene redondeada y a la escala real
                hovered = path == self.hover
                sel = self.selectable and path in self.selected
                if sel:
                    painter.setPen(QPen(self.accent, 3))
                else:
                    painter.setPen(QPen(QColor(255, 255, 255, 220 if hovered else 22), 2 if hovered else 1))
                painter.drawPath(clip)
                st = self.status.get(path)
                if st:
                    self._paint_badge(painter, r, st)
                if self.selectable:
                    self._paint_check(painter, r, sel, hovered)
        painter.end()

    def _paint_badge(self, painter, r, status):
        """Marca de estado: check verde = ya sincronizado; NUEVO = falta copiar."""
        if status == "synced":
            c = QPointF(r.right() - 18, r.top() + 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 110))
            painter.drawEllipse(c, 13, 13)
            painter.setBrush(QColor("#2ecc71"))
            painter.drawEllipse(c, 11, 11)
            pen = QPen(QColor("#ffffff"), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath(QPointF(c.x() - 5, c.y()))
            path.lineTo(c.x() - 1.5, c.y() + 3.5)
            path.lineTo(c.x() + 5, c.y() - 4)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        else:
            text = tr(self.lang, "cam_badge_new")
            f = QFont(self.header_font)
            f.setPixelSize(11)
            painter.setFont(f)
            w = painter.fontMetrics().horizontalAdvance(text) + 16
            pill = QRectF(r.right() - w - 8, r.top() + 8, w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.accent)
            painter.drawRoundedRect(pill, 10, 10)
            painter.setPen(QColor("#0d0d12"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _check_rect(self, r):
        return QRectF(r.left() + 8, r.top() + 8, 24, 24)

    def _paint_check(self, painter, r, selected, hovered):
        cr = self._check_rect(r)
        painter.setPen(QPen(QColor(255, 255, 255, 230 if (selected or hovered) else 150), 2))
        painter.setBrush(self.accent if selected else QColor(0, 0, 0, 90))
        painter.drawEllipse(cr)
        if selected:
            pen = QPen(QColor("#0d0d12"), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            c = cr.center()
            path = QPainterPath(QPointF(c.x() - 5, c.y()))
            path.lineTo(c.x() - 1.5, c.y() + 3.5)
            path.lineTo(c.x() + 5, c.y() - 4)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    def _cell_rect(self, path):
        off = self.verticalScrollBar().value()
        x0 = self._x0()
        for y, kind, payload in self._visible_rows():
            if kind == "t" and path in payload:
                c = payload.index(path)
                return QRectF(x0 + c * (THUMB_W + self.SP), y - off, THUMB_W, THUMB_H)
        return None

    def toggle_selected(self, path):
        if path in self.selected:
            self.selected.discard(path)
        else:
            self.selected.add(path)
        self.viewport().update()
        self.selection_changed.emit()

    # ---- interaccion
    def path_at(self, pos):
        off = self.verticalScrollBar().value()
        x0 = self._x0()
        for y, kind, payload in self._visible_rows():
            if kind == "t" and y - off <= pos.y() < y - off + THUMB_H:
                rel = pos.x() - x0
                c = rel // (THUMB_W + self.SP)
                if rel >= 0 and 0 <= c < len(payload) and rel % (THUMB_W + self.SP) < THUMB_W:
                    return payload[int(c)]
        return None

    def mouseMoveEvent(self, event):
        h = self.path_at(event.position().toPoint())
        if h != self.hover:
            self.hover = h
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.viewport().update()

    def leaveEvent(self, event):
        if self.hover is not None:
            self.hover = None
            self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._press = self.path_at(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            path = self.path_at(pos)
            if path and path == self._press:
                cell = self._cell_rect(path)
                ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if self.selectable and (ctrl or (cell is not None and self._check_rect(cell).contains(QPointF(pos)))):
                    self.toggle_selected(path)      # circulo de la esquina (o Ctrl+clic): seleccionar
                else:
                    self.activated.emit(path)
        self._press = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        path = self.path_at(event.pos())
        if path:
            self.context.emit(path, event.globalPos())

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            path = self.path_at(event.pos())
            if path:
                QToolTip.showText(event.globalPos(), self.tooltips.get(path, path), self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def shutdown(self):
        if self.provider is not None:
            self.provider.shutdown()


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

        self.lbl_loading = QLabel("")
        self.lbl_loading.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.4); background: transparent;")
        outer.addWidget(self.lbl_loading)
        self.lbl_loading.hide()

        # Vista virtualizada (dibuja solo lo visible); "scroll" se mantiene como
        # nombre por compatibilidad con el resto del codigo.
        self.view = ThumbGridView(self)
        self.view.activated.connect(lambda path: self.play_video(path) if self.KIND == "video" else self.open_image(path))
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

    def _show_context_menu(self, path, global_pos):
        """Clic derecho en una miniatura: ruta, copiar ruta, Explorador,
        abrir e informacion del archivo (metadatos)."""
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


# --- [ PLATZHALTER-ICONS: WLAN / BLUETOOTH / GPS ] ---
#
# Diese drei Werte werden von der GoPro (aktuell) nicht per USB/MTP
# ausgelesen - es gibt schlicht keine zuverlaessige Schnittstelle dafuer
# in diesem Sync-Tool. Damit die Geraete-Info-Karte trotzdem vollstaendig
# und "fertig" wirkt, zeigen wir hier bewusst dezente, inaktive
# Platzhalter-Symbole (statt Emojis, die je nach System unterschiedlich
# aussehen). Sobald echte Werte verfuegbar sind, kann set_active()
# genutzt werden, um das Icon farbig/aktiv darzustellen.

class ConnectivityBadge(QWidget):
    def __init__(self, icon_type, label_text, tooltip_text=None, parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.label_text = label_text
        self._active = False
        self._color = QColor("#00f2fe")
        self.setFixedSize(74, 64)
        self.setToolTip(tooltip_text or label_text)

    def set_active(self, active, hex_color=None):
        self._active = active
        if hex_color:
            self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, event):
        try:
            self._paint(event)
        except Exception:
            print("=== FEHLER beim Zeichnen von ConnectivityBadge ===")
            traceback.print_exc()

    def _paint(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = 38
        x = (self.width() - size) / 2
        y = 2
        icon_color = self._color if self._active else QColor(255, 255, 255, 130)
        bg_color = QColor(self._color) if self._active else QColor(255, 255, 255, 14)
        if self._active:
            bg_color.setAlpha(30)
        border_color = QColor(self._color) if self._active else QColor(255, 255, 255, 28)
        if self._active:
            border_color.setAlpha(160)
        else:
            border_color.setAlpha(28)

        painter.setPen(QPen(border_color, 1.4 if self._active else 1))
        painter.setBrush(bg_color)
        painter.drawEllipse(QRectF(x, y, size, size))

        cx, cy = x + size / 2, y + size / 2
        pen = QPen(icon_color, 2.1)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.icon_type == "wifi":
            for r in (5, 9, 13):
                rect = QRectF(cx - r, cy + 3 - r, r * 2, r * 2)
                painter.drawArc(rect, 35 * 16, 110 * 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)
            painter.drawEllipse(QRectF(cx - 1.8, cy + 4.2, 3.6, 3.6))

        elif self.icon_type == "bluetooth":
            # Exaktes Bluetooth-Logo als gefuellte Flaeche (Koordinaten
            # 1:1 von der offiziellen Glyphen-Geometrie abgeleitet, mit
            # OddEven-Fuellregel fuer die beiden kleinen Dreiecks-Ausschnitte
            # in der Mitte). Das ergibt den sauberen, klassischen Look statt
            # der vorherigen, aus einzelnen Linien "zusammengestrichelten"
            # Variante.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)

            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            outer = [
                (5.40, -3.65), (0.55, -8.50), (-0.30, -8.50), (-0.30, -2.05),
                (-4.20, -5.95), (-5.40, -4.75), (-0.65, 0.00), (-5.40, 4.75),
                (-4.20, 5.95), (-0.30, 2.05), (-0.30, 8.50), (0.55, 8.50),
                (5.40, 3.65), (1.75, 0.00),
            ]
            path.moveTo(cx + outer[0][0], cy + outer[0][1])
            for dx, dy in outer[1:]:
                path.lineTo(cx + dx, cy + dy)
            path.closeSubpath()

            for tri in (
                [(1.40, -5.24), (3.00, -3.65), (1.40, -2.05)],
                [(3.00, 3.65), (1.40, 5.24), (1.40, 2.05)],
            ):
                path.moveTo(cx + tri[0][0], cy + tri[0][1])
                for dx, dy in tri[1:]:
                    path.lineTo(cx + dx, cy + dy)
                path.closeSubpath()

            painter.drawPath(path)

        elif self.icon_type == "gps":
            # Klassischer Standort-Pin (Teardrop) mit ausgestanztem Punkt in
            # der Mitte - wie das Ortungssymbol in den meisten Kamera-Apps.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)
            r = 7.2
            pin = QPainterPath()
            pin.addEllipse(QRectF(cx - r, cy - r - 3, r * 2, r * 2))
            tail = QPainterPath()
            tail.moveTo(cx - 5.2, cy + 1.6)
            tail.lineTo(cx, cy + 12)
            tail.lineTo(cx + 5.2, cy + 1.6)
            tail.closeSubpath()
            pin = pin.united(tail)
            painter.drawPath(pin)
            hole_color = bg_color if self._active else QColor(38, 40, 52, 255)
            painter.setBrush(hole_color)
            painter.drawEllipse(QRectF(cx - 2.6, cy - r - 3 + r - 2.6, 5.2, 5.2))

        painter.setPen(icon_color if self._active else QColor(255, 255, 255, 140))
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)
        text_rect = QRectF(0, y + size + 3, self.width(), 16)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter, self.label_text)


class SDCardIcon(QWidget):
    """Kleines, dezent gezeichnetes Speicherkarten-Symbol fuer die
    SPEICHERPLATZ-Beschriftung (ersetzt das 💾-Emoji durch ein zur
    restlichen Geraete-Info passendes Vektor-Icon)."""
    def __init__(self, color_hex="#ffffff", alpha=140, parent=None):
        super().__init__(parent)
        self._color = QColor(color_hex)
        self._alpha = alpha
        self.setFixedSize(16, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._color)
        c.setAlpha(self._alpha)

        w, h = 13, 17
        x, y = (self.width() - w) / 2, (self.height() - h) / 2
        cut = 4.5

        body = QPainterPath()
        body.moveTo(x, y + 3)
        body.lineTo(x, y + h - 2)
        body.quadTo(x, y + h, x + 2, y + h)
        body.lineTo(x + w - 2, y + h)
        body.quadTo(x + w, y + h, x + w, y + h - 2)
        body.lineTo(x + w, y + cut)
        body.lineTo(x + w - cut, y)
        body.lineTo(x + 2, y)
        body.quadTo(x, y, x, y + 2)
        body.closeSubpath()

        pen = QPen(c, 1.3)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(body)

        # Kontakt-Streifen (die kleinen "Pins" oben, typisch fuer SD-Karten)
        pin_pen = QPen(c, 1.1)
        painter.setPen(pin_pen)
        pin_top = y + 1.5
        pin_bottom = y + 5.5
        for i in range(3):
            px = x + 2.5 + i * 3.0
            painter.drawLine(QPointF(px, pin_top), QPointF(px, pin_bottom))


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
        self._lane_pct = {"copy": None, "backup": None}

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
        return self._copy_busy() or self._backup_busy()

    def _copy_busy(self):
        w = getattr(self, "sync_worker", None)
        return w is not None and w.isRunning()

    def _backup_busy(self):
        w = getattr(self, "backup_worker", None)
        return w is not None and w.isRunning()

    @staticmethod
    def _lane(task):
        return "backup" if task[0] == "backup" else "copy"

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
            else:
                out.append((tr(self.lang, "queue_import_item", folder=short_path(task[1])), f"{task[1]}  →  {task[2]}"))
        return out

    @staticmethod
    def _task_key(task):
        if task[0] == "camera":
            return ("camera",)
        if task[0] == "backup":
            return ("backup", os.path.normcase(os.path.abspath(task[1])),
                    tuple(os.path.normcase(os.path.abspath(d)) for d in task[2]))
        return ("import", os.path.normcase(os.path.abspath(task[1])), os.path.normcase(os.path.abspath(task[2])))

    def _try_enqueue(self, task):
        """Agrega a la cola si no es lo mismo que lo que ya se copia o espera."""
        key = self._task_key(task)
        if any(cur is not None and self._task_key(cur) == key for cur in (self._current_task, self._backup_task)):
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
        self.activity_page.set_running(self._backup_task is not None)
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

    def start_backup(self, source=None, dests=None):
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
        task = ("backup", source, tuple(dests))
        if self._backup_busy():
            if self._try_enqueue(task):
                show_toast(self, tr(self.lang, "backup_queued"))
            return
        self.backup_worker = BackupWorkerThread(source, dests, self.lang, self)
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
        self.activity_page.set_running(self._current_task is not None)
        self.backup_panel.hide()
        task = self._pop_next("backup") if not stats.get("cancelled") else None
        if task is not None:
            QTimer.singleShot(400, lambda: self.start_backup(task[1], list(task[2])))
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


# --- [ MAIN WINDOW ] ---

# --- [ THEME-EINSTELLUNGEN: FARBE, HINTERGRUNDBILD, WEICHZEICHNER ] ---

class _ToggleTrackWidget(QWidget):
    """Nur die eigentliche Schalter-Flaeche (Track + Knopf) von ToggleSwitch -
    als eigenes Widget, damit der Knopf per QPropertyAnimation animiert
    werden kann."""
    def __init__(self, w, h, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self._w = w
        self._h = h
        self._knob_x = 3.0
        self._checked = False
        self._accent = QColor("#00f2fe")

    def _get_knob_x(self):
        return self._knob_x

    def _set_knob_x(self, v):
        self._knob_x = v
        self.update()

    knobX = Property(float, _get_knob_x, _set_knob_x)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self._accent) if self._checked else QColor(255, 255, 255, 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(0, 0, self._w, self._h, self._h / 2, self._h / 2)
        knob_d = self._h - 6
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(self._knob_x, 3, knob_d, knob_d))


class ToggleSwitch(QWidget):
    """Ersetzt QCheckBox in der ganzen App - absichtlich API-kompatibel
    (setText/text/isChecked/setChecked/toggled), damit bestehender Code nur
    minimal angepasst werden musste. Ein Umschalten spielt toggle_on.wav
    bzw. toggle_off.wav (statt des generischen Klick-Sounds) - dafuer
    mussten echte An/Aus-Schalter her statt Checkboxen."""
    toggled = Signal(bool)

    TRACK_W = 42
    TRACK_H = 24

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._checked = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._track = _ToggleTrackWidget(self.TRACK_W, self.TRACK_H)
        layout.addWidget(self._track)

        self.label = QLabel(text)
        self.label.setStyleSheet("color: rgba(255,255,255,0.75); font-size: 13px; background: transparent; border: none;")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        self._anim = QPropertyAnimation(self._track, b"knobX", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def setText(self, text):
        self.label.setText(text)

    def text(self):
        return self.label.text()

    def set_accent(self, hex_color):
        self._track._accent = QColor(hex_color)
        self._track.update()

    def isChecked(self):
        return self._checked

    def _target_x(self, checked):
        knob_d = self.TRACK_H - 6
        return float(self.TRACK_W - knob_d - 3) if checked else 3.0

    def setChecked(self, checked, animate=True):
        checked = bool(checked)
        self._checked = checked
        self._track._checked = checked
        target = self._target_x(checked)
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._track._knob_x)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._track._knob_x = target
        self._track.update()

    def changeEvent(self, event):
        # Deshabilitado = atenuado; antes se veia igual que uno activo.
        if event.type() == QEvent.Type.EnabledChange:
            effect = None
            if not self.isEnabled():
                effect = QGraphicsOpacityEffect(self)
                effect.setOpacity(0.4)
            self.setGraphicsEffect(effect)
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)
        super().changeEvent(event)

    def mousePressEvent(self, event):
        if self.isEnabled():
            new_state = not self._checked
            self.setChecked(new_state)
            SoundManager.play_toggle(new_state)
            self.toggled.emit(new_state)
        super().mousePressEvent(event)


class AvatarPreview(QWidget):
    """Rundes Profilbild-Vorschau-Widget - klickbar, um per Dateidialog ein
    neues Bild auszuwaehlen. Ohne gesetztes Bild wird ein dezenter
    Platzhalter (Initiale des Namens oder ein generisches Personen-Icon)
    gezeichnet."""
    changed = Signal(str)

    def __init__(self, size=88, accent="#00f2fe", parent=None):
        super().__init__(parent)
        self._size = size
        self._accent = QColor(accent)
        self._path = ""
        self._pixmap = None
        self._initial = ""
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_accent(self, hex_color):
        self._accent = QColor(hex_color)
        self.update()

    def set_initial(self, text):
        self._initial = (text or "").strip()[:1].upper()
        self.update()

    def set_image_path(self, path):
        self._path = path or ""
        if self._path and os.path.exists(self._path):
            pix = QPixmap(self._path)
            self._pixmap = pix if not pix.isNull() else None
        else:
            self._pixmap = None
        self.update()

    def mousePressEvent(self, event):
        path, _ = QFileDialog.getOpenFileName(
            self, "", _safe_local_dir(), "*.png *.jpg *.jpeg *.bmp *.webp",
        )
        if path:
            self.set_image_path(path)
            self.changed.emit(path)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._size

        clip_path = QPainterPath()
        clip_path.addEllipse(0, 0, r, r)
        painter.setClipPath(clip_path)

        if self._pixmap is not None:
            dpr = screen_dpr(self)
            scaled = self._pixmap.scaled(int(r * dpr), int(r * dpr), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            sz = scaled.deviceIndependentSize()
            x = int((sz.width() - r) // 2)
            y = int((sz.height() - r) // 2)
            painter.drawPixmap(-x, -y, scaled)
        else:
            # Platzhalter bewusst in Graustufen (statt Akzentfarbe) - wirkt
            # neutraler/dezenter, solange noch kein echtes Profilbild gewaehlt ist.
            grad = QLinearGradient(0, 0, r, r)
            grad.setColorAt(0.0, QColor(120, 122, 130))
            grad.setColorAt(1.0, QColor(60, 62, 70))
            painter.fillRect(0, 0, r, r, grad)
            painter.setPen(QColor(255, 255, 255, 235))
            if self._initial:
                font = QFont("Segoe UI", int(r * 0.36), QFont.Weight.Bold)
                painter.setFont(font)
                painter.drawText(QRectF(0, 0, r, r), Qt.AlignmentFlag.AlignCenter, self._initial)
            else:
                # Statt des "👤"-Emojis (das Windows IMMER in seinen eigenen
                # Farben - meist lila - rendert, egal welchen Stift man
                # setzt) hier ein selbst gezeichnetes, garantiert einfarbiges
                # Personen-Symbol: Kopf (Kreis) + Schultern (Bogen).
                painter.setBrush(QColor(255, 255, 255, 220))
                painter.setPen(Qt.PenStyle.NoPen)
                head_r = r * 0.15
                painter.drawEllipse(QRectF(r / 2 - head_r, r * 0.28 - head_r, head_r * 2, head_r * 2))
                shoulders = QPainterPath()
                shoulders.moveTo(r * 0.24, r * 0.82)
                shoulders.cubicTo(r * 0.24, r * 0.52, r * 0.76, r * 0.52, r * 0.76, r * 0.82)
                shoulders.closeSubpath()
                painter.drawPath(shoulders)

        painter.setClipping(False)
        pen = QPen(QColor(255, 255, 255, 60), 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(1, 1, r - 2, r - 2)


def set_autostart(enabled):
    """Traegt GoPro Sync Pro in den Windows-Autostart ein bzw. entfernt den
    Eintrag wieder (HKEY_CURRENT_USER, kein Adminrecht noetig). Der Eintrag
    startet die App mit "--silent", damit sie beim Windows-Login nicht
    sofort ein Fenster aufreisst (siehe main() / open_on_gopro_connect)."""
    if winreg is None:
        return
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            exe = sys.executable
            script = os.path.abspath(__file__)
            installed = os.path.join(os.getenv("LOCALAPPDATA") or "", "Programs", "GoPro Sync", "GoProSync.exe")
            if getattr(sys, "frozen", False):
                # Als .exe gebaut (PyInstaller o.ae.) - kein separates Skript noetig.
                cmd = f'"{exe}" --silent'
            elif os.path.exists(installed):
                # Version de prueba (desde el codigo): el inicio con Windows
                # debe abrir la app INSTALADA, no el codigo ni un Python temporal.
                cmd = f'"{installed}" --silent'
            else:
                cmd = f'"{exe}" "{script}" --silent'
            winreg.SetValueEx(key, "GoProSyncPro", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "GoProSyncPro")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Autostart konnte nicht gesetzt werden: {e}")


# Cada idioma se muestra SIEMPRE en su propio idioma, sin importar el actual.
_LANGUAGE_LABELS = [
    ("es", "Español", "Usar GoPro Sync en español"),
    ("en", "English", "Use GoPro Sync in English"),
    ("de", "Deutsch", "GoPro Sync auf Deutsch verwenden"),
]
LANGUAGE_NATIVE_NAMES = {code: name for code, name, _hint in _LANGUAGE_LABELS}


def _make_language_buttons(current, accent, on_pick):
    """Fila vertical de botones grandes para elegir idioma (asistente y dialogo)."""
    box = QVBoxLayout()
    box.setSpacing(10)
    buttons = {}
    for code, label, hint in _LANGUAGE_LABELS:
        b = QPushButton(label + "\n" + hint)
        b.setCheckable(True)
        b.setChecked(code == current)
        b.setMinimumHeight(58)
        b.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.06); color: #fff; font-size: 14px; font-weight: 700;"
            " border: 1px solid rgba(255,255,255,0.15); border-radius: 12px; padding: 10px 18px; text-align: left; }"
            " QPushButton:hover { background: rgba(255,255,255,0.12); }"
            f" QPushButton:checked {{ border: 2px solid {accent}; background: rgba(255,255,255,0.14); }}"
        )
        b.clicked.connect(lambda checked=False, c=code: on_pick(c))
        box.addWidget(b)
        buttons[code] = b
    return box, buttons


def short_path(path, keep=2):
    """Acorta una ruta dejando la unidad y las ultimas carpetas (se ve el final),
    p. ej. C:/Users/x/Fotos/GoPro/Tarjeta -> C:/.../GoPro/Tarjeta."""
    path = os.path.normpath(path or "")
    drive, rest = os.path.splitdrive(path)
    parts = [x for x in rest.split(os.sep) if x]
    if len(parts) <= keep + 1:
        return path
    return drive + os.sep + "…" + os.sep + os.sep.join(parts[-keep:])


def wrap_path(path, width=58):
    """Corta una ruta larga en varias lineas, en las barras. Antes se usaba un
    espacio invisible (U+200B) para que el QLabel cortara solo, pero Century
    Gothic no lo tiene y Windows tardaba ~2 s buscando otra fuente: la
    ventana quedaba congelada ("No responde") al abrir cada dialogo."""
    path = path or ""
    if len(path) <= width:
        return path
    lines, cur = [], ""
    for part in re.split(r"(?<=[\\/])", path):
        if cur and len(cur) + len(part) > width:
            lines.append(cur)
            cur = ""
        cur += part
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _human_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def scan_import_source(source, target):
    """Cuenta lo que se importaria desde 'source' hacia 'target' (mismo
    criterio que la sincronizacion: nombre + tamano en cualquier subcarpeta)."""
    exts = tuple(MEDIA_EXTENSIONS_BY_MODE["all"])
    photo_exts = tuple(MEDIA_EXTENSIONS_BY_MODE["pictures"])
    existing = {}
    if target and os.path.isdir(target):
        for root, _d, names in os.walk(target):
            for n in names:
                try:
                    index_existing(existing, n, os.path.getsize(os.path.join(root, n)))
                except OSError:
                    pass
    info = {"photos": 0, "videos": 0, "bytes": 0, "new": 0, "existing": 0, "new_bytes": 0}
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.upper() != "SYSTEM VOLUME INFORMATION"]
        for n in names:
            low = n.lower()
            if not low.endswith(exts):
                continue
            try:
                size = os.path.getsize(os.path.join(root, n))
            except OSError:
                continue
            info["photos" if low.endswith(photo_exts) else "videos"] += 1
            info["bytes"] += size
            if size in existing_candidates(existing, n):
                info["existing"] += 1
            else:
                info["new"] += 1
                info["new_bytes"] += size
    return info


def list_import_files(source, target):
    """Archivos que se importarian desde 'source', con su estado respecto de
    'target' (mismo criterio que la importacion: nombre + tamano)."""
    exts = tuple(MEDIA_EXTENSIONS_BY_MODE["all"])
    video_exts = tuple(MEDIA_EXTENSIONS_BY_MODE["videos"])
    existing = {}
    if target and os.path.isdir(target):
        for root, _d, names in os.walk(target):
            for n in names:
                try:
                    index_existing(existing, n, os.path.getsize(os.path.join(root, n)))
                except OSError:
                    pass
    out = []
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.upper() != "SYSTEM VOLUME INFORMATION"]
        for n in names:
            low = n.lower()
            if not low.endswith(exts):
                continue
            full = os.path.join(root, n)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out.append({"path": full, "name": n, "size": st.st_size, "mtime": st.st_mtime,
                        "video": low.endswith(video_exts),
                        "synced": st.st_size in existing_candidates(existing, n)})
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


class _InflightUnion:
    """inflight de dos proveedores, para que la grilla lo maneje como uno."""

    def __init__(self, *providers):
        self.providers = providers

    def clear(self):
        for pr in self.providers:
            pr.inflight.clear()

    def discard(self, key):
        for pr in self.providers:
            pr.inflight.discard(key)

    def __contains__(self, key):
        return any(key in pr.inflight for pr in self.providers)


class MixedThumbProvider(QObject):
    """Miniaturas de fotos y videos mezclados (una tarjeta SD): cada archivo
    va al proveedor que corresponde."""
    ready = Signal(str, QImage)

    def __init__(self, lang, parent=None):
        super().__init__(parent)
        self.pics = ThumbProvider("picture", {}, lang, self)
        self.vids = ThumbProvider("video", {}, lang, self)
        self.pics.ready.connect(self.ready)
        self.vids.ready.connect(self.ready)
        self.inflight = _InflightUnion(self.pics, self.vids)
        self._gen = 0

    @property
    def generation(self):
        return self._gen

    @generation.setter
    def generation(self, value):
        self._gen = value
        self.pics.generation += 1
        self.vids.generation += 1

    def request(self, paths):
        vexts = tuple(MEDIA_EXTENSIONS_BY_MODE["videos"])
        self.vids.request([x for x in paths if x.lower().endswith(vexts)])
        self.pics.request([x for x in paths if not x.lower().endswith(vexts)])

    def shutdown(self):
        self.pics.shutdown()
        self.vids.shutdown()


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


_BG_SCANS = set()   # mantiene vivos los conteos aunque se cierre el dialogo


class ScanThread(QThread):
    """Corre un conteo de archivos (func(*args)) fuera del hilo de la
    interfaz: con una tarjeta SD o un disco lento, contar en el hilo
    principal congelaba la ventana y Windows ponia "No responde"."""
    done = Signal(int, object)        # token, resultado (None si fallo)

    def __init__(self, token, func, *args):
        super().__init__()
        self.token, self.func, self.args = token, func, args
        _BG_SCANS.add(self)
        self.finished.connect(lambda: _BG_SCANS.discard(self))

    def run(self):
        try:
            result = self.func(*self.args)
        except Exception as e:
            print("[Conteo]", e)
            result = None
        self.done.emit(self.token, result)


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


def scan_backup(source, dest):
    """Lo que copiaria el respaldo (mismo criterio que BackupWorkerThread)."""
    info = {"total": 0, "bytes": 0, "pending": 0, "pending_bytes": 0, "skipped": 0}
    for root, _d, names in os.walk(source):
        for n in names:
            if n.endswith(".part"):
                continue
            full = os.path.join(root, n)
            try:
                st = os.stat(full)
            except OSError:
                continue
            info["total"] += 1
            info["bytes"] += st.st_size
            try:
                d = os.stat(os.path.join(dest, os.path.relpath(full, source))) if dest else None
                same = d is not None and d.st_size == st.st_size and abs(d.st_mtime - st.st_mtime) <= 2
            except OSError:
                same = False
            if same:
                info["skipped"] += 1
            else:
                info["pending"] += 1
                info["pending_bytes"] += st.st_size
    return info


class BackupDialog(QDialog):
    """Respaldar a disco: confirmar carpeta a respaldar y destino (ambos
    cambiables) con un resumen. La copia corre despues en Actividad."""

    def __init__(self, parent, lang, source, dest, accent, remember_default=True):
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


def _fmt_eta(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


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


# --- [ METADATOS DE ARCHIVOS (ficha de informacion) ] ---

_EXIF_TAGS = {0x010F: "make", 0x0110: "model", 0x0131: "software", 0x0132: "datetime", 0x9003: "taken",
              0x829A: "exposure", 0x829D: "fnumber", 0x8827: "iso", 0x920A: "focal", 0xA405: "focal35",
              0xA002: "width", 0xA003: "height"}


def read_jpeg_exif(path):
    """Lee los datos EXIF principales de un JPG (camara, fecha, exposicion,
    ISO, GPS...). Solo lee el principio del archivo."""
    out = {}
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
        if head[:2] != b"\xff\xd8":
            return out
        pos = 2
        tiff = None
        while pos + 4 <= len(head) and head[pos] == 0xFF:
            marker, seg = head[pos + 1], struct.unpack(">H", head[pos + 2:pos + 4])[0]
            if marker == 0xE1 and head[pos + 4:pos + 10] == b"Exif\x00\x00":
                tiff = head[pos + 10:pos + 2 + seg]
                break
            if marker in (0xDA, 0xD9):
                break
            pos += 2 + seg
        if not tiff:
            return out
        bo = "<" if tiff[:2] == b"II" else ">"
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

        def value(typ, count, raw_off):
            n = sizes.get(typ, 1) * count
            data = tiff[raw_off:raw_off + 4] if n <= 4 else tiff[struct.unpack(bo + "I", tiff[raw_off:raw_off + 4])[0]:][:n]
            if typ == 2:
                return data.split(b"\x00")[0].decode("latin1", "replace").strip()
            if typ == 3:
                return struct.unpack(bo + "H", data[:2])[0]
            if typ == 4:
                return struct.unpack(bo + "I", data[:4])[0]
            if typ in (5, 10):
                vals = []
                for i in range(count):
                    a, b = struct.unpack(bo + ("ii" if typ == 10 else "II"), data[i * 8:i * 8 + 8])
                    vals.append((a, b))
                return vals if count > 1 else vals[0]
            return None

        def read_ifd(off, into, tags=None, follow=True):
            n = struct.unpack(bo + "H", tiff[off:off + 2])[0]
            for i in range(n):
                e = off + 2 + 12 * i
                tag, typ, count = struct.unpack(bo + "HHI", tiff[e:e + 8])
                if follow and tag in (0x8769, 0x8825):
                    sub = struct.unpack(bo + "I", tiff[e + 8:e + 12])[0]
                    if tag == 0x8769:
                        read_ifd(sub, into)
                    else:
                        gps = {}
                        read_ifd(sub, gps, tags={1: "latref", 2: "lat", 3: "lonref", 4: "lon"}, follow=False)
                        into["gps"] = gps
                    continue
                key = (tags or _EXIF_TAGS).get(tag)
                if key:
                    try:
                        into[key] = value(typ, count, e + 8)
                    except Exception:
                        pass

        read_ifd(struct.unpack(bo + "I", tiff[4:8])[0], out)
    except Exception:
        pass
    return out


def _mp4_boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            break
        yield typ, pos + hdr, pos + size
        pos += size


def read_mp4_info(path):
    """Duracion, resolucion, fps, codec y datos de la GoPro (firmware) de un MP4."""
    info = {}
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            pos = 0
            moov = None
            while pos + 8 <= fsize:
                f.seek(pos)
                h = f.read(16)
                size = struct.unpack(">I", h[:4])[0]
                if size == 1:
                    size = struct.unpack(">Q", h[8:16])[0]
                elif size == 0:
                    size = fsize - pos
                if size < 8:
                    break
                if h[4:8] == b"moov":
                    f.seek(pos)
                    moov = f.read(min(size, 32 * 1024 * 1024))
                    break
                pos += size
        if not moov:
            return info
        for typ, a, b in _mp4_boxes(moov, 8):
            if typ == b"mvhd":
                v = moov[a]
                if v == 1:
                    ts, dur = struct.unpack(">IQ", moov[a + 20:a + 32])
                else:
                    ts, dur = struct.unpack(">II", moov[a + 12:a + 20])
                if ts:
                    info["duration"] = dur / ts
            elif typ == b"udta":
                for t2, a2, b2 in _mp4_boxes(moov, a, b):
                    if t2 == b"FIRM":
                        info["firmware"] = moov[a2:b2].split(b"\x00")[0].decode("latin1", "replace").strip()
            elif typ == b"trak":
                kind = None
                track = {}
                for t2, a2, b2 in _mp4_boxes(moov, a, b):
                    if t2 == b"tkhd":
                        track["w"] = struct.unpack(">I", moov[b2 - 8:b2 - 4])[0] >> 16
                        track["h"] = struct.unpack(">I", moov[b2 - 4:b2])[0] >> 16
                    elif t2 == b"mdia":
                        for t3, a3, b3 in _mp4_boxes(moov, a2, b2):
                            if t3 == b"hdlr":
                                kind = moov[a3 + 8:a3 + 12]
                            elif t3 == b"mdhd":
                                v = moov[a3]
                                track["timescale"] = struct.unpack(">I", moov[a3 + (20 if v == 1 else 12):a3 + (24 if v == 1 else 16)])[0]
                            elif t3 == b"minf":
                                for t4, a4, b4 in _mp4_boxes(moov, a3, b3):
                                    if t4 == b"stbl":
                                        for t5, a5, b5 in _mp4_boxes(moov, a4, b4):
                                            if t5 == b"stsd":
                                                track["codec"] = moov[a5 + 12:a5 + 16].decode("latin1", "replace")
                                            elif t5 == b"stts" and struct.unpack(">I", moov[a5 + 4:a5 + 8])[0]:
                                                track["delta"] = struct.unpack(">I", moov[a5 + 12:a5 + 16])[0]
                if kind == b"vide" and "video" not in info:
                    info["video"] = track
                elif kind == b"soun" and "audio" not in info:
                    info["audio"] = track
    except Exception:
        pass
    return info


_CODEC_NAMES = {"avc1": "H.264 (AVC)", "hvc1": "H.265 (HEVC)", "hev1": "H.265 (HEVC)", "mp4a": "AAC"}


def describe_media(path, lang):
    """Lista de (seccion, etiqueta, valor) para la ficha de informacion."""
    rows = []
    try:
        st = os.stat(path)
        rows += [("file", tr(lang, "info_name"), os.path.basename(path)),
                 ("file", tr(lang, "info_path"), os.path.dirname(path)),
                 ("file", tr(lang, "info_size"), f"{_human_size(st.st_size)}  ({st.st_size:,} bytes)".replace(",", ".")),
                 ("file", tr(lang, "info_created"), datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime)).strftime("%d/%m/%Y %H:%M:%S")),
                 ("file", tr(lang, "info_modified"), datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M:%S"))]
    except OSError:
        pass
    low = path.lower()
    media = []
    if low.endswith((".jpg", ".jpeg")):
        ex = read_jpeg_exif(path)
        cam = " ".join(x for x in (ex.get("make"), ex.get("model")) if x)
        if cam:
            media.append((tr(lang, "info_camera"), cam))
        if ex.get("software"):
            media.append((tr(lang, "info_firmware"), ex["software"]))
        taken = ex.get("taken") or ex.get("datetime")
        if taken:
            try:
                taken = datetime.strptime(taken, "%Y:%m:%d %H:%M:%S").strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                pass
            media.append((tr(lang, "info_taken"), taken))
        if ex.get("width") and ex.get("height"):
            mp = ex["width"] * ex["height"] / 1e6
            media.append((tr(lang, "info_resolution"), f"{ex['width']} × {ex['height']}  ({mp:.1f} MP)"))
        if isinstance(ex.get("exposure"), tuple) and ex["exposure"][1]:
            a, b = ex["exposure"]
            media.append((tr(lang, "info_exposure"), f"1/{round(b / a)} s" if a and a < b else f"{a / b:g} s"))
        if isinstance(ex.get("fnumber"), tuple) and ex["fnumber"][1]:
            media.append((tr(lang, "info_aperture"), f"f/{ex['fnumber'][0] / ex['fnumber'][1]:.1f}"))
        if ex.get("iso"):
            media.append((tr(lang, "info_iso"), str(ex["iso"])))
        if isinstance(ex.get("focal"), tuple) and ex["focal"][1]:
            txt = f"{ex['focal'][0] / ex['focal'][1]:.1f} mm"
            if ex.get("focal35"):
                txt += f"  ({ex['focal35']} mm eq. 35 mm)"
            media.append((tr(lang, "info_focal"), txt))
        gps = ex.get("gps") or {}
        try:
            def deg(v):
                return v[0][0] / v[0][1] + v[1][0] / v[1][1] / 60 + v[2][0] / v[2][1] / 3600
            lat = deg(gps["lat"]) * (-1 if gps.get("latref") == "S" else 1)
            lon = deg(gps["lon"]) * (-1 if gps.get("lonref") == "W" else 1)
            media.append((tr(lang, "info_gps"), f"{lat:.6f}, {lon:.6f}"))
        except Exception:
            pass
    elif low.endswith(".mp4"):
        if is_broken_mp4(path):
            media.append((tr(lang, "info_damaged"), tr(lang, "video_damaged")))
        else:
            mi = read_mp4_info(path)
            if mi.get("firmware"):
                media.append((tr(lang, "info_firmware"), mi["firmware"]))
            if mi.get("duration"):
                d = int(mi["duration"])
                media.append((tr(lang, "info_duration"), f"{d // 3600:d}:{d % 3600 // 60:02d}:{d % 60:02d}" if d >= 3600
                              else f"{d // 60:d}:{d % 60:02d}"))
            v = mi.get("video") or {}
            if v.get("w"):
                media.append((tr(lang, "info_resolution"), f"{v['w']} × {v['h']}"))
            if v.get("timescale") and v.get("delta"):
                media.append((tr(lang, "info_fps"), f"{v['timescale'] / v['delta']:.2f}".rstrip("0").rstrip(".")))
            if v.get("codec"):
                media.append((tr(lang, "info_codec"), _CODEC_NAMES.get(v["codec"], v["codec"])))
            a = mi.get("audio") or {}
            if a.get("codec"):
                rate = f", {a['timescale'] / 1000:g} kHz" if a.get("timescale") else ""
                media.append((tr(lang, "info_audio"), _CODEC_NAMES.get(a["codec"], a["codec"]) + rate))
    if not media:
        media.append(("", tr(lang, "info_none")))
    rows += [("media", k, v) for k, v in media]
    return rows


def move_to_recycle_bin(path):
    """Mueve un archivo a la Papelera de reciclaje (no lo borra para siempre).
    Devuelve True si el archivo ya no esta en su lugar."""
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = os.path.abspath(path) + "\0"        # lista terminada en doble NUL
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return res == 0 and not op.fAnyOperationsAborted and not os.path.exists(path)
    except Exception as e:
        print(f"[Papelera] No se pudo mover {path}: {e}")
        return False


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


def show_in_explorer(path):
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    except Exception:
        pass


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


class ActivityNavButton(QPushButton):
    """Item "Actividad" del menu: mientras hay una copia en curso se rellena
    de fondo segun el progreso total y muestra el porcentaje."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._pct = None
        self._accent = QColor("#00f2fe")

    def set_progress(self, pct, accent=None):
        self._pct = pct
        if accent:
            self._accent = QColor(accent)
        self.update()

    def paintEvent(self, event):
        if self._pct is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.setClipPath(path)
            p.fillRect(r, QColor(255, 255, 255, 14))
            fill = QColor(self._accent)
            fill.setAlpha(70)
            p.fillRect(QRectF(r.left(), r.top(), r.width() * max(0.0, min(100.0, self._pct)) / 100.0, r.height()), fill)
            p.end()
        super().paintEvent(event)
        if self._pct is not None:
            p = QPainter(self)
            p.setPen(QColor(255, 255, 255, 220))
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            p.drawText(self.rect().adjusted(0, 0, -14, 0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self._pct:.0f}%")
            p.end()


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


def camera_item_key(entry):
    return f"{entry.get('path') or ''}|{entry.get('name') or ''}"


class CameraListThread(QThread):
    """Lista los archivos de la camara (MTP) en segundo plano."""
    listed = Signal(object)        # lista de dicts o None si fallo

    def run(self):
        try:
            script = build_list_script(MEDIA_EXTENSIONS_BY_MODE["all"])
            proc = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", startupinfo=_hidden_startupinfo(), timeout=120)
            payload = _extract_result_json(proc.stdout)
            if isinstance(payload, dict) and "error" in payload:
                payload = None
            elif isinstance(payload, dict):
                payload = [payload]
            self.listed.emit(payload)
        except Exception:
            self.listed.emit(None)


class WpdError(Exception):
    pass


def _wpd_is_timeout(exc):
    hr = getattr(exc, "hresult", None)
    if hr is None and getattr(exc, "args", None):
        hr = exc.args[0]
    return hr in (-2147023436, 0x800705B4)       # ERROR_TIMEOUT: la camara no contesta


class WpdCameraSession:
    """Conexion directa con la camara por WPD (el protocolo MTP), sin pasar por
    el Explorador: sin ventanas de Windows y con progreso exacto por bytes.
    Usar siempre desde UN solo hilo (el que llamo a open())."""
    CLIENT_FMT = "{204D9F0C-2292-4080-9F42-40664E70F859}"

    def __init__(self, name_match="HERO"):
        self.name_match = name_match.upper()
        self.dev = None
        self.objects = {}            # NOMBRE -> objectID
        self.by_folder = {}          # (CARPETA, NOMBRE) -> objectID
        self._com = False

    # ---- conexion
    def open(self, stop=lambda: False, open_attempts=6, enum_attempts=8):
        """Abre la camara y recorre sus archivos. Devuelve True si quedo lista."""
        try:
            import comtypes
            import comtypes.client
            from comtypes import GUID
            from ctypes import POINTER, byref, c_ulong, cast
        except Exception as e:
            print("[Camara WPD] falta comtypes:", e)
            return False
        self.comtypes, self.GUID, self.POINTER, self.byref, self.c_ulong, self.cast = (
            comtypes, GUID, POINTER, byref, c_ulong, cast)
        comtypes.CoInitialize()
        self._com = True
        try:
            self.api, self.types = _wpd_modules()
            self.k_friendly = self._pkey("{26D4979A-E643-4626-9E2B-736DC0C92FDC}", 12)
            self.k_name = self._pkey("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 12)
            self.k_thumb = self._pkey("{C7C407BA-98FA-46B5-9960-23FEC124CFDE}", 0)
            self.k_data = self._pkey("{E81E79BE-34F0-41BF-B53F-F1A06AE87842}", 0)
            mgr = comtypes.client.CreateObject(GUID("{0af10cec-2ecd-4b92-9581-34f6ae0637f3}"),
                                               interface=self.api.IPortableDeviceManager)
            for _ in range(open_attempts):          # la camara puede estar ocupada un momento
                if stop():
                    return False
                self.dev = self._find_device(mgr)
                if self.dev is not None:
                    break
                time.sleep(1.0)
            if self.dev is None:
                print("[Camara WPD] no se pudo abrir la camara")
                return False
            content = self.dev.Content()
            self.props = content.Properties()
            self.res = content.Transfer()
            self._content = content
            for attempt in range(enum_attempts):    # recien conectada contesta "ocupada" un rato
                try:
                    self._enumerate(stop)
                    return True
                except Exception as e:
                    if stop() or attempt == enum_attempts - 1:
                        raise
                    print("[Camara WPD] ocupada, reintento:", e)
                    time.sleep(3.0)
        except Exception as e:
            print("[Camara WPD]", e)
            return False
        return False

    def close(self):
        try:
            if self.dev is not None:
                self.dev.Close()
        except Exception:
            pass
        self.dev = None
        if self._com:
            self._com = False
            try:
                self.comtypes.CoUninitialize()
            except Exception:
                pass

    def _pkey(self, fmt, pid, lib=None):
        k = (lib or self.api)._tagpropertykey()
        k.fmtid = self.GUID(fmt)
        k.pid = pid
        return k

    def _find_device(self, mgr):
        c, byref, cast, POINTER = self.comtypes, self.byref, self.cast, self.POINTER
        mgr.RefreshDeviceList()
        cnt = self.c_ulong(0)
        mgr._IPortableDeviceManager__com_GetDevices(None, byref(cnt))
        if not cnt.value:
            return None
        arr = (c.c_wchar_p * cnt.value)()
        mgr._IPortableDeviceManager__com_GetDevices(cast(arr, POINTER(c.c_wchar_p)), byref(cnt))
        for did in list(arr):
            d = c.client.CreateObject(self.GUID("{728a21c5-3d9e-48d7-9810-864848f0f404}"),
                                      interface=self.api.IPortableDevice)
            cv = c.client.CreateObject(self.GUID("{0c15d503-d017-47ce-9016-7b3f978721cc}"),
                                       interface=self.types.IPortableDeviceValues)
            cv.SetStringValue(byref(self._pkey(self.CLIENT_FMT, 2, self.types)), "GoPro Sync")
            for pid, val in ((3, 1), (4, 0), (5, 0), (9, 0x80000000)):   # 9 = solo lectura
                cv.SetUnsignedIntegerValue(byref(self._pkey(self.CLIENT_FMT, pid, self.types)), val)
            try:
                d.Open(did, cv)
                name = d.Content().Properties().GetValues("DEVICE", None).GetStringValue(byref(self.k_friendly))
            except Exception:
                continue
            if self.name_match in (name or "").upper():
                return d
            d.Close()
        return None

    def _children(self, oid):
        c, byref = self.comtypes, self.byref
        out = []
        enum = self._content.EnumObjects(0, oid, None)
        while True:
            ids = (c.c_wchar_p * 64)()
            fetched = self.c_ulong(0)
            enum._IEnumPortableDeviceObjectIDs__com_Next(64, self.cast(ids, self.POINTER(c.c_wchar_p)), byref(fetched))
            if not fetched.value:
                break
            out += [ids[i] for i in range(fetched.value)]
        return out

    def _enumerate(self, stop):
        objects, by_folder = {}, {}
        stack = [("DEVICE", "")]
        while stack and not stop():
            oid, folder = stack.pop()
            for ch in self._children(oid):
                try:
                    n = self.props.GetValues(ch, None).GetStringValue(self.byref(self.k_name)) or ""
                except Exception:
                    n = ""
                if "." in n:
                    objects[n.upper()] = ch
                    by_folder[(folder.upper(), n.upper())] = ch
                else:
                    stack.append((ch, n))
        self.objects, self.by_folder = objects, by_folder

    def object_id(self, name, folder=None):
        if folder:
            oid = self.by_folder.get((folder.upper(), name.upper()))
            if oid is not None:
                return oid
        return self.objects.get(name.upper())

    # ---- lectura
    def _stream(self, oid, key):
        r = self.res.GetStream(oid, self.byref(key), 0, 0)
        cand = list(r) if isinstance(r, (list, tuple)) else [r]
        st = next((x for x in cand if hasattr(x, "RemoteRead")), None)
        if st is None:
            raise WpdError("sin stream")
        return st

    def thumbnail(self, name):
        oid = self.object_id(name)
        if oid is None:
            raise WpdError("no esta en la camara")
        st = self._stream(oid, self.k_thumb)
        data = []
        while True:
            buf, got = st.RemoteRead(65536)
            if not got:
                break
            data.append(bytes(buf[:got]))
        return b"".join(data)

    def copy_to(self, name, dest, folder=None, progress=None, cancel=lambda: False, chunk=1024 * 1024):
        """Copia el archivo de la camara a 'dest' (via .part). progress(bytes).
        Devuelve False si se cancelo. Lanza excepcion si fallo."""
        oid = self.object_id(name, folder)
        if oid is None:
            raise WpdError("no esta en la camara")
        part = dest + ".part"
        done = 0
        st = self._stream(oid, self.k_data)
        try:
            with open(part, "wb") as f:
                while True:
                    if cancel():
                        return False
                    buf, got = st.RemoteRead(chunk)
                    if not got:
                        break
                    f.write(bytes(buf[:got]))
                    done += got
                    if progress:
                        progress(done)
                f.flush()
                os.fsync(f.fileno())      # a disco ANTES de renombrar (corte de luz / borrar de la camara)
            os.replace(part, dest)
            return True
        finally:
            del st                         # cerrar el stream prolijo (libera la lectura en la camara)
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass


class WpdCameraWorker(QThread):
    """Lector de la galeria de la camara en su propio hilo: miniaturas (las da
    la camara, unos KB, sin copiar nada) y copias puntuales para la vista previa."""
    thumb_ready = Signal(str, QImage)    # nombre, miniatura (nula si fallo)
    file_ready = Signal(str, str)        # nombre, ruta local ("" si fallo)
    names_ready = Signal(object)         # conjunto de nombres de la camara
    not_responding = Signal()            # la camara dejo de contestar (MTP trabado)

    def __init__(self, name_match="HERO", parent=None):
        super().__init__(parent)
        self.name_match = name_match
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._thumbs = []
        self._files = []
        self._stop = False
        self.objects = {}
        self._timeouts = 0
        self.dead = False

    def set_thumb_requests(self, names):
        with self._lock:
            self._thumbs = list(names)       # solo lo que se ve AHORA (lo viejo se descarta)
        self._event.set()

    def add_thumb_request(self, name):
        with self._lock:
            if name not in self._thumbs:
                self._thumbs.append(name)
        self._event.set()

    def request_file(self, name, dest):
        with self._lock:
            self._files.append((name, dest))
        self._event.set()

    def stop(self):
        self._stop = True
        self._event.set()

    def _note_error(self, exc):
        if _wpd_is_timeout(exc):
            self._timeouts += 1
            if self._timeouts >= 3 and not self.dead:
                self.dead = True
                print("[Camara WPD] la camara no responde")
                self.not_responding.emit()

    def run(self):
        cam = WpdCameraSession(self.name_match)
        try:
            if not cam.open(stop=lambda: self._stop):
                return
            self.objects = cam.objects
            self.names_ready.emit(set(cam.objects))
            while not self._stop:
                self._event.wait(0.5)
                self._event.clear()
                while not self._stop:
                    with self._lock:
                        job = ("file",) + self._files.pop(0) if self._files else (
                            ("thumb", self._thumbs.pop(0)) if self._thumbs else None)
                    if job is None:
                        break
                    if self.dead:                 # no insistir: cada intento tarda 3 s
                        if job[0] == "file":
                            self.file_ready.emit(job[1], "")
                        else:
                            self.thumb_ready.emit(job[1], QImage())
                        continue
                    if job[0] == "file":
                        name, dest = job[1], job[2]
                        try:
                            # sin cancelar a mitad: la vista previa es chica y termina sola
                            cam.copy_to(name, dest)
                            self._timeouts = 0
                            self.file_ready.emit(name, dest)
                        except Exception as e:
                            self._note_error(e)
                            self.file_ready.emit(name, "")
                    else:
                        name = job[1]
                        img = QImage()
                        try:
                            img = QImage.fromData(cam.thumbnail(name))
                            self._timeouts = 0
                        except Exception as e:
                            self._note_error(e)
                        self.thumb_ready.emit(name, img)
        except Exception as e:
            print("[Camara WPD]", e)
        finally:
            cam.close()


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


class TabHoverSoundFilter(QObject):
    """QTabBar zeichnet seine Tabs selbst (keine echten QPushButtons) - der
    globale ClickSoundFilter/GlowHoverAnimator erfasst Hover darauf also
    nicht automatisch. Dieser Filter spielt beim Wechsel auf einen anderen
    Tab (unter dem Mauszeiger) einen Hover-Sound ab, damit sich die Tabs im
    Settings-Dialog genauso anfuehlen wie alle anderen Buttons."""
    def __init__(self, tab_bar, parent=None):
        super().__init__(parent)
        self.tab_bar = tab_bar
        self._last_index = -1
        tab_bar.setMouseTracking(True)

    def eventFilter(self, obj, event):
        if obj is self.tab_bar:
            if event.type() == QEvent.Type.MouseMove:
                idx = self.tab_bar.tabAt(event.position().toPoint())
                if idx != -1 and idx != self._last_index:
                    SoundManager.play_hover()
                self._last_index = idx
            elif event.type() == QEvent.Type.Leave:
                self._last_index = -1
        return False


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
        # Der restliche Dialog-Text selbst wird erst beim naechsten Oeffnen
        # neu aufgebaut (der Dialog ist modal - der Rest der App links im
        # Hintergrund uebernimmt die neue Sprache aber sofort).


APP_POPUP_STYLE = """
/* ---- Estilo general de la app: se aplica a TODO (tambien a las ventanas que
   se abren aparte), asi nada queda con el aspecto por defecto de Windows ---- */
QDialog { background-color: #1c1e29; }
QDialog QLabel { color: #ffffff; }
QMenu {
    background-color: #1f2230; color: #ffffff; border: 1px solid rgba(255,255,255,0.14);
    border-radius: 10px; padding: 6px; font-size: 13px;
}
QMenu::item { padding: 7px 22px 7px 14px; border-radius: 7px; background: transparent; color: #ffffff; }
QMenu::item:selected { background-color: ACCENT_SOFT; color: #ffffff; }
QMenu::item:disabled { color: rgba(255,255,255,0.45); }
QMenu::separator { height: 1px; background: rgba(255,255,255,0.12); margin: 5px 8px; }
QToolTip {
    background-color: #1f2230; color: #ffffff; border: 1px solid rgba(255,255,255,0.18);
    border-radius: 6px; padding: 5px 8px; font-size: 12px;
}
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px 4px 0px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0px 4px 2px 4px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.18); min-height: 30px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: rgba(255,255,255,0.18); min-width: 30px; border-radius: 5px; }
QScrollBar::handle:hover { background: rgba(255,255,255,0.32); }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QSlider::groove:horizontal { height: 6px; background: rgba(255,255,255,0.12); border-radius: 3px; }
QSlider::sub-page:horizontal { background: ACCENT; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background: ACCENT; }
QLineEdit, QDateEdit {
    background: rgba(255,255,255,0.06); color: #ffffff; border: 1px solid rgba(255,255,255,0.15);
    border-radius: 8px; padding: 6px 10px; selection-background-color: ACCENT_SOFT;
}
QLineEdit:focus, QDateEdit:focus { border: 1px solid ACCENT; }
QDateEdit::drop-down { border: none; width: 22px; }
QCalendarWidget QWidget { background-color: #1f2230; color: #ffffff; alternate-background-color: #1f2230; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background-color: #262a3a; }
QCalendarWidget QToolButton {
    color: #ffffff; background: transparent; border: none; border-radius: 6px; padding: 4px 8px; font-weight: 700;
}
QCalendarWidget QToolButton:hover { background: rgba(255,255,255,0.10); }
QCalendarWidget QAbstractItemView {
    background-color: #1f2230; color: #ffffff; selection-background-color: ACCENT; selection-color: #0d0d12;
    outline: none;
}
QCalendarWidget QAbstractItemView:disabled { color: rgba(255,255,255,0.3); }
QCalendarWidget QSpinBox { background: #262a3a; color: #ffffff; border: none; }
QColorDialog { background-color: #1c1e29; }
QColorDialog QLabel, QColorDialog QSpinBox, QColorDialog QLineEdit { color: #ffffff; }
QColorDialog QSpinBox { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; }
QDialog QPushButton:!flat {
    background: rgba(255,255,255,0.08); color: #ffffff; border: 1px solid rgba(255,255,255,0.14);
    border-radius: 10px; padding: 7px 16px; font-weight: 600;
}
QDialog QPushButton:hover { background: rgba(255,255,255,0.16); }
QTreeWidget {
    background: rgba(255,255,255,0.03); color: #ffffff; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px; alternate-background-color: rgba(255,255,255,0.025); outline: none; font-size: 13px;
}
QTreeWidget::item { padding: 4px 2px; border: none; }
QTreeWidget::item:selected { background: ACCENT_SOFT; color: #ffffff; }
QTreeWidget::item:hover { background: rgba(255,255,255,0.06); }
QTreeWidget::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.35); background: rgba(255,255,255,0.05); }
QTreeWidget::indicator:checked { background: ACCENT; border: 1px solid ACCENT; }
QHeaderView::section {
    background: #262a3a; color: rgba(255,255,255,0.7); border: none; padding: 6px 8px; font-weight: 700; font-size: 12px;
}
"""


def apply_popup_style(accent="#00f2fe"):
    """Menus contextuales y tooltips con los colores de la app en TODA la
    aplicacion (antes usaban los de Windows: texto/fondo que no combinaban)."""
    app = QApplication.instance()
    if app is None:
        return
    c = QColor(accent)
    css = (APP_POPUP_STYLE.replace("ACCENT_SOFT", f"rgba({c.red()},{c.green()},{c.blue()},0.25)")
           .replace("ACCENT", c.name()))
    base = getattr(app, "_base_style", None)
    if base is None:
        base = app.styleSheet() or ""
        app._base_style = base
    app.setStyleSheet(base + css)


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
        self.taskbar = TaskbarProgress(self)
        self.sync_page.activity_progress.connect(self.taskbar.set)

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

    def backup_from_menu(self):
        """Respaldar a disco: dialogo con carpeta a respaldar + destino
        (cambiables) y confirmacion; la copia sigue en segundo plano."""
        dirs = list(self.config.get("backup_dirs") or [])
        last_src = self.config.get("backup_last_source") or ""
        source = last_src if last_src and os.path.isdir(last_src) else self.sync_page.sync_target_dir
        dest = self.config.get("backup_last_dest") or (dirs[0] if dirs else "")
        dlg = BackupDialog(self, self.lang, source, dest, self.config.get("accent_color", "#00f2fe"),
                           remember_default=self.config.get("backup_last_remember", True))
        if not dlg.exec() or not dlg.dest:
            return
        # La proxima vez el dialogo arranca con lo mismo que se eligio ahora.
        self.config["backup_last_source"] = dlg.source
        self.config["backup_last_dest"] = dlg.dest
        self.config["backup_last_remember"] = dlg.remember()
        if dlg.remember() and not any(os.path.normcase(d) == os.path.normcase(dlg.dest) for d in dirs):
            dirs.append(dlg.dest)
            self.config["backup_dirs"] = dirs
        save_config(self.config)
        self.show_activity()
        self.sync_page.start_backup(dlg.source, [dlg.dest])

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
        for attr in ("sync_worker", "backup_worker"):
            w = getattr(sp, attr, None)
            if w is not None and w.isRunning():
                w.cancel()
        for attr in ("sync_worker", "backup_worker"):
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
        for attr in ("sync_worker", "backup_worker"):
            w = getattr(sp, attr, None)
            if w is not None and w.isRunning():
                w.cancel()
        for attr in ("sync_worker", "backup_worker"):
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

        # Sin spinner: durante la construccion de la ventana Qt no puede
        # redibujar (p. ej. ~1 s cargando las fuentes) y un spinner quieto
        # parece colgado. La barra avanza por etapas y nunca "se traba".


# Progreso del arranque: main() lo conecta a la pantalla de carga. Entre
# etapa y etapa se procesan eventos, asi la animacion no se queda trabada.
BOOT_PROGRESS = None


def boot_step(pct, key):
    if BOOT_PROGRESS is not None:
        try:
            BOOT_PROGRESS(pct, key)
        except Exception:
            pass


def bring_to_front(window):
    """Muestra la ventana y le da el foco de verdad. Windows no deja que una
    app se ponga al frente si otra tiene el foco (y al cerrarse la pantalla
    de carga se lo devolvia a la ventana anterior): se usa AttachThreadInput
    para que SetForegroundWindow funcione."""
    window.show()
    window.setWindowState((window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
    window.raise_()
    window.activateWindow()
    if os.name != "nt":
        return
    try:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        hwnd = int(window.winId())
        fg = u32.GetForegroundWindow()
        fg_thread = u32.GetWindowThreadProcessId(fg, None) if fg else 0
        me = k32.GetCurrentThreadId()
        attached = bool(fg_thread and fg_thread != me and u32.AttachThreadInput(fg_thread, me, True))
        try:
            u32.ShowWindow(hwnd, 9)          # SW_RESTORE
            u32.BringWindowToTop(hwnd)
            u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u32.AttachThreadInput(fg_thread, me, False)
        if u32.GetForegroundWindow() != hwnd:
            # Si Windows sigue bloqueando el cambio de foco (la app la abrio
            # otro proceso en segundo plano), una pulsacion de Alt cuenta como
            # interaccion del usuario y habilita SetForegroundWindow.
            VK_MENU, KEYUP = 0x12, 0x0002
            u32.keybd_event(VK_MENU, 0, 0, 0)
            u32.SetForegroundWindow(hwnd)
            u32.keybd_event(VK_MENU, 0, KEYUP, 0)
    except Exception:
        pass


def apply_dark_titlebar(hwnd):
    """Barra de titulo oscura (Windows 10 1809+ / 11) para que las ventanas
    auxiliares no queden con la barra blanca sobre la app oscura."""
    try:
        hwnd = int(hwnd)
        val = ctypes.c_int(1)
        for attr in (20, 19):            # DWMWA_USE_IMMERSIVE_DARK_MODE (19 = Windows 10 viejos)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        # Repintar el marco ya mismo (si no, se ve claro hasta el proximo cambio de foco)
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    except Exception:
        pass


def install_dark_titlebars(app):
    """Cada ventana nueva (dialogos, visores) recibe la barra oscura al
    activarse; se marca para no repetirlo."""
    done = set()

    def on_focus(win):
        if win is None:
            return
        try:
            wid = int(win.winId())
        except Exception:
            return
        if wid and wid not in done:
            done.add(wid)
            apply_dark_titlebar(wid)
    app.focusWindowChanged.connect(on_focus)


class TaskbarProgress:
    """Progreso en el icono de la barra de tareas (como el Explorador al
    copiar), via ITaskbarList3."""
    TBPF_NOPROGRESS, TBPF_NORMAL = 0, 2

    def __init__(self, window):
        self.window = window
        self.tb = None
        self.last = None
        try:
            import comtypes
            import comtypes.client
            from comtypes import GUID, IUnknown, COMMETHOD, HRESULT
            from ctypes import c_int, c_ulonglong, wintypes

            class ITaskbarList(IUnknown):
                _iid_ = GUID("{56FDF342-FD6D-11d0-958A-006097C9A090}")
                _methods_ = [COMMETHOD([], HRESULT, "HrInit"),
                             COMMETHOD([], HRESULT, "AddTab", (["in"], wintypes.HWND, "hwnd")),
                             COMMETHOD([], HRESULT, "DeleteTab", (["in"], wintypes.HWND, "hwnd")),
                             COMMETHOD([], HRESULT, "ActivateTab", (["in"], wintypes.HWND, "hwnd")),
                             COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], wintypes.HWND, "hwnd"))]

            class ITaskbarList2(ITaskbarList):
                _iid_ = GUID("{602D4995-B13A-429b-A66E-1935E44F4317}")
                _methods_ = [COMMETHOD([], HRESULT, "MarkFullscreenWindow",
                                       (["in"], wintypes.HWND, "hwnd"), (["in"], wintypes.BOOL, "full"))]

            class ITaskbarList3(ITaskbarList2):
                _iid_ = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
                _methods_ = [COMMETHOD([], HRESULT, "SetProgressValue", (["in"], wintypes.HWND, "hwnd"),
                                       (["in"], c_ulonglong, "done"), (["in"], c_ulonglong, "total")),
                             COMMETHOD([], HRESULT, "SetProgressState", (["in"], wintypes.HWND, "hwnd"),
                                       (["in"], c_int, "flags"))]

            self.tb = comtypes.client.CreateObject(GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}"),
                                                   interface=ITaskbarList3)
            self.tb.HrInit()
        except Exception as e:
            print("[Barra de tareas] sin progreso:", e)
            self.tb = None

    def set(self, pct):
        """pct 0-100, o None para quitarlo."""
        value = None if pct is None else int(round(pct * 10))
        if value == self.last:
            return
        self.last = value
        self.apply()

    def apply(self):
        if self.tb is None:
            return
        try:
            hwnd = int(self.window.winId())
            if self.last is None:
                self.tb.SetProgressState(hwnd, self.TBPF_NOPROGRESS)
            else:
                self.tb.SetProgressState(hwnd, self.TBPF_NORMAL)
                self.tb.SetProgressValue(hwnd, max(0, min(1000, self.last)), 1000)
        except Exception:
            pass


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


def main():
    minimize_own_console()
    app = QApplication(sys.argv)
    app.setWindowIcon(load_app_icon())
    install_dark_titlebars(app)

    config = load_config()
    def get_glow_color():
        return config.get("accent_color", "#00f2fe")

    SoundManager.configure(config.get("ui_sound_volume", 70), config.get("ui_sounds_muted", False))
    SoundManager.preload_all()

    glow_animator = GlowHoverAnimator(get_glow_color)
    app.installEventFilter(glow_animator)
    app._glow_animator = glow_animator

    click_filter = ClickSoundFilter()
    app.installEventFilter(click_filter)
    app._click_filter = click_filter

    # "--silent" wird von set_autostart() beim Windows-Login-Eintrag
    # mitgegeben. Ist zusaetzlich "App bei GoPro-Anschluss oeffnen"
    # aktiviert, startet die App dann OHNE Splash/Fenster und wartet still
    # im Hintergrund, bis tatsaechlich eine Kamera angeschlossen wird.
    silent_start = "--silent" in sys.argv
    open_on_connect = silent_start and config.get("open_on_gopro_connect", False) and config.get("autostart_enabled", False)

    if silent_start:
        # Inicio con Windows: SIN pantalla de carga ni ventana. Queda el icono
        # junto al reloj vigilando la camara; la ventana se abre con un clic
        # en el icono (o sola al conectar la GoPro si esta activado).
        window = MainWindow()
        app.commitDataRequest.connect(window.on_session_end)
        setup_tray(app, window, config.get("language", "es"))
        if open_on_connect:
            window.sync_page.monitor_thread.connection_changed.connect(window.show_from_background_on_connect)
        if window.config.get("onboarding_done", False):
            window.sync_page.start_monitoring()
    else:
        # WICHTIG: Splash zuerst zeigen, DANACH erst MainWindow() bauen.
        # Vorher war es umgekehrt - MainWindow() baut die komplette Sync-
        # Seite, beide Galerien und ggf. den Einrichtungsassistenten auf,
        # was spuerbar Zeit braucht. Solange stand der Nutzer vor einem
        # komplett leeren Bildschirm, OHNE dass ueberhaupt das Ladebild zu
        # sehen war ("es passiert erstmal lange nichts"). Jetzt erscheint
        # das Ladebild sofort, WAEHREND im Hintergrund das Fenster gebaut wird.
        splash = AnimatedSplash(asset_path("GoPro Logo.png"))
        splash.show()
        app.processEvents()
        SoundManager.play_boot()

        boot_lang = config.get("language", "es")

        def _boot(pct, key):
            splash.set_status(pct, tr(boot_lang, key))
            splash.angle = (splash.angle + 30) % 360
            app.processEvents()

        global BOOT_PROGRESS
        BOOT_PROGRESS = _boot
        start_time = time.monotonic()
        window = MainWindow()
        app.commitDataRequest.connect(window.on_session_end)
        BOOT_PROGRESS = None
        splash.set_status(100, tr(boot_lang, "boot_done"))
        build_ms = (time.monotonic() - start_time) * 1000

        if config.get("autostart_enabled", False):
            setup_tray(app, window, boot_lang)

        def _reveal_main_window():
            # Primero la ventana al frente y DESPUES cerrar la pantalla de
            # carga: si no, Windows devolvia el foco a la ventana anterior y
            # la app quedaba en segundo plano.
            bring_to_front(window)
            splash.timer.stop()
            splash.close()
            QTimer.singleShot(150, lambda: bring_to_front(window))
            # FIX: Startet die Abfrage der GoPro erst HIER.
            # Dadurch triggert das Video garantiert erst, wenn die UI da ist!
            # Laeuft der Einrichtungsassistent noch (erster Start), wird das
            # Monitoring bewusst NICHT gestartet - sonst wuerde bei bereits
            # angeschlossener GoPro im Hintergrund sofort das Verbindungs-
            # Video anlaufen, waehrend man noch mitten in der Einrichtung ist.
            # Startet stattdessen automatisch in on_onboarding_finished().
            if window.config.get("onboarding_done", False):
                window.sync_page.start_monitoring()
                # Instalaciones anteriores sin idioma elegido: preguntarlo una vez.
                QTimer.singleShot(300, window.ask_language_if_needed)

        # Ladebild insgesamt ca. 1.4s zeigen - NICHT als fixer Zuschlag ON TOP
        # der (jetzt bereits waehrenddessen sichtbaren) Bauzeit, sondern
        # abzueglich der Zeit, die window = MainWindow() gerade schon
        # gedauert hat. War die Bauzeit laenger als 1.4s, wird trotzdem
        # noch kurz (200ms) nachgewartet, damit der Uebergang nicht
        # abrupt wirkt.
        remaining_ms = max(0, 500 - int(build_ms))   # antes: minimo fijo de 1,4 s
        QTimer.singleShot(remaining_ms, _reveal_main_window)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
