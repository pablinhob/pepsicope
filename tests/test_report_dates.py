"""Fechas en el informe: siempre de git, en el resumen y en las notas sin fecha propia."""

from __future__ import annotations

import pytest

from periscope.config import load_config
from periscope.html_report import render_html
from periscope.report import render


def _commit(day: str, author: str = "Ana Dev") -> dict:
    return {"hash": "a" * 40, "author": author, "email": "a@x.io", "date": f"{day}T10:00:00"}


def _base(**extra: object) -> dict:
    changes = {
        "baseline": False,
        "since": "2026-10-01T07:00:00",
        "until": "2026-10-22T07:00:00",
        "repo_total": 2,
        "new_repos": [],
        "deleted_repos": [],
        "metadata_changes": [],
        "repo_changes": [],
        "workspace_changes": {},
        "errors": [],
        "summary": None,
    }
    changes.update(extra)
    return changes


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config(tmp_path / "missing.env")
    cfg.workspace = "acme"
    return cfg


def test_incremental_summary_and_notes_are_dated(cfg):
    shop = {
        "slug": "shop",
        "html_url": None,
        "commit_count": 2,
        "commits": [_commit("2026-10-15"), _commit("2026-10-03")],
        "authors": {"Ana Dev": 2},
        "frameworks_added": ["Laravel"],
        "ai": {
            "summary": "Pagos nuevos",
            "practice_changes": [{"title": "Tests", "description": "x"}],
        },
    }
    summary = {
        "highlights": ["`shop` añade pagos", "Algo general"],
        "ways_of_working": [{"title": "PRs", "description": "Más revisión", "repos": ["shop"]}],
        "attention": ["`shop`: revisar claves"],
    }
    md = render(cfg, _base(repo_changes=[shop], summary=summary), {})
    assert "_Cambios entre el 2026-10-01 07:00 y el 2026-10-22 07:00._" in md
    assert "| 2 | 2026-10-03 | 2026-10-15 |" in md
    assert "- **shop** _(cambios del 2026-10-03 al 2026-10-15)_: Pagos nuevos" in md
    assert "**añade** Laravel _(cambios del 2026-10-03 al 2026-10-15)_" in md
    assert "**Tests** _(cambios del 2026-10-03 al 2026-10-15)_ — x" in md
    assert "- `shop` añade pagos · _cambios del 2026-10-03 al 2026-10-15_" in md
    assert "- Algo general\n" in md
    assert "_(shop)_ · _cambios del 2026-10-03 al 2026-10-15_" in md
    assert "- `shop`: revisar claves · _cambios del 2026-10-03 al 2026-10-15_" in md
    # el HTML sigue asociando cada punto a su repo pese a la fecha
    assert '<li data-filter data-repos="shop"><strong>shop</strong>' in render_html(md)


def test_baseline_dates_cover_whole_history(cfg):
    repo = {
        "slug": "shop",
        "html_url": None,
        "first_commit": _commit("2024-02-06"),
        "last_commit": _commit("2026-10-21"),
        "created_on": "2024-02-06T10:00:00",
        "signals": {},
        "services": {},
    }
    summary = {"highlights": ["`shop` es el núcleo"]}
    md = render(cfg, _base(baseline=True, since=None, new_repos=[repo], summary=summary), {})
    assert "| Historia analizada | 2024-02-06 → 2026-10-21 |" in md
    assert "Historia de los repos analizados: del 2024-02-06 al 2026-10-21." in md
    assert "- `shop` es el núcleo · _actividad del 2024-02-06 al 2026-10-21_" in md


def test_single_day_and_unknown_repos(cfg):
    shop = {
        "slug": "shop",
        "html_url": None,
        "commit_count": 1,
        "commits": [_commit("2026-10-10")],
        "authors": {"Ana Dev": 1},
    }
    summary = {"highlights": ["`otro` no tiene fechas", "`shop` cambia"]}
    md = render(cfg, _base(repo_changes=[shop], summary=summary), {})
    assert "- `otro` no tiene fechas\n" in md
    assert "- `shop` cambia · _cambios el 2026-10-10_" in md
