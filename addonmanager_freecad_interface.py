# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2023 FreeCAD Project Association                        *
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

"""Classes to encapsulate the Addon Manager's interaction with FreeCAD, and to provide
replacements when the Addon Manager is not run from within FreeCAD (e.g. during unit
testing).

Usage:
from addonmanager_freecad_interface import Console, DataPaths, Preferences
"""

import json
import logging
import os
import platform
import shutil
import tempfile

# pylint: disable=too-few-public-methods

try:
    import FreeCAD

    try:
        from freecad.utils import get_python_exe

        try:
            _ = get_python_exe()
        except AttributeError as e:
            raise RuntimeError("Could not get the FreeCAD python executable") from e
    except ImportError:
        # This was only added in FreeCAD 1.0 -- to support FreeCAD 0.21 a backup strategy must be
        # used. This code is borrowed from FreeCAD 1.1dev.
        def get_python_exe():
            prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/PythonConsole")
            python_exe = prefs.GetString("ExternalPythonExecutable", "Not set")
            fc_dir = FreeCAD.getHomePath()
            if not python_exe or python_exe == "Not set" or not os.path.exists(python_exe):
                python_exe = os.path.join(fc_dir, "bin", "python3")
                if "Windows" in platform.system():
                    python_exe += ".exe"

            if not python_exe or not os.path.exists(python_exe):
                python_exe = os.path.join(fc_dir, "bin", "python")
                if "Windows" in platform.system():
                    python_exe += ".exe"

            if not python_exe or not os.path.exists(python_exe):
                python_exe = shutil.which("python3")

            if not python_exe or not os.path.exists(python_exe):
                python_exe = shutil.which("python")

            if not python_exe or not os.path.exists(python_exe):
                return ""

            python_exe = python_exe.replace("/", os.path.sep)
            prefs.SetString("ExternalPythonExecutable", python_exe)
            return python_exe

    if not hasattr(FreeCAD, "Console"):
        raise ImportError("Unrecognized FreeCAD version")

    Console = FreeCAD.Console
    ParamGet = FreeCAD.ParamGet
    Version = FreeCAD.Version
    getUserAppDataDir = FreeCAD.getUserAppDataDir
    getUserMacroDir = FreeCAD.getUserMacroDir
    getUserCachePath = FreeCAD.getUserCachePath
    translate = FreeCAD.Qt.translate
    loadUi = None
    GuiUp = FreeCAD.GuiUp

    if GuiUp:
        import FreeCADGui

        if hasattr(FreeCADGui, "PySideUic"):
            loadUi = FreeCADGui.PySideUic.loadUi

        addPreferencePage = FreeCADGui.addPreferencePage
    else:
        FreeCADGui = None

