"""Punto de entrada: `periscope <comando>`."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from periscope import __version__
from periscope.ai import AI, AIError
from periscope.analysis import Analyzer
from periscope.bitbucket import Bitbucket, BitbucketError
from periscope.config import FEATURES, Config, ConfigError, load_config
from periscope.html_report import write_html
from periscope.report import render
from periscope.snapshot import STAMP_FORMAT, collect, latest_snapshot, save_snapshot

log = logging.getLogger("periscope")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


# =========================================================================
def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    if args.no_ai:
        cfg.ai_backend = "none"
    if args.repo:
        cfg.repo_include = args.repo
    if args.max_repos is not None:
        cfg.max_repos = args.max_repos
    cfg.validate()

    started = time.monotonic()
    stamp = datetime.now().strftime(STAMP_FORMAT)
    prev_path, prev = latest_snapshot(cfg)
    if prev:
        log.info("Comparando con el snapshot %s", prev_path.name)
    else:
        log.info("No hay snapshots previos: este será el informe de LÍNEA BASE (puede tardar)")

    ai = None
    if cfg.ai_enabled:
        ai = AI(cfg)
        log.info("IA activa: %s (%s)", cfg.ai_backend, ai.model)

    bb = Bitbucket(cfg)
    try:
        cur = collect(cfg, bb, prev)
        changes = Analyzer(cfg, bb, prev, cur, ai).run()
    finally:
        bb.close()

    snap_path = cfg.snapshots_dir / f"{stamp}.json"
    stats = {
        "duration": str(timedelta(seconds=int(time.monotonic() - started))),
        "ai_backend": cfg.ai_backend,
        "ai_model": ai.model if ai else "—",
        "ai_calls": ai.calls if ai else 0,
        "ai_cache_hits": ai.cache_hits if ai else 0,
        "snapshot": snap_path.relative_to(cfg.root),
    }
    md = render(cfg, changes, stats)
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = "linea-base" if changes["baseline"] else "cambios"
    report_path = cfg.reports_dir / f"{stamp}_{cfg.workspace}_{suffix}.md"
    report_path.write_text(md)
    html_path = write_html(report_path)

    if args.no_save:
        log.info(
            "--no-save: no se guarda el snapshot (el próximo informe volverá a comparar con el "
            "anterior)"
        )
    else:
        save_snapshot(cfg, cur, stamp)
    log.info("Informe: %s", report_path.relative_to(cfg.root))
    log.info("Informe HTML: %s", html_path.relative_to(cfg.root))
    print(report_path)
    return 0


def cmd_html(cfg: Config, args: argparse.Namespace) -> int:
    """Genera el HTML de informes Markdown ya existentes (por defecto, todos los de reports/)."""
    if args.reports:
        paths = [Path(p) for p in args.reports]
    else:
        paths = sorted(cfg.reports_dir.glob("*.md")) if cfg.reports_dir.exists() else []
    if not paths:
        print("No hay informes que convertir", file=sys.stderr)
        return 1
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print(f"No existe: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1
    for path in paths:
        print(write_html(path))
    return 0


def cmd_check(cfg: Config, args: argparse.Namespace) -> int:
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(f"  {'✔' if good else '✘'} {text}")

    print("Configuración")
    try:
        cfg.validate()
        line(True, ".env válido")
    except ConfigError as e:
        line(False, f".env incompleto:\n{e}")

    print("Herramientas")
    line(shutil.which("git") is not None, f"git: {shutil.which('git')}")
    ssh = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-T",
            "git@bitbucket.org",
        ],
        capture_output=True,
        text=True,
    )
    # El aviso de "huella añadida" (primera conexión) no es el resultado: se descarta
    ssh_lines = [
        ln
        for ln in (ssh.stdout + ssh.stderr).splitlines()
        if ln.strip() and "Permanently added" not in ln
    ]
    ssh_out = "\n".join(ssh_lines)
    line(
        "authenticated" in ssh_out,
        f"SSH a Bitbucket: {ssh_out.splitlines()[0] if ssh_out else 'sin respuesta'}",
    )
    if cfg.ai_backend == "claude-cli":
        path = shutil.which(cfg.claude_cli_path)
        version = (
            subprocess.run([path, "--version"], capture_output=True, text=True).stdout.strip()
            if path
            else ""
        )
        line(path is not None, f"Claude Code: {path or 'no encontrado'} {version}")

    if cfg.workspace and cfg.bb_api_token:
        print("Bitbucket")
        bb = Bitbucket(cfg)
        try:
            me = bb.current_user()
            line(True, f"autenticado como {me.get('display_name')} ({me.get('nickname')})")
            ws = bb.workspace()
            line(True, f"workspace: {ws.get('name')} ({cfg.workspace})")
            page = bb._get(f"/repositories/{cfg.workspace}", {"pagelen": 1})
            line(True, f"repositorios visibles: {page.get('size', '?')}")
        except (BitbucketError, Exception) as e:
            line(False, f"error: {e}")
        finally:
            bb.close()

    if args.ai and cfg.ai_enabled:
        print("IA")
        try:
            ai = AI(cfg)
            out = ai.ask_json(
                "Responde ok=true.",
                {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                "prueba",
            )
            line(bool(out.get("ok")), f"{cfg.ai_backend} ({ai.model}) responde")
        except AIError as e:
            line(False, f"error: {e}")
    elif cfg.ai_enabled:
        print("IA\n  · usa `periscope check --ai` para hacer una llamada de prueba")
    return 0 if ok else 1


def cmd_features(cfg: Config, args: argparse.Namespace) -> int:
    for name, label in FEATURES.items():
        print(f"  {'1' if cfg.on(name) else '0'}  FEATURE_{name:<20} {label}")
    print(f"\n  IA: {cfg.ai_backend}")
    return 0


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    snaps = sorted(cfg.snapshots_dir.glob("*.json")) if cfg.snapshots_dir.exists() else []
    reports = sorted(cfg.reports_dir.glob("*.md")) if cfg.reports_dir.exists() else []
    clones = list(cfg.repos_dir.iterdir()) if cfg.repos_dir.exists() else []
    cache = list(cfg.ai_cache_dir.glob("*.json")) if cfg.ai_cache_dir.exists() else []
    print(f"  Workspace:       {cfg.workspace or '(sin configurar)'}")
    print(
        f"  Snapshots:       {len(snaps)}"
        + (f" (último: {snaps[-1].name})" if snaps else " → el próximo run será línea base")
    )
    print(
        f"  Informes:        {len(reports)}"
        + (f" (último: {reports[-1].name})" if reports else "")
    )
    print(f"  Clones locales:  {len(clones)}")
    print(f"  Caché de IA:     {len(cache)} respuestas")
    return 0


def cmd_reset_state(cfg: Config, args: argparse.Namespace) -> int:
    targets = {
        "snapshots": cfg.snapshots_dir,
        "ai-cache": cfg.ai_cache_dir,
        "repos": cfg.repos_dir,
    }
    chosen = list(targets) if args.what == "all" else [args.what]
    for key in chosen:
        if targets[key].exists():
            shutil.rmtree(targets[key])
            print(f"  borrado {targets[key].relative_to(cfg.root)}")
    return 0


# =========================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="periscope", description="Vigilancia incremental de Bitbucket"
    )
    parser.add_argument("--version", action="version", version=f"periscope {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="genera un informe nuevo")
    p.add_argument("--no-ai", action="store_true", help="sin IA (solo detección determinista)")
    p.add_argument("--no-save", action="store_true", help="no guardar el snapshot (para pruebas)")
    p.add_argument("--repo", help="regex de repos a procesar (sobrescribe REPO_INCLUDE)")
    p.add_argument("--max-repos", type=int, help="máximo de repos (sobrescribe MAX_REPOS)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="comprueba configuración y conectividad")
    p.add_argument("--ai", action="store_true", help="hace también una llamada de prueba a la IA")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("html", help="genera el HTML de informes Markdown existentes")
    p.add_argument("reports", nargs="*", help="informes .md (por defecto, todos los de reports/)")
    p.set_defaults(func=cmd_html)

    sub.add_parser("features", help="lista las funcionalidades y su estado").set_defaults(
        func=cmd_features
    )
    sub.add_parser("status", help="estado de snapshots, informes y cachés").set_defaults(
        func=cmd_status
    )

    p = sub.add_parser("reset-state", help="borra estado local (el próximo run será línea base)")
    p.add_argument("what", choices=["snapshots", "ai-cache", "repos", "all"])
    p.set_defaults(func=cmd_reset_state)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        cfg = load_config()
        return args.func(cfg, args)
    except ConfigError as e:
        print(f"Configuración incorrecta:\n{e}\nEdita el archivo .env", file=sys.stderr)
        return 2
    except (BitbucketError, AIError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido", file=sys.stderr)
        return 130
