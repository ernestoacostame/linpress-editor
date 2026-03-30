#!/usr/bin/env bash
# LinPress — Script de arranque
# Inicia el servidor local y abre el editor en el navegador

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$SCRIPT_DIR/linpress-server.py"
HTML="$SCRIPT_DIR/linpress.html"

# Verificar dependencias
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 no está instalado."
  echo "Instala con: sudo pacman -S python  (Arch)"
  echo "             sudo apt install python3  (Debian/Ubuntu)"
  exit 1
fi

if [[ ! -f "$HTML" ]]; then
  echo "Error: No se encuentra linpress.html en $SCRIPT_DIR"
  exit 1
fi

echo "Iniciando LinPress..."
exec python3 "$SERVER"
