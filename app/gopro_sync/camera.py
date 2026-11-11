"""Deteccion de la camara (MTP en Windows, libgphoto2 en macOS/Linux), bateria y espacio."""
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from PySide6.QtCore import QThread, Signal
from .env import (
    ACTIVE_SOURCE,
    CAMERA_BROWSING,
    CONFIG_DIR,
    GOPRO_MODEL_MATCH_PATTERN,
    GOPRO_MODEL_ORDER,
    IS_MAC,
    IS_WINDOWS,
    LAST_DEVICE_NAME,
    SYNC_IN_PROGRESS,
    _hidden_startupinfo,
)
from .i18n import _fmt_gb

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

# Globaler Cache für den API-Akkuwert
GLOBAL_CACHED_BATTERY = None


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


class GPhotoBackend:
    """Camara por USB en macOS y Linux via libgphoto2 (el paquete de Python
    'gphoto2' ya trae la biblioteca). Una sola conexion compartida y
    protegida con un lock: libgphoto2 no admite dos usos a la vez."""

    def __init__(self):
        self.lock = threading.RLock()
        self.cam = None
        self.name = None
        self._gp = None
        self._last = (0.0, None)

    def gp(self):
        if self._gp is None:
            try:
                import gphoto2 as gp
                self._gp = gp
            except Exception as e:
                print("[gphoto2] no disponible:", e)
                self._gp = False
        return self._gp or None

    def autodetect(self):
        gp = self.gp()
        if not gp:
            return None
        now = time.monotonic()
        if now - self._last[0] < 2.0:
            return self._last[1]
        name = None
        try:
            for nm, _addr in gp.Camera.autodetect():
                if "gopro" in nm.lower() or "hero" in nm.lower():
                    name = nm
                    break
        except Exception as e:
            print("[gphoto2] autodetect:", e)
        if name is None and self.cam is not None:
            self.close()                         # se desconecto
        self._last = (now, name)
        self.name = name
        return name

    def open(self):
        gp = self.gp()
        if not gp:
            raise RuntimeError("gphoto2")
        with self.lock:
            if self.cam is None:
                if IS_MAC:
                    # macOS toma la camara con PTPCamera (Fotos / Captura de Imagen) y no la suelta
                    subprocess.run(["killall", "PTPCamera"], capture_output=True)
                cam = gp.Camera()
                cam.init()
                self.cam = cam
            return self.cam

    def close(self):
        with self.lock:
            if self.cam is not None:
                try:
                    self.cam.exit()
                except Exception:
                    pass
                self.cam = None

    def _walk(self, cam, folder):
        out = [(folder, n) for n, _v in cam.folder_list_files(folder)]
        for sub, _v in cam.folder_list_folders(folder):
            out.extend(self._walk(cam, folder.rstrip("/") + "/" + sub))
        return out

    def list_files(self, exts=None):
        """Mismo formato que el listado por MTP de Windows: name, size, path, dt."""
        with self.lock:
            try:
                cam = self.open()
                items = []
                for folder, name in self._walk(cam, "/"):
                    if exts and not name.lower().endswith(exts):
                        continue
                    try:
                        info = cam.file_get_info(folder, name)
                        size, mt = int(info.file.size), int(info.file.mtime)
                    except Exception:
                        size, mt = 0, 0
                    dt = datetime.fromtimestamp(mt).strftime("%Y-%m-%dT%H:%M:%S") if mt else ""
                    items.append({"name": name, "size": size, "path": folder, "dt": dt})
                return items
            except Exception:
                self.close()
                raise

    def read_file(self, folder, name, dest, size=0, progress=None, cancel=lambda: False, chunk=1024 * 1024):
        """Copia por partes (sin cargar el video entero en memoria)."""
        gp = self.gp()
        part = dest + ".part"
        with self.lock:
            cam = self.open()
            try:
                with open(part, "wb") as f:
                    try:
                        off = 0
                        buf = bytearray(chunk)
                        while True:
                            if cancel():
                                return False
                            n = cam.file_read(folder, name, gp.GP_FILE_TYPE_NORMAL, off, buf)
                            if not n:
                                break
                            f.write(memoryview(buf)[:n])
                            off += n
                            if progress and size:
                                progress(min(100.0, off * 100.0 / size))
                            if size and off >= size:
                                break
                    except gp.GPhoto2Error:
                        # camara sin lectura parcial: el archivo entero de una vez
                        f.seek(0)
                        f.truncate()
                        data = cam.file_get(folder, name, gp.GP_FILE_TYPE_NORMAL).get_data_and_size()
                        f.write(memoryview(data))
                    f.flush()
                    os.fsync(f.fileno())
                if size and os.path.getsize(part) != size:
                    raise IOError("size_mismatch")
                os.replace(part, dest)
                return True
            finally:
                if os.path.exists(part):
                    try:
                        os.remove(part)
                    except OSError:
                        pass

    def thumbnail(self, folder, name):
        gp = self.gp()
        with self.lock:
            cam = self.open()
            return bytes(cam.file_get(folder, name, gp.GP_FILE_TYPE_PREVIEW).get_data_and_size())

    def delete(self, folder, name):
        with self.lock:
            self.open().file_delete(folder, name)

    def status(self):
        out = {"battery": None, "storage_pct": None, "capacity_str": None, "free_str": None}
        with self.lock:
            try:
                cam = self.open()
                for st in cam.get_storageinfo():
                    total = st.capacitykbytes * 1024
                    free = st.freekbytes * 1024
                    if total:
                        out.update(storage_pct=int(round((total - free) * 100 / total)),
                                   capacity_str=_fmt_gb(total), free_str=_fmt_gb(free))
                        break
                try:
                    out["battery"] = parse_battery_percent(cam.get_single_config("batterylevel").get_value())
                except Exception:
                    pass
            except Exception as e:
                print("[gphoto2] estado:", e)
        return out


