"""Integracion con el sistema: ventanas de Windows, barra de tareas, papelera, inicio automatico."""
import ctypes
import os
import subprocess
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from .env import APP_DIR, IS_MAC, IS_WINDOWS, winreg

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


def _app_command():
    if getattr(sys, "frozen", False):
        if IS_MAC and ".app/Contents/MacOS/" in sys.executable:
            return ["open", "-a", sys.executable.split(".app/")[0] + ".app", "--args", "--silent"]
        return [os.environ.get("APPIMAGE") or sys.executable, "--silent"]
    return [sys.executable, os.path.join(APP_DIR, "main.py"), "--silent"]


def _set_autostart_posix(enabled):
    try:
        if IS_MAC:
            path = os.path.expanduser("~/Library/LaunchAgents/com.gopro-sync.app.plist")
            if enabled:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                args = "".join(f"<string>{a}</string>" for a in _app_command())
                with open(path, "w", encoding="utf-8") as f:
                    f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
                            '<key>Label</key><string>com.gopro-sync.app</string>'
                            f'<key>ProgramArguments</key><array>{args}</array>'
                            '<key>RunAtLoad</key><true/></dict></plist>\n')
            elif os.path.exists(path):
                os.remove(path)
        else:
            path = os.path.join(os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                "autostart", "gopro-sync.desktop")
            if enabled:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                cmd = " ".join(f'"{a}"' for a in _app_command())
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"[Desktop Entry]\nType=Application\nName=GoPro Sync\nExec={cmd}\n"
                            "X-GNOME-Autostart-enabled=true\n")
            elif os.path.exists(path):
                os.remove(path)
    except Exception as e:
        print("[Inicio automatico]", e)


def autostart_registered():
    """Si "Iniciar con Windows" esta activo de verdad (lo puede activar el
    instalador o quitarlo el usuario desde Windows). None si no se puede saber."""
    if IS_WINDOWS:
        if winreg is None:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "GoProSyncPro")
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return None
    if IS_MAC:
        return os.path.exists(os.path.expanduser("~/Library/LaunchAgents/com.gopro-sync.app.plist"))
    return os.path.exists(os.path.join(os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                       "autostart", "gopro-sync.desktop"))


def set_autostart(enabled):
    """Traegt GoPro Sync Pro in den Windows-Autostart ein bzw. entfernt den
    Eintrag wieder (HKEY_CURRENT_USER, kein Adminrecht noetig). Der Eintrag
    startet die App mit "--silent", damit sie beim Windows-Login nicht
    sofort ein Fenster aufreisst (siehe main() / open_on_gopro_connect)."""
    if not IS_WINDOWS:
        _set_autostart_posix(enabled)
        return
    if winreg is None:
        return
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            exe = sys.executable
            script = os.path.join(APP_DIR, "main.py")
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


def move_to_recycle_bin(path):
    """Mueve un archivo a la Papelera de reciclaje (no lo borra para siempre).
    Devuelve True si el archivo ya no esta en su lugar."""
    if not IS_WINDOWS:
        try:
            from PySide6.QtCore import QFile
            return bool(QFile.moveToTrash(path)) or not os.path.exists(path)
        except Exception:
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


def show_in_explorer(path):
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif IS_MAC:
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) or path])
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


def _install_dialog_dark_titlebar():
    """Todo dialogo (propio o de Qt) pone la barra oscura justo al mostrarse,
    antes de dibujarse: antes se aplicaba al recibir el foco y se veia un
    instante con el estilo claro de Windows."""
    if not IS_WINDOWS or getattr(QDialog, "_dark_titlebar_hooked", False):
        return
    original = QDialog.showEvent

    def show_event(self, event):
        apply_dark_titlebar(int(self.winId()))
        original(self, event)
    QDialog.showEvent = show_event
    QDialog._dark_titlebar_hooked = True


def install_dark_titlebars(app):
    """Cada ventana nueva (dialogos, visores) recibe la barra oscura al
    activarse; se marca para no repetirlo."""
    _install_dialog_dark_titlebar()
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
