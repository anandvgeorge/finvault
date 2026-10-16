# FinVault

Centralised financial record, hosted on-prem. 

The public website and GitHub Pages publishing instructions are in [docs/](docs/README.md).

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
uv run --locked uvicorn finvault.api.main:app --reload
```

Open <http://127.0.0.1:8000/>. Both commands use `data/finvault.db`.

## Tests and CI

Run the unit tests locally:

```bash
uv run --locked python -m unittest discover -s tests -v
```

[GitHub Actions CI](.github/workflows/ci.yml) runs the same tests on pushes and
pull requests, and can be started manually from the Actions tab. It uses the
Python version in `.python-version` and the dependencies in `uv.lock`. Tests use
synthetic data and mocked Gmail access; no Google credentials or repository
secrets are required.

After tests pass on `main`, CI creates or advances the `production` branch to
that exact tested commit. Older runs skip promotion if `main` has moved on;
updates never force-push. Only the promotion job has repository write access,
using GitHub's automatically supplied token. A manual run on `main` can also
promote it after passing tests. Pi deployment is configured separately; updating
this branch alone does not restart the Pi application.

## License

FinVault is available under the [MIT License](LICENSE). You may use, modify,
and redistribute it, including commercially, provided the copyright and license
notices are retained in copies or substantial portions of the software.

Contact: [anandvgeorge@gmail.com](mailto:anandvgeorge@gmail.com).
