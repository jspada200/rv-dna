#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""HTTP helper for pushing the in-review version to DNA.

Pure Python with no RV or Qt imports so it can be unit tested outside RV.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class InReviewResult:
    ok: bool
    status: Optional[int]
    url: str
    request_body: str
    response_body: str = ""


def in_review_url(api_base_url: str, playlist_id: str) -> str:
    base = api_base_url.rstrip("/")
    return f"{base}/playlists/{playlist_id}/metadata"


def set_in_review(
    api_base_url: str,
    playlist_id: str,
    version_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> InReviewResult:
    """PUT in_review for a playlist.

    Callers may ignore failures; ``ok`` is True only for 2xx responses.
    """
    url = in_review_url(api_base_url, playlist_id)
    request_body = json.dumps({"in_review": version_id})
    request = urllib.request.Request(
        url,
        data=request_body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers),
        },
        method="PUT",
    )
    try:
        with opener(request, timeout=10) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            body = response.read().decode("utf-8", errors="replace")
            return InReviewResult(
                ok=200 <= status < 300,
                status=status,
                url=url,
                request_body=request_body,
                response_body=body,
            )
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(error.reason or error)
        return InReviewResult(
            ok=False,
            status=int(error.code),
            url=url,
            request_body=request_body,
            response_body=body,
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return InReviewResult(
            ok=False,
            status=None,
            url=url,
            request_body=request_body,
            response_body=str(error),
        )
