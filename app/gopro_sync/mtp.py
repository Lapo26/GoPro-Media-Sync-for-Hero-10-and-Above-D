"""Archivos de la camara: scripts de PowerShell (MTP) y acceso directo por WPD."""
import json
import os
import subprocess
import threading
import time
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
from .env import GOPRO_MODEL_MATCH_PATTERN, IS_WINDOWS, _hidden_startupinfo
from .fileops import MEDIA_EXTENSIONS_BY_MODE, SYNC_MEDIA_EXTENSIONS
from .camera import GPHOTO, GPhotoSession, _wpd_modules

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


def camera_item_key(entry):
    return f"{entry.get('path') or ''}|{entry.get('name') or ''}"


class CameraListThread(QThread):
    """Lista los archivos de la camara (MTP) en segundo plano."""
    listed = Signal(object)        # lista de dicts o None si fallo

    def run(self):
        if not IS_WINDOWS:
            try:
                self.listed.emit(GPHOTO.list_files(tuple(e.lower() for e in MEDIA_EXTENSIONS_BY_MODE["all"])))
            except Exception as e:
                print("[gphoto2] listar:", e)
                self.listed.emit(None)
            return
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
        cam = WpdCameraSession(self.name_match) if IS_WINDOWS else GPhotoSession(self.name_match)
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
