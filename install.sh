#!/bin/bash
# ==============================================================================
#  JARVIS v2 (ADVANCED, M1) - macOS APPLE SILICON SETUP
#  No sudo anywhere in this script.
# ==============================================================================
set -e

cd "$(dirname "$0")"

echo ""
echo "=================================================================="
echo "          JARVIS v2 - ADVANCED SETUP (Mac M1 / Apple Silicon)"
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
    read -r -p "Enter Groq API Key (or press Enter to skip and use local mode): " GROQ_KEY
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
read -r -p "Optional: enter an OpenAI API key for the backup brain (or press Enter to skip): " OPENAI_KEY_INPUT
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

# 7. Next steps ------------------------------------------------------------------
echo ""
echo "=================================================================="
echo "          JARVIS v2 SETUP COMPLETE"
echo "=================================================================="
echo ""
echo "IMPORTANT - macOS microphone permission (one time):"
echo "  System Settings > Privacy & Security > Microphone > enable Terminal"
echo "  (If you launch from Jarvis.command, macOS will prompt you the first time.)"
echo ""
echo "Start Jarvis (voice):"
echo "  cd $PWD && ./jarvis"
echo "Start in text mode:"
echo "  cd $PWD && ./jarvis --text"
echo "Run a single command:"
echo "  cd $PWD && ./jarvis --once \"what time is it\""
echo "Open the holographic HUD:"
echo "  open jarvis_workplace.html"
echo ""

read -r -p "Would you like to start Jarvis now in text mode? (y/n): " START_NOW
if [ "$START_NOW" = "y" ] || [ "$START_NOW" = "Y" ]; then
    python jarvis.py --text
fi
