# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022-2025 The FreeCAD project association AISBL         *
# *   Copyright (c) 2015 Yorik van Havre <yorik@uncreated.net>              *
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

import os
import functools
import tempfile
import threading
from typing import Dict

from PySideWrapper import QtGui, QtCore, QtWidgets, QtSvg

from addonmanager_workers_startup import (
    CreateAddonListWorker,
    CheckWorkbenchesForUpdatesWorker,
    GetBasicAddonStatsWorker,
    GetAddonScoreWorker,
)
from addonmanager_installer_gui import AddonInstallerGUI, MacroInstallerGUI
from addonmanager_uninstaller_gui import AddonUninstallerGUI
from addonmanager_update_all_gui import UpdateAllGUI
import addonmanager_utilities as utils
import addonmanager_freecad_interface as fci
from composite_view import CompositeView
from Widgets.addonmanager_widget_global_buttons import WidgetGlobalButtonBar
from Widgets.addonmanager_widget_progress_bar import Progress
from package_list import PackageListItemModel
from Addon import Addon
from addonmanager_python_deps_gui import (
    PythonPackageManager,
)
from addonmanager_devmode import DeveloperMode
from addonmanager_firstrun import FirstRunDialog
from addonmanager_connection_checker import ConnectionCheckerGUI
from addonmanager_devmode_metadata_checker import MetadataValidators

from addonmanager_metadata import MetadataReader

import NetworkManager

from AddonManagerOptions import AddonManagerOptions

translate = fci.translate


def QT_TRANSLATE_NOOP(_, txt):
    return txt


__title__ = "FreeCAD Addon Manager Module"
__author__ = "Yorik van Havre", "Jonathan Wiedemann", "Kurt Kremitzki", "Chris Hennes"
__url__ = "https://www.freecad.org"

"""
FreeCAD Addon Manager Module

Fetches various types of addons from a variety of sources. Built-in sources are:
* https://github.com/FreeCAD/FreeCAD-addons
* https://github.com/FreeCAD/FreeCAD-macros
* https://wiki.freecad.org/

Additional git sources may be configure via user preferences.

You need a working internet connection, and optionally git -- if git is not available, ZIP archives
are downloaded instead.
"""

#  \defgroup ADDONMANAGER AddonManager
#  \ingroup ADDONMANAGER
#  \brief The Addon Manager allows users to install workbenches and macros made by other users
#  @{

INSTANCE = None


class SvgIconEngine(QtGui.QIconEngine):
    def __init__(self, svg_bytes: bytes):
        super().__init__()
        self.renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg_bytes))

    def paint(self, painter: QtGui.QPainter, rect: QtCore.QRect, mode, state):
        self.renderer.render(painter, rect)

    def pixmap(self, size: QtCore.QSize, mode, state):
        pixmap = QtGui.QPixmap(size)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        self.renderer.render(painter)
        painter.end()
        return pixmap


def scalable_icon_from_svg_bytes(svg_bytes: bytes) -> QtGui.QIcon:
    engine = SvgIconEngine(svg_bytes)
    return QtGui.QIcon(engine)


def get_icon(repo: Addon, update: bool = False) -> QtGui.QIcon:
    """Returns an icon for an Addon. Uses a cached icon if possible, unless `update` is True,
    in which case the icon is regenerated."""

    icon_path = os.path.join(os.path.dirname(__file__), "Resources", "icons")
    path = ""

    if not update:
        if repo.icon and not repo.icon.isNull() and repo.icon.isValid():
            return repo.icon
        elif repo.icon_data:
            repo.icon = scalable_icon_from_svg_bytes(repo.icon_data)
            return repo.icon

    default_icon = QtGui.QIcon(os.path.join(icon_path, "document-package.svg"))
    if repo.repo_type == Addon.Kind.WORKBENCH:
        default_icon = QtGui.QIcon(os.path.join(icon_path, "document-package.svg"))
    elif repo.repo_type == Addon.Kind.MACRO:
        if repo.macro and repo.macro.icon_data:
            if repo.macro.icon_extension == "svg":
                default_icon = scalable_icon_from_svg_bytes(repo.macro.icon_data)
            else:
                pixmap = QtGui.QPixmap()
                pixmap.loadFromData(repo.macro.icon_data)
                default_icon = QtGui.QIcon(pixmap)
        elif repo.macro and repo.macro.xpm:
            cache_path = fci.DataPaths().cache_dir
            am_path = os.path.join(cache_path, "AddonManager", "MacroIcons")
            os.makedirs(am_path, exist_ok=True)
            path = os.path.join(am_path, repo.name + "_icon.xpm")
            if not os.path.exists(path):
                with open(path, "w") as f:
                    f.write(repo.macro.xpm)
            default_icon = QtGui.QIcon(repo.macro.xpm)
        else:
            default_icon = QtGui.QIcon(os.path.join(icon_path, "document-python.svg"))
    elif repo.repo_type == Addon.Kind.PACKAGE:
        # The cache might not have been downloaded yet, check to see if it's there...
        if repo.icon_data:
            default_icon = scalable_icon_from_svg_bytes(repo.icon_data)
        elif repo.contains_workbench():
            default_icon = QtGui.QIcon(os.path.join(icon_path, "document-package.svg"))
        elif repo.contains_macro():
            default_icon = QtGui.QIcon(os.path.join(icon_path, "document-python.svg"))
        else:
            default_icon = QtGui.QIcon(os.path.join(icon_path, "document-package.svg"))

    if QtCore.QFile.exists(path):
        addon_icon = QtGui.QIcon(path)
    else:
        addon_icon = default_icon
    repo.icon = addon_icon

    return addon_icon