GPHOTO = GPhotoBackend()


class GPhotoSession:
    """Misma interfaz que WpdCameraSession, para la galeria de la camara en macOS/Linux."""

    def __init__(self, name_match="HERO"):
        self.objects = {}

    def open(self, stop=lambda: False, **_k):
        try:
            items = GPHOTO.list_files()
        except Exception as e:
            print("[gphoto2]", e)
            return False
        self.objects = {e["name"].upper(): (e["path"], e["name"], e["size"]) for e in items}
        return True

    def thumbnail(self, name):
        folder, nm, _sz = self.objects[name.upper()]
        return GPHOTO.thumbnail(folder, nm)

    def copy_to(self, name, dest, folder=None, progress=None, cancel=lambda: False, chunk=1024 * 1024):
        f, nm, size = self.objects[name.upper()]
        cb = (lambda pct: progress(int(pct * size / 100))) if progress else None
        return GPHOTO.read_file(f, nm, dest, size, cb, cancel, chunk)

    def close(self):
        pass                     # la conexion es compartida (GPHOTO)


def _posix_mount_roots():
    """Unidades montadas (tarjetas, discos) en macOS / Linux."""
    if IS_MAC:
        bases = ["/Volumes"]
    else:
        user = os.environ.get("USER") or ""
        bases = [f"/media/{user}", f"/run/media/{user}", "/media", "/mnt"]
    roots = []
    for b in bases:
        try:
            for n in os.listdir(b):
                full = os.path.join(b, n)
                if os.path.isdir(full) and full not in roots:
                    roots.append(full)
        except OSError:
            pass
    return roots


def detect_connected_gopro_model():
    """Prueft, welche der unterstuetzten GoPro-Modelle (siehe GOPRO_MODELS)
    gerade per MTP am PC haengt. Gibt den Modell-Key (z.B. "HERO11") zurueck
    oder None, falls keine unterstuetzte GoPro gefunden wurde."""
    if not IS_WINDOWS:
        name = GPHOTO.autodetect()
        if not name:
            return None
        LAST_DEVICE_NAME["name"] = name
        up = name.upper()
        return next((m for m in GOPRO_MODEL_ORDER if m in up), "HERO5")
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
    if not IS_WINDOWS:
        for root in _posix_mount_roots():
            dcim = os.path.join(root, "DCIM")
            try:
                if any(_GOPRO_DCIM_SUBDIR_RE.match(e) for e in os.listdir(dcim)):
                    return root, _model_from_version_txt(root)
            except OSError:
                continue
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
    if not IS_WINDOWS:
        return GPHOTO.status()
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
