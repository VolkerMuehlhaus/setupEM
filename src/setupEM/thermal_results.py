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

import csv
import json
import os
import glob
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np
import meshio

# Elmer's Post File in util_elmer.py is written with "Coordinate Scaling = Real 1e-6"
# applied on read-in (mesh coordinates in um are scaled down to meters for the solve),
# so node coordinates recovered from the .vtu are in meters and need to be scaled back
# up to um to match the layout's native units.
_METERS_TO_UM = 1e6

_VTU_DTYPE_MAP = {
    "Float32": "<f4", "Float64": "<f8",
    "Int32": "<i4", "Int64": "<i8",
    "UInt32": "<u4", "UInt64": "<u8", "UInt8": "<u1",
}
_VTU_HEADER_FMT = {"UInt32": ("<I", 4), "UInt64": ("<Q", 8)}


def _read_raw_appended_vtu(path, with_cells=False):
    """Read points + point-data arrays from a VTK XML UnstructuredGrid (.vtu) file
    whose <AppendedData encoding="raw"> block holds literal binary (not base64).

    meshio 5.3.5 (the latest as of writing) always base64-decodes an "appended"
    format DataArray regardless of the declared encoding, so it raises on exactly
    this file shape - confirmed against Elmer 26.2's own Post File writer, which
    uses encoding="raw" by default. ParaView reads these files fine (it's built on
    the full VTK library, not meshio's pure-Python reader), which is why "the file
    looks good in ParaView" but this summary previously showed "n/a" here.

    Only handles what create a hotspot summary needs: the Points array and each
    PointData array, each still shaped format="appended" (Elmer doesn't compress
    or base64-encode its own appended blocks, so no compression/base64 support
    needed here). Raises on anything else - the caller falls back to meshio first,
    and to "no location available" if both fail. with_cells: also the cells
    (connectivity, offsets, VTK types) and the CellData arrays (Elmer: GeometryIds).
    """
    with open(path, "rb") as f:
        raw = f.read()

    marker = raw.index(b"<AppendedData")
    tag_end = raw.index(b">", marker) + 1
    underscore = raw.index(b"_", tag_end)
    # Re-close the header (already-closed elements before <AppendedData> stay as-is;
    # only <AppendedData> itself and the root <VTKFile> are still open at this point)
    # so ElementTree can parse just the structure, ignoring the binary blob entirely.
    header = ET.fromstring(raw[:underscore] + b"</AppendedData></VTKFile>")
    blob = raw[underscore + 1:]

    header_fmt, header_size = _VTU_HEADER_FMT[header.attrib.get("header_type", "UInt32")]

    def read_array(elem):
        offset = int(elem.attrib["offset"])
        n_bytes = struct.unpack_from(header_fmt, blob, offset)[0]
        dtype = _VTU_DTYPE_MAP[elem.attrib["type"]]
        count = n_bytes // np.dtype(dtype).itemsize
        arr = np.frombuffer(blob, dtype=dtype, count=count, offset=offset + header_size)
        num_components = int(elem.attrib.get("NumberOfComponents", "1"))
        return arr.reshape(-1, num_components) if num_components > 1 else arr

    points_elem = header.find(".//Points/DataArray")
    points = read_array(points_elem).reshape(-1, 3)

    point_data = {}
    for data_array in header.findall(".//PointData/DataArray"):
        if data_array.attrib.get("format") == "appended":
            point_data[data_array.attrib["Name"]] = read_array(data_array)

    if not with_cells:
        return points, point_data
    cell_arrays = {e.attrib["Name"]: read_array(e) for e in header.findall(".//Cells/DataArray")}
    cell_data = {e.attrib["Name"]: read_array(e) for e in header.findall(".//CellData/DataArray")
                 if e.attrib.get("format") == "appended"}
    cells = (cell_arrays["connectivity"], cell_arrays["offsets"], cell_arrays["types"])
    return points, point_data, cells, cell_data


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


def find_thermal_paraview_file(run_path):
    """Return the best file to hand to ParaView for the thermal results: a .pvtu
    (written when the solve was split across multiple partitions - it's the index
    that ties the per-partition pieces back into one dataset) if one exists, else
    the plain .vtu from find_thermal_vtu(). Use this for launching ParaView, not
    find_thermal_vtu() directly - a .pvtu has no data arrays of its own, so it
    would break read_minmax_temperature()'s parsing if used there instead.
    """
    matches = []
    for d in _candidate_dirs(run_path):
        matches += glob.glob(os.path.join(d, "thermal_results*.pvtu"))
    if matches:
        return max(matches, key=os.path.getmtime)
    return find_thermal_vtu(run_path)


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
        try:
            mesh = meshio.read(path)
            points, point_data = mesh.points, mesh.point_data
        except Exception:
            # meshio 5.3.5 always base64-decodes an "appended" DataArray regardless of
            # the declared encoding, so it raises on Elmer's own encoding="raw" Post
            # File output (confirmed: ParaView reads these files fine via full VTK -
            # this isn't a corrupt/half-written file). Fall back to a narrow raw-vtu
            # reader for exactly this case before giving up.
            points, point_data = _read_raw_appended_vtu(path)
        temp_key = next(
            (key for key in point_data if "temp" in key.lower()),
            None,
        )
        if temp_key is None:
            return None
        temperature = point_data[temp_key]
        max_index = temperature.argmax()
        x, y, z = points[max_index]
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


