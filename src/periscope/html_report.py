"""Conversión del informe Markdown en una página HTML autocontenida, con filtros.

El HTML se genera a partir del Markdown que produce `report.py`, así que solo hay una fuente
de verdad y cualquier informe existente se puede convertir. La página no usa recursos
externos: el CSS y el JavaScript van dentro del propio fichero.

El contenido del informe incluye texto de los repos y de la IA (dato no confiable): todo se
escapa y solo se admiten enlaces http(s).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from periscope.report import baseline_notice

HTML_SUFFIX = ".html"

_REPO_PARTS = ("Inventario de repositorios", "Repositorios nuevos")
_SLUG = r"[A-Za-z0-9][\w.\-]*"
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])_(?=\S)(.+?)(?<=\S)_(?!\w)")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# Avisos de GitHub: «> [!WARNING]» en la primera línea de una cita
_ALERT = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]$")
_ALERT_TITLES = {
    "note": "Nota",
    "tip": "Consejo",
    "important": "Importante",
    "warning": "Atención",
    "caution": "Cuidado",
}
_LEGACY_BASELINE = "**Primer informe (línea base).**"
_LEAD_REPO = re.compile(rf"^(?:`({_SLUG})`|\*\*({_SLUG})\*\*):")
_CELL_REPO = re.compile(rf"^(?:\[({_SLUG})\]\(|`({_SLUG})`)")
_DETAILS_OPEN = re.compile(r"^<details><summary>(.*)</summary>$")
_TABLE_SEP = re.compile(r"^\|(\s*:?-+:?\s*\|)+$")


def html_path(md_path: Path) -> Path:
    """Ruta del HTML de un informe: el mismo nombre con `.html` al final (`x.md.html`)."""
    return md_path.with_name(md_path.name + HTML_SUFFIX)


def write_html(md_path: Path) -> Path:
    """Genera el HTML de un informe Markdown existente y devuelve su ruta."""
    out = html_path(md_path)
    out.write_text(render_html(md_path.read_text()))
    return out


def render_html(markdown: str) -> str:
    """Convierte un informe Markdown de Periscope en una página HTML autocontenida."""
    lines = markdown.splitlines()
    known = _known_repos(lines)
    body = _Builder(known).build(lines)
    meta = _repo_meta(lines)
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "Periscope")
    return _PAGE.format(
        title=_esc(title),
        css=_CSS,
        js=_JS,
        header=body.header,
        toc=body.toc,
        parts=body.parts,
        footer=body.footer,
        meta=_esc_script(json.dumps(meta, ensure_ascii=False)),
    )


# --- inline ---------------------------------------------------------------------------
def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _esc_script(text: str) -> str:
    """JSON seguro dentro de <script>: impide cerrar la etiqueta desde los datos."""
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _inline(text: str) -> str:
    """Formato en línea del subconjunto de Markdown que usa el informe."""
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = text.replace("\\|", "|")
    text = _CODE.sub(lambda m: keep(f"<code>{_esc(m.group(1))}</code>"), text)
    text = _LINK.sub(
        lambda m: keep(f'<a href="{_esc(m.group(2))}">{_emphasis(_esc(m.group(1)))}</a>'), text
    )
    # Las fechas no se parten por el guion al estrechar las tablas
    text = _DATE.sub(r'<span class="nw">\1</span>', _emphasis(_esc(text)))
    while _PLACEHOLDER.search(text):
        text = _PLACEHOLDER.sub(lambda m: stash[int(m.group(1))], text)
    return text


def _emphasis(text: str) -> str:
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)


# --- análisis previo --------------------------------------------------------------------
def _cells(line: str) -> list[str]:
    inner = line.strip()[1:-1] if line.strip().endswith("|") else line.strip()[1:]
    return [c.strip() for c in re.split(r"(?<!\\)\|", inner)]


def _cell_repo(cell: str) -> str | None:
    m = _CELL_REPO.match(cell)
    return (m.group(1) or m.group(2)) if m else None


def _repo_tables(lines: list[str]) -> list[tuple[list[str], list[list[str]]]]:
    """Tablas cuya primera columna es «Repo»: cabecera y filas."""
    tables = []
    for i, line in enumerate(lines[:-1]):
        if not (line.startswith("|") and _TABLE_SEP.match(lines[i + 1].strip())):
            continue
        head = _cells(line)
        if head and head[0] == "Repo":
            rows = []
            for row in lines[i + 2 :]:
                if not row.startswith("|"):
                    break
                rows.append(_cells(row))
            tables.append((head, rows))
    return tables


def _known_repos(lines: list[str]) -> set[str]:
    """Slugs de repos que aparecen en el informe, para etiquetar cada elemento."""
    known = {
        repo for _, rows in _repo_tables(lines) for row in rows if (repo := _cell_repo(row[0]))
    }
    in_repo_part = False
    for line in lines:
        if line.startswith("## "):
            in_repo_part = line[3:].startswith(_REPO_PARTS)
        elif line.startswith("### ") and in_repo_part:
            known.add(line[4:].strip())
        elif line.startswith("- ") and (m := _LEAD_REPO.match(line[2:])):
            known.add(m.group(1) or m.group(2))
    return known


def _repo_meta(lines: list[str]) -> dict[str, dict[str, str]]:
    """Proyecto y lenguaje de cada repo, sacados de las tablas de inventario."""
    meta: dict[str, dict[str, str]] = {}
    for head, rows in _repo_tables(lines):
        cols = {name: i for i, name in enumerate(head)}
        for row in rows:
            repo = _cell_repo(row[0])
            if not repo:
                continue
            entry = meta.setdefault(repo, {})
            for key, col in (("project", "Proyecto"), ("language", "Lenguaje")):
                if col in cols and cols[col] < len(row) and row[cols[col]]:
                    entry[key] = row[cols[col]]
    return meta


# --- construcción del cuerpo ------------------------------------------------------------
def _row(cells: list[str], aligns: list[str]) -> str:
    out = []
    for n, cell in enumerate(cells):
        cls = f' class="{aligns[n]}"' if n < len(aligns) and aligns[n] else ""
        out.append(f"<td{cls}>{_inline(cell)}</td>")
    return "".join(out)


@dataclass
class _Body:
    header: str = ""
    toc: str = ""
    parts: str = ""
    footer: str = ""


@dataclass
class _Builder:
    """Recorre el Markdown bloque a bloque y lo agrupa en secciones filtrables."""

    known: set[str]
    out: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    footer: list[str] = field(default_factory=list)
    toc: list[str] = field(default_factory=list)
    part_open: bool = False
    article_open: bool = False
    in_repo_part: bool = False
    in_footer: bool = False

    def build(self, lines: list[str]) -> _Body:
        """Construye la cabecera, el índice, las secciones y el apéndice."""
        i = 0
        while i < len(lines):
            i = self._block(lines, i)
        self._close_part()
        return _Body(
            "\n".join(self.header),
            "\n".join(self.toc),
            "\n".join(self.out),
            "\n".join(self.footer),
        )

    # -- bloques
    def _block(self, lines: list[str], i: int) -> int:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            return i + 1
        if line.startswith("# "):
            self.header.append(f"<h1>{_inline(line[2:].strip())}</h1>")
            return i + 1
        if line.startswith("## "):
            self._open_part(line[3:].strip())
            return i + 1
        if line.startswith("### "):
            self._heading3(line[4:].strip())
            return i + 1
        if stripped == "---":
            # Tras la raya va el apéndice de la ejecución: fuera de secciones y de filtros
            self._close_part()
            self.in_footer = True
            return i + 1
        if line.startswith(">"):
            return self._quote(lines, i)
        if line.startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            return self._table(lines, i)
        if line.startswith("- "):
            return self._list(lines, i)
        if m := _DETAILS_OPEN.match(stripped):
            self._emit(f"<details><summary>{_inline(m.group(1))}</summary>")
            return i + 1
        if stripped == "</details>":
            self._emit("</details>")
            return i + 1
        return self._paragraph(lines, i)

    def _paragraph(self, lines: list[str], i: int) -> int:
        chunk = []
        while i < len(lines) and lines[i].strip() and not self._starts_block(lines, i):
            chunk.append(lines[i].strip())
            i += 1
        self._emit(f"<p>{_inline(' '.join(chunk))}</p>")
        return i

    @staticmethod
    def _starts_block(lines: list[str], i: int) -> bool:
        line = lines[i]
        return (
            line.startswith(("#", ">", "- ", "|"))
            or line.strip() in ("---", "</details>")
            or bool(_DETAILS_OPEN.match(line.strip()))
        )

    def _quote(self, lines: list[str], i: int) -> int:
        chunk = []
        while i < len(lines) and lines[i].startswith(">"):
            chunk.append(lines[i].lstrip(">").strip())
            i += 1
        alert = _ALERT.match(chunk[0])
        if alert:
            kind = alert.group(1).lower()
            paragraphs = "".join(f"<p>{_inline(line)}</p>" for line in chunk[1:] if line)
            self._emit(
                f'<div class="callout {kind}" role="note">'
                f'<p class="callout-title">{_ALERT_TITLES[kind]}</p>{paragraphs}</div>'
            )
        elif chunk[0].startswith(_LEGACY_BASELINE):
            # Informes anteriores a los avisos [!WARNING]: el aviso de línea base era una cita
            # breve. Se muestra el aviso actual, que explica por qué hay tanta información.
            self._quote(baseline_notice(len(self.known)), 0)
        else:
            self._emit(f"<blockquote><p>{_inline(' '.join(chunk))}</p></blockquote>")
        return i

    def _list(self, lines: list[str], i: int) -> int:
        items = []
        while i < len(lines) and lines[i].startswith("- "):
            items.append(self._list_item(lines[i][2:].strip()))
            i += 1
        self._emit("<ul>", *items, "</ul>")
        return i

    def _list_item(self, text: str) -> str:
        if self.article_open or self._static:
            return f"<li>{_inline(text)}</li>"
        lead = _LEAD_REPO.match(text)
        repos = [lead.group(1) or lead.group(2)] if lead else self._mentions(text)
        return f"<li{self._filter_attrs(repos)}>{_inline(text)}</li>"

    def _table(self, lines: list[str], i: int) -> int:
        head = _cells(lines[i])
        aligns = ["num" if c.endswith(":") else "" for c in _cells(lines[i + 1])]
        repo_table = bool(head) and head[0] == "Repo"
        i += 2
        rows = []
        while i < len(lines) and lines[i].startswith("|"):
            cells = _cells(lines[i])
            rows.append(f"<tr{self._row_attrs(cells, repo_table)}>{_row(cells, aligns)}</tr>")
            i += 1
        ths = "".join(f"<th>{_inline(h)}</th>" for h in head)
        self._emit(
            '<div class="tablewrap"><table>',
            f"<thead><tr>{ths}</tr></thead>",
            "<tbody>",
            *rows,
            "</tbody></table></div>",
        )
        return i

    def _row_attrs(self, cells: list[str], repo_table: bool) -> str:
        if self._static or self.article_open:
            return ""
        if repo_table:
            repo = _cell_repo(cells[0])
            return self._filter_attrs([repo] if repo else [])
        return self._filter_attrs(self._mentions(" | ".join(cells)))

    # -- secciones
    @property
    def _static(self) -> bool:
        """Fuera de las secciones (cabecera y apéndice) no se filtra nada."""
        return not self.part_open

    def _open_part(self, title: str) -> None:
        self._close_part()
        anchor = f"s{len(self.toc) + 1}"
        self.in_repo_part = title.startswith(_REPO_PARTS)
        self.out.append(f'<section class="part" id="{anchor}">')
        self.out.append(f"<h2>{_inline(title)}</h2>")
        self.toc.append(f'<li><a href="#{anchor}" data-part="{anchor}">{_inline(title)}</a></li>')
        self.part_open = True

    def _close_part(self) -> None:
        self._close_article()
        if self.part_open:
            self.out.append("</section>")
            self.part_open = False

    def _heading3(self, title: str) -> None:
        self._close_article()
        if self.in_repo_part and title in self.known:
            self.out.append(f'<article class="repo"{self._filter_attrs([title])}>')
            self.out.append(f'<h3 id="repo-{_esc(title)}">{_esc(title)}</h3>')
            self.article_open = True
        else:
            self.out.append(f"<h3>{_inline(title)}</h3>")

    def _close_article(self) -> None:
        if self.article_open:
            self.out.append("</article>")
            self.article_open = False

    def _emit(self, *fragments: str) -> None:
        if self.part_open:
            self.out.extend(fragments)
        elif self.in_footer:
            self.footer.extend(fragments)
        else:
            self.header.extend(fragments)

    # -- etiquetas de filtro
    def _mentions(self, text: str) -> list[str]:
        """Repos conocidos que cita un elemento: en código, como enlace o en `_(a, b)_`."""
        found = {a or b for a, b in re.findall(rf"`({_SLUG})`|\[({_SLUG})\]\(", text)}
        for group in re.findall(r"_\(([^)]*)\)_", text):
            found.update(name.strip() for name in group.split(","))
        return sorted(found & self.known)

    @staticmethod
    def _filter_attrs(repos: list[str]) -> str:
        return f' data-filter data-repos="{_esc(" ".join(repos))}"'


# --- plantilla --------------------------------------------------------------------------
_CSS = """
:root {
  --bg: #f5f6f8; --surface: #ffffff; --fg: #1c232e; --muted: #5d6877; --line: #d3d9e1;
  --accent: #2357c6; --soft: #e9eef7; --warn: #9a5b00; --warn-soft: #fbf1e1;
  --code: #eef1f5;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12161c; --surface: #1a2029; --fg: #e5e9ef; --muted: #9aa5b3; --line: #343f4e;
    --accent: #84a9ff; --soft: #1f2a3b; --warn: #f0a94b; --warn-soft: #33270f;
    --code: #232b37;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.55 var(--sans);
}
.layout {
  max-width: 1180px; margin: 0 auto; padding: 24px 16px 64px;
  display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 28px;
}
header.top { grid-column: 1 / -1; display: grid; gap: 10px; }
h1 { font-size: 1.7rem; line-height: 1.25; margin: 0; text-wrap: balance; }
h2 { font-size: 1.3rem; margin: 0 0 12px; text-wrap: balance; }
h3 { font-size: 1.05rem; margin: 22px 0 8px; }
p { margin: 0 0 12px; max-width: 80ch; }
a { color: var(--accent); }
code {
  font: 0.86em var(--mono); background: var(--code); padding: 1px 5px; border-radius: 4px;
  overflow-wrap: anywhere;
}
blockquote {
  margin: 0; padding: 10px 14px; border-left: 3px solid var(--accent);
  background: var(--soft); border-radius: 0 6px 6px 0;
}
blockquote p { margin: 0; }
.callout {
  padding: 14px 18px; border: 1px solid var(--line); border-left: 4px solid var(--accent);
  background: var(--soft); border-radius: 8px; display: grid; gap: 6px;
}
.callout p { margin: 0; }
.callout-title {
  font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--accent);
}
.callout.warning, .callout.caution, .callout.important {
  border-left-color: var(--warn); background: var(--warn-soft);
}
.callout.warning .callout-title, .callout.caution .callout-title,
.callout.important .callout-title { color: var(--warn); }
hr { border: 0; border-top: 1px solid var(--line); margin: 24px 0; }
ul { margin: 0 0 12px; padding-left: 1.3em; display: grid; gap: 4px; }
details { margin: 8px 0; }
summary { cursor: pointer; color: var(--muted); }
.tablewrap {
  overflow-x: auto; background: var(--surface); border: 1px solid var(--line);
  border-radius: 8px; margin: 0 0 14px;
}
table { border-collapse: collapse; width: 100%; font-size: 0.93rem; }
th, td { text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--line); }
th {
  font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted);
  font-weight: 600; white-space: nowrap; position: sticky; top: 0; background: var(--surface);
}
tbody tr:last-child td { border-bottom: 0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td:first-child, .nw { white-space: nowrap; }
nav.toc { position: sticky; top: 16px; align-self: start; display: grid; gap: 14px; }
nav.toc ul { list-style: none; padding: 0; gap: 2px; }
nav.toc a {
  display: block; padding: 4px 8px; border-radius: 6px; text-decoration: none;
  color: var(--fg); font-size: 0.92rem;
}
nav.toc a:hover, nav.toc a:focus-visible { background: var(--soft); }
nav.toc a.empty { color: var(--muted); }
.filters {
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px; display: grid; gap: 10px;
}
.filters label { display: grid; gap: 3px; font-size: 0.8rem; color: var(--muted); }
.filters select, .filters input[type="search"] {
  font: inherit; font-size: 0.92rem; color: var(--fg); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 5px 8px; width: 100%;
}
.filters .check { display: flex; gap: 6px; align-items: center; color: var(--fg); }
.filters button {
  font: inherit; font-size: 0.88rem; border: 1px solid var(--line); background: var(--bg);
  color: var(--fg); border-radius: 6px; padding: 5px 8px; cursor: pointer;
}
.count { font-size: 0.82rem; color: var(--muted); }
main { min-width: 0; display: grid; gap: 28px; }
section.part {
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 18px 20px;
}
section.part .tablewrap { border-radius: 6px; }
article.repo { border-top: 1px solid var(--line); padding-top: 4px; }
article.repo h3 { font-family: var(--mono); font-size: 1rem; }
.none { color: var(--muted); font-style: italic; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 820px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
  nav.toc { position: static; }
}
@media print {
  nav.toc { display: none; }
  .layout { display: block; }
  section.part { break-inside: auto; border: 0; padding: 0; margin-bottom: 18px; }
}
"""

_JS = """
(() => {
  const meta = JSON.parse(document.getElementById("repo-meta").textContent);
  const $ = (id) => document.getElementById(id);
  const items = [...document.querySelectorAll("[data-filter]")];
  const repoOf = (el) => el.dataset.repos ? el.dataset.repos.split(" ") : [];
  const repos = [...new Set(items.flatMap(repoOf))].sort();
  const values = (key) =>
    [...new Set(Object.values(meta).map((m) => m[key]).filter(Boolean))].sort();
  const fill = (select, options) => {
    for (const value of options) select.add(new Option(value, value));
    if (!options.length) select.closest("label").hidden = true;
  };
  fill($("f-repo"), repos);
  fill($("f-project"), values("project"));
  fill($("f-language"), values("language"));
  const total = items.length;

  function allowed() {
    const repo = $("f-repo").value, project = $("f-project").value;
    const language = $("f-language").value;
    if (!repo && !project && !language) return null;
    return new Set(repos.filter((r) =>
      (!repo || r === repo) &&
      (!project || (meta[r] || {}).project === project) &&
      (!language || (meta[r] || {}).language === language)));
  }

  function apply() {
    const keep = allowed();
    const text = $("f-text").value.trim().toLowerCase();
    const warn = $("f-warn").checked;
    let shown = 0;
    for (const el of items) {
      const own = repoOf(el);
      const okRepo = !keep || own.some((r) => keep.has(r));
      const content = el.textContent;
      const ok = okRepo && (!text || content.toLowerCase().includes(text)) &&
        (!warn || content.includes("\\u26a0"));
      el.hidden = !ok;
      if (ok) shown += 1;
    }
    for (const group of document.querySelectorAll("main ul, main .tablewrap")) {
      const rows = group.querySelectorAll(":scope > li[data-filter], tbody > tr[data-filter]");
      if (rows.length) group.hidden = [...rows].every((r) => r.hidden);
    }
    for (const part of document.querySelectorAll("section.part")) {
      const own = part.querySelectorAll("[data-filter]");
      const empty = own.length > 0 && [...own].every((el) => el.hidden);
      const filtering = keep || text || warn;
      part.hidden = Boolean(filtering) && (own.length === 0 || empty);
      const link = document.querySelector(`nav.toc a[data-part="${part.id}"]`);
      if (link) link.classList.toggle("empty", part.hidden);
    }
    $("f-count").textContent = (keep || text || warn)
      ? `${shown} de ${total} elementos` : `${total} elementos`;
    const params = new URLSearchParams();
    for (const [key, id] of [["repo", "f-repo"], ["proyecto", "f-project"],
      ["lenguaje", "f-language"], ["texto", "f-text"]]) {
      if ($(id).value) params.set(key, $(id).value);
    }
    if (warn) params.set("avisos", "1");
    const hash = params.toString();
    history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
  }

  const start = new URLSearchParams(location.hash.slice(1));
  if (start.has("repo") || start.has("proyecto") || start.has("lenguaje") ||
      start.has("texto") || start.has("avisos")) {
    $("f-repo").value = start.get("repo") || "";
    $("f-project").value = start.get("proyecto") || "";
    $("f-language").value = start.get("lenguaje") || "";
    $("f-text").value = start.get("texto") || "";
    $("f-warn").checked = start.get("avisos") === "1";
  }
  for (const id of ["f-repo", "f-project", "f-language", "f-warn"]) {
    $(id).addEventListener("change", apply);
  }
  $("f-text").addEventListener("input", apply);
  $("f-reset").addEventListener("click", () => {
    for (const id of ["f-repo", "f-project", "f-language", "f-text"]) $(id).value = "";
    $("f-warn").checked = false;
    apply();
  });
  apply();
})();
"""

_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="layout">
<header class="top">
{header}
</header>
<nav class="toc" aria-label="Filtros e índice">
<form class="filters" onsubmit="return false">
<label>Repositorio<select id="f-repo"><option value="">Todos</option></select></label>
<label>Proyecto<select id="f-project"><option value="">Todos</option></select></label>
<label>Lenguaje<select id="f-language"><option value="">Todos</option></select></label>
<label>Buscar<input type="search" id="f-text" placeholder="Texto, autor, librería…"></label>
<label class="check"><input type="checkbox" id="f-warn"> Solo avisos ⚠️</label>
<button type="button" id="f-reset">Quitar filtros</button>
<span class="count" id="f-count" aria-live="polite"></span>
</form>
<ul>
{toc}
</ul>
</nav>
<main>
{parts}
<footer class="appendix">
{footer}
</footer>
</main>
</div>
<script type="application/json" id="repo-meta">{meta}</script>
<script>{js}</script>
</body>
</html>
"""
