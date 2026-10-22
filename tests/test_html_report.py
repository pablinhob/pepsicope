"""Conversión del informe Markdown a HTML autocontenido."""

from __future__ import annotations

import json
import re
from pathlib import Path

from periscope.html_report import html_path, render_html, write_html

SAMPLE = """# Periscope · acme · Línea base · 2026-10-22 07:08

> **Primer informe (línea base).** Recoge todo lo que existe hoy.

| Indicador | Valor |
|---|---:|
| Repositorios en el workspace | 2 |

**En una frase:** Dos repos <script>alert(1)</script>.

## Inventario de repositorios (2)

| Repo | Proyecto | Lenguaje | Creado | Creador (1er commit) | Frameworks | Último commit |
|---|---|---|---|---|---|---|
| [shop](https://bitbucket.org/acme/shop) | core | php | 2026-01-01 | Bea Lead | Laravel \
| 2026-01-02 |
| [billing-api](https://bitbucket.org/acme/billing-api) | pagos | javascript | 2026-01-01 \
| Fran | NestJS | 2026-01-03 |

### shop

**Para qué sirve:** Tienda con [enlace raro](javascript:alert(1)) y `snake_case`.

- **Tests:** 0 archivos de test / 1 de código
- Un apaño con <img src=x onerror=alert(1)>

<details><summary>Dependencias</summary>

- `composer.json` (composer, 1): laravel/framework

</details>

### billing-api

- **Frameworks:** NestJS

## Resumen final

### Lo más importante

- `shop` necesita tests ⚠️
- **Pagos nuevos**: integración con Stripe _(billing-api, shop)_
- Algo general sin repo

---

<details><summary>Detalles de la ejecución</summary>

- Duración: 0:01:00

</details>
"""


def test_html_path_keeps_md_name():
    assert html_path(Path("reports/x_cambios.md")) == Path("reports/x_cambios.md.html")


def test_page_is_self_contained():
    page = render_html(SAMPLE)
    assert page.startswith("<!doctype html>")
    assert "<link" not in page
    assert not re.search(r"<script[^>]+src=", page)
    assert not re.search(r"url\(\s*['\"]?https?:", page)


def test_untrusted_content_is_escaped():
    page = render_html(SAMPLE)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img" not in page
    assert 'href="javascript:' not in page
    assert '<a href="https://bitbucket.org/acme/shop">shop</a>' in page
    assert "<code>snake_case</code>" in page


def test_repo_sections_and_rows_are_tagged_for_filters():
    page = render_html(SAMPLE)
    assert '<article class="repo" data-filter data-repos="shop">' in page
    assert '<article class="repo" data-filter data-repos="billing-api">' in page
    assert '<tr data-filter data-repos="shop">' in page
    assert '<li data-filter data-repos="shop"><code>shop</code> necesita tests ⚠️</li>' in page
    assert 'data-repos="billing-api shop"' in page
    assert '<li data-filter data-repos="">Algo general sin repo</li>' in page


def test_header_and_appendix_are_not_filtered():
    page = render_html(SAMPLE)
    header = page.split('<header class="top">')[1].split("</header>")[0]
    footer = page.split('<footer class="appendix">')[1].split("</footer>")[0]
    assert "<h1>" in header and "<td" in header and "data-filter" not in header
    assert "Duración" in footer and "data-filter" not in footer


def test_repo_meta_comes_from_inventory():
    page = render_html(SAMPLE)
    raw = page.split('<script type="application/json" id="repo-meta">')[1].split("</script>")[0]
    meta = json.loads(raw)
    assert meta == {
        "shop": {"project": "core", "language": "php"},
        "billing-api": {"project": "pagos", "language": "javascript"},
    }


def test_github_alert_becomes_callout():
    page = render_html("# T\n\n> [!WARNING]\n> **Sin comparación.**\n> Todo es estado actual.\n")
    assert '<div class="callout warning" role="note">' in page
    assert '<p class="callout-title">Atención</p>' in page
    assert "<p><strong>Sin comparación.</strong></p><p>Todo es estado actual.</p>" in page
    assert "[!WARNING]" not in page


def test_legacy_baseline_quote_is_highlighted():
    page = render_html(SAMPLE)
    header = page.split('<header class="top">')[1].split("</header>")[0]
    assert '<div class="callout warning" role="note">' in header
    assert "no hay ningún informe anterior con el que comparar" in header
    assert "(2 repositorios)" in header
    assert "<blockquote>" not in header


def test_write_html_next_to_markdown(tmp_path):
    md = tmp_path / "2026_acme_linea-base.md"
    md.write_text(SAMPLE)
    out = write_html(md)
    assert out == tmp_path / "2026_acme_linea-base.md.html"
    assert "Inventario de repositorios" in out.read_text()
