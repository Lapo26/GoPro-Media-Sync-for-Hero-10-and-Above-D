"""Arranque de la app y autoprueba (--selftest)."""
import json
import os
import sys
import tempfile
import time
import traceback
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from .env import CONFIG_DIR, CONFIG_FILE, IS_WINDOWS, asset_path, load_config, minimize_own_console, save_config
from .i18n import tr
from .winshell import autostart_registered, bring_to_front, install_dark_titlebars
from .sound import ClickSoundFilter, SoundManager
from .camera import GPHOTO
from .videotools import ffmpeg_exe
from .ui.graphics import GlowHoverAnimator, load_app_icon, set_boot_progress
from .ui.main_window import AnimatedSplash, MainWindow, setup_tray

def main():
    minimize_own_console()
    app = QApplication(sys.argv)
    app.setWindowIcon(load_app_icon())
    install_dark_titlebars(app)       # respaldo: otras ventanas, al recibir el foco

    config = load_config()
    # "Iniciar con Windows" puede haberlo activado el instalador (o quitado el
    # usuario desde Windows): Ajustes muestra el estado real.
    real = autostart_registered()
    if real is not None and real != bool(config.get("autostart_enabled", False)):
        config["autostart_enabled"] = real
        save_config(config)
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

        set_boot_progress(_boot)
        start_time = time.monotonic()
        window = MainWindow()
        app.commitDataRequest.connect(window.on_session_end)
        set_boot_progress(None)
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

def run_selftest():
    """Arranque sin ventana para CI (macOS/Linux/Windows): construye la
    ventana principal, comprueba los componentes y sale."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"onboarding_done": True, "language": "es", "language_chosen": True,
                       "target_dir": os.path.join(tempfile.gettempdir(), "gpsync-selftest")}, f)
    app = QApplication(sys.argv[:1])
    ok = True
    try:
        w = MainWindow()
        w.show()
        end = time.monotonic() + 3
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.05)
        print("SELFTEST ventana: OK")
    except Exception:
        traceback.print_exc()
        ok = False
    print("SELFTEST ffmpeg:", ffmpeg_exe() or "FALTA")
    ok = ok and bool(ffmpeg_exe())
    if not IS_WINDOWS:
        has_gp = bool(GPHOTO.gp())
        print("SELFTEST gphoto2:", "OK" if has_gp else "FALTA")
        ok = ok and has_gp
        if has_gp:
            gp = GPHOTO.gp()
            try:
                ports = gp.PortInfoList()
                ports.load()
                abilities = gp.CameraAbilitiesList()
                abilities.load()
                n_ports, n_cams = ports.count(), abilities.count()
            except Exception as e:
                print("SELFTEST gphoto2 error:", e)
                n_ports = n_cams = 0
            # sin estos modulos libgphoto2 no ve ninguna camara (empaquetado incompleto)
            print(f"SELFTEST gphoto2 puertos: {n_ports} | modelos conocidos: {n_cams}")
            ok = ok and n_ports > 0 and n_cams > 0
            print("SELFTEST camara:", GPHOTO.autodetect() or "ninguna conectada")
    print("SELFTEST", "OK" if ok else "FALLO")
    return 0 if ok else 1
