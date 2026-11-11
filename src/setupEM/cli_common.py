########################################################################
#
# Copyright 2025 Volker Muehlhaus and IHP PDK Authors
#
# Licensed under the GNU General Public License, Version 3.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.gnu.org/licenses/gpl-3.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
########################################################################

"""
Command line helpers shared by setupEM, setupThermal, stackupEditor, resultViewer and fieldViewer.

No Qt in here: the commands read their arguments with these before they create the QApplication, so that
--help, --version and a wrong argument need no display.
"""

import json
import os
import re

# file suffix of a settings file -> the application it belongs to
CONFIG_APPS = {"simcfg": "setupEM", "tsimcfg": "setupThermal"}


def package_version():
    """__version__ of the setupEM package, also when a module is run as a plain script."""
    try:
        from . import __version__
        return __version__
    except ImportError:
        init = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
        with open(init, encoding="utf-8") as f:
            m = re.search(r'^__version__\s*=\s*"([^"]+)"', f.read(), re.M)
        return m.group(1) if m else "unknown"


def add_version_argument(parser):
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")


def config_file_error(path, app_name):
    """Why the settings file or model script given at start cannot be opened by app_name; None if it can."""
    if not os.path.isfile(path):
        return f"file not found: {path}"
    suffix = os.path.splitext(path)[1].lower().lstrip(".")
    if suffix == "py":
        return None
    own_suffix = next(s for s, app in CONFIG_APPS.items() if app == app_name)
    owner = CONFIG_APPS.get(suffix)
    if owner is not None and owner != app_name:
        return f"{path} is a {owner} settings file, open it with {owner}"
    if suffix != own_suffix:
        return f"{path} is not a {app_name} settings file (*.{own_suffix}) or a model script (*.py)"
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return f"{path} cannot be read: {e}"
    application = data.get("application", "") if isinstance(data, dict) else ""
    if application == app_name:
        return None
    if application in CONFIG_APPS.values():
        return f"{path} is a {application} settings file, open it with {application}"
    return f"{path} is not a {app_name} settings file (no \"application\": \"{app_name}\" in it)"


def start_file_errors(app_name, simcfg="", gdsfile="", xmlfile=""):
    """The messages for the files given at start that cannot be opened ('' = not given)."""
    errors = []
    if simcfg:
        error = config_file_error(simcfg, app_name)
        if error:
            errors.append(error)
    for path in (gdsfile, xmlfile):
        if path and not os.path.isfile(path):
            errors.append(f"file not found: {path}")
    return errors
