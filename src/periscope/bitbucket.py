"""Cliente mínimo de la API REST de Bitbucket Cloud (2.0) y del audit log de Atlassian."""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from periscope.config import Config

log = logging.getLogger(__name__)

API = "https://api.bitbucket.org/2.0"


class BitbucketError(Exception):
    pass


class Bitbucket:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = httpx.Client(
            base_url=API,
            headers={"Authorization": self.api_auth_header(), "Accept": "application/json"},
            timeout=60,
        )

    # --- autenticación -------------------------------------------------
    def api_auth_header(self) -> str:
        raw = f"{self.cfg.bb_email}:{self.cfg.bb_api_token}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    # --- HTTP -----------------------------------------------------------
    def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        for attempt in range(5):
            resp = self.client.get(url, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                log.warning("Bitbucket %s en %s, reintento en %ss", resp.status_code, url, wait)
                time.sleep(wait)
                continue
            if resp.status_code in (401, 403):
                raise BitbucketError(
                    f"Bitbucket devolvió {resp.status_code} en {url}. "
                    "Revisa las credenciales y los scopes del token en .env"
                )
            resp.raise_for_status()
            return resp.json()
        raise BitbucketError(f"Bitbucket no responde correctamente en {url}")

    def _paginate(self, path: str, params: dict | None = None) -> Iterator[dict[str, Any]]:
        url: str | None = path
        params = {"pagelen": 100, **(params or {})}
        while url:
            page = self._get(url, params=params)
            yield from page.get("values", [])
            url = page.get("next")
            params = None  # "next" ya trae los parámetros

    # --- endpoints ------------------------------------------------------
    def current_user(self) -> dict[str, Any]:
        return self._get("/user")

    def workspace(self) -> dict[str, Any]:
        return self._get(f"/workspaces/{self.cfg.workspace}")

    def repositories(self) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repositories/{self.cfg.workspace}", {"sort": "-created_on"}))

    def projects(self) -> list[dict[str, Any]]:
        return list(self._paginate(f"/workspaces/{self.cfg.workspace}/projects"))

    def members(self) -> list[dict[str, Any]]:
        return list(self._paginate(f"/workspaces/{self.cfg.workspace}/members"))

    def close(self) -> None:
        self.client.close()


def audit_events(cfg: Config, since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    """Eventos del audit log de la organización Atlassian (requiere Atlassian Guard).

    Se devuelven sin filtrar por tipo: luego se buscan menciones a repos concretos,
    así no dependemos de los nombres exactos de las acciones.
    """
    base = f"https://api.atlassian.com/admin/v1/orgs/{cfg.atlassian_org_id}/events"
    url: str | None = base
    base_params = {"from": since_iso, "to": until_iso}
    params: dict | None = dict(base_params)
    headers = {
        "Authorization": f"Bearer {cfg.atlassian_admin_api_key}",
        "Accept": "application/json",
    }
    events: list[dict[str, Any]] = []
    with httpx.Client(timeout=60, headers=headers) as client:
        while url and len(events) < 20000:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
            events.extend(body.get("data", []))
            nxt = (body.get("links") or {}).get("next")
            if not nxt:
                url = None
            elif nxt.startswith("http"):
                url, params = nxt, None
            else:  # cursor opaco
                url, params = base, {**base_params, "cursor": nxt}
    return events
