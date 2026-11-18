#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""DNA API helpers and in-review targeting for the RV DNA package.

In-review lives on a DNA session: one or more playlists that share a
transcript and an in-review version. RV sets it with
``PUT /sessions/{session_id}/in-review`` on the playlist RV is on.

Pure Python with no RV or Qt imports so it can be unit tested outside RV.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Iterable, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class InReviewResult:
    ok: bool
    status: Optional[int]
    url: str
    request_body: str
    response_body: str = ""
    method: str = "PUT"


@dataclass(frozen=True)
class DnaSessionInfo:
    """A DNA session as returned by the API (only the fields RV uses)."""

    id: str
    name: Optional[str]
    playlist_ids: Tuple[str, ...]
    external_id: Optional[str] = None
    in_review_playlist_id: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.name or self.id

    @property
    def is_implicit(self) -> bool:
        """The one-playlist session DNA creates for a playlist in playlist mode."""
        return (
            self.external_id is None
            and len(self.playlist_ids) == 1
            and self.name == f"Playlist {self.playlist_ids[0]}"
        )

    @classmethod
    def from_api(cls, raw: Mapping) -> Optional["DnaSessionInfo"]:
        if not isinstance(raw, Mapping):
            return None
        session_id = raw.get("_id") or raw.get("id")
        if session_id is None or session_id == "":
            return None
        playlist_ids = raw.get("playlist_ids")
        if not isinstance(playlist_ids, list):
            playlist_ids = []
        in_review = raw.get("in_review_playlist_id")
        return cls(
            id=str(session_id),
            name=raw.get("name") or None,
            playlist_ids=tuple(str(p) for p in playlist_ids),
            external_id=raw.get("external_id") or None,
            in_review_playlist_id=str(in_review) if in_review else None,
        )


@dataclass(frozen=True)
class SgContext:
    """What Screening Room says the user is browsing."""

    entity_id: str
    entity_type: str
    name: str
    project_id: str

    @property
    def is_playlist(self) -> bool:
        return self.entity_type == "Playlist"


def parse_sg_context(contents: str) -> Optional[SgContext]:
    """Parse Screening Room's ``sg-version-context-changed`` event contents.

    The format is ``id|type|name|project_id|valid``; a cleared context is
    ``-1||||``. Returns None for a cleared, invalid or malformed context.
    """
    parts = (contents or "").split("|")
    if len(parts) < 5:
        return None
    # The name may itself contain "|": id and type lead, project and flag trail.
    entity_id, entity_type = parts[0].strip(), parts[1].strip()
    project_id, valid = parts[-2].strip(), parts[-1].strip()
    name = "|".join(parts[2:-2])
    if not entity_id.isdigit() or not entity_type or valid != "valid":
        return None
    return SgContext(entity_id, entity_type, name, project_id)


# --- Targeting ---------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """Where an in-review update goes, or why it is skipped."""

    playlist_id: Optional[str]
    skip_reason: Optional[str] = None
    # True when the skip is something the user should hear about once even
    # outside debug mode (as opposed to routine skips like other media).
    notify: bool = False


def choose_playlist(
    version_id: str,
    playlist_id: Optional[str],
    playlist_from_rv: bool,
    session: Optional[DnaSessionInfo],
    playlist_has_version: Callable[[str], Optional[bool]],
) -> Target:
    """Pick the playlist to set ``version_id`` in review on.

    ``playlist_id`` is the playlist RV is on (``playlist_from_rv``) or the one
    picked at sign-in. With a DNA ``session``, the playlist must belong to it;
    when RV reports no playlist, the session's playlists are searched (the one
    already in review first). Without a session, the playlist is used as is.
    ``playlist_has_version`` returns None when the lookup failed.
    """

    def check(candidate: str) -> Target:
        has = playlist_has_version(candidate)
        if has is None:
            return Target(None, f"could not read the versions of playlist {candidate}")
        if not has:
            return Target(None, f"version {version_id} is not on playlist {candidate}")
        return Target(candidate)

    if session is None:
        if not playlist_id:
            return Target(None, "no playlist to set in review on", notify=True)
        return check(playlist_id)

    if playlist_from_rv and playlist_id:
        if playlist_id not in session.playlist_ids:
            return Target(
                None,
                f"playlist {playlist_id} is not in DNA session "
                f"{session.display_name}",
                notify=True,
            )
        return check(playlist_id)

    # RV does not say which playlist it is on: find one in the session.
    candidates = _unique(
        [session.in_review_playlist_id, playlist_id, *session.playlist_ids]
    )
    candidates = [c for c in candidates if c in session.playlist_ids]
    for candidate in candidates:
        if playlist_has_version(candidate):
            return Target(candidate)
    return Target(
        None,
        f"version {version_id} is not on any playlist in DNA session "
        f"{session.display_name}",
    )


