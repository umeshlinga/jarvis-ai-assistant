#!/bin/bash
# ==============================================================================
#  JARVIS v2 (ADVANCED, M1) - macOS APPLE SILICON SETUP
#  No sudo anywhere in this script.
# ==============================================================================
set -e

cd "$(dirname "$0")"

echo ""
echo "=================================================================="
echo "          JARVIS v7 - ONE ICON SETUP (Mac M1 / Apple Silicon)"
echo "=================================================================="
echo ""

# 1. Architecture + OS check -------------------------------------------------
ARCH="$(uname -m)"
echo "Detected architecture: $ARCH"
if [ "$ARCH" = "arm64" ]; then
    echo "Apple Silicon confirmed - sounddevice's arm64 wheels will be used (no PyAudio compile needed)."
else
    echo "Warning: this build is tuned for Apple Silicon (arm64). It may still work on $ARCH."
fi

if [ "$(uname -s)" != "Darwin" ]; then
    echo "Warning: this installer is for macOS. Voice (say), pmset, osascript and other"
    echo "         Mac actions will not work on this system, but local/text mode can still run."
fi

# 2. Optional portaudio via Homebrew (only for the OPTIONAL PyAudio extra) --
if command -v brew >/dev/null 2>&1; then
    echo "Homebrew found - installing portaudio (only needed for optional PyAudio)..."
    brew install portaudio || echo "portaudio install skipped/failed - fine, Jarvis v2 does not need it."
else
    echo "Homebrew not found. It is OPTIONAL for Jarvis v2 (sounddevice needs no portaudio)."
    echo "If you ever want it, install Homebrew with this official line, then re-run:"
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo "Continuing without Homebrew..."
fi

# 3. Pick a Python (3.12, then 3.11, then 3.10) ------------------------------
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "Python 3 is not installed. Install Python 3.10+ from https://www.python.org/downloads/macos/ and re-run."
    exit 1
fi
echo "Using Python: $($PYTHON_BIN --version) ($PYTHON_BIN)"

# 4. Virtual environment + dependencies --------------------------------------
if [ ! -d ".venv" ]; then
    echo "Creating isolated Python environment (.venv)..."
    "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing required packages..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. Config directory, API keys, default config ------------------------------
CONFIG_DIR="$HOME/.openjarvis"
mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_DIR/free.key" ] && [ -z "$GROQ_API_KEY" ]; then
    echo ""
    echo "Setup your FREE Groq API key (steps in README.md - console.groq.com/keys):"
    echo "One free key powers the brain, the fast Whisper ears AND the"
    echo "human (neural) speaking voice."
    echo "(Groq speech-to-text: fast, and much better with Indian-English accents"
    echo " than the free Google recognizer)."
    read -r -p "Enter Groq API Key (or press Enter to skip and use local mode): " GROQ_KEY
    # v7 key guard: a key in the wrong slot silently poisons every call
    # that uses it (this exact mix-up cost a whole v6 session). Groq
    # keys start "gsk_"; anything starting "sk-" belongs to OpenAI.
    case "$GROQ_KEY" in
        sk-*)
            echo "That looks like an OpenAI key, not a Groq key - skipped."
            echo "Jarvis will run in local mode until a Groq key (gsk_...) is added."
            GROQ_KEY=""
            ;;
    esac
    if [ -n "$GROQ_KEY" ]; then
        printf "%s" "$GROQ_KEY" > "$CONFIG_DIR/free.key"
        chmod 600 "$CONFIG_DIR/free.key"
        echo "Groq key saved to $CONFIG_DIR/free.key (permissions 600)."
    else
        echo "Skipped. Jarvis will run in local mode until a key is added."
    fi
else
    echo "Found an existing Groq key - keeping it."
fi

echo ""
echo "Optional but powerful: an OpenAI key upgrades Jarvis's brain for"
echo "tasks and agent work - a Muse-class brain plans and chains tools"
echo "with it, while Groq stays his fast, free ears and everyday chat."
echo "It also enables the OpenAI human-voice fallback. The key is read"
echo "at runtime, so pasting it later (no reinstall) works too."
# v7 self-heal check: an openai.key written by an earlier install may
# hold the GROQ key (a clipboard mix-up). Jarvis v7 ignores a wrong key
# at runtime, but say so plainly here too. The file is left untouched.
if [ -f "$CONFIG_DIR/openai.key" ]; then
    case "$(cat "$CONFIG_DIR/openai.key")" in
        gsk_*)
            echo "Note: your existing $CONFIG_DIR/openai.key holds a Groq key (gsk_...),"
            echo "not an OpenAI key. Jarvis v7 ignores it - he stays Groq-only, as you chose."
            echo "You can delete that file any time; nothing depends on it."
            ;;
    esac
fi

read -r -p "Optional: enter an OpenAI API key to upgrade the task/agent brain (or press Enter to skip): " OPENAI_KEY_INPUT
# v7 key guard (symmetric to the Groq step): never write a Groq key
# into the OpenAI slot.
case "$OPENAI_KEY_INPUT" in
    gsk_*)
        echo "That looks like a Groq key, not OpenAI - skipped. Jarvis stays Groq-only."
        OPENAI_KEY_INPUT=""
        ;;