except ImportError:
    FreeCAD = None
    FreeCADGui = None
    getUserAppDataDir = None
    getUserCachePath = None
    getUserMacroDir = None
    loadUi = None

    try:
        from PySide6 import QtCore, QtWidgets

        GuiUp = (
            True
            if hasattr(QtWidgets, "QApplication") and QtWidgets.QApplication.instance()
            else False
        )
        from PySide6.QtUiTools import QUiLoader
    except ImportError:
        try:
            from PySide2 import QtCore, QtWidgets

            GuiUp = True if QtWidgets.QApplication.instance() else False
            from PySide2.QtUiTools import QUiLoader
        except ImportError:
            GuiUp = False

    if GuiUp:

        def loadUi(path: str):
            loader = QUiLoader()
            file = QtCore.QFile(path)
            file.open(QtCore.QFile.ReadOnly)
            window = loader.load(file)
            file.close()
            return window

        def addPreferencePage(_options_class, _name: str):
            """Don't do anything with a preference page right now"""
            pass

    def translate(context: str, sourceText: str, disambiguation: str = "", n: int = -1) -> str:
        return QtCore.QCoreApplication.translate(context, sourceText, disambiguation, n)

    def Version():
        return 1, 1, 0, "dev"

    def get_python_exe():
        return shutil.which("python3")

    class ConsoleReplacement:
        """If FreeCAD's Console is not available, create a replacement by redirecting FreeCAD
        log calls to Python's built-in logging facility."""

        @staticmethod
        def PrintLog(arg: str) -> None:
            logging.log(logging.DEBUG, arg)

        @staticmethod
        def PrintMessage(arg: str) -> None:
            logging.info(arg)

        @staticmethod
        def PrintWarning(arg: str) -> None:
            logging.warning(arg)

        @staticmethod
        def PrintError(arg: str) -> None:
            logging.error(arg)

    Console = ConsoleReplacement()

    class ParametersReplacement:
        """Proxy for FreeCAD's Parameters when not running within FreeCAD. NOT
        serialized, only exists for the duration of the program's execution. Only
        provides the functions used by the Addon Manager, this class is not intended
        to be a complete replacement for FreeCAD's preferences system."""

        parameters = {}

        def GetBool(self, name: str, default: bool) -> bool:
            return self._Get(name, default)

        def GetInt(self, name: str, default: int) -> int:
            return self._Get(name, default)

        def GetFloat(self, name: str, default: float) -> float:
            return self._Get(name, default)

        def GetString(self, name: str, default: str) -> str:
            return self._Get(name, default)

        def _Get(self, name, default):
            return self.parameters[name] if name in self.parameters else default

        def SetBool(self, name: str, value: bool) -> None:
            self.parameters[name] = value

        def SetInt(self, name: str, value: int) -> None:
            self.parameters[name] = value

        def SetFloat(self, name: str, value: float) -> None:
            self.parameters[name] = value

        def SetString(self, name: str, value: str) -> None:
            self.parameters[name] = value

        def RemBool(self, name: str) -> None:
            self.parameters.pop(name)

        def RemInt(self, name: str) -> None:
            self.parameters.pop(name)

        def RemFloat(self, name: str) -> None:
            self.parameters.pop(name)

        def RemString(self, name: str) -> None:
            self.parameters.pop(name)

    def ParamGet(_: str):
        return ParametersReplacement()


class DataPaths:
    """Provide access to various data storage paths. If not running within FreeCAD,
    all paths are temp directories. If not run within FreeCAD, all directories are
    deleted when the last reference to this class is deleted."""

    data_dir = None
    mod_dir = None
    macro_dir = None
    cache_dir = None
    home_dir = None

    reference_count = 0

    def __init__(self):
        if FreeCAD:
            if self.data_dir is None:
                self.data_dir = getUserAppDataDir()
            if self.mod_dir is None:
                self.mod_dir = os.path.join(getUserAppDataDir(), "Mod")
            if self.cache_dir is None:
                self.cache_dir = getUserCachePath()
            if self.macro_dir is None:
                self.macro_dir = getUserMacroDir(True)
            if self.home_dir is None:
                self.home_dir = FreeCAD.getHomePath()
        else:
            self.reference_count += 1
            if self.data_dir is None:
                self.data_dir = tempfile.mkdtemp()
            if self.mod_dir is None:
                self.mod_dir = tempfile.mkdtemp()
            if self.cache_dir is None:
                self.cache_dir = tempfile.mkdtemp()
            if self.macro_dir is None:
                self.macro_dir = tempfile.mkdtemp()
            if self.home_dir is None:
                self.home_dir = os.path.join(os.path.dirname(__file__))

    def __del__(self):
        self.reference_count -= 1
        if not FreeCAD and self.reference_count <= 0:
            self._delete_paths()

    def _delete_paths(self):
        if FreeCAD:
            return
        paths = [self.data_dir, self.mod_dir, self.cache_dir, self.macro_dir, self.mod_dir]
        for path in paths:
            if os.path.isdir(path):
                os.rmdir(path)
        self.data_dir = None
        self.mod_dir = None
        self.cache_dir = None
        self.macro_dir = None


