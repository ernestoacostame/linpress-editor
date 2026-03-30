#!/usr/bin/env python3
"""
LinPress — Servidor local de persistencia
Escucha en localhost:40821 y gestiona lectura/escritura de archivos
en ~/.config/linpress/{borradores,credenciales}/

Endpoints:
  GET  /ping                        → {"ok": true}
  GET  /read?file=<ruta_relativa>   → contenido del archivo JSON
  POST /write                       → {"file": "...", "data": {...}}
  POST /delete                      → {"file": "..."}
  GET  /list?dir=<borradores|credenciales>  → lista de archivos
"""

import http.server
import json
import os
import pathlib
import sys
import urllib.parse
import threading
import webbrowser
import signal

# ── Rutas base ───────────────────────────────────────────────────────
CONFIG_DIR   = pathlib.Path.home() / ".config" / "linpress"
DRAFTS_DIR   = CONFIG_DIR / "borradores"
CREDS_DIR    = CONFIG_DIR / "credenciales"
PORT         = 40821
HTML_FILE    = pathlib.Path(__file__).parent / "linpress.html"

# Crear estructura de directorios si no existe
for d in [DRAFTS_DIR, CREDS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

print(f"[LinPress] Directorios de datos:")
print(f"  Borradores:   {DRAFTS_DIR}")
print(f"  Credenciales: {CREDS_DIR}")

ALLOWED_DIRS = {
    "borradores":   DRAFTS_DIR,
    "credenciales": CREDS_DIR,
}

def resolve_path(rel: str) -> pathlib.Path | None:
    """Resuelve una ruta relativa como 'borradores/foo.json' a una ruta absoluta segura."""
    parts = pathlib.PurePosixPath(rel).parts
    if not parts:
        return None
    dir_key = parts[0]
    if dir_key not in ALLOWED_DIRS:
        return None
    base = ALLOWED_DIRS[dir_key]
    # Evitar path traversal
    filename = parts[1] if len(parts) > 1 else None
    if not filename:
        return None
    safe = (base / filename).resolve()
    if not str(safe).startswith(str(base.resolve())):
        return None
    return safe


class LinPressHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # Silenciar logs de acceso

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg, status=400):
        self.send_json({"ok": False, "error": msg}, status)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        path   = parsed.path

        if path == "/ping":
            self.send_json({"ok": True, "version": "1.0.0", "app": "LinPress"})

        elif path == "/read":
            rel = params.get("file", "")
            fpath = resolve_path(rel)
            if not fpath:
                return self.send_error_json("Ruta inválida")
            if not fpath.exists():
                return self.send_json({"ok": False, "exists": False})
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                self.send_json({"ok": True, "exists": True, "data": data})
            except Exception as e:
                self.send_error_json(f"Error leyendo archivo: {e}")

        elif path == "/list":
            dir_key = params.get("dir", "")
            base = ALLOWED_DIRS.get(dir_key)
            if not base:
                return self.send_error_json("Directorio inválido")
            try:
                files = [
                    {
                        "name": f.name,
                        "stem": f.stem,
                        "modified": f.stat().st_mtime,
                        "size": f.stat().st_size,
                    }
                    for f in sorted(base.glob("*.json"), key=lambda x: -x.stat().st_mtime)
                ]
                self.send_json({"ok": True, "files": files})
            except Exception as e:
                self.send_error_json(str(e))

        else:
            self.send_error_json("Endpoint no encontrado", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            return self.send_error_json(f"JSON inválido: {e}")

        if path == "/write":
            rel   = payload.get("file", "")
            data  = payload.get("data")
            if data is None:
                return self.send_error_json("Campo 'data' requerido")
            fpath = resolve_path(rel)
            if not fpath:
                return self.send_error_json("Ruta inválida")
            try:
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                self.send_json({"ok": True, "file": str(fpath)})
            except Exception as e:
                self.send_error_json(f"Error escribiendo: {e}")

        elif path == "/delete":
            rel   = payload.get("file", "")
            fpath = resolve_path(rel)
            if not fpath:
                return self.send_error_json("Ruta inválida")
            if fpath.exists():
                fpath.unlink()
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False, "error": "Archivo no encontrado"})

        else:
            self.send_error_json("Endpoint no encontrado", 404)


def run_server():
    server = http.server.HTTPServer(("127.0.0.1", PORT), LinPressHandler)
    print(f"[LinPress] Servidor escuchando en http://127.0.0.1:{PORT}")
    print(f"[LinPress] Abriendo editor...")

    # Abrir el HTML en el navegador después de un breve delay
    def open_browser():
        import time; time.sleep(0.5)
        webbrowser.open(HTML_FILE.as_uri())
    threading.Thread(target=open_browser, daemon=True).start()

    # Capturar Ctrl+C para cerrar limpiamente
    def on_sigint(sig, frame):
        print("\n[LinPress] Cerrando servidor...")
        server.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGINT, on_sigint)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_server()
