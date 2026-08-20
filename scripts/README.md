These scripts support building/releasing this repository, as opposed to `src/scripts/`, which are end-user helper scripts shipped with the installed setupEM package.

**build_pypi_readme.py** regenerates `README_pypi.md` at the repo root from `README.md`, rewriting relative `./doc/...` links and `<img src="./...">` tags to absolute GitHub URLs so images render on the PyPI package page. Run this before `python -m build`, from the repo root: `python scripts/build_pypi_readme.py`
