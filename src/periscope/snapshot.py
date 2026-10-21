"""Snapshot del estado del workspace: es la base de comparación entre informes."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from periscope import scanners
from periscope.bitbucket import Bitbucket
from periscope.config import Config
from periscope.gitrepo import Repo

log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1
STAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


def repo_dir(cfg: Config, uuid: str) -> Path:
    return cfg.repos_dir / re.sub(r"[^A-Za-z0-9-]", "", uuid)


def open_repo(cfg: Config, bb: Bitbucket, meta: dict) -> Repo:
    return Repo(repo_dir(cfg, meta["uuid"]), f"git@bitbucket.org:{meta['full_name']}.git")


# --- persistencia ------------------------------------------------------------
def latest_snapshot(cfg: Config) -> tuple[Path | None, dict | None]:
    files = sorted(cfg.snapshots_dir.glob("*.json"))
    if not files:
        return None, None
    return files[-1], json.loads(files[-1].read_text())


def save_snapshot(cfg: Config, snap: dict, stamp: str) -> Path:
    cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.snapshots_dir / f"{stamp}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    return path


# --- recolección -----------------------------------------------------------------
def _repo_meta(raw: dict) -> dict:
    links = raw.get("links") or {}
    return {
        "uuid": raw["uuid"],
        "slug": raw["slug"],
        "name": raw.get("name"),
        "full_name": raw["full_name"],
        "description": (raw.get("description") or "").strip(),
        "language": raw.get("language") or "",
        "is_private": raw.get("is_private"),
        "project_key": (raw.get("project") or {}).get("key"),
        "project_name": (raw.get("project") or {}).get("name"),
        "created_on": raw.get("created_on"),
        "updated_on": raw.get("updated_on"),
        "size": raw.get("size"),
        "mainbranch": (raw.get("mainbranch") or {}).get("name"),
        "html_url": (links.get("html") or {}).get("href"),
    }


def _filter_repos(cfg: Config, repos: list[dict]) -> list[dict]:
    if cfg.repo_include:
        rx = re.compile(cfg.repo_include)
        repos = [r for r in repos if rx.search(r["slug"])]
    if cfg.repo_exclude:
        rx = re.compile(cfg.repo_exclude)
        repos = [r for r in repos if not rx.search(r["slug"])]
    if cfg.max_repos:
        repos = repos[: cfg.max_repos]
    return repos


GIT_KEYS = ("git", "deps", "frameworks", "services", "signals", "config", "base_images")


def _scan_repo(cfg: Config, bb: Bitbucket, meta: dict, prev: dict | None) -> dict:
    snap = dict(meta)
    # Si Bitbucket dice que no ha cambiado nada desde la última vez, reutilizamos el análisis
    if prev and prev.get("updated_on") == meta["updated_on"] and (prev.get("git") or {}).get("ok"):
        for key in GIT_KEYS:
            snap[key] = prev.get(key)
        snap["reused"] = True
        return snap

    if not meta["mainbranch"]:
        snap["git"] = {"ok": False, "error": "repositorio vacío (sin rama principal)"}
        return snap

    repo = open_repo(cfg, bb, meta)
    try:
        repo.sync(meta["mainbranch"])
        files = repo.files()
        first = repo.first_commit()
        head = repo.commit_info("HEAD")
        snap["git"] = {
            "ok": True,
            "head": head.hash if head else None,
            "head_commit": head.as_dict() if head else None,
            "first_commit": first.as_dict() if first else None,
            "commit_count": repo.commit_count(),
            "branches": repo.branches(),
        }
        deps = (
            scanners.scan_dependencies(repo, files)
            if cfg.on("LIBRARIES") or cfg.on("FRAMEWORKS")
            else {}
        )
        snap["deps"] = deps
        snap["frameworks"] = scanners.scan_frameworks(files, deps)
        snap["services"] = (
            scanners.scan_services(repo, files, deps)
            if cfg.on("EXTERNAL_SERVICES")
            else {"sdks": [], "hosts": [], "images": []}
        )
        snap["signals"] = scanners.scan_signals(repo, files, with_docblocks=cfg.on("PRACTICES"))
        snap["config"] = scanners.scan_config(files)
        snap["base_images"] = scanners.base_images(repo, files) if cfg.on("INFRA") else []
    except Exception as e:
        log.warning("Error analizando %s: %s", meta["slug"], e)
        snap["git"] = {"ok": False, "error": str(e)[:500]}
    return snap


def collect(cfg: Config, bb: Bitbucket, prev: dict | None) -> dict[str, Any]:
    taken_at = datetime.now().astimezone().isoformat(timespec="seconds")
    snap: dict[str, Any] = {
        "version": SNAPSHOT_VERSION,
        "taken_at": taken_at,
        "workspace": cfg.workspace,
        "repos": {},
        "projects": {},
        "members": {},
        "errors": [],
    }

    if cfg.on("WORKSPACE"):
        try:
            snap["projects"] = {
                p["key"]: {
                    "name": p.get("name"),
                    "created_on": p.get("created_on"),
                    "is_private": p.get("is_private"),
                }
                for p in bb.projects()
            }
        except Exception as e:
            snap["errors"].append(f"proyectos: {e}")
        try:
            for m in bb.members():
                user = m.get("user") or {}
                snap["members"][user.get("uuid", "?")] = {
                    "name": user.get("display_name"),
                    "nickname": user.get("nickname"),
                }
        except Exception as e:
            snap["errors"].append(f"miembros: {e}")

    log.info("Listando repositorios del workspace %s…", cfg.workspace)
    all_metas = [_repo_meta(r) for r in bb.repositories()]
    metas = _filter_repos(cfg, all_metas)
    log.info("%d repositorios en el workspace, %d a procesar", len(all_metas), len(metas))

    prev_repos = (prev or {}).get("repos", {})
    # Los repos que quedan fuera del filtro conservan su estado anterior: así no aparecen como
    # nuevos ni como borrados, y el snapshot sigue siendo completo.
    selected = {m["uuid"] for m in metas}
    for m in all_metas:
        if m["uuid"] not in selected and m["uuid"] in prev_repos:
            snap["repos"][m["uuid"]] = {**prev_repos[m["uuid"]], "carried": True}
    snap["inventory"] = {m["uuid"]: m["slug"] for m in all_metas}

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, cfg.git_workers)) as pool:
        futures = {
            pool.submit(_scan_repo, cfg, bb, m, prev_repos.get(m["uuid"])): m for m in metas
        }
        for fut in as_completed(futures):
            r = fut.result()
            snap["repos"][r["uuid"]] = r
            done += 1
            state = (
                "sin cambios"
                if r.get("reused")
                else ("ok" if (r.get("git") or {}).get("ok") else "error")
            )
            log.info("[%d/%d] %s (%s)", done, len(metas), r["slug"], state)
            if not (r.get("git") or {}).get("ok"):
                snap["errors"].append(f"{r['slug']}: {(r.get('git') or {}).get('error')}")
    return snap
