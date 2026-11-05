"""The code version recorded with every run: the git commit the code was built from."""

from __future__ import annotations

import functools
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[3]


@functools.cache
def code_version() -> str:
    """GIT_SHA if set (CI bakes it into the image), else the local checkout's commit.

    A local checkout with uncommitted changes gets a "-dirty" suffix, so a run made from
    unsaved work can't be mistaken for one made from the commit.
    """
    if sha := os.getenv("GIT_SHA"):
        return sha

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                              check=True, timeout=5).stdout.strip()
    try:
        sha = git("rev-parse", "--short=12", "HEAD")
        return f"{sha}-dirty" if git("status", "--porcelain") else sha
    except (OSError, subprocess.SubprocessError):
        return "unknown"
