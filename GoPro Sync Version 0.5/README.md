# GoPro Sync — con HERO5, tarjeta SD y respaldos

Fork de [JelloC19/GoPro-Media-Sync-for-Hero-10-and-Above-D](https://github.com/JelloC19/GoPro-Media-Sync-for-Hero-10-and-Above-D)
pensado como reemplazo de **GoPro Quik for desktop**: conectas la cámara o la
tarjeta y copia fotos y videos a tu PC, ordenados en carpetas `AAAA-MM-DD`
según la fecha de captura, sin duplicar lo que ya tenías.

## Qué agrega este fork

| Función | Detalle |
|---|---|
| **GoPro HERO5 (Black/Session) por USB** | Se detecta como `HERO5 Black` en "Este equipo" (MTP). La fecha de los MP4 de la HERO5 se interpreta en hora local (la cámara no guarda UTC), así que los videos grabados de noche no terminan en la carpeta del día equivocado. |
| **Sincronizar desde la tarjeta microSD** | Si conectas la tarjeta con un lector, se detecta sola (`DCIM\100GOPRO`…) y el modelo se lee de `MISC\version.txt`. La copia va directo por el sistema de archivos (mucho más rápida y estable que MTP) y **conserva los atributos**: fechas de creación, modificación y acceso. Tiene prioridad la cámara por USB si están las dos. |
| **Carpetas por fecha como Quik** | `D:\GoPro\2017-07-14\GOPR0001.MP4`. Lo que ya existe con el mismo nombre y tamaño (en cualquier subcarpeta) se salta, así que puedes apuntar a tu carpeta antigua de Quik. |
| **Fotos RAW** | Incluye `.GPR` (RAW de HERO5 Black y posteriores). |
| **Respaldo a discos externos** | En *Ajustes → Ubicación → Respaldo* agregas una o varias carpetas (p. ej. `E:\Respaldo GoPro`). Después de cada sync (o con **Respaldar ahora**) la carpeta local se copia de forma incremental: solo lo nuevo o cambiado, con fechas conservadas. **En el respaldo nunca se borra nada**; los discos que no estén conectados se saltan y se avisa. |
| **Borrar de la cámara/tarjeta** | Si está activado, solo se borra lo que ya está copiado y verificado (mismo tamaño). En la SD también se eliminan los `.LRV`/`.THM` que lo acompañan. |
| **Columnas MTP en español** | El espacio libre de la cámara ahora se lee también en Windows en español. |

### Rendimiento

- **Miniaturas en caché** (`%APPDATA%\GoProSyncPro\thumb_cache`) y decodificación JPEG reducida: abrir la galería con miles de fotos pasa de varios minutos cada vez a segundos (medido: 40 fotos 8,4 s → 0,4 s).
- **Un solo PowerShell persistente** para detectar la cámara, en vez de abrir uno nuevo cada 0,8 s (~1100 ms → ~170 ms por consulta).
- El sondeo Open GoPro (solo para HERO9+) ya no usa una IP fija ni frena el bucle con la cámara desconectada; el estado de batería/espacio se consulta cada ~30 s y se pausa durante la copia.
- Se quitaron el código antiguo sin uso (`core/`, `ui/`, `GoPro Sync.txt`), artefactos de compilación e imágenes que nadie usaba.

## Instalación

1. Doble clic en `install.bat` (instala Python si hace falta y los paquetes de `requirements.txt`).
2. Ejecuta `python main.py`.
3. En el asistente inicial, elige tu carpeta de destino (p. ej. `D:\GoPro`).

## Uso con HERO5

- **Por USB:** conecta la cámara encendida. Aparece "HERO 5" y pulsas sincronizar. La HERO5 no informa la batería por USB; el espacio de la tarjeta sí se muestra.
- **Con lector de tarjetas (recomendado para mucho volumen):** inserta la microSD y sincroniza igual. Es la forma más rápida y la que conserva todas las fechas originales.

## Notas

- Interfaz en alemán/inglés (heredada del proyecto original).
- Requiere Windows (usa la Shell de Windows para MTP).
