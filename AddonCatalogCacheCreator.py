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
import xml.etree.ElementTree
from dataclasses import dataclass, asdict
from typing import List, Optional

import base64
import io
import json
import os
import requests
import shutil
import subprocess
import zipfile

import AddonCatalog
import addonmanager_metadata


ADDON_CATALOG_URL = (
    "https://raw.githubusercontent.com/FreeCAD/FreeCAD-addons/master/AddonCatalog.json"
)
BASE_DIRECTORY = "./"
MAX_COUNT = 10  # Do at most this many repos (for testing purposes)

# Repos that are too large, or that should for some reason not be cloned here
EXCLUDED_REPOS = ["parts_library"]


@dataclass
class CacheEntry:
    """All contents of a CacheEntry are the text contents of the file listed. The icon data is
    base64-encoded (although it was probably an SVG, other formats are supported)."""

    package_xml: str = ""
    requirements_txt: str = ""
    metadata_txt: str = ""
    icon_data: str = ""


class AddonCatalogCacheCreator:

    def __init__(self, addon_catalog_url=ADDON_CATALOG_URL):
        self.addon_catalog_url = addon_catalog_url
        self.catalog = self.fetch_catalog()

    def fetch_catalog(self) -> AddonCatalog.AddonCatalog:
        response = requests.get(self.addon_catalog_url)
        if response.status_code != 200:
            raise RuntimeError(
                f"ERROR: Failed to fetch addon catalog from {self.addon_catalog_url}"
            )
        return AddonCatalog.AddonCatalog(response.json())


class CacheWriter:

    def __init__(self):
        self.catalog: AddonCatalog = None
        if os.path.isabs(BASE_DIRECTORY):
            self.cwd = BASE_DIRECTORY
        else:
            self.cwd = os.path.normpath(os.path.join(os.path.curdir, BASE_DIRECTORY))
        self._cache = {}

    def write(self):
        self.create_local_copy_of_addons()
        with open("addon_catalog_cache.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(self._cache, indent="  "))

    def create_local_copy_of_addons(self):
        self.catalog = AddonCatalogCacheCreator().catalog
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
            entry = self.generate_cache_entry(addon_id, index, catalog_entry)
            if addon_id not in self._cache:
                self._cache[addon_id] = []
            if entry is not None:
                self._cache[addon_id].append(asdict(entry))
            else:
                self._cache[addon_id].append({})

    def generate_cache_entry(
        self, addon_id: str, index: int, catalog_entry: AddonCatalog.AddonCatalogEntry
    ) -> Optional[CacheEntry]:
        """Create the cache entry for this catalog entry, if there is data to cache. If there is
        nothing to cache, returns None."""
        path_to_package_xml = self.find_file("package.xml", addon_id, index, catalog_entry)
        cache_entry = None
        if path_to_package_xml and os.path.exists(path_to_package_xml):
            cache_entry = self.generate_cache_entry_from_package_xml(path_to_package_xml)

        path_to_requirements = self.find_file("requirements.txt", addon_id, index, catalog_entry)
        if path_to_requirements and os.path.exists(path_to_requirements):
            if cache_entry is None:
                cache_entry = CacheEntry()
            with open(path_to_requirements, "r", encoding="utf-8") as f:
                cache_entry.requirements_txt = f.read()

        path_to_metadata = self.find_file("metadata.txt", addon_id, index, catalog_entry)
        if path_to_metadata and os.path.exists(path_to_metadata):
            if cache_entry is None:
                cache_entry = CacheEntry()
            with open(path_to_metadata, "r", encoding="utf-8") as f:
                cache_entry.metadata_txt = f.read()

        return cache_entry

    def generate_cache_entry_from_package_xml(
        self, path_to_package_xml: str
    ) -> Optional[CacheEntry]:
        cache_entry = CacheEntry()
        with open(path_to_package_xml, "r", encoding="utf-8") as f:
            cache_entry.package_xml = f.read()
        try:
            metadata = addonmanager_metadata.MetadataReader.from_bytes(
                cache_entry.package_xml.encode("utf-8")
            )
        except xml.etree.ElementTree.ParseError:
            print(f"ERROR: Failed to parse XML from {path_to_package_xml}")
            return None
        except RuntimeError:
            print(f"ERROR: Failed to read metadata from {path_to_package_xml}")
            return None

        relative_icon_path = self.get_icon_from_metadata(metadata)
        absolute_icon_path = os.path.join(os.path.dirname(path_to_package_xml), relative_icon_path)
        if os.path.exists(absolute_icon_path):
            with open(absolute_icon_path, "rb") as f:
                cache_entry.icon_data = base64.b64encode(f.read()).decode("utf-8")
        return cache_entry

    def create_local_copy_of_single_addon_with_git(
        self, addon_id: str, index: int, catalog_entry: AddonCatalog.AddonCatalogEntry
    ):
        expected_name = self.get_directory_name(addon_id, index, catalog_entry)
        self.clone_or_update(expected_name, catalog_entry.repository, catalog_entry.git_ref)

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
        response = requests.get(catalog_entry.zip_url)
        if response.status_code != 200:
            print(f"ERROR: Failed to fetch zip data for {addon_id} from {catalog_entry.zip_url}.")
            return
        extract_to_dir = self.get_directory_name(addon_id, index, catalog_entry)
        if os.path.exists(extract_to_dir):
            shutil.rmtree(extract_to_dir)
        os.makedirs(extract_to_dir, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
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
            command = ["git", "merge", "--quiet"]
            completed_process = subprocess.run(command)
            if completed_process.returncode != 0:
                os.chdir(old_dir)
                raise RuntimeError(f"git merge failed for {name} branch {branch}")
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
            return metadata.icon
        for content_type in metadata.content:
            for content_item in metadata.content[content_type]:
                icon = CacheWriter.get_icon_from_metadata(content_item)
                if icon:
                    return icon
        return None


if __name__ == "__main__":
    writer = CacheWriter()
    writer.write()
