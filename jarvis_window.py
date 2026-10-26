"""
jarvis_window.py - Jarvis v7 "One Icon": his own native pop-up window.

Owner's demand (live, v6 run): "I don't want to use the terminal... any
pop up should come out and speak like a man." So since v7 Jarvis has a
face of his own: a small floating window (dark panel, always on top)
that shows his state, the last thing he heard and said, and three
buttons - Stop, "What did I miss", Quit. It replaces the browser HUD
as the everyday face (the HUD still exists, but opens only when he
explicitly says "show yourself").

Tkinter is import-guarded: on a machine (or headless VM) where tkinter
or a display is missing, every function here is a safe no-op and Jarvis
runs voice-only exactly as before. All cross-thread UI updates travel
through a queue pumped by Tk's after() loop - worker threads only ever
touch the thread-safe state model below, never Tk widgets directly.

Threading model: Tk wants the main thread, so `jarvis.py --window`
runs this module's run_window() in the main thread and the voice loop
on a worker thread. Quit works both ways: the window's Quit button sets
the quit event the loops poll between cycles, and the loops finishing
first is equally fine (the process simply ends).
"""

import queue
import threading

try:
    import tkinter as tk
    _TK_AVAILABLE = True
except Exception:  # no tkinter / no display stack at all
    tk = None
    _TK_AVAILABLE = False

# ---------------------------------------------------------------------------
# Thread-safe state model (this is what tests exercise headlessly)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_model = {"state": "idle", "heard": "", "spoken": "", "alert": None}
_cmd_queue = queue.Queue()       # window buttons -> the voice loops
_ui_queue = queue.Queue()        # any thread -> the Tk pump
_quit_event = threading.Event()
_active = False                  # True while the window is on screen
_quit_callback = None            # optional extra hook, set by jarvis.py


def available():
    """True when tkinter imported - the window MIGHT be creatable."""
    return _TK_AVAILABLE


def is_active():
    return _active


def get_state():
    with _lock:
        return dict(_model)


def set_state(state=None, heard=None, spoken=None):
    """Update the model; the Tk pump picks changes up within ~100ms."""
    with _lock:
        if state is not None:
            _model["state"] = str(state)
        if heard is not None:
            _model["heard"] = str(heard)
        if spoken is not None:
            _model["spoken"] = str(spoken)
        snap = dict(_model)
    _ui_queue.put(("state", snap))


def show_alert(text):
    """
    Raise the window for an announcement (butler / notification alerts
    call this at the same moment they are spoken). Guarded no-op when
    no window exists - the model still records the alert.
    """
    text = str(text or "")
    if not text:
        return
    with _lock:
        _model["alert"] = text
        _model["state"] = "alert"
    _ui_queue.put(("alert", text))


def queue_command(text):
    """A button (or test) injects a command for the voice loops."""
    if text:
        _cmd_queue.put(str(text))


def poll_commands():
    """Drain injected commands (called by the loops between cycles)."""
    out = []
    while True:
        try:
            out.append(_cmd_queue.get_nowait())
        except queue.Empty:
            break
    return out


def set_quit_callback(fn):
    global _quit_callback
    _quit_callback = fn


def request_quit():
    """Ask Jarvis to shut down (window Quit button / external caller)."""
    if _quit_event.is_set():
        return
    _quit_event.set()
    cb = _quit_callback
    if cb is not None:
        try:
            cb()
        except Exception:
            pass
    _ui_queue.put(("quit", None))


def quit_requested():
    return _quit_event.is_set()


def _reset_for_tests():
    global _active
    _quit_event.clear()
    with _lock:
        _model.update({"state": "idle", "heard": "", "spoken": "", "alert": None})
    poll_commands()
    while True:
        try:
            _ui_queue.get_nowait()
        except queue.Empty:
            break
    _active = False


# ---------------------------------------------------------------------------
# The Tk window itself (main thread only)
# ---------------------------------------------------------------------------

_STATE_COLORS = {
    "idle": "#8a97a5", "listening": "#2ee6a8", "thinking": "#f5c542",
    "speaking": "#35d3f4", "working": "#b78cff", "alert": "#ff5c5c",
    "stopped": "#8a97a5",
}
_BG = "#0b0f14"
_PANEL = "#111927"
_FG = "#d7e3ee"


