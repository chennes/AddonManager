# SPDX-License-Identifier: LGPL-2.1-or-later

# pylint: import-outside-toplevel,

"""Tests for the AddonCatalog and AddonCatalogEntry classes."""
from pathlib import Path
from unittest import mock, main, TestCase
from unittest.mock import patch
from urllib.parse import urlparse
from pyfakefs.fake_filesystem_unittest import TestCase as PyFakeFSTestCase

from addonmanager_path_utilities import (
    Classification,
    PathKind,
    classify_path,
    classify_local_path,
    file_url_to_path,
    is_git_url,
    is_local_git_repo,
    is_remote_zip_url,
    is_scp_like_git,
    is_windows_drive_path,
    looks_like_bare_git_dir,
)


class TestPathUtilitiesNoFS(TestCase):
    """Tests for the path utilities that don't need filesystem access."""

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_is_windows_drive_path_true_cases(self):
        cases = [
            "C:/",
            "C:\\",
            "d:/",
            "z:\\",
            "E:/folder",
            "c:\\windows",
        ]
        for s in cases:
            with self.subTest(s=s):
                self.assertTrue(is_windows_drive_path(s), s)

    def test_is_windows_drive_path_false_cases(self):
        cases = [
            "",  # too short
            "C:",  # too short for slash
            "3:/",  # first char not alpha
            "/C:/",  # starts with slash
            "CC:/",  # colon not at index 1
            "c:abc",  # third char not slash or backslash
            "\\\\server\\share",  # UNC path
            "C//",  # wrong third char (second is also '/')
            "C-/",  # wrong second char
        ]
        for s in cases:
            with self.subTest(s=s):
                self.assertFalse(is_windows_drive_path(s), s)

    def test_is_remote_zip_url_true_cases(self):
        cases = [
            "http://example.com/file.zip",
            "https://example.com/archive.ZIP",
            "ftp://host/path/to/addon.zip",
        ]
        for url in cases:
            with self.subTest(url=url):
                parsed = urlparse(url)
                self.assertTrue(is_remote_zip_url(parsed), url)

    def test_is_remote_zip_url_false_cases(self):
        cases = [
            "http://example.com/file.tar.gz",  # not .zip
            "https://",  # missing netloc
            "https://example.com/noext",  # path without .zip
            "file:///tmp/archive.zip",  # scheme not allowed
            "git://example.com/repo.zip",  # scheme not allowed
            "",  # empty string parse
        ]
        for url in cases:
            with self.subTest(url=url):
                parsed = urlparse(url)
                self.assertFalse(is_remote_zip_url(parsed), url)

    def test_is_git_url_true_cases(self):
        cases = [
            "git+https://github.com/org/repo",  # scheme startswith git+
            "git+ssh://git@example.com/repo.git",  # git+ssh should match via startswith
            "ssh://git@example.com/repo.git",  # ssh with netloc
            "git://example.com/repo.git",  # git with netloc
            "http://github.com/org/repo",  # http, non-zip path
            "https://gitlab.com/org/repo.git",  # https, non-zip path
            "https://example.com/repo",  # https, no .zip extension
        ]
        for url in cases:
            with self.subTest(url=url):
                parsed = urlparse(url)
                self.assertTrue(is_git_url(parsed), url)

    def test_is_git_url_false_cases(self):
        cases = [
            "http://example.com/archive.zip",  # http but .zip path
            "https://example.com/path/to/file.ZIP",  # https but .zip (case-insensitive)
            "file:///local/path/repo",  # unsupported scheme
            "ftp://example.com/repo.git",  # unsupported scheme
            "ssh://",  # missing netloc
            "git://",  # missing netloc
            "",  # empty string parse
        ]
        for url in cases:
            with self.subTest(url=url):
                parsed = urlparse(url)
                self.assertFalse(is_git_url(parsed), url)

    def test_is_scp_like_git_true_cases(self):
        cases = [
            "git@github.com:org/repo.git",  # typical GitHub SSH form
            "user@host:project/repo",  # with user@
            "host:repo.git",  # bare host with .git
            "example.com:org/repo",  # host with slash
        ]
        for s in cases:
            with self.subTest(s=s):
                self.assertTrue(is_scp_like_git(s), s)

    def test_is_scp_like_git_false_cases(self):
        cases = [
            "https://github.com/org/repo.git",  # contains ://
            "C:/repo",  # Windows drive path
            "C:\\repo",  # Windows drive path with backslash
            "host:",  # colon but no right part
            "user@:repo.git",  # empty host after user@
            "host:..",  # right part invalid, not repoish
            "host:folder",  # no slash and not .git
            "://malformed",  # colon-slash-slash => early return
            "no_colon_here",  # missing colon
        ]
        for s in cases:
            with self.subTest(s=s):
                self.assertFalse(is_scp_like_git(s), s)

    @patch("addonmanager_path_utilities.classify_local_path")
    @patch("addonmanager_path_utilities.file_url_to_path")
    def test_classify_input_file_url_delegates_to_file_and_local(
        self, m_file_url_to_path, m_classify_local_path
    ):
        # Arrange
        sentinel_path = Path("/mapped/from/url")
        sentinel_result = Classification(kind=PathKind.LOCAL_COPY, normalized="/mapped/from/url")
        m_file_url_to_path.return_value = sentinel_path
        m_classify_local_path.return_value = sentinel_result

        # Act
        out = classify_path("file:///mapped/from/url")

        # Assert
        self.assertIs(out, sentinel_result)
        m_file_url_to_path.assert_called_once()
        m_classify_local_path.assert_called_once_with(sentinel_path)

    @patch("addonmanager_path_utilities.is_git_url", return_value=False)
    @patch("addonmanager_path_utilities.is_remote_zip_url", return_value=True)
    def test_classify_input_remote_zip_branch(self, m_is_zip, m_is_git):
        out = classify_path("https://example.com/a.zip")
        self.assertEqual(out.kind, PathKind.REMOTE_ZIP)
        self.assertEqual(out.normalized, "https://example.com/a.zip")
        m_is_zip.assert_called_once()
        # is_git_url must not be needed if zip matched first
        m_is_git.assert_not_called()

    @patch("addonmanager_path_utilities.is_git_url", return_value=True)
    @patch("addonmanager_path_utilities.is_remote_zip_url", return_value=False)
    def test_classify_input_git_url_branch(self, m_is_zip, m_is_git):
        out = classify_path("https://example.com/org/repo")
        self.assertEqual(out.kind, PathKind.GIT_CLONE)
        self.assertEqual(out.normalized, "https://example.com/org/repo")
        m_is_zip.assert_called_once()
        m_is_git.assert_called_once()

    @patch("addonmanager_path_utilities.is_git_url", return_value=False)
    @patch("addonmanager_path_utilities.is_remote_zip_url", return_value=False)
    def test_classify_input_unknown_scheme_defaults_to_git_clone(self, m_is_zip, m_is_git):
        out = classify_path("weird://host/path")
        self.assertEqual(out.kind, PathKind.GIT_CLONE)
        self.assertEqual(out.normalized, "weird://host/path")
        m_is_zip.assert_called_once()
        m_is_git.assert_called_once()

    @patch("addonmanager_path_utilities.is_scp_like_git", return_value=True)
    def test_classify_input_scp_like_git_branch(self, m_is_scp):
        s = "git@github.com:org/repo.git"
        out = classify_path(s)
        self.assertEqual(out.kind, PathKind.GIT_CLONE)
        self.assertEqual(out.normalized, s)
        m_is_scp.assert_called_once_with(s)

    @patch("addonmanager_path_utilities.classify_local_path")
    @patch.object(Path, "expanduser")
    @patch("addonmanager_path_utilities.is_scp_like_git", return_value=False)
    def test_classify_input_local_path_delegates_to_classify_local_path_with_expansion(
        self, m_is_scp, m_expanduser, m_classify_local_path
    ):
        # Arrange
        input_s = "~/projects/demo"
        expanded = Path("/home/user/projects/demo")
        m_expanduser.return_value = expanded
        sentinel_result = Classification(kind=PathKind.LOCAL_COPY, normalized=str(expanded))
        m_classify_local_path.return_value = sentinel_result

        # Act
        out = classify_path(input_s)

        # Assert
        self.assertIs(out, sentinel_result)
        m_is_scp.assert_called_once_with(input_s)
        m_expanduser.assert_called_once()  # ensures expansion was attempted
        m_classify_local_path.assert_called_once_with(expanded)