# ---------- temperature per heat source / device ----------

_VTK_TETRAHEDRA = (10, 24)      # linear and quadratic tetrahedron; the volume comes from the 4 corner nodes
_SOURCE_PREFIX = "source_"      # gds2palace names the body of a heat source source_<GDS layer>


def read_body_names(run_path):
    """{body id: name} from the Elmer mesh's mesh.names (its bodies section), or {} if there is none."""
    path = os.path.join(run_path, "mesh", "mesh.names")
    names, in_bodies = {}, False
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("!"):
                    in_bodies = "bodies" in line
                elif in_bodies and line.startswith("$") and "=" in line:
                    name, value = line[1:].split("=")
                    names[int(value)] = name.strip()
    except (OSError, ValueError):
        return {}
    return names


def _tetrahedra(path):
    """(points, temperature per point, corner node indices per tetrahedron (n x 4), body id per tetrahedron) of
    an Elmer result .vtu. Elmer's file also holds the boundary elements; only the tetrahedra are volume."""
    try:
        mesh = meshio.read(path)
        points, point_data = mesh.points, mesh.point_data
        blocks = [(block.data[:, :4], ids) for block, ids in zip(mesh.cells, mesh.cell_data.get("GeometryIds", []))
                  if block.type in ("tetra", "tetra10")]
        corners = np.vstack([b for b, _ in blocks]) if blocks else np.zeros((0, 4), int)
        body = np.concatenate([np.asarray(i).ravel() for _, i in blocks]) if blocks else np.zeros(0, int)
    except Exception:
        # Elmer's raw appended .vtu, which meshio cannot read (see _read_raw_appended_vtu)
        points, point_data, (connectivity, offsets, types), cell_data = _read_raw_appended_vtu(path, with_cells=True)
        starts = np.concatenate(([0], offsets[:-1]))
        tetra = np.isin(types, _VTK_TETRAHEDRA)
        corners = connectivity[starts[tetra][:, None] + np.arange(4)]
        body = cell_data["GeometryIds"][tetra]
    temp_key = next(key for key in point_data if "temp" in key.lower())
    return np.asarray(points), np.asarray(point_data[temp_key]).ravel(), corners, np.asarray(body)


def source_temperatures(run_path):
    """{GDS layer of a heat source: (mean, max) temperature in K} from the Elmer result in run_path: the mean is
    the volume-weighted average over the heat source body, the max its hottest node. {} if there is no result."""
    path = find_thermal_vtu(run_path)
    names = read_body_names(run_path)
    if path is None or not names:
        return {}
    points, temperature, corners, body = _tetrahedra(path)
    result = {}
    for body_id, name in names.items():
        if not name.startswith(_SOURCE_PREFIX):
            continue
        tets = corners[body == body_id]
        if len(tets) == 0:
            continue
        p = points[tets]
        volume = np.abs(np.einsum("ij,ij->i", p[:, 1] - p[:, 0], np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 0]))) / 6
        t_cell = temperature[tets].mean(axis=1)
        result[int(name[len(_SOURCE_PREFIX):])] = (float((t_cell * volume).sum() / volume.sum()),
                                                   float(temperature[np.unique(tets)].max()))
    return result


