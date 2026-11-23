#!/bin/sh
# Установка второго мозга одной командой:
#   curl -fsSL https://raw.githubusercontent.com/vladilenm/modern-second-brain/main/install.sh | sh
# Повторный запуск обновляет код; данные (data/, config.json, inbox/) не трогаются.
# BRAIN_HOME — куда ставить (~/brain), BRAIN_REF — ветка (main).
set -eu

REPO="${BRAIN_REPO:-https://github.com/vladilenm/modern-second-brain.git}"
REF="${BRAIN_REF:-main}"
TARGET="${BRAIN_HOME:-$HOME/brain}"
BIN_DIR="${BRAIN_BIN_DIR:-$HOME/.local/bin}"

say() { printf '%s\n' "$*"; }
fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
# Скрипт приходит через pipe, поэтому вопросы задаём через /dev/tty.
tty_ok() { [ -z "${BRAIN_NO_TTY:-}" ] && (: </dev/tty) 2>/dev/null; }
ask_yes() {
    tty_ok || return 1
    printf '%s [Д/н] ' "$1" >/dev/tty
    read -r answer </dev/tty || answer=""
    case "$answer" in
        н*|Н*|n*|N*) return 1 ;;
        *) return 0 ;;
    esac
}

[ "$(uname -s)" = "Darwin" ] || fail "пока поддерживается только macOS"
xcode-select -p >/dev/null 2>&1 || fail "нужны Command Line Tools: выполни xcode-select --install, дождись установки и запусти эту команду снова"
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || fail "нужен Python 3.9 или новее в /usr/bin/python3 (идёт с Command Line Tools)"

case "$TARGET" in
    "$HOME/Documents"|"$HOME/Documents/"*|"$HOME/Desktop"|"$HOME/Desktop/"*|"$HOME/Downloads"|"$HOME/Downloads/"*)
        fail "не ставь в Документы, Рабочий стол или Загрузки: macOS не пускает туда фоновую синхронизацию. Например: BRAIN_HOME=\$HOME/brain" ;;
esac

if [ -d "$TARGET/.git" ] && [ -f "$TARGET/brain/cli.py" ]; then
    say "Обновляю $TARGET"
    git -C "$TARGET" pull --ff-only --quiet
elif [ -e "$TARGET" ]; then
    fail "$TARGET уже есть и это не второй мозг — выбери другую папку: BRAIN_HOME=\$HOME/другая-папка"
else
    say "Скачиваю в $TARGET"
    PARENT="$(dirname "$TARGET")"
    mkdir -p "$PARENT"
    TMP="$(mktemp -d "$PARENT/.brain-install.XXXXXX")"
    trap 'rm -rf "$TMP"' EXIT
    git clone --quiet --depth 1 --branch "$REF" "$REPO" "$TMP/brain"
    mv "$TMP/brain" "$TARGET"
    rmdir "$TMP"
    trap - EXIT
fi

mkdir -p "$BIN_DIR"
ln -sf "$TARGET/bin/brain" "$BIN_DIR/brain"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        LINE="export PATH=\"$BIN_DIR:\$PATH\""
        if ask_yes "Добавить $BIN_DIR в PATH (строка в ~/.zprofile), чтобы команда brain работала из любой папки?"; then
            printf '\n# второй мозг: команда brain\n%s\n' "$LINE" >>"$HOME/.zprofile"
            say "Добавлено. В новых окнах терминала команда brain доступна."
        else
            say "Чтобы команда brain работала из любой папки, добавь в ~/.zprofile строку:"
            say "  $LINE"
        fi ;;
esac

say "Установлено: $TARGET"
if [ -z "${BRAIN_NO_SETUP:-}" ] && tty_ok; then
    exec "$TARGET/bin/brain" setup </dev/tty >/dev/tty
fi
say "Дальше: $TARGET/bin/brain setup"
