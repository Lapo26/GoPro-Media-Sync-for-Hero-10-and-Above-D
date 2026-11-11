"""Sonidos de la interfaz."""
import array
import os
import random
import sys
import tempfile
import wave
from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import QAbstractButton
from .env import asset_path, winsound

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