class Preferences:
    """Wrap access to all user preferences. If run within FreeCAD, user preferences are
    persistent, otherwise they only exist per-run. All preferences are controlled by a
    central JSON file defining their defaults."""

    preferences_defaults = {}

    def __init__(self, defaults_data=None):
        """Set up the preferences, initializing the class statics if necessary. If
        defaults_data is provided it is used as the preferences defaults. If it is not
        provided, then the defaults are read in from the standard defaults file,
        addonmanager_preferences_defaults.json, located in the same directory as this
        Python file."""
        if not self.preferences_defaults:
            if defaults_data:
                self.preferences_defaults = defaults_data
            else:
                self._load_preferences_defaults()
        self.prefs = ParamGet("User parameter:BaseApp/Preferences/Addons")

    def get(self, name: str):
        """Get the preference value for the given key"""
        if name not in self.preferences_defaults:
            raise RuntimeError(
                f"Unrecognized preference {name} -- did you add "
                + "it to addonmanager_preferences_defaults.json?"
            )
        if isinstance(self.preferences_defaults[name], bool):
            return self.prefs.GetBool(name, self.preferences_defaults[name])
        if isinstance(self.preferences_defaults[name], int):
            return self.prefs.GetInt(name, self.preferences_defaults[name])
        if isinstance(self.preferences_defaults[name], float):
            return self.prefs.GetFloat(name, self.preferences_defaults[name])
        if isinstance(self.preferences_defaults[name], str):
            return self.prefs.GetString(name, self.preferences_defaults[name])
        # We don't directly support any other types from the JSON file (e.g. arrays)
        type_name = type(self.preferences_defaults[name])
        raise RuntimeError(f"Unrecognized type for {name}: {type_name}")

    def set(self, name: str, value):
        """Set the preference value for the given key. Must exist (e.g. must be in the
        addonmanager_preferences_defaults.json file)."""
        if name not in self.preferences_defaults:
            raise RuntimeError(
                f"Unrecognized preference {name} -- did you add "
                + "it to addonmanager_preferences_defaults.json?"
            )
        if isinstance(self.preferences_defaults[name], bool):
            self.prefs.SetBool(name, value)
        elif isinstance(self.preferences_defaults[name], int):
            self.prefs.SetInt(name, value)
        elif isinstance(self.preferences_defaults[name], float):
            self.prefs.SetFloat(name, value)
        elif isinstance(self.preferences_defaults[name], str):
            self.prefs.SetString(name, value)
        else:
            # We don't directly support any other types from the JSON file (e.g. arrays)
            type_name = type(self.preferences_defaults[name])
            raise RuntimeError(f"Unrecognized type for {name}: {type_name}")

    def rem(self, name: str):
        """Remove the preference. Must have an entry in the
        addonmanager_preferences_defaults.json file."""
        if name not in self.preferences_defaults:
            raise RuntimeError(
                f"Unrecognized preference {name} -- did you add "
                + "it to addonmanager_preferences_defaults.json?"
            )
        if isinstance(self.preferences_defaults[name], bool):
            return self.prefs.RemBool(name)
        if isinstance(self.preferences_defaults[name], int):
            return self.prefs.RemInt(name)
        if isinstance(self.preferences_defaults[name], float):
            return self.prefs.RemFloat(name)
        if isinstance(self.preferences_defaults[name], str):
            return self.prefs.RemString(name)
        # We don't directly support any other types from the JSON file (e.g. arrays)
        type_name = type(self.preferences_defaults[name])
        raise RuntimeError(f"Unrecognized type for {name}: {type_name}")

    @classmethod
    def _load_preferences_defaults(cls, filename=None):
        """Loads the preferences defaults JSON file from either a specified file, or
        from the standard addonmanager_preferences_defaults.json file."""

        if filename is None:
            json_file = os.path.join(
                os.path.dirname(__file__), "addonmanager_preferences_defaults.json"
            )
        else:
            json_file = filename
        with open(json_file, "r", encoding="utf-8") as f:
            file_contents = f.read()
        cls.preferences_defaults = json.loads(file_contents)
