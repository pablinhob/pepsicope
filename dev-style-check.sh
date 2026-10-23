#!/usr/bin/env bash
# =====================================================================
#  Comprueba el estilo del código SIN modificar nada.
#    - Black: formato (muestra el diff de lo que cambiaría)
#    - Ruff:  PEP 8, nombres, orden de imports y posibles bugs
#  Uso: ./dev-style-check.sh
#  Para corregir: ./dev-style-fix.sh
# =====================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
BIN="$ROOT/.venv/bin"
TARGETS=(src tests)

[[ -x "$BIN/black" && -x "$BIN/ruff" ]] || { echo "Falta el entorno. Ejecuta: ./workspace-periscope.sh setup"; exit 1; }

rc=0
echo "▸ Black (formato)"
"$BIN/black" --check --diff --color "${TARGETS[@]}" || rc=1
echo
echo "▸ Ruff (PEP 8, imports, bugs)"
"$BIN/ruff" check "${TARGETS[@]}" || rc=1
echo

if [[ $rc -eq 0 ]]; then
  echo "✔ Estilo correcto"
else
  echo "✘ Hay cosas que corregir. Ejecuta: ./dev-style-fix.sh"
fi
exit $rc
