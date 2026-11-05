"""OncoLake branding for the local Dagster UI. `make dev` runs this before starting Dagster.

Dagster has no setting for its logo, so this adds one stylesheet to the page Dagster serves.
Dagster's code isn't changed: its index.html gets one extra <link> line (and the OncoLake
favicon), and the logo and stylesheet go in a separate oncolake/ folder beside it.

- Safe to run any number of times.
- Reinstalling or upgrading Dagster removes it; the next `make dev` puts it back.
- If a Dagster update changes its page, this prints a warning and Dagster starts with its own
  logo. Branding never stops Dagster from starting.

    python -m orisun_iwadi.entrypoints.dagster.ui_branding            # apply
    python -m orisun_iwadi.entrypoints.dagster.ui_branding --remove   # undo
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
CSS = HERE / "static" / "dagster.css"
MARK = HERE.parent / "api" / "static" / "mark.svg"

MARKER = "<!-- oncolake-branding -->"
LINK = f'{MARKER}<link rel="stylesheet" href="__PATH_PREFIX__/oncolake/dagster.css"/>'
PNG_ICON = '<link rel="icon" type="image/png" href="__PATH_PREFIX__/favicon.png"/>'
SVG_ICON_HREF = 'href="__PATH_PREFIX__/favicon.svg"'
OUR_ICON_HREF = 'href="__PATH_PREFIX__/oncolake/mark.svg"'
FOLDER = "oncolake"


def webapp_build_dir() -> pathlib.Path:
    import dagster_webserver

    return pathlib.Path(dagster_webserver.__file__).parent / "webapp" / "build"


def _warn(message: str) -> None:
    print(f"OncoLake branding skipped: {message}. Dagster will show its own logo.",
          file=sys.stderr)


def _write_atomically(path: pathlib.Path, text: str) -> None:
    # Dagster reads index.html on every page load; never let it see a half-written file.
    tmp = path.with_suffix(".oncolake-tmp")
    tmp.write_text(text, encoding="utf8")
    os.replace(tmp, path)


def apply(build: pathlib.Path) -> bool:
    """Brand the UI in `build`. Returns False (after a warning) if it couldn't."""
    index = build / "index.html"
    try:
        html = index.read_text(encoding="utf8")
        if MARKER not in html and "</head>" not in html:
            _warn("Dagster's page has an unexpected layout")
            return False
        folder = build / FOLDER
        folder.mkdir(exist_ok=True)
        shutil.copyfile(CSS, folder / "dagster.css")
        shutil.copyfile(MARK, folder / "mark.svg")
        if MARKER in html:
            return True  # already branded; the files above are refreshed
        backup = build / "index.html.orig"
        if not backup.exists():
            backup.write_text(html, encoding="utf8")
        html = html.replace("</head>", f"{LINK}</head>", 1)
        html = html.replace(PNG_ICON, "").replace(SVG_ICON_HREF, OUR_ICON_HREF)
        _write_atomically(index, html)
        return True
    except OSError as e:
        _warn(str(e))
        return False


def remove(build: pathlib.Path) -> None:
    """Put Dagster's page back exactly as it was installed."""
    backup = build / "index.html.orig"
    if backup.exists():
        _write_atomically(build / "index.html", backup.read_text(encoding="utf8"))
        backup.unlink()
    shutil.rmtree(build / FOLDER, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        build = webapp_build_dir()
    except ImportError:
        _warn("dagster-webserver is not installed")
        return 0
    if "--remove" in args:
        remove(build)
        print("Removed OncoLake branding from the Dagster UI.")
    elif apply(build):
        print("OncoLake branding applied to the Dagster UI.")
    return 0  # never fail: Dagster should start either way


if __name__ == "__main__":
    sys.exit(main())
