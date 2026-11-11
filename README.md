# GoPro Sync: HERO5, tarjeta SD y respaldos

Reemplazo de **GoPro Quik for desktop** para Windows, macOS y Linux. Conectás la cámara o la
tarjeta y la app copia fotos y videos a tu PC, ordenados en carpetas
`AAAA-MM-DD` según la fecha de grabación, sin duplicar lo que ya tenías.
Después los podés respaldar en uno o varios discos externos, ver en una
galería, unir los videos partidos, ver el recorrido GPS y más.

Fork de [JelloC19/GoPro-Media-Sync-for-Hero-10-and-Above-D](https://github.com/JelloC19/GoPro-Media-Sync-for-Hero-10-and-Above-D)
(ver [Proyecto original](#proyecto-original)).

## Descargar e instalar

En **[Releases](https://github.com/Lapo26/GoPro-Sync/releases/latest)**:

- `GoProSync-Setup-X.Y.Z.exe`: el **instalador**. No pide permisos de administrador, agrega GoPro Sync al menú Inicio, ofrece crear el acceso directo en el escritorio y queda en *Aplicaciones* de Windows con su desinstalador.
- `GoProSync-X.Y.Z-portable.zip`: la versión **portable** para Windows. Se descomprime y se ejecuta `GoProSync.exe`.
- `GoProSync-X.Y.Z-macOS-arm64.dmg`: **macOS** (Apple Silicon, M1 o posterior). Abrís el .dmg y arrastrás GoPro Sync a *Aplicaciones*. La app no está firmada por Apple: la primera vez, clic derecho sobre la app → **Abrir** → **Abrir**.
- `GoProSync-X.Y.Z-Linux-x86_64.AppImage`: **Linux** (Ubuntu 22.04+, Fedora 36+, Debian 12+ y similares). Le das permiso de ejecución (`chmod +x GoProSync-*.AppImage`) y la abrís.

**Actualizaciones:** la app busca versiones nuevas al iniciar. Si hay una, aparece **Actualizar a X.Y.Z** en el menú lateral y se instala con un clic. Se puede desactivar en *Ajustes*.

La configuración y la caché de miniaturas se conservan entre versiones. Se guardan en `%APPDATA%\GoProSyncPro` (Windows), `~/Library/Application Support/GoProSyncPro` (macOS) o `~/.config/GoProSyncPro` (Linux).

## Funciones

### Copiar desde la cámara o la tarjeta

| Función | Detalle |
|---|---|
| **GoPro HERO5 por USB** | Detecta la cámara (`HERO5 Black`), muestra la batería y el espacio libre, y copia directamente por MTP (WPD), sin la ventana "Copiando…" de Windows y con progreso real. |
| **Galería de la cámara** | *Cámara → Ver archivos de la cámara*: las miniaturas de lo que hay en la cámara, con ✓ en lo ya copiado y **NUEVO** en lo que falta. Elegís uno o varios y **Sincronizar seleccionados**. Clic = vista previa sin copiar. |
| **Importar SD / carpeta** | Desde un lector de tarjetas o cualquier carpeta. **Elegir archivos…** muestra la misma grilla para importar solo lo que marques. Conserva las fechas de creación, modificación y acceso. |
| **Carpetas por fecha como Quik** | `D:\GoPro\2017-07-14\HERO5 Black 1\GOPR0001.MP4`. Lo que ya existe con el mismo nombre y tamaño, en cualquier subcarpeta (incluidas las copias `_1`), se saltea; podés apuntar a tu carpeta antigua de Quik. |
| **Conteo real** | El progreso cuenta solo lo que falta copiar ("1 de 15", no "1 de 177"); lo que ya estaba se informa aparte. |
| **Robusta** | Copia a `.part` y renombra al terminar, forzando la escritura a disco: un corte de luz no deja archivos a medias con el nombre final. Un archivo dañado o de 0 bytes se saltea sin trabar la copia; Cancelar responde aunque la tarjeta esté trabada; salir en medio de una copia pide confirmación y la corta de forma ordenada. |
| **Borrar después de copiar** | Opcional: solo se borra lo ya copiado y verificado (mismo tamaño), con sus `.LRV`/`.THM`. |

### Respaldo a discos externos

- **Respaldar a disco** (menú lateral): elegís la carpeta y el destino; el diálogo recuerda la última configuración.
- Corre en segundo plano en **Actividad**, en paralelo con la sincronización, con progreso, Cancelar y cola.
- Incremental: solo lo nuevo o cambiado. **En el respaldo nunca se borra nada**; los discos desconectados se saltean y se avisa.
- **Verificar el contenido** (opcional): compara byte por byte lo ya respaldado y vuelve a copiar lo que se haya dañado en el disco.
- Respaldo automático después de cada sincronización que copió algo (*Ajustes → Ubicación*).

### Galería y herramientas de video

| Función | Detalle |
|---|---|
| **Galería** | Fotos y videos por fecha, filtro por fecha, miniaturas en caché (miles de archivos en segundos), información del archivo (EXIF / MP4), borrar a la Papelera. |
| **Unir videos partidos** | La GoPro corta las grabaciones largas en partes de ~4 GB (`GOPR1234`, `GP011234`…). *Videos → Unir videos partidos* las une **sin recomprimir** (misma calidad, conservando el GPS y la fecha). Opción de mandar las partes a la Papelera. |
| **Recorrido GPS** | Clic derecho en un video → *Ver recorrido en el mapa*: el trayecto sobre OpenStreetMap con distancia, velocidad máxima y promedio, y altura (filtra la deriva del GPS mientras busca señal). |
| **Versión liviana** | Clic derecho → *Crear versión liviana para compartir*: 720p H.264 lista para WhatsApp o mail, en `Videos\GoPro Sync`. |
| **Reparar videos dañados** | Para videos sin índice (grabación cortada): clic derecho → *Intentar reparar*. Recupera la **imagen, sin sonido**, usando otro video sano de la misma cámara como referencia. El original no se toca. |

Las herramientas corren en segundo plano en **Actividad**, con progreso y Cancelar. El video se procesa con ffmpeg, que viene incluido.

### Interfaz

- Español, inglés y alemán (se elige al primer inicio; *Ajustes → Sistema*).
- Tema oscuro en toda la app, incluidas las barras de título; progreso de la copia en el ícono de la barra de tareas.
- Con inicio automático, queda junto al reloj y abre al conectar la cámara.
- Nítida en pantallas 4K / escaladas.

## Requisitos

- **Windows 10 u 11** (64 bits): la cámara por USB usa MTP/WPD de Windows.
- **macOS 12 o posterior** (Apple Silicon) y **Linux** x86_64: la cámara por USB usa [libgphoto2](http://gphoto.org/), que viene incluida.
  - En **Mac**, si Fotos o Captura de Imagen se abren al conectar la cámara, cerralos: mientras la usan, GoPro Sync no puede leerla (la app intenta liberarla sola).
  - En **Linux**, si el explorador de archivos montó la cámara, desmontala. Si no aparece, puede faltar permiso sobre el USB: instalá el paquete `libgphoto2` de tu distribución (trae las reglas udev) y volvé a conectarla.
- Para el mapa GPS y las actualizaciones hace falta conexión a internet. En macOS y Linux la app avisa de las versiones nuevas y abre la descarga; en Windows se instalan solas.

## Desarrollo

```
app/                 código de la app (main.py) y recursos (assets/)
packaging/build.ps1  compila en Windows: PyInstaller + ZIP portable + instalador Inno Setup
packaging/build_unix.sh  compila en macOS (.dmg) o Linux (.AppImage)
packaging/try.ps1    abre la versión actual para probarla en tu PC
packaging/GoProSync.iss  script del instalador
release.ps1          publica una versión nueva
.github/workflows/release.yml  build y Release automáticos en GitHub
```

**Ejecutar desde el código:** `app\install.bat` (instala Python y dependencias), y después `python app\main.py`.

**Compilar localmente:** `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`. El resultado queda en `dist\`. Para generar también el instalador hace falta Inno Setup (`winget install JRSoftware.InnoSetup`).

### Probar antes de publicar

```powershell
.\packaging\try.ps1 -FromSource
```

Abre la versión actual del código en tu PC, sin publicar nada. Usa siempre tu configuración real: lo que cambies en la prueba queda guardado y lo ve también la app instalada. Mientras tanto cierra la GoPro Sync instalada. La primera vez crea el entorno `.venv` con las dependencias. Sin `-FromSource` compila primero (más lento, pero prueba el ejecutable real).

### Publicar una versión

```powershell
.\release.ps1 1.4.0
```

El script comprueba que estés en `main` sin cambios pendientes, cambia `APP_VERSION` en `app\main.py`, hace el commit, crea el tag `v1.4.0` y lo sube a GitHub. GitHub Actions compila en paralelo para Windows, macOS y Linux, corre una autoprueba de cada ejecutable (`--selftest`) y publica el Release con los instaladores (unos 10 min); la app instalada lo ofrece sola como actualización. Las notas del Release se generan a partir de los commits.

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