esac
if [ -n "$OPENAI_KEY_INPUT" ]; then
    printf "%s" "$OPENAI_KEY_INPUT" > "$CONFIG_DIR/openai.key"
    chmod 600 "$CONFIG_DIR/openai.key"
    echo "OpenAI key saved to $CONFIG_DIR/openai.key (permissions 600)."
else
    echo "No OpenAI key - Groq / local mode only. You can add one later at $CONFIG_DIR/openai.key"
fi

if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cp config.example.json "$CONFIG_DIR/config.json"
    echo "Default config written to $CONFIG_DIR/config.json"
else
    echo "Existing config kept at $CONFIG_DIR/config.json"
fi

if [ ! -f "$CONFIG_DIR/contacts.json" ]; then
    cp contacts.example.json "$CONFIG_DIR/contacts.json"
    echo "Contacts book created at $CONFIG_DIR/contacts.json - edit it to add your people"
    echo "(name -> phone / email), so Jarvis can message and call them by voice."
else
    echo "Existing contacts kept at $CONFIG_DIR/contacts.json"
fi

# 6. Launchers -----------------------------------------------------------------
cat > jarvis <<'LAUNCHER'
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
exec python jarvis.py "$@"
LAUNCHER
chmod +x jarvis
echo "Launcher created: ./jarvis"

if [ -d "$HOME/Desktop" ]; then
    cat > "$HOME/Desktop/Jarvis.command" <<DESKTOP_LAUNCHER
#!/bin/bash
cd "$PWD"
source .venv/bin/activate
exec python jarvis.py
DESKTOP_LAUNCHER
    chmod +x "$HOME/Desktop/Jarvis.command"
    echo "Desktop launcher created: ~/Desktop/Jarvis.command (double-click to start Jarvis)"
fi

# 6b. Jarvis.app (v7 "One Icon") - double-click life, no Terminal ---------
# A real .app bundle in ~/Applications (plus a Desktop symlink) whose
# only job is to run `python jarvis.py --window` from this repo: Jarvis
# appears as his own floating window that talks. The repo's ABSOLUTE
# path is baked into the launcher at install time. macOS-only and fully
# guarded: any failure here must never fail the install itself.
if [ "$(uname -s)" = "Darwin" ]; then
(
    set +e
    REPO_DIR="$PWD"
    APP_DIR="$HOME/Applications/Jarvis.app"
    mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" || exit 0

    cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Jarvis</string>
    <key>CFBundleDisplayName</key>
    <string>Jarvis</string>
    <key>CFBundleIdentifier</key>
    <string>com.umeshlinga.jarvis</string>
    <key>CFBundleVersion</key>
    <string>7.0</string>
    <key>CFBundleShortVersionString</key>
    <string>7.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>Jarvis</string>
    <key>CFBundleIconFile</key>
    <string>Jarvis</string>
    <key>LSUIElement</key>
    <false/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Jarvis listens for your voice commands on this Mac.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>Jarvis controls your apps (Messages, Mail, Calendar) only when you ask him to.</string>
</dict>
</plist>
PLIST

    cat > "$APP_DIR/Contents/MacOS/Jarvis" <<APP_LAUNCHER
#!/bin/bash
# Jarvis.app launcher - generated by install.sh (Jarvis v7).
# Runs Jarvis in --window mode from the repo; all output goes to the
# app log so a double-click start never needs a Terminal.
REPO_DIR="$REPO_DIR"
LOG="\$HOME/.openjarvis/jarvis-app.log"
mkdir -p "\$HOME/.openjarvis"
START=\$SECONDS
cd "\$REPO_DIR" || {
    osascript -e 'display dialog "Jarvis could not find his folder at $REPO_DIR - it may have been moved. Re-run install.sh from the repo, or ask Leo." buttons {"OK"} default button "OK" with title "Jarvis"' >/dev/null 2>&1
    exit 1
}
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi
python jarvis.py --window >> "\$LOG" 2>&1
RC=\$?
if [ \$RC -ne 0 ] && [ \$((SECONDS - START)) -lt 5 ]; then
    osascript -e "display dialog \"Jarvis stopped right after starting. The log is at \$LOG - open it, or ask Leo and mention the Jarvis log.\" buttons {\"OK\"} default button \"OK\" with title \"Jarvis\"" >/dev/null 2>&1
fi
exit \$RC
APP_LAUNCHER
    chmod +x "$APP_DIR/Contents/MacOS/Jarvis" || exit 0

    # Icon: app/icon.png -> Jarvis.icns via sips + iconutil when present.
    if [ -f "$REPO_DIR/app/icon.png" ] && command -v sips >/dev/null 2>&1 \
        && command -v iconutil >/dev/null 2>&1; then
        ICONSET="$(mktemp -d)/Jarvis.iconset"
        mkdir -p "$ICONSET"
        for s in 16 32 64 128 256 512; do
            sips -z "$s" "$s" "$REPO_DIR/app/icon.png" \
                --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
            d=$((s * 2))
            sips -z "$d" "$d" "$REPO_DIR/app/icon.png" \
                --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
        done
        iconutil -c icns "$ICONSET" \
            -o "$APP_DIR/Contents/Resources/Jarvis.icns" >/dev/null 2>&1
        rm -rf "$(dirname "$ICONSET")"
    fi

    # Desktop: a symlink, so there is one Jarvis.app to keep updated.
    if [ -d "$HOME/Desktop" ]; then
        rm -rf "$HOME/Desktop/Jarvis.app"
        ln -sfn "$APP_DIR" "$HOME/Desktop/Jarvis.app"
    fi
    echo "Jarvis.app created: ~/Applications/Jarvis.app (+ Desktop shortcut)."
    echo "Double-click it to start Jarvis - no Terminal, no browser pop-open."
) || true
fi

