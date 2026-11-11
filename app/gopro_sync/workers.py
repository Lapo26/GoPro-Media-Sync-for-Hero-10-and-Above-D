"""Tareas en segundo plano: sincronizar, importar, respaldar, contar."""
import json
import os
import shutil
import subprocess
import time
import traceback
from datetime import datetime
from PySide6.QtCore import QThread, Signal
from .env import (
    ACTIVE_SOURCE,
    GOPRO_MODELS,
    IS_WINDOWS,
    LAST_DEVICE_NAME,
    SYNC_IN_PROGRESS,
    _hidden_startupinfo,
)
from .i18n import tr
from .winshell import (
    HIDE_NATIVE_COPY_DIALOG,
    _find_copy_dialog_hwnd,
    _find_progressbar_child,
    _hide_native_copy_dialog,
    _read_progress_via_uia,
    _read_progressbar_percent,
    _user32,
)
from .media import _read_jpeg_exif_datetime, get_media_capture_date, replaceable_broken_copy
from .fileops import (
    JOINED_PARTS,
    MEDIA_EXTENSIONS_BY_MODE,
    ReadStalled,
    _CopyCancelled,
    _is_same_or_inside,
    _parse_camera_dt,
    _remove_stale_part,
    call_with_stall_guard,
    copy_file_preserving,
    existing_candidates,
    files_identical,
    index_existing,
    quik_camera_folder,
    set_file_times,
)
from .camera import GPHOTO, _GOPRO_DCIM_SUBDIR_RE, _find_gopro_card_root, _model_from_version_txt
from .mtp import (
    WpdCameraSession,
    WpdError,
    _extract_result_json,
    _wpd_is_timeout,
    build_batch_delete_script,
    build_list_script,
    build_single_copy_script,
    camera_item_key,
)

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

    def __init__(self, source_dir, backup_dirs, lang="de", parent=None, verify=False):
        super().__init__(parent)
        self.source_dir = source_dir
        self.backup_dirs = [d for d in (backup_dirs or []) if d]
        self.lang = lang
        self.verify = verify          # comparar tambien el contenido de lo ya respaldado
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
        to_verify = []        # ya respaldados, a comparar byte por byte
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
                        if self.verify:
                            to_verify.append((src, rel, size, dst))
                        continue
                except OSError:
                    pass
                pending.append((src, rel, size, dst))
        self.plan.emit([size for _s, _r, size, _d in pending] + [size for _s, _r, size, _d in to_verify])

        copied = errors = 0
        error_list = []
        total = len(pending) + len(to_verify)
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

        # Verificacion: lo que "parece" respaldado (mismo tamano y fecha) se
        # compara byte por byte; si la copia se dano en el disco, se vuelve a copiar.
        verified = bad = 0
        for vidx, (src, rel, size, dst) in enumerate(to_verify, start=len(pending) + 1):
            if self._cancel:
                break
            name = tr(self.lang, "backup_verifying_name", name=os.path.basename(rel))
            self.progress.emit(vidx, total, name, 0.0)
            try:
                same = files_identical(src, dst, lambda done, i=vidx, n=name, sz=size:
                                       self.progress.emit(i, total, n, done * 100.0 / sz if sz else 100.0),
                                       lambda: self._cancel)
            except _CopyCancelled:
                break
            except Exception as e:
                errors += 1
                error_list.append((os.path.basename(rel), str(e)))
                continue
            verified += 1
            if not same:
                bad += 1
                print(f"[Respaldo] Copia danada, se vuelve a copiar: {dst}")
                if not copy_file_preserving(src, dst, size, None, should_cancel=lambda: self._cancel):
                    errors += 1
                    error_list.append((os.path.basename(rel), tr(self.lang, "err_verify_mismatch")))
            self.progress.emit(vidx, total, name, 100.0)

        info = {"copied": copied, "skipped": skipped, "errors": errors, "missing": missing,
                "error_list": error_list, "total": total, "verified": verified, "repaired": bad}
        if self._cancel:
            info["cancelled"] = True
            self._finish(False, tr(self.lang, "backup_cancelled", copied=copied), info)
            return
        msg = tr(self.lang, "backup_done", copied=copied, skipped=skipped)
        if self.verify and verified:
            msg += (tr(self.lang, "backup_verified_bad", n=verified, bad=bad) if bad
                    else tr(self.lang, "backup_verified_ok", n=verified))
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
        """Ya hay una copia con el mismo nombre y tamano en el destino (o es
        una parte que ya se unio en un video completo)."""
        if f"{name.lower()}|{size}" in JOINED_PARTS:
            return True
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

    def _run_gphoto(self):
        """Camara por USB en macOS/Linux (libgphoto2): mismo criterio que en
        Windows (nombre + tamano, carpetas por fecha, solo lo nuevo)."""
        if not GPHOTO.gp():
            self.finished_sync.emit(False, tr(self.lang, "gphoto_missing"), {})
            return
        exts = tuple(e.lower() for e in MEDIA_EXTENSIONS_BY_MODE[self.sync_mode])
        try:
            items = GPHOTO.list_files(exts)
        except Exception as e:
            print("[gphoto2] listar:", e)
            busy = "busy" in str(e).lower() or "claim" in str(e).lower()
            self.finished_sync.emit(False, tr(self.lang, "gphoto_busy" if busy else "gopro_not_found"), {})
            return
        if self.only_keys is not None:
            items = [e for e in items if camera_item_key(e) in self.only_keys]
        if not items:
            self.finished_sync.emit(True, tr(self.lang, "no_new_media"), {"total": 0, "copied": 0, "deleted": 0, "errors": 0})
            return
        os.makedirs(self.target_dir, exist_ok=True)
        existing_index = {}
        for root, _dirs, files in os.walk(self.target_dir):
            for f in files:
                if f.endswith(".part"):
                    _remove_stale_part(os.path.join(root, f))
                    continue
                index_existing(existing_index, f, os.path.join(root, f))
        cam_folder = quik_camera_folder(self.target_dir, self.device_name,
                                        [p for paths in existing_index.values() for p in paths])
        already, pending, to_delete = 0, [], []
        for e in items:
            if e["size"] and self._already_there(existing_index, e["name"], e["size"]):
                already += 1
                if self.delete_after:
                    to_delete.append((e["path"], e["name"]))
            else:
                pending.append(e)
        total = len(pending)
        self.plan.emit([e["size"] for e in pending])
        copied = errors = deleted = stalls = 0
        for idx, e in enumerate(pending, start=1):
            if self._cancel:
                break
            name, size, folder = e["name"], e["size"], e["path"]
            self.progress.emit(idx, total, name, 0.0)
            if size == 0:
                errors += 1
                self._err(name, tr(self.lang, "err_empty_file"))
                continue
            dt = _parse_camera_dt(e.get("dt")) or datetime.now()
            dest_dir = self.target_dir
            if self.sort_into_date_folders:
                dest_dir = os.path.join(self.target_dir, dt.strftime("%Y-%m-%d"))
                if cam_folder:
                    dest_dir = os.path.join(dest_dir, cam_folder)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, name)
            if os.path.exists(dest) and not replaceable_broken_copy(dest, size):
                base, ext = os.path.splitext(name)
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(dest_dir, f"{base}_{counter}{ext}")
                    counter += 1
            try:
                ok = GPHOTO.read_file(folder, name, dest, size,
                                      lambda pct, i=idx, n=name: self.progress.emit(i, total, n, pct),
                                      lambda: self._cancel)
                stalls = 0
            except Exception as ex:
                ok = False
                errors += 1
                stalls += 1
                self._err(name, self._reason_from_exception(ex))
                GPHOTO.close()
                if stalls >= 3:
                    self.finished_sync.emit(False, tr(self.lang, "gopro_not_found"),
                                            {"total": total, "copied": copied, "deleted": 0, "errors": errors,
                                             "error_list": list(self.error_list)})
                    return
                continue
            if self._cancel:
                break
            if ok:
                set_file_times(dest, dt)
                index_existing(existing_index, os.path.basename(dest), dest)
                copied += 1
                self.progress.emit(idx, total, name, 100.0)
                if self.delete_after:
                    to_delete.append((folder, name))
        if self._cancel:
            self._cancelled_result(copied, total)
            return
        if self.delete_after and to_delete:
            self.progress.emit(total, total, "__CLEANUP__" + tr(self.lang, "cleaning_up"), 100.0)
            for folder, name in to_delete:
                try:
                    GPHOTO.delete(folder, name)
                    deleted += 1
                except Exception as ex:
                    print("[gphoto2] borrar:", name, ex)
        result = {"total": total, "copied": copied, "deleted": deleted, "errors": errors,
                  "already": already, "error_list": list(self.error_list)}
        self.finished_sync.emit(errors == 0, self._result_message(copied, total, already, deleted, errors), result)

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
        if not IS_WINDOWS:
            try:
                self._run_gphoto()
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
