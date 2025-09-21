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

from enum import StrEnum, auto
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname


class PathKind(StrEnum):
    GIT_CLONE = "git clone"
    REMOTE_ZIP = "remote ZIP"
    LOCAL_ZIP = "local ZIP"
    LOCAL_COPY = "local copy"


@dataclass
class Classification:
    kind: PathKind
    exists: Optional[bool] = None  # Only checked for local paths
    is_git_repo: Optional[bool] = None
    normalized: Optional[str] = None


def classify_path(text: str) -> Classification:
    """Given something expected to be an installable path of some kind (either a local path or a URL
    of some variety), return a Classification object describing the path. This makes some
    approximations along the way for simplicity so may not handle all possible edge cases. If it
    isn't really anything recognizable, returns a Classification with kind=PathKind.LOCAL_COPY."""
    s = text.strip()
    parsed = urlparse(s)
    if parsed.scheme:
        if parsed.scheme == "file":
            local = file_url_to_path(parsed)
            return classify_local_path(local)
        if is_remote_zip_url(parsed):
            return Classification(kind=PathKind.REMOTE_ZIP, normalized=s)
        if is_git_url(parsed):
            return Classification(kind=PathKind.GIT_CLONE, normalized=s)
        return Classification(kind=PathKind.GIT_CLONE, normalized=s)
    if is_scp_like_git(s):
        return Classification(kind=PathKind.GIT_CLONE, normalized=s)
    return classify_local_path(Path(s).expanduser())


def addon_id_from_classification(c: Classification) -> str:
    """
    Given a Classification, extract the last path component (file or directory name),
    stripping any extension if present.

    Examples:
      /home/user/archive.zip -> "archive"
      /repos/myproject       -> "myproject"
      https://host/x/y.git   -> "y"
      git@github.com:org/repo.git -> "repo"
    """
    s = c.normalized or ""

    # Handle SCP-like (git@host:org/repo.git) specially
    if "@" in s and ":" in s and "://" not in s:
        right = s.split(":", 1)[1]
        last = Path(right).name
        return Path(last).stem

    # If it's a URL, parse and use its path
    parsed = urlparse(s)
    if parsed.scheme and parsed.path:
        return Path(parsed.path).stem

    # Fallback: treat it as a filesystem path
    return Path(s).stem


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


def classify_local_path(path: Path) -> Classification:
    if not path.exists():
        # Caller can decide whether to error or create; we report intent + exists=False
        return Classification(
            kind=PathKind.LOCAL_COPY, exists=False, is_git_repo=False, normalized=str(path)
        )

    if path.is_file() and path.suffix.lower() == ".zip":
        return Classification(
            kind=PathKind.LOCAL_ZIP, exists=True, is_git_repo=False, normalized=str(path)
        )

    if path.is_dir() and is_local_git_repo(path):
        # Treat existing local repo as a clone source (e.g., for `git clone --local`)
        return Classification(
            kind=PathKind.GIT_CLONE, exists=True, is_git_repo=True, normalized=str(path)
        )

    return Classification(
        kind=PathKind.LOCAL_COPY, exists=True, is_git_repo=False, normalized=str(path)
    )
