# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2025 The FreeCAD project association AISBL              *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

"""Classes and utility functions to generate a remotely hosted cache of all addon catalog entries.
Intended to be run by a server-side systemd timer to generate a file that is then loaded by the
Addon Manager in each FreeCAD installation."""
import datetime
from dataclasses import is_dataclass, fields
from typing import Any, List, Optional

import base64
import enum
import hashlib
import io
import json
import os
import requests
import subprocess
import defusedxml.ElementTree as ElementTree
import zipfile

import AddonCatalog
import addonmanager_metadata
import addonmanager_utilities as utils


ADDON_CATALOG_URL = (
    "https://raw.githubusercontent.com/FreeCAD/FreeCAD-addons/master/AddonCatalog.json"
)
BASE_DIRECTORY = "./CatalogCache"
MAX_COUNT = 10000  # Do at most this many repos (for testing purposes this can be made smaller)

# Repos that are too large, or that should for some reason not be cloned here
EXCLUDED_REPOS = ["parts_library"]


def recursive_serialize(obj: Any):
    """Recursively serialize an object, supporting non-dataclasses that themselves contain
    dataclasses (in this case, AddonCatalog, which contains AddonCatalogEntry)"""
    if is_dataclass(obj):
        result = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            result[f.name] = recursive_serialize(value)
        return result
    elif isinstance(obj, list):
        return [recursive_serialize(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: recursive_serialize(v) for k, v in obj.items()}
    elif hasattr(obj, "__dict__"):
        return {k: recursive_serialize(v) for k, v in vars(obj).items() if not k.startswith("__")}
    else:
        return obj


class GitRefType(enum.IntEnum):
    """Enum for the type of git ref (tag, branch, or hash)."""

    TAG = 1
    BRANCH = 2
    HASH = 3


class CatalogFetcher:
    """Fetches the addon catalog from the given URL and returns an AddonCatalog object. Separated
    from the main class for easy mocking during tests. Note that every instantiation of this class
    will run a new fetch of the catalog."""

    def __init__(self, addon_catalog_url: str = ADDON_CATALOG_URL):
        self.addon_catalog_url = addon_catalog_url
        self.catalog = self.fetch_catalog()

    def fetch_catalog(self) -> AddonCatalog.AddonCatalog:
        """Fetch the addon catalog from the given URL and return an AddonCatalog object."""
        response = requests.get(self.addon_catalog_url, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(
                f"ERROR: Failed to fetch addon catalog from {self.addon_catalog_url}"
            )
        return AddonCatalog.AddonCatalog(response.json())


class CacheWriter:
    """Writes a JSON file containing a cache of all addon catalog entries. The cache is a copy of
    the package.xml, requirements.txt, and metadata.txt files from the addon repositories, as well
    as a base64-encoded icon image. The cache is written to the current working directory."""

    def __init__(self):
        self.catalog: AddonCatalog = None
        self.icon_errors = {}
        if os.path.isabs(BASE_DIRECTORY):
            self.cwd = BASE_DIRECTORY
        else:
            self.cwd = os.path.normpath(os.path.join(os.getcwd(), BASE_DIRECTORY))
        self._cache = {}

    def write(self):
        original_working_directory = os.getcwd()
        os.makedirs(self.cwd, exist_ok=True)
        os.chdir(self.cwd)
        self.create_local_copy_of_addons()

        with zipfile.ZipFile(
            os.path.join(self.cwd, "addon_catalog_cache.zip"), "w", zipfile.ZIP_DEFLATED
        ) as zipf:
            zipf.writestr(
                "addon_catalog_cache.json",
                json.dumps(recursive_serialize(self.catalog.get_catalog()), indent="  "),
            )

        # Also generate the sha256 hash of the zip file and store it
        with open("addon_catalog_cache.zip", "rb") as cache_file:
            cache_file_content = cache_file.read()
        sha256 = hashlib.sha256(cache_file_content).hexdigest()
        with open("addon_catalog_cache.zip.sha256", "w", encoding="utf-8") as hash_file:
            hash_file.write(sha256)

        with open(os.path.join(self.cwd, "icon_errors.json"), "w") as f:
            json.dump(self.icon_errors, f, indent="  ")

        os.chdir(original_working_directory)
        print(f"Wrote cache to {os.path.join(self.cwd, 'addon_catalog_cache.zip')}")

    def create_local_copy_of_addons(self):
        self.catalog = CatalogFetcher().catalog
        counter = 0
        for addon_id, catalog_entries in self.catalog.get_catalog().items():
            if addon_id in EXCLUDED_REPOS:
                continue
            self.create_local_copy_of_single_addon(addon_id, catalog_entries)
            counter += 1
            if counter >= MAX_COUNT:
                break

    def create_local_copy_of_single_addon(
        self, addon_id: str, catalog_entries: List[AddonCatalog.AddonCatalogEntry]
    ):
        for index, catalog_entry in enumerate(catalog_entries):
            if catalog_entry.repository is not None:
                self.create_local_copy_of_single_addon_with_git(addon_id, index, catalog_entry)
            elif catalog_entry.zip_url is not None:
                self.create_local_copy_of_single_addon_with_zip(addon_id, index, catalog_entry)
            else:
                print(
                    f"ERROR: Invalid catalog entry for {addon_id}. "
                    "Neither git info nor zip info was specified."
                )
                continue
            metadata = self.generate_cache_entry(addon_id, index, catalog_entry)
            self.catalog.add_metadata_to_entry(addon_id, index, metadata)
            self.create_zip_of_entry(addon_id, index, catalog_entry)

    def generate_cache_entry(
        self, addon_id: str, index: int, catalog_entry: AddonCatalog.AddonCatalogEntry
    ) -> Optional[AddonCatalog.CatalogEntryMetadata]:
        """Create the cache entry for this catalog entry if there is data to cache. If there is
        nothing to cache, returns None."""
        path_to_package_xml = self.find_file("package.xml", addon_id, index, catalog_entry)
        cache_entry = None
        if path_to_package_xml and os.path.exists(path_to_package_xml):
            cache_entry = self.generate_cache_entry_from_package_xml(path_to_package_xml)

        path_to_requirements = self.find_file("requirements.txt", addon_id, index, catalog_entry)
        if path_to_requirements and os.path.exists(path_to_requirements):
            if cache_entry is None:
                cache_entry = AddonCatalog.CatalogEntryMetadata()
            with open(path_to_requirements, "r", encoding="utf-8") as f:
                cache_entry.requirements_txt = f.read()

        path_to_metadata = self.find_file("metadata.txt", addon_id, index, catalog_entry)
        if path_to_metadata and os.path.exists(path_to_metadata):
            if cache_entry is None:
                cache_entry = AddonCatalog.CatalogEntryMetadata()
            with open(path_to_metadata, "r", encoding="utf-8") as f:
                cache_entry.metadata_txt = f.read()

        dirname = CacheWriter.get_directory_name(addon_id, index, catalog_entry)
        if os.path.exists(os.path.join(self.cwd, dirname, ".git")):
            old_dir = os.getcwd()
            os.chdir(os.path.join(self.cwd, dirname))
            last_updated_time = CacheWriter.determine_last_commit_time()
            if last_updated_time:
                catalog_entry.last_update_time = last_updated_time.isoformat()
            os.chdir(old_dir)

        zip_name = os.path.join(self.cwd, dirname + ".zip")
        if os.path.exists(zip_name):
            # Don't use os.path.join, by convention this path is always UNIX style, and local
            # users are required to translate it into their OS's format as needed
            catalog_entry.relative_cache_path = BASE_DIRECTORY + "/" + dirname + ".zip"

        return cache_entry

    def generate_cache_entry_from_package_xml(
        self, path_to_package_xml: str
    ) -> Optional[AddonCatalog.CatalogEntryMetadata]:
        cache_entry = AddonCatalog.CatalogEntryMetadata()
        with open(path_to_package_xml, "r", encoding="utf-8") as f:
            cache_entry.package_xml = f.read()
        try:
            metadata = addonmanager_metadata.MetadataReader.from_bytes(
                cache_entry.package_xml.encode("utf-8")
            )
        except ElementTree.ParseError:
            print(f"ERROR: Failed to parse XML from {path_to_package_xml}")
            return None
        except RuntimeError:
            print(f"ERROR: Failed to read metadata from {path_to_package_xml}")
            return None

        relative_icon_path = self.get_icon_from_metadata(metadata)
        if relative_icon_path is not None:
            absolute_icon_path = os.path.join(
                os.path.dirname(path_to_package_xml), relative_icon_path
            )
            if os.path.exists(absolute_icon_path):
                with open(absolute_icon_path, "rb") as f:
                    cache_entry.icon_data = base64.b64encode(f.read()).decode("utf-8")
            else:
                self.icon_errors[metadata.name] = relative_icon_path
                print(f"ERROR: Could not find icon file {absolute_icon_path}")
        return cache_entry

    def create_local_copy_of_single_addon_with_git(
        self, addon_id: str, index: int, catalog_entry: AddonCatalog.AddonCatalogEntry
    ):
        expected_name = self.get_directory_name(addon_id, index, catalog_entry)
        try:
            self.clone_or_update(expected_name, catalog_entry.repository, catalog_entry.git_ref)
        except RuntimeError as e:
            print(f"ERROR: Failed to clone or update {addon_id} from {catalog_entry.repository}.")
            print(f"ERROR: {e}")

    @staticmethod
    def get_directory_name(addon_id, index, catalog_entry):
        expected_name = os.path.join(addon_id, str(index) + "-")
        if catalog_entry.branch_display_name:
            expected_name += catalog_entry.branch_display_name.replace("/", "-")
        elif catalog_entry.git_ref:
            expected_name += catalog_entry.git_ref.replace("/", "-")
        else:
            expected_name += "unknown-branch-name"
        return expected_name

    def create_local_copy_of_single_addon_with_zip(
        self, addon_id: str, index: int, catalog_entry: AddonCatalog.AddonCatalogEntry
    ):
        response = requests.get(catalog_entry.zip_url, timeout=10)
        if response.status_code != 200:
            print(f"ERROR: Failed to fetch zip data for {addon_id} from {catalog_entry.zip_url}.")
            return
        extract_to_dir = self.get_directory_name(addon_id, index, catalog_entry)
        if os.path.exists(extract_to_dir):
            utils.rmdir(extract_to_dir)
        os.makedirs(extract_to_dir, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
            latest = max(
                (info.date_time for info in zip_file.infolist() if not info.is_dir()), default=None
            )
            if latest is not None:
                catalog_entry.last_update_time = datetime.datetime(*latest).isoformat()
            zip_file.extractall(path=extract_to_dir)

    @staticmethod
    def clone_or_update(name: str, url: str, branch: str) -> None:
        """If a directory called "name" exists, and it contains a subdirectory called .git,
        then 'git fetch' is called; otherwise we use 'git clone' to make a bare, shallow
        copy of the repo (in the normal case where minimal is True), or a normal clone,
        if minimal is set to False."""

        if not os.path.exists(os.path.join(os.getcwd(), name, ".git")):
            print(f"Cloning {url} to {name}", flush=True)
            # Shallow, but do include the last commit on each branch and tag
            command = [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                branch,
                url,
                name,
            ]
            completed_process = subprocess.run(command)
            if completed_process.returncode != 0:
                raise RuntimeError(f"Clone failed for {url}")
        else:
            print(f"Updating {name}", flush=True)
            old_dir = os.getcwd()
            os.chdir(os.path.join(old_dir, name))
            try:
                # Determine if we are dealing with a tag, branch, or hash
                git_ref_type = CacheWriter.determine_git_ref_type(name, url, branch)
                command = ["git", "fetch"]
                completed_process = subprocess.run(command)
                if completed_process.returncode != 0:
                    os.chdir(old_dir)
                    raise RuntimeError(f"git fetch failed for {name}")
                command = ["git", "checkout", branch, "--quiet"]
                completed_process = subprocess.run(command)
                if completed_process.returncode != 0:
                    os.chdir(old_dir)
                    raise RuntimeError(f"git checkout failed for {name} branch {branch}")
                if git_ref_type == GitRefType.BRANCH:
                    command = ["git", "merge", "--quiet"]
                    completed_process = subprocess.run(command)
                    if completed_process.returncode != 0:
                        os.chdir(old_dir)
                        raise RuntimeError(f"git merge failed for {name} branch {branch}")
            except RuntimeError as e:
                # In the event of basically ANY error, delete the original and re-clone.
                print(e)
                print("Deleting and re-cloning the original repo")
                os.chdir(old_dir)
                utils.rmdir(os.path.join(old_dir, name))
                CacheWriter.clone_or_update(name, url, branch)
            os.chdir(old_dir)

    def find_file(
        self,
        filename: str,
        addon_id: str,
        index: int,
        catalog_entry: AddonCatalog.AddonCatalogEntry,
    ) -> Optional[str]:
        """Find a given file in the downloaded cache for this addon. Returns None if the file does
        not exist."""
        start_dir = os.path.join(self.cwd, self.get_directory_name(addon_id, index, catalog_entry))
        for dirpath, _, filenames in os.walk(start_dir):
            if filename in filenames:
                return os.path.join(dirpath, filename)
        return None

    @staticmethod
    def get_icon_from_metadata(metadata: addonmanager_metadata.Metadata) -> Optional[str]:
        """Try to locate the icon file specified for this Addon. Recursively search through the
        levels of the metadata and return the first specified icon file path. Returns None of there
        is no icon specified for this Addon (which is not allowed by the standard, but we don't want
        to crash the cache writer)."""
        if metadata.icon:
            if (
                metadata.subdirectory
                and metadata.subdirectory != "."
                and metadata.subdirectory != "./"
            ):
                return metadata.subdirectory + "/" + metadata.icon
            return metadata.icon
        for content_type in metadata.content:
            for content_item in metadata.content[content_type]:
                icon = CacheWriter.get_icon_from_metadata(content_item)
                if icon:
                    return icon
        return None

    @staticmethod
    def determine_git_ref_type(name: str, url: str, branch: str) -> GitRefType:
        """Determine if the given branch, tag, or hash is a tag, branch, or hash. Returns the type
        if determinable, otherwise raises a RuntimeError."""
        command = ["git", "show-ref", "--verify", f"refs/remotes/origin/{branch}"]
        completed_process = subprocess.run(command)
        if completed_process.returncode == 0:
            return GitRefType.BRANCH
        command = ["git", "show-ref", "--tags"]
        completed_process = subprocess.run(command, capture_output=True)
        completed_process_output = completed_process.stdout.decode("utf-8")
        if branch in completed_process_output:
            return GitRefType.TAG
        command = ["git", "rev-parse", branch]
        completed_process = subprocess.run(command)
        if completed_process.returncode == 0:
            return GitRefType.HASH
        raise RuntimeError(
            f"Could not determine if {branch} of {name} is a tag, branch, or hash. "
            f"Output was: {completed_process_output}"
        )

    @staticmethod
    def determine_last_commit_time() -> datetime.datetime:
        """Executed on the current working directory. Returns the time of the last commit."""
        command = ["git", "log", "-1", "--format=%cd", "--date=iso-strict"]
        completed_process = subprocess.run(command, capture_output=True)
        completed_process_output = completed_process.stdout.decode("utf-8").strip()
        try:
            dt = datetime.datetime.fromisoformat(completed_process_output)
        except ValueError:
            print(f"ERROR: Failed to parse last commit time from {completed_process_output}")
            dt = None
        return dt

    def create_zip_of_entry(
        self, addon_id: str, index: int, catalog_entry: AddonCatalog.AddonCatalogEntry
    ):
        """Create a zip file containing the contents of the addon directory for this entry. The
        zip file is written to a file with the same name as the calculated addon cache directory
        in the current working directory."""

        dirname = CacheWriter.get_directory_name(addon_id, index, catalog_entry)
        start_dir = os.path.join(self.cwd, dirname)
        zip_file_path = os.path.join(self.cwd, f"{dirname}.zip")
        temp_file_path = zip_file_path + ".new"

        if not os.path.isdir(start_dir):
            print(
                f"ERROR: Directory {start_dir} does not exist. Skipping zip creation for addon {addon_id}."
            )
            return

        with zipfile.ZipFile(temp_file_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(start_dir):
                if ".git" in dirs:
                    dirs.remove(".git")  # Don't visit .git directories
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, start_dir)
                    try:
                        zf.write(full_path, rel_path)
                    except (OSError, FileNotFoundError, RuntimeError) as e:
                        print(f"WARNING: Could not add {full_path} to zip archive: {e}")
        try:
            good = False
            with zipfile.ZipFile(temp_file_path, "r") as zf:
                good = zf.testzip() is None
            if good:
                if os.path.exists(zip_file_path):
                    os.remove(zip_file_path)
                os.rename(temp_file_path, zip_file_path)
            else:
                os.remove(temp_file_path)
                print(
                    f"Failed to create zip file {zip_file_path} for addon {addon_id}: data is corrupt"
                )
        except zipfile.BadZipFile:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            print(
                f"Failed to create zip file {zip_file_path} for addon {addon_id}: data is not a valid zip file"
            )


if __name__ == "__main__":
    writer = CacheWriter()
    writer.write()
