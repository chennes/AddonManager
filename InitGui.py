# SPDX-License-Identifier: LGPL-2.1-or-later

# InitGui.py is run automatically by FreeCAD during the GUI initialization process by reading the
# file into memory and running `exec` on its contents (so __file__ is not defined directly).

import os
import AddonManager

FreeCADGui.addLanguagePath(
    os.path.join(os.path.dirname(AddonManager.__file__), "Resources", "translations")
)
FreeCADGui.addCommand("Std_AddonMgr", AddonManager.CommandAddonManager())
