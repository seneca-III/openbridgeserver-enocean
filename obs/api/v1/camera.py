"""Kamera-Proxy — leitet Kamera-Streams vom Backend weiter.

GET /api/v1/camera/proxy   Proxyt einen HTTP-Stream zur Kamera

SSRF-Schutz:
  - Nur HTTP/HTTPS-Schemas erlaubt
  - Hostname wird per DNS aufgelöst und zentral gegen öffentliche Ziele bzw.
    die operatorgepflegte URL-Target-Allowlist geprüft
  - follow_redirects=False im Stream-Client verhindert Redirect-basiertes SSRF
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from obs.api.auth import decode_token
from obs.security.url_targets import evaluate_url_target

router = APIRouter(tags=["camera"])


async def _check_ssrf(url: str) -> None:
    decision = await asyncio.to_thread(evaluate_url_target, url)
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=decision.api_detail())


# ── Authentifizierung ──────────────────────────────────────────────────────────


async def _camera_auth(
    request: Request,
    _token: str = Query("", alias="_token", description="JWT als Query-Parameter"),
) -> str:
    """Akzeptiert JWT entweder als 'Authorization: Bearer …'-Header
    oder als URL-Query-Parameter '?_token=…' (nötig für <img>/<video>-Tags).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return decode_token(auth_header[7:])
    if _token:
        return decode_token(_token)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide Authorization: Bearer {token} or ?_token=",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Proxy-Endpunkt ─────────────────────────────────────────────────────────────


@router.get("/proxy")
async def proxy_camera(
    url: str = Query(..., description="Vollständige Kamera-URL (http://…)"),
    username: str = Query("", description="Basic-Auth Benutzername"),
    password: str = Query("", description="Basic-Auth Passwort"),
    apikey_param: str = Query("", description="API-Key Query-Parameter-Name"),
    apikey_value: str = Query("", description="API-Key Wert"),
    _user: str = Depends(_camera_auth),
) -> StreamingResponse:
    """Proxyt den Kamera-Stream vom Backend aus.
    Ermöglicht HTTPS-Browser → Server → HTTP-Kamera (Mixed-Content-Bypass).
    """
    # 1. Schema-Validierung
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nur HTTP/HTTPS-URLs erlaubt",
        )

    # 2. SSRF-Prüfung: gesperrte Ziel-IPs
    await _check_ssrf(url)

    # 3. API-Key anhängen
    target = url
    if apikey_param and apikey_value:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}{apikey_param}={apikey_value}"

    auth = (username, password) if username else None

    # 4. HEAD-Request: Erreichbarkeit prüfen + Content-Type holen
    content_type = "application/octet-stream"
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=False,  # Redirects nicht folgen (SSRF via Redirect)
        ) as hc:
            head = await hc.head(target, auth=auth)

        if head.status_code in (301, 302, 307, 308):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Kamera-URL leitet weiter — Redirects sind nicht erlaubt",
            )
        if head.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Kamera: Authentifizierung fehlgeschlagen (401)",
            )
        # 405 = HEAD nicht unterstützt → optimistisch weiterfahren
        if head.status_code != 405 and head.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Kamera antwortet mit {head.status_code}",
            )
        ct = head.headers.get("content-type", "")
        if ct:
            # Header-Injection verhindern
            content_type = ct.split("\n")[0].split("\r")[0]

    except HTTPException:
        raise
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kamera nicht erreichbar: {exc}",
        ) from exc

    # 5. Streaming-Generator (kein follow_redirects)
    async def _stream() -> AsyncGenerator[bytes]:
        async with httpx.AsyncClient(
            timeout=None,
            follow_redirects=False,
        ) as hc:
            try:
                async with hc.stream("GET", target, auth=auth) as resp:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        yield chunk
            except httpx.RequestError:
                return  # Verbindung unterbrochen — Stream still beenden

    return StreamingResponse(_stream(), media_type=content_type)
