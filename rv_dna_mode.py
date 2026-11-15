#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""RV mode that signs in to DNA and pushes in-review version changes."""

from PySide6 import QtCore, QtWidgets
from rv import commands, extra_commands, qtutils, rvtypes

from rv_dna_auth import DnaEntityRef, SessionStore
from rv_dna_in_review import ensure_session_for_playlist, set_in_review
from rv_dna_signin_dialog import SignInDialog
from rv_dna_version_id import extract_version_id, load_version_id_paths

SETTINGS_GROUP = "DNA"
DEBUG_SETTING = "debug"

# DNA refusals that are expected in normal use and stay quiet outside debug
# mode: 400 version not on the playlist, 409 in-review pinned.
EXPECTED_REFUSALS = (400, 409)

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

    def _build_menu(self):
        if self._session:
            playlist = self._session.playlist.display_name
            status = f"Signed in as {self._session.email} — {playlist}"
            account_item = ("Sign Out", self.sign_out)
            switch_item = (
                "Switch show/playlist...",
                self.switch_playlist,
                None,
                lambda: commands.NeutralMenuState,
            )
        else:
            status = "Not signed in"
            account_item = ("Sign In...", self.sign_in)
            switch_item = (
                "Switch show/playlist...",
                self.switch_playlist,
                None,
                lambda: commands.DisabledMenuState,
            )
        return [
            (
                "DNA",
                [
                    (status, None, None, lambda: commands.DisabledMenuState),
                    (*account_item, None, lambda: commands.NeutralMenuState),
                    switch_item,
                    ("_", None),
                    ("Set DNA URL...", self.set_url, None, lambda: commands.NeutralMenuState),
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
            self._store.save_session(dialog.session)
            self._refresh()
            playlist = dialog.session.playlist.display_name
            extra_commands.displayFeedback(
                f"DNA playlist: {playlist} ({dialog.session.display_name})",
                3.0,
            )
            self._sync_current_source()
        elif result == SignInDialog.CHANGE_URL and self._prompt_for_url():
            self._run_desktop_flow(title=title)

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

        review_session_id = self._review_session_id(session)
        if not review_session_id:
            return

        result = set_in_review(
            session.api_base_url,
            review_session_id,
            session.playlist.id,
            version_id,
            session.auth_headers(),
        )
        self._debug_result(result)
        if result.ok:
            self._last_sent_version_id = version_id
            self._last_error = None
        elif result.status == 404:
            # The DNA session was deleted. Don't fall back to another session
            # silently; picking a playlist again stores a fresh one.
            extra_commands.displayFeedback(
                "DNA session not found. Choose DNA > Switch show/playlist...", 5.0
            )
        else:
            self._report_failure(result)

    def _review_session_id(self, session):
        """DNA session id for in-review updates, looked up once if needed."""
        if session.review_session:
            return session.review_session.id
        session_id, result = ensure_session_for_playlist(
            session.api_base_url,
            session.project.id,
            session.playlist.id,
            session.auth_headers(),
        )
        if not session_id:
            self._debug_result(result)
            self._report_failure(result)
            return None
        self._store_review_session(session, DnaEntityRef(id=session_id))
        return session_id

    def _store_review_session(self, session, review_session):
        updated = session.with_review_session(review_session)
        self._store.save_session(updated)
        self._session = updated

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
        if message == self._last_error:
            return
        self._last_error = message
        extra_commands.displayFeedback(message, 5.0)

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
