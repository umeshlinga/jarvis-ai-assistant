# Jarvis AI Assistant — Advanced v5 "Autonomous Butler" (M1)

> Built on the original jarvis-ai-assistant by saipradeep77964 (github.com/saipradeep77964/jarvis-ai-assistant), extended by Umesh Linga.

A voice-controlled AI assistant for **MacBook M1 / Apple Silicon (arm64)**, rebuilt and extended from the original project above. The original is **not** Umesh's invention — this version keeps its brain / actions / market design and adds the fixes and features below.

---

## What's new in v5 — "Autonomous Butler"

v5 is built from Umesh's live v4.1 run, in his own words: *"If I say stop it's not stopping — it keeps on reading… it's blabbering… if I say open, it should open normally and NOT speak… in the backend it should run automatically; if something is important it should remind me… you should read everything in my laptop… What's the time? — 'It's six forty-five.' That length."* Four changes:

**1. He stops when you say stop (barge-in).** Playback is now a tracked, killable process. While Jarvis talks, a second ear listens in short windows for stop words only — **"stop"**, **"quiet"**, **"shut up"**, **"enough"**, **"Jarvis, stop"** — and cuts him off mid-sentence, then goes back to listening. An echo guard stops him mistaking his *own* voice (heard through the speakers) for you. Saying just "stop" as your next command is also understood silently — no reply, no lecture. (Ctrl+C still stops Jarvis entirely; the keyboard isn't watched while he listens.)

**2. Silent actions, short answers (speak policy).** Pure acknowledgements — "Opening Chrome.", "Volume set to 50 percent.", "Screenshot saved…", "Timer set…" — are **printed and shown on the HUD, never spoken**. Jarvis speaks only when he has something to tell you: answers, results, problems, questions. And spoken answers are trimmed: chat to the first 1–2 sentences (~55 words), information routes (search, file reads, market, task updates) to ~110 words; the full text is always on screen and on the HUD. The brain's default is now **one or two short sentences** — "What's the time?" → "It's six forty-five." Config `speak_actions: true` restores the old speak-everything behaviour.

**3. A proactive butler.** A background thread (config `proactive_enabled`, checks every `proactive_interval_min`, default 30) watches your **Reminders** (due within 30 minutes, or overdue), **Calendar** (events starting within 30 minutes), **battery** (≤ 20% and not charging), offers your **morning briefing once a day** after 7 AM, and — once a morning, config `proactive_jobs` — counts fresh web results for *bioinformatics jobs in Indianapolis* and asks: *"Umesh, I found some openings like this. Want me to open them?"* Alerts are **spoken once, between listen cycles** — never over your voice. Suggestions are questions: say **yes / open it** and he does it (the job suggestion opens the real LinkedIn Jobs results), say **no / leave it** and he drops it silently. Anything else and the suggestion simply expires. Each item fires once per day — no nagging.

**4. He reads your files.** **"Read my resume"**, **"read report.pdf"**, **"what's in notes.txt"**, **"open and read contract.docx"** — Jarvis finds the file, tells you its name and size, and reads a 3–4 sentence summary aloud. **"Read more"** speaks the actual text, a page (~110 words) at a time, remembering where he stopped. Formats: `.txt .md .py .csv .json .log` directly, `.docx` from its XML, `.pdf` via the free **poppler** tool (`pdftotext`) — without poppler he opens the PDF and tells you honestly that reading it aloud needs `brew install poppler`. Other formats are opened on screen with the same honesty. He never invents file content.

Plus small GUI verbs: **"scroll up / scroll down"**, **"press enter / tab / escape"**, **"switch to <app>"**, and best-effort **"click <name>"** (System Events searches the front window for a UI element with that name/title — needs Accessibility permission for Terminal, and says so when it can't).

**New config keys (v5):** `speak_actions` — false · `proactive_enabled` — true · `proactive_interval_min` — 30 · `proactive_jobs` — true.

---

## What's new in v4.1 — "Do the work" + human voice

v4.1 is built from Umesh's first real v4 run (screenshots, not guesses). Four fixes:

**1. Jarvis DOES the work now — he doesn't just talk about it.** In v4, "show me the job openings in Indianapolis in bioinformatics" matched no command, fell through to chat, and got generic *"check LinkedIn and Indeed"* suggestions. Now a job search **acts**: Jarvis opens **LinkedIn Jobs with the live results in your browser** and **reads the top findings aloud** ("I searched for bioinformatics jobs in Indianapolis and opened LinkedIn Jobs with the live results. Here's what I found: …"). Everyday information phrasings act too — **"show me …"**, **"find me …"**, **"find out …"**, **"what are the …"**, **"any news about …"**, a bare **"search …"** or **"look up …"** all run a real web search; when you said "show", the results page opens in your browser as well, so you can *see* the work.

**2. Dead-model latch (speed + a clean terminal).** Groq no longer offers `llama-3.1-8b-instant` on this account — v4 retried it on *every* message and ate a 404 before answering. The fast chat model is now **`openai/gpt-oss-20b`** (already proven on his key), an old saved `llama-3.1-8b-instant` config value migrates automatically, and **any** model that ever answers "model not found" is skipped silently for the rest of the session — mentioned once, never retried.

**3. Sharper ears.** Groq Whisper now uses the full **`whisper-large-v3`** model (accuracy over turbo) plus a **vocabulary prompt** (`stt_vocab_prompt`): Whisper is told the words you actually say — *Jarvis, Umesh Linga, bioinformatics, Indianapolis, LinkedIn, resume…* — the exact words v4 garbled ("bioinformatics" → "Bighton"). Edit the prompt in `~/.openjarvis/config.json` to teach him new words.

**4. A human voice.** See the next section — the robotic Mac voice is now the fallback, not the voice.

### Human voice (new in v4.1)

Jarvis now speaks with a **neural, human-sounding voice** instead of the robotic macOS `say` voice. How it works:

- **Engine:** Groq's Orpheus text-to-speech (`canopylabs/orpheus-v1-english`), using the **same Groq key** you already have — no new account, no new key. If an OpenAI key is configured, OpenAI's neural TTS is the second choice; macOS `say` is the final fallback (config `tts_engine`: `"auto"` / `"groq"` / `"openai"` / `"say"`).
- **Voice:** default **`daniel`** — a male voice, the Jarvis feel. Male alternatives: **austin**, **troy**. Female: **hannah**, **autumn**, **diana**. To change it, edit `~/.openjarvis/config.json` and set `"tts_voice": "hannah"` (any of the six), then restart Jarvis.
- **Test it:** `./jarvis --voice-test` speaks one sentence in the current voice.
- **How it stays natural:** replies are split into sentence-sized chunks (Orpheus takes ~200 characters at a time) and played back-to-back with `afplay`, so it sounds like one person talking.
- **If the neural voice fails** (no internet, model unavailable), Jarvis says so **once** in the terminal and uses the Mac voice for the rest of the session — replies are never blocked or lost.
- **Honest note:** the neural voice **needs internet**. Offline, Jarvis falls back to macOS `say`. For a better *offline* voice, download a macOS **Premium** voice: **System Settings → Accessibility → Spoken Content → System Voice → Manage Voices…**, pick a Premium/Enhanced voice (e.g. Samantha Premium or Daniel Premium), download it, select it as the System Voice, and set `"voice"` in the config to its name with `"tts_engine": "say"` if you want it always.

---

## What's new in v4 — "Supercomputer"

v4 is a speed + awareness upgrade on top of v3. Everything from v3 still works; this is what changed:

**A. Fast, accurate ears (the big fix).** In `auto` mode Jarvis now transcribes your voice with **Groq's Whisper API** using the same free Groq key as the brain — much faster than the free Google recognizer, and far better with Indian-English accents. *(v4.1: the model is now the full `whisper-large-v3`, plus a vocabulary prompt — see above.)* The order is: **Groq Whisper → local faster-whisper → Google**. If Groq's ears hiccup (network / rate limit), Jarvis falls through to the next recognizer in the same breath and rests Groq for 60 seconds, then tries it again — one bad minute never kills it. Recording still stops ~1 second after you finish speaking (VAD), so there's no long dead air at the end of every sentence.

**B. Two-speed brain + a companion who knows you.** Everyday chat runs on a fast model; deep / advisory questions ("what do you think…", advice, analysis) use the big models. *(v4.1: the fast model is `openai/gpt-oss-20b` — Groq dropped `llama-3.1-8b-instant` from this account, and dead models are now latched off instead of retried per message.)* Spoken answers are **two to four natural sentences** by default — say "explain in detail" or "tell me more" when you want the long version (that kills the old "plain text" feel). The system prompt carries a short personal context (config `personal_context` — who you are, your field, your job search) so answers talk about *your* situation instead of generic advice. Edit it in `~/.openjarvis/config.json`; it lives on your Mac and only travels inside your own Groq/OpenAI requests.

**C. Live visual HUD.** Jarvis now has a face you can watch: a localhost-only page (`http://127.0.0.1:8765`, config `hud_port`) showing his current state (listening / thinking / speaking / working), the live transcript ("You said" / "Jarvis"), and an activity + background-task feed. It opens automatically on startup (config `hud_auto_open`), or say **"show yourself" / "open the hud"** any time. No internet needed — Jarvis serves it himself, bound to 127.0.0.1 only.

**D. Browser brain.** Say **"search for electric cars and tell me"** (or "read about …") and Jarvis fetches DuckDuckGo results and reads you the top 3; say **"open the first result"** to open the top hit. Say **"read bbc dot com"** or **"read this website …"** and he fetches the page and summarises it in 4–5 spoken sentences (if the brain is offline he reads the first sentences raw and tells you so). Say **"open browser"** to open your default browser.

**E. Coding by voice.** Say **"create a python file called hello that prints the date"** (or "write code for X in file Y") and Jarvis writes the code (deep model, code only) into `~/jarvis-projects` (config `code_dir`). **"Open it"** opens it in VS Code or your text editor. **"Run it"** asks for your confirmation first, then runs it as a background task and speaks the result — or the error's last line plus "say fix it". **"Fix it"** sends the code + error back to the model, rewrites the file (keeping a `.bak`), tells you what changed in one line — and never re-runs it without you saying so.

**F. Background tasks that talk.** Timers and code runs now run as background tasks. When one finishes (or fails), Jarvis **announces it out loud, unprompted** — you never have to ask. Ask **"what are you doing"** / **"task status"** any time to hear what's running and what last finished. Announcements land **between listen cycles**, not over your voice mid-sentence — that's how the speak/listen loop works, not a bug.

**G. Files: attach + copy.** **"Email Mom and attach report.pdf"** opens Apple Mail with a new *visible* message addressed to Mom with the file attached — Jarvis **never presses Send**; you check it and send it yourself. If Mail won't cooperate, he opens a Gmail compose + shows you the file in Finder instead, and says so honestly. **"Copy report to downloads"** copies a file to Downloads / Documents / Desktop / Pictures (a copy needs no confirmation; moves and trash still do).

**How the speed works now:** Groq Whisper ears (fast transcription) + a fast model for everyday chat + VAD recording that stops when you stop talking + two-to-four-sentence spoken answers. The slow parts of v3 — free Google recognizer latency, a reasoning model answering small talk, long spoken monologues — are gone.

**v3 is "Full Voice Control":** Jarvis doesn't just open apps any more — he opens any file or folder by name, messages and calls your contacts, types and presses keys for you, controls music, creates real Reminders and Calendar events, manages files (with your confirmation), and — just as importantly — he **talks with you**: a friendly conversational companion who explains things, gives honest opinions when you ask, and speaks every reply in full. See "Talking with Jarvis" and the v3 command table below.

---

## v3.1 — speed + accuracy fix (from Umesh's first live run)

His first voice run worked, but every sentence was slow: the offline Whisper load was failing on his Mac with a huggingface-hub error (`open() got an unexpected keyword argument 'metadata_errors'`) and v3.0 retried that slow failing load after **every** sentence before falling back to Google. v3.1 fixes this three ways:

| Fix | What changed |
|---|---|
| **Fail-once latch** | If the Whisper load fails once, Jarvis remembers it for the session and goes straight to the fast Google recognizer — no retry, no error spam |
| **Xet disabled** | `HF_HUB_DISABLE_XET=1` is set before loading Whisper (the Xet download backend is what produced the `metadata_errors` crash), giving Whisper a clean classic-download path on the next fresh start |
| **Silence detection** | Recording no longer runs a fixed 6 seconds: it stops ~1 second after you stop speaking, so replies start much sooner |
| **Accent accuracy** | Google recognition now uses `stt_language` (default **en-IN**, better for Indian-English speech); change it in `~/.openjarvis/config.json` |

To update an installed copy: stop Jarvis with **Ctrl+C**, then `git pull`, then `./jarvis` again. No reinstall needed. To STOP Jarvis in voice mode at any time: press **Ctrl+C** (stops Jarvis entirely), or say **"Jarvis, goodbye"** (typing `quit` does not work in voice mode — he isn't reading the keyboard while listening). To stop only his *speech* mid-sentence (v5), say **"stop"** / **"Jarvis, stop"** — he cuts off and keeps listening.

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

### Speech engines (v4.1) + offline voice (faster-whisper)

By default (`stt_engine: "auto"` in the config) Jarvis's ears run in this order: **Groq Whisper** (online, using your Groq key — the full `whisper-large-v3` model plus your `stt_vocab_prompt` vocabulary hint; fastest, best with Indian-English) → **faster-whisper locally** (if installed — your voice never leaves the Mac) → **Google's online recognizer**. A Groq ears error falls through in the same listen and rests Groq for 60 seconds. Set `stt_engine` to `"groq"`, `"whisper"` or `"google"` to force one engine.

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
- `stt_engine` — "auto" / "groq" / "google" / "whisper" · `whisper_model` — "base.en" · `suggestions` — true
- **v4 keys:** `groq_stt_model` — "whisper-large-v3" (v4.1; was the turbo variant) · `stt_vocab_prompt` — words Whisper should expect (your name, your field, your apps) · `fast_model` — "openai/gpt-oss-20b" (chat tier; `groq_models` is the deep tier; a saved "llama-3.1-8b-instant" auto-migrates to this) · `personal_context` — short bio the brain uses to talk about your situation · `hud_port` — 8765 · `hud_auto_open` — true · `code_dir` — "~/jarvis-projects"
- **v4.1 voice keys:** `tts_engine` — "auto" / "groq" / "openai" / "say" · `groq_tts_model` — "canopylabs/orpheus-v1-english" · `tts_voice` — "daniel" (male: daniel/austin/troy; female: hannah/autumn/diana) · `openai_tts_model` — "gpt-4o-mini-tts" · `openai_tts_voice` — "nova"

Files Jarvis keeps in `~/.openjarvis/`: `config.json`, `contacts.json`, `free.key` (Groq), `openai.key` (optional), `history.json`, `notes.txt`, and optionally `mail_cache.txt` (see below).

## Safety

- **No sudo** anywhere — installer or actions.
- **Keys stay local**: stored only in `~/.openjarvis/*.key` with `chmod 600`, or in environment variables. Never in this repo.
- **Confirmations**: sending any message (iMessage, WhatsApp), moving a file, trashing a file, emptying the trash, restarting, shutting down and logging out never run without an explicit "yes".
- Local actions run on your Mac only; the only network calls are the LLM provider you configured, Google's speech recognizer (below), and Yahoo Finance for market data.

## Honest limits

- **No real Gmail reading.** Jarvis cannot access your Gmail account. "Read my emails" says exactly that and opens Gmail for you. The only exception: if *you* save a briefing text at `~/.openjarvis/mail_cache.txt`, Jarvis will read that file back to you — it still never fetches mail itself. (The original version invented a fake inbox summary; v2 removes that.)
- **Market signals are simple moving averages** over Yahoo Finance data — a rough technical indicator, **not financial advice**.
- **Speech recognition (v4)**: in `auto` mode your voice audio for each command is sent to **Groq's Whisper API** for transcription (fast, best accent handling). If Groq's ears fail, Jarvis falls back to **faster-whisper locally** (fully local after the one-time model download, if installed) and then to **Google's free recognizer** (audio sent to Google, exactly like the original). Set `stt_engine: "whisper"` to keep transcription fully on the Mac. Speech *output* (v4.1) is the neural human voice while online (your reply text is sent to Groq's Orpheus TTS, or OpenAI TTS if configured) and macOS `say` when offline or if the neural voice fails — see "Human voice" above.
- **Neural voice availability depends on your Groq account.** If Orpheus (`canopylabs/orpheus-v1-english`) is not enabled on your Groq key, the first reply will fall back to the Mac voice and the terminal will show one "[Human voice not available…]" line. The fix is then to point `groq_tts_model` in `~/.openjarvis/config.json` at a speech model your account does have (check console.groq.com → Models), or set `tts_engine: "say"` to stop trying.
- **First-use "Allow" prompts are Apple's, not Jarvis's**: the first time Jarvis touches Messages, Mail, Calendar/Reminders, or Accessibility features, macOS will pop an "Allow" prompt. Click Allow — Jarvis cannot click it for you, and until you do, that one feature stays blocked.
- **WhatsApp still can't silently attach or send arbitrary files** — it opens the prepared chat (and presses Enter only with a known number + Accessibility + your "yes"). Email attachments go through **Apple Mail, visibly**, and are never auto-sent.
- **Online brain + ears need internet.** Offline, Jarvis still runs all local commands, and transcription can stay local with faster-whisper — but open-ended thinking, web search/reading, and Groq ears all need a connection, and he says so instead of pretending.
- **Voice coding is confirmed-first and sandboxed**: generated code is only written into `~/jarvis-projects`, runs only after your "yes", with a 60-second timeout — review code you didn't write before running it, like anywhere else.
- **He is not literally a supercomputer.** Jarvis is a fast voice agent on your M1: the "supercomputer" feel comes from Groq's cloud models doing the heavy thinking. Heavy coding answers still depend on the online model's quality and the free tier's rate limits.
- Voice quality, mic pickup, and `say` voices depend on your Mac and macOS permissions — grant the Microphone permission above.
- **WhatsApp sending** needs three things at once: the person's number in `contacts.json`, Accessibility permission for Terminal, and your "yes" — only then does Jarvis press Enter for you. Without a number, it opens WhatsApp Web prefilled and says so. **Email never auto-sends**: Jarvis opens a prefilled Gmail compose and you press Send.
- **Do Not Disturb** needs a one-time Shortcut on your Mac named `Toggle Focus` (Shortcuts app → New Shortcut → action "Set Focus" toggled → name it `Toggle Focus`). Without it, Jarvis says exactly that and changes nothing.
- **Calls** use FaceTime on the Mac (there is no cellular calling) — they need the contact's number or email.
- **Offline thinking:** Jarvis's opinions and open-ended explanations need the online brain (Groq / OpenAI). Offline he runs commands and local facts, and says so for anything else — he doesn't fake thinking offline.
- **Reminders / Calendar parsing** is simple by design (today / tomorrow + `6 pm` / `18:30`); anything fancier loses the due date rather than being mis-dated.
- **Barge-in (v5) is best-effort hearing, not magic.** The stop ear listens while Jarvis talks, but in a noisy room — or with the speakers loud — he may miss a quiet "stop", and the echo guard (which stops him obeying his *own* voice) may rarely swallow a real one. Shout it once, plainly: "Jarvis, stop." If the mic can't open alongside playback on some setup, the stop ear disables itself silently for the session and everything else keeps working.
- **The butler (v5) only sees what's on this Mac**: Apple Reminders, Apple Calendar, and the battery — plus one morning web count for the job suggestion. He can't see Google Calendar, your phone, or your email, and alerts wait for a gap between listen cycles, so one can land a few seconds after it triggers. Nothing is ever repeated the same day.
- **"Click <name>" (v5) is best-effort** and needs Accessibility permission for Terminal (System Settings → Privacy & Security → Accessibility). It clicks the first visible UI element whose name or title matches in the front app's front window; hidden menus and canvas-drawn apps (games, some Electron screens) may not expose elements at all — then he says he couldn't, and clicks nothing.
- **Reading PDFs aloud (v5) needs poppler** (`brew install poppler` provides `pdftotext`). Without it Jarvis opens PDFs but won't pretend to read them. Scanned-image PDFs have no text layer even with poppler — he'll report no readable text rather than inventing content.

## Tests

```bash
python3 tests/test_actions.py     # actions router (stubs all system helpers; nothing opens, no network) + v4.1 do-the-work routing
python3 tests/test_voice.py       # STT engines: Groq-ears order/latch + whisper latch + v4.1 vocab prompt, text chunking, neural-TTS order/latch (all stubbed)
python3 tests/test_brain_tiers.py # v4 fast/deep model routing + prompt + v4.1 dead-model migration/latch (stubbed client)
python3 tests/test_hud.py         # v4 HUD server on an ephemeral port (localhost only)
python3 tests/test_web.py         # v4 web search/read + v4.1 job search & topic cleaning (HTTP layer stubbed)
python3 tests/test_code.py        # v4 voice coding in a temp sandbox (LLM stubbed)
python3 tests/test_tasks.py       # v4 background-task manager + announcer
python3 tests/test_read.py        # v5 read-anything: txt/docx extraction, pdf honesty, read-more paging, routes (stubbed search/brain)
python3 tests/test_butler.py      # v5 proactive butler: alerts once/day, suggestion yes/no/expiry (injected providers + clock)
```
