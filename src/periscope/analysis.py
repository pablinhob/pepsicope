"""Comparación entre snapshots + atribución con git + interpretación con IA."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from periscope import bitbucket
from periscope.ai import AI, AIBudgetExceededError, AIError
from periscope.bitbucket import Bitbucket
from periscope.config import Config
from periscope.gitrepo import GitError, Repo
from periscope.snapshot import open_repo

log = logging.getLogger(__name__)

META_FIELDS = {
    "slug": "slug",
    "name": "nombre",
    "is_private": "privado",
    "project_key": "proyecto",
    "mainbranch": "rama principal",
    "description": "descripción",
    "language": "lenguaje",
}


# =========================================================================
#  Utilidades
# =========================================================================
def _major(version: str) -> str | None:
    m = re.search(r"(\d+)(?:\.(\d+))?", version or "")
    if not m:
        return None
    # En 0.x el "minor" se comporta como major
    return f"0.{m.group(2)}" if m.group(1) == "0" and m.group(2) else m.group(1)


def _set_diff(old: list | None, new: list | None) -> tuple[list, list]:
    old_s, new_s = set(old or []), set(new or [])
    return sorted(new_s - old_s), sorted(old_s - new_s)


def _who_added_file(repo: Repo, old: str | None, path: str) -> dict | None:
    c = repo.first_adding_commit(old, path)
    return c.as_dict() if c else None


def _who_added_package(repo: Repo, old: str, manifest: str, package: str) -> dict | None:
    """Primer commit del rango que añade una línea con el paquete en el manifiesto."""
    commits = repo.log_range(old, paths=[manifest], limit=40)
    needle = re.compile(
        r"^\+.*[\"'\s/:]" + re.escape(package.split(":")[-1]) + r"[\"'\s=<>~^@:\[]", re.M
    )
    for c in reversed(commits):  # del más antiguo al más reciente
        if needle.search(repo.commit_diff(c.hash, manifest)):
            return c.as_dict()
    return commits[-1].as_dict() if commits else None


def _audit_mentions(events: list[dict], *needles: str) -> list[dict]:
    found = []
    for ev in events:
        blob = json.dumps(ev, ensure_ascii=False)
        if any(n and n in blob for n in needles):
            attrs = ev.get("attributes") or {}
            found.append(
                {
                    "time": attrs.get("time"),
                    "action": (attrs.get("action") or ev.get("type")),
                    "actor": ((attrs.get("actor") or {}).get("name")),
                }
            )
    return found[:10]


# =========================================================================
#  Esquemas de salida de la IA
# =========================================================================
def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required or list(props),
        "additionalProperties": False,
    }


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


S = {"type": "string"}
PURPOSE_SCHEMA = _obj(
    {
        "purpose": S,
        "kind": S,
        "stack": S,
        "notable": _arr(S),
        "maturity": S,
    }
)
CHANGE_SCHEMA = _obj(
    {
        "summary": S,
        "library_reasons": _arr(_obj({"name": S, "reason": S})),
        "framework_notes": _arr(S),
        "external_services": _arr(_obj({"service": S, "kind": S, "intent": S, "evidence": S})),
        "new_features": _arr(
            _obj(
                {
                    "title": S,
                    "description": S,
                    "hidden": {"type": "boolean"},
                    "evidence": S,
                    "authors": _arr(S),
                }
            )
        ),
        "practice_changes": _arr(_obj({"title": S, "description": S, "evidence": S})),
        "infra_config_changes": _arr(_obj({"title": S, "description": S, "evidence": S})),
        "attention": _arr(S),
    }
)
SUMMARY_SCHEMA = _obj(
    {
        "headline": S,
        "highlights": _arr(S),
        "ways_of_working": _arr(_obj({"title": S, "description": S, "repos": _arr(S)})),
        "infrastructure": _arr(_obj({"title": S, "description": S, "repos": _arr(S)})),
        "deleted_repo_reasons": _arr(_obj({"slug": S, "reason": S})),
        "attention": _arr(S),
    }
)


# =========================================================================
#  Análisis
# =========================================================================
class Analyzer:
    def __init__(self, cfg: Config, bb: Bitbucket, prev: dict | None, cur: dict, ai: AI | None):
        self.cfg, self.bb, self.prev, self.cur, self.ai = cfg, bb, prev, cur, ai
        self.baseline = prev is None
        self.audit: list[dict] = []
        self.ai_errors: list[str] = []

    # --- IA protegida ---------------------------------------------------
    def _ask(self, prompt: str, schema: dict, label: str) -> dict | None:
        if not self.ai:
            return None
        try:
            return self.ai.ask_json(prompt, schema, label)
        except AIBudgetExceededError as e:
            self.ai_errors.append(f"{label}: omitido ({e})")
        except (AIError, json.JSONDecodeError) as e:
            log.warning("IA falló en %s: %s", label, e)
            self.ai_errors.append(f"{label}: {e}")
        return None

    # --- punto de entrada --------------------------------------------------
    def run(self) -> dict[str, Any]:
        cfg = self.cfg
        prev_repos = (self.prev or {}).get("repos", {})
        cur_repos = self.cur["repos"]
        inventory = self.cur.get("inventory", {})

        if cfg.on("AUDIT_LOG") and self.prev:
            try:
                self.audit = bitbucket.audit_events(
                    cfg, self.prev["taken_at"], self.cur["taken_at"]
                )
            except Exception as e:
                self.cur["errors"].append(f"audit log: {e}")

        changes: dict[str, Any] = {
            "baseline": self.baseline,
            "since": (self.prev or {}).get("taken_at"),
            "until": self.cur["taken_at"],
            "workspace": cfg.workspace,
            "repo_total": len(inventory),
            "new_repos": [],
            "deleted_repos": [],
            "metadata_changes": [],
            "repo_changes": [],
            "workspace_changes": {},
            "errors": list(self.cur.get("errors", [])),
        }

        # Workspace
        if cfg.on("WORKSPACE") and self.prev:
            wc = {}
            for key, label in (("projects", "projects"), ("members", "members")):
                old, new = self.prev.get(key) or {}, self.cur.get(key) or {}
                if not new:  # no se pudo leer: no reportar todo como eliminado
                    continue
                wc[f"{label}_added"] = [{"id": k, **new[k]} for k in new.keys() - old.keys()]
                wc[f"{label}_removed"] = [{"id": k, **old[k]} for k in old.keys() - new.keys()]
            changes["workspace_changes"] = wc

        # Nuevos
        new_uuids = [
            u for u, r in cur_repos.items() if u not in prev_repos and not r.get("carried")
        ]
        if cfg.on("NEW_REPOS"):
            with ThreadPoolExecutor(max_workers=max(1, cfg.ai_workers)) as pool:
                changes["new_repos"] = list(
                    pool.map(lambda u: self._new_repo(cur_repos[u]), new_uuids)
                )
            changes["new_repos"].sort(key=lambda r: r.get("created_on") or "", reverse=True)

        # Eliminados
        if cfg.on("DELETED_REPOS") and self.prev:
            for uuid, old in prev_repos.items():
                if uuid not in inventory:
                    changes["deleted_repos"].append(self._deleted_repo(old))

        # Repos existentes
        existing = [u for u in cur_repos if u in prev_repos and not cur_repos[u].get("carried")]
        if cfg.on("REPO_METADATA"):
            for u in existing:
                diffs = [
                    (label, prev_repos[u].get(f), cur_repos[u].get(f))
                    for f, label in META_FIELDS.items()
                    if prev_repos[u].get(f) != cur_repos[u].get(f)
                ]
                if diffs:
                    changes["metadata_changes"].append(
                        {"slug": cur_repos[u]["slug"], "changes": diffs}
                    )

        changed = [
            u
            for u in existing
            if not cur_repos[u].get("reused")
            and (cur_repos[u].get("git") or {}).get("ok")
            and (prev_repos[u].get("git") or {}).get("ok")
            and (
                (cur_repos[u]["git"].get("head") != prev_repos[u]["git"].get("head"))
                or cur_repos[u]["git"].get("branches") != prev_repos[u]["git"].get("branches")
            )
        ]
        with ThreadPoolExecutor(max_workers=max(1, cfg.ai_workers)) as pool:
            results = list(
                pool.map(lambda u: self._changed_repo(prev_repos[u], cur_repos[u]), changed)
            )
        changes["repo_changes"] = sorted(
            (r for r in results if r), key=lambda r: -r["commit_count"]
        )

        if cfg.on("AI_SUMMARY"):
            changes["summary"] = self._summary(changes)
        changes["errors"] += self.ai_errors
        return changes

    # --- repo nuevo ------------------------------------------------------
    def _new_repo(self, r: dict) -> dict:
        git = r.get("git") or {}
        out = {
            "slug": r["slug"],
            "name": r.get("name"),
            "html_url": r.get("html_url"),
            "created_on": r.get("created_on"),
            "language": r.get("language"),
            "description": r.get("description"),
            "project": r.get("project_name"),
            "is_private": r.get("is_private"),
            "size": r.get("size"),
            "first_commit": git.get("first_commit"),
            "last_commit": git.get("head_commit"),
            "commit_count": git.get("commit_count"),
            "branches": len(git.get("branches") or {}),
            "frameworks": r.get("frameworks") or [],
            "services": r.get("services") or {},
            "signals": r.get("signals") or {},
            "base_images": r.get("base_images") or [],
            "deps": {
                path: {
                    "ecosystem": m["ecosystem"],
                    "count": len(m.get("packages", {})),
                    "packages": sorted(m.get("packages", {})),
                }
                for path, m in (r.get("deps") or {}).items()
            },
            "error": None if git.get("ok") else git.get("error"),
            "audit": _audit_mentions(self.audit, r["uuid"], r["full_name"]),
        }
        if self.cfg.on("AI_REPO_PURPOSE") and git.get("ok"):
            out["ai"] = self._ask(
                self._purpose_prompt(r, out), PURPOSE_SCHEMA, f"propósito de {r['slug']}"
            )
        return out

    def _purpose_prompt(self, r: dict, info: dict) -> str:
        repo = open_repo(self.cfg, self.bb, r)
        files = repo.files()
        tree = sorted(p for p in files if p.count("/") <= 1)[:250]
        readme_path = next(
            (p for p in sorted(files, key=len) if re.fullmatch(r"(?i)readme(\.\w+)?", p)), None
        )
        readme = (repo.read(readme_path) or "")[:6000] if readme_path else "(sin README)"
        recent = [c.as_dict() for c in repo.log_range(None, limit=30)]
        try:
            contributors = repo.contributors()
        except GitError:
            contributors = []
        data = {
            k: info[k]
            for k in (
                "slug",
                "name",
                "description",
                "language",
                "project",
                "created_on",
                "first_commit",
                "commit_count",
                "frameworks",
                "services",
                "deps",
                "base_images",
            )
        }
        data["signals"] = {k: v for k, v in info["signals"].items() if v}
        return (
            "Analiza este repositorio de Bitbucket recién detectado y explica para qué sirve.\n"
            "Devuelve: purpose (2-4 frases: qué hace y para quién, intuyendo el motivo de su "
            "creación), "
            "kind (tipo: API, web, backoffice, app móvil, librería, microservicio, script, "
            "infraestructura, "
            "documentación, prueba de concepto...), stack (lenguajes, frameworks y piezas clave "
            "en una línea), "
            "notable (puntos llamativos: integraciones, uso de IA, tests, contenedores, "
            "calidad...), "
            "maturity (prototipo / en desarrollo / productivo / abandonado, con breve "
            "justificación).\n\n"
            f"## Datos\n```json\n{json.dumps(data, ensure_ascii=False, indent=1)[:20000]}\n```\n\n"
            f"## Colaboradores\n{json.dumps(contributors, ensure_ascii=False)}\n\n"
            f"## Últimos commits\n{json.dumps(recent, ensure_ascii=False)[:8000]}\n\n"
            f"## Árbol (2 niveles)\n{chr(10).join(tree)}\n\n## README\n{readme}\n"
        )

    # --- repo eliminado ------------------------------------------------------
    def _deleted_repo(self, old: dict) -> dict:
        git = old.get("git") or {}
        return {
            "slug": old["slug"],
            "name": old.get("name"),
            "description": old.get("description"),
            "project": old.get("project_name"),
            "created_on": old.get("created_on"),
            "last_seen": self.prev["taken_at"],
            "last_update": old.get("updated_on"),
            "last_commit": git.get("head_commit"),
            "commit_count": git.get("commit_count"),
            "frameworks": old.get("frameworks") or [],
            "language": old.get("language"),
            "audit": _audit_mentions(self.audit, old["uuid"], old.get("full_name", "")),
        }

    # --- repo con cambios ---------------------------------------------------
    def _changed_repo(self, old: dict, new: dict) -> dict | None:
        cfg = self.cfg
        repo = open_repo(cfg, self.bb, new)
        old_head = old["git"].get("head")
        res: dict[str, Any] = {
            "slug": new["slug"],
            "html_url": new.get("html_url"),
            "commit_count": 0,
            "commits": [],
            "authors": {},
            "history_rewritten": False,
        }
        try:
            if old_head and repo.has_commit(old_head) and repo.is_ancestor(old_head, "HEAD"):
                commits = repo.log_range(old_head)
                base = old_head
            else:  # force-push o cambio de rama principal: tiramos de fechas
                res["history_rewritten"] = True
                commits = repo.log_range(None, since=self.prev["taken_at"])
                base = commits[-1].hash + "^" if commits else None
            res["commit_count"] = len(commits)
            res["commits"] = [c.as_dict() for c in commits]
            for c in commits:
                res["authors"][c.author] = res["authors"].get(c.author, 0) + 1

            if cfg.on("LIBRARIES") and base:
                res |= self._libraries(repo, base, old.get("deps") or {}, new.get("deps") or {})
            if cfg.on("FRAMEWORKS"):
                res["frameworks_added"], res["frameworks_removed"] = _set_diff(
                    old.get("frameworks"), new.get("frameworks")
                )
            if cfg.on("EXTERNAL_SERVICES"):
                os_, ns = old.get("services") or {}, new.get("services") or {}
                res["sdks_added"], res["sdks_removed"] = _set_diff(os_.get("sdks"), ns.get("sdks"))
                res["hosts_added"], _ = _set_diff(os_.get("hosts"), ns.get("hosts"))
                res["images_added"], _ = _set_diff(os_.get("images"), ns.get("images"))
            if cfg.on("PRACTICES"):
                res["practices"] = self._practices(
                    repo, base, old.get("signals") or {}, new.get("signals") or {}
                )
            if cfg.on("INFRA"):
                res["infra"] = self._infra(repo, base, old, new)
            if cfg.on("CONFIG_PIPELINES"):
                res["config"] = self._config(
                    repo, base, old.get("config") or {}, new.get("config") or {}
                )
            if cfg.on("BRANCHES"):
                res |= self._branches(
                    repo, new, old["git"].get("branches") or {}, new["git"].get("branches") or {}
                )

            if cfg.on("AI_CHANGE_ANALYSIS") and (commits or res.get("branches_added")):
                diff, truncated = (
                    repo.diff(base, "HEAD", cfg.ai_max_diff_chars) if base else ("", False)
                )
                stat = repo.diff_stat(base, "HEAD") if base else ""
                res["ai"] = self._ask(
                    self._change_prompt(new, res, stat, diff, truncated),
                    CHANGE_SCHEMA,
                    f"cambios en {new['slug']}",
                )
        except GitError as e:
            res["error"] = str(e)
        if not (res["commit_count"] or res.get("branches_added") or res.get("error")):
            return None
        return res

    def _libraries(self, repo: Repo, base: str, old: dict, new: dict) -> dict:
        added, removed, upgraded = [], [], []
        for path, manifest in new.items():
            new_pk = manifest.get("packages", {})
            old_pk = (old.get(path) or {}).get("packages", {})
            new_manifest = path not in old
            for name in sorted(new_pk.keys() - old_pk.keys()):
                added.append(
                    {
                        "manifest": path,
                        "ecosystem": manifest["ecosystem"],
                        "name": name,
                        "version": new_pk[name],
                        "new_manifest": new_manifest,
                        "by": _who_added_package(repo, base, path, name),
                    }
                )
            for name in sorted(old_pk.keys() - new_pk.keys()):
                removed.append({"manifest": path, "name": name, "version": old_pk[name]})
            for name in sorted(new_pk.keys() & old_pk.keys()):
                if old_pk[name] != new_pk[name] and _major(old_pk[name]) != _major(new_pk[name]):
                    upgraded.append(
                        {"manifest": path, "name": name, "from": old_pk[name], "to": new_pk[name]}
                    )
        for path, manifest in old.items():
            if path not in new:
                removed += [
                    {"manifest": path, "name": n, "version": v, "manifest_removed": True}
                    for n, v in manifest.get("packages", {}).items()
                ]
        return {"libs_added": added, "libs_removed": removed, "libs_major_upgrades": upgraded}

    def _practices(self, repo: Repo, base: str | None, old: dict, new: dict) -> list[dict]:
        out = []
        ot, nt = old.get("test_files", 0), new.get("test_files", 0)
        if ot == 0 and nt > 0:
            out.append({"kind": "tests", "text": f"Aparecen los primeros tests ({nt} archivos)"})
        elif ot > 0 and nt == 0:
            out.append({"kind": "tests", "text": f"Desaparecen todos los tests (había {ot})"})
        elif nt - ot >= 10 and nt >= ot * 1.5:
            out.append({"kind": "tests", "text": f"Fuerte aumento de tests: {ot} → {nt} archivos"})

        code = max(new.get("code_files", 0), 1)
        od, nd = old.get("docblocks", 0), new.get("docblocks", 0)
        if nd - od >= 20 and od / max(old.get("code_files", 0), 1) < 0.5:
            out.append(
                {
                    "kind": "docs",
                    "text": f"Se empieza a documentar con docblocks: {od} → {nd} "
                    f"({nd / code:.1f} por archivo de código)",
                }
            )
        oty, nty = old.get("typed_docblocks", 0), new.get("typed_docblocks", 0)
        if nty - oty >= 30 and oty < 30:
            out.append(
                {"kind": "docs", "text": f"Aparecen anotaciones @param/@return: {oty} → {nty}"}
            )

        for kind, label in (("ai_tooling", "Tooling de IA"), ("quality", "Calidad / testing")):
            added, removed = _set_diff(old.get(kind), new.get(kind))
            for path in added:
                out.append(
                    {
                        "kind": kind,
                        "text": f"{label}: nuevo `{path}`",
                        "by": _who_added_file(repo, base, path),
                    }
                )
            for path in removed:
                out.append({"kind": kind, "text": f"{label}: eliminado `{path}`"})
        return out

    def _infra(self, repo: Repo, base: str | None, old: dict, new: dict) -> list[dict]:
        out = []
        os_, ns = old.get("signals") or {}, new.get("signals") or {}
        for kind, label in (
            ("containers", "Contenedores/virtualización"),
            ("iac", "Infraestructura como código"),
        ):
            added, removed = _set_diff(os_.get(kind), ns.get(kind))
            if added and not os_.get(kind):
                out.append(
                    {"kind": kind, "text": f"{label}: primera vez en el repo", "first": True}
                )
            for path in added:
                out.append(
                    {
                        "kind": kind,
                        "text": f"{label}: nuevo `{path}`",
                        "by": _who_added_file(repo, base, path),
                    }
                )
            for path in removed:
                out.append({"kind": kind, "text": f"{label}: eliminado `{path}`"})
        added, removed = _set_diff(old.get("base_images"), new.get("base_images"))
        for img in added:
            out.append({"kind": "image", "text": f"Nueva imagen base `{img}`"})
        for img in removed:
            out.append({"kind": "image", "text": f"Deja de usarse la imagen base `{img}`"})
        return out

    def _config(self, repo: Repo, base: str | None, old: dict, new: dict) -> list[dict]:
        out = []
        for path in sorted(old.keys() | new.keys()):
            if old.get(path) == new.get(path):
                continue
            status = (
                "nuevo" if path not in old else "eliminado" if path not in new else "modificado"
            )
            commits = (
                repo.log_range(base, paths=[path], limit=5)
                if base and status != "eliminado"
                else []
            )
            out.append({"path": path, "status": status, "commits": [c.as_dict() for c in commits]})
        return out

    def _branches(self, repo: Repo, new: dict, old_b: dict, new_b: dict) -> dict:
        main = new.get("mainbranch")
        added = []
        for name in sorted(new_b.keys() - old_b.keys()):
            info = {"name": name, **new_b[name], "commits_ahead": [], "files_changed": 0}
            if main and name != main:
                ahead = repo.log_range(f"origin/{main}", f"origin/{name}", limit=20)
                info["commits_ahead"] = [c.as_dict() for c in ahead]
                mb = repo.merge_base(f"origin/{main}", f"origin/{name}")
                if mb:
                    names = repo.diff_names(mb, f"origin/{name}")
                    info["files_changed"] = len(names)
                    info["sample_files"] = names[:25]
            added.append(info)
        return {"branches_added": added, "branches_removed": sorted(old_b.keys() - new_b.keys())}

    def _change_prompt(
        self, repo_meta: dict, res: dict, stat: str, diff: str, truncated: bool
    ) -> str:
        detected = {k: v for k, v in res.items() if k not in ("commits", "html_url") and v}
        return (
            f"Analiza los cambios del repositorio `{repo_meta['slug']}` desde el último informe "
            f"({self.prev['taken_at']}). Lenguaje: {repo_meta.get('language')}. "
            f"Frameworks: {', '.join(repo_meta.get('frameworks') or []) or '—'}.\n\n"
            "Devuelve:\n"
            "- summary: 2-4 frases sobre qué se ha hecho en este periodo y con qué intención "
            "aparente.\n"
            "- library_reasons: para cada biblioteca añadida, por qué parece haberse incorporado "
            "(mira el diff).\n"
            "- framework_notes: cambios de framework o sub-framework (migraciones, nuevos "
            "paquetes del "
            "ecosistema, cambios de versión mayor) si los hay.\n"
            "- external_services: servicios externos nuevos o NUEVAS interacciones con servicios "
            "ya usados "
            "(nuevos endpoints, webhooks, colas, buckets, APIs, variables de entorno de "
            "credenciales...). "
            "kind = 'nuevo servicio' o 'nueva interacción'.\n"
            "- new_features: funcionalidades nuevas. Marca hidden=true si parece oculta o no "
            "anunciada: "
            "detrás de feature flags, rutas/endpoints no enlazados, comandos o jobs nuevos, "
            "paneles de "
            "administración, código en ramas sin fusionar, funcionalidad deshabilitada o "
            "comentada, "
            "commits con mensajes que no describen lo que realmente hacen.\n"
            "- practice_changes: cambios en la forma de trabajar (tests, documentación/docblocks, "
            "tipado, "
            "linters, convenciones, uso deliberado de IA con archivos de configuración/arneses, "
            "etc.).\n"
            "- infra_config_changes: virtualización, contenedores, IaC, pipelines, despliegue, "
            "configuración.\n"
            "- attention: cosas que un CTO debería revisar (riesgos, secretos, malas prácticas, "
            "decisiones "
            "de arquitectura relevantes).\n\n"
            "## Detectado automáticamente\n```json\n"
            f"{json.dumps(detected, ensure_ascii=False, indent=1)[:25000]}\n```\n\n"
            f"## Commits ({res['commit_count']})\n"
            + "\n".join(
                f"- {c['date'][:10]} {c['author']}: {c['subject']}" for c in res["commits"][:200]
            )
            + f"\n\n## Estadística del diff\n```\n{stat[-6000:]}\n```\n\n"
            f"## Diff{' (truncado)' if truncated else ''}\n```diff\n{diff}\n```\n"
        )

    # --- resumen final --------------------------------------------------------
    def _summary(self, changes: dict) -> dict | None:
        def slim_repo(r: dict) -> dict:
            keep = {
                k: v
                for k, v in r.items()
                if v and k not in ("commits", "html_url", "deps", "signals")
            }
            keep["commits"] = [f"{c['author']}: {c['subject']}" for c in r.get("commits", [])[:15]]
            return keep

        def slim_new(r: dict) -> dict:
            keep = {k: v for k, v in r.items() if v and k not in ("deps", "html_url", "signals")}
            keep["deps_total"] = sum(d["count"] for d in r.get("deps", {}).values())
            keep["signals"] = {
                k: (len(v) if isinstance(v, list) else v) for k, v in r.get("signals", {}).items()
            }
            return keep

        data = {
            "baseline": changes["baseline"],
            "workspace_changes": changes["workspace_changes"],
            "new_repos": [slim_new(r) for r in changes["new_repos"]],
            "deleted_repos": changes["deleted_repos"],
            "metadata_changes": changes["metadata_changes"],
            "repo_changes": [slim_repo(r) for r in changes["repo_changes"]],
        }
        blob = json.dumps(data, ensure_ascii=False)
        if len(blob) > 400_000:
            blob = blob[:400_000] + "…(truncado)"
        if changes["baseline"]:
            intro = (
                "Este es el PRIMER informe (línea base): describe el panorama completo del "
                "workspace: "
                "qué tipos de proyectos hay, stacks dominantes, servicios externos, prácticas de "
                "trabajo (tests, docs, IA, contenedores, CI) y qué destaca. "
                "'deleted_repo_reasons' vacío."
            )
        else:
            intro = (
                "Resume los cambios del workspace en este periodo. En 'ways_of_working' describe "
                "cambios "
                "en la forma de trabajar (p. ej. empiezan a aparecer tests donde no había, "
                "docblocks, uso "
                "deliberado de IA con archivos de configuración y arneses, nuevas convenciones). "
                "En "
                "'infrastructure' virtualización, contenedores, pipelines, configuración. En "
                "'deleted_repo_reasons' intuye por qué se eliminó cada repo borrado (p. ej. si "
                "reaparece "
                "con otro nombre o se fusionó en otro)."
            )
        prompt = (
            f"{intro}\nheadline: una frase. highlights: 5-12 puntos clave, de más a menos "
            "importante. "
            "attention: lo que el CTO debería revisar.\n\n"
            f"## Cambios detectados\n```json\n{blob}\n```\n"
        )
        return self._ask(prompt, SUMMARY_SCHEMA, "resumen final")
