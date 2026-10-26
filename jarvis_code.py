"""
jarvis_code.py - Jarvis v4 voice coding.

The sandbox is the code folder (config code_dir, default ~/jarvis-projects).
Jarvis can:

- CREATE: "create a python file called hello that prints the date" -
  the deep-tier LLM writes the code, Jarvis saves <name>.py into the
  sandbox and remembers it (~/.openjarvis/code_state.json).
- OPEN: "open it" - opens the last file in VS Code if installed, else in
  the default text editor.
- RUN: "run it" - only after a spoken "yes" (the confirm callback). The
  run happens in a BACKGROUND TASK (jarvis_tasks): system python3, this
  file only, 60-second timeout, stdout/stderr captured. When it ends,
  Jarvis speaks the outcome - success plus the last output lines, or the
  error's last line plus "say fix it".
- FIX: "fix it" - the last code + last error go back to the deep tier;
  the file is overwritten with the corrected code (the old version kept
  as <name>.py.bak) and Jarvis speaks a one-line summary of the change.
  A fixed file is NEVER auto-run - running it needs a fresh "run it".

All subprocess/open calls are module-level helpers (_run_python, _open)
so tests can stub them; nothing here uses a shell.
"""

import json
import os
import re
import subprocess
import sys

import jarvis_config
import jarvis_tasks

RUN_TIMEOUT = 60

_CODE_SYSTEM = ("You are a careful Python programmer. Return ONLY the code "
                "in a single ```python block - no explanations before or "
                "after it. The code must be a complete, runnable Python 3 "
                "script using only the standard library unless the task "
                "clearly needs a common package.")


def code_dir():
    cfg = jarvis_config.load_config()
    path = os.path.expanduser(str(cfg.get("code_dir", "~/jarvis-projects")))
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _state_file():
    return os.path.expanduser("~/.openjarvis/code_state.json")


