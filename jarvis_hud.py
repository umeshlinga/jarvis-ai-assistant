"""
jarvis_hud.py - Jarvis v4 live visual HUD.

A tiny standard-library-only HTTP server (localhost, 127.0.0.1 only) that
serves hud.html and streams what Jarvis is doing over Server-Sent Events
(SSE): his state (listening / thinking / speaking / working / idle), the
live transcript ("You said" / Jarvis replies), every action he runs, and
background-task updates. Umesh asked to SEE Jarvis working - this is
that window.

Usage:
    server = jarvis_hud.start_server(port)   # None if the port is taken
    jarvis_hud.emit("heard", "open youtube")
    jarvis_hud.emit("state", "listening")
    jarvis_hud.stop_server()

emit() is thread-safe and safe to call before the server starts (events
are kept in a backlog of the last 100 and replayed to new connections).
No external assets: hud.html is self-contained, so the HUD also works
with no internet connection.
"""

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_events = []            # backlog: list of dicts {ts, kind, text}
_subscribers = set()    # queue.Queue per connected SSE client
_server = None
_server_thread = None

MAX_BACKLOG = 100


def emit(kind, text):
    """Record + broadcast one HUD event. Never raises."""
    ev = {"ts": time.time(), "kind": str(kind), "text": str(text)}
    try:
        with _lock:
            _events.append(ev)
            del _events[:-MAX_BACKLOG]
            subs = list(_subscribers)
        for q in subs:
            try:
                q.put_nowait(ev)
            except Exception:
                pass
    except Exception:
        pass
    return ev


def recent_events():
    with _lock:
        return list(_events)


def is_running():
    return _server is not None


def hud_url(port=None):
    if port is None:
        try:
            import jarvis_config
            port = int(jarvis_config.load_config().get("hud_port", 8765))
        except Exception:
            port = 8765
    return f"http://127.0.0.1:{port}/"


def _page_html():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ("<!DOCTYPE html><title>JARVIS v4</title>"
                "<h1>JARVIS v4</h1><p>HUD page file (hud.html) missing.</p>")


class _Handler(BaseHTTPRequestHandler):
    server_version = "JarvisHUD/1.0"

    def log_message(self, *args):  # keep the console clean
        pass

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                body = _page_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/events":
                self._events_stream()
            else:
                self.send_response(404)
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass

    def _events_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = queue.Queue()
        with _lock:
            _subscribers.add(q)
            backlog = list(_events)
        try:
            self.wfile.write(b": jarvis hud connected\n\n")
            self.wfile.flush()
            try:
                owner = "Umesh"
                import jarvis_config
                owner = str(jarvis_config.load_config().get("owner_name", "Umesh"))
            except Exception:
                pass
            hello = {"ts": time.time(), "kind": "hello", "text": owner}
            self.wfile.write(f"data: {json.dumps(hello)}\n\n".encode("utf-8"))
            for ev in backlog:
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while getattr(self.server, "_jarvis_running", True):
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Heartbeat comment keeps the connection (and the
                    # browser's auto-reconnect) healthy.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _lock:
                _subscribers.discard(q)


def start_server(port=None):
    """
    Start the HUD server in a daemon thread. Returns the HTTPServer, or
    None if the port is unavailable (Jarvis continues without the HUD).
    """
    global _server, _server_thread
    if _server is not None:
        return _server
    if port is None:
        try:
            import jarvis_config
            port = int(jarvis_config.load_config().get("hud_port", 8765))
        except Exception:
            port = 8765
    try:
        server = ThreadingHTTPServer(("127.0.0.1", int(port)), _Handler)
    except Exception as e:
        print(f"[HUD disabled: could not bind 127.0.0.1:{port} ({e})]")
        return None
    server.daemon_threads = True
    server._jarvis_running = True
    thread = threading.Thread(target=server.serve_forever, name="jarvis-hud", daemon=True)
    thread.start()
    _server = server
    _server_thread = thread
    return server


def stop_server():
    global _server, _server_thread
    server = _server
    _server = None
    _server_thread = None
    if server is not None:
        try:
            server._jarvis_running = False
            server.shutdown()
            server.server_close()
        except Exception:
            pass


def open_hud():
    """Open the HUD page in the default browser. Guarded; returns bool."""
    try:
        import webbrowser
        return bool(webbrowser.open(hud_url()))
    except Exception:
        return False
