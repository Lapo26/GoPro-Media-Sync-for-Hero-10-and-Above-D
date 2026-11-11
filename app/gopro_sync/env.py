"""Plataforma, rutas, configuracion persistente y estado global compartido."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from PySide6.QtCore import QStandardPaths

try:
    import winreg  # nur unter Windows verfuegbar (fuer Autostart-Eintrag)
except ImportError:
    winreg = None
try:
    import winsound  # nur unter Windows verfuegbar (fuer den Hover-Sound)
except ImportError:
    winsound = None

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

IS_WINDOWS = os.name == "nt"
# Carpeta "app" (o la del ejecutable compilado): ahi estan assets/ y main.py.
APP_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IS_MAC = sys.platform == "darwin"


def _config_base():
    # Windows: igual que siempre (%APPDATA%), para no perder la configuracion existente.
    if IS_WINDOWS:
        return os.getenv("APPDATA") or os.path.expanduser("~")
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support")
    return os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


CONFIG_DIR = os.path.join(_config_base(), "GoProSyncPro")
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
        if IS_WINDOWS:
            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return {0x0A: "es", 0x07: "de"}.get(primary, "en")
        from PySide6.QtCore import QLocale
        code = QLocale.system().name()[:2].lower()
        return code if code in ("es", "de") else "en"
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
    return os.path.join(APP_DIR, "assets", filename)


# --- [ GOPRO STATUS & API SWITCH ] ---

def _hidden_startupinfo():
    if not IS_WINDOWS:
        return None                      # STARTUPINFO solo existe en Windows
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si
