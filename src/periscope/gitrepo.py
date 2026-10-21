"""Clones locales parciales (blob:none) de cada repo, actualizados de forma incremental.

El acceso es por SSH con la clave del usuario (la misma que usa con `git` a mano).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SEP = "\x1f"
LOG_FORMAT = SEP.join(["%H", "%an", "%ae", "%aI", "%s"])

# Ruido que no aporta al análisis de diffs
DIFF_EXCLUDES = [
    ":(exclude,glob)**/*.lock",
    ":(exclude,glob)**/package-lock.json",
    ":(exclude,glob)**/pnpm-lock.yaml",
    ":(exclude,glob)**/yarn.lock",
    ":(exclude,glob)**/composer.lock",
    ":(exclude,glob)**/go.sum",
    ":(exclude,glob)**/*.min.js",
    ":(exclude,glob)**/*.min.css",
    ":(exclude,glob)**/*.map",
    ":(exclude,glob)**/vendor/**",
    ":(exclude,glob)**/node_modules/**",
    ":(exclude,glob)**/dist/**",
    ":(exclude,glob)**/build/**",
    ":(exclude,glob)**/*.svg",
]


class GitError(Exception):
    pass


@dataclass
class Commit:
    hash: str
    author: str
    email: str
    date: str
    subject: str

    def as_dict(self) -> dict:
        return {
            "hash": self.hash,
            "author": self.author,
            "email": self.email,
            "date": self.date,
            "subject": self.subject,
        }


def _parse_log(out: str) -> list[Commit]:
    commits = []
    for line in out.splitlines():
        parts = line.split(SEP)
        if len(parts) == 5:
            commits.append(Commit(*parts))
    return commits


class Repo:
    def __init__(self, path: Path, url: str):
        self.path = path
        self.url = url

    # --- ejecución -------------------------------------------------------
    def _git(
        self, *args: str, cwd: Path | None = None, check: bool = True, timeout: int = 1800
    ) -> str:
        env = {
            # BatchMode: si la clave SSH pidiera passphrase, fallar en vez de quedarse colgado.
            # accept-new: en una máquina nueva acepta la huella de bitbucket.org la primera vez,
            # pero sigue rechazando una huella que cambie (posible suplantación).
            "GIT_SSH_COMMAND": (
                "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20"
            ),
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "LC_ALL": "C",
        }
        cmd = ["git", *args]
        proc = subprocess.run(
            cmd, cwd=cwd or self.path, env=env, capture_output=True, timeout=timeout
        )
        if check and proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")
            raise GitError(f"git {' '.join(args[:2])}: {err.strip()[:500]}")
        return proc.stdout.decode(errors="replace")

    # --- sincronización -----------------------------------------------------
    def sync(self, branch: str) -> None:
        """Clona o actualiza el repo y deja el working tree en origin/<branch>."""
        if not (self.path / ".git").exists():
            if self.path.exists():
                shutil.rmtree(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._git(
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--no-tags",
                self.url,
                str(self.path),
                cwd=self.path.parent,
            )
        self._git("fetch", "--prune", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*")
        self._git("checkout", "--force", "--detach", f"origin/{branch}")
        self._git("clean", "-fdq")

    # --- consultas ------------------------------------------------------
    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def commit_info(self, ref: str) -> Commit | None:
        out = self._git("log", "-1", f"--format={LOG_FORMAT}", ref, check=False)
        commits = _parse_log(out)
        return commits[0] if commits else None

    def first_commit(self) -> Commit | None:
        out = self._git(
            "log", "--reverse", f"--format={LOG_FORMAT}", "--max-parents=0", "HEAD", check=False
        )
        commits = _parse_log(out)
        return commits[0] if commits else None

    def commit_count(self) -> int:
        out = self._git("rev-list", "--count", "HEAD", check=False).strip()
        return int(out) if out.isdigit() else 0

    def contributors(self, limit: int = 10) -> list[dict]:
        out = self._git("shortlog", "-sne", "HEAD", check=False)
        result = []
        for line in out.splitlines()[:limit]:
            count, _, who = line.strip().partition("\t")
            result.append({"who": who, "commits": int(count) if count.isdigit() else 0})
        return result

    def is_ancestor(self, old: str, new: str) -> bool:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", old, new], cwd=self.path, capture_output=True
        )
        return proc.returncode == 0

    def has_commit(self, sha: str) -> bool:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=self.path,
            capture_output=True,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
        return proc.returncode == 0

    def log_range(
        self,
        old: str | None,
        new: str = "HEAD",
        since: str | None = None,
        paths: list[str] | None = None,
        limit: int = 500,
        no_merges: bool = True,
    ) -> list[Commit]:
        args = ["log", f"--format={LOG_FORMAT}", f"-n{limit}"]
        if no_merges:
            args.append("--no-merges")
        if since:
            args.append(f"--since={since}")
        args.append(f"{old}..{new}" if old else new)
        if paths:
            args += ["--", *paths]
        return _parse_log(self._git(*args, check=False))

    def first_adding_commit(self, old: str | None, path: str) -> Commit | None:
        out = self._git(
            "log",
            "--diff-filter=A",
            f"--format={LOG_FORMAT}",
            f"{old}..HEAD" if old else "HEAD",
            "--",
            path,
            check=False,
        )
        commits = _parse_log(out)
        return commits[-1] if commits else None

    def branches(self) -> dict[str, dict]:
        fmt = SEP.join(
            ["%(refname:short)", "%(objectname)", "%(committerdate:iso-strict)", "%(authorname)"]
        )
        out = self._git("for-each-ref", f"--format={fmt}", "refs/remotes/origin", check=False)
        result = {}
        for line in out.splitlines():
            parts = line.split(SEP)
            if len(parts) != 4:
                continue
            name = parts[0].removeprefix("origin/")
            if name == "HEAD" or name == "origin":
                continue
            result[name] = {"hash": parts[1], "date": parts[2], "author": parts[3]}
        return result

    def files(self) -> dict[str, str]:
        """{ruta: blob_sha} del árbol en HEAD (solo árboles, no descarga blobs)."""
        out = self._git("ls-tree", "-r", "-z", "HEAD", check=False)
        result = {}
        for entry in out.split("\0"):
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                result[path] = parts[2]
        return result

    def read(self, rel: str, max_bytes: int = 2_000_000) -> str | None:
        p = self.path / rel
        try:
            if p.is_file() and p.stat().st_size <= max_bytes:
                return p.read_text(errors="replace")
        except OSError:
            pass
        return None

    def show(self, ref: str, rel: str) -> str | None:
        try:
            return self._git("show", f"{ref}:{rel}")
        except GitError:
            return None

    def grep_count(self, pattern: str, pathspecs: list[str]) -> int:
        out = self._git("grep", "-I", "-c", "-E", pattern, "--", *pathspecs, check=False)
        total = 0
        for line in out.splitlines():
            _, _, n = line.rpartition(":")
            total += int(n) if n.isdigit() else 0
        return total

    def grep_matches(self, pattern: str, pathspecs: list[str], limit: int = 20000) -> list[str]:
        out = self._git("grep", "-I", "-h", "-o", "-E", pattern, "--", *pathspecs, check=False)
        return out.splitlines()[:limit]

    def diff(self, old: str, new: str = "HEAD", max_chars: int = 60000) -> tuple[str, bool]:
        out = self._git(
            "diff", "--no-color", "--unified=2", old, new, "--", ".", *DIFF_EXCLUDES, check=False
        )
        if len(out) > max_chars:
            return out[:max_chars], True
        return out, False

    def diff_stat(self, old: str, new: str = "HEAD") -> str:
        return self._git("diff", "--stat=120", old, new, "--", ".", *DIFF_EXCLUDES, check=False)

    def diff_names(self, old: str, new: str) -> list[str]:
        out = self._git("diff", "--name-status", "--no-renames", old, new, check=False)
        return out.splitlines()

    def merge_base(self, a: str, b: str) -> str | None:
        out = self._git("merge-base", a, b, check=False).strip()
        return out or None

    def commit_diff(self, sha: str, path: str) -> str:
        return self._git(
            "show", "--format=", "--no-color", "--unified=0", sha, "--", path, check=False
        )
