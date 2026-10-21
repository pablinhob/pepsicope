#!/usr/bin/env bash
# =====================================================================
#  Para el entorno de Periscope.
#  Cierra la terminal abierta con ./dev-start-env.sh y te devuelve a la
#  anterior, ya sin el entorno.
#  Uso: ./dev-stop-env.sh
# =====================================================================
set -euo pipefail

if [[ -n "${PERISCOPE_ENV_SHELL_PID:-}" && "$PERISCOPE_ENV_SHELL_PID" == "$PPID" ]]; then
  echo "✔ Entorno parado"
  # SIGHUP es la señal de "terminal cerrada": la shell sale limpiamente (guarda historial)
  kill -HUP "$PPID"
elif [[ -n "${PERISCOPE_ENV_SHELL_PID:-}" ]]; then
  echo "✘ Estás en una shell anidada dentro del entorno. Escribe exit hasta volver a la de"
  echo "  ./dev-start-env.sh y ejecuta de nuevo ./dev-stop-env.sh"
  exit 1
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  # Activado por otra vía (p. ej. VS Code lo activa solo en sus terminales)
  echo "El entorno no se arrancó con ./dev-start-env.sh. Para pararlo escribe: deactivate"
else
  echo "✔ El entorno no está activo"
fi
