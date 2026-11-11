"""Copia segura de archivos, deteccion de duplicados y conteos de carpetas."""
import ctypes
import os
import re
import shutil
import threading
import time
from datetime import datetime

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


def files_identical(a, b, progress=None, cancel=lambda: False, chunk=4 * 1024 * 1024):
    """Compara dos archivos byte por byte (se corta en la primera diferencia)."""
    def work(beat):
        done = 0
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                if cancel():
                    raise _CopyCancelled()
                x = fa.read(chunk)
                y = fb.read(chunk)
                beat()
                if x != y:
                    return False
                if not x:
                    return True
                done += len(x)
                if progress:
                    progress(done)
    return call_with_stall_guard(work, 60, cancel)


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
JOINED_PARTS = set()        # "nombre|tamano" de partes ya unidas (para no volver a copiarlas de la camara)
