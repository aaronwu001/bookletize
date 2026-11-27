# bookletize

Python package that rearranges PDF pages into foldable, saddle-stitched booklets.
Command line tool (`bookletize`) and tkinter desktop app (`bookletize-gui`).

## Layout

```
src/bookletize/
  core.py       page ordering + PDF placement; the only place with PDF logic
  cli.py        argparse front end -> core.make_booklets()
  gui.py        tkinter front end  -> core.make_booklets() (runs in a worker thread)
  __main__.py   python -m bookletize
tests/          pytest suite (builds tiny PDFs with pypdf; no fixtures on disk)
scripts/        PyInstaller entry points for the Windows .exe builds
docs/           screenshot, Traditional Chinese guide (使用說明.txt)
.github/workflows/
  ci.yml        tests on Linux / Windows / macOS, Python 3.9 / 3.12 / 3.13
  release.yml   on tag v*: wheel + sdist + Windows .exe -> GitHub Release; optional PyPI
```

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
bookletize --help
bookletize-gui            # needs python3-tk on Ubuntu: sudo apt install python3-tk
python -m build           # wheel + sdist into dist/
```

## Design notes

- Imposition: booklet of n pages (n % 4 == 0), sheet k front = (n-1-2k, 2k), back = (2k+1, n-2-2k).
- Pages are placed as Form XObjects with raw content bytes (never parsed); this keeps heavy PDFs fast
  and avoids pypdf 7's restriction on modifying pages not attached to a writer. Do not switch back to
  `merge_transformed_page` / `transfer_rotation_to_content`.
- `/Rotate` is handled inside `page_matrix()`, not by modifying the source page.
- Must work with pypdf 6 and 7. CI tests pypdf 6 on the ubuntu-24.04 / Python 3.9 job
  (ubuntu-latest has no Python 3.9).
- Printable annotations (highlights, comments, filled form fields) are drawn into the page XObject
  from their `/AP /N` streams, like a viewer printing; hidden or non-print annotations are skipped.
  Page `/Resources` may be shared between pages, so copy the cloned dict before adding to it.
- Dependency is `pypdf[crypto]` so AES-encrypted PDFs (owner password only) open; this also gets
  `cryptography` into the PyInstaller .exe. PDFs needing a user password raise `BookletError`.
- Bad input (unreadable, password, no pages, margin too large) raises `BookletError` from core;
  the CLI also turns `PyPdfError` / `OSError` into `error: ...` with exit code 2.
- Several booklets are written to separate files by default; `--merge` puts them in one file.
- Each booklet is padded to a multiple of 4 independently.
- `--margin` is in millimetres on the CLI and GUI; `core.make_booklets(margin=...)` takes points.
- User-facing text and docs: do not use a double dash as punctuation (CLI flags are fine).

## Releasing

Repo: github.com/aaronwu001/bookletize (WSL has `gh` logged in as aaronwu001).

1. Bump `version` in both `pyproject.toml` and `src/bookletize/__init__.py`.
2. `git tag vX.Y.Z && git push origin vX.Y.Z`; the Release workflow attaches the wheel, sdist,
   `bookletize-gui.exe` and `bookletize.exe` to a GitHub Release.
3. Ask the owner to test the .exe on Windows.

PyPI publishing is not used (the owner decided against it); the `pypi` job stays off unless the
repo variable `PUBLISH_PYPI=true` is set.
