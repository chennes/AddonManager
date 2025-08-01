# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022-2025 The FreeCAD project association AISBL         *
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

"""Classes to manage the GUI presentation of installing an Addon (e.g. the sequence of dialog boxes
that do dependency resolution, error handling, etc.). See AddonInstallerGUI and MacroInstallerGUI
classes for details."""

import os
import sys
from typing import List

import addonmanager_freecad_interface as fci
from addonmanager_toolbar_adapter import ToolbarAdapter

# Get whatever version of PySide we can
try:
    from PySide import QtCore, QtWidgets  # Use the FreeCAD wrapper
except ImportError:
    try:
        from PySide6 import QtCore, QtWidgets  # Outside FreeCAD, try Qt6 first
    except ImportError:
        from PySide2 import QtCore, QtWidgets  # Fall back to Qt5

from addonmanager_installer import AddonInstaller, MacroInstaller
from addonmanager_dependency_installer import DependencyInstaller
import addonmanager_utilities as utils
from Addon import Addon, MissingDependencies

translate = fci.translate

# pylint: disable=c-extension-no-member,too-few-public-methods,too-many-instance-attributes


class AddonInstallerGUI(QtCore.QObject):
    """GUI functions (sequence of dialog boxes) for installing an addon interactively. The actual
    installation is handled by the AddonInstaller class running in a separate QThread. An instance
    of this AddonInstallerGUI class should NOT be run in a separate thread, but on the main GUI
    thread. All dialogs are modal."""

    # External classes are expected to "set and forget" this class, but in the event that some
    # action must be taken if the addon is actually installed, this signal is provided. Note that
    # this class already provides a "Successful installation" dialog, so external code need not
    # do so.
    success = QtCore.Signal(object)

    # Emitted once all work has been completed, regardless of success or failure
    finished = QtCore.Signal()

    def __init__(self, addon: Addon, addons: List[Addon] = None):
        super().__init__()
        self.addon_to_install = addon
        self.addons = [] if addons is None else addons
        self.installer = AddonInstaller(addon)
        self.dependency_installer = None
        self.install_worker = None
        self.dependency_dialog = None
        self.dependency_worker_thread = None
        self.dependency_installation_dialog = None
        self.installing_dialog = None
        self.worker_thread = None

        # Set up the installer connections
        self.installer.success.connect(self._installation_succeeded)
        self.installer.failure.connect(self._installation_failed)

    def shutdown(self):
        try:
            self._stop_thread(self.worker_thread)
            self._stop_thread(self.dependency_worker_thread)
        except RuntimeError:
            # In some circumstances during shutdown the underlying C++ thread may already be gone: we don't care
            pass

    @staticmethod
    def _stop_thread(thread: QtCore.QThread):
        if thread and hasattr(thread, "quit"):
            if thread.isRunning():
                fci.Console.PrintMessage(
                    "INTERNAL ERROR: a QThread is still running when it should have finished"
                )

                thread.requestInterruption()
                thread.wait(100)
            thread.quit()
            thread.wait(500)
            if thread.isRunning():
                fci.Console.PrintError(
                    "INTERNAL ERROR: Thread did not quit() cleanly, using terminate()\n"
                )
                thread.terminate()

    def run(self):
        """Instructs this class to begin displaying the necessary dialogs to guide a user through
        an Addon installation sequence. All dialogs are modal."""

        # Dependency check
        deps = MissingDependencies(self.addon_to_install, self.addons)

        # Python interpreter version check
        stop_installation = self._check_python_version(deps)
        if stop_installation:
            self.finished.emit()
            return

        # Required Python
        if hasattr(deps, "python_requires") and deps.python_requires:
            # Disallowed packages:
            stop_installation = self._handle_disallowed_python(deps.python_requires)
            if stop_installation:
                self.finished.emit()
                return
            # Allowed but uninstalled is handled below

        # Remove any disallowed packages from the optional list
        if hasattr(deps, "python_optional") and deps.python_optional:
            self._clean_up_optional(deps)

        # Missing FreeCAD workbenches
        if hasattr(deps, "wbs") and deps.wbs:
            stop_installation = self._report_missing_workbenches(deps.wbs)
            if stop_installation:
                self.finished.emit()
                return

        # If we have any missing dependencies, display a dialog to the user asking if they want to
        # install them.
        if deps.external_addons or deps.python_requires or deps.python_optional:
            # Recoverable: ask the user if they want to install the missing deps, do so, then
            # proceed with the installation
            self._resolve_dependencies_then_install(deps)
        else:
            # No missing deps, just install
            self.install()

    def _handle_disallowed_python(self, python_requires: List[str]) -> bool:
        """Determine if we are missing any required Python packages that are not in the allowed
        packages list. If so, display a message to the user, and return True. Otherwise return
        False."""

        bad_packages = []
        for dep in python_requires:
            if dep.lower() not in AddonInstaller.allowed_packages:
                bad_packages.append(dep)

        for dep in bad_packages:
            python_requires.remove(dep)

        if bad_packages:
            # pylint: disable=line-too-long
            message = (
                "<p>"
                + translate(
                    "AddonsInstaller",
                    "This addon requires Python packages that are not installed, and cannot be installed automatically. To use this addon you must install the following Python packages manually:",
                )
                + "</p><ul>"
            )
            if len(bad_packages) < 15:
                for dep in bad_packages:
                    message += f"<li>{dep}</li>"
            else:
                message += "<li>(" + translate("AddonsInstaller", "Too many to list") + ")</li>"
            message += "</ul>"
            message += "To ignore this error and install anyway, press OK."
            r = QtWidgets.QMessageBox.critical(
                utils.get_main_am_window(),
                translate("AddonsInstaller", "Missing Requirement"),
                message,
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
            )

            if r == QtWidgets.QMessageBox.Ok:
                # Force the installation to proceed
                return False
            return True
        return False

    def _report_missing_workbenches(self, wbs) -> bool:
        """If there are missing workbenches, display a dialog informing the user. Returns True to
        stop the installation, or False to proceed."""
        addon_name = self.addon_to_install.name
        if len(wbs) == 1:
            name = wbs[0]
            message = translate(
                "AddonsInstaller",
                "Addon '{}' requires '{}', which is not available in your copy of FreeCAD.",
            ).format(addon_name, name)
        else:
            # pylint: disable=line-too-long
            message = (
                "<p>"
                + translate(
                    "AddonsInstaller",
                    "Addon '{}' requires the following workbenches, which are not available in your copy of FreeCAD:",
                ).format(addon_name)
                + "</p><ul>"
            )
            for wb in wbs:
                message += "<li>" + wb + "</li>"
            message += "</ul>"
            message += translate("AddonsInstaller", "Press OK to install anyway.")
        r = QtWidgets.QMessageBox.critical(
            utils.get_main_am_window(),
            translate("AddonsInstaller", "Missing Requirement"),
            message,
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        )
        return r == QtWidgets.QMessageBox.Cancel

    def _resolve_dependencies_then_install(self, missing) -> None:
        """Ask the user how they want to handle dependencies, do that, then install."""
        self.dependency_dialog = fci.loadUi(
            os.path.join(os.path.dirname(__file__), "dependency_resolution_dialog.ui")
        )

        for addon in missing.external_addons:
            self.dependency_dialog.listWidgetAddons.addItem(addon)
        for mod in missing.python_requires:
            self.dependency_dialog.listWidgetPythonRequired.addItem(mod)
        for mod in missing.python_optional:
            item = QtWidgets.QListWidgetItem(mod)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            self.dependency_dialog.listWidgetPythonOptional.addItem(item)

        self.dependency_dialog.buttonBox.button(QtWidgets.QDialogButtonBox.Yes).clicked.connect(
            self._dependency_dialog_yes_clicked
        )
        self.dependency_dialog.buttonBox.button(QtWidgets.QDialogButtonBox.Ignore).clicked.connect(
            self._dependency_dialog_ignore_clicked
        )
        self.dependency_dialog.buttonBox.button(QtWidgets.QDialogButtonBox.Cancel).setDefault(True)
        self.dependency_dialog.exec()

    def _check_python_version(self, missing: MissingDependencies) -> bool:
        """Make sure we have a compatible Python version. Returns True to stop the installation
        or False to continue."""

        # For now only look at the minor version, since major is always Python 3
        minor_required = missing.python_min_version["minor"]
        if sys.version_info.minor < minor_required:
            # pylint: disable=line-too-long
            QtWidgets.QMessageBox.critical(
                utils.get_main_am_window(),
                translate("AddonsInstaller", "Incompatible Python version"),
                translate(
                    "AddonsInstaller",
                    "This addon (or one of its dependencies) requires Python {}.{}, and your system is running {}.{}. Installation cancelled.",
                ).format(
                    missing.python_min_version["major"],
                    missing.python_min_version["minor"],
                    sys.version_info.major,
                    sys.version_info.minor,
                ),
                QtWidgets.QMessageBox.Cancel,
            )
            return True
        return False

    def _clean_up_optional(self, missing: MissingDependencies):
        good_packages = []
        for dep in missing.python_optional:
            if dep in self.installer.allowed_packages:
                good_packages.append(dep)
            else:
                fci.Console.PrintWarning(
                    translate(
                        "AddonsInstaller",
                        "Optional dependency on {} ignored because it is not in the allow-list",
                    ).format(dep)
                    + "\n"
                )
        missing.python_optional = good_packages

    def _dependency_dialog_yes_clicked(self) -> None:
        # Get the lists out of the dialog:
        addons = []
        for row in range(self.dependency_dialog.listWidgetAddons.count()):
            item = self.dependency_dialog.listWidgetAddons.item(row)
            name = item.text()
            for repo in self.addons:
                if repo.name == name or (
                    hasattr(repo, "display_name") and repo.display_name == name
                ):
                    addons.append(repo)

        python_requires = []
        for row in range(self.dependency_dialog.listWidgetPythonRequired.count()):
            item = self.dependency_dialog.listWidgetPythonRequired.item(row)
            python_requires.append(item.text())

        python_optional = []
        for row in range(self.dependency_dialog.listWidgetPythonOptional.count()):
            item = self.dependency_dialog.listWidgetPythonOptional.item(row)
            if item.checkState() == QtCore.Qt.Checked:
                python_optional.append(item.text())

        self._run_dependency_installer(addons, python_requires, python_optional)

    def _run_dependency_installer(self, addons, python_requires, python_optional):
        """Run the dependency installer (in a separate thread) for the given dependencies"""
        self.dependency_installer = DependencyInstaller(addons, python_requires, python_optional)
        self.dependency_installer.no_python_exe.connect(self._report_no_python_exe)
        self.dependency_installer.no_pip.connect(self._report_no_pip)
        self.dependency_installer.failure.connect(self._report_dependency_failure)
        self.dependency_installer.finished.connect(self._dependencies_finished)

        self.dependency_worker_thread = QtCore.QThread(self)
        self.dependency_worker_thread.setObjectName("Dependency Installer Thread")
        self.dependency_installer.moveToThread(self.dependency_worker_thread)
        self.dependency_worker_thread.started.connect(self.dependency_installer.run)

        self.dependency_installation_dialog = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Information,
            translate("AddonsInstaller", "Installing dependencies"),
            translate("AddonsInstaller", "Installing dependencies") + "...",
            QtWidgets.QMessageBox.Cancel,
            parent=utils.get_main_am_window(),
        )
        self.dependency_installation_dialog.rejected.connect(self._cancel_dependency_installation)
        self.dependency_installation_dialog.show()
        self.dependency_worker_thread.start()

    def _report_no_python_exe(self) -> None:
        """Callback for the dependency installer failing to locate a Python executable."""
        if self.dependency_installation_dialog is not None:
            self.dependency_installation_dialog.hide()
        # pylint: disable=line-too-long
        result = QtWidgets.QMessageBox.critical(
            utils.get_main_am_window(),
            translate("AddonsInstaller", "Cannot execute Python"),
            translate(
                "AddonsInstaller",
                "Failed to automatically locate your Python executable, or the path is set incorrectly. Please check the Addon Manager preferences setting for the path to Python.",
            )
            + "\n\n"
            + translate(
                "AddonsInstaller",
                "Dependencies could not be installed. Continue with installation of {} anyway?",
            ).format(self.addon_to_install.name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if result == QtWidgets.QMessageBox.Yes:
            self.install()
        else:
            self.finished.emit()

    def _report_no_pip(self, command: str) -> None:
        """Callback for the dependency installer failing to access pip."""
        if self.dependency_installation_dialog is not None:
            self.dependency_installation_dialog.hide()
        # pylint: disable=line-too-long
        result = QtWidgets.QMessageBox.critical(
            utils.get_main_am_window(),
            translate("AddonsInstaller", "Cannot execute pip"),
            translate(
                "AddonsInstaller",
                "Failed to execute pip, which may be missing from your Python installation. Please ensure your system "
                "has pip installed and try again. The failed command was:",
            )
            + f" \n\n{command}\n\n"
            + translate(
                "AddonsInstaller",
                "Continue with installation of {} anyway?",
            ).format(self.addon_to_install.name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if result == QtWidgets.QMessageBox.Yes:
            self.install()
        else:
            self.finished.emit()

    def _report_dependency_failure(self, short_message: str, details: str) -> None:
        """Callback for dependency installation failure."""
        if self.dependency_installation_dialog is not None:
            self.dependency_installation_dialog.hide()
        if self.dependency_installer and hasattr(self.dependency_installer, "finished"):
            self.dependency_installer.finished.disconnect(self._report_dependency_success)
        fci.Console.PrintError(details + "\n")
        result = QtWidgets.QMessageBox.critical(
            utils.get_main_am_window(),
            translate("AddonsInstaller", "Package installation failed"),
            short_message
            + "\n\n"
            + translate("AddonsInstaller", "See Report View for detailed failure log.")
            + "\n\n"
            + translate(
                "AddonsInstaller",
                "Continue with installation of {} anyway?",
            ).format(self.addon_to_install.name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if result == QtWidgets.QMessageBox.Yes:
            self.install()
        else:
            self.finished.emit()

    def _report_dependency_success(self):
        """Callback for dependency installation success."""
        if self.dependency_installation_dialog is not None:
            self.dependency_installation_dialog.hide()
        self.install()

    def _dependencies_finished(self, success: bool):
        if success:
            self._report_dependency_success()
        self.dependency_worker_thread.quit()

    def _dependency_dialog_ignore_clicked(self) -> None:
        """Callback for when dependencies are ignored."""
        self.install()

    def _cancel_dependency_installation(self) -> None:
        """Cancel was clicked in the dialog. NOTE: Does no cleanup, the state after cancellation is
        unknown. In most cases pip can recover from whatever we've done to it."""
        self.dependency_worker_thread.blockSignals(True)
        self.dependency_installer.blockSignals(True)
        self.dependency_worker_thread.requestInterruption()
        self.dependency_installation_dialog.hide()
        self.dependency_worker_thread.wait()
        self.finished.emit()

    def install(self) -> None:
        """Installs or updates a workbench, macro, or package"""
        self.worker_thread = QtCore.QThread()
        self.worker_thread.setObjectName("AddonInstallerGUI worker thread")
        self.installer.moveToThread(self.worker_thread)
        self.installer.finished.connect(self.worker_thread.quit)
        self.worker_thread.started.connect(self.installer.run)

        self.installing_dialog = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.NoIcon,
            translate("AddonsInstaller", "Installing Addon"),
            translate("AddonsInstaller", "Installing FreeCAD addon '{}'").format(
                self.addon_to_install.display_name
            ),
            QtWidgets.QMessageBox.Cancel,
            parent=utils.get_main_am_window(),
        )
        self.installing_dialog.rejected.connect(self._cancel_addon_installation)
        self.installer.finished.connect(self.installing_dialog.hide)
        self.installing_dialog.show()
        self.worker_thread.start()  # Returns immediately

    def _cancel_addon_installation(self):
        dlg = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.NoIcon,
            translate("AddonsInstaller", "Cancelling"),
            translate("AddonsInstaller", "Cancelling installation of '{}'").format(
                self.addon_to_install.display_name
            ),
            QtWidgets.QMessageBox.NoButton,
            parent=utils.get_main_am_window(),
        )
        dlg.show()
        if self.worker_thread.isRunning():
            # Interruption can take a second or more, depending on what was being done. Make sure
            # we stay responsive and update the dialog with the text above, etc.
            self.worker_thread.requestInterruption()
            self.worker_thread.quit()
            while self.worker_thread.isRunning():
                self.worker_thread.wait(50)
                QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.AllEvents)
        path = os.path.join(self.installer.installation_path, self.addon_to_install.name)
        if os.path.exists(path):
            utils.rmdir(path)
        dlg.hide()
        self.finished.emit()

    def _installation_succeeded(self):
        """Called if the installation was successful."""
        QtWidgets.QMessageBox.information(
            utils.get_main_am_window(),
            translate("AddonsInstaller", "Success"),
            translate("AddonsInstaller", "{} was installed successfully").format(
                self.addon_to_install.name
            ),
            QtWidgets.QMessageBox.Ok,
        )
        self.success.emit(self.addon_to_install)
        self.finished.emit()

    def _installation_failed(self, addon, message):
        """Called if the installation failed."""
        error_dialog = QtWidgets.QMessageBox(utils.get_main_am_window())
        error_dialog.setIcon(QtWidgets.QMessageBox.Critical)
        error_dialog.setWindowTitle(translate("AddonsInstaller", "Installation Failed"))
        error_dialog.setText(
            translate("AddonsInstaller", "Failed to install {}").format(addon.name)
        )
        error_dialog.setStandardButtons(QtWidgets.QMessageBox.Cancel)
        error_dialog.setDetailedText(message)
        error_dialog.exec()
        self.finished.emit()


class MacroInstallerGUI(QtCore.QObject):
    """Install a macro, providing feedback about the process via dialog boxes, and then offer to
    add the macro to a custom toolbar. Should be run on the main GUI thread: this class internally
    launches a QThread for the actual installation process."""

    # Only success should matter to external code: all user interaction is handled via this class
    success = QtCore.Signal(object)

    # Emitted once all work has been completed, regardless of success or failure
    finished = QtCore.Signal()

    def __init__(self, addon):
        """The provided addon object must have an attribute called "macro", and that attribute must
        itself provide a callable "install" method that takes a single string, the path to the
        installation location."""
        super().__init__()
        self.addon_to_install = addon
        self.worker_thread = None
        self.installer = MacroInstaller(self.addon_to_install)
        self.toolbar_adapter = ToolbarAdapter()
        self.macro_dir = fci.DataPaths().macro_dir

    def quit(self):
        if self.worker_thread and hasattr(self.worker_thread, "quit"):
            self.worker_thread.quit()
            self.worker_thread.wait(500)
            if self.worker_thread.isRunning():
                fci.Console.PrintError(
                    "INTERNAL ERROR: Thread did not quit() cleanly, using terminate()\n"
                )
                self.worker_thread.clean_terminate()

    def run(self):
        """Perform the installation, including any necessary user interaction via modal dialog
        boxes. If installation proceeds successfully to completion, emits the success() signal."""

        self.worker_thread = QtCore.QThread()
        self.worker_thread.setObjectName("MacroInstallerGUI worker thread")
        self.installer.moveToThread(self.worker_thread)
        self.installer.finished.connect(self.worker_thread.quit)
        self.installer.success.connect(self._base_installation_success)
        self.worker_thread.started.connect(self.installer.run)
        self.worker_thread.start()  # Returns immediately

    def _base_installation_success(self):
        """Callback for a successful basic macro installation."""
        self.success.emit(self.addon_to_install)
        self._ask_to_install_toolbar_button()  # Synchronous set of modals
        self.finished.emit()

    def _ask_to_install_toolbar_button(self) -> None:
        """Presents a dialog to the user asking if they want to install a toolbar button for
        a particular macro, and walks through that process if they agree to do so."""
        do_not_show_dialog = fci.Preferences().get("dontShowAddMacroButtonDialog")
        button_exists = self._macro_button_exists()
        if not do_not_show_dialog and not button_exists:
            add_toolbar_button_dialog = fci.loadUi(
                os.path.join(os.path.dirname(__file__), "add_toolbar_button_dialog.ui")
            )
            add_toolbar_button_dialog.buttonYes.clicked.connect(self._install_toolbar_button)
            add_toolbar_button_dialog.buttonNever.clicked.connect(
                lambda: fci.Preferences().set("dontShowAddMacroButtonDialog", True)
            )
            add_toolbar_button_dialog.exec()

    def _macro_button_exists(self) -> bool:
        """Returns True if a button already exists for this macro, or False if not."""
        command = self.toolbar_adapter.find_custom_command(self.addon_to_install.macro.filename)
        if not command:
            return False
        toolbars = self.toolbar_adapter.get_toolbars()
        for toolbar in toolbars:
            if toolbar.GetString(command, "*") != "*":
                return True
        return False

    def _ask_for_toolbar(self, custom_toolbars):
        """Determine what toolbar to add the icon to. The first time it is called it prompts the
        user to select or create a toolbar. After that, the prompt is optional and can be configured
        via a preference. Returns the pref group for the new toolbar."""

        # If this is the first time we got to this chunk of code, we are always going to ask.
        first_time = fci.Preferences().get("FirstTimeAskingForToolbar")
        fci.Preferences().set("FirstTimeAskingForToolbar", False)
        ask = fci.Preferences().get("alwaysAskForToolbar")

        if first_time or ask:
            select_toolbar_dialog = fci.loadUi(
                os.path.join(os.path.dirname(__file__), "select_toolbar_dialog.ui")
            )

            select_toolbar_dialog.comboBox.clear()

            for group in custom_toolbars:
                name = self.toolbar_adapter.get_toolbar_name(group)
                if name:
                    select_toolbar_dialog.comboBox.addItem(name)
                else:
                    fci.Console.PrintWarning(
                        f"Custom toolbar {group} does not have a Name element\n"
                    )
            new_menubar_option_text = translate("AddonsInstaller", "Create new toolbar")
            select_toolbar_dialog.comboBox.addItem(new_menubar_option_text)

            result = select_toolbar_dialog.exec()
            if result == QtWidgets.QDialog.Accepted:
                selection = select_toolbar_dialog.comboBox.currentText()
                if select_toolbar_dialog.checkBox.checkState() == QtCore.Qt.Unchecked:
                    fci.Preferences().set("alwaysAskForToolbar", False)
                else:
                    fci.Preferences().set("alwaysAskForToolbar", True)
                if selection == new_menubar_option_text:
                    return self.toolbar_adapter.create_new_custom_toolbar()
                return self.toolbar_adapter.get_toolbar_with_name(selection)
            return None

        # If none of the above code returned...
        custom_toolbar_name = fci.Preferences().get("CustomToolbarName")
        toolbar = self.toolbar_adapter.get_toolbar_with_name(custom_toolbar_name)
        if not toolbar:
            # They told us not to ask, but then the toolbar got deleted... ask anyway!
            fci.Preferences().rem("alwaysAskForToolbar")
            return self._ask_for_toolbar(custom_toolbars)
        return toolbar

    def _install_toolbar_button(self) -> None:
        """If the user has requested a toolbar button be installed, this function is called
        to continue the process and request any additional required information."""
        custom_toolbar_name = fci.Preferences().get("CustomToolbarName")

        # Default to false here: if the variable hasn't been set, we don't assume
        # that we have to ask, because the simplest is to just create a new toolbar
        # and never ask at all.
        ask = fci.Preferences().get("alwaysAskForToolbar")

        # See if there is already a custom toolbar for macros:
        custom_toolbars = self.toolbar_adapter.get_toolbars()
        if custom_toolbars:
            # If there are already custom toolbars, see if one of them is the one we used last time
            found_toolbar = False
            for toolbar_pref_group in custom_toolbars:
                name = self.toolbar_adapter.get_toolbar_name(toolbar_pref_group)
                if name == custom_toolbar_name:
                    custom_toolbar = toolbar_pref_group
                    found_toolbar = True
                    break
            if ask or not found_toolbar:
                # We have to ask the user what to do...
                custom_toolbar = self._ask_for_toolbar(custom_toolbars)
                if custom_toolbar:
                    custom_toolbar_name = custom_toolbar.GetString("Name")
                    fci.Preferences().set("CustomToolbarName", custom_toolbar_name)
        else:
            # Create a custom toolbar
            custom_toolbar = self.toolbar_adapter.create_new_custom_toolbar()

        if custom_toolbar:
            self._install_macro_to_toolbar(custom_toolbar)
        else:
            fci.Console.PrintMessage("In the end, no custom toolbar was set, bailing out\n")

    def _install_macro_to_toolbar(self, toolbar) -> None:
        """Adds an icon for the given macro to the given toolbar."""
        menuText = self.addon_to_install.display_name
        tooltipText = f"<b>{self.addon_to_install.display_name}</b>"
        if self.addon_to_install.macro.comment:
            tooltipText += f"<br/><p>{self.addon_to_install.macro.comment}</p>"
            whatsThisText = self.addon_to_install.macro.comment
        else:
            whatsThisText = translate(
                "AddonsInstaller", "A macro installed with the FreeCAD Addon Manager"
            )
        statustipText = (
            translate("AddonsInstaller", "Run", "Indicates a macro that can be 'run'")
            + " "
            + self.addon_to_install.display_name
        )
        if self.addon_to_install.macro.icon:
            if os.path.isabs(self.addon_to_install.macro.icon):
                pixmapText = os.path.normpath(self.addon_to_install.macro.icon)
            else:
                pixmapText = os.path.normpath(
                    os.path.join(self.macro_dir, self.addon_to_install.macro.icon)
                )
        elif self.addon_to_install.macro.xpm:
            icon_file = os.path.normpath(
                os.path.join(self.macro_dir, self.addon_to_install.macro.name + "_icon.xpm")
            )
            with open(icon_file, "w", encoding="utf-8") as f:
                f.write(self.addon_to_install.macro.xpm)
            pixmapText = icon_file
        else:
            pixmapText = None

        # Add this command to that toolbar
        self.toolbar_adapter.create_custom_command(
            toolbar,
            self.addon_to_install.macro.filename,
            menuText,
            tooltipText,
            whatsThisText,
            statustipText,
            pixmapText,
        )