class TestPathUtilitiesWithFS(PyFakeFSTestCase):
    """Tests for path utilities that require filesystem interactions."""

    def setUp(self):
        self.setUpPyfakefs()  # initialize fake filesystem

    def test_file_url_to_path_simple(self):
        parsed = urlparse("file:///tmp/demo.txt")
        p = file_url_to_path(parsed)
        self.assertIsInstance(p, Path)
        self.assertEqual(str(p), "/tmp/demo.txt")

    def test_file_url_to_path_with_spaces_and_encoding(self):
        parsed = urlparse("file:///C:/Program%20Files/MyApp")
        p = file_url_to_path(parsed)
        self.assertIn("Program Files", str(p))

    def test_looks_like_bare_git_dir_true_when_head_file_and_dirs_exist(self):
        repo = Path("/bare-repo")
        self.fs.create_file(repo / "HEAD")
        self.fs.create_dir(repo / "objects")
        self.fs.create_dir(repo / "refs")
        self.assertTrue(looks_like_bare_git_dir(repo))

    def test_looks_like_bare_git_dir_false_when_missing_any_component(self):
        repo = Path("/bare-incomplete")
        self.fs.create_file(repo / "HEAD")
        self.fs.create_dir(repo / "objects")
        self.assertFalse(looks_like_bare_git_dir(repo))

    def test_is_local_git_repo_true_with_dot_git_dir(self):
        repo = Path("/cloned-repo")
        self.fs.create_dir(repo / ".git")
        self.assertTrue(is_local_git_repo(repo))

    def test_is_local_git_repo_true_with_bare_repo(self):
        repo = Path("/bare-repo2")
        self.fs.create_file(repo / "HEAD")
        self.fs.create_dir(repo / "objects")
        self.fs.create_dir(repo / "refs")
        self.assertTrue(is_local_git_repo(repo))

    def test_is_local_git_repo_false_for_plain_dir(self):
        repo = Path("/not-a-repo")
        self.fs.create_file(repo / "somefile.txt")
        self.assertFalse(is_local_git_repo(repo))

    def test_classify_local_path_nonexistent(self):
        p = Path("/does/not/exist")
        out = classify_local_path(p)
        self.assertEqual(out.kind, PathKind.LOCAL_COPY)
        self.assertFalse(out.exists)
        self.assertFalse(out.is_git_repo)
        self.assertEqual(out.normalized, str(p))

    def test_classify_local_path_zip_file_case_insensitive(self):
        p = Path("/tmp/archive.ZIP")
        self.fs.create_file(p)
        out = classify_local_path(p)
        self.assertEqual(out.kind, PathKind.LOCAL_ZIP)
        self.assertTrue(out.exists)
        self.assertFalse(out.is_git_repo)
        self.assertEqual(out.normalized, str(p))

    def test_classify_local_path_regular_file_non_zip(self):
        p = Path("/tmp/readme.txt")
        self.fs.create_file(p)
        out = classify_local_path(p)
        self.assertEqual(out.kind, PathKind.LOCAL_COPY)
        self.assertTrue(out.exists)
        self.assertFalse(out.is_git_repo)
        self.assertEqual(out.normalized, str(p))

    @patch("addonmanager_path_utilities.is_local_git_repo", return_value=True)
    def test_classify_local_path_dir_git_repo(self, m_is_local_git_repo):
        p = Path("/projects/repo")
        self.fs.create_dir(p)
        out = classify_local_path(p)
        self.assertEqual(out.kind, PathKind.GIT_CLONE)
        self.assertTrue(out.exists)
        self.assertTrue(out.is_git_repo)
        self.assertEqual(out.normalized, str(p))
        m_is_local_git_repo.assert_called_once_with(p)

    @patch("addonmanager_path_utilities.is_local_git_repo", return_value=False)
    def test_classify_local_path_dir_not_git_repo(self, m_is_local_git_repo):
        p = Path("/projects/dir")
        self.fs.create_dir(p)
        out = classify_local_path(p)
        self.assertEqual(out.kind, PathKind.LOCAL_COPY)
        self.assertTrue(out.exists)
        self.assertFalse(out.is_git_repo)
        self.assertEqual(out.normalized, str(p))
        m_is_local_git_repo.assert_called_once_with(p)


if __name__ == "__main__":
    main()
