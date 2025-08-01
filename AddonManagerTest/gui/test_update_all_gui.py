# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022-2025 FreeCAD project association AISBL             *
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

from time import sleep
import unittest

from Addon import Addon
from addonmanager_update_all_gui import UpdateAllGUI

try:
    from PySide import QtCore, QtWidgets
except ImportError:
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

from addonmanager_update_all_gui import AddonStatus


class MockUpdater(QtCore.QObject):
    success = QtCore.Signal(object)
    failure = QtCore.Signal(object)
    finished = QtCore.Signal()

    def __init__(self, addon, addons=[]):
        super().__init__()
        self.addon_to_install = addon
        self.addons = addons
        self.has_run = False
        self.emit_success = True
        self.work_function = None  # Set to some kind of callable to make this function take time

    def run(self):
        self.has_run = True
        if self.work_function is not None and callable(self.work_function):
            self.work_function()
        if self.emit_success:
            self.success.emit(self.addon_to_install)
        else:
            self.failure.emit(self.addon_to_install)
        self.finished.emit()


class MockUpdaterFactory:
    def __init__(self, addons):
        self.addons = addons
        self.work_function = None
        self.updater = None

    def get_updater(self, addon):
        self.updater = MockUpdater(addon, self.addons)
        self.updater.work_function = self.work_function
        return self.updater


class MockAddon:
    def __init__(self, name):
        self.display_name = name
        self.name = name
        self.macro = None
        self.metadata = None
        self.installed_metadata = None

    def status(self):
        return Addon.Status.UPDATE_AVAILABLE


class CallInterceptor:
    def __init__(self):
        self.called = False
        self.args = None

    def intercept(self, *args):
        self.called = True
        self.args = args


class TestUpdateAllGui(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass
