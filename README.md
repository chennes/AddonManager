# FreeCAD Addon Manager

This module was originally developed within FreeCAD, and has now been extracted into its own git repository. It is re-integrated into
FreeCAD's source tree as a git submodule.

## Roadmap

This module is under active development, with the following rough plan

1. Complete migration to using the FreeCAD wrapper class to allow running outside of FreeCAD.
   1. Split `addonmanager_utilities.py` along GUI/No-GUI lines
   2. Refactor tests into pure GUI/No-GUI lines
   3. Verify that all `FreeCAD.*` and `FreeCADGui.*` calls are wrapped
2. Update GitHub CI to run Addon Manager test suite.
3. Migrate to a JSON-formatted addon repository list.
4. Rearrange codebase to better separate GUI from logic code.
5. Begin GUI redesign.

PRs are welcome!
