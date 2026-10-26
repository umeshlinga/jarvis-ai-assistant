# Jarvis AI Assistant — Advanced (M1)

> Built on the original jarvis-ai-assistant by saipradeep77964 (github.com/saipradeep77964/jarvis-ai-assistant), extended by Umesh Linga.

A voice-controlled AI assistant for **MacBook M1 / Apple Silicon (arm64)**, rebuilt and extended from the original project above. The original is **not** Umesh's invention — this version keeps its brain / actions / market design and adds the fixes and features below.

---

## What's new in v2

| Area | Original | v2 (this build) |
|---|---|---|
| Microphone on M1 | `SpeechRecognition.Microphone()` needs PyAudio, which was **missing from requirements** and often fails to compile on Apple Silicon — the mic broke | Records with **sounddevice** (prebuilt arm64 wheels, no compilation) into an in-memory WAV, then transcribes via SpeechRecognition `AudioFile`. **PyAudio is not required at runtime.** Text mode fallback if no mic |
| Brain | Groq only | **Groq first, OpenAI as second provider** (auto-selected by which key exists). Groq models are configurable; OpenAI defaults to `gpt-4.1-mini` |
| Memory | Last 6 messages, in RAM only | Conversation history **persists** to `~/.openjarvis/history.json` (last 20 turns kept) |
| Mail brief | **Invented** "3 unread priority emails…" | **Fixed.** Jarvis never invents emails — see Honest limits |
| Actions | Open app/site, volume up/down/mute, WhatsApp, YouTube/Google search, time/date, market | All of those, **plus**: quit app, open folder, find file (Spotlight), screenshot, battery, system info, Wi-Fi status, set volume to N%, unmute, dark mode toggle, lock screen, clipboard copy/read, notes, timers, ChatGPT/LinkedIn/Indeed |
| Config | Hard-coded voice, models, name | `~/.openjarvis/config.json` — owner name, wake word, voice, speech rate, models, provider order |
| Entry point | `jarvis_voice.py` only | `jarvis.py` — `--text`, `--once "command"`, `--no-speak`, voice by default, honest startup banner (provider + mic status) |
| Safety | — | Destructive actions (empty trash, restart, shutdown, logout) require a spoken/typed **"yes"** confirmation. No `sudo` anywhere |

Unchanged from the original, on purpose: the rate-limit cooldown + local fallback engine, the Yahoo Finance market module (plus 3 added US tickers: **MSFT, TSLA, ^GSPC / S&P 500**), the holographic HUD (`jarvis_workplace.html`), and local-actions-before-LLM ordering.

---

## Install on Mac M1 (exact commands)

You need macOS on Apple Silicon and Python 3.10+ (install Python from python.org if `python3 --version` shows nothing).

```bash
# 1. Get this folder onto your Mac, then open Terminal in it:
cd jarvis-advanced

# 2. Run the installer (no sudo; it creates .venv, installs deps,
#    asks for your keys, writes the default config, and creates launchers):
chmod +x install.sh
./install.sh

# 3. Start Jarvis (voice):
./jarvis

# …or text mode, or a single command:
./jarvis --text
./jarvis --once "what time is it"
```

The installer also creates **`~/Desktop/Jarvis.command`** — double-click it to start Jarvis.

**One-time mic permission:** System Settings → Privacy & Security → Microphone → enable **Terminal** (macOS will also prompt on first launch). Without this, macOS silently gives Jarvis silence, and it will fall back to text mode.

Optional extras (old PyAudio path / pyttsx3) are in `requirements-optional.txt` and are **not** needed:

```bash
brew install portaudio   # only if you ever install the optional PyAudio
```

---

## Get a free Groq key

1. Go to **console.groq.com/keys** and sign in (free account).
2. Click **Create API Key**, name it anything, and copy the key.
3. Paste it when `install.sh` asks — it is saved to `~/.openjarvis/free.key` with `chmod 600`.
   Or save it yourself any time:
   ```bash
   mkdir -p ~/.openjarvis
   printf 'YOUR_KEY' > ~/.openjarvis/free.key
   chmod 600 ~/.openjarvis/free.key
   ```
4. Optional OpenAI key (paid): put it in `~/.openjarvis/openai.key` the same way, or set `OPENAI_API_KEY`. Jarvis uses Groq first whenever a Groq key exists.

Pressing Enter at the key prompts is fine — Jarvis runs in **local mode** (apps, volume, notes, timers, battery, market, etc. still work; open-ended questions need a cloud key).

---

## Voice command table

Say **"Jarvis, …"** first, or just the command.

### From the original

