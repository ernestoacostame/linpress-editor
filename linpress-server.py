#!/usr/bin/env python3
"""
LinPress — Servidor local  v2
==============================
Escucha en http://127.0.0.1:40821

Endpoints de archivos:
  GET  /ping
  GET  /read?file=<ruta>
  POST /write       { "file": "...", "data": {...} }
  POST /delete      { "file": "..." }
  GET  /list?dir=<borradores|credenciales>

Proxy WordPress (resuelve CORS completamente):
  POST /wp-proxy    { "url": "https://...", "method": "GET|POST|...",
                      "headers": {...}, "body": "..." }

Parada limpia:
  Ctrl+C cierra en menos de 1 segundo sin necesidad de matar el proceso.
"""

import http.server
import json
import pathlib
import sys
import urllib.parse
import urllib.request
import urllib.error
import threading
import webbrowser
import signal
import time

# ── Directorios ──────────────────────────────────────────────────────
CONFIG_DIR = pathlib.Path.home() / ".config" / "linpress"
DRAFTS_DIR = CONFIG_DIR / "borradores"
CREDS_DIR  = CONFIG_DIR / "credenciales"
PORT       = 40821
HTML_FILE  = pathlib.Path(__file__).parent / "linpress.html"

for d in (DRAFTS_DIR, CREDS_DIR):
    d.mkdir(parents=True, exist_ok=True)

ALLOWED_DIRS = {"borradores": DRAFTS_DIR, "credenciales": CREDS_DIR}


def resolve_path(rel: str):
    parts = pathlib.PurePosixPath(rel).parts
    if len(parts) < 2:
        return None
    base = ALLOWED_DIRS.get(parts[0])
    if not base:
        return None
    safe = (base / parts[1]).resolve()
    if not str(safe).startswith(str(base.resolve())):
        return None
    return safe


CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


class LinPressHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silenciar logs de acceso

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS.items():
            self.send_header(k, v)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def err(self, msg, status=400):
        self.send_json({"ok": False, "error": msg}, status)

    def read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        route  = parsed.path

        if route == "/ping":
            return self.send_json({"ok": True, "version": "2.0", "app": "LinPress"})

        if route == "/read":
            fpath = resolve_path(params.get("file", ""))
            if not fpath:
                return self.err("Ruta invalida")
            if not fpath.exists():
                return self.send_json({"ok": False, "exists": False})
            try:
                self.send_json({"ok": True, "exists": True,
                                "data": json.loads(fpath.read_text("utf-8"))})
            except Exception as e:
                self.err(f"Error leyendo: {e}")
            return

        if route == "/list":
            base = ALLOWED_DIRS.get(params.get("dir", ""))
            if not base:
                return self.err("Directorio invalido")
            files = sorted(base.glob("*.json"), key=lambda f: -f.stat().st_mtime)
            self.send_json({"ok": True, "files": [
                {"name": f.name, "stem": f.stem,
                 "modified": f.stat().st_mtime, "size": f.stat().st_size}
                for f in files
            ]})
            return

        self.err("Endpoint no encontrado", 404)

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        raw   = self.read_body()

        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            return self.err(f"JSON invalido: {e}")

        # ── Archivos ─────────────────────────────────────────────────
        if route == "/write":
            fpath = resolve_path(payload.get("file", ""))
            data  = payload.get("data")
            if fpath is None or data is None:
                return self.err("Parametros invalidos")
            try:
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
                self.send_json({"ok": True, "file": str(fpath)})
            except Exception as e:
                self.err(f"Error escribiendo: {e}")
            return

        if route == "/delete":
            fpath = resolve_path(payload.get("file", ""))
            if not fpath:
                return self.err("Ruta invalida")
            if fpath.exists():
                fpath.unlink()
            self.send_json({"ok": True})
            return

        # ── Proxy WordPress ───────────────────────────────────────────
        # El navegador no puede llamar directamente a la API de WordPress
        # desde file:// por restricciones CORS. Este endpoint reenvía la
        # peticion desde Python (sin restricciones) y devuelve el resultado.
        if route == "/wp-proxy":
            target_url = payload.get("url", "").strip()
            method     = payload.get("method", "GET").upper()
            fwd_hdrs   = payload.get("headers", {})
            body_str   = payload.get("body", "")

            if not target_url.startswith("http"):
                return self.err("URL invalida")

            body_bytes = body_str.encode("utf-8") if body_str else None
            req = urllib.request.Request(
                url=target_url, data=body_bytes, method=method)
            for hk, hv in fwd_hdrs.items():
                req.add_header(hk, hv)
            if body_bytes:
                req.add_header("Content-Type", "application/json")

            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    resp_body       = resp.read()
                    resp_status     = resp.status
                    wp_total        = resp.headers.get("X-WP-Total", "")
                    wp_total_pages  = resp.headers.get("X-WP-TotalPages", "")

                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_body)))
                for k, v in CORS.items():
                    self.send_header(k, v)
                self.send_header("X-Proxy-Status",    str(resp_status))
                self.send_header("X-WP-Total",        wp_total)
                self.send_header("X-WP-TotalPages",   wp_total_pages)
                self.end_headers()
                self.wfile.write(resp_body)

            except urllib.error.HTTPError as e:
                err_body = e.read()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                for k, v in CORS.items():
                    self.send_header(k, v)
                self.send_header("X-Proxy-Status", str(e.code))
                self.end_headers()
                try:
                    err_json = json.loads(err_body)
                except Exception:
                    err_json = {"message": err_body.decode("utf-8",
                                                           errors="replace")}
                self.wfile.write(json.dumps(
                    {"ok": False, "wp_error": err_json, "status": e.code},
                    ensure_ascii=False).encode("utf-8"))

            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        self.err("Endpoint no encontrado", 404)


# ── Arranque con parada limpia ───────────────────────────────────────
def run():
    server = http.server.HTTPServer(("127.0.0.1", PORT), LinPressHandler)
    server.allow_reuse_address = True

    print(f"\n  LinPress  •  servidor local v2")
    print(f"  {'─' * 37}")
    print(f"  Escuchando en  http://127.0.0.1:{PORT}")
    print(f"  Datos en       {CONFIG_DIR}")
    print(f"  Editor         {HTML_FILE}")
    print(f"  Para detener   Ctrl+C\n")

    def _open_browser():
        time.sleep(0.6)
        webbrowser.open(HTML_FILE.as_uri())
    threading.Thread(target=_open_browser, daemon=True).start()

    # serve_forever en hilo daemon → hilo principal libre para señales
    srv = threading.Thread(target=server.serve_forever, daemon=True)
    srv.start()

    def _shutdown(sig=None, frame=None):
        print("\n[LinPress] Deteniendo servidor…")
        # server.shutdown() puede llamarse desde cualquier hilo
        threading.Thread(target=server.shutdown, daemon=True).start()
        srv.join(timeout=2)
        print("[LinPress] Hasta pronto!")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Mantener el proceso vivo esperando señales
    try:
        while srv.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    run()
