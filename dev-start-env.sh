#!/usr/bin/env bash
# =====================================================================
#  Arranca el entorno de Periscope.
#  Abre una terminal nueva (dentro de la actual) con el entorno activado
#  y tu configuración habitual de zsh/bash.
#  Uso:    ./dev-start-env.sh
#  Parar:  ./dev-stop-env.sh   (o escribe: exit)
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

if [[ ! -f "$VENV/bin/activate" ]]; then
  echo "✘ El entorno no existe. Ejecuta: ./periscope.sh setup"
  exit 1
fi
if [[ "${VIRTUAL_ENV:-}" == "$VENV" ]]; then
  echo "✔ Ya estás dentro del entorno. Para salir: ./dev-stop-env.sh"
  exit 0
fi

# Con exec, la nueva terminal hereda el PID de este script: dev-stop-env.sh lo usa para
# saber que la terminal que tiene que cerrar es esta y no otra.
export PERISCOPE_ENV_SHELL_PID=$$
cd "$ROOT"

# Primero tu configuración habitual y después el activate, para que el entorno quede por delante
rc_dir="$(mktemp -d)"
echo "✔ Entorno activado ($("$VENV/bin/python" --version)). Para salir: ./dev-stop-env.sh"
case "$(basename "${SHELL:-/bin/bash}")" in
  zsh)
    # ZDOTDIR vuelve a $HOME antes de cargar tu config: así el historial y demás rutas
    # que dependen de él apuntan a tus archivos de siempre y no a la carpeta temporal.
    cat > "$rc_dir/.zshrc" <<EOF
ZDOTDIR="\$HOME"
# /etc/zshrc de macOS ya fijó HISTFILE con el ZDOTDIR temporal
[[ "\$HISTFILE" == "$rc_dir"/* ]] && HISTFILE="\$HOME/.zsh_history"
[[ -f "\$HOME/.zshenv" ]] && source "\$HOME/.zshenv"
[[ -f "\$HOME/.zshrc" ]] && source "\$HOME/.zshrc"
source "$VENV/bin/activate"
rm -rf "$rc_dir"
EOF
    ZDOTDIR="$rc_dir" exec zsh -i
    ;;
  *)
    cat > "$rc_dir/rc" <<EOF
[[ -f "\$HOME/.bashrc" ]] && source "\$HOME/.bashrc"
source "$VENV/bin/activate"
rm -rf "$rc_dir"
EOF
    exec bash --rcfile "$rc_dir/rc" -i
    ;;
esac