def _load_state():
    try:
        path = _state_file()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_state(state):
    try:
        d = os.path.expanduser("~/.openjarvis")
        os.makedirs(d, exist_ok=True)
        with open(_state_file(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def last_file():
    path = _load_state().get("last_file")
    return path if path and os.path.exists(path) else None


def _llm_code(prompt_text, system=_CODE_SYSTEM):
    """Deep-tier one-shot completion; None when the brain is unavailable."""
    try:
        import jarvis_brain
        return jarvis_brain.complete(prompt_text, system=system, deep=True, max_tokens=1500)
    except Exception:
        return None


def _extract_code(reply):
    if not reply:
        return None
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply, re.DOTALL)
    code = (m.group(1) if m else reply).strip()
    return code or None


def _safe_name(name):
    base = re.sub(r"[^A-Za-z0-9_]+", "_", (name or "script").strip()).strip("_").lower()
    return base or "script"


def create_code(name, task):
    """Generate code for `task` with the deep tier and save <name>.py."""
    task = (task or "").strip()
    if not task:
        return "Tell me what the code should do - for example, 'create a python file called timer that counts down from ten'."
    reply = _llm_code(f"Write a Python script that does this: {task}")
    code = _extract_code(reply)
    if not code:
        return ("I couldn't write that code just now - my thinking brain didn't "
                "answer. That usually means we're offline or the model is busy. "
                "Nothing was created.")
    fname = _safe_name(name) + ".py"
    path = os.path.join(code_dir(), fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(code + "\n")
    except Exception as e:
        return f"I wrote the code in my head but couldn't save it to {fname}: {e}"
    state = _load_state()
    state.update({"last_file": path, "last_task": task, "last_error": ""})
    _save_state(state)
    return (f"Created {fname} in your jarvis-projects folder. "
            f"Say 'open it' to look at it, or 'run it' and I'll run it for you.")


def _open(args):
    try:
        return subprocess.run(["open"] + list(args), capture_output=True).returncode == 0
    except Exception:
        return False


def open_last_code():
    path = last_file()
    if not path:
        return ("You haven't asked me to create any code yet in jarvis-projects - "
                "say 'create a python file called hello that prints hello' first.")
    name = os.path.basename(path)
    # VS Code if it is installed, else the default text editor.
    apps_dir = "/Applications"
    if os.path.isdir(os.path.join(apps_dir, "Visual Studio Code.app")):
        _open(["-a", "Visual Studio Code", path])
        return f"Opening {name} in Visual Studio Code."
    _open(["-t", path])
    return f"Opening {name} in your text editor."


def _run_python(path, timeout=RUN_TIMEOUT):
    """Run a file with the system python3. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=timeout, cwd=os.path.dirname(path) or None)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"Timed out after {timeout} seconds and was stopped."
    except Exception as e:
        return 1, "", str(e)


def run_file_sync(path):
    """Run now (blocking) and return the spoken outcome. Used by the task job."""
    rc, out, err = _run_python(path)
    name = os.path.basename(path)
    state = _load_state()
    if rc == 0:
        state["last_error"] = ""
        _save_state(state)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if lines:
            tail = " ".join(lines[-2:])
            return f"{name} ran successfully. Its last output was: {tail}"
        return f"{name} ran successfully, with no output."
    err_lines = [ln.strip() for ln in (err or "").splitlines() if ln.strip()]
    last_err = err_lines[-1] if err_lines else f"exit code {rc}"
    state["last_error"] = err or last_err
    state["last_error_file"] = path
    _save_state(state)
    return (f"{name} hit an error: {last_err}. "
            f"Say 'fix it' and I'll try to repair the code.")


def run_last_code(confirm=None):
    """Confirm first, then run the last created file as a background task."""
    path = last_file()
    if not path:
        return ("There's no code file to run yet - say 'create a python file "
                "called hello that prints hello' first.")
    name = os.path.basename(path)
    if confirm is None:
        return (f"Running code needs your confirmation, and I can't ask you right "
                f"now - so I did not run {name}.")
    try:
        ok = bool(confirm(f"Run {name} now? Say yes to confirm."))
    except Exception:
        ok = False
    if not ok:
        return f"Okay, I did not run {name}."
    jarvis_tasks.start(f"Running {name}", lambda: run_file_sync(path))
    return (f"Starting {name} now in the background. Keep talking to me - "
            f"I'll speak up with the result when it's done.")


def fix_last_code():
    """Send last code + last error to the deep tier; overwrite with a fix."""
    state = _load_state()
    path = state.get("last_error_file") or state.get("last_file")
    if not path or not os.path.exists(path):
        return "There's no code file for me to fix yet."
    error = (state.get("last_error") or "").strip()
    if not error:
        return (f"{os.path.basename(path)} hasn't reported an error - if something "
                f"looks wrong, tell me what it should do differently.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            old_code = f.read()
    except Exception as e:
        return f"I couldn't read {os.path.basename(path)} to fix it: {e}"
    reply = _llm_code(
        "Here is a Python script:\n\n" + old_code +
        "\n\nRunning it produced this error:\n\n" + error[-2000:] +
        "\n\nReturn the full corrected script.",
    )
    code = _extract_code(reply)
    if not code:
        return ("I couldn't get a fix from my thinking brain just now - it may be "
                "offline or busy. The file is unchanged.")
    try:
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write(old_code)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code + "\n")
    except Exception as e:
        return f"I got a fix but couldn't save it: {e}"
    state["last_error"] = ""
    _save_state(state)
    summary = _llm_code(
        f"In ONE short spoken sentence, what was wrong and what changed? "
        f"Old error: {error.splitlines()[-1] if error.splitlines() else error}. "
        f"New code:\n{code[:1500]}",
        system="Answer with one short plain sentence, no markdown.",
    )
    name = os.path.basename(path)
    if summary:
        return (f"I fixed {name}: {summary.strip()} The old version is saved as "
                f"{name}.bak. Say 'run it' when you want to try again - I won't "
                f"run it on my own.")
    return (f"I rewrote {name} with a fix, and kept the old version as {name}.bak. "
            f"Say 'run it' when you want to try again - I won't run it on my own.")
