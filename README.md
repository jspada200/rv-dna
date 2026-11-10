# rv-dna

An RV package that connects [RV](https://github.com/AcademySoftwareFoundation/OpenRV)
to [DNA (Dailies Notes Assistant)](https://github.com/AcademySoftwareFoundation/dna).

This first version handles signing in to DNA. It adds a **DNA** menu to RV with:

- **Signed in as ...** / **Not signed in**: the current status.
- **Sign In...** / **Sign Out**: one item that changes with the status. Sign In
  opens DNA's sign-in page in a window inside RV; the window closes once you are
  signed in. Sign Out forgets the stored session.
- **Set DNA URL...**: the address of the DNA web app, for example
  `https://dna.example.com` or `http://localhost:5173` for a local stack.

## Requirements

- RV or OpenRV built with Qt 6 and PySide6 (tested with RV 2026.0.1 and OpenRV 4.0.2).
- A DNA frontend that includes the `/desktop-signin` route. Older DNA builds load
  the normal DNA app instead and RV never receives the sign-in.

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
3. The window closes and the DNA menu shows **Signed in as ...**.

The session is kept across RV restarts. Changing the DNA URL signs you out.

## How it works

RV loads `<DNA URL>/desktop-signin` in an embedded Chromium view. Before the page
loads, RV injects a small `dnaDesktopBridge` object using Qt WebChannel, and only
for pages on the configured DNA origin. When the user signs in, the DNA page calls
`dnaDesktopBridge.signInComplete(...)` with the token, the user, the auth provider
and the DNA API URL, so RV only needs the web app URL. The token never appears in
a URL.

Other RV packages can read the session with:

```python
import rv_dna_mode

session = rv_dna_mode.theMode().session()  # None when signed out
if session:
    headers = session.auth_headers()       # {"Authorization": "Bearer ..."}
    api = session.api_base_url
```

Settings are stored with RV's `writeSettings` under the `DNA` group: `url` (the
DNA web app URL) and `session` (JSON). The session is stored in plain text in
RV's preferences.

## Known limits

- Google sign-in has not been tested from inside RV yet. Google may refuse to
  sign in from an embedded browser.
- Google access tokens expire after about an hour and are not refreshed. Sign in
  again when DNA requests start returning 401.

## Development

```sh
make test      # unit tests for rv_dna_auth.py, run with RV's bundled Python
make package   # build dist/dna-<version>.rvpkg only
```

| File | Purpose |
| --- | --- |
| `rv_dna_mode.py` | The RV mode: DNA menu, URL prompt, sign in and out. |
| `rv_dna_signin_dialog.py` | The sign-in window: web view, injected bridge, error panel. |
| `rv_dna_auth.py` | URL helpers, payload validation, session storage. No RV or Qt imports. |
