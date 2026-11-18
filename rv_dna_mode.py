#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""RV mode that signs in to DNA and pushes in-review version changes."""

import time

from PySide6 import QtCore, QtWidgets
from rv import commands, extra_commands, qtutils, rvtypes

from rv_dna_auth import DnaEntityRef, SessionStore
from rv_dna_in_review import (
    choose_playlist,
    ensure_session_for_playlist,
    get_playlist_version_ids,
    get_session,
    list_project_sessions,
    parse_sg_context,
    set_in_review,
    suggest_session,
)
from rv_dna_signin_dialog import SignInDialog
from rv_dna_version_id import extract_version_id, load_version_id_paths

SETTINGS_GROUP = "DNA"
DEBUG_SETTING = "debug"

# DNA refusals that are expected in normal use and stay quiet outside debug
# mode: 409 in-review pinned.
EXPECTED_REFUSALS = (409,)

# Screening Room announces what the user is browsing (a playlist, a shot...)
# as "id|type|name|project_id|valid". RV follows the playlist it reports.
SG_CONTEXT_EVENT = "sg-version-context-changed"

# How long a playlist's version list is reused before asking DNA again.
PLAYLIST_VERSIONS_TTL_S = 30.0

NO_SESSION_LABEL = "No session (each playlist's own)"

# Events RV sends while media is loading or the view changes. The new source
# is often not in the timeline yet when they fire, so each one schedules a
# short deferred sync instead of syncing inline.
LOAD_EVENTS = (
    "source-group-complete",
    "after-progressive-loading",
    "source-media-set",
    "after-graph-view-change",
)
LOAD_SYNC_DELAY_MS = 150
LOAD_SYNC_ATTEMPTS = 10


def _read_setting(name, default):
    return commands.readSettings(SETTINGS_GROUP, name, default)


def _write_setting(name, value):
    commands.writeSettings(SETTINGS_GROUP, name, value)


def _first_string_property(path):
    try:
        values = commands.getStringProperty(path)
    except Exception:
        return None
    if not values:
        return None
    value = values[0]
    return value if isinstance(value, str) and value else None


def gather_source_fields(source):
    """Collect property/metadata bags used by the version-ID extractor."""
    properties = {}
    metadata = {}

    movie = _first_string_property(f"{source}.media.movie")
    if movie:
        properties["File"] = movie
        properties["media.movie"] = movie

    try:
        info = commands.sourceMediaInfo(source)
    except Exception:
        info = None
    if isinstance(info, dict):
        for key in ("file", "File", "path", "movie"):
            value = info.get(key)
            if isinstance(value, str) and value:
                properties.setdefault("File", value)
                properties[key] = value
        for key, value in info.items():
            if isinstance(key, str) and key.lower() in (
                "version_id",
                "sg_version_id",
                "versionid",
            ):
                metadata[key] = value
                properties[key] = value

    for name in ("File", "version_id", "sg_version_id", "Version"):
        value = _first_string_property(f"{source}.attributes.{name}")
        if value:
            properties.setdefault(name, value)
            if name.lower() != "file":
                metadata.setdefault(name, value)

    return properties, metadata


