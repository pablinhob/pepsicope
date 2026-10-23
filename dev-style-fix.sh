#!/usr/bin/env bash
# =====================================================================
#  Corrige el estilo del código.
#    1. Black reformatea (espacios, saltos de línea, comillas, comas...)
#    2. Ruff arregla lo automático (orden de imports, sintaxis moderna...)
#    3. Lista lo que no se puede corregir solo y hay que hacer a mano
#  Uso: ./dev-style-fix.sh
#  Para solo comprobar: ./dev-style-check.sh
# =====================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
BIN="$ROOT/.venv/bin"
TARGETS=(src tests)

[[ -x "$BIN/black" && -x "$BIN/ruff" ]] || { echo "Falta el entorno. Ejecuta: ./workspace-periscope.sh setup"; exit 1; }

echo "▸ Black: reformateando"
"$BIN/black" "${TARGETS[@]}"
echo
echo "▸ Ruff: corrigiendo lo automático"
"$BIN/ruff" check --fix --quiet "${TARGETS[@]}" >/dev/null
# Lo que Ruff acaba de tocar vuelve a pasar por Black
"$BIN/black" --quiet "${TARGETS[@]}"
echo

echo "▸ Pendiente de corregir a mano"
if "$BIN/ruff" check "${TARGETS[@]}"; then
  echo
  echo "✔ Todo corregido y conforme a PEP 8"
else
  echo
  echo "✘ Lo de arriba no se corrige solo (p. ej. cadenas de más de 99 caracteres o nombres)."
  exit 1
fi
