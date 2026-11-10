#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""RV mode that adds a DNA menu for signing in to DNA."""

from PySide6 import QtWidgets
from rv import commands, extra_commands, qtutils, rvtypes

from rv_dna_auth import SessionStore
from rv_dna_signin_dialog import SignInDialog

SETTINGS_GROUP = "DNA"


def _read_setting(name, default):
    return commands.readSettings(SETTINGS_GROUP, name, default)


def _write_setting(name, value):
    commands.writeSettings(SETTINGS_GROUP, name, value)


class DnaMode(rvtypes.MinorMode):
    def __init__(self):
        rvtypes.MinorMode.__init__(self)
        self._store = SessionStore(_read_setting, _write_setting)
        self._session = self._store.load_session()
        self.init("dna", None, None, self._build_menu())

    def session(self):
        """The signed-in DnaSession, or None."""
        return self._session

    def _build_menu(self):
        if self._session:
            status = f"Signed in as {self._session.email}"
            account_item = ("Sign Out", self.sign_out)
        else:
            status = "Not signed in"
            account_item = ("Sign In...", self.sign_in)
        return [
            (
                "DNA",
                [
                    (status, None, None, lambda: commands.DisabledMenuState),
                    (*account_item, None, lambda: commands.NeutralMenuState),
                    ("_", None),
                    ("Set DNA URL...", self.set_url, None, lambda: commands.NeutralMenuState),
                ],
            )
        ]

    def _refresh(self):
        self._session = self._store.load_session()
        self.setMenuStrict(self._build_menu())

    def sign_in(self, event=None):
        url = self._store.url or self._prompt_for_url()
        if not url:
            return

        dialog = SignInDialog(url, qtutils.sessionWindow())
        result = dialog.exec()
        if result == QtWidgets.QDialog.Accepted and dialog.session:
            self._store.save_session(dialog.session)
            self._refresh()
            extra_commands.displayFeedback(
                f"Signed in to DNA as {dialog.session.display_name}", 3.0
            )
        elif result == SignInDialog.CHANGE_URL and self._prompt_for_url():
            self.sign_in()

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


the_mode = None


def createMode():
    global the_mode
    the_mode = DnaMode()
    return the_mode


def theMode():
    return the_mode
