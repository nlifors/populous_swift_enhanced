#!/bin/sh
# Builds Populous.app in the project folder. The app contains no game data: put
# your Populous disk image (.adf) next to it, or choose it when asked.
set -e
cd "$(dirname "$0")"
swift build -c release --product Populous
APP=Populous.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
BIN="$(swift build -c release --show-bin-path)"
cp "$BIN/Populous" "$APP/Contents/MacOS/Populous"
# The HD models (made by Assets/blender/build_assets.py).
cp -R "$BIN/Populous_PopulousHD.bundle" "$APP/Contents/Resources/"
# The Dock and Finder icon; tools/make-icon.py redraws it.
cp Populous.icns "$APP/Contents/Resources/Populous.icns"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Populous</string>
    <key>CFBundleExecutable</key><string>Populous</string>
    <key>CFBundleIdentifier</key><string>local.populous-port</string>
    <key>CFBundleIconFile</key><string>Populous</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.1</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
echo "Built $APP"