def read_thermal_map(path):
    """The thermal export's map (<layout>_thermalmap.json, with the instance names), or None."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("sources"), list) else None


def device_temperatures(run_path, thermal, thermal_map=None):
    """(rows, reference temperature) per heat source of the Elmer result in run_path, ordered by GDS layer.
    thermal: the model's heat sources and constant temperatures as dicts with type ('source' / 'constanttemp'),
    source_layernum, power or temp, as in a .tsimcfg. thermal_map: the thermal export's map, for the instance
    names (None: none). A row has layer, instance, device, pcell, power_W, T_mean_K, T_max_K, dT_mean_K, dT_max_K;
    dT is relative to the reference temperature, the lowest constant temperature of the model."""
    temperatures = source_temperatures(run_path)
    if not temperatures:
        return [], None
    fixed = [float(t["temp"]) for t in thermal if t.get("type") == "constanttemp" and t.get("temp") is not None]
    t_ref = min(fixed) if fixed else None
    power = {int(t["source_layernum"]): t.get("power") for t in thermal if t.get("type") == "source"}
    devices = {s.get("layer"): s for s in (thermal_map or {}).get("sources", [])}
    rows = []
    for layer in sorted(temperatures):
        t_mean, t_max = temperatures[layer]
        device = devices.get(layer, {})
        rows.append({"layer": layer, "instance": device.get("instance") or "", "device": device.get("device", ""),
                     "pcell": device.get("pcell", ""), "power_W": power.get(layer), "T_mean_K": t_mean,
                     "T_max_K": t_max, "dT_mean_K": None if t_ref is None else t_mean - t_ref,
                     "dT_max_K": None if t_ref is None else t_max - t_ref})
    return rows, t_ref


_TEMPERATURE_COLUMNS = ["instance", "device", "pcell", "layer", "power_W", "T_mean_K", "T_max_K", "dT_mean_K",
                        "dT_max_K"]


def write_temperatures_csv(path, rows, t_ref):
    """Write the rows of device_temperatures() as CSV, after comment lines that say what the columns mean."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("# Temperature per heat source from the Elmer thermal result. T_mean: volume-weighted average over "
                "the heat source, T_max: its hottest mesh node.\n")
        if t_ref is not None:
            f.write(f"# dT: relative to {t_ref:g} K, the lowest constant temperature of the model (for a circuit "
                    "simulator's temperature rise per instance, e.g. trise / dtemp).\n")
        else:
            f.write("# dT: none, the model has no constant temperature.\n")
        writer = csv.writer(f)
        writer.writerow(_TEMPERATURE_COLUMNS)
        for row in rows:
            writer.writerow(["" if row[k] is None else f"{row[k]:.6g}" if isinstance(row[k], float) else row[k]
                             for k in _TEMPERATURE_COLUMNS])


def format_temperature_table(rows):
    """The rows of device_temperatures() as aligned text lines for the log."""
    headers = ["Instance", "Layer", "Power", "T mean", "T max", "dT mean", "dT max"]

    def kelvin(value):
        return "n/a" if value is None else f"{value:.1f} K"

    table = [[row["instance"] or row["device"] or "-", str(row["layer"]),
              "n/a" if row["power_W"] is None else f"{row['power_W']:g} W", kelvin(row["T_mean_K"]),
              kelvin(row["T_max_K"]), kelvin(row["dT_mean_K"]), kelvin(row["dT_max_K"])] for row in rows]
    widths = [max(len(headers[i]), *(len(r[i]) for r in table)) for i in range(len(headers))]

    def fmt_row(cells):
        return " | ".join(cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i]) for i, cell in enumerate(cells))

    return [fmt_row(headers), "-+-".join("-" * w for w in widths)] + [fmt_row(r) for r in table]


def temperatures_from_tsimcfg(tsimcfg_path):
    """(rows, reference temperature, csv path written) for a setupThermal model: its Elmer result in
    <sim_path>/elmer_model/<model>_data, the instance names from the thermal map next to its GDS file; writes
    <sim_path>/<model>_temperatures.csv. Raises ValueError if the file is not a setupThermal model or there is no
    result yet."""
    try:
        with open(tsimcfg_path, encoding="utf-8") as f:
            config = json.load(f)
        values = config["saved_values"]
        sim_path, basename = values["sim_path"], values["model_basename"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"{tsimcfg_path} is not a setupThermal model with a model folder and name: {e}")
    run_path = os.path.join(sim_path, "elmer_model", basename + "_data")
    gds = values.get("GdsFile") or ""
    thermal_map = read_thermal_map(os.path.splitext(gds)[0] + "_thermalmap.json") if gds else None
    rows, t_ref = device_temperatures(run_path, config.get("thermal", []), thermal_map)
    if not rows:
        raise ValueError(f"no thermal result with heat sources in {run_path}: has the simulation run?")
    csv_path = os.path.join(sim_path, basename + "_temperatures.csv")
    write_temperatures_csv(csv_path, rows, t_ref)
    return rows, t_ref, csv_path


def main(argv=None):
    """python -m setupEM.thermal_results <model>.tsimcfg: temperature per heat source, also as a CSV file."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m setupEM.thermal_results <model>.tsimcfg\n"
              "  writes the temperature per heat source of the model's Elmer result to <model>_temperatures.csv "
              "next to the model script, with the instance names of the thermal export")
        return 2
    try:
        rows, t_ref, csv_path = temperatures_from_tsimcfg(argv[0])
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1
    print("\n".join(format_temperature_table(rows)))
    print(f"\nwritten: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
