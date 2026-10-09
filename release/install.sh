#!/bin/bash
# A6SiliconPatcher by TheHappyJolly - Asphalt 6: Adrenaline, Apple Silicon patcher/installer.
# Takes the user's own copy of "Asphalt 6.app" (version 1.0.9 from Gameloft) and builds a patched, Apple Silicon
# compatible copy of it in /Applications. Nothing from the game is downloaded or redistributed: only the launcher/runtime ("engine")
# is fetched; the game's own files are cloned from the copy that is already on this Mac.
#
#   bash -c "$(curl -fsSL https://archive.org/download/a6siliconpatcher/install.sh)"
#
# Options:  --uninstall   remove the installed copy     --orig <path>   use this "Asphalt 6.app"
# Env:      A6_BASE_URL (where manifest.txt and the engine live; file:// works), A6_INSTALL_DIR, A6_ORIG, A6_NO_LAUNCH
set -u
export PYTHONDONTWRITEBYTECODE=1      # nothing may be written into the signed app bundle
BASE_URL="${A6_BASE_URL:-https://archive.org/download/a6siliconpatcher}"
APP_NAME="Asphalt 6 (Apple Silicon).app"
ORIG_ARG="${A6_ORIG:-}"
UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) UNINSTALL=1 ;;
    --orig) shift; ORIG_ARG="${1:-}" ;;
    *) ;;
  esac
  shift
done

