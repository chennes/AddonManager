import sys
import unittest

from PySideWrapper import QtCore, QtWidgets


from addonmanager_python_deps_gui import PythonPackageManagerGui


class TestPythonPackageManagerGui(unittest.TestCase):

    def setUp(self) -> None:
        self.manager = PythonPackageManagerGui([])


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    QtCore.QTimer.singleShot(0, unittest.main)
    app.exec()
