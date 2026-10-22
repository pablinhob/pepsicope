"""Render del informe Markdown."""

from __future__ import annotations

from typing import Any

from periscope.config import Config


def _d(iso: str | None) -> str:
    return iso.replace("T", " ")[:16] if iso else "—"


def _esc(text: Any) -> str:
    if isinstance(text, bool):
        return "sí" if text else "no"
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _commit_link(html_url: str | None, c: dict | None) -> str:
    if not c:
        return "—"
    short = c["hash"][:8]
    ref = f"[`{short}`]({html_url}/commits/{c['hash']})" if html_url else f"`{short}`"
    return f"{_esc(c['author'])}, {_d(c['date'])} ({ref})"


def _repo_link(r: dict) -> str:
    return f"[{r['slug']}]({r['html_url']})" if r.get("html_url") else f"`{r['slug']}`"


def baseline_notice(repos: int) -> list[str]:
    """Aviso del informe de línea base, como aviso de GitHub (`> [!WARNING]`).

    GitHub, la vista previa de VS Code y el HTML lo muestran destacado.
    """
    return [
        "> [!WARNING]",
        "> **Primer informe (línea base): no hay ningún informe anterior con el que comparar.**",
        f"> Por eso recoge todo lo que existe hoy en el workspace ({repos} repositorios) y no "
        "solo cambios. Nada de lo que aparece aquí es necesariamente nuevo ni reciente: es el "
        "estado actual, con la historia completa de cada repo.",
        "> A partir del próximo informe solo se mostrarán las diferencias respecto a este.",
    ]


