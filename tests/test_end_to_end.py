"""Simula un workspace con repos git locales y comprueba línea base + informe incremental.

Sin IA por defecto.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from periscope import snapshot
from periscope.ai import AI
from periscope.analysis import Analyzer
from periscope.config import load_config
from periscope.gitrepo import Repo
from periscope.report import render


def git(cwd: Path, *args: str, author: str = "Ana Dev") -> None:
    env = {
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": f"{author.split()[0].lower()}@x.io",
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": "ci@x.io",
        "PATH": os.environ["PATH"],
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def commit(root: Path, msg: str, author: str = "Ana Dev") -> None:
    git(root, "add", "-A", author=author)
    git(root, "commit", "-m", msg, author=author)


class FakeBitbucket:
    def __init__(self, remotes: Path):
        self.remotes = remotes
        self.repos: dict[str, dict] = {}

    def add(self, slug: str, updated: str, private: bool = True) -> None:
        self.repos[slug] = {
            "uuid": "{" + f"uuid-{slug}" + "}",
            "slug": slug,
            "name": slug,
            "full_name": f"acme/{slug}",
            "description": "",
            "language": "php",
            "is_private": private,
            "project": {"key": "CORE", "name": "Core"},
            "created_on": "2026-01-01T10:00:00+00:00",
            "updated_on": updated,
            "size": 1,
            "mainbranch": {"name": "main"},
            "links": {"html": {"href": f"https://bitbucket.org/acme/{slug}"}},
        }

    def repositories(self):
        return list(self.repos.values())

    def projects(self):
        return [{"key": "CORE", "name": "Core"}]

    def members(self):
        return [{"user": {"uuid": "{u1}", "display_name": "Ana Dev", "nickname": "ana"}}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = load_config(tmp_path / "missing.env")
    cfg.root = tmp_path
    cfg.workspace = "acme"
    # PERISCOPE_TEST_AI=1 usa Claude de verdad (lento, consume) y deja los informes en
    # PERISCOPE_TEST_OUT
    cfg.ai_backend = "claude-cli" if os.environ.get("PERISCOPE_TEST_AI") else "none"
    remotes = tmp_path / "remotes"
    bb = FakeBitbucket(remotes)
    monkeypatch.setattr(
        snapshot,
        "open_repo",
        lambda c, b, meta: Repo(snapshot.repo_dir(c, meta["uuid"]), str(remotes / meta["slug"])),
    )
    import periscope.analysis as analysis

    monkeypatch.setattr(analysis, "open_repo", snapshot.open_repo)
    return cfg, bb, remotes


def new_remote(remotes: Path, slug: str) -> Path:
    root = remotes / slug
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    return root


def run_once(cfg, bb):
    _, prev = snapshot.latest_snapshot(cfg)
    cur = snapshot.collect(cfg, bb, prev)
    ai = AI(cfg) if cfg.ai_enabled else None
    changes = Analyzer(cfg, bb, prev, cur, ai).run()
    md = render(cfg, changes, {"snapshot": "x"})
    if out := os.environ.get("PERISCOPE_TEST_OUT"):
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / f"{cur['taken_at'].replace(':', '-')}.md").write_text(md)
    snapshot.save_snapshot(cfg, cur, cur["taken_at"].replace(":", "-"))
    return changes, md


def test_baseline_then_incremental(env):
    cfg, bb, remotes = env

    # --- estado inicial: un repo Laravel sin tests y otro que se borrará
    shop = new_remote(remotes, "shop")
    write(
        shop,
        "composer.json",
        json.dumps({"require": {"php": "^8.3", "laravel/framework": "^11.0"}}),
    )
    write(shop, "artisan", "#!/usr/bin/env php")
    write(shop, "app/Http/Controllers/HomeController.php", "<?php\nclass HomeController {}\n")
    write(shop, "README.md", "# Shop\nTienda online")
    commit(shop, "Initial commit", author="Bea Lead")
    old = new_remote(remotes, "legacy")
    write(old, "index.php", "<?php echo 1;")
    commit(old, "init")
    bb.add("shop", "2026-01-01T10:00:00+00:00")
    bb.add("legacy", "2026-01-01T10:00:00+00:00")

    changes, md = run_once(cfg, bb)
    assert changes["baseline"]
    assert {r["slug"] for r in changes["new_repos"]} == {"shop", "legacy"}
    shop_info = next(r for r in changes["new_repos"] if r["slug"] == "shop")
    assert shop_info["first_commit"]["author"] == "Bea Lead"
    assert "Laravel" in shop_info["frameworks"]
    assert "Línea base" in md

    # --- cambios: stripe + tests + docblocks + Docker + CLAUDE.md + rama nueva + borrado
    #     + repo nuevo
    write(
        shop,
        "composer.json",
        json.dumps(
            {
                "require": {
                    "php": "^8.3",
                    "laravel/framework": "^12.0",
                    "stripe/stripe-php": "^16.0",
                },
                "require-dev": {"pestphp/pest": "^3.0"},
            }
        ),
    )
    commit(shop, "Pagos con Stripe", author="Carlos Back")
    for i in range(3):
        write(shop, f"tests/Feature/Pay{i}Test.php", "<?php test('ok', fn() => true);")
    docs = "\n".join(
        "    /**\n     * @param int $x\n     * @return int\n     */\n    public function "
        f"f{i}($x) {{}}"
        for i in range(40)
    )
    write(shop, "app/Services/Billing.php", f"<?php\nclass Billing {{\n{docs}\n}}\n")
    write(shop, "app/Services/Api.php", "<?php $u = 'https://api.newpartner.io/v2/orders';")
    commit(shop, "Tests y servicio de billing", author="Carlos Back")
    write(shop, "Dockerfile", "FROM php:8.4-fpm\n")
    write(shop, "CLAUDE.md", "# Instrucciones para Claude")
    write(shop, "bitbucket-pipelines.yml", "pipelines: {}\n")
    commit(shop, "Docker, pipelines e IA", author="Dani Ops")
    git(shop, "checkout", "-q", "-b", "feature/secret-admin")
    write(shop, "app/Admin/Hidden.php", "<?php // panel")
    commit(shop, "WIP panel admin", author="Eva Front")
    git(shop, "checkout", "-q", "main")
    bb.repos["shop"]["updated_on"] = "2026-02-01T10:00:00+00:00"
    bb.repos["shop"]["is_private"] = False

    del bb.repos["legacy"]
    api = new_remote(remotes, "billing-api")
    write(
        api, "package.json", json.dumps({"dependencies": {"@nestjs/core": "^11", "openai": "^5"}})
    )
    commit(api, "bootstrap", author="Fran Node")
    bb.add("billing-api", "2026-02-01T10:00:00+00:00")

    changes, md = run_once(cfg, bb)
    assert not changes["baseline"]
    assert [r["slug"] for r in changes["new_repos"]] == ["billing-api"]
    assert {"NestJS"} <= set(changes["new_repos"][0]["frameworks"])
    assert "OpenAI" in changes["new_repos"][0]["services"]["sdks"]
    assert [r["slug"] for r in changes["deleted_repos"]] == ["legacy"]
    assert any(label == "privado" for label, _, _ in changes["metadata_changes"][0]["changes"])

    rc = next(r for r in changes["repo_changes"] if r["slug"] == "shop")
    added = {lib["name"]: lib for lib in rc["libs_added"]}
    assert added["stripe/stripe-php"]["by"]["author"] == "Carlos Back"
    assert "pestphp/pest" in added
    assert any(u["name"] == "laravel/framework" for u in rc["libs_major_upgrades"])
    assert "Stripe" in rc["sdks_added"]
    assert "api.newpartner.io" in rc["hosts_added"]
    texts = " ".join(p["text"] for p in rc["practices"])
    assert "primeros tests" in texts and "docblocks" in texts and "CLAUDE.md" in texts
    assert any(p.get("first") for p in rc["infra"])
    assert any(
        c["path"] == "bitbucket-pipelines.yml" and c["status"] == "nuevo" for c in rc["config"]
    )
    branch = next(b for b in rc["branches_added"] if b["name"] == "feature/secret-admin")
    assert branch["commits_ahead"][0]["author"] == "Eva Front"

    for needle in (
        "stripe/stripe-php",
        "Repositorios eliminados",
        "feature/secret-admin",
        "Dockerfile",
        "billing-api",
        "⚠️",
    ):
        assert needle in md, needle

    # --- tercera ejecución sin cambios: el informe no debe repetir nada
    changes, md = run_once(cfg, bb)
    assert (
        not changes["new_repos"] and not changes["deleted_repos"] and not changes["repo_changes"]
    )
