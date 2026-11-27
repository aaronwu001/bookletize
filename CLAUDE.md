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
- Must work with pypdf 6 and 7.
- Several booklets are written to separate files by default; `--merge` puts them in one file.
- Each booklet is padded to a multiple of 4 independently.
- `--margin` is in millimetres on the CLI and GUI; `core.make_booklets(margin=...)` takes points.
- User-facing text and docs: do not use a double dash as punctuation (CLI flags are fine).

## First-time setup checklist (for Claude Code)

The owner only has the old single-file `booklet.py`; this repo replaces it. Steps:

1. Ask the owner for their GitHub username, then replace `<your-username>` in `README.md`
   and `<你的帳號>` in `docs/使用說明.txt`.
2. Confirm the name in `LICENSE` (currently "Aaron").
3. Create a venv, `pip install -e ".[dev]"`, run `pytest -q` (expect all tests to pass).
4. `sudo apt install python3-tk` if needed, then launch `bookletize-gui` once to check it opens.
5. `git init -b main`, commit everything, `gh repo create bookletize --public --source . --push`.
6. Check the CI workflow passes on GitHub (`gh run watch`).
7. Release: `git tag v1.0.0 && git push origin v1.0.0`; the Release workflow attaches the wheel and
   `bookletize-gui.exe` / `bookletize.exe`. Ask the owner to download the .exe on Windows and test it.
8. Optional PyPI: check the name `bookletize` is still free on pypi.org, register the repo as a
   Trusted Publisher (workflow `release.yml`, environment `pypi`), set repo variable
   `PUBLISH_PYPI=true`, then push a new tag. Bump `version` in both `pyproject.toml` and
   `src/bookletize/__init__.py` first.
9. Delete this checklist section once done.
