#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""Embedded webview that runs the DNA desktop sign-in page."""

import json

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView

from rv_dna_auth import (
    InvalidPayloadError,
    parse_auth_payload,
    same_origin,
    signin_url,
    url_origin,
)

BRIDGE_OBJECT_NAME = "dnaDesktopBridge"
BRIDGE_READY_EVENT = "dna-desktop-bridge-ready"
DNA_BACKGROUND = "#0d0d12"

_BOOTSTRAP_JS = """
(function () {
  if (window.location.origin !== %(origin)s) return;
  function connect() {
    if (typeof qt === 'undefined' || !qt.webChannelTransport) return false;
    new QWebChannel(qt.webChannelTransport, function (channel) {
      window.%(name)s = channel.objects.%(name)s;
      window.dispatchEvent(new Event(%(event)s));
    });
    return true;
  }
  if (!connect()) {
    document.addEventListener('DOMContentLoaded', connect, { once: true });
  }
})();
"""


def _read_qwebchannel_js():
    resource = QtCore.QFile(":/qtwebchannel/qwebchannel.js")
    if not resource.open(QtCore.QIODevice.ReadOnly):
        raise RuntimeError("Qt WebChannel JavaScript resource is unavailable")
    try:
        return bytes(resource.readAll()).decode("utf-8")
    finally:
        resource.close()


class _Bridge(QtCore.QObject):
    """The only object exposed to the DNA page."""

    payloadReceived = QtCore.Signal(str)

    @QtCore.Slot(str)
    def signInComplete(self, payload_json):
        self.payloadReceived.emit(payload_json)


class _PopupDialog(QtWidgets.QDialog):
    """Hosts windows the page opens, such as Google's sign-in popup."""

    def __init__(self, profile, parent):
        super().__init__(parent)
        self.setWindowTitle("Sign in")
        self.resize(500, 640)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

        self.page = _DnaPage(profile, self, popup_parent=parent)
        self.page.windowCloseRequested.connect(self.close)
        self.page.titleChanged.connect(self.setWindowTitle)

        view = QWebEngineView(self)
        view.setPage(self.page)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)


class _DnaPage(QWebEnginePage):
    def __init__(self, profile, parent, popup_parent):
        super().__init__(profile, parent)
        self._popup_parent = popup_parent
        self.setBackgroundColor(QtGui.QColor(DNA_BACKGROUND))

    def createWindow(self, _window_type):
        popup = _PopupDialog(self.profile(), self._popup_parent)
        popup.show()
        return popup.page


class SignInDialog(QtWidgets.QDialog):
    """Loads DNA's /desktop-signin route and waits for the session handoff.

    ``exec()`` returns ``Accepted`` with ``self.session`` set on success,
    ``Rejected`` when the user closes the window, or ``CHANGE_URL`` when the
    user asks to edit the DNA URL from the error panel.
    """

    CHANGE_URL = 2

    def __init__(self, dna_url, parent=None, title="Sign in to DNA"):
        super().__init__(parent)
        self.session = None
        self._dna_url = dna_url
        self._signin_url = signin_url(dna_url)

        self.setWindowTitle(title)
        self.resize(520, 680)
        self.setStyleSheet(f"QDialog {{ background: {DNA_BACKGROUND}; }}")

        self._profile = QWebEngineProfile()
        self._page = _DnaPage(self._profile, None, popup_parent=self)
        self._install_bridge()

        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._page.loadFinished.connect(self._on_load_finished)

        self._stack = QtWidgets.QStackedWidget(self)
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._build_error_panel())

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._load()

    def _install_bridge(self):
        self._bridge = _Bridge(self)
        self._bridge.payloadReceived.connect(self._on_payload)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject(BRIDGE_OBJECT_NAME, self._bridge)
        self._page.setWebChannel(self._channel)

        script = QWebEngineScript()
        script.setName("dna-desktop-bridge")
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(
            _read_qwebchannel_js()
            + _BOOTSTRAP_JS
            % {
                "origin": json.dumps(url_origin(self._dna_url)),
                "name": BRIDGE_OBJECT_NAME,
                "event": json.dumps(BRIDGE_READY_EVENT),
            }
        )
        self._page.scripts().insert(script)

    def _build_error_panel(self):
        panel = QtWidgets.QWidget(self)
        panel.setStyleSheet(
            "QLabel { color: #f0f0f5; font-size: 14px; }"
            "QLabel#detail { color: #a0a0b8; font-size: 12px; }"
            "QPushButton { color: #f0f0f5; background: #1a1a24;"
            " border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px;"
            " padding: 8px 16px; }"
            "QPushButton:hover { background: #22222f; }"
            "QPushButton#primary { background: #8b5cf6; border: none; }"
            "QPushButton#primary:hover { background: #7c3aed; }"
        )

        title = QtWidgets.QLabel("Could not reach DNA", panel)
        title.setAlignment(QtCore.Qt.AlignCenter)
        detail = QtWidgets.QLabel(self._signin_url, panel)
        detail.setObjectName("detail")
        detail.setAlignment(QtCore.Qt.AlignCenter)
        detail.setWordWrap(True)

        retry = QtWidgets.QPushButton("Retry", panel)
        retry.setObjectName("primary")
        retry.clicked.connect(self._load)
        change = QtWidgets.QPushButton("Change URL...", panel)
        change.clicked.connect(lambda: self.done(self.CHANGE_URL))

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(change)
        buttons.addWidget(retry)
        buttons.addStretch()

        layout = QtWidgets.QVBoxLayout(panel)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addSpacing(12)
        layout.addLayout(buttons)
        layout.addStretch()
        return panel

    def _load(self):
        self._stack.setCurrentIndex(0)
        self._view.load(QtCore.QUrl(self._signin_url))

    def _on_load_finished(self, ok):
        if not ok and not self.session:
            self._stack.setCurrentIndex(1)

    def _on_payload(self, payload_json):
        page_url = self._page.url().toString()
        if not same_origin(page_url, self._dna_url):
            print(f"DNA: ignored sign-in from unexpected origin {page_url}")
            return
        try:
            self.session = parse_auth_payload(payload_json, self._dna_url)
        except InvalidPayloadError as e:
            QtWidgets.QMessageBox.warning(self, "DNA sign-in failed", str(e))
            return
        self.accept()

    def done(self, result):
        super().done(result)
        self._teardown()

    def _teardown(self):
        if self._page is None:
            return
        self._page.loadFinished.disconnect(self._on_load_finished)
        self._view.setPage(QWebEnginePage(self._view))
        for popup in self.findChildren(_PopupDialog):
            popup.page.deleteLater()
            popup.deleteLater()
        self._page.deleteLater()
        self._profile.deleteLater()
        self._page = None
