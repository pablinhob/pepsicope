#!/usr/bin/env bash
# =====================================================================
#  Boldest Periscope — gestor del entorno y de la aplicación
#  Uso: ./workspace-periscope.sh <comando> [args]     (./workspace-periscope.sh help)
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY_VERSION="$(tr -d '[:space:]' < .python-version 2>/dev/null || echo 3.15)"
VENV="$ROOT/.venv"
export UV_PROJECT_ENVIRONMENT="$VENV"

c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_err=$'\033[31m'; c_dim=$'\033[2m'; c_b=$'\033[1m'; c_0=$'\033[0m'
info() { echo "${c_b}▸${c_0} $*"; }
ok()   { echo "${c_ok}✔${c_0} $*"; }
warn() { echo "${c_warn}!${c_0} $*"; }
die()  { echo "${c_err}✘${c_0} $*" >&2; exit 1; }

confirm() {
  local answer
  read -r -p "$1 [s/N] " answer
  [[ "$answer" =~ ^[sSyY]$ ]]
}

need_uv() {
  command -v uv >/dev/null 2>&1 && return 0
  warn "uv no está instalado (gestor de Python y paquetes)."
  if command -v brew >/dev/null 2>&1; then
    confirm "¿Instalarlo con Homebrew?" || die "Instala uv: https://docs.astral.sh/uv/"
    brew install uv
  else
    confirm "¿Instalarlo con el instalador oficial (curl | sh)?" || die "Instala uv: https://docs.astral.sh/uv/"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
}

need_venv() {
  [[ -x "$VENV/bin/python" ]] || die "El entorno no existe. Ejecuta: ./workspace-periscope.sh setup"
}

usage() {
  cat <<EOF
${c_b}Boldest Periscope${c_0} — gestor del entorno

${c_b}Entorno${c_0}
  setup                 Instala uv y Python $PY_VERSION, crea .venv, instala dependencias y prepara .env
  start                 Arranca el entorno (igual que ./dev-start-env.sh)
  stop                  Para el entorno     (igual que ./dev-stop-env.sh)
  status                Estado del entorno y de la aplicación
  doctor                Comprueba herramientas, .env y conexión con Bitbucket (y IA con --ai)
  reset                 Borra .venv y lo reinstala desde cero (no toca datos ni informes)
  destroy               Borra .venv y cachés de Python (no toca datos ni informes)
  python-upgrade [ver]  Cambia la versión de Python (por defecto la última estable) y reinstala

${c_b}Bibliotecas${c_0}
  install               Instala exactamente lo que fija uv.lock
  add <pkg...>          Añade bibliotecas            (add --dev <pkg> para desarrollo)
  remove <pkg...>       Elimina bibliotecas
  upgrade [pkg...]      Actualiza todas o algunas bibliotecas dentro de lo permitido
  outdated              Lista las bibliotecas con versiones nuevas
  list                  Lista las bibliotecas instaladas
  tree                  Árbol de dependencias
  export                Genera requirements.txt a partir del lock
  lock                  Regenera uv.lock

${c_b}Aplicación${c_0}
  run [opciones]        Genera un informe (opciones: --no-ai --no-save --repo REGEX --max-repos N -v)
  features              Muestra qué funcionalidades están activas (se cambian en .env)
  html [informe.md...]  Genera el HTML (.md.html) de informes existentes (por defecto, todos)
  reports               Lista los informes generados
  last                  Muestra la ruta del último informe (y lo abre en VS Code si 'code' existe)
  reset-state <qué>     Borra estado: snapshots | ai-cache | repos | all  (snapshots → próximo run = línea base)

${c_b}Desarrollo${c_0}
  dev-style-check.sh / dev-style-fix.sh   Scripts aparte: comprobar / corregir estilo (Black + Ruff)
  test                  pytest
  py <args>             Ejecuta python dentro del entorno
EOF
}

cmd="${1:-help}"; shift || true

