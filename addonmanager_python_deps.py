# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022-2025 FreeCAD Project Association AISBL             *
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

"""Provides classes and support functions for managing the automatically-installed
Python library dependencies. No support is provided for uninstalling those dependencies
because pip's uninstall function does not support the target directory argument."""

import dataclasses
import os
import re
import subprocess
from typing import Dict, Iterable, List, TypedDict, Optional
from addonmanager_metadata import Version
from addonmanager_utilities import (
    create_pip_call,
    run_interruptable_subprocess,
    get_pip_target_directory,
    translate,
)

import addonmanager_freecad_interface as fci

from PySideWrapper import QtCore

translate = fci.translate


class PipFailed(Exception):
    """Exception thrown when pip times out or otherwise fails to return valid results"""


def pip_has_dry_run_support() -> bool:
    """Returns True if pip supports the --dry-run option, False otherwise."""
    try:
        pip_version_string = call_pip(["--version"])[0]
        version_str = pip_version_string.split()[1]
        return Version(version_str) >= Version("23.1")
    except PipFailed:
        return False


def call_pip(args: List[str]) -> List[str]:
    """Tries to locate the appropriate Python executable and run pip with version checking
    disabled. Fails if Python can't be found or if pip is not installed."""

    try:
        call_args = create_pip_call(args)
    except RuntimeError as exception:
        raise PipFailed() from exception

    try:
        proc = run_interruptable_subprocess(call_args)
    except subprocess.CalledProcessError as exception:
        raise PipFailed("pip timed out") from exception

    if proc.returncode != 0:
        raise PipFailed(proc.stderr)

    data = proc.stdout
    return data.split("\n")


@dataclasses.dataclass
class PackageInfo:
    name: str
    installed_version: str
    available_version: str
    dependencies: List[str]


def parse_pip_list_output(all_packages, outdated_packages) -> List[PackageInfo]:
    """Parses the output from pip into a dictionary with update information in it. The pip
    output should be an array of lines of text."""

    # All Packages output looks like this:
    # Package    Version
    # ---------- -------
    # gitdb      4.0.9
    # setuptools 41.2.0

    # Outdated Packages output looks like this:
    # Package    Version Latest Type
    # ---------- ------- ------ -----
    # pip        21.0.1  22.1.2 wheel
    # setuptools 41.2.0  63.2.0 wheel

    packages: Dict[str, PackageInfo] = {}
    skip_counter = 0
    for line in all_packages:
        if skip_counter < 2:
            skip_counter += 1
            continue
        entries = line.split()
        if len(entries) > 1:
            package_name = entries[0]
            installed_version = entries[1]
            packages[package_name] = PackageInfo(package_name, installed_version, "", [])

    skip_counter = 0
    for line in outdated_packages:
        if skip_counter < 2:
            skip_counter += 1
            continue
        entries = line.split()
        if len(entries) > 1:
            package_name = entries[0]
            available_version = entries[2]
            if package_name not in packages:
                raise RuntimeError(
                    "all_packages does not contain all packages in outdated_packages"
                )
            packages[package_name].available_version = available_version

    return list(packages.values())


