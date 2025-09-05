# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2025 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of the FreeCAD Addon Manager.                       *
# *                                                                         *
# *   This is free software: you can redistribute it and/or modify it       *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   It is distributed in the hope that it will be useful, but             *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with the FreeCAD Addon Manager. If not, see             *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname


class InputKind(Enum):
    GIT_CLONE = auto()
    REMOTE_ZIP = auto()
    LOCAL_ZIP = auto()
    LOCAL_COPY = auto()


@dataclass
class Classification:
    kind: InputKind
    exists: Optional[bool] = None
    is_git_repo: Optional[bool] = None
    normalized: Optional[str] = None


# ---------------- helpers ----------------


def is_windows_drive_path(s: str) -> bool:
    return len(s) >= 3 and s[1] == ":" and s[0].isalpha() and s[2] in ("/", "\\")


def is_remote_zip_url(parsed) -> bool:
    return (
        parsed.scheme in {"http", "https", "ftp"}
        and bool(parsed.netloc)
        and parsed.path.lower().endswith(".zip")
    )


def is_git_url(parsed) -> bool:
    if parsed.scheme.startswith("git+"):
        return True
    if parsed.scheme in {"ssh", "git"} and parsed.netloc:
        return True
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        # Treat non-zip HTTP(S) as a potential git host (GitHub/GitLab/etc.)
        return not parsed.path.lower().endswith(".zip")
    return False


def is_scp_like_git(s: str) -> bool:
    # user@host:path or host:path (avoid Windows "C:\")
    if "://" in s or is_windows_drive_path(s):
        return False
    if ":" not in s:
        return False
    left, right = s.split(":", 1)
    if not right:
        return False
    host_part = left.split("@", 1)[-1]  # after optional user@
    # conservative checks so we don't misfire on odd inputs
    has_host = host_part not in ("", ".", "..")
    has_repoish_path = ("/" in right) or right.endswith(".git")
    return has_host and has_repoish_path


def file_url_to_path(parsed) -> Path:
    return Path(url2pathname(unquote(parsed.path)))


def looks_like_bare_git_dir(path: Path) -> bool:
    return (path / "HEAD").is_file() and (path / "objects").is_dir() and (path / "refs").is_dir()


def is_local_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir() or looks_like_bare_git_dir(path)


# ---------------- main API ----------------


def classify_input(text: str) -> Classification:
    s = text.strip()
    parsed = urlparse(s)

    # URL forms
    if parsed.scheme:
        if parsed.scheme == "file":
            local = file_url_to_path(parsed)
            return classify_local_path(local)
        if is_remote_zip_url(parsed):
            return Classification(kind=InputKind.REMOTE_ZIP, normalized=s)
        if is_git_url(parsed):
            return Classification(kind=InputKind.GIT_CLONE, normalized=s)
        # If you prefer to reject unknown schemes, handle that in your caller.
        return Classification(kind=InputKind.GIT_CLONE, normalized=s)

    # SCP-like git (git@host:org/repo.git)
    if is_scp_like_git(s):
        return Classification(kind=InputKind.GIT_CLONE, normalized=s)

    # Local paths
    return classify_local_path(Path(s).expanduser())


def classify_local_path(path: Path) -> Classification:
    if not path.exists():
        # Caller can decide whether to error or create; we report intent + exists=False
        return Classification(
            kind=InputKind.LOCAL_COPY, exists=False, is_git_repo=False, normalized=str(path)
        )

    if path.is_file() and path.suffix.lower() == ".zip":
        return Classification(
            kind=InputKind.LOCAL_ZIP, exists=True, is_git_repo=False, normalized=str(path)
        )

    if path.is_dir() and is_local_git_repo(path):
        # Treat existing local repo as a clone source (e.g., for `git clone --local`)
        return Classification(
            kind=InputKind.GIT_CLONE, exists=True, is_git_repo=True, normalized=str(path)
        )

    return Classification(
        kind=InputKind.LOCAL_COPY, exists=True, is_git_repo=False, normalized=str(path)
    )


# --------------- quick check ---------------
if __name__ == "__main__":
    samples = [
        "ssh://git@github.com/org/repo.git",
        "git://git.kernel.org/pub/scm/git/git.git",
        "https://github.com/org/repo",
        "git@github.com:org/repo.git",
        "https://github.com/org/repo/archive/refs/heads/main.zip",
        "file:///usr/src/project/repo.git",
        "file:///C:/Users/me/Downloads/data.zip",
        r"C:\Users\me\src\project",
        "/tmp/some-repo",
        "/tmp/file.zip",
        "relative/path",
    ]
    for s in samples:
        c = classify_input(s)
        print(
            f"{s:70} -> {c.kind.name:10} exists={c.exists} git_repo={c.is_git_repo} normalized={c.normalized}"
        )
