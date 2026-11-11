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

"""Update a script edited in place from a settings file (*.simcfg) of the same layout.

A layout tool such as the KLayout plug-in klayout_em_export writes the GDS file and a *.simcfg next to it. When the
layout changes, it exports again: same GDS file name, new ports (numbered 1..N without gaps, so they can shift).
A model script made before, maybe edited by hand, still has the old ports. setupEM's edit-in-place mode can update
it: the settings that belong to the layout (LAYOUT_KEYS and the ports) come from the *.simcfg, everything else
(frequencies, mesh, the script's own code) stays.

No Qt in here, so the module can be tested on its own.
"""

import ast
import glob
import json
import math
import os

__all__ = ["LAYOUT_KEYS", "port_differences", "layout_differences", "newer_configs", "model_scripts",
           "same_path", "linked_scripts", "model_settings_snapshot", "settings_differences"]

# saved_values that belong to the layout export, besides the ports
LAYOUT_KEYS = ("GdsFile", "SubstrateFile", "cellname", "purpose")
LAYOUT_LABELS = {"GdsFile": "GDSII file", "SubstrateFile": "stackup", "cellname": "cell", "purpose": "purpose"}

_PORT_KEYS = ("source_layernum", "direction", "from_layername", "to_layername", "target_layername")


def _port_signature(port):
    """(source layer, direction, from, to, target) of a port dict; Z0 and excitation are not part of it."""
    values = []
    for key in _PORT_KEYS:
        value = port.get(key)
        if key == "source_layernum" and value not in (None, ""):
            value = int(value)
        elif key == "direction" and isinstance(value, str):
            value = value.upper()
        values.append(value or None)
    layer, direction, frm, to, target = values
    if direction and "Z" in direction:
        target = None
    else:
        frm = to = None
    return layer, direction, frm, to, target


def _describe(signature):
    layer, direction, frm, to, target = signature
    if direction and "Z" in direction:
        return f"via {frm} -> {to} ({direction}) on layer {layer}"
    return f"in-plane {direction} on {target}, layer {layer}"


def port_differences(old_ports, new_ports):
    """Readable differences from old_ports to new_ports (lists of port dicts), [] if they match. Compared per port
    number: layer, direction, from/to/target layer; not Z0 or excitation."""
    old = {int(p["portnumber"]): _port_signature(p) for p in old_ports if p.get("portnumber") not in (None, "")}
    new = {int(p["portnumber"]): _port_signature(p) for p in new_ports if p.get("portnumber") not in (None, "")}
    out = []
    for number in sorted(set(old) | set(new)):
        if number not in new:
            out.append(f"port {number}: removed")
        elif number not in old:
            out.append(f"port {number}: new, {_describe(new[number])}")
        elif old[number] != new[number]:
            out.append(f"port {number}: was {_describe(old[number])}, now {_describe(new[number])}")
    return out


def same_path(a, b, base_dir=""):
    """True if the paths a and b name the same file; relative paths are taken from base_dir."""
    return _same_path(a, b, base_dir)


def _same_path(a, b, base_dir=""):
    if not a or not b:
        return a == b
    norm = lambda p: os.path.normcase(os.path.abspath(os.path.join(base_dir, p)))
    return norm(a) == norm(b)


def layout_differences(old_values, new_values, new_base_dir=""):
    """Readable differences of the LAYOUT_KEYS from old_values to new_values (saved_values dicts); file paths in
    new_values relative to new_base_dir."""
    out = []
    for key in LAYOUT_KEYS:
        if key not in new_values:
            continue
        old, new = old_values.get(key), new_values.get(key)
        if key in ("GdsFile", "SubstrateFile"):
            if not _same_path(old, new, new_base_dir):
                out.append(f"{LAYOUT_LABELS[key]}: {os.path.basename(old or '-')} -> {os.path.basename(new or '-')}")
        elif key == "purpose":
            if [int(p) for p in old or []] != [int(p) for p in new or []]:
                out.append(f"{LAYOUT_LABELS[key]}: {old} -> {new}")
        elif old != new:
            out.append(f"{LAYOUT_LABELS[key]}: {old} -> {new}")
    return out


def newer_configs(script_path, gds_path, folders, suffix=".simcfg", application="setupEM"):
    """[(path, data)] of the settings files in folders that belong to gds_path (their GdsFile is that file) and are
    newer than script_path, newest first."""
    try:
        script_time = os.path.getmtime(script_path)
    except OSError:
        return []
    seen, out = set(), []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for path in glob.glob(os.path.join(folder, "*" + suffix)):
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            try:
                if os.path.getmtime(path) <= script_time:
                    continue
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("application") != application:
                continue
            gds = (data.get("saved_values") or {}).get("GdsFile")
            if gds and _same_path(gds, gds_path, os.path.dirname(path)):
                out.append((path, data))
    out.sort(key=lambda item: os.path.getmtime(item[0]), reverse=True)
    return out


