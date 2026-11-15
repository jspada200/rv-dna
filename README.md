# rv-dna

An RV package that connects [RV](https://github.com/AcademySoftwareFoundation/OpenRV)
to [DNA (Dailies Notes Assistant)](https://github.com/AcademySoftwareFoundation/dna).

This package signs in to DNA, selects a show and playlist, and broadcasts
in-review version changes while you scrub media in RV. It adds a **DNA** menu
with:

- **Signed in as ... — playlist** / **Not signed in**: the current status.
- **Sign In...** / **Sign Out**: Sign In opens DNA's desktop sign-in page in a
  window inside RV (auth, then show, then playlist). The window closes once the
  selection is handed to RV. Sign Out forgets the stored session.
- **Switch show/playlist...**: reopen the picker without signing out.
- **Set DNA URL...**: the address of the DNA web app, for example
  `https://dna.example.com` or `http://localhost:5173` for a local stack.
- **Debug mode**: when checked, RV shows non-blocking viewer feedback for each
  in-review request and response (and when a sync is skipped). The same
  messages are printed in full to RV's console, prefixed `rv_dna:`.

## Requirements

- RV or OpenRV built with Qt 6 and PySide6 (tested with RV 2026.0.1 and OpenRV 4.0.2).
- A DNA build with sessions (in-review lives on `/sessions/{id}/in-review`), and
  a frontend that includes the `/desktop-signin` route and payload version 2
  (auth plus project and playlist). Older DNA builds will fail the handoff.

## Install

```sh
make install                                   # uses /Applications/RV.app
make install RV_APP="/Applications/RV 2.app"   # a different RV install
```

This builds `dist/dna-<version>.rvpkg` and installs it into
`~/Library/Application Support/RV`. Override `RV_SUPPORT_AREA` to install
somewhere else, or `RVPKG` to point at a specific `rvpkg` binary.

The package is optional. Enable it in RV under **Preferences > Packages** by
ticking **Load** next to **DNA**, then restart RV. To enable it for every user
of a support area instead, run `rvpkg -optin <path to the installed dna-<version>.rvpkg>`.

## Sign in

1. Choose **DNA > Sign In...**. If no DNA URL is saved yet, RV asks for it first.
2. Sign in the same way you do in the DNA web app (email, or Google when the DNA
   server uses Google sign-in).
3. Select a show, then a playlist.
4. The window closes and the DNA menu shows **Signed in as ... — playlist**.

The session (including show/playlist) is kept across RV restarts. Changing the
DNA URL signs you out.

## Version sync

RV syncs when the playhead moves onto a different source, and as soon as new
media is opened or the view changes (it retries for a moment while the media is
still loading), so a newly opened version goes in review without pressing play.

When the viewed source changes, RV extracts a version ID from configurable
search paths in `version_id_paths.json` and sets it in review on the DNA
session with `PUT /sessions/{session_id}/in-review`. The default paths look for:

1. A ShotGrid-style `File` URL containing `/file_serve/version/<id>/`
2. A `version_id` metadata or property value

The DNA session comes from the sign-in page. With DNA's sessions feature on,
that is the session picked at sign-in, which may hold several playlists. When
the sign-in carries no session, RV asks DNA for the playlist's own session once
(`POST /sessions/ensure-for-playlist`) and remembers it.

If no version ID is found, the version is not on the selected playlist, or
in-review is pinned, RV leaves in-review alone. Other failures show a message
over the viewer, once until the next successful update: DNA unreachable, an
expired sign-in (choose **DNA > Sign In...**), a deleted DNA session (choose
**DNA > Switch show/playlist...**), or any other rejection with its status code.

## How it works

RV loads `<DNA URL>/desktop-signin` in an embedded Chromium view. Before the page
loads, RV injects a small `dnaDesktopBridge` object using Qt WebChannel, and only
for pages on the configured DNA origin. When the user finishes show/playlist
selection, the DNA page calls `dnaDesktopBridge.signInComplete(...)` with the
token, the user, the auth provider, the DNA API URL, the selected project
and playlist, and the DNA session (when the page sends one). The token never
appears in a URL.

Other RV packages can read the session with:

```python
import rv_dna_mode

session = rv_dna_mode.theMode().session()  # None when signed out
if session:
    headers = session.auth_headers()       # {"Authorization": "Bearer ..."}
    api = session.api_base_url
    playlist_id = session.playlist.id
    review_session = session.review_session  # DNA session, or None until first sync
```

Settings are stored with RV's `writeSettings` under the `DNA` group: `url` (the
DNA web app URL) and `session` (JSON). The session is stored in plain text in
RV's preferences.

## Known limits

- Google sign-in has not been tested from inside RV yet. Google may refuse to
  sign in from an embedded browser.
- Google access tokens expire after about an hour and are not refreshed. Sign in
  again when DNA requests start returning 401.
- Playlist ID is chosen in the DNA UI; RV does not infer it from ShotGrid or
  the loaded session yet.

## Development

```sh
make test      # unit tests, run with RV's bundled Python
make package   # build dist/dna-<version>.rvpkg only
```

| File | Purpose |
| --- | --- |
| `rv_dna_mode.py` | The RV mode: DNA menu, URL prompt, sign in, version sync. |
| `rv_dna_signin_dialog.py` | The sign-in window: web view, injected bridge, error panel. |
| `rv_dna_auth.py` | URL helpers, payload validation, session storage. No RV or Qt imports. |
| `rv_dna_version_id.py` | Declarative version-ID extraction from source fields. |
| `rv_dna_in_review.py` | HTTP helpers for session in-review updates and session lookup. |
| `version_id_paths.json` | Ordered search paths for version IDs. |
