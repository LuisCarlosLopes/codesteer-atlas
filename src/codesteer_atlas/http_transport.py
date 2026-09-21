"""POST JSON compartilhado: F4 interpreta o texto; Jev interpreta o envelope."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


class _NoRedirectHandler(HTTPRedirectHandler):
    """Recusa 3xx: Jev não segue host distinto com a credencial reutilizada."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise HTTPError(req.full_url, code, msg, headers, fp)


def post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    api_key: Optional[str] = None,
    timeout_s: float,
    max_response_bytes: Optional[int] = None,
    allow_redirects: bool = True,
) -> str:
    """
    Envia JSON e devolve o corpo textual bruto.

    Não registra payload, resposta nem headers. `max_response_bytes` e
    `allow_redirects=False` existem só para o avaliador Jev; F4 omite ambos.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(url, data=body, headers=headers, method="POST")
    if allow_redirects:
        response_cm = urlopen(request, timeout=timeout_s)
    else:
        response_cm = build_opener(_NoRedirectHandler).open(request, timeout=timeout_s)
    with response_cm as response:
        if max_response_bytes is None:
            raw = response.read()
        else:
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise ValueError("response_too_large")
    return raw.decode("utf-8", errors="replace")
