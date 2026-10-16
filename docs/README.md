# FinVault public website

This directory contains a standalone static website for GitHub Pages. It uses
plain HTML and one local stylesheet, with no build step, JavaScript, analytics,
external fonts, Gmail integration, or connection to the finance dashboard.

- `index.html`: project introduction.
- `privacy.html`: current Gmail access, local storage, logs, and data practices.
- `terms.html`: simple terms for personal, self-hosted use.
- `styles.css`: shared responsive styling.
- `.nojekyll`: serve the static files without Jekyll processing.


## Preview locally

From the repository root:

```bash
python3 -m http.server 8080 --bind 127.0.0.1 --directory docs
```

Visit <http://127.0.0.1:8080/> and check all three pages. Stop the server with
Ctrl+C. All page and stylesheet links are relative, so the site also works under
GitHub Pages' `/finvault/` project path.