class PythonPackageListModel(QtCore.QAbstractListModel):
    """The non-GUI portion of the Python package manager. This class is responsible for
    communicating with pip and generating a list of packages to be installed, acting as a model
    for the Qt view."""

    def __init__(self, addons):
        super().__init__()
        self.addons = addons
        self.is_venv = False
        self.vendor_path = get_pip_target_directory()  # Ignored if running in a venv
        self.package_list = []

    def reset_package_list(self):
        """
        Use pip to figure out the current package list, resetting this model. Will raise `PipFailed`
        if the pip calls fail, which represents a catastrophic/unrecoverable failure from this
        class's point of view.
        """

        self.beginResetModel()
        self.package_list.clear()
        outdated_packages_stdout = call_pip(["list", "-o", "--path", self.vendor_path])
        all_packages_stdout = call_pip(["list", "--path", self.vendor_path])
        self.package_list = parse_pip_list_output(all_packages_stdout, outdated_packages_stdout)
        self.endResetModel()

    def rowCount(self, parent=None) -> int:
        if parent is not None:
            return 0
        return len(self.package_list)

    def data(self, index, role=...) -> Optional[str]:
        row = index.row()
        col = index.column()
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return self.package_list[row].name
            elif col == 1:
                return self.package_list[row].installed_version
            elif col == 2:
                return self.package_list[row].available_version
            elif col == 3:
                if not self.package_list[row].dependencies:
                    dependent_addons = self.get_dependent_addons(self.package_list[row].name)
                    for addon in dependent_addons:
                        if addon["optional"]:
                            self.package_list[row].dependencies.append(addon["name"] + "*")
                        else:
                            self.package_list[row].dependencies.append(addon["name"])
                return ", ".join(self.package_list[row].dependencies)
        return None

    def headerData(self, section, orientation, role=...) -> Optional[str]:
        if (
            orientation == QtCore.Qt.Orientation.Horizontal
            and role == QtCore.Qt.ItemDataRole.DisplayRole
        ):
            if section == 0:
                return translate("AddonsInstaller", "Package")
            elif section == 1:
                return translate("AddonsInstaller", "Installed Version")
            elif section == 2:
                return translate("AddonsInstaller", "Available Version")
            elif section == 3:
                return translate("AddonsInstaller", "Dependencies")
        return None

    def flags(self, index) -> QtCore.Qt.ItemFlag:
        return QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable

    class DependentAddon(TypedDict):
        name: str
        optional: bool

    def get_dependent_addons(self, package) -> List[DependentAddon]:
        dependent_addons = []
        for addon in self.addons:
            # if addon.installed_version is not None:
            if package.lower() in addon.python_requires:
                dependent_addons.append({"name": addon.name, "optional": False})
            elif package.lower() in addon.python_optional:
                dependent_addons.append({"name": addon.name, "optional": True})
        return dependent_addons

    def update_package(self, index: QtCore.QModelIndex) -> None:
        """Run pip --upgrade on the given package. Updates all dependent packages as well."""

        row = index.row()
        package_name = self.package_list[row].name
        self.beginResetModel()  # Reset the whole model because we don't know what will get updated
        try:
            fci.Console.PrintLog(
                f"Running 'pip install --upgrade --target {self.vendor_path} {package_name}'\n"
            )
            call_pip(["install", "--upgrade", package_name, "--target", self.vendor_path])
        except PipFailed as e:
            fci.Console.PrintError(str(e) + "\n")
        self.endResetModel()

    def update_multiple_packages(self, package_list: Optional[List[QtCore.QModelIndex]] = None):
        """Updates/installs multiple packages: if package_list is None, all packages are updated."""

        if package_list is None:
            package_list = self.package_list
        self.beginResetModel()
        updates = []
        for index in package_list:
            row = index.row()
            package_name = self.package_list[row].name
            updates.append(package_name)
        update_string = " ".join(updates)
        try:
            fci.Console.PrintLog(
                f"Running 'pip install --upgrade --target {self.vendor_path} {update_string}'\n"
            )
            command = ["install", "--upgrade", "--target", self.vendor_path]
            command.extend(updates)
            call_pip(command)
        except PipFailed as e:
            fci.Console.PrintError(str(e) + "\n")
        self.endResetModel()

    def determine_new_python_dependencies(self, addons) -> set[str]:
        """Given a list of Addon objects, finds the Python dependencies for those addons. Also
        accepts a single Addon object, in which case only its dependencies are evaluated. If using
        a recent version of pip, the dry-run option is used to determine which packages would be
        installed, otherwise just lists the dependencies as they are listed in the
        addon metadata, filtered to only show new ones."""

        if not isinstance(addons, Iterable):
            addons = [addons]

        python_dependencies = set()
        for addon in addons:
            python_dependencies.update(addon.python_requires)
            python_dependencies.update(addon.python_optional)

        result = set()
        # If we have at least pip 23.1, we can use dry-run:
        if pip_has_dry_run_support():
            command = ["install", "--upgrade", "--dry-run"]
            command.extend(python_dependencies)
            output = call_pip(command)

            for line in output:
                match = re.match(r"Would install ([\w\-.]+)-([\d.]+)", line)
                if match:
                    name, _ = match.groups()
                    result.add(name)
        else:
            result.update(python_dependencies)
            result.difference_update(set([p.name for p in self.package_list]))
        return result

    def all_dependencies_installed(self, addon) -> bool:
        """Returns True if all dependencies for the given addon are installed, or False if not."""
        dependencies = self.determine_new_python_dependencies(addon)
        return len(dependencies) == 0
