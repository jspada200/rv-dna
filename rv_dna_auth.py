#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""DNA sign-in session handling for the RV DNA package.

Pure Python with no RV or Qt imports so it can be unit tested outside RV.
"""

import json
from dataclasses import asdict, dataclass, replace
from typing import Callable, Optional
from urllib.parse import urlsplit

PAYLOAD_VERSION = 2
SIGNIN_PATH = "/desktop-signin"
AUTH_PROVIDERS = ("none", "google")

URL_SETTING = "url"
SESSION_SETTING = "session"

_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class InvalidPayloadError(ValueError):
    """Raised when the sign-in payload from the DNA page is malformed."""


def normalize_dna_url(url: str) -> str:
    """Return a canonical DNA frontend URL, or raise ValueError.

    A missing scheme defaults to http for local hosts and https otherwise.
    Query strings, fragments and trailing slashes are dropped.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Enter the DNA URL, for example https://dna.example.com")

    if "://" not in url:
        host = url.split("/", 1)[0].rsplit(":", 1)[0].lower()
        url = ("http://" if host in _LOCAL_HOSTS else "https://") + url

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.netloc:
        raise ValueError(f"Not a valid http(s) URL: {url}")

    return f"{scheme}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def signin_url(dna_url: str) -> str:
    return normalize_dna_url(dna_url) + SIGNIN_PATH


def url_origin(url: str) -> str:
    """Return scheme://host[:port] with default ports removed."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None or port == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def same_origin(a: str, b: str) -> bool:
    return url_origin(a) == url_origin(b)


@dataclass(frozen=True)
class DnaEntityRef:
    id: str
    name: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class DnaSession:
    token: str
    email: str
    user_id: str
    auth_provider: str
    api_base_url: str
    dna_url: str
    project: DnaEntityRef
    playlist: DnaEntityRef
    name: Optional[str] = None
    picture: Optional[str] = None
    # The DNA session (one or more playlists sharing in-review) that RV drives.
    # Sent by DNA's sign-in page; older DNA builds omit it, and the package
    # then looks up the playlist's own session (see rv_dna_in_review).
    review_session: Optional[DnaEntityRef] = None

    @property
    def display_name(self) -> str:
        return self.name or self.email

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def with_review_session(self, review_session: Optional[DnaEntityRef]) -> "DnaSession":
        return replace(self, review_session=review_session)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "DnaSession":
        raw = json.loads(data)
        project = raw.get("project")
        playlist = raw.get("playlist")
        if not isinstance(project, dict) or not isinstance(playlist, dict):
            raise ValueError("session is missing project or playlist")
        return cls(
            token=raw["token"],
            email=raw["email"],
            user_id=raw["user_id"],
            auth_provider=raw["auth_provider"],
            api_base_url=raw["api_base_url"],
            dna_url=raw["dna_url"],
            project=DnaEntityRef(id=str(project["id"]), name=project.get("name")),
            playlist=DnaEntityRef(id=str(playlist["id"]), name=playlist.get("name")),
            name=raw.get("name"),
            picture=raw.get("picture"),
            review_session=_entity_ref_or_none(raw.get("review_session")),
        )


def _entity_ref_or_none(value) -> Optional[DnaEntityRef]:
    if not isinstance(value, dict):
        return None
    entity_id = value.get("id")
    if entity_id is None or str(entity_id).strip() == "":
        return None
    name = value.get("name")
    return DnaEntityRef(id=str(entity_id), name=str(name) if name else None)


def _require_str(obj: dict, key: str, label: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidPayloadError(f"Sign-in payload is missing {label}")
    return value


def _optional_str(obj: dict, key: str) -> Optional[str]:
    value = obj.get(key)
    return value if isinstance(value, str) and value else None


def _parse_entity_ref(obj: dict, key: str, label: str) -> DnaEntityRef:
    value = obj.get(key)
    if not isinstance(value, dict):
        raise InvalidPayloadError(f"Sign-in payload is missing the {label}")
    entity_id = value.get("id")
    if entity_id is None or (isinstance(entity_id, str) and not entity_id.strip()):
        raise InvalidPayloadError(f"Sign-in payload is missing the {label} id")
    name = value.get("name")
    if name is not None and not isinstance(name, str):
        name = str(name)
    return DnaEntityRef(id=str(entity_id), name=name or None)


def parse_auth_payload(payload_json: str, dna_url: str) -> DnaSession:
    """Validate the JSON sent by the DNA desktop sign-in page."""
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError) as e:
        raise InvalidPayloadError(f"Sign-in payload is not valid JSON: {e}")

    if not isinstance(payload, dict):
        raise InvalidPayloadError("Sign-in payload must be a JSON object")

    if payload.get("version") != PAYLOAD_VERSION:
        raise InvalidPayloadError(
            f"Unsupported sign-in payload version: {payload.get('version')!r}"
        )

    user = payload.get("user")
    if not isinstance(user, dict):
        raise InvalidPayloadError("Sign-in payload is missing the user")

    auth_provider = payload.get("authProvider")
    if auth_provider not in AUTH_PROVIDERS:
        raise InvalidPayloadError(f"Unknown auth provider: {auth_provider!r}")

    api_base_url = _require_str(payload, "apiBaseUrl", "the API URL")
    try:
        api_base_url = normalize_dna_url(api_base_url)
    except ValueError as e:
        raise InvalidPayloadError(str(e))

    email = _require_str(user, "email", "the user email")
    return DnaSession(
        token=_require_str(payload, "token", "the token"),
        email=email,
        user_id=_optional_str(user, "id") or email,
        auth_provider=auth_provider,
        api_base_url=api_base_url,
        dna_url=normalize_dna_url(dna_url),
        project=_parse_entity_ref(payload, "project", "project"),
        playlist=_parse_entity_ref(payload, "playlist", "playlist"),
        name=_optional_str(user, "name"),
        picture=_optional_str(user, "picture"),
        review_session=_entity_ref_or_none(payload.get("session")),
    )


class SessionStore:
    """Persists the DNA URL and session through injected settings callables.

    ``read(name, default)`` and ``write(name, value)`` are bound to a
    settings group by the caller (RV's readSettings / writeSettings).
    """

    def __init__(
        self,
        read: Callable[[str, str], str],
        write: Callable[[str, str], None],
    ) -> None:
        self._read = read
        self._write = write

    @property
    def url(self) -> Optional[str]:
        value = self._read(URL_SETTING, "")
        try:
            return normalize_dna_url(value) if value else None
        except ValueError:
            return None

    def set_url(self, url: str) -> str:
        """Save a new DNA URL. Signs out when the URL changes."""
        normalized = normalize_dna_url(url)
        if normalized != self.url:
            self.clear_session()
        self._write(URL_SETTING, normalized)
        return normalized

    def load_session(self) -> Optional[DnaSession]:
        data = self._read(SESSION_SETTING, "")
        if not data:
            return None
        try:
            session = DnaSession.from_json(data)
        except (TypeError, ValueError, KeyError):
            return None
        if session.dna_url != self.url:
            return None
        return session

    def save_session(self, session: DnaSession) -> None:
        self._write(SESSION_SETTING, session.to_json())

    def clear_session(self) -> None:
        self._write(SESSION_SETTING, "")