def _reads_gds(script_path, gds_path):
    """True if one of the script's string literals, taken relative to the script's folder, is gds_path (the script
    is read with ast, never run)."""
    try:
        with open(script_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    if os.path.basename(gds_path) not in text:          # cheap test before parsing
        return False
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    folder = os.path.dirname(os.path.abspath(script_path))
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.lower().endswith(".gds")
               and _same_path(n.value, gds_path, folder) for n in ast.walk(tree))


def model_scripts(gds_path, folders):
    """Model scripts (*.py) in folders that read gds_path, newest first. Their age says nothing against a settings
    file of the same layout: a layout tool writes that file again on every export, also after the script was made.
    The script is the model; Create Model also writes the settings to <model>.simcfg, but another settings file of
    the layout (or one saved before) does not have the settings made in setupEM since."""
    seen, out = set(), []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for path in glob.glob(os.path.join(folder, "*.py")):
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            if _reads_gds(path, gds_path):
                out.append(path)
    out.sort(key=os.path.getmtime, reverse=True)
    return out


# ---------- Which model script a settings file belongs to ----------
#
# A layout tool's export (e.g. <layout>.simcfg of the KLayout plug-in) and the model script made from it in setupEM
# are linked in the settings file:
#   "written_with": "<model>.py"   Create Model wrote this settings file together with the script (same folder)
#   "model_script": "<path>.py"    Create Model wrote the script for a model opened from this settings file under
#                                  another name; the path is relative to the settings file's folder
# The layout tool keeps both keys when it writes the file again. A script that only reads the same GDS file is not
# linked: it can be any script (an older copy, a hand-written one).

# settings that are not part of the model's setup, or that the GUI takes from the preferences either way
SETTINGS_NOT_COMPARED = ("unit", "model_basename", "sim_path", "preview_only", "no_preview",
                         "solver_maxits", "solver_tol", "fdump_enabled")


def linked_scripts(config_path, data):
    """[(script path, written_with)] of the existing model scripts the settings file names, written_with first."""
    folder = os.path.dirname(os.path.abspath(config_path))
    out = []
    for key in ("written_with", "model_script"):
        name = data.get(key) if isinstance(data, dict) else None
        if not isinstance(name, str) or not name.lower().endswith(".py"):
            continue
        if key == "written_with" and os.path.basename(name) != name:
            continue                                    # written_with is a file name in the same folder
        path = os.path.normpath(os.path.join(folder, name))
        if os.path.isfile(path) and not any(_same_path(path, p) for p, _ in out):
            out.append((path, key == "written_with"))
    return out


def model_settings_snapshot(script_path, suffix=".simcfg", application="setupEM"):
    """The settings Create Model wrote with the script (<model>.simcfg, "written_with" the script), if the script was
    not changed after it; else None (then they say nothing about the script)."""
    path = os.path.splitext(script_path)[0] + suffix
    try:
        if os.path.getmtime(path) < os.path.getmtime(script_path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("application") != application or \
            data.get("written_with") != os.path.basename(script_path):
        return None
    return data


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same_setting(a, b):
    """Two values of a setting as saved in settings files: numbers to rounding, a list and a scalar alike if every
    element is the scalar (the oversize, one value or one per side), lists and dicts element by element."""
    if _is_number(a) and _is_number(b):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-15)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same_setting(x, y) for x, y in zip(a, b))
    if isinstance(a, (list, tuple)) and _is_number(b):
        return len(a) > 0 and all(_same_setting(x, b) for x in a)
    if _is_number(a) and isinstance(b, (list, tuple)):
        return _same_setting(b, a)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_setting(a[k], b[k]) for k in a)
    return a == b


def _show(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value)) if abs(value) < 1e15 else str(value)
    return str(value)


def settings_differences(script_data, config_data):
    """Readable differences between the settings of a model script (script_data: the settings file Create Model
    wrote with it) and a settings file of the same layout (config_data), [] if none. Compared: the settings both
    files have, except the layout settings and ports (they come from the settings file either way) and
    SETTINGS_NOT_COMPARED; and the solver (Palace or Elmer)."""
    script_values = script_data.get("saved_values") or {}
    config_values = config_data.get("saved_values") or {}
    out = []
    if "elmer_mode" in config_data and \
            bool(script_data.get("elmer_mode", False)) != bool(config_data.get("elmer_mode", False)):
        solver = lambda data: "Elmer" if data.get("elmer_mode") else "Palace"
        out.append(f"solver: {solver(script_data)} in the script, {solver(config_data)} in the config")
    for key in sorted(config_values):
        if key in LAYOUT_KEYS or key in SETTINGS_NOT_COMPARED or key not in script_values:
            continue
        if not _same_setting(script_values[key], config_values[key]):
            out.append(f"{key}: {_show(script_values[key])} in the script, {_show(config_values[key])} in the config")
    return out

