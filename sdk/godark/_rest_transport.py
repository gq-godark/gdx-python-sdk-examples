"""Async HTTP transport for docs-shaped REST endpoints under ``/api/v1``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class RestEnvelopeError(Exception):
    """``code != 0`` in a docs REST envelope."""

    def __init__(self, code: int, message: str | None, request_id: str | None) -> None:
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"code={code} message={message!r}")


def _unwrap(env: dict[str, Any]) -> dict[str, Any]:
    if env.get("code", 1) != 0:
        raise RestEnvelopeError(
            int(env.get("code", 1)),
            env.get("message"),
            env.get("request_id"),
        )
    data = env.get("data")
    if not isinstance(data, dict):
        raise RestEnvelopeError(1500, "missing data object", env.get("request_id"))
    return data


class RestTransport:
    """Thin ``httpx`` wrapper — asserts docs envelope shape."""

    def __init__(
        self,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            transport=transport,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def time_public(self) -> dict[str, Any]:
        r = await self._client.get("/api/v1/time")
        r.raise_for_status()
        return _unwrap(r.json())

    async def auth_token(
        self,
        *,
        grant_type: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        passphrase: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if token is not None:
            body["token"] = token
        else:
            if grant_type is not None:
                body["grant_type"] = grant_type
            if client_id is not None:
                body["client_id"] = client_id
            if client_secret is not None:
                body["client_secret"] = client_secret
            if passphrase is not None:
                body["passphrase"] = passphrase
        r = await self._client.post("/api/v1/auth/token", json=body)
        r.raise_for_status()
        return _unwrap(r.json())

    async def session_setup(self, *, bearer: str, client_ecdh_pubkey: str) -> dict[str, Any]:
        r = await self._client.post(
            "/api/v1/session/setup",
            json={"client_ecdh_pubkey": client_ecdh_pubkey},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def post_encrypted_order(self, *, bearer: str, body: Mapping[str, Any]) -> dict[str, Any]:
        r = await self._client.post(
            "/api/v1/orders",
            json=dict(body),
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def delete_encrypted_order(
        self,
        *,
        bearer: str,
        order_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        r = await self._client.request(
            "DELETE",
            f"/api/v1/orders/{order_id}",
            json=dict(body),
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def delete_encrypted_order_by_client_order_id(
        self,
        *,
        bearer: str,
        client_order_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        """``DELETE /api/v1/orders?client_order_id=`` — edge resolves server-side index."""
        r = await self._client.request(
            "DELETE",
            "/api/v1/orders",
            params={"client_order_id": client_order_id},
            json=dict(body),
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def patch_encrypted_order(
        self,
        *,
        bearer: str,
        order_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        r = await self._client.patch(
            f"/api/v1/orders/{order_id}",
            json=dict(body),
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def get_order(self, *, bearer: str, order_id: str) -> dict[str, Any]:
        r = await self._client.get(
            f"/api/v1/orders/{order_id}",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def get_order_by_client_order_id(
        self, *, bearer: str, client_order_id: str
    ) -> dict[str, Any]:
        r = await self._client.get(
            "/api/v1/orders",
            params={"client_order_id": client_order_id},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def register_client_order_mapping(
        self,
        *,
        bearer: str,
        client_order_id: str,
        order_id: str,
    ) -> dict[str, Any]:
        """``POST /api/v1/orders/_register_coid`` — push (coid, order_id) mapping post-decrypt."""
        r = await self._client.post(
            "/api/v1/orders/_register_coid",
            json={"client_order_id": client_order_id, "order_id": order_id},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())

    async def get_auth_me(self, *, bearer: str) -> dict[str, Any]:
        r = await self._client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return r.json()

    async def get_shielded_pool_balances(self, *, bearer: str, owner: str) -> dict[str, Any]:
        r = await self._client.get(
            f"/api/v1/shielded-pool/balances/{owner}",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return r.json()

    async def revoke_token(self, *, bearer: str) -> dict[str, Any]:
        r = await self._client.post(
            "/api/v1/auth/token/revoke",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        return _unwrap(r.json())