def _on_stop_clicked():
    """The window's Stop button: cut his voice, keep him running."""
    try:
        import jarvis_voice
        jarvis_voice.stop_speaking()
    except Exception:
        pass
    set_state(state="listening")


def run_window():
    """
    Build the floating window and run Tk's mainloop (call from the
    MAIN thread - see jarvis.py --window). Returns True when the window
    actually ran; False (with the caller falling back to plain voice
    mode) when tkinter/a display is unavailable.
    """
    global _active
    if not _TK_AVAILABLE:
        return False
    try:
        root = tk.Tk()
    except Exception:
        return False

    widgets = {}
    try:
        root.title("JARVIS")
        root.geometry("380x300")
        root.configure(bg=_BG)
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        header = tk.Frame(root, bg=_BG)
        header.pack(fill="x", padx=12, pady=(10, 4))
        dot = tk.Label(header, text="●", bg=_BG, fg=_STATE_COLORS["idle"],
                       font=("Helvetica", 16))
        dot.pack(side="left")
        state_lbl = tk.Label(header, text="JARVIS", bg=_BG, fg=_FG,
                             font=("Helvetica", 15, "bold"))
        state_lbl.pack(side="left", padx=(6, 0))
        widgets["dot"] = dot
        widgets["state"] = state_lbl

        transcript = tk.Text(root, bg=_PANEL, fg=_FG, wrap="word",
                             font=("Helvetica", 11), relief="flat",
                             padx=8, pady=8, height=10, state="disabled")
        transcript.pack(fill="both", expand=True, padx=12, pady=6)
        widgets["transcript"] = transcript

        buttons = tk.Frame(root, bg=_BG)
        buttons.pack(fill="x", padx=12, pady=(2, 10))

        def _append(line):
            try:
                transcript.configure(state="normal")
                transcript.insert("end", line + "\n")
                transcript.see("end")
                transcript.configure(state="disabled")
            except Exception:
                pass

        def _destroy():
            try:
                root.destroy()
            except Exception:
                pass

        def _on_quit():
            request_quit()
            _destroy()

        def _on_missed():
            queue_command("what did I miss")

        tk.Button(buttons, text="Stop", command=_on_stop_clicked,
                  width=8).pack(side="left")
        tk.Button(buttons, text="What did I miss", command=_on_missed,
                  width=15).pack(side="left", padx=6)
        tk.Button(buttons, text="Quit", command=_on_quit,
                  width=8).pack(side="right")
        root.protocol("WM_DELETE_WINDOW", _on_quit)

        def _apply(snap):
            st = str(snap.get("state", "idle")).lower()
            try:
                widgets["dot"].configure(fg=_STATE_COLORS.get(st, "#8a97a5"))
                widgets["state"].configure(
                    text="JARVIS — " + ("ALERT" if st == "alert" else st.upper()))
            except Exception:
                pass

        def _pump():
            try:
                while True:
                    kind, payload = _ui_queue.get_nowait()
                    if kind == "state":
                        _apply(payload)
                        if payload.get("heard"):
                            _append("You: " + payload["heard"])
                        if payload.get("spoken"):
                            _append("Jarvis: " + payload["spoken"])
                    elif kind == "alert":
                        _apply({"state": "alert"})
                        _append("— " + str(payload))
                        for fn in ("deiconify", "lift", "focus_force"):
                            try:
                                getattr(root, fn)()
                            except Exception:
                                pass
                    elif kind == "quit":
                        _destroy()
                        return
            except queue.Empty:
                pass
            try:
                root.after(100, _pump)
            except Exception:
                pass

        _active = True
        _apply(get_state())
        _append("Jarvis is here. Talk to me, or use the buttons.")
        root.after(100, _pump)
        root.mainloop()
    except Exception:
        return False
    finally:
        _active = False
    return True


if __name__ == "__main__":
    print("tkinter available:", available())
    if available():
        set_state(state="listening", heard="open github",
                  spoken="Opening GitHub.")
        run_window()
