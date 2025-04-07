# FreeCAD Addon Manager

This module was originally developed within FreeCAD, and has now been extracted into its own git repository. It is re-integrated into
FreeCAD's source tree as a git submodule.

## Roadmap

This module is under active development, with the following rough plan

1. Make modifications necessary for treating the Addon Manager as an addon itself, allowing self-update.
2. Complete migration to using the FreeCAD wrapper class to allow running outside of FreeCAD.
3. Update GitHub CI to run Addon Manager test suite.
4. Migrate to a JSON-formatted addon repository list.
5. Rearrange codebase to better separate GUI from logic code.
6. Begin GUI redesign.

PRs are welcome!
