# SPDX-License-Identifier: LGPL-2.1-or-later

# InitGui.py is run automatically by FreeCAD during the GUI initialization process

import os
import AddonManager

if __file__:
    FreeCADGui.addLanguagePath(os.path.join(os.path.dirname(__file__), "Resources", "translations"))
else:
    FreeCAD.Console.Warning("__file__ not defined, cannot set language path\n")

FreeCADGui.addCommand("Std_AddonMgr", AddonManager.CommandAddonManager())