class Report:
    def __init__(self, cfg: Config, changes: dict, stats: dict):
        self.cfg, self.c, self.stats = cfg, changes, stats
        self.lines: list[str] = []

    def w(self, *lines: str) -> None:
        self.lines.extend(lines)

    def render(self) -> str:
        c, cfg = self.c, self.cfg
        title = "Línea base" if c["baseline"] else "Cambios"
        self.w(f"# Periscope · {cfg.workspace} · {title} · {_d(c['until'])}", "")
        if c["baseline"]:
            self._baseline_notice()
        else:
            self.w(f"> Periodo analizado: **{_d(c['since'])} → {_d(c['until'])}**", "")
        self._overview()
        if cfg.on("WORKSPACE"):
            self._workspace()
        if cfg.on("NEW_REPOS"):
            self._new_repos()
        if cfg.on("DELETED_REPOS") and not c["baseline"]:
            self._deleted_repos()
        if cfg.on("REPO_METADATA") and not c["baseline"]:
            self._metadata()
        if not c["baseline"]:
            self._activity()
            if cfg.on("LIBRARIES"):
                self._libraries()
            if cfg.on("FRAMEWORKS"):
                self._frameworks()
            if cfg.on("EXTERNAL_SERVICES"):
                self._services()
            self._features()
            if cfg.on("PRACTICES"):
                self._practices()
            if cfg.on("INFRA"):
                self._infra()
            if cfg.on("CONFIG_PIPELINES"):
                self._config()
        self._summary()
        self._appendix()
        return "\n".join(self.lines).rstrip() + "\n"

    # --- secciones ----------------------------------------------------------
    def _baseline_notice(self) -> None:
        self.w(*baseline_notice(len(self.c["new_repos"])), "")

    def _overview(self) -> None:
        c = self.c
        rows = [("Repositorios en el workspace", c["repo_total"])]
        if c["baseline"]:
            rows.append(("Repositorios analizados", len(c["new_repos"])))
        else:
            rows += [
                ("Repositorios nuevos", len(c["new_repos"])),
                ("Repositorios eliminados", len(c["deleted_repos"])),
                ("Repositorios con actividad", len(c["repo_changes"])),
                (
                    "Commits nuevos (rama principal)",
                    sum(r["commit_count"] for r in c["repo_changes"]),
                ),
                (
                    "Bibliotecas añadidas",
                    sum(len(r.get("libs_added", [])) for r in c["repo_changes"]),
                ),
                ("Ramas nuevas", sum(len(r.get("branches_added", [])) for r in c["repo_changes"])),
            ]
        self.w("| Indicador | Valor |", "|---|---:|", *[f"| {k} | {v} |" for k, v in rows], "")
        headline = (c.get("summary") or {}).get("headline")
        if headline:
            self.w(f"**En una frase:** {headline}", "")

    def _workspace(self) -> None:
        wc = self.c.get("workspace_changes") or {}
        if not any(wc.values()):
            return
        self.w("## Workspace", "")
        labels = {
            "projects_added": "Proyectos nuevos",
            "projects_removed": "Proyectos eliminados",
            "members_added": "Miembros nuevos",
            "members_removed": "Miembros que ya no están",
        }
        for key, label in labels.items():
            items = wc.get(key) or []
            if items:
                self.w(
                    f"**{label}:** "
                    + ", ".join(
                        (
                            f"{_esc(i.get('name'))} (`{i['id']}`)"
                            if "projects" in key
                            else _esc(i.get("name"))
                        )
                        for i in items
                    ),
                    "",
                )

    def _new_repos(self) -> None:
        repos = self.c["new_repos"]
        title = "Inventario de repositorios" if self.c["baseline"] else "Repositorios nuevos"
        self.w(f"## {title} ({len(repos)})", "")
        if not repos:
            self.w("_Ninguno._", "")
            return
        self.w(
            "| Repo | Proyecto | Lenguaje | Creado | Creador (1er commit) | Frameworks | Último "
            "commit |",
            "|---|---|---|---|---|---|---|",
        )
        for r in repos:
            fc, lc = r.get("first_commit") or {}, r.get("last_commit") or {}
            self.w(
                f"| {_repo_link(r)} | {_esc(r.get('project'))} | {_esc(r.get('language'))} | "
                f"{_d(r.get('created_on'))[:10]} | {_esc(fc.get('author'))} | "
                f"{_esc(', '.join(r.get('frameworks') or []))} | {_d(lc.get('date'))[:10]} |"
            )
        self.w("")
        for r in repos:
            self._new_repo_detail(r)

    def _new_repo_detail(self, r: dict) -> None:
        self.w(f"### {r['slug']}", "")
        ai = r.get("ai") or {}
        if ai.get("purpose"):
            self.w(f"**Para qué sirve:** {ai['purpose']}", "")
        facts = [
            (
                "Creado",
                f"{_d(r.get('created_on'))} · primer commit: "
                f"{_commit_link(r.get('html_url'), r.get('first_commit'))}",
            ),
            ("Tipo", ai.get("kind")),
            ("Stack", ai.get("stack")),
            ("Madurez", ai.get("maturity")),
            ("Proyecto", r.get("project")),
            ("Descripción", r.get("description")),
            ("Visibilidad", "privado" if r.get("is_private") else "**público**"),
            (
                "Actividad",
                f"{r.get('commit_count') or 0} commits, {r.get('branches') or 0} ramas, "
                f"último: {_commit_link(r.get('html_url'), r.get('last_commit'))}",
            ),
            ("Frameworks", ", ".join(r.get("frameworks") or [])),
        ]
        svc = r.get("services") or {}
        facts.append(("Servicios externos (SDK)", ", ".join(svc.get("sdks") or [])))
        facts.append(("Imágenes docker-compose", ", ".join(svc.get("images") or [])))
        sig = r.get("signals") or {}
        facts += [
            (
                "Tests",
                f"{sig.get('test_files', 0)} archivos de test / {sig.get('code_files', 0)} de "
                "código",
            ),
            (
                "Docblocks",
                f"{sig.get('docblocks', 0)} (con @param/@return: {sig.get('typed_docblocks', 0)})",
            ),
            ("CI/CD", ", ".join(f"`{p}`" for p in sig.get("ci") or [])),
            ("Contenedores", ", ".join(f"`{p}`" for p in (sig.get("containers") or [])[:8])),
            ("IaC", ", ".join(f"`{p}`" for p in (sig.get("iac") or [])[:8])),
            ("Tooling de IA", ", ".join(f"`{p}`" for p in (sig.get("ai_tooling") or [])[:10])),
            ("Calidad", ", ".join(f"`{p}`" for p in (sig.get("quality") or [])[:10])),
            ("Imágenes base", ", ".join(f"`{i}`" for i in r.get("base_images") or [])),
        ]
        for a in r.get("audit") or []:
            facts.append(("Audit log", f"{a['action']} por {a['actor']} el {_d(a['time'])}"))
        if r.get("error"):
            facts.append(("⚠️ Error", r["error"]))
        for k, v in facts:
            if v:
                self.w(f"- **{k}:** {v}")
        for n in ai.get("notable") or []:
            self.w(f"- {n}")
        if r.get("deps"):
            self.w("", "<details><summary>Dependencias</summary>", "")
            for path, m in r["deps"].items():
                self.w(
                    f"- `{path}` ({m['ecosystem']}, {m['count']}): {', '.join(m['packages'][:80])}"
                    + (" …" if m["count"] > 80 else "")
                )
            self.w("", "</details>")
        hosts = svc.get("hosts") or []
        if hosts:
            self.w(
                "",
                f"<details><summary>Hosts externos en el código ({len(hosts)})</summary>",
                "",
                ", ".join(hosts[:150]),
                "",
                "</details>",
            )
        self.w("")

    def _deleted_repos(self) -> None:
        repos = self.c["deleted_repos"]
        self.w(f"## Repositorios eliminados ({len(repos)})", "")
        if not repos:
            self.w("_Ninguno._", "")
            return
        reasons = {
            x["slug"]: x["reason"]
            for x in (self.c.get("summary") or {}).get("deleted_repo_reasons", [])
        }
        self.w(
            "| Repo | Proyecto | Creado | Visto por última vez | Último commit | Quién lo borró | "
            "Motivo probable |",
            "|---|---|---|---|---|---|---|",
        )
        for r in repos:
            who = (
                "; ".join(f"{a['actor']} ({_d(a['time'])})" for a in r.get("audit") or [])
                or "desconocido (activa FEATURE_AUDIT_LOG)"
            )
            lc = r.get("last_commit") or {}
            self.w(
                f"| `{r['slug']}` | {_esc(r.get('project'))} | {_d(r.get('created_on'))[:10]} | "
                f"{_d(r.get('last_seen'))} | {_esc(lc.get('author'))} {_d(lc.get('date'))[:10]} | "
                f"{_esc(who)} | {_esc(reasons.get(r['slug'], '—'))} |"
            )
        self.w(
            "",
            "_La fecha exacta de borrado está entre «visto por última vez» y este informe._",
            "",
        )

    def _metadata(self) -> None:
        items = self.c["metadata_changes"]
        if not items:
            return
        self.w("## Cambios en repositorios (metadatos)", "")
        for it in items:
            for label, old, new in it["changes"]:
                warn = " ⚠️" if label == "privado" and new is False else ""
                self.w(
                    f"- `{it['slug']}`: **{label}** {_esc(old) or '∅'} → {_esc(new) or '∅'}{warn}"
                )
        self.w("")

    def _activity(self) -> None:
        repos = self.c["repo_changes"]
        self.w(f"## Actividad por repositorio ({len(repos)})", "")
        if not repos:
            self.w("_Sin actividad en el periodo._", "")
            return
        self.w("| Repo | Commits | Autores | Ramas nuevas |", "|---|---:|---|---:|")
        for r in repos:
            authors = ", ".join(
                f"{a} ({n})" for a, n in sorted(r["authors"].items(), key=lambda x: -x[1])
            )
            self.w(
                f"| {_repo_link(r)} | {r['commit_count']} | {_esc(authors)} | "
                f"{len(r.get('branches_added', []))} |"
            )
        self.w("")
        for r in repos:
            summary = (r.get("ai") or {}).get("summary")
            if summary or r.get("history_rewritten") or r.get("error"):
                self.w(
                    f"- **{r['slug']}**: {summary or ''}"
                    + (
                        " ⚠️ _historia reescrita (force-push o cambio de rama principal)_"
                        if r.get("history_rewritten")
                        else ""
                    )
                    + (f" ⚠️ error: {r['error']}" if r.get("error") else "")
                )
        self.w("")

    def _libraries(self) -> None:
        repos = [
            r
            for r in self.c["repo_changes"]
            if r.get("libs_added") or r.get("libs_removed") or r.get("libs_major_upgrades")
        ]
        self.w("## Bibliotecas", "")
        if not repos:
            self.w("_Sin cambios de dependencias._", "")
            return
        added = [(r, lib) for r in repos for lib in r.get("libs_added", [])]
        if added:
            self.w(
                f"### Añadidas ({len(added)})",
                "",
                "| Repo | Biblioteca | Versión | Manifiesto | Quién / cuándo | Motivo probable |",
                "|---|---|---|---|---|---|",
            )
            for r, lib in added:
                reasons = {
                    x["name"]: x["reason"] for x in (r.get("ai") or {}).get("library_reasons", [])
                }
                self.w(
                    f"| `{r['slug']}` | **{_esc(lib['name'])}** | {_esc(lib['version'])} | "
                    f"`{lib['manifest']}`"
                    f"{' (nuevo)' if lib.get('new_manifest') else ''} | "
                    f"{_commit_link(r.get('html_url'), lib.get('by'))} "
                    f"| {_esc(reasons.get(lib['name'], ''))} |"
                )
            self.w("")
        upgrades = [(r, u) for r in repos for u in r.get("libs_major_upgrades", [])]
        if upgrades:
            self.w(f"### Actualizaciones de versión mayor ({len(upgrades)})", "")
            for r, u in upgrades:
                self.w(
                    f"- `{r['slug']}`: **{u['name']}** {u['from']} → {u['to']} (`{u['manifest']}`)"
                )
            self.w("")
        removed = [(r, lib) for r in repos for lib in r.get("libs_removed", [])]
        if removed:
            self.w(f"### Eliminadas ({len(removed)})", "")
            by_repo: dict[str, list[str]] = {}
            for r, lib in removed:
                by_repo.setdefault(r["slug"], []).append(lib["name"])
            for slug, names in by_repo.items():
                self.w(f"- `{slug}`: {', '.join(names)}")
            self.w("")

    def _frameworks(self) -> None:
        lines = []
        for r in self.c["repo_changes"]:
            if r.get("frameworks_added"):
                lines.append(f"- `{r['slug']}`: **añade** {', '.join(r['frameworks_added'])}")
            if r.get("frameworks_removed"):
                lines.append(
                    f"- `{r['slug']}`: **deja de usar** {', '.join(r['frameworks_removed'])}"
                )
            for note in (r.get("ai") or {}).get("framework_notes", []):
                lines.append(f"- `{r['slug']}`: {note}")
        self._section("Frameworks y sub-frameworks", lines)

    def _services(self) -> None:
        lines = []
        for r in self.c["repo_changes"]:
            if r.get("sdks_added"):
                lines.append(f"- `{r['slug']}`: nuevos SDK de **{', '.join(r['sdks_added'])}**")
            if r.get("sdks_removed"):
                lines.append(
                    f"- `{r['slug']}`: deja de usar SDK de {', '.join(r['sdks_removed'])}"
                )
            if r.get("images_added"):
                lines.append(
                    f"- `{r['slug']}`: nuevos servicios en docker-compose: "
                    f"{', '.join(r['images_added'])}"
                )
            if r.get("hosts_added"):
                hosts = r["hosts_added"]
                lines.append(
                    f"- `{r['slug']}`: nuevos hosts en el código: {', '.join(hosts[:25])}"
                    + (f" (+{len(hosts) - 25})" if len(hosts) > 25 else "")
                )
            for s in (r.get("ai") or {}).get("external_services", []):
                lines.append(
                    f"- `{r['slug']}`: **{s['service']}** ({s['kind']}) — {s['intent']}"
                    + (f" _Evidencia: {s['evidence']}_" if s.get("evidence") else "")
                )
        self._section("Servicios externos", lines)

    def _features(self) -> None:
        lines = []
        for r in self.c["repo_changes"]:
            for f in (r.get("ai") or {}).get("new_features", []):
                tag = " **[posible funcionalidad oculta]**" if f.get("hidden") else ""
                who = f" — {', '.join(f['authors'])}" if f.get("authors") else ""
                lines.append(
                    f"- `{r['slug']}`: **{f['title']}**{tag}{who}. {f['description']}"
                    + (f" _Evidencia: {f['evidence']}_" if f.get("evidence") else "")
                )
        if self.cfg.on("BRANCHES"):
            for r in self.c["repo_changes"]:
                for b in r.get("branches_added", []):
                    subjects = "; ".join(c["subject"] for c in b.get("commits_ahead", [])[:5])
                    lines.append(
                        f"- `{r['slug']}`: rama nueva **`{b['name']}`** de "
                        f"{_esc(b.get('author'))} "
                        f"({_d(b.get('date'))}), {len(b.get('commits_ahead', []))} commits sin "
                        "fusionar, "
                        f"{b.get('files_changed', 0)} archivos tocados"
                        + (f". Commits: _{_esc(subjects)}_" if subjects else "")
                    )
        hidden = sorted(lines, key=lambda line: "oculta" not in line)
        self._section("Funcionalidades nuevas y trabajo en curso", hidden)

    def _practices(self) -> None:
        lines = []
        for r in self.c["repo_changes"]:
            for p in r.get("practices", []):
                by = f" ({_commit_link(r.get('html_url'), p['by'])})" if p.get("by") else ""
                lines.append(f"- `{r['slug']}`: {p['text']}{by}")
            for p in (r.get("ai") or {}).get("practice_changes", []):
                lines.append(f"- `{r['slug']}`: **{p['title']}** — {p['description']}")
        self._section("Forma de trabajar", lines)

    def _infra(self) -> None:
        lines = []
        for r in self.c["repo_changes"]:
            for p in r.get("infra", []):
                by = f" ({_commit_link(r.get('html_url'), p['by'])})" if p.get("by") else ""
                bold = "**" if p.get("first") else ""
                lines.append(f"- `{r['slug']}`: {bold}{p['text']}{bold}{by}")
        self._section("Virtualización e infraestructura", lines)

    def _config(self) -> None:
        lines = []
        for r in self.c["repo_changes"]:
            for f in r.get("config", []):
                who = _commit_link(r.get("html_url"), f["commits"][0]) if f.get("commits") else ""
                lines.append(
                    f"- `{r['slug']}`: `{f['path']}` {f['status']}"
                    + (f" — último cambio: {who}" if who else "")
                )
            for p in (r.get("ai") or {}).get("infra_config_changes", []):
                lines.append(f"- `{r['slug']}`: **{p['title']}** — {p['description']}")
        self._section("Configuración y pipelines", lines)

    def _summary(self) -> None:
        s = self.c.get("summary")
        attention = [
            a
            for r in self.c["repo_changes"]
            for a in (f"`{r['slug']}`: {x}" for x in (r.get("ai") or {}).get("attention", []))
        ]
        if not s and not attention:
            return
        self.w("## Resumen final", "")
        if s:
            if s.get("highlights"):
                self.w("### Lo más importante", "", *[f"- {h}" for h in s["highlights"]], "")
            for key, title in (
                ("ways_of_working", "Cambios en la forma de trabajar"),
                ("infrastructure", "Infraestructura, virtualización y configuración"),
            ):
                if s.get(key):
                    self.w(f"### {title}", "")
                    for item in s[key]:
                        repos = f" _({', '.join(item['repos'])})_" if item.get("repos") else ""
                        self.w(f"- **{item['title']}**: {item['description']}{repos}")
                    self.w("")
            # El resumen global ya integra lo que cada repo marcó como «a revisar»
            attention = list(s.get("attention") or []) or attention
        if attention:
            self.w("### Para revisar", "", *[f"- {a}" for a in attention], "")

    def _appendix(self) -> None:
        st = self.stats
        self.w("---", "", "<details><summary>Detalles de la ejecución</summary>", "")
        active = [k for k, v in self.cfg.features.items() if v]
        self.w(
            f"- Duración: {st.get('duration')}",
            f"- IA: {st.get('ai_backend')} · modelo `{st.get('ai_model')}` · "
            f"{st.get('ai_calls', 0)} llamadas · "
            f"{st.get('ai_cache_hits', 0)} desde caché",
            f"- Funcionalidades activas: {', '.join(active)}",
            f"- Snapshot: `{st.get('snapshot')}`",
        )
        if self.c.get("errors"):
            self.w("", "**Incidencias:**", "", *[f"- {_esc(e)}" for e in self.c["errors"][:200]])
        self.w("", "</details>")

    def _section(self, title: str, lines: list[str]) -> None:
        self.w(f"## {title}", "")
        self.w(*(lines or ["_Sin novedades._"]), "")


def render(cfg: Config, changes: dict, stats: dict) -> str:
    return Report(cfg, changes, stats).render()
