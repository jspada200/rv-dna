#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""RV mode that signs in to DNA and pushes in-review version changes."""

from PySide6 import QtWidgets
from rv import commands, extra_commands, qtutils, rvtypes

from rv_dna_auth import SessionStore
from rv_dna_in_review import set_in_review
from rv_dna_signin_dialog import SignInDialog
from rv_dna_version_id import extract_version_id, load_version_id_paths

SETTINGS_GROUP = "DNA"
DEBUG_SETTING = "debug"


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
        self.init(
            "dna",
            [
                ("frame-changed", self.on_frame_changed, "DNA"),
                ("source-group-complete", self.on_source_group_complete, "DNA"),
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
        self.setMenuStrict(self._build_menu())

    def toggle_debug(self, event=None):
        self._debug = not self._debug
        _write_setting(DEBUG_SETTING, "true" if self._debug else "false")
        self.setMenuStrict(self._build_menu())
        extra_commands.displayFeedback(
            f"DNA debug mode {'on' if self._debug else 'off'}", 2.0
        )

    def _debug_feedback(self, message, seconds=6.0):
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
        self._sync_current_source()

    def on_source_group_complete(self, event):
        self._last_source = None
        self._sync_current_source()

    def _sync_current_source(self):
        session = self._session
        if not session:
            return
        try:
            sources = commands.sourcesAtFrame(commands.frame())
        except Exception:
            return
        if not sources:
            return
        source = sources[0]
        if source == self._last_source:
            return
        self._last_source = source

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

        url = f"{session.api_base_url.rstrip('/')}/playlists/{session.playlist.id}/metadata"
        request_body = f'{{"in_review": "{version_id}"}}'
        if self._debug:
            self._debug_feedback(f"DNA debug request: PUT {url} {request_body}")

        result = set_in_review(
            session.api_base_url,
            session.playlist.id,
            version_id,
            session.auth_headers(),
        )
        if self._debug:
            status = result.status if result.status is not None else "network error"
            body = (result.response_body or "(empty)").replace("\n", " ")
            if len(body) > 240:
                body = body[:237] + "..."
            self._debug_feedback(
                f"DNA debug response: {status} {body}",
                8.0,
            )
        if result.ok:
            self._last_sent_version_id = version_id


the_mode = None


def createMode():
    global the_mode
    the_mode = DnaMode()
    return the_mode


def theMode():
    return the_mode
