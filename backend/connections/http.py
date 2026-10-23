import asyncio
import base64
import json

import httpx

from backend.decisions.validation import validate_data
from backend.errors import DomainError
from backend.security.destinations import resolve_destination


class HTTPConnection:
    def __init__(self, client: httpx.AsyncClient, vault):
        self.client, self.vault = client, vault

    async def request(
        self, connection: dict, *, method=None, payload=None, suffix="", key=None, testing=False
    ):
        # Only a saved destination can be used. No model-generated URL or redirect is followed.
        endpoint = connection["endpoint"].rstrip("/") + suffix
        secrets = self.vault.get(connection["id"])
        headers = {"Accept": "application/json"}
        if connection["auth_type"] == "bearer" and secrets.get("token"):
            headers["Authorization"] = "Bearer " + secrets["token"]
        elif connection["auth_type"] == "basic":
            value = base64.b64encode(
                f"{secrets.get('username', '')}:{secrets.get('password', '')}".encode()
            ).decode()
            headers["Authorization"] = "Basic " + value
        elif connection["auth_type"] == "header" and secrets.get("token"):
            if connection["auth_header"].lower() in {
                "host",
                "content-length",
                "transfer-encoding",
                "connection",
            }:
                raise DomainError(
                    "invalid_auth_header", "This header cannot be used for connection authentication."
                )
            headers[connection["auth_header"]] = secrets["token"]
        if key:
            headers["Idempotency-Key"] = key
        method = method or connection["method"]
        attempts = 1 if testing else 1 + connection["retries"]
        if payload is not None and not suffix:
            validate_data(payload, connection["output_schema"])
        for attempt in range(attempts):
            pinned, host, port = await resolve_destination(endpoint, connection)
            headers["Host"] = host if port in {80, 443} else f"{host}:{port}"
            try:
                async with self.client.stream(
                    method,
                    pinned,
                    json=payload,
                    headers=headers,
                    timeout=connection["timeout_s"],
                    follow_redirects=False,
                    extensions={"sni_hostname": host},
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 262144:
                            raise DomainError(
                                "connection_response_size", "The destination response exceeds 256 KiB.", 502
                            )
                    result = httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=bytes(content),
                        request=response.request,
                    )
                if 300 <= result.status_code < 400:
                    raise DomainError(
                        "redirect_blocked",
                        "Connection redirects are blocked. Save the final destination explicitly.",
                        403,
                    )
                if result.status_code >= 500 and attempt + 1 < attempts:
                    await asyncio.sleep(min(2**attempt, 4))
                    continue
                return result
            except (httpx.HTTPError, OSError):
                if attempt + 1 == attempts:
                    raise DomainError(
                        "connection_failed",
                        "The destination is unavailable, failed TLS validation, or timed out.",
                        502,
                    ) from None
                await asyncio.sleep(min(2**attempt, 4))
        raise DomainError("connection_failed", "The destination did not respond.", 502)

    async def read(self, connection):
        response = await self.request(connection, method="GET")
        if not 200 <= response.status_code < 300:
            raise DomainError("connection_status", f"The input returned HTTP {response.status_code}.", 502)
        try:
            value = response.json()
        except (ValueError, json.JSONDecodeError):
            value = {"text": response.text}
        validate_data(value, connection["input_schema"])
        return self.vault.scrub(connection["id"], value)
