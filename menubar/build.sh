#!/bin/sh
# Сборка значка и установка в ~/Applications/BrainBar.app. Если он был запущен — перезапускает.
set -e
cd "$(dirname "$0")"
BUILD=build/BrainBar.app
APP="$HOME/Applications/BrainBar.app"

rm -rf "$BUILD"
mkdir -p "$BUILD/Contents/MacOS"
cp Info.plist "$BUILD/Contents/Info.plist"
xcrun swiftc -O -swift-version 5 -o "$BUILD/Contents/MacOS/BrainBar" BrainBar.swift
codesign --force --sign - "$BUILD"

running=0
pgrep -x BrainBar >/dev/null && running=1 && pkill -x BrainBar
mkdir -p "$HOME/Applications"
rm -rf "$APP"
cp -R "$BUILD" "$APP"
echo "установлено: $APP"
[ "$running" = 1 ] && open "$APP" && echo "перезапущен"
exit 0
