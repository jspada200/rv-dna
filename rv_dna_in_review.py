#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""HTTP helpers for pushing the in-review version to DNA.

In-review lives on a DNA session (one or more playlists sharing a transcript
and in-review version). The package sets it with
``PUT /sessions/{session_id}/in-review``. When the sign-in did not carry a
session, ``POST /sessions/ensure-for-playlist`` finds or creates the
one-playlist session DNA uses in playlist mode.

Pure Python with no RV or Qt imports so it can be unit tested outside RV.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class InReviewResult:
    ok: bool
    status: Optional[int]
    url: str
    request_body: str
    response_body: str = ""
    method: str = "PUT"


def _base(api_base_url: str) -> str:
    return api_base_url.rstrip("/")


def in_review_url(api_base_url: str, session_id: str) -> str:
    return f"{_base(api_base_url)}/sessions/{session_id}/in-review"


def ensure_session_url(api_base_url: str) -> str:
    return f"{_base(api_base_url)}/sessions/ensure-for-playlist"


def _send_json(
    method: str,
    url: str,
    body: dict,
    headers: Mapping[str, str],
    opener,
) -> InReviewResult:
    """Send a JSON request. Never raises; ``ok`` is True only for 2xx."""
    request_body = json.dumps(body)
    request = urllib.request.Request(
        url,
        data=request_body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers),
        },
        method=method,
    )
    result = InReviewResult(
        ok=False, status=None, url=url, request_body=request_body, method=method
    )
    try:
        with opener(request, timeout=10) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            return replace(
                result,
                ok=200 <= status < 300,
                status=status,
                response_body=response.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as error:
        try:
            response_body = error.read().decode("utf-8", errors="replace")
        except Exception:
            response_body = str(error.reason or error)
        return replace(result, status=int(error.code), response_body=response_body)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return replace(result, response_body=str(error))


def ensure_session_for_playlist(
    api_base_url: str,
    project_id: str,
    playlist_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> Tuple[Optional[str], InReviewResult]:
    """Find or create DNA's one-playlist session for ``playlist_id``.

    Returns ``(session_id, result)``; ``session_id`` is None on failure.
    """
    result = _send_json(
        "POST",
        ensure_session_url(api_base_url),
        {"project_id": project_id, "playlist_id": playlist_id},
        headers,
        opener,
    )
    if not result.ok:
        return None, result
    # DNA serialises the session id as "_id" (Mongo alias); accept "id" too.
    try:
        session = json.loads(result.response_body)
        session_id = session.get("_id") or session.get("id")
    except (ValueError, AttributeError):
        session_id = None
    if session_id is None or session_id == "":
        return None, replace(
            result,
            ok=False,
            response_body=f"no session id in response: {result.response_body}",
        )
    return str(session_id), result


def set_in_review(
    api_base_url: str,
    session_id: str,
    playlist_id: str,
    version_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> InReviewResult:
    """Set the session's in-review version, on ``playlist_id``.

    Callers may ignore failures. DNA answers 409 while the in-review version
    is pinned, and 404 when the session no longer exists.
    """
    return _send_json(
        "PUT",
        in_review_url(api_base_url, session_id),
        {
            "in_review_playlist_id": playlist_id,
            "in_review_version_id": version_id,
        },
        headers,
        opener,
    )
