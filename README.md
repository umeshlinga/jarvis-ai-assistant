# Jarvis AI Assistant — Advanced v3 "Full Voice Control" (M1)

> Built on the original jarvis-ai-assistant by saipradeep77964 (github.com/saipradeep77964/jarvis-ai-assistant), extended by Umesh Linga.

A voice-controlled AI assistant for **MacBook M1 / Apple Silicon (arm64)**, rebuilt and extended from the original project above. The original is **not** Umesh's invention — this version keeps its brain / actions / market design and adds the fixes and features below.

**v3 is "Full Voice Control":** Jarvis doesn't just open apps any more — he opens any file or folder by name, messages and calls your contacts, types and presses keys for you, controls music, creates real Reminders and Calendar events, manages files (with your confirmation), and — just as importantly — he **talks with you**: a friendly conversational companion who explains things, gives honest opinions when you ask, and speaks every reply in full. See "Talking with Jarvis" and the v3 command table below.

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

Anything else goes to the cloud brain (Groq, else OpenAI) for a spoken answer.

### New in v3 — Full Voice Control

| Say | Jarvis does |
|---|---|
| "Open file resume.pdf" / "Open resume file" / "Open my report document" | Finds the file by name (Spotlight, preferring Desktop/Documents/Downloads) and opens the top match — and tells you which one |
| "Open folder projects" | Finds and opens that folder |
| "Send a message to Mom saying running late" | iMessage via the Messages app — **after you confirm with "yes"**. Unknown contact → he asks for the number/email and sends nothing |
| "Send WhatsApp to Mom saying hello" | Opens the real chat via `wa.me/<number>`; after your "yes", waits for it to load and presses Enter for you (needs Accessibility permission, below). No number in contacts → opens WhatsApp Web prefilled and says why |
| "Email Mom saying see you at six" / "Send email to mom@example.com subject Lunch saying bring salad" | Opens a **prefilled Gmail compose** (to / subject / body). He never sends email silently — you press Send |
| "Call Mom" / "Video call Mom" / "FaceTime Mom" | Starts a FaceTime / FaceTime-audio call from the contact's number or email |
| "Type the meeting moved to five" | Types the text into whatever app is active |
| "Press Enter" / "Press Tab" / "Press Escape" / "Press Space" / "Press Delete" | Presses the key |
| "Copy that" / "Paste" / "Select all" / "Save this" / "Undo" | Cmd+C / Cmd+V / Cmd+A / Cmd+S / Cmd+Z in the active app |
| "Find refund policy" | Cmd+F, then types the text |
| "New tab" / "Close tab" / "Close window" / "Switch app" / "Show desktop" / "Mission Control" / "Minimize window" / "Zoom window" / "Scroll up" / "Scroll down" | Window & navigation control |
| "Play music" / "Pause music" / "Next song" / "Previous song" | Controls Spotify if installed, otherwise Apple Music |
| "Play Blinding Lights on Spotify" | Opens the Spotify search for it |
| "Remind me to call Mom" / "Remind me to pay rent tomorrow at 6 pm" | Creates a **real** reminder in Apple Reminders (due date parsed from today/tomorrow + times like `6 pm` or `18:30`; no time → no due date) |
| "Add event dentist tomorrow at 10 am" | Creates a **real** 1-hour event in Apple Calendar |
| "Create folder invoices on desktop" / "Create file notes.txt in documents" | Creates it in that folder |
| "Move report.pdf to downloads" | Moves the file — **after your "yes"** |
| "Trash old-draft.docx" / "Delete screenshot.png" | Moves the file to the Trash (never a permanent delete) — **after your "yes"** |
| "Brightness up" / "Brightness down" | Screen brightness keys |
| "Wi-Fi on" / "Wi-Fi off" | Toggles Wi-Fi (`networksetup`) |
| "Turn on Do Not Disturb" | Runs your `Toggle Focus` Shortcut — needs the one-time Shortcut setup in Honest limits |
| "Read this" / "Read my screen selection" / "What's on my screen" | Copies the selection and speaks it back. Nothing copied/selected → he says so honestly |
| "Take a video" / "Record my screen" | **Not supported** — he says so (screenshots work) |
| "Open website example dot com" | Opens https://example.com |
| "Morning briefing" / "Brief me" / "What's my day" | Spoken briefing: greeting, date, time, battery, your reminders, a market one-liner, and a friendly close |

Date/time parsing for reminders and events is deliberately simple: only **today / tomorrow** and times like `6 pm`, `6:30 pm`, `18:30`. Anything fancier ("next Friday", "in two hours") is not parsed — the reminder is created without a due date and Jarvis tells you what he created.

### Permissions v3 needs (two of them)

1. **Microphone** — System Settings → Privacy & Security → **Microphone** → enable **Terminal** (or your launcher app).
2. **Accessibility** — System Settings → Privacy & Security → **Accessibility** → enable **Terminal**. Without this, the type/press-key commands and the WhatsApp auto-send Enter press silently do nothing — macOS blocks them, by design.

### Contacts — so Jarvis can message & call people

Jarvis keeps a simple address book at `~/.openjarvis/contacts.json` (the installer creates it from `contacts.example.json`):

1. Open the file: `open ~/.openjarvis/contacts.json` (or edit it in any text editor).
2. Add your people in this shape:
   ```json
   {
     "Mom": {"phone": "+1 555 010 2030", "email": "mom@example.com"},
     "Pallavi": {"phone": "+91 98765 43210", "email": ""}
   }
   ```
