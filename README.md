# FreeCAD Addon Manager

Install and update third-party addons to FreeCAD, including Workbenches, Macros, Preference Packs, and more. Install
*this* addon to update the internal Addon Manager to the latest version (and to allow future self-updating).

This module was originally developed within FreeCAD, and has now been extracted into its own git repository. It is
currently re-integrated into FreeCAD's source tree as a git submodule and continues to ship with FreeCAD.

## Addon Sources

The main source of addons is the git repository at https://github.com/FreeCAD/FreeCAD-Addons. Custom addon sources can
be configured in the Addon Manager preferences when running FreeCAD. These addons are primarily written by
third parties and provided by repositories not under the FreeCAD authors' or maintainers' control: you use these addons
at your own risk.

## Addon Manager Design Goals

The Addon Manager is now designed to be self-updating, with a goal of allowing versions of FreeCAD back to 0.21 to
use their default copy of Addon Manager to install a new version of Addon Manager. This means that the Addon Manager
should support PySide2 and Python 3.8 for the foreseeable future.

The Addon Manager is also designed to be run in a "standalone" mode to allow for easier UI development. In this mode
it does not interact with FreeCAD at all, and does not use or affect "real" FreeCAD preferences, module installation,
etc.

## Roadmap

This module is under active development, with the following rough plan

1. Migrate to a JSON-formatted addon repository catalog, replacing the original `.gitmodules`-based addon source
2. Rearrange codebase to better separate GUI from logic code.
3. Implement detection of changed addon dependencies when updating.
4. Implement remote caching of icons and macros.
5. Begin GUI redesign.

PRs are welcome!
