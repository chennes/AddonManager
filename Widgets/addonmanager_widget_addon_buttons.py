# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2024 FreeCAD Project Association                        *
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

"""Defines a QWidget-derived class for displaying the single-addon buttons."""

from enum import Enum, auto
import os
from typing import List

from addonmanager_freecad_interface import translate

from PySideWrapper import QtCore, QtGui, QtWidgets


class ButtonBarDisplayMode(Enum):
    TextOnly = auto()
    IconsOnly = auto()
    TextAndIcons = auto()


class WidgetAddonButtons(QtWidgets.QWidget):

    install_branch = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget = None):
        super().__init__(parent)
        self.setup_to_change_branch = False
        self.is_addon_manager = False
        self.actions = []
        self.branch_menu = None
        self.display_mode = ButtonBarDisplayMode.TextAndIcons
        self._setup_ui()
        self._set_icons()
        self.retranslateUi(None)

    def set_display_mode(self, mode: ButtonBarDisplayMode):
        """NOTE: Not really implemented yet -- TODO: Implement this functionality"""
        if mode == self.display_mode:
            return
        self._setup_ui()
        self._set_icons()
        self.retranslateUi(None)

    def set_can_check_for_updates(self, can_check_for_updates: bool):
        """Only non-catalog addons have a separate update checker -- addons in the catalog don't
        query their update status individually."""
        self.update.setVisible(can_check_for_updates)

    def set_installation_status(
        self, installed: bool, available_branches: List[str], disabled: bool
    ):
        """Set up the buttons for a given installation status.
        :param installed: Whether the addon is currently installed or not.
        :param available_branches: The list of branches available -- cna be empty in which case it is
        not presented to the user as an option to change.
        :param disabled: Whether the addon is currently disabled."""
        self.is_addon_manager = False
        self.setup_to_change_branch = False
        self.uninstall.setVisible(installed)
        if not available_branches or len(available_branches) == 1 and not installed:
            self.install.setVisible(not installed)
            self.install.setMenu(None)
            self.branch_menu = None
        else:
            self.install.setVisible(True)
            self.branch_menu = QtWidgets.QMenu()
            self.install.setMenu(self.branch_menu)
            self.actions.clear()
            if installed:
                self.setup_to_change_branch = True
            for branch in available_branches:
                if hasattr(QtGui, "QAction"):
                    # Qt6
                    new_action = QtGui.QAction()
                else:
                    # Qt5
                    new_action = QtWidgets.QAction()
                new_action.setText(branch)
                new_action.triggered.connect(self.action_activated)
                self.actions.append(new_action)
                self.branch_menu.addAction(new_action)

            self.enable.setVisible(installed and disabled)
            self.disable.setVisible(installed and not disabled)
        self.retranslateUi(None)

    def action_activated(self, _):
        sender = self.sender()
        if not sender:
            return
        if hasattr(sender, "text"):
            self.install_branch.emit(sender.text())

    def set_can_run(self, can_run: bool):
        self.run_macro.setVisible(can_run)

    def setup_for_addon_manager(self):
        """If the addon in question is the Addon Manager itself, then we tweak some things: there
        is no "disable" option, and "uninstall" becomes "revert to built-in"."""
        self.disable.setVisible(False)
        self.enable.setVisible(False)
        self.is_addon_manager = True
        self.retranslateUi(None)

    def _setup_ui(self):
        if self.layout():
            self.setLayout(None)  # TODO: Check this
        self.horizontal_layout = QtWidgets.QHBoxLayout()
        self.horizontal_layout.setContentsMargins(0, 0, 0, 0)
        self.back = QtWidgets.QToolButton(self)
        self.install = QtWidgets.QPushButton(self)
        self.uninstall = QtWidgets.QPushButton(self)
        self.enable = QtWidgets.QPushButton(self)
        self.disable = QtWidgets.QPushButton(self)
        self.update = QtWidgets.QPushButton(self)
        self.run_macro = QtWidgets.QPushButton(self)
        self.check_for_update = QtWidgets.QPushButton(self)
        self.horizontal_layout.addWidget(self.back)
        self.horizontal_layout.addStretch()
        self.horizontal_layout.addWidget(self.check_for_update)
        self.horizontal_layout.addWidget(self.install)
        self.horizontal_layout.addWidget(self.uninstall)
        self.horizontal_layout.addWidget(self.enable)
        self.horizontal_layout.addWidget(self.disable)
        self.horizontal_layout.addWidget(self.update)
        self.horizontal_layout.addWidget(self.run_macro)
        self.setLayout(self.horizontal_layout)

    def set_show_back_button(self, show: bool) -> None:
        self.back.setVisible(show)

    def _set_icons(self):
        icon_path = os.path.join(os.path.dirname(__file__), "..", "Resources", "icons")
        self.back.setIcon(
            QtGui.QIcon.fromTheme("back", QtGui.QIcon(os.path.join(icon_path, "button_left.svg")))
        )

    def retranslateUi(self, _):
        self.check_for_update.setText(translate("AddonsInstaller", "Check for Update"))
        if self.setup_to_change_branch:
            self.install.setText(translate("AddonsInstaller", "Switch to branch"))
        elif self.is_addon_manager:
            self.install.setText(translate("AddonsInstaller", "Override built-in"))
        else:
            self.install.setText(translate("AddonsInstaller", "Install"))
        self.disable.setText(translate("AddonsInstaller", "Disable"))
        self.enable.setText(translate("AddonsInstaller", "Enable"))
        self.update.setText(translate("AddonsInstaller", "Update"))
        self.run_macro.setText(translate("AddonsInstaller", "Run"))
        self.back.setToolTip(translate("AddonsInstaller", "Return to Package List"))
        if self.is_addon_manager:
            self.uninstall.setText(translate("AddonsInstaller", "Revert to built-in"))
        else:
            self.uninstall.setText(translate("AddonsInstaller", "Uninstall"))