def suggest_session(
    sessions: Iterable[DnaSessionInfo], playlist_id: Optional[str]
) -> Optional[DnaSessionInfo]:
    """A named session holding ``playlist_id``, to preselect in the picker."""
    for session in sessions:
        if not session.is_implicit and playlist_id in session.playlist_ids:
            return session
    return None


def _unique(values: Iterable[Optional[str]]) -> List[str]:
    return [v for v in dict.fromkeys(values) if v]


# --- HTTP --------------------------------------------------------------------


def _base(api_base_url: str) -> str:
    return api_base_url.rstrip("/")


def in_review_url(api_base_url: str, session_id: str) -> str:
    return f"{_base(api_base_url)}/sessions/{session_id}/in-review"


def session_url(api_base_url: str, session_id: str) -> str:
    return f"{_base(api_base_url)}/sessions/{session_id}"


def project_sessions_url(api_base_url: str, project_id: str) -> str:
    return f"{_base(api_base_url)}/projects/{project_id}/sessions"


def playlist_versions_url(api_base_url: str, playlist_id: str) -> str:
    return f"{_base(api_base_url)}/playlists/{playlist_id}/versions"


def ensure_session_url(api_base_url: str) -> str:
    return f"{_base(api_base_url)}/sessions/ensure-for-playlist"


def _send_json(
    method: str,
    url: str,
    body: Optional[dict],
    headers: Mapping[str, str],
    opener,
) -> InReviewResult:
    """Send a request with an optional JSON body. Never raises."""
    request_body = "" if body is None else json.dumps(body)
    request = urllib.request.Request(
        url,
        data=None if body is None else request_body.encode("utf-8"),
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


def _json_body(result: InReviewResult):
    try:
        return json.loads(result.response_body)
    except ValueError:
        return None


def _bad_response(result: InReviewResult, what: str) -> InReviewResult:
    return replace(
        result, ok=False, response_body=f"{what} in response: {result.response_body}"
    )


def list_project_sessions(
    api_base_url: str,
    project_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> Tuple[Optional[List[DnaSessionInfo]], InReviewResult]:
    """DNA sessions for a project. ``None`` on failure."""
    result = _send_json(
        "GET", project_sessions_url(api_base_url, project_id), None, headers, opener
    )
    if not result.ok:
        return None, result
    body = _json_body(result)
    if not isinstance(body, list):
        return None, _bad_response(result, "no session list")
    sessions = [DnaSessionInfo.from_api(raw) for raw in body]
    return [s for s in sessions if s is not None], result


def get_session(
    api_base_url: str,
    session_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> Tuple[Optional[DnaSessionInfo], InReviewResult]:
    """One DNA session. ``None`` on failure (404 when it was deleted)."""
    result = _send_json(
        "GET", session_url(api_base_url, session_id), None, headers, opener
    )
    if not result.ok:
        return None, result
    session = DnaSessionInfo.from_api(_json_body(result))
    if session is None:
        return None, _bad_response(result, "no session")
    return session, result


def get_playlist_version_ids(
    api_base_url: str,
    playlist_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> Tuple[Optional[frozenset], InReviewResult]:
    """Version ids on a production-tracking playlist. ``None`` on failure."""
    result = _send_json(
        "GET", playlist_versions_url(api_base_url, playlist_id), None, headers, opener
    )
    if not result.ok:
        return None, result
    body = _json_body(result)
    if not isinstance(body, list):
        return None, _bad_response(result, "no version list")
    ids = frozenset(
        str(v["id"]) for v in body if isinstance(v, Mapping) and v.get("id") is not None
    )
    return ids, result


def ensure_session_for_playlist(
    api_base_url: str,
    project_id: str,
    playlist_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> Tuple[Optional[str], InReviewResult]:
    """Find or create DNA's one-playlist session for ``playlist_id``."""
    result = _send_json(
        "POST",
        ensure_session_url(api_base_url),
        {"project_id": project_id, "playlist_id": playlist_id},
        headers,
        opener,
    )
    if not result.ok:
        return None, result
    session = DnaSessionInfo.from_api(_json_body(result))
    if session is None:
        return None, _bad_response(result, "no session id")
    return session.id, result


def set_in_review(
    api_base_url: str,
    session_id: str,
    playlist_id: str,
    version_id: str,
    headers: Mapping[str, str],
    opener=urllib.request.urlopen,
) -> InReviewResult:
    """Set the session's in-review version on ``playlist_id``.

    DNA answers 409 while the in-review version is pinned, and 404 when the
    session no longer exists.
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