class CommandAddonManager(QtCore.QObject):
    """The main Addon Manager class and FreeCAD command"""

    workers = [
        "create_addon_list_worker",
        "check_worker",
        "show_worker",
        "update_all_worker",
        "check_for_python_package_updates_worker",
        "get_basic_addon_stats_worker",
        "get_addon_score_worker",
    ]

    lock = threading.Lock()
    restart_required = False

    finished = QtCore.Signal()

    def __init__(self):
        super().__init__()

        QT_TRANSLATE_NOOP("QObject", "Addon Manager")
        if fci.GuiUp:
            fci.addPreferencePage(
                AddonManagerOptions,
                "Addon Manager",
            )

        self.item_model = None
        self.developer_mode = None
        self.installer_gui = None
        self.composite_view = None
        self.button_bar = None

        self.dialog = None
        self.startup_sequence = []
        self.packages_with_updates = set()

        self.number_of_progress_regions = 0
        self.current_progress_region = 0

        self.check_worker = None
        self.check_for_python_package_updates_worker = None
        self.update_all_worker = None
        self.create_addon_list_worker = None
        self.get_addon_score_worker = None
        self.get_basic_addon_stats_worker = None

        self.manage_python_packages_dialog = None

        # Set up the connection checker
        self.connection_checker = ConnectionCheckerGUI()
        self.connection_checker.connection_available.connect(self.launch)

        # Give other parts of the AM access to the current instance
        global INSTANCE
        INSTANCE = self

    def GetResources(self) -> Dict[str, str]:
        """FreeCAD-required function: get the core resource information for this Mod."""
        return {
            "Pixmap": "AddonManager",
            "MenuText": QT_TRANSLATE_NOOP("Std_AddonMgr", "&Addon Manager"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Std_AddonMgr",
                "Manages external workbenches, macros, and preference packs",
            ),
            "Group": "Tools",
        }

    def Activated(self) -> None:
        """FreeCAD-required function: called when the command is activated."""
        NetworkManager.InitializeNetworkManager()
        first_run_dialog = FirstRunDialog()
        if not first_run_dialog.exec():
            return
        self.connection_checker.start()

    def launch(self) -> None:
        """Shows the Addon Manager UI"""

        # create the dialog
        self.dialog = fci.loadUi(os.path.join(os.path.dirname(__file__), "AddonManager.ui"))
        self.dialog.setObjectName("AddonManager_Main_Window")
        # self.dialog.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        metadata = MetadataReader.from_file(os.path.join(os.path.dirname(__file__), "package.xml"))
        am_version = str(metadata.version)
        self.dialog.setWindowTitle(translate("AddonsInstaller", "Addon Manager v") + am_version)

        # clean up the leftovers from previous runs
        self.packages_with_updates = set()
        self.startup_sequence = []
        self.cleanup_workers()

        # restore window geometry from the stored state
        w = fci.Preferences().get("WindowWidth")
        h = fci.Preferences().get("WindowHeight")
        self.composite_view = CompositeView(self.dialog)
        self.button_bar = WidgetGlobalButtonBar(self.dialog)

        # If we are checking for updates automatically, hide the Check for updates button:
        autocheck = fci.Preferences().get("AutoCheck")
        if autocheck:
            self.button_bar.check_for_updates.hide()
        else:
            self.button_bar.update_all_addons.hide()

        # Set up the listing of packages using the model-view-controller architecture
        self.item_model = PackageListItemModel()
        self.composite_view.setModel(self.item_model)
        self.dialog.layout().addWidget(self.composite_view)
        self.dialog.layout().addWidget(self.button_bar)

        # set nice icons to everything, by theme with fallback to FreeCAD icons
        icon_path = os.path.join(os.path.dirname(__file__), "Resources", "icons")
        self.dialog.setWindowIcon(QtGui.QIcon(os.path.join(icon_path, "addon_manager.svg")))

        dev_mode_active = fci.Preferences().get("developerMode")

        # enable/disable stuff
        self.button_bar.update_all_addons.setEnabled(False)
        self.hide_progress_widgets()
        if dev_mode_active:
            self.button_bar.developer_tools.show()
        else:
            self.button_bar.developer_tools.hide()

        # connect slots
        self.dialog.rejected.connect(self.reject)
        self.dialog.accepted.connect(self.accept)
        self.button_bar.update_all_addons.clicked.connect(self.update_all)
        self.button_bar.close.clicked.connect(self.dialog.reject)
        self.button_bar.check_for_updates.clicked.connect(
            lambda: self.force_check_updates(standalone=True)
        )
        self.button_bar.python_dependencies.triggered.connect(self.show_python_updates_dialog)
        self.button_bar.addons_folder.triggered.connect(self.open_addons_folder)
        self.button_bar.developer_tools.clicked.connect(self.show_developer_tools)
        self.composite_view.package_list.stop_loading.connect(self.stop_update)
        self.composite_view.package_list.setEnabled(False)
        self.composite_view.execute.connect(self.execute_macro)
        self.composite_view.install.connect(self.launch_installer_gui)
        self.composite_view.uninstall.connect(self.remove)
        self.composite_view.update.connect(self.update)
        self.composite_view.update_status.connect(self.status_updated)

        # center the dialog over the FreeCAD window if it exists
        self.dialog.resize(w, h)
        if fci.FreeCADGui:
            mw = fci.FreeCADGui.getMainWindow()
            self.dialog.move(
                mw.frameGeometry().topLeft() + mw.rect().center() - self.dialog.rect().center()
            )

        # begin populating the table in a set of sub-threads
        self.startup()

        # rock 'n roll!!!
        self.dialog.exec()

    def cleanup_workers(self) -> None:
        """Ensure that no workers are running by explicitly asking them to stop and waiting for
        them until they do"""
        for worker in self.workers:
            if hasattr(self, worker):
                thread = getattr(self, worker)
                if thread:
                    if not thread.isFinished():
                        thread.blockSignals(True)
                        thread.requestInterruption()
        for worker in self.workers:
            if hasattr(self, worker):
                thread = getattr(self, worker)
                if thread:
                    if not thread.isFinished():
                        finished = thread.wait(500)
                        if not finished:
                            fci.Console.PrintWarning(
                                translate(
                                    "AddonsInstaller",
                                    "Worker process {} is taking a long time to stop…",
                                ).format(worker)
                                + "\n"
                            )

    def accept(self) -> None:
        self.finished.emit()

    def reject(self) -> None:
        """called when the window has been closed"""

        # save window geometry for next use
        fci.Preferences().set("WindowWidth", self.dialog.width())
        fci.Preferences().set("WindowHeight", self.dialog.height())

        # ensure all threads are finished before closing
        ok_to_close = True
        self.startup_sequence = []
        for worker in self.workers:
            if hasattr(self, worker):
                thread = getattr(self, worker)
                if thread:
                    if not thread.isFinished():
                        thread.blockSignals(True)
                        thread.requestInterruption()
                        ok_to_close = False
        while not ok_to_close:
            ok_to_close = True
            for worker in self.workers:
                if hasattr(self, worker):
                    thread = getattr(self, worker)
                    if thread:
                        thread.wait(25)
                        if not thread.isFinished():
                            ok_to_close = False
            QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.AllEvents)

        if self.restart_required:
            # display restart dialog
            m = QtWidgets.QMessageBox()
            m.setWindowTitle(translate("AddonsInstaller", "Addon Manager"))
            icon_path = os.path.join(os.path.dirname(__file__), "Resources", "icons")
            m.setWindowIcon(QtGui.QIcon(os.path.join(icon_path, "addon_manager.svg")))
            m.setText(
                translate(
                    "AddonsInstaller",
                    "You must restart FreeCAD for changes to take effect.",
                )
            )
            m.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            m.setStandardButtons(
                QtWidgets.QMessageBox.StandardButton.Ok
                | QtWidgets.QMessageBox.StandardButton.Cancel
            )
            m.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Cancel)
            ok_btn = m.button(QtWidgets.QMessageBox.StandardButton.Ok)
            cancel_btn = m.button(QtWidgets.QMessageBox.StandardButton.Cancel)
            ok_btn.setText(translate("AddonsInstaller", "Restart now"))
            cancel_btn.setText(translate("AddonsInstaller", "Restart later"))
            ret = m.exec_()
            if ret == QtWidgets.QMessageBox.StandardButton.Ok:
                # restart FreeCAD after a delay to give time to this dialog to close
                QtCore.QTimer.singleShot(1000, utils.restart_freecad)

        self.finished.emit()

    def startup(self) -> None:
        """Downloads the available packages listings and populates the table"""

        # Each function in this list is expected to launch a thread and connect its completion
        # signal to self.do_next_startup_phase, or to shortcut to calling
        # self.do_next_startup_phase if it is not launching a worker
        self.startup_sequence = [
            self.populate_packages_table,
            self.activate_table_widgets,
            self.check_updates,
            self.check_python_updates,
            self.fetch_addon_stats,
            self.fetch_addon_score,
            self.select_addon,
        ]
        self.number_of_progress_regions = len(self.startup_sequence)
        self.current_progress_region = 0
        self.do_next_startup_phase()

    def do_next_startup_phase(self) -> None:
        """Pop the top item in self.startup_sequence off the list and run it"""

        if len(self.startup_sequence) > 0:
            phase_runner = self.startup_sequence.pop(0)
            self.current_progress_region += 1
            phase_runner()
        else:
            self.hide_progress_widgets()
            self.composite_view.package_list.item_filter.invalidateFilter()

    def populate_packages_table(self) -> None:
        self.item_model.clear()

        self.create_addon_list_worker = CreateAddonListWorker()
        self.create_addon_list_worker.addon_repo.connect(self.add_addon_repo)
        self.update_progress_bar(translate("AddonsInstaller", "Creating addon list"), 10, 100)
        self.create_addon_list_worker.finished.connect(self.do_next_startup_phase)  # Link to step 2
        self.create_addon_list_worker.start()

    def activate_table_widgets(self) -> None:
        self.composite_view.package_list.setEnabled(True)
        self.composite_view.package_list.ui.view_bar.search.setFocus()
        self.do_next_startup_phase()

    def on_package_updated(self, repo: Addon) -> None:
        """Called when the named package has either new metadata or a new icon (or both)"""

        with self.lock:
            repo.icon = get_icon(repo, update=True)
            self.item_model.reload_item(repo)

    def select_addon(self) -> None:
        prefs = fci.Preferences()
        selection = prefs.get("SelectedAddon")
        if selection:
            self.composite_view.package_list.select_addon(selection)
            prefs.set("SelectedAddon", "")
        self.do_next_startup_phase()

    def check_updates(self) -> None:
        """checks every installed addon for available updates"""

        autocheck = fci.Preferences().get("AutoCheck")
        if not autocheck:
            fci.Console.PrintLog(
                "Addon Manager: Skipping update check because AutoCheck user preference is False\n"
            )
            self.do_next_startup_phase()
            return
        if not self.packages_with_updates:
            self.force_check_updates(standalone=False)
        else:
            self.do_next_startup_phase()

    def force_check_updates(self, standalone=False) -> None:
        if hasattr(self, "check_worker"):
            thread = self.check_worker
            if thread:
                if not thread.isFinished():
                    self.do_next_startup_phase()
                    return

        self.button_bar.update_all_addons.setText(
            translate("AddonsInstaller", "Checking for updates…")
        )
        self.packages_with_updates.clear()
        self.button_bar.update_all_addons.show()
        self.button_bar.check_for_updates.setDisabled(True)
        self.check_worker = CheckWorkbenchesForUpdatesWorker(self.item_model.repos)
        self.check_worker.finished.connect(self.do_next_startup_phase)
        self.check_worker.finished.connect(self.update_check_complete)
        self.check_worker.progress_made.connect(self.update_progress_bar)
        if standalone:
            self.current_progress_region = 1
            self.number_of_progress_regions = 1
        self.check_worker.update_status.connect(self.status_updated)
        self.check_worker.start()
        self.enable_updates(len(self.packages_with_updates))

    def status_updated(self, repo: Addon) -> None:
        self.item_model.reload_item(repo)
        if repo.status() == Addon.Status.UPDATE_AVAILABLE:
            self.packages_with_updates.add(repo)
            self.enable_updates(len(self.packages_with_updates))
        elif repo.status() == Addon.Status.PENDING_RESTART:
            self.restart_required = True

    def enable_updates(self, number_of_updates: int) -> None:
        """enables the update button"""

        if number_of_updates:
            self.button_bar.set_number_of_available_updates(number_of_updates)
        elif (
            hasattr(self, "check_worker")
            and self.check_worker is not None
            and self.check_worker.isRunning()
        ):
            self.button_bar.update_all_addons.setText(
                translate("AddonsInstaller", "Checking for updates…")
            )
        else:
            self.button_bar.set_number_of_available_updates(0)

    def update_check_complete(self) -> None:
        self.enable_updates(len(self.packages_with_updates))
        self.button_bar.check_for_updates.setEnabled(True)

    def check_python_updates(self) -> None:
        PythonPackageManager.migrate_old_am_installations()  # Migrate 0.20 to 0.21
        self.do_next_startup_phase()

    def show_python_updates_dialog(self) -> None:
        if not self.manage_python_packages_dialog:
            self.manage_python_packages_dialog = PythonPackageManager(self.item_model.repos)
        self.manage_python_packages_dialog.show()

    def fetch_addon_stats(self) -> None:
        """Fetch the Addon Stats JSON data from a URL"""
        url = fci.Preferences().get("AddonsStatsURL")
        if url and url != "NONE":
            self.get_basic_addon_stats_worker = GetBasicAddonStatsWorker(
                url, self.item_model.repos, self.dialog
            )
            self.get_basic_addon_stats_worker.finished.connect(self.do_next_startup_phase)
            self.get_basic_addon_stats_worker.update_addon_stats.connect(self.update_addon_stats)
            self.get_basic_addon_stats_worker.start()
        else:
            self.do_next_startup_phase()

    def update_addon_stats(self, addon: Addon):
        self.item_model.reload_item(addon)

    def fetch_addon_score(self) -> None:
        """Fetch the Addon score JSON data from a URL"""
        prefs = fci.Preferences()
        url = prefs.get("AddonsScoreURL")
        if url and url != "NONE":
            self.get_addon_score_worker = GetAddonScoreWorker(
                url, self.item_model.repos, self.dialog
            )
            self.get_addon_score_worker.finished.connect(self.score_fetched_successfully)
            self.get_addon_score_worker.finished.connect(self.do_next_startup_phase)
            self.get_addon_score_worker.update_addon_score.connect(self.update_addon_score)
            self.get_addon_score_worker.start()
        else:
            self.composite_view.package_list.ui.view_bar.set_rankings_available(False)
            self.do_next_startup_phase()

    def update_addon_score(self, addon: Addon):
        self.item_model.reload_item(addon)

    def score_fetched_successfully(self):
        self.composite_view.package_list.ui.view_bar.set_rankings_available(True)

    def show_developer_tools(self) -> None:
        """Display the developer tools dialog"""
        if not self.developer_mode:
            self.developer_mode = DeveloperMode()
        self.developer_mode.show()

        checker = MetadataValidators()
        checker.validate_all(self.item_model.repos)

    def add_addon_repo(self, addon_repo: Addon) -> None:
        """adds a workbench to the list"""

        if addon_repo.icon is None or addon_repo.icon.isNull():
            addon_repo.icon = get_icon(addon_repo)
        for repo in self.item_model.repos:
            if repo.name == addon_repo.name:
                # self.item_model.reload_item(repo) # If we want to have later additions superseded
                # earlier
                return
        self.item_model.append_item(addon_repo)

    def append_to_repos_list(self, repo: Addon) -> None:
        """this function allows threads to update the main list of workbenches"""
        self.item_model.append_item(repo)

    def update(self, repo: Addon) -> None:
        self.launch_installer_gui(repo)

    def mark_repo_update_available(self, repo: Addon, available: bool) -> None:
        if available:
            repo.set_status(Addon.Status.UPDATE_AVAILABLE)
        else:
            repo.set_status(Addon.Status.NO_UPDATE_AVAILABLE)
        self.item_model.reload_item(repo)
        self.composite_view.package_details_controller.show_repo(repo)

    def launch_installer_gui(self, addon: Addon) -> None:
        if self.installer_gui is not None:
            fci.Console.PrintError(
                translate(
                    "AddonsInstaller",
                    "Cannot launch a new installer until the previous one has finished.",
                )
            )
            return
        if addon.macro is not None:
            self.installer_gui = MacroInstallerGUI(addon)
        else:
            self.installer_gui = AddonInstallerGUI(addon, self.item_model.repos)
        self.installer_gui.success.connect(self.on_package_status_changed)
        self.installer_gui.finished.connect(self.cleanup_installer)
        self.installer_gui.run()  # Does not block

    def cleanup_installer(self) -> None:
        QtCore.QTimer.singleShot(500, self.no_really_clean_up_the_installer)

    def no_really_clean_up_the_installer(self) -> None:
        self.installer_gui = None

    def update_all(self) -> None:
        """Asynchronously apply all available updates: individual failures are noted, but do not
        stop other updates"""

        if self.installer_gui is not None:
            fci.Console.PrintError(
                translate(
                    "AddonsInstaller",
                    "Cannot launch a new installer until the previous one has finished.",
                )
            )
            return

        self.installer_gui = UpdateAllGUI(self.item_model.repos)
        self.installer_gui.addon_updated.connect(self.on_package_status_changed)
        self.installer_gui.finished.connect(self.cleanup_installer)
        self.installer_gui.run()  # Does not block

    def hide_progress_widgets(self) -> None:
        """hides the progress bar and related widgets"""
        self.composite_view.package_list.set_loading(False)

    def show_progress_widgets(self) -> None:
        self.composite_view.package_list.set_loading(True)

    def update_progress_bar(self, message: str, current_value: int, max_value: int) -> None:
        """Update the progress bar, showing it if it's hidden"""

        max_value = max_value if max_value > 0 else 1

        if current_value < 0:
            current_value = 0
        elif current_value > max_value:
            current_value = max_value

        self.show_progress_widgets()

        progress = Progress(
            status_text=message,
            number_of_tasks=self.number_of_progress_regions,
            current_task=self.current_progress_region - 1,
            current_task_progress=current_value / max_value,
        )
        self.composite_view.package_list.update_loading_progress(progress)

    def stop_update(self) -> None:
        self.cleanup_workers()
        self.hide_progress_widgets()

    def on_package_status_changed(self, repo: Addon) -> None:
        if repo.status() == Addon.Status.PENDING_RESTART:
            self.restart_required = True
        self.item_model.reload_item(repo)
        self.composite_view.package_details_controller.show_repo(repo)
        if repo in self.packages_with_updates:
            self.packages_with_updates.remove(repo)
            self.enable_updates(len(self.packages_with_updates))

    def execute_macro(self, repo: Addon) -> None:
        """executes a selected macro"""

        if not fci.FreeCADGui:
            fci.Console.PrintError("Cannot execute a FreeCAD Macro outside FreeCAD")
            return

        macro = repo.macro
        if not macro or not macro.code:
            return

        if macro.is_installed():
            macro_path = os.path.join(fci.DataPaths().macro_dir, macro.filename)
            fci.FreeCADGui.open(str(macro_path))
            self.dialog.hide()
            fci.FreeCADGui.SendMsgToActiveView("Run")
        else:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_install_succeeded = macro.install(temp_dir)
                if not temp_install_succeeded:
                    fci.Console.PrintError(
                        translate("AddonsInstaller", "Temporary installation of macro failed.")
                    )
                    return
                macro_path = os.path.join(temp_dir, macro.filename)
                fci.FreeCADGui.open(str(macro_path))
                self.dialog.hide()
                fci.FreeCADGui.SendMsgToActiveView("Run")

    def remove(self, addon: Addon) -> None:
        """Remove this addon."""
        if self.installer_gui is not None:
            fci.Console.PrintError(
                translate(
                    "AddonsInstaller",
                    "Cannot launch a new installer until the previous one has finished.",
                )
            )
            return
        self.installer_gui = AddonUninstallerGUI(addon)
        self.installer_gui.finished.connect(self.cleanup_installer)
        self.installer_gui.finished.connect(
            functools.partial(self.on_package_status_changed, addon)
        )
        self.installer_gui.run()  # Does not block

    @staticmethod
    def open_addons_folder():
        addons_folder = fci.DataPaths().mod_dir
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(addons_folder))
        return


# @}