# 7. Optional offline voice (faster-whisper) --------------------------------
echo ""
read -r -p "Install offline voice (faster-whisper, ~200MB with model download on first use)? (y/n): " OFFLINE_VOICE
if [ "$OFFLINE_VOICE" = "y" ] || [ "$OFFLINE_VOICE" = "Y" ]; then
    echo "Installing optional packages (faster-whisper etc.)..."
    pip install -r requirements-optional.txt
    # Make sure stt_engine=auto is set so Whisper is used when available.
    "$PYTHON_BIN" - "$CONFIG_DIR/config.json" <<'PYEOF' || true
import json, sys
path = sys.argv[1]
try:
    with open(path) as f:
        cfg = json.load(f)
except Exception:
    cfg = {}
cfg["stt_engine"] = "auto"
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("stt_engine=auto set in", path)
PYEOF
    echo "Offline voice installed. The Whisper model downloads ONCE on first"
    echo "use (needs internet that one time), then voice works fully offline."
else
    echo "Skipping offline voice - Jarvis will use Groq Whisper ears online"
    echo "(with Google's recognizer as the last fallback)."
    echo "You can install it any time: pip install -r requirements-optional.txt"
fi

# 8. Next steps ------------------------------------------------------------------
echo ""
echo "=================================================================="
echo "          JARVIS v7 SETUP COMPLETE"
echo "=================================================================="
echo ""
echo "YOUR NEW DAILY LIFE (v7): double-click Jarvis.app on your Desktop."
echo "No Terminal. No browser popping open. Jarvis appears as his own"
echo "small window that talks; its Stop button cuts his voice, Quit"
echo "closes him. (Terminal stays as Leo's repair hatch.)"
echo ""
echo "Health check any time Leo asks for one:"
echo "  cd $PWD && ./jarvis --doctor"
echo ""
echo "Human voice: Jarvis speaks with a neural human voice (Groq Orpheus,"
echo "voice 'daniel') powered by your Groq key - test it any time with"
echo "  ./jarvis --voice-test"
echo "It needs internet; offline he falls back to the macOS voice."
echo "Change the voice in ~/.openjarvis/config.json (tts_voice):"
echo "  male: daniel, austin, troy - female: hannah, autumn, diana."
echo ""
echo "IMPORTANT - macOS permissions (one time):"
echo "  Microphone:   System Settings > Privacy & Security > Microphone > enable Terminal"
echo "  Accessibility (for typing/key commands + WhatsApp send + pop-up watching):"
echo "                System Settings > Privacy & Security > Accessibility > enable Terminal"
echo "  Full Disk Access (so Jarvis can READ your notifications, v6):"
echo "                System Settings > Privacy & Security > Full Disk Access > enable Terminal"
echo "                (Without it he still watches pop-ups - see README 'Give Jarvis full access'.)"
echo "  (If you launch from Jarvis.command, macOS will prompt you the first time.)"
echo "  NOTE for Jarvis.app: permissions belong to the app that runs Jarvis."
echo "  If macOS asks about 'Jarvis' (the app) instead of Terminal - Microphone,"
echo "  Accessibility, Full Disk Access, Automation - allow it the same way;"
echo "  you may need to add 'Jarvis' in those lists once, next to Terminal."
echo ""
echo "Start Jarvis (voice, from Terminal):"
echo "  cd $PWD && ./jarvis"
echo "Start in text mode:"
echo "  cd $PWD && ./jarvis --text"
echo "Run a single command:"
echo "  cd $PWD && ./jarvis --once \"what time is it\""
echo "Live visual HUD (watch Jarvis work, live transcript + tasks):"
echo "  v7: it no longer pops open in your browser by itself. Say"
echo "  'show yourself' when you want it, or open http://127.0.0.1:8765"
echo "  yourself (localhost only). His own window is the everyday face."
echo "Open the holographic HUD page:"
echo "  open jarvis_workplace.html"
echo ""

read -r -p "Would you like to start Jarvis now in text mode? (y/n): " START_NOW
if [ "$START_NOW" = "y" ] || [ "$START_NOW" = "Y" ]; then
    python jarvis.py --text
fi
