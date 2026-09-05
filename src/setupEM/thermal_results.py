########################################################################
#
# Copyright 2025-2026 Volker Muehlhaus and IHP PDK Authors
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

"""Parse Elmer thermal solver output (thermal_results.dat / thermal_results.vtu)
into a human-readable summary for the setupThermal "Create Model" log. No Qt
dependency, so this can be exercised standalone against real Elmer output
directories.
"""

import os
import glob

import meshio

# Elmer's Post File in util_elmer.py is written with "Coordinate Scaling = Real 1e-6"
# applied on read-in (mesh coordinates in um are scaled down to meters for the solve),
# so node coordinates recovered from the .vtu are in meters and need to be scaled back
# up to um to match the layout's native units.
_METERS_TO_UM = 1e6


def _candidate_dirs(run_path):
    # util_elmer.py writes "Filename = ../thermal_results.dat" and
    # "Post File = ../thermal_results.vtu", which reads as "one level above run_path"
    # -- but Elmer actually resolves both relative to the Mesh DB directory ("mesh"
    # under run_path, per "Mesh DB \"mesh\" \".\"" in the .sif), so "../" cancels back
    # out to run_path itself (confirmed via thermal_results.dat.names: "Metadata for
    # SaveScalars file: mesh/../thermal_results.dat"). run_path is therefore the real
    # location; its parent is kept as a defensive fallback for other Elmer builds/versions.
    run_path = os.path.normpath(run_path)
    return [run_path, os.path.dirname(run_path)]


def find_thermal_dat(run_path):
    """Return the path to thermal_results.dat, or None if it doesn't exist yet."""
    for d in _candidate_dirs(run_path):
        path = os.path.join(d, "thermal_results.dat")
        if os.path.isfile(path):
            return path
    return None


def find_thermal_vtu(run_path):
    """Return the path to the thermal results .vtu file, or None if it doesn't exist yet.

    Elmer's Post File writer appends a timestep suffix (e.g. thermal_results_t0001.vtu)
    even for a single steady-state solve, so this globs rather than matching a fixed name;
    if more than one is found (re-run, or a multi-iteration solve), the most recently
    written one wins.
    """
    matches = []
    for d in _candidate_dirs(run_path):
        matches += glob.glob(os.path.join(d, "thermal_results*.vtu"))
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


def read_minmax_temperature(run_path):
    """Return (t_min, t_max) from thermal_results.dat, or None if missing/unparsable."""
    path = find_thermal_dat(run_path)
    if path is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        if not lines:
            return None
        # SaveScalars writes one row per solver call; steady-state thermal is a single
        # solve, but take the last line defensively in case Elmer ever appends more.
        values = [float(v) for v in lines[-1].split()]
        if len(values) < 2:
            return None
        # Column order is fixed by our own .sif generation: Operator 1 = min, Operator 2 = max.
        return values[0], values[1]
    except (OSError, ValueError):
        return None


def find_max_temperature_location(run_path):
    """Return (x, y, z) in um of the mesh node with the highest temperature, or None
    if the thermal results .vtu doesn't exist yet or can't be read/parsed.
    """
    path = find_thermal_vtu(run_path)
    if path is None:
        return None
    try:
        mesh = meshio.read(path)
        temp_key = next(
            (key for key in mesh.point_data if "temp" in key.lower()),
            None,
        )
        if temp_key is None:
            return None
        temperature = mesh.point_data[temp_key]
        max_index = temperature.argmax()
        x, y, z = mesh.points[max_index]
        return x * _METERS_TO_UM, y * _METERS_TO_UM, z * _METERS_TO_UM
    except Exception:
        # Any read/parse failure (unsupported VTU variant, half-written file while a
        # simulation is still running, etc.) should never block the min/max summary.
        return None


_SOURCE_TABLE_HEADERS = ["Type", "GDS Layer", "Target Layer", "Value"]


def format_source_table(rows):
    """Format (type, gds_layer, target_layer, value) string rows into an aligned table,
    same layout style as palace_results.py's AMR results table. The caller decides row
    order (heat sources first, then constant-temperature boundaries, per setupThermal.py).
    """
    headers = _SOURCE_TABLE_HEADERS
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]

    def fmt_row(cells):
        return " | ".join(
            cell.rjust(widths[i]) if i == len(cells) - 1 else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        )

    lines = [fmt_row(headers), "-+-".join("-" * w for w in widths)]
    lines += [fmt_row(row) for row in rows]
    return lines


def build_thermal_summary(run_path, source_lines=None):
    """Return a formatted multi-line summary of Elmer thermal results found relative
    to run_path (the case.sif working directory), or an explanatory message if
    nothing is there yet.

    source_lines, if given, is a list of pre-formatted strings describing the model's
    heat sources / constant-temperature boundaries (e.g. str(heatsource_instance)) --
    this module only knows about Elmer's output files, not the GUI's in-memory model,
    so the caller supplies these rather than this module importing simulation_setup.
    """
    minmax = read_minmax_temperature(run_path)
    if minmax is None:
        return (f"No thermal results found yet (expected thermal_results.dat under {run_path}) "
                 "-- has the simulation finished?")
    t_min, t_max = minmax

    location = find_max_temperature_location(run_path)
    if location is not None:
        x, y, z = location
        location_line = f"Hotspot location  : x={x:.3f} um  y={y:.3f} um  z={z:.3f} um"
    else:
        location_line = "Hotspot location  : n/a (see thermal_results.vtu)"

    lines = ["=== Thermal results ==="]
    if source_lines:
        lines.extend(source_lines)
        lines.append("-" * 40)
    lines += [
        f"Min temperature   : {t_min:.2f} K",
        f"Max temperature   : {t_max:.2f} K",
        location_line,
        "=" * 40,
    ]
    return "\n".join(lines)
