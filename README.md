# FinVault

Self-hosted personal finance tracker powered by Gmail transaction emails.

The public website and GitHub Pages publishing instructions are in [docs/](docs/README.md).

## Architecture

```mermaid
flowchart LR
    Gmail["Gmail<br/>Read-only OAuth"] -->|New emails| Ingest["Email ingestion"]

    subgraph SQLite["Local SQLite · data/finvault.db"]
        Emails["Saved emails"]
        Transactions["Transactions"]
    end

    Ingest --> Emails
    Emails -->|Without a saved transaction| Parser["Transaction parser"]
    Parser --> Transactions
    Transactions -->|Transaction data| API["FastAPI + Uvicorn"]
    Categories["Private categories.toml"] -->|Category definitions| API
    API -->|HTML + JSON| Dashboard["Browser dashboard"]
```

Emails with saved transactions are not reparsed; failed or unsupported emails
remain eligible for a later attempt.

## Local testing

Run these commands from the repository root. Install dependencies first:

```bash
uv sync --locked
```

Run Gmail ingestion and transaction processing (`src/finvault/__main__.py`).
Place your Google OAuth `credentials.json` in the repository root; the first run
opens a browser for authorization and saves `token.json`.

```bash
uv run --locked python -m finvault
```

Start the UI server (`src/finvault/api/main.py`):

```bash
uv run --locked uvicorn finvault.api.main:app --host 0.0.0.0 --reload
```

On the same machine, open <http://127.0.0.1:8000/>. From another machine over
Tailscale, open `http://<server-tailscale-ip>:8000/`. The server listens on all
IPv4 interfaces, including the LAN. Both commands use `data/finvault.db`.

## Categories

Category IDs and labels come entirely from `categories.toml` in the server's
working directory. Create your private file from the tracked example:

```bash
cp categories.example.toml categories.toml
chmod 600 categories.toml
```

On the laptop, keep this file in the project root. On the Pi, keep it at
`~/.local/share/finvault/categories.toml`, alongside the runtime data and OAuth
files, outside `current/` and `releases/`. After deploying this version, initialize
it on the Pi if you have not already copied your private config there:

```bash
cp "$HOME/.local/share/finvault/current/categories.example.toml" \
  "$HOME/.local/share/finvault/categories.toml"
chmod 600 "$HOME/.local/share/finvault/categories.toml"
```

Only `categories.example.toml` belongs in Git; `categories.toml` is ignored.
Set `FINVAULT_CATEGORIES_CONFIG` to override the file path if needed. A missing
file provides an empty catalog; an invalid file produces an explicit error.

Edit `[categories]` to add IDs and labels. Keep IDs stable when renaming labels
so existing transaction assignments remain intact. Refresh the dashboard to load
changes on desktop and mobile; no server restart is needed. Removed definitions
remain visible as unavailable on transactions that already use them.

Use **Settings** at the dashboard's top right (or open `/settings`) to edit
categories and keyword rules. Add categories, rename labels, and move rules up or
down to change priority. On desktop, category tiles stay visible beside the rules
as you scroll; click or tap a name to rename it, then Enter to finish or Escape
to cancel that rename. Enter one keyword per line and choose Either, Debit, or
Credit for each rule. Save changes writes the server's private `categories.toml`;
Discard reloads its saved settings. Editing fields alone does not write the file.
Saving rewrites TOML formatting and removes comments. Laptop and Pi settings remain
separate; opening the Pi's page from your laptop edits the Pi's file.

- `GET /categories`: category IDs and labels only, without private keyword rules.
- `GET /settings/categories`: complete category and rule settings.
- `PUT /settings/categories`: validate and replace the complete settings, saved
  atomically with file permissions `600`.

The settings API uses `{"categories": [{"id": "custom", "label": "Custom"}],
"rules": []}`. Keyword rules can be configured and saved, but automatic
assignment is not implemented yet.

## Tests and CI

Run the unit tests locally:

```bash
uv run --locked python -m unittest discover -s tests -v
```

Run dashboard and settings interaction tests with Node.js (no npm install needed):

```bash
node --test tests/*.test.cjs
```

[GitHub Actions CI](.github/workflows/ci.yml) runs the same tests on pushes and
pull requests, and can be started manually from the Actions tab. It uses the
Python version in `.python-version` and the dependencies in `uv.lock`. Tests use
synthetic data and mocked Gmail access; no Google credentials or repository
secrets are required.

## Development workflow

Develop, test, and commit code changes on `dev`, then push:

```bash
git switch dev
git push origin dev
```

When ready to release, open a pull request on GitHub with **base: `main`** and
**compare: `dev`**. Review the test results, then merge the PR.

Pushes to `dev` and pull requests run tests without deploying. After a merge,
CI tests `main` again and promotes its passing commit to `production`. The Pi
deploys it on the next update check. CI maintains the `production` branch.

## Deployment architecture

```mermaid
flowchart TB
    Laptop["Laptop<br/>Develop and test"] -->|git push| Dev

    subgraph GitHub["GitHub"]
        Dev["dev"] --> DevCI["Run tests on dev"]
        Dev --> PR["Pull request<br/>dev → main"]
        PR --> PRCI["Run tests on the PR"]
        PRCI -->|Review and merge| Main["main"]
        Main --> CI["Run tests on main"]
        CI -->|Tests pass on current main| Production["production<br/>Exact tested commit"]
    end

    subgraph Pi["Raspberry Pi · systemd user services"]
        Timer["finvault-deploy.timer<br/>5 min after the last check"] --> Deploy["finvault-deploy.service<br/>Deployment script"]
        Deploy --> Clone["~/finvault<br/>Git clone"]
        Clone -->|Extract commit + uv sync --locked| Release["releases/SHA/<br/>Code + Python environment"]
        Release -->|Switch active release| Current["current symlink"]
        Current --> UI["finvault-ui.service<br/>Dashboard on port 8000"]
        Current --> Ingest["finvault-ingest.service"]
        Schedule["finvault-ingest.timer<br/>Every 15 minutes"] --> Ingest
    end

    Laptop -->|Browser over Tailscale| UI
    Production -->|Pi pulls code| Clone
```

Releases and `current` live under `~/.local/share/finvault/`. The database,
OAuth files, and logs stay outside the release directories.

Unchanged commits skip deployment. Updates wait for ingestion to finish, restart
the UI, and check startup; failed startup triggers a rollback to the previous
code. Database changes are not rolled back.

See the [Pi deployment guide](deploy/README.md) for setup commands.

## License

FinVault is available under the [MIT License](LICENSE). You may use, modify,
and redistribute it, including commercially, provided the copyright and license
notices are retained in copies or substantial portions of the software.

Contact: [anandvgeorge@gmail.com](mailto:anandvgeorge@gmail.com).