# ------------------------------------------------------------------ output helpers
case "$(defaults read -g AppleLanguages 2>/dev/null | tr -d ' \n"(),' | cut -c1-2)" in ru) LANG_UI=ru ;; *) LANG_UI=en ;; esac
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'; else B=; G=; R=; Y=; D=; N=; fi
t() { if [ "$LANG_UI" = ru ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
STEP=0; STEPS=7
step() { STEP=$((STEP+1)); printf '\n%s[%d/%d] %s%s\n' "$B" "$STEP" "$STEPS" "$(t "$1" "$2")" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$(t "$1" "$2")"; }
info() { printf '  %s%s%s\n' "$D" "$(t "$1" "$2")" "$N"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$(t "$1" "$2")"; }
STAGE=""
drop_stage() { if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then rm -rf "${STAGE:?}"; fi; }
die()  { printf '\n%s✗ %s%s\n' "$R" "$(t "$1" "$2")" "$N" >&2
         drop_stage
         printf '\n%s\n' "$(t 'Установка остановлена, ничего не сломано: оригинальная игра не тронута.' 'Installation stopped, nothing was broken: your original game is untouched.')" >&2
         exit 1; }
TTY=/dev/tty
have_tty() { (exec 3< "$TTY") 2>/dev/null; }
ask() {  # ask <ru> <en>  -> reads a line from the terminal into REPLY_LINE
  REPLY_LINE=
  if have_tty; then printf '  %s ' "$(t "$1" "$2")" > "$TTY"; IFS= read -r REPLY_LINE < "$TTY" || true; fi
}
sha() { shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1; }
fmt_mb() { echo $(( ($1 + 1048575) / 1048576 )); }

TMP=""
cleanup() { drop_stage; if [ -n "$TMP" ] && [ -d "$TMP" ]; then rm -rf "${TMP:?}"; fi; }
trap cleanup EXIT
trap 'printf "\n"; exit 130' INT TERM

printf '\n%s%s%s\n' "$B" "A6SiliconPatcher by TheHappyJolly" "$N"
printf '%s%s%s\n' "$D" "Asphalt 6: Adrenaline - Apple Silicon" "$N"
printf '%s\n' "$(t 'Установщик пропатченной копии игры (нужна ваша собственная копия Asphalt 6.app).' 'Installer of the patched copy of the game (needs your own copy of Asphalt 6.app).')"

# ------------------------------------------------------------------ install dir
if [ -n "${A6_INSTALL_DIR:-}" ]; then INSTALL_DIR="$A6_INSTALL_DIR"
elif [ -w /Applications ]; then INSTALL_DIR=/Applications
else INSTALL_DIR="$HOME/Applications"; fi
DEST="$INSTALL_DIR/$APP_NAME"

if [ "$UNINSTALL" = 1 ]; then
  if [ -d "$DEST" ] && [ -f "$DEST/Contents/Resources/engine/game.i386" ]; then
    rm -rf "${DEST:?}" && printf '%s\n' "$(t "Удалено: $DEST" "Removed: $DEST")"
  else
    printf '%s\n' "$(t 'Установленной копии не найдено.' 'No installed copy found.')"
  fi
  printf '%s\n' "$(t 'Ваши сохранения (~/Library/Application Support) не тронуты.' 'Your saves (~/Library/Application Support) were not touched.')"
  exit 0
fi

# ------------------------------------------------------------------ 1. system
step "Проверка системы" "Checking the system"
[ "$(uname -s)" = Darwin ] || die "Это установщик для macOS." "This installer is for macOS."
MACV="$(sw_vers -productVersion 2>/dev/null || echo 0)"; MAJOR="${MACV%%.*}"
[ "${MAJOR:-0}" -ge 11 ] || die "Нужна macOS 11 или новее (у вас $MACV)." "macOS 11 or newer is required (you have $MACV)."
ok "macOS $MACV" "macOS $MACV"
ARCH="$(uname -m)"
if [ "$ARCH" != arm64 ]; then
  die "Этот патч нужен для компьютеров Mac на Apple Silicon (M1 и новее). У вас Intel: macOS $MACV уже не запускает 32-битные игры." \
      "This patch is for Apple Silicon Macs (M1 and newer). You have an Intel Mac: macOS $MACV cannot run 32-bit games any more."
fi
ok "Apple Silicon ($ARCH)" "Apple Silicon ($ARCH)"
for tool in curl shasum tar ditto plutil codesign xattr cp mdfind arch; do
  command -v "$tool" >/dev/null 2>&1 || die "В системе не найдена команда $tool." "The command $tool was not found on this system."
done
if arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
  ok "Rosetta 2 уже установлена" "Rosetta 2 is already installed"
else
  info "Rosetta 2 не установлена. Ставлю (понадобится пароль администратора; при вводе символы не отображаются)." "Rosetta 2 is not installed. Installing it (the administrator password is needed; typing is not echoed)."
  if have_tty; then sudo softwareupdate --install-rosetta --agree-to-license < "$TTY" || true
  else sudo softwareupdate --install-rosetta --agree-to-license || true; fi
  arch -x86_64 /usr/bin/true >/dev/null 2>&1 || die "Не удалось установить Rosetta 2. Установите её вручную командой: sudo softwareupdate --install-rosetta --agree-to-license и запустите установщик снова." \
      "Could not install Rosetta 2. Install it manually with: sudo softwareupdate --install-rosetta --agree-to-license and run this installer again."
  ok "Rosetta 2 установлена" "Rosetta 2 installed"
fi
mkdir -p "$INSTALL_DIR" 2>/dev/null
[ -w "$INSTALL_DIR" ] || die "Нет доступа на запись в $INSTALL_DIR." "No write access to $INSTALL_DIR."

# ------------------------------------------------------------------ 2. manifest
step "Получение информации о версии" "Fetching the release information"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/A6SiliconPatcher.XXXXXX")" || die "Не удалось создать временную папку." "Could not create a temporary folder."
curl -fsSL --retry 3 --connect-timeout 20 "$BASE_URL/manifest.txt" -o "$TMP/manifest.txt" || die \
  "Не удалось скачать данные о версии с $BASE_URL. Проверьте интернет и попробуйте снова." \
  "Could not download the release information from $BASE_URL. Check your internet connection and try again."
mf() { sed -n "s/^$1=//p" "$TMP/manifest.txt" | head -n1; }
VERSION="$(mf VERSION)"; ENGINE_FILE="$(mf ENGINE_FILE)"; ENGINE_SHA="$(mf ENGINE_SHA256)"; ENGINE_SIZE="$(mf ENGINE_SIZE)"
ORIG_BIN_SHA="$(mf ORIG_BIN_SHA256)"; ORIG_DAT_SHA="$(mf ORIG_DAT_SHA256)"; PATCHED_DAT_SHA="$(mf PATCHED_DAT_SHA256)"
if [ -z "$VERSION" ] || [ -z "$ENGINE_FILE" ] || [ -z "$ENGINE_SHA" ] || [ -z "$ORIG_BIN_SHA" ] || [ -z "$ORIG_DAT_SHA" ]; then
  die "Файл версии повреждён." "The release file is damaged."
fi
ok "версия патча $VERSION" "patch version $VERSION"

# ------------------------------------------------------------------ 3. original game
step "Поиск вашей копии Asphalt 6" "Looking for your copy of Asphalt 6"
ORIG=""; SEEN=""; WRONG_VERSION=""; ORIG_WHY=""
check_orig() {   # 0 = this is the right version; ORIG_WHY says what is wrong otherwise
  local app="$1" bin="$1/Contents/MacOS/Asphalt 6" dat="$1/Contents/Resources/data/file000516.dat"
  ORIG_WHY=""
  [ -f "$bin" ] && [ -f "$dat" ] || { ORIG_WHY="notgame"; return 1; }
  if [ -f "$app/Contents/Resources/engine/game.i386" ]; then ORIG_WHY="patched"; return 1; fi      # a copy made by this patcher
  if [ "$(sha "$bin")" = "$ORIG_BIN_SHA" ] && [ "$(sha "$dat")" = "$ORIG_DAT_SHA" ]; then return 0; fi
  ORIG_WHY="version"; return 1
}
try_orig() {
  local p="$1"; p="${p%/}"; [ -d "$p" ] || return 1
  case "$SEEN" in *"|$p|"*) return 1 ;; esac
  SEEN="$SEEN|$p|"
  if check_orig "$p"; then ORIG="$p"; return 0; fi
  [ "$ORIG_WHY" = version ] && WRONG_VERSION="$p"
  return 1
}
if [ -n "$ORIG_ARG" ]; then try_orig "$ORIG_ARG"; fi
if [ -z "$ORIG" ]; then
  for c in "/Applications/Asphalt 6.app" "$HOME/Applications/Asphalt 6.app" "$HOME/Downloads/Asphalt 6.app" "$HOME/Desktop/Asphalt 6.app" "$HOME/Documents/Asphalt 6.app"; do
    if try_orig "$c"; then break; fi
  done
fi
if [ -z "$ORIG" ]; then
  info "Ищу через Spotlight..." "Searching with Spotlight..."
  while IFS= read -r c; do
    if try_orig "$c"; then break; fi
  done < <(mdfind -name "Asphalt 6.app" 2>/dev/null)
fi
TRIES=0
while [ -z "$ORIG" ] && [ $TRIES -lt 3 ]; do
  TRIES=$((TRIES+1))
  if [ -n "$WRONG_VERSION" ]; then
    warn "Найдена игра, но другой версии: $WRONG_VERSION. Патч рассчитан на версию 1.0.9 (Gameloft, 32-битная для Mac)." "Found a game, but of another version: $WRONG_VERSION. The patch is made for version 1.0.9 (Gameloft, 32-bit Mac)."
    WRONG_VERSION=""
  else
    warn "Не нашёл Asphalt 6.app на этом Mac." "Could not find Asphalt 6.app on this Mac."
  fi
  have_tty || break
  ask "Перетащите Asphalt 6.app в это окно Терминала и нажмите Enter (или просто Enter для отмены):" "Drag Asphalt 6.app into this Terminal window and press Enter (or just Enter to cancel):"
  P="$REPLY_LINE"; [ -n "$P" ] || break
  P="$(printf '%s' "$P" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']\$//" -e 's/\\\(.\)/\1/g')"
  case "$P" in "~"*) P="$HOME${P#\~}" ;; esac
  if ! try_orig "$P"; then
    [ "$ORIG_WHY" = notgame ] && warn "Это не Asphalt 6.app." "That is not Asphalt 6.app."
    [ "$ORIG_WHY" = patched ] && warn "Это уже пропатченная копия; нужна оригинальная игра." "That is an already patched copy; the original game is needed."
  fi
done
if [ -z "$ORIG" ]; then
  die "Не нашёл подходящую копию Asphalt 6. Нужна оригинальная игра (версия 1.0.9). Положите Asphalt 6.app в «Программы» или «Загрузки» и запустите установщик снова." \
      "Could not find a suitable copy of Asphalt 6. You need the original game (version 1.0.9). Put Asphalt 6.app in Applications or Downloads and run the installer again."
fi
ok "найдена: $ORIG" "found: $ORIG"

# ------------------------------------------------------------------ 4. engine download
step "Загрузка движка (около $(fmt_mb "${ENGINE_SIZE:-0}") МБ)" "Downloading the engine (about $(fmt_mb "${ENGINE_SIZE:-0}") MB)"
AVAIL_KB="$(df -k "$INSTALL_DIR" | awk 'NR==2{print $4}')"
NEED_MB=$(( $(fmt_mb "${ENGINE_SIZE:-0}") * 4 + 600 ))
if [ "${AVAIL_KB:-0}" -lt $((NEED_MB * 1024)) ]; then
  die "Мало места на диске: нужно примерно $NEED_MB МБ свободно." "Not enough free disk space: about $NEED_MB MB are needed."
fi
n=0
while :; do
  n=$((n+1))
  if curl -fL --retry 3 --connect-timeout 20 -C - -# "$BASE_URL/$ENGINE_FILE" -o "$TMP/$ENGINE_FILE"; then break; fi
  [ $n -lt 3 ] || die "Не удалось скачать движок. Проверьте интернет и попробуйте снова." "Could not download the engine. Check your internet connection and try again."
  warn "Повторная попытка загрузки..." "Retrying the download..."
done
[ "$(sha "$TMP/$ENGINE_FILE")" = "$ENGINE_SHA" ] || die "Контрольная сумма загруженного файла не совпала (файл повреждён). Попробуйте ещё раз." "The checksum of the downloaded file does not match (damaged file). Try again."
ok "контрольная сумма совпала" "checksum verified"
mkdir -p "$TMP/pkg" && tar -xzf "$TMP/$ENGINE_FILE" -C "$TMP/pkg" || die "Не удалось распаковать движок." "Could not unpack the engine."
rm -f "${TMP:?}/$ENGINE_FILE"
PKG="$TMP/pkg"; PY="$PKG/engine/x64/python/bin/python3"
[ -x "$PY" ] || die "В загруженном архиве нет Python." "The downloaded package has no Python."

# ------------------------------------------------------------------ 5. build the app
step "Сборка вашей копии игры" "Building your copy of the game"
STAGE="$INSTALL_DIR/.a6build.$$.app"
[ -e "$STAGE" ] && rm -rf "${STAGE:?}"
info "Копирую игру (на APFS это мгновенно и не занимает места)..." "Copying the game (instant and free of extra space on APFS)..."
if ! cp -c -R "$ORIG" "$STAGE" 2>/dev/null; then
  [ -e "$STAGE" ] && rm -rf "${STAGE:?}"
  cp -R "$ORIG" "$STAGE" || die "Не удалось скопировать игру." "Could not copy the game."
fi
ENG="$STAGE/Contents/Resources/engine"
chmod -R u+w "$STAGE" 2>/dev/null
# 5a. the options menu movie gets the new toggles (built from YOUR file, nothing of it is downloaded)
rm -f "${STAGE:?}/Contents/Resources/data/file000516.dat"
if ! arch -x86_64 "$PY" "$PKG/engine/tools/patch_options_swf.py" "$ORIG/Contents/Resources/data/file000516.dat" "$STAGE/Contents/Resources/data/file000516.dat" >"$TMP/patch.log" 2>&1; then
  cat "$TMP/patch.log" >&2; die "Не удалось подготовить меню настроек." "Could not prepare the options menu."
fi
if [ -n "$PATCHED_DAT_SHA" ] && [ "$(sha "$STAGE/Contents/Resources/data/file000516.dat")" != "$PATCHED_DAT_SHA" ]; then
  die "Меню настроек получилось не таким, как ожидалось." "The options menu did not come out as expected."
fi
ok "меню настроек: Gameplay, 3D Wheels, Original Duel Difficulty" "options menu: Gameplay, 3D Wheels, Original Duel Difficulty"
# 5b. the engine
mkdir -p "$ENG"
ditto "$PKG/engine" "$ENG" || die "Не удалось установить движок." "Could not install the engine."
rm -rf "${ENG:?}/installer" "${ENG:?}/tools"
mv "$STAGE/Contents/MacOS/Asphalt 6" "$ENG/game.i386"        # the original i386 executable is only data for the engine
# 5c. data derived from the original executable is built here, from your copy
if ! PYTHONPATH="$PKG/engine/installer/site" arch -x86_64 "$PY" "$PKG/engine/installer/meta_gen.py" "$ENG/game.i386" "$PKG/engine/installer/objects.json" "$ENG/game_meta.json" >"$TMP/meta.log" 2>&1; then
  cat "$TMP/meta.log" >&2; die "Не удалось проанализировать исполняемый файл игры." "Could not analyse the game's executable."
fi
ok "исполняемый файл игры проанализирован" "the game's executable was analysed"
cp "$PKG/launcher/Asphalt 6" "$STAGE/Contents/MacOS/Asphalt 6"; chmod +x "$STAGE/Contents/MacOS/Asphalt 6"
echo "$VERSION" > "$ENG/BUILD"
plutil -replace CFBundleIdentifier -string com.gameloft.asphalt6mac.arm64 "$STAGE/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "Asphalt 6 (Apple Silicon)" "$STAGE/Contents/Info.plist"
plutil -replace LSMinimumSystemVersion -string 11.0 "$STAGE/Contents/Info.plist"
find "$STAGE" -name "*.x" -delete 2>/dev/null
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
xattr -cr "$STAGE" 2>/dev/null
codesign --force --sign - "$STAGE/Contents/MacOS/Asphalt 6" >/dev/null 2>&1
codesign --force --sign - "$ENG/native/a6launcher" "$ENG/native/libhost.dylib" >/dev/null 2>&1
codesign --force --sign - "$STAGE" >/dev/null 2>&1
codesign --verify --strict "$STAGE" >/dev/null 2>&1 || die "Не удалось подписать приложение." "Could not sign the application."
ok "приложение собрано и подписано" "the application is built and signed"

# ------------------------------------------------------------------ 6. self test
step "Проверка" "Self-check"
arch -x86_64 "$ENG/x64/python/bin/python3" -c "
import sys; sys.path.insert(0, '$ENG/x64/site'); sys.path.insert(0, '$ENG')
import objc, numpy, Quartz, AppKit, json
m = json.load(open('$ENG/game_meta.json'))
assert len(m['bindings']) > 6000 and m['objects'], 'meta'
print('ok')" >"$TMP/selftest.log" 2>&1
if [ "$(tail -n1 "$TMP/selftest.log")" != ok ]; then cat "$TMP/selftest.log" >&2; die "Самопроверка не прошла." "The self-check failed."; fi
codesign --verify --strict "$STAGE" >/dev/null 2>&1 || die "Подпись приложения нарушена." "The application signature is broken."
ok "все компоненты на месте" "all components are in place"

# ------------------------------------------------------------------ 7. put in place
step "Установка в $INSTALL_DIR" "Installing into $INSTALL_DIR"
if [ -d "$DEST" ]; then rm -rf "${DEST:?}" || die "Не удалось заменить прежнюю копию." "Could not replace the previous copy."; fi
mv "$STAGE" "$DEST" || die "Не удалось переместить приложение." "Could not move the application."
STAGE=""
touch "$DEST"
ok "готово: $DEST" "done: $DEST"
printf '\n%s%s%s\n' "$G$B" "$(t 'Готово! Игра установлена.' 'Done! The game is installed.')" "$N"
printf '%s\n' "$(t "Ищите «Asphalt 6 (Apple Silicon)» в папке «Программы» (Launchpad). Оригинальная игра не изменена." "Look for \"Asphalt 6 (Apple Silicon)\" in your Applications folder (Launchpad). The original game was not changed.")"
printf '%s\n' "$(t 'Новые пункты: Options → Visuals → 3D Wheels in Race; Options → Gameplay → Original Duel Difficulty.' 'New items: Options → Visuals → 3D Wheels in Race; Options → Gameplay → Original Duel Difficulty.')"
printf '%s\n' "$(t 'Удалить: повторите команду с --uninstall (сохранения останутся).' 'To remove it: run the command again with --uninstall (your saves stay).')"
if have_tty && [ -z "${A6_NO_LAUNCH:-}" ]; then
  ask "Запустить игру сейчас? [Y/n]" "Launch the game now? [Y/n]"
  case "$REPLY_LINE" in n|N|no|NO|н|Н) ;; *) open "$DEST" ;; esac
fi
exit 0
