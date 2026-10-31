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
import os

__all__ = ["LAYOUT_KEYS", "port_differences", "layout_differences", "newer_configs", "newer_scripts"]

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


def newer_scripts(config_path, gds_path, folders):
    """Model scripts (*.py) in folders that read gds_path and are newer than config_path, newest first: they hold
    settings made after the config was saved (setupEM's Create Model writes the script, not the config)."""
    try:
        config_time = os.path.getmtime(config_path)
    except OSError:
        return []
    seen, out = set(), []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for path in glob.glob(os.path.join(folder, "*.py")):
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            try:
                if os.path.getmtime(path) <= config_time:
                    continue
            except OSError:
                continue
            if _reads_gds(path, gds_path):
                out.append(path)
    out.sort(key=os.path.getmtime, reverse=True)
    return out
