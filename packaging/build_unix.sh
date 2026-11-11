#!/usr/bin/env bash
# Compila GoPro Sync para macOS (.dmg) o Linux (.AppImage). Lo usa GitHub Actions.
#   bash packaging/build_unix.sh            (usa "python3")
#   PYTHON=python bash packaging/build_unix.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' app/main.py)
OS=$(uname -s)
echo "== GoPro Sync $VERSION ($OS) =="

"$PY" -m pip install --disable-pip-version-check -q -r app/requirements.txt pyinstaller pillow
rm -rf build dist

COMMON=(--noconfirm --clean --windowed --name GoProSync
        --add-data "app/assets:assets"
        --collect-data imageio_ffmpeg
        --collect-all gphoto2
        --exclude-module tkinter --exclude-module unittest --exclude-module pydoc
        --distpath dist --workpath build)

if [ "$OS" = "Darwin" ]; then
  "$PY" -m PyInstaller "${COMMON[@]}" --icon app/assets/app_icon.png \
      --osx-bundle-identifier com.gopro-sync.app app/main.py
  # Autoprueba del ejecutable empaquetado (sin ventana)
  QT_QPA_PLATFORM=offscreen "dist/GoProSync.app/Contents/MacOS/GoProSync" --selftest
  ARCH=$(uname -m)
  hdiutil create -volname "GoPro Sync" -srcfolder dist/GoProSync.app -ov -format UDZO \
      "dist/GoProSync-$VERSION-macOS-$ARCH.dmg"
  ls -la dist/*.dmg
else
  "$PY" -m PyInstaller "${COMMON[@]}" app/main.py
  QT_QPA_PLATFORM=offscreen dist/GoProSync/GoProSync --selftest
  # AppImage: un solo archivo que corre en la mayoria de las distribuciones
  APPDIR=dist/AppDir
  mkdir -p "$APPDIR/usr/bin"
  cp -a dist/GoProSync/. "$APPDIR/usr/bin/"
  cp app/assets/app_icon.png "$APPDIR/gopro-sync.png"
  cat > "$APPDIR/gopro-sync.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=GoPro Sync
Comment=Copia y respalda fotos y videos de tu GoPro
Exec=GoProSync
Icon=gopro-sync
Categories=Graphics;Photography;
Terminal=false
DESK
  cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/GoProSync" "$@"
RUN
  chmod +x "$APPDIR/AppRun"
  if [ ! -x build/appimagetool ]; then
    curl -fsSL -o build/appimagetool \
      https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x build/appimagetool
  fi
  ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 build/appimagetool "$APPDIR" "dist/GoProSync-$VERSION-Linux-x86_64.AppImage"
  ls -la dist/*.AppImage
fi
