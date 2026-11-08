# GoPro Sync: HERO5, tarjeta SD y respaldos

Reemplazo de **GoPro Quik for desktop**: conectas la cámara o la tarjeta y la
app copia fotos y videos a tu PC, ordenados en carpetas `AAAA-MM-DD` según la
fecha de captura, sin duplicar lo que ya tenías. También puede copiar después
esa carpeta a uno o varios discos externos.

Fork de [JelloC19/GoPro-Media-Sync-for-Hero-10-and-Above-D](https://github.com/JelloC19/GoPro-Media-Sync-for-Hero-10-and-Above-D)
(ver [Proyecto original](#proyecto-original)).

## Descargar e instalar

En **[Releases](https://github.com/Lapo26/GoPro-Media-Sync-for-Hero-10-and-Above-D/releases/latest)**:

- `GoProSync-Setup-X.Y.Z.exe`: el instalador. No pide permisos de administrador y crea accesos directos. Para actualizar, instala la nueva versión encima.
- `GoProSync-X.Y.Z-portable.zip`: la versión portable. Se descomprime y se ejecuta `GoProSync.exe`.

La configuración y la caché de miniaturas se guardan en `%APPDATA%\GoProSyncPro` y se conservan entre versiones.

## Qué agrega este fork

| Función | Detalle |
|---|---|
| **GoPro HERO5 (Black/Session) por USB** | Se detecta como `HERO5 Black` en "Este equipo" (MTP). La fecha de los MP4 se interpreta en hora local (la HERO5 no guarda UTC), así que los videos grabados de noche no terminan en la carpeta del día equivocado. |
| **Sincronizar desde la tarjeta microSD** | Si conectas la tarjeta con un lector, se detecta sola (`DCIM\100GOPRO`…) y el modelo se lee de `MISC\version.txt`. La copia va directo por el sistema de archivos (mucho más rápida y estable que MTP) y **conserva los atributos**: fechas de creación, modificación y acceso. Si están la cámara por USB y la tarjeta a la vez, tiene prioridad la cámara. |
| **Carpetas por fecha como Quik** | `D:\GoPro\2017-07-14\GOPR0001.MP4`. Lo que ya existe con el mismo nombre y tamaño (en cualquier subcarpeta) se salta, así que puedes apuntar a tu carpeta antigua de Quik. |
| **Fotos RAW** | Incluye `.GPR` (RAW de HERO5 Black y posteriores). |
| **Respaldo a discos externos** | En *Ajustes → Ubicación → Respaldo* agregas una o varias carpetas (p. ej. `E:\Respaldo GoPro`). Después de cada sync, o con **Respaldar ahora**, la carpeta local se copia de forma incremental: solo lo nuevo o cambiado, con las fechas conservadas. **En el respaldo nunca se borra nada**; los discos que no estén conectados se saltan y se avisa. |
| **Borrar de la cámara/tarjeta** | Si está activado, solo se borra lo que ya está copiado y verificado (mismo tamaño). En la SD también se eliminan los `.LRV`/`.THM` que lo acompañan. |
| **Idiomas** | Español, inglés y alemán. Al primer inicio se elige el idioma (viene preseleccionado el de Windows); se cambia en *Ajustes → Sistema*. |
| **Pantallas 4K / escaladas** | Logo, miniaturas, fondo y fotogramas se generan a la resolución real de la pantalla: nítidos con Windows al 150-200 %. Ícono propio legible en todos los tamaños (`packaging/make_icon.py`). |
| **Rendimiento** | Caché de miniaturas: abrir una galería con miles de fotos pasa de minutos a segundos. La detección usa un PowerShell persistente (~1100 ms → ~170 ms por consulta). Hay menos sondeo en segundo plano y se pausa durante la copia. |

### Uso con HERO5

- **Por USB:** conecta la cámara encendida, aparece "HERO 5" y pulsas sincronizar. La HERO5 no informa la batería por USB; el espacio de la tarjeta sí se muestra.
- **Con lector de tarjetas (recomendado para mucho volumen):** inserta la microSD y sincroniza igual. Es la forma más rápida y la que conserva todas las fechas originales.

## Desarrollo

```
app/                 código de la app (main.py) y recursos (assets/)
packaging/build.ps1  compila: PyInstaller + ZIP portable + instalador Inno Setup
packaging/try.ps1    compila y abre la versión actual para probarla en tu PC
packaging/GoProSync.iss  script del instalador
release.ps1          publica una versión nueva
.github/workflows/release.yml  build y Release automáticos en GitHub
```

**Ejecutar desde el código:** `app\install.bat` (instala Python y dependencias), y después `python app\main.py`.

**Compilar localmente:** `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`. El resultado queda en `dist\`. Para generar también el instalador hace falta Inno Setup (`winget install JRSoftware.InnoSetup`).

### Probar antes de publicar

```powershell
.\packaging\try.ps1
```

Compila la versión actual del código y la abre en tu PC, sin publicar nada. Usa siempre tu configuración real: lo que cambies en la prueba queda guardado y lo ve también la app instalada. Mientras tanto cierra la GoPro Sync instalada. La primera vez crea el entorno `.venv` con las dependencias, lo que tarda unos minutos.

- `.\packaging\try.ps1 -FromSource`: abre directo desde el código, sin compilar. Es lo más rápido para probar cambios.

### Publicar una versión

```powershell
.\release.ps1 1.1.0
```

El script comprueba que estés en `main` sin cambios pendientes, cambia `APP_VERSION` en `app\main.py`, hace el commit y crea el tag `v1.1.0`, y lo sube a GitHub. GitHub Actions compila y publica el Release con el instalador y el ZIP (tarda unos 5-10 min). Las notas del Release se generan solas a partir de los commits.

Para probar un build sin publicar nada: pestaña **Actions → Build y release → Run workflow**. Los archivos quedan como artefactos de esa ejecución.

## Proyecto original

Creado por [@JelloC19](https://github.com/JelloC19). Descripción original:

> 
> 
> <img width="600" height="337" alt="GoProSyncDemo1-ezgif com-video-to-gif-converter (3)" src="https://github.com/user-attachments/assets/d129b235-fe8d-48d2-8e65-d0cca5bd21e4" />
> 
> 
> # GoPro-Media-Sync-for-Hero-10-and-Above-D
> This is what i would describe as a Pre Alpha its only Purpose is to Sync your gallery with the PC without you doing anything. 
> Just open the install file wait for Python to install and then launch via the Shortcut :D 
> 
> GoPro Sync Pro
> GoPro Sync Pro is a modern, lightweight desktop application built with Python and PySide6 for Windows. It aims to provide a more manageable way to connect your GoPro, view its hardware status, and synchronize your photos and videos to your local PC by working around some of the typical Windows MTP (Media Transfer Protocol) hiccups.
> 
> 🌟 Why this tool?
> Transferring large files from a GoPro via standard Windows MTP is notoriously flaky, prone to freezing, and often blocks hardware status queries. GoPro Sync Pro tackles this with a pragmatic hybrid approach: It reads the camera's battery level via Bluetooth Low Energy (BLE) to avoid stressing the USB connection, while handling file transfers through isolated PowerShell commands to keep the MTP pipeline from choking on large loops.
> 
> 🚀 Key Features
> Pragmatic Media Sync: Copies .mp4, .jpg, and .png files from the camera to a local folder. It includes a basic duplicate check to skip files that are already in the target directory.
> 
> BLE Battery Info: Uses a background BLE (Bluetooth) scan to fetch the battery percentage. This bypasses the MTP connection entirely, preventing the typical timeout freezes when asking the camera for its status.
> 
> Modern UI & Animations: A clean dark-mode interface with smooth CSS gradient animations, drop shadows, and customizable accent colors.
> 
> Built-in Media Galleries: Automatically generates thumbnails (powered by OpenCV) for synced media. Includes a functional video player and a simple photo viewer.
> 
> Real-time Progress: An animated overlay popup shows the currently transferring file and a rough ETA.
> 
> 🛠️ Tech Stack
> Python 3
> 
> PySide6 (Qt6) for the GUI and multimedia playback.
> 
> Bleak for asynchronous Bluetooth Low Energy communication.
> 
> OpenCV (cv2) for fast video thumbnail generation.
> 
> PowerShell & Shell.Application for executing isolated MTP file transfers.