| Say | Jarvis does |
|---|---|
| "Open VS Code" / "Open Chrome" / "Open Terminal" | Launches the app |
| "Open YouTube" / "Open Gmail" / "Open GitHub" | Opens the site |
| "Play lofi on YouTube" | Opens YouTube search results for it |
| "Search for best coffee near me" / "Google …" | Opens a Google search |
| "Send WhatsApp to Pallavi saying hello" | Opens WhatsApp Web with the message filled in |
| "Open WhatsApp" | Opens WhatsApp Web |
| "Volume up" / "Volume down" / "Mute" | Adjusts system volume |
| "What time is it" / "What is the date" | Speaks the time / date |
| "Stock market update" / "Analyze Nvidia" / "Analyze Microsoft" / "Analyze Tesla" | Yahoo Finance price + simple moving-average signal |

### New in v2

| Say | Jarvis does |
|---|---|
| "Open ChatGPT" / "Open LinkedIn" / "Open Indeed" | Opens the site |
| "Quit Safari" / "Close Spotify" | Quits the app |
| "Open Downloads folder" / "Open Documents" / "Open Desktop" / "Open Pictures" | Opens that folder in Finder |
| "Find file report.pdf" | Spotlight (`mdfind`) search, speaks the top 3 matches |
| "Take a screenshot" | Saves a timestamped screenshot to the Desktop |
| "Battery status" | Speaks battery % and charging state (`pmset`) |
| "System info" | Chip, macOS version, RAM and disk usage |
| "Wi-Fi status" | Speaks the connected network name |
| "Set volume to 50 percent" | Sets volume to exactly 50% and unmutes |
| "Unmute" | Turns muting off |
| "Toggle dark mode" / "Dark mode" | Flips macOS dark mode |
| "Lock screen" / "Lock my Mac" | Locks/sleeps the display (`pmset displaysleepnow`) |
| "Copy the meeting is at five" | Copies that text to the clipboard (`pbcopy`) |
| "Read my clipboard" | Speaks the clipboard contents (`pbpaste`) |
| "Note buy milk tomorrow" | Appends a timestamped line to `~/.openjarvis/notes.txt` |
| "Set a timer for 5 minutes" | Background timer; Jarvis speaks when it fires |
| "Read my emails" / "Check mail" | **Honest answer + opens Gmail** (see limits below) |
| "Empty trash" / "Restart" / "Shut down" / "Log out" | **Asks for a "yes" confirmation first**, then acts |

Anything else goes to the cloud brain (Groq, else OpenAI) for a short spoken answer.

---

## Text mode

```bash
./jarvis --text
./jarvis --once "battery status" --no-speak
```

Text mode is also the automatic fallback whenever no microphone is available.

## Config

`~/.openjarvis/config.json` (created from `config.example.json` by the installer):

- `owner_name` — "Umesh" · `wake_word` — "jarvis"
- `voice` — macOS `say` voice, default "Samantha" · `speech_rate` — 185
- `groq_models` — tried in order · `provider_order` — `["groq", "openai"]`
- `openai_model` — "gpt-4.1-mini" · `openai_base_url` — "https://api.openai.com/v1"
- `max_history_turns` — 20

Files Jarvis keeps in `~/.openjarvis/`: `config.json`, `free.key` (Groq), `openai.key` (optional), `history.json`, `notes.txt`, and optionally `mail_cache.txt` (see below).

## Safety

- **No sudo** anywhere — installer or actions.
- **Keys stay local**: stored only in `~/.openjarvis/*.key` with `chmod 600`, or in environment variables. Never in this repo.
- **Confirmations**: empty trash, restart, shutdown and logout never run without an explicit "yes".
- Local actions run on your Mac only; the only network calls are the LLM provider you configured, Google's speech recognizer (below), and Yahoo Finance for market data.

## Honest limits

- **No real Gmail reading.** Jarvis cannot access your Gmail account. "Read my emails" says exactly that and opens Gmail for you. The only exception: if *you* save a briefing text at `~/.openjarvis/mail_cache.txt`, Jarvis will read that file back to you — it still never fetches mail itself. (The original version invented a fake inbox summary; v2 removes that.)
- **Market signals are simple moving averages** over Yahoo Finance data — a rough technical indicator, **not financial advice**.
- **Speech recognition goes to Google's free recognizer**, exactly like the original: your voice audio for each command is sent to Google for transcription. Speech *output* is local (macOS `say`).
- Voice quality, mic pickup, and `say` voices depend on your Mac and macOS permissions — grant the Microphone permission above.
- WhatsApp "sending" opens WhatsApp Web with the message pre-filled; you press Send yourself.

## Tests

```bash
python3 tests/test_actions.py   # stubs all system helpers; nothing opens, no network
```
