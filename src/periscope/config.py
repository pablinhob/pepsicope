"""Carga de configuración desde .env (sin contaminar os.environ)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FEATURES = {
    "NEW_REPOS": "Repositorios nuevos",
    "DELETED_REPOS": "Repositorios eliminados",
    "REPO_METADATA": "Cambios de metadatos de repos",
    "WORKSPACE": "Proyectos y miembros del workspace",
    "BRANCHES": "Ramas nuevas",
    "LIBRARIES": "Bibliotecas",
    "FRAMEWORKS": "Frameworks",
    "EXTERNAL_SERVICES": "Servicios externos",
    "PRACTICES": "Forma de trabajar",
    "INFRA": "Virtualización e infraestructura",
    "CONFIG_PIPELINES": "Configuración y pipelines",
    "AI_REPO_PURPOSE": "IA: propósito de repos nuevos",
    "AI_CHANGE_ANALYSIS": "IA: análisis de cambios",
    "AI_SUMMARY": "IA: resumen final",
    "AUDIT_LOG": "Audit log de Atlassian",
}
FEATURE_DEFAULT_OFF = {"AUDIT_LOG"}


class ConfigError(Exception):
    pass


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    # Por si el valor arrastra un comentario en línea
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


@dataclass
class Config:
    workspace: str
    bb_email: str
    bb_api_token: str

    ai_backend: str
    claude_cli_path: str
    claude_model: str
    ai_workers: int
    ai_timeout: int
    ai_max_diff_chars: int
    ai_max_calls: int

    atlassian_org_id: str
    atlassian_admin_api_key: str

    repo_include: str
    repo_exclude: str
    max_repos: int
    git_workers: int
    report_language: str

    features: dict[str, bool] = field(default_factory=dict)

    root: Path = PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def ai_cache_dir(self) -> Path:
        return self.data_dir / "ai_cache"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def on(self, feature: str) -> bool:
        return self.features.get(feature, False)

    @property
    def ai_enabled(self) -> bool:
        return self.ai_backend == "claude-cli"

    def validate(self) -> None:
        problems = []
        if not self.workspace:
            problems.append("BITBUCKET_WORKSPACE está vacío")
        if not self.bb_email:
            problems.append("BITBUCKET_EMAIL está vacío")
        if not self.bb_api_token:
            problems.append(
                "BITBUCKET_API_TOKEN está vacío (necesario para listar los repos por la API)"
            )
        if self.ai_backend not in {"claude-cli", "none"}:
            problems.append(f"AI_BACKEND debe ser claude-cli o none, no {self.ai_backend!r}")
        if self.on("AUDIT_LOG") and not (self.atlassian_org_id and self.atlassian_admin_api_key):
            problems.append(
                "FEATURE_AUDIT_LOG=1 requiere ATLASSIAN_ORG_ID y ATLASSIAN_ADMIN_API_KEY"
            )
        if problems:
            raise ConfigError("\n".join(f"  - {p}" for p in problems))


def load_config(env_file: Path | None = None) -> Config:
    env_file = env_file or PROJECT_ROOT / ".env"
    values: dict[str, str | None] = {}
    if env_file.exists():
        values.update(dotenv_values(env_file))
    # Un FEATURE_* exportado en la shell gana al .env (útil para pruebas puntuales:
    # FEATURE_AI_SUMMARY=0 ./periscope.sh run)
    for name in FEATURES:
        key = f"FEATURE_{name}"
        if key in os.environ:
            values[key] = os.environ[key]

    def s(key: str, default: str = "") -> str:
        return _clean(values.get(key)) or default

    def i(key: str, default: int) -> int:
        raw = s(key)
        try:
            return int(raw) if raw else default
        except ValueError as e:
            raise ConfigError(f"  - {key} debe ser un número entero, no {raw!r}") from e

    features = {}
    for name in FEATURES:
        raw = s(f"FEATURE_{name}", "0" if name in FEATURE_DEFAULT_OFF else "1")
        features[name] = raw.lower() in {"1", "true", "yes", "on", "si", "sí"}

    return Config(
        workspace=s("BITBUCKET_WORKSPACE"),
        bb_email=s("BITBUCKET_EMAIL"),
        bb_api_token=s("BITBUCKET_API_TOKEN"),
        ai_backend=s("AI_BACKEND", "claude-cli"),
        claude_cli_path=s("CLAUDE_CLI_PATH", "claude"),
        claude_model=s("CLAUDE_MODEL", "opus"),
        ai_workers=i("AI_WORKERS", 3),
        ai_timeout=i("AI_TIMEOUT", 900),
        ai_max_diff_chars=i("AI_MAX_DIFF_CHARS", 60000),
        ai_max_calls=i("AI_MAX_CALLS", 0),
        atlassian_org_id=s("ATLASSIAN_ORG_ID"),
        atlassian_admin_api_key=s("ATLASSIAN_ADMIN_API_KEY"),
        repo_include=s("REPO_INCLUDE"),
        repo_exclude=s("REPO_EXCLUDE"),
        max_repos=i("MAX_REPOS", 0),
        git_workers=i("GIT_WORKERS", 4),
        report_language=s("REPORT_LANGUAGE", "español"),
        features=features,
    )