class DnaMode(rvtypes.MinorMode):
    def __init__(self):
        rvtypes.MinorMode.__init__(self)
        self._store = SessionStore(_read_setting, _write_setting)
        self._session = self._store.load_session()
        self._version_id_paths = load_version_id_paths()
        self._debug = self._load_debug()
        self._last_source = None
        self._last_sent_version_id = None
        self._last_error = None
        # Playlist RV is on, from production tracking: (id, name) or None.
        self._rv_playlist = None
        self._playlist_versions = {}  # playlist id -> (fetched_at, frozenset)
        self._playlist_sessions = {}  # playlist id -> its own DNA session id
        self._load_sync_attempts = 0
        self._load_sync_timer = QtCore.QTimer()
        self._load_sync_timer.setSingleShot(True)
        self._load_sync_timer.setInterval(LOAD_SYNC_DELAY_MS)
        self._load_sync_timer.timeout.connect(self._run_load_sync)
        self.init(
            "dna",
            [
                ("frame-changed", self.on_frame_changed, "DNA"),
                *[(name, self.on_media_loaded, "DNA") for name in LOAD_EVENTS],
                (SG_CONTEXT_EVENT, self.on_sg_context_changed, "DNA"),
            ],
            None,
            self._build_menu(),
        )

    def session(self):
        """The signed-in DnaSession, or None."""
        return self._session

    def _load_debug(self):
        return str(_read_setting(DEBUG_SETTING, "false")).lower() in (
            "1",
            "true",
            "yes",
        )

    def _debug_menu_state(self):
        return (
            commands.CheckedMenuState if self._debug else commands.UncheckedMenuState
        )

    def _status_line(self):
        session = self._session
        if not session:
            return "Not signed in"
        if session.review_session:
            target = f"session {session.review_session.display_name}"
        else:
            target = "each playlist's own session"
        return f"Signed in as {session.email} — {target}"

    def _playlist_line(self):
        if self._rv_playlist:
            return f"Playlist: {self._rv_playlist[1]} (from RV)"
        if self._session:
            return f"Playlist: {self._session.playlist.display_name} (from sign-in)"
        return "Playlist: none"

    def _build_menu(self):
        signed_in = self._session is not None

        def state(enabled):
            return lambda: (
                commands.NeutralMenuState if enabled else commands.DisabledMenuState
            )

        account_item = (
            ("Sign Out", self.sign_out) if signed_in else ("Sign In...", self.sign_in)
        )
        return [
            (
                "DNA",
                [
                    (self._status_line(), None, None, state(False)),
                    (self._playlist_line(), None, None, state(False)),
                    (*account_item, None, state(True)),
                    ("Choose session...", self.choose_session, None, state(signed_in)),
                    (
                        "Switch show/playlist...",
                        self.switch_playlist,
                        None,
                        state(signed_in),
                    ),
                    ("_", None),
                    ("Set DNA URL...", self.set_url, None, state(True)),
                    (
                        "Debug mode",
                        self.toggle_debug,
                        None,
                        self._debug_menu_state,
                    ),
                ],
            )
        ]

    def _refresh(self):
        self._session = self._store.load_session()
        self._debug = self._load_debug()
        self._last_source = None
        self._last_sent_version_id = None
        self._last_error = None
        self._playlist_versions = {}
        self._playlist_sessions = {}
        self.setMenuStrict(self._build_menu())

    def toggle_debug(self, event=None):
        self._debug = not self._debug
        _write_setting(DEBUG_SETTING, "true" if self._debug else "false")
        self.setMenuStrict(self._build_menu())
        extra_commands.displayFeedback(
            f"DNA debug mode {'on' if self._debug else 'off'}", 2.0
        )

    def _debug_feedback(self, message, seconds=6.0):
        # The viewer truncates long messages; the console gets them in full.
        print(f"rv_dna: {message}", flush=True)
        extra_commands.displayFeedback(message, seconds)

    def sign_in(self, event=None):
        self._run_desktop_flow(title="Sign in to DNA")

    def switch_playlist(self, event=None):
        if not self._session:
            return
        self._run_desktop_flow(title="Select DNA playlist")

    def _run_desktop_flow(self, title):
        url = self._store.url or self._prompt_for_url()
        if not url:
            return

        dialog = SignInDialog(url, qtutils.sessionWindow(), title=title)
        result = dialog.exec()
        if result == QtWidgets.QDialog.Accepted and dialog.session:
            previous = self._session
            signed_in = dialog.session
            # Keep the chosen session across re-sign-ins in the same project.
            if (
                previous
                and previous.review_session
                and previous.project.id == signed_in.project.id
            ):
                signed_in = signed_in.with_review_session(previous.review_session)
            self._store.save_session(signed_in)
            self._refresh()
            extra_commands.displayFeedback(
                f"Signed in to DNA as {signed_in.display_name}", 3.0
            )
            if not signed_in.review_session:
                self.choose_session(only_if_sessions=True)
            self._sync_current_source()
        elif result == SignInDialog.CHANGE_URL and self._prompt_for_url():
            self._run_desktop_flow(title=title)

    def choose_session(self, event=None, only_if_sessions=False):
        """Pick the DNA session RV drives.

        ``only_if_sessions``: skip the picker when the project has no named
        sessions (DNA without the sessions feature only has implicit ones).
        """
        session = self._session
        if not session:
            return
        sessions, result = list_project_sessions(
            session.api_base_url, session.project.id, session.auth_headers()
        )
        if sessions is None:
            self._debug_result(result)
            if not only_if_sessions:
                QtWidgets.QMessageBox.warning(
                    qtutils.sessionWindow(),
                    "DNA sessions",
                    f"Could not load DNA sessions ({result.status or 'network error'}).",
                )
            return
        named = [s for s in sessions if not s.is_implicit]
        if only_if_sessions and not named:
            return

        current_id = session.review_session.id if session.review_session else None
        playlist_id = self._rv_playlist[0] if self._rv_playlist else session.playlist.id
        preselect = next((s for s in named if s.id == current_id), None)
        if preselect is None and current_id is None:
            preselect = suggest_session(named, playlist_id)

        labels = [NO_SESSION_LABEL] + [s.display_name for s in named]
        index = named.index(preselect) + 1 if preselect else 0
        label, ok = QtWidgets.QInputDialog.getItem(
            qtutils.sessionWindow(),
            "DNA session",
            "Session RV drives (RV follows the playlist it has open):",
            labels,
            index,
            False,
        )
        if not ok:
            return
        chosen = None if label == NO_SESSION_LABEL else named[labels.index(label) - 1]
        ref = DnaEntityRef(id=chosen.id, name=chosen.name) if chosen else None
        self._store.save_session(session.with_review_session(ref))
        self._refresh()
        extra_commands.displayFeedback(
            f"DNA session: {chosen.display_name if chosen else NO_SESSION_LABEL}", 3.0
        )
        self._sync_current_source()

    def sign_out(self, event=None):
        self._store.clear_session()
        self._refresh()
        extra_commands.displayFeedback("Signed out of DNA", 3.0)

    def set_url(self, event=None):
        self._prompt_for_url()

    def _prompt_for_url(self):
        """Ask for the DNA URL and save it. Returns the URL, or None if cancelled."""
        parent = qtutils.sessionWindow()
        text = self._store.url or ""
        while True:
            text, ok = QtWidgets.QInputDialog.getText(
                parent,
                "DNA URL",
                "DNA web app URL (for example https://dna.example.com):",
                QtWidgets.QLineEdit.Normal,
                text,
            )
            if not ok:
                return None
            try:
                url = self._store.set_url(text)
            except ValueError as e:
                QtWidgets.QMessageBox.warning(parent, "Invalid DNA URL", str(e))
                continue
            self._refresh()
            return url

    def on_frame_changed(self, event):
        # reject() lets other packages (Screening Room, ...) see the event too.
        event.reject()
        self._sync_current_source()

    def on_media_loaded(self, event):
        """New media or a new view: sync as soon as the source is on screen."""
        event.reject()
        # Players reuse source node names across loads, so forget the last one.
        self._last_source = None
        self._load_sync_attempts = LOAD_SYNC_ATTEMPTS
        self._load_sync_timer.start()  # restarts, so a burst of events coalesces

    def on_sg_context_changed(self, event):
        """Follow the playlist Screening Room has open."""
        event.reject()
        context = parse_sg_context(event.contents())
        if context is None or not context.is_playlist:
            return  # browsing something else: keep the last playlist
        playlist = (context.entity_id, context.name or context.entity_id)
        session = self._session
        if session and context.project_id and context.project_id != session.project.id:
            if self._debug:
                self._debug_feedback(
                    f"DNA debug: ignored playlist {playlist[1]} from project "
                    f"{context.project_id}; signed in to {session.project.display_name}"
                )
            return
        if playlist == self._rv_playlist:
            return
        self._rv_playlist = playlist
        self._last_sent_version_id = None
        self._last_error = None
        self.setMenuStrict(self._build_menu())
        if self._debug:
            self._debug_feedback(f"DNA debug: RV playlist is {playlist[1]} ({playlist[0]})")

    def _run_load_sync(self):
        if self._sync_current_source():
            return
        # Nothing at the current frame yet (still loading): try again shortly.
        self._load_sync_attempts -= 1
        if self._load_sync_attempts > 0:
            self._load_sync_timer.start()

    def _sync_current_source(self):
        """Push the on-screen version to DNA if it changed.

        Returns False only when there is no source at the current frame yet,
        so load-time callers know to retry.
        """
        session = self._session
        if not session:
            return True
        try:
            sources = commands.sourcesAtFrame(commands.frame())
        except Exception:
            return False
        if not sources:
            return False
        source = sources[0]
        if source == self._last_source:
            return True
        self._last_source = source
        self._push_source(session, source)
        return True

    def _push_source(self, session, source):
        properties, metadata = gather_source_fields(source)
        version_id = extract_version_id(
            properties=properties,
            metadata=metadata,
            paths=self._version_id_paths,
        )
        if not version_id:
            if self._debug:
                self._debug_feedback(
                    f"DNA debug: no version ID for {source} "
                    f"props={properties!r} meta={metadata!r}",
                    8.0,
                )
            return
        if version_id == self._last_sent_version_id:
            if self._debug:
                self._debug_feedback(
                    f"DNA debug: skipped, version {version_id} already sent"
                )
            return

        if session.review_session:
            dna_session, result = get_session(
                session.api_base_url, session.review_session.id, session.auth_headers()
            )
            if dna_session is None:
                self._debug_result(result)
                if result.status == 404:
                    self._notify_once(
                        "DNA session not found. Choose DNA > Choose session..."
                    )
                else:
                    self._report_failure(result)
                return
        else:
            dna_session = None

        rv_playlist_id = self._rv_playlist[0] if self._rv_playlist else None
        target = choose_playlist(
            version_id,
            rv_playlist_id or session.playlist.id,
            rv_playlist_id is not None,
            dna_session,
            lambda playlist_id: self._playlist_has_version(
                session, playlist_id, version_id
            ),
        )
        if target.playlist_id is None:
            if self._debug:
                self._debug_feedback(f"DNA debug: skipped, {target.skip_reason}")
            elif target.notify:
                self._notify_once(f"DNA: in review not set, {target.skip_reason}")
            return

        if dna_session is not None:
            session_id = dna_session.id
        else:
            session_id = self._playlist_session_id(session, target.playlist_id)
            if not session_id:
                return

        result = set_in_review(
            session.api_base_url,
            session_id,
            target.playlist_id,
            version_id,
            session.auth_headers(),
        )
        self._debug_result(result)
        if result.ok:
            self._last_sent_version_id = version_id
            self._last_error = None
        else:
            self._report_failure(result)

    def _playlist_has_version(self, session, playlist_id, version_id):
        """Whether the playlist holds the version; None if DNA can't say."""
        cached = self._playlist_versions.get(playlist_id)
        now = time.monotonic()
        if cached and now - cached[0] < PLAYLIST_VERSIONS_TTL_S and version_id in cached[1]:
            return True
        version_ids, result = get_playlist_version_ids(
            session.api_base_url, playlist_id, session.auth_headers()
        )
        if version_ids is None:
            self._debug_result(result)
            self._report_failure(result)
            return None
        self._playlist_versions[playlist_id] = (now, version_ids)
        return version_id in version_ids

    def _playlist_session_id(self, session, playlist_id):
        """The playlist's own DNA session (no session chosen), looked up once."""
        if playlist_id in self._playlist_sessions:
            return self._playlist_sessions[playlist_id]
        session_id, result = ensure_session_for_playlist(
            session.api_base_url,
            session.project.id,
            playlist_id,
            session.auth_headers(),
        )
        if not session_id:
            self._debug_result(result)
            self._report_failure(result)
            return None
        self._playlist_sessions[playlist_id] = session_id
        return session_id

    def _notify_once(self, message):
        if message == self._last_error:
            return
        self._last_error = message
        extra_commands.displayFeedback(message, 5.0)

    def _report_failure(self, result):
        """Tell the user a DNA request failed, once per distinct failure.

        Debug mode already shows every response; expected refusals stay quiet.
        """
        if self._debug or result.status in EXPECTED_REFUSALS:
            return
        if result.status is None:
            message = "DNA unreachable: in review is not syncing"
        elif result.status == 401:
            message = "DNA sign-in expired. Choose DNA > Sign In..."
        else:
            message = f"DNA rejected the in-review update ({result.status})"
        self._notify_once(message)

    def _debug_result(self, result):
        if not self._debug:
            return
        self._debug_feedback(
            f"DNA debug request: {result.method} {result.url} {result.request_body}"
        )
        status = result.status if result.status is not None else "network error"
        body = (result.response_body or "(empty)").replace("\n", " ")
        if len(body) > 240:
            body = body[:237] + "..."
        self._debug_feedback(f"DNA debug response: {status} {body}", 8.0)


the_mode = None


def createMode():
    global the_mode
    the_mode = DnaMode()
    return the_mode


def theMode():
    return the_mode