case "$cmd" in
  # ------------------------------------------------------------------ entorno
  setup)
    need_uv
    info "Instalando Python $PY_VERSION (gestionado por uv)…"
    uv python install "$PY_VERSION"
    info "Creando entorno e instalando dependencias…"
    uv sync
    if [[ ! -f .env ]]; then
      cp .env.example .env
      chmod 600 .env
      ok "Creado .env a partir de .env.example → rellénalo con tus credenciales"
    fi
    mkdir -p reports data
    command -v git >/dev/null || warn "git no está instalado"
    command -v claude >/dev/null && ok "Claude Code: $(claude --version 2>/dev/null)" \
      || warn "No encuentro 'claude' en el PATH (necesario si AI_BACKEND=claude-cli)"
    ok "Entorno listo: $("$VENV/bin/python" --version)"
    echo "  Siguiente paso: edita .env y ejecuta ${c_b}./workspace-periscope.sh doctor${c_0}"
    ;;
  start|shell) exec "$ROOT/dev-start-env.sh" ;;
  stop)        exec "$ROOT/dev-stop-env.sh" ;;
  status)
    if [[ -x "$VENV/bin/python" ]]; then
      ok "Entorno: $VENV ($("$VENV/bin/python" --version))"
      echo "  Bibliotecas instaladas: $(uv pip list --python "$VENV/bin/python" 2>/dev/null | tail -n +3 | wc -l | tr -d ' ')"
    else
      warn "Entorno no creado (./workspace-periscope.sh setup)"
    fi
    [[ -n "${VIRTUAL_ENV:-}" ]] && echo "  Activo en esta shell: sí" || echo "  Activo en esta shell: no"
    [[ -f .env ]] && ok ".env presente" || warn ".env no existe"
    [[ -x "$VENV/bin/periscope" ]] && "$VENV/bin/periscope" status
    ;;
  doctor)
    need_uv; need_venv
    uv run periscope check "$@"
    ;;
  reset)
    need_uv
    confirm "Se borrará .venv y se reinstalará desde uv.lock. ¿Seguir?" || exit 0
    rm -rf "$VENV"
    uv sync
    ok "Entorno reinstalado"
    ;;
  destroy)
    confirm "Se borrará .venv y las cachés de Python. ¿Seguir?" || exit 0
    rm -rf "$VENV" .pytest_cache .ruff_cache
    find . -name __pycache__ -type d -prune -not -path "./data/*" -exec rm -rf {} +
    ok "Entorno eliminado (vuelve a crearlo con ./workspace-periscope.sh setup)"
    ;;
  python-upgrade)
    need_uv
    target="${1:-}"
    if [[ -z "$target" ]]; then
      target="$(uv python list --only-downloads 2>/dev/null | awk '{print $1}' | grep -E '^cpython-3\.[0-9]+\.[0-9]+-' \
        | sed -E 's/^cpython-([0-9]+\.[0-9]+)\..*/\1/' | sort -t. -k1,1n -k2,2n | tail -1)"
    fi
    [[ -n "$target" ]] || die "No pude determinar la última versión de Python"
    info "Cambiando a Python $target…"
    uv python install "$target"
    echo "$target" > .python-version
    # Sin "sed -i": su sintaxis difiere entre macOS (BSD) y Linux (GNU)
    tmp="$(mktemp)"
    sed -E "s/^requires-python = .*/requires-python = \">=$target\"/" pyproject.toml > "$tmp"
    mv "$tmp" pyproject.toml
    rm -rf "$VENV"
    uv lock && uv sync
    ok "Ahora en $("$VENV/bin/python" --version)"
    ;;

  # ------------------------------------------------------------- bibliotecas
  install)   need_uv; uv sync --frozen 2>/dev/null || uv sync; ok "Dependencias instaladas" ;;
  add)       need_uv; [[ $# -gt 0 ]] || die "Uso: ./workspace-periscope.sh add [--dev] <paquete...>"; uv add "$@" ;;
  remove)    need_uv; [[ $# -gt 0 ]] || die "Uso: ./workspace-periscope.sh remove <paquete...>"; uv remove "$@" ;;
  upgrade)
    need_uv
    if [[ $# -gt 0 ]]; then
      args=(); for p in "$@"; do args+=(--upgrade-package "$p"); done
      uv lock "${args[@]}"
    else
      uv lock --upgrade
    fi
    uv sync
    ok "Dependencias actualizadas"
    ;;
  outdated)  need_venv; uv pip list --outdated --python "$VENV/bin/python" ;;
  list)      need_venv; uv pip list --python "$VENV/bin/python" ;;
  tree)      need_uv; uv tree ;;
  export)    need_uv; uv export --no-hashes --no-dev --format requirements-txt > requirements.txt; ok "requirements.txt generado" ;;
  lock)      need_uv; uv lock ;;

  # -------------------------------------------------------------- aplicación
  run)       need_venv; uv run periscope run "$@" ;;
  features)  need_venv; uv run periscope features ;;
  html)      need_venv; uv run periscope html "$@" ;;
  reports)   ls -1 reports/*.md 2>/dev/null || echo "Aún no hay informes" ;;
  last)
    last="$(ls -1 reports/*.md 2>/dev/null | tail -1)"
    [[ -n "$last" ]] || die "Aún no hay informes"
    echo "$last"
    command -v code >/dev/null && code "$last" || true
    ;;
  reset-state)
    need_venv
    what="${1:-}"; [[ -n "$what" ]] || die "Uso: ./workspace-periscope.sh reset-state snapshots|ai-cache|repos|all"
    confirm "Se borrará el estado '$what'. ¿Seguir?" || exit 0
    uv run periscope reset-state "$what"
    ;;

  # -------------------------------------------------------------- desarrollo
  test)      need_uv; uv run pytest "$@" ;;
  py)        need_venv; "$VENV/bin/python" "$@" ;;

  help|-h|--help) usage ;;
  *) usage; die "Comando desconocido: $cmd" ;;
esac