3. Save. Lookups are case-insensitive and partial ("call mom", "WhatsApp pal…" work). No restart needed.

Phone is used for iMessage, WhatsApp (`wa.me`) and FaceTime; email for iMessage/FaceTime and prefilled email. A name Jarvis doesn't know is never guessed — he asks you for the number/email instead of sending to the wrong person.

### Offline voice (faster-whisper)

By default (`stt_engine: "auto"` in the config) Jarvis uses **faster-whisper locally** when it's installed — your voice never leaves the Mac for transcription — and falls back to Google's online recognizer when it isn't (with the reason printed). Set `stt_engine` to `"whisper"` to force offline or `"google"` to force online.

Install it via the installer's prompt, or any time with:

```bash
source .venv/bin/activate
pip install -r requirements-optional.txt
```

The speech model (`whisper_model`, default `base.en`) **downloads once on first use — that one download needs internet (~200MB with the package). After that, voice recognition works fully offline.** Offline, all the local commands above still work and are spoken in full; open-ended chat is limited (see Honest limits).

---

## Talking with Jarvis

Jarvis is a **conversational companion**, not a vending machine for facts. Just talk to him like a person sitting next to you:

- **Chat:** "How are you today?", "Explain how inflation works like I'm new to it", "Tell me about the Norman 2019 Perturb-seq paper" — he explains properly: what it is, why it matters, what it means for you.
- **Opinions & advice:** "What do you think about taking a contract role?", "Is it correct that I should learn Rust next?", "Should I apply to this job?" — Jarvis gives a clear, reasoned view: what looks right, what looks wrong, why, and what he'd suggest instead. Opinion questions are routed to his thinking brain even when a local keyword might match. If he doesn't know a fact, he says so plainly — he never invents one.
- **He remembers the conversation** — history persists across restarts (last 30 turns) in `~/.openjarvis/history.json`, so follow-ups like "and what about the second point?" make sense.
- **Every reply is spoken in full** in voice mode — there are no silent, print-only answers.
- **Morning briefing:** on startup he greets you by name and time of day, mentions your battery if it's low or unplugged, and suggests the briefing: say **"morning briefing"** for your date, time, battery, reminders, and a market one-liner.

**Honest limit on "thinking":** his thinking, opinions and open-ended explanations come from the **online AI brain (Groq / OpenAI)**. Offline, he does not truly think — he follows commands and reports local facts, and for an opinion question he'll tell you exactly that and ask you to ask again online.

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
- `max_history_turns` — 30
- `stt_engine` — "auto" / "google" / "whisper" · `whisper_model` — "base.en" · `suggestions` — true

Files Jarvis keeps in `~/.openjarvis/`: `config.json`, `contacts.json`, `free.key` (Groq), `openai.key` (optional), `history.json`, `notes.txt`, and optionally `mail_cache.txt` (see below).

## Safety

- **No sudo** anywhere — installer or actions.
- **Keys stay local**: stored only in `~/.openjarvis/*.key` with `chmod 600`, or in environment variables. Never in this repo.
- **Confirmations**: sending any message (iMessage, WhatsApp), moving a file, trashing a file, emptying the trash, restarting, shutting down and logging out never run without an explicit "yes".
- Local actions run on your Mac only; the only network calls are the LLM provider you configured, Google's speech recognizer (below), and Yahoo Finance for market data.

## Honest limits

- **No real Gmail reading.** Jarvis cannot access your Gmail account. "Read my emails" says exactly that and opens Gmail for you. The only exception: if *you* save a briefing text at `~/.openjarvis/mail_cache.txt`, Jarvis will read that file back to you — it still never fetches mail itself. (The original version invented a fake inbox summary; v2 removes that.)
- **Market signals are simple moving averages** over Yahoo Finance data — a rough technical indicator, **not financial advice**.
- **Speech recognition**: with faster-whisper installed (the installer's offline-voice option), transcription is **fully local** after the one-time model download. Without it, recognition uses Google's free recognizer, exactly like the original: your voice audio for each command is sent to Google for transcription. See "Offline voice". Speech *output* is always local (macOS `say`).
- Voice quality, mic pickup, and `say` voices depend on your Mac and macOS permissions — grant the Microphone permission above.
- **WhatsApp sending** needs three things at once: the person's number in `contacts.json`, Accessibility permission for Terminal, and your "yes" — only then does Jarvis press Enter for you. Without a number, it opens WhatsApp Web prefilled and says so. **Email never auto-sends**: Jarvis opens a prefilled Gmail compose and you press Send.
- **Do Not Disturb** needs a one-time Shortcut on your Mac named `Toggle Focus` (Shortcuts app → New Shortcut → action "Set Focus" toggled → name it `Toggle Focus`). Without it, Jarvis says exactly that and changes nothing.
- **Calls** use FaceTime on the Mac (there is no cellular calling) — they need the contact's number or email.
- **Offline thinking:** Jarvis's opinions and open-ended explanations need the online brain (Groq / OpenAI). Offline he runs commands and local facts, and says so for anything else — he doesn't fake thinking offline.
- **Reminders / Calendar parsing** is simple by design (today / tomorrow + `6 pm` / `18:30`); anything fancier loses the due date rather than being mis-dated.

## Tests

```bash
python3 tests/test_actions.py   # stubs all system helpers; nothing opens, no network
```
