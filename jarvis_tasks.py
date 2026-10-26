"""
jarvis_tasks.py - Jarvis v4 background task manager.

Long jobs (running code Jarvis wrote, timers, ...) run in daemon threads
so the listen loop keeps listening - Umesh can move on to the next thing
while Jarvis works, and Jarvis ANNOUNCES progress out loud: when a task
starts, finishes, or fails, the registered announcer is called with a
short spoken line.

The announcer is registered by the voice/text loops (jarvis_voice) with
a speak()-based callback; before that (or in --once mode) announcements
are simply printed. Announcements are delivered from the task thread,
i.e. between Jarvis's own listen/speak cycles - they never talk over
the user mid-sentence by design (see README).

No imports beyond the standard library: this module must never create
an import cycle (actions -> tasks, voice -> tasks, code -> tasks).
"""

import threading
import time

_lock = threading.Lock()
_tasks = {}          # id -> {name, status, started, finished, result, error}
_next_id = 1
_announcer = None    # callable(str) or None


def set_announcer(fn):
    """Register the callback that speaks/prints task updates."""
    global _announcer
    _announcer = fn


def _announce(text):
    fn = _announcer
    if fn is None:
        print(f"[TASK] {text}")
        return
    try:
        fn(text)
    except Exception:
        # An announcer problem must never kill a task thread.
        try:
            print(f"[TASK] {text}")
        except Exception:
            pass


def start(name, fn):
    """
    Run fn() in a daemon thread. fn returns a short spoken result string
    (announced on completion) or None (a generic "finished" line is
    announced). Exceptions are captured and announced as failures.
    Returns the task id.
    """
    global _next_id
    with _lock:
        task_id = _next_id
        _next_id += 1
        _tasks[task_id] = {
            "name": str(name),
            "status": "running",
            "started": time.time(),
            "finished": None,
            "result": None,
            "error": None,
        }

    def _job():
        try:
            result = fn()
            with _lock:
                entry = _tasks.get(task_id)
                if entry is not None:
                    entry["status"] = "done"
                    entry["finished"] = time.time()
                    entry["result"] = result
            _announce(result if result else f"Finished: {name}.")
        except Exception as e:  # task code must never crash the process
            with _lock:
                entry = _tasks.get(task_id)
                if entry is not None:
                    entry["status"] = "failed"
                    entry["finished"] = time.time()
                    entry["error"] = str(e)
            _announce(f"{name} failed: {e}")

    t = threading.Thread(target=_job, name=f"jarvis-task-{task_id}", daemon=True)
    t.start()
    _announce(f"Started: {name}. I'll tell you when it's done - ask me anything else meanwhile.")
    return task_id


def list_tasks():
    """Snapshot of all tasks, oldest first."""
    with _lock:
        return [dict(v, id=k) for k, v in sorted(_tasks.items())]


def running():
    return [t for t in list_tasks() if t["status"] == "running"]


def status_text():
    """A spoken summary for 'what are you doing / task status'."""
    tasks = list_tasks()
    if not tasks:
        return "I'm not running any background tasks right now - I'm all yours."
    parts = []
    run = [t for t in tasks if t["status"] == "running"]
    done = [t for t in tasks if t["status"] != "running"]
    if run:
        names = ", ".join(t["name"] for t in run)
        parts.append(f"Right now I'm working on: {names}. I'll speak up as soon as each one finishes.")
    if done:
        last = done[-1]
        if last["status"] == "done":
            outcome = last.get("result") or "finished fine"
            parts.append(f"My last finished task was {last['name']}: {outcome}")
        else:
            parts.append(f"My last task, {last['name']}, failed: {last.get('error') or 'unknown error'}.")
    return " ".join(parts)


def wait_all(timeout=10.0):
    """Block until no task is running (used by tests and shutdown)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not running():
            return True
        time.sleep(0.05)
    return not running()


def _reset_for_tests():
    global _next_id
    with _lock:
        _tasks.clear()
        _next_id = 1
