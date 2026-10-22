#!/usr/bin/env python3
"""Hook PreToolUse (Bash): bloquea cualquier comando que modifique git o código remoto.

Claude Code envía el comando por stdin como JSON. Si es de escritura, se sale con código 2 y el
motivo en stderr: Claude Code no lo ejecuta y le muestra el motivo a Claude.
Permitido: git de solo lectura (status, diff, log, show, blame, fetch...).
"""

import json
import re
import sys

# Subcomandos de git que modifican el repositorio, el índice, el working tree o el remoto
GIT_WRITE = (
    "commit|push|pull|merge|rebase|reset|revert|cherry-pick|tag|am|apply|add|rm|mv|restore|"
    "checkout|switch|stash|clean|init|update-ref|symbolic-ref|filter-branch|filter-repo|replace|"
    "notes|commit-tree|gc|prune|submodule|worktree|sparse-checkout|lfs|bisect|reflog\\s+(delete|expire)"
)
# Opciones globales de git que pueden ir antes del subcomando: -C ruta, -c clave=valor, --git-dir=...
GIT_PREFIX = r"(?:^|[;&|({\s`$])git(?:\s+(?:-[Cc]\s+\S+|--\S+))*\s+"

RULES = [
    (rf"{GIT_PREFIX}(?:{GIT_WRITE})\b", "comando git que modifica el repositorio"),
    # branch / remote / config solo en modo consulta
    (
        rf"{GIT_PREFIX}branch\s+(?!(?:-a|-r|-v|-vv|--list|--all|--remotes|--show-current|"
        r"--contains|--merged|--no-merged)\b)\S",
        "git branch que crea, borra o renombra ramas",
    ),
    (rf"{GIT_PREFIX}remote\s+(?!(?:-v|show|get-url)\b)\S", "git remote que cambia remotos"),
    (rf"{GIT_PREFIX}config\s+(?!(?:--get|--get-all|--list|-l|--show-origin)\b)\S", "git config de escritura"),
    # GitHub CLI: PRs, repos, releases, issues y llamadas de escritura a la API
    (
        r"(?:^|[;&|(\s])gh\s+(?:pr|repo|release|issue|gist|workflow|secret|variable|label)\s+"
        r"(?!(?:view|list|status|diff|checks)\b)\S",
        "gh que modifica GitHub",
    ),
    (r"(?:^|[;&|(\s])gh\s+api\b.*(?:-X\s*(?:POST|PUT|PATCH|DELETE)|--method\s+(?:POST|PUT|PATCH|DELETE)|"
     r"\s-[fF]\s|--field|--raw-field|--input)", "gh api de escritura"),
    # Peticiones de escritura HTTP contra servicios de código
    (
        r"(?:curl|wget|http|https|xh)\b.*(?:-X\s*(?:POST|PUT|PATCH|DELETE)|--request\s+(?:POST|PUT|PATCH|DELETE)|"
        r"\s-d\s|--data|--form|\s-F\s|--upload-file|--method=(?:POST|PUT|PATCH|DELETE))"
        r".*(?:bitbucket\.org|atlassian\.(?:com|net)|github\.com|gitlab\.com)",
        "petición HTTP de escritura contra Bitbucket/GitHub/GitLab",
    ),
]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    for pattern, reason in RULES:
        if re.search(pattern, command, re.IGNORECASE):
            print(
                f"BLOQUEADO por la política del proyecto ({reason}). En este proyecto nunca se "
                "modifica git ni código remoto: ni commit, ni push, ni nada equivalente. Deja los "
                "cambios en el working tree y dile al usuario qué archivos han cambiado.",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
