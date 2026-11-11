"""A split EM region in setupEM: the pieces of klayout_em_export's Export All, as rows of the Models list.

<export>.simcfg has a "pieces" section (klayout_em_export pieces.py): one model per piece in <export>_pieces/
(p1.simcfg, p2.simcfg, ...) and full.simcfg, the whole region as one model to check the joined result. After the
pieces are simulated, join_pieces connects their results at the cuts.

The settings are those of the whole region, in <export>.simcfg, the same for every piece (frequencies, mesh, solver,
boundaries). Two exceptions:
- the box sides of a piece follow from the region's: a side on a cut is a port wall (PEC if no line crosses it), a
  side on the region's edge has the region's boundary there. A port wall of the region is one for the piece that has
  a port on that edge (found in the piece's layout, so a wall set in setupEM after the export counts too); for a
  piece without a port there, that stretch of the wall is PEC, at the same place as the port wall (wall_extension
  for the region's port_wall_extension), as in the full model;
- a piece may have its own mesh settings (MESH_KEYS, "own_settings" in its .simcfg), e.g. a finer mesh in one
  difficult piece.
Each piece's .simcfg (and its model script) gets the result of both, so that it is a complete model.

Unchanged pieces are not simulated again: a piece's run key is a SHA-256 of what decides its result (the geometry
fingerprint of the export, its settings, the gds2palace version and the version of combine_extend_snp.py), stored
as piece.key in its model folder after a successful run. A piece whose key is unchanged and whose result is there
is reused. The Palace version is not in the key (setupEM can't know it before the run): after a Palace update, tick
"Run unchanged pieces too".
"""
import hashlib
import json
import os
import re
import shutil

if __package__ in (None, ""):
    from join_pieces import _model_place, joined_path, read_set, result_path
else:
    from .join_pieces import _model_place, joined_path, read_set, result_path

FREQUENCY_KEYS = ("fstart", "fstop", "fstep", "fpoint", "fdump")
# settings that don't change a piece's result (where it is, what it is called, how setupEM shows it)
# the fingerprint covers what the piece contains: its file names, folder and the layout's cell name (a new revision
# is often a renamed cell) are no change
NOT_IN_KEY = {"sim_path", "model_basename", "GdsFile", "SubstrateFile", "cellname", "preview_only", "no_preview",
              "unit"}
KEY_FILE = "piece.key"
MESH_KEY_FILE = "mesh.key"      # the run key the piece was last meshed with: Start Simulation skips an unchanged mesh

# the settings a piece may have of its own (the Mesh tab's "Own mesh settings for this piece"); all others are shared
MESH_KEYS = ("refined_cellsize", "refined_cellsize_override", "meshsize_max", "cells_per_wavelength", "order",
             "adaptive_mesh_iterations", "amr_tol", "amr_max_dof", "adaptive_mesh_conformal")
# what is a piece's own in any case: its layout, cell and folder; its box sides follow from the region's
PIECE_KEYS = {"GdsFile", "cellname", "sim_path", "model_basename", "boundary", "margin", "air_around",
              "preview_only", "no_preview", "port_wall_extension", "reference_plane_shift", "wall_extension"}
# per-side port wall settings ({side: value}): the region's, on the sides of a piece that are the region's edge; a cut
# has none (its reference plane must stay on the cut for the join)
SIDE_SETTINGS = ("port_wall_extension", "reference_plane_shift")
# gds2palace's setting for a wall that is not a port wall, moved outward: a piece's stretch of a region port wall
# without a port of the piece, where the region has a port_wall_extension
WALL_EXTENSION = "wall_extension"
OWN_KEY = "own_settings"
WALLS_KEY = "wall_sides"        # the piece's cut sides with port wall ports, as the export recorded them
EDGE_TOL = 1e-3                 # a port on the layout edge: within this distance (gds2palace's tolerance), um
SIDES = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
MARGIN_SIDES = SIDES[:4]
PORTWALL = "PORTWALL"
TOL = 1e-6


def _side_values(value, count, default):
    """A saved per-side value (a list of count values, or one value for all) as a list of count values."""
    if isinstance(value, (list, tuple)) and len(value) == count:
        return list(value)
    if value is None or value == "" or isinstance(value, (list, tuple)):
        return [default] * count
    return [value] * count


def _compact(values):
    """One value when all sides have it, else the list (as setupEM saves them)."""
    return values[0] if len(set(map(str, values))) == 1 else values


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


_edge_cache = {}


def ports_on_edge(gds, cellname, port_layers):
    """The sides of the layout edge (bounding box of the cell) with a port shape on them: {'xmin', ...}. A shape
    counts on a side when it reaches it, as gds2palace finds the ports of a port wall (in-plane ports and via ports
    drawn on the edge). None when the layout can't be read."""
    try:
        stamp = (os.path.abspath(gds), os.path.getmtime(gds), os.path.getsize(gds), cellname, tuple(sorted(port_layers)))
    except OSError:
        return None
    if stamp in _edge_cache:
        return _edge_cache[stamp]
    try:
        import gdspy
        lib = gdspy.GdsLibrary(infile=gds)
        cell = lib.cells.get(cellname) or (lib.top_level() or [None])[0]
        by_spec = cell.get_polygons(by_spec=True)
    except Exception:       # noqa: BLE001 - an unreadable layout: the caller falls back to the export's record
        return None
    points = [p for polys in by_spec.values() for p in polys if len(p)]
    if not points:
        return None
    x0, y0 = min(p[:, 0].min() for p in points), min(p[:, 1].min() for p in points)
    x1, y1 = max(p[:, 0].max() for p in points), max(p[:, 1].max() for p in points)
    out = set()
    for (layer, _), polys in by_spec.items():
        if layer not in port_layers:
            continue
        for p in polys:
            if p[:, 0].min() - x0 <= EDGE_TOL:
                out.add("xmin")
            if x1 - p[:, 0].max() <= EDGE_TOL:
                out.add("xmax")
            if p[:, 1].min() - y0 <= EDGE_TOL:
                out.add("ymin")
            if y1 - p[:, 1].max() <= EDGE_TOL:
                out.add("ymax")
    if len(_edge_cache) > 64:
        _edge_cache.clear()
    _edge_cache[stamp] = out
    return out


def is_set_file(data):
    section = data.get("pieces") if isinstance(data, dict) else None
    return isinstance(section, dict) and bool(section.get("pieces"))


def _versions():
    """(gds2palace version, combine_extend_snp.py version) as installed here, '' when unknown."""
    g2p, combine = "", ""
    try:
        import gds2palace
        g2p = getattr(gds2palace, "__version__", "")
        from gds2palace import utilities
        scripts = utilities.get_scripts_dir()
        if scripts:
            with open(os.path.join(scripts, "combine_extend_snp.py"), encoding="utf-8") as f:
                m = re.search(r'^__version__\s*=\s*"([^"]+)"', f.read(), re.M)
                combine = m.group(1) if m else ""
    except Exception:       # noqa: BLE001 - a missing gds2palace only means no reuse
        pass
    return g2p, combine


class PieceSet:
    def __init__(self, path, data=None):
        self.path = os.path.abspath(path)
        self.data, self.section = read_set(path) if data is None else (data, data["pieces"])
        self.dir = os.path.dirname(self.path)
        self.name = os.path.splitext(os.path.basename(self.path))[0]
        self._record_wall_sides()
        self.path_messages = self._resolve_paths()

    # ---------- rows ----------

    def _row(self, name, simcfg, box=None, fingerprint=None, ports=0, full=False):
        data = _load(simcfg)
        sim_path, model = _model_place(simcfg, data, name)
        if box:
            label = f"{name}   x {box[0]:g}..{box[2]:g}, y {box[1]:g}..{box[3]:g} µm"
        else:
            label = "full (check): the whole region as one model"
        if data.get(OWN_KEY):
            label += "  · own mesh"
        return {"sim_path": os.path.join(sim_path, "palace_model", f"{model}_data").replace("\\", "/"),
                "model_basename": model, "label": label, "params": {}, "piece": name, "simcfg": simcfg,
                "model_dir": sim_path, "fingerprint": fingerprint or data.get("fingerprint"), "ports": ports,
                "full": full}

    def rows(self):
        """Models list rows (setupEM's run records plus piece, simcfg, model_dir, fingerprint, ports, full): the
        pieces in their order, then the full check model if the export wrote one."""
        out = [self._row(p["name"], os.path.join(self.dir, p["simcfg"]), p.get("box"), p.get("fingerprint"),
                         len(p.get("ports", {})))
               for p in self.section["pieces"]]
        full = self.section.get("full")
        if full and os.path.isfile(os.path.join(self.dir, full)):
            out.append(self._row("full", os.path.join(self.dir, full), ports=len(self.section.get("external", [])),
                                 full=True))
        return out

    def row_for(self, path):
        """The row of a piece's .simcfg, or of the model script Create Model wrote for it (same folder and name)."""
        if not path:
            return None
        key = os.path.normcase(os.path.splitext(os.path.abspath(path))[0])
        return next((r for r in self.rows()
                     if os.path.normcase(os.path.splitext(os.path.abspath(r["simcfg"]))[0]) == key), None)

    # ---------- shared settings, the box sides of a piece, its own mesh ----------

    def _entry(self, row):
        return next((p for p in self.section["pieces"] if p["name"] == row.get("piece")), None)

    def _resolve_paths(self):
        """An export written on another machine (P:/... on Windows, /mnt/pub/... on Linux, another gds2palace
        install): its layout, stackup and model folders, found here and written back once. Returns what was
        replaced (texts)."""
        messages = []

        def find(path, folders, stackup=False):
            if not path or os.path.exists(path):
                return None
            name = os.path.basename(str(path).replace("\\", "/"))
            for folder in folders:
                candidate = os.path.join(folder, name)
                if os.path.isfile(candidate):
                    return candidate.replace("\\", "/")
            if stackup:
                try:
                    from gds2palace import utilities
                    candidate = utilities.get_stackup_path(name)
                    if candidate and os.path.isfile(candidate):
                        return str(candidate).replace("\\", "/")
                except Exception:       # noqa: BLE001 - no bundled stackup of that name
                    pass
            return None

        set_values = self.data.setdefault("saved_values", {})
        changed_set = False
        for key, stackup in (("GdsFile", False), ("SubstrateFile", True)):
            new = find(set_values.get(key), [self.dir], stackup)
            if new:
                messages.append(f"{key}: {set_values[key]} not found, using {new}")
                set_values[key] = new
                changed_set = True
        if changed_set:
            data = _load(self.path)
            data["saved_values"] = set_values
            _write(self.path, data)
        for row in self.rows():
            data = _load(row["simcfg"])
            values = data.get("saved_values")
            if not values:
                continue
            folder = os.path.dirname(row["simcfg"])
            changed = False
            if values.get("sim_path") and not os.path.isdir(values["sim_path"]):
                values["sim_path"] = folder.replace("\\", "/")
                changed = True
            new = find(values.get("GdsFile"), [folder, self.dir])
            if new:
                values["GdsFile"] = new
                changed = True
            if set_values.get("SubstrateFile") and values.get("SubstrateFile") != set_values["SubstrateFile"] \
                    and not os.path.isfile(values.get("SubstrateFile") or ""):
                values["SubstrateFile"] = set_values["SubstrateFile"]
                changed = True
            if changed:
                _write(row["simcfg"], data)
        return messages

    def _record_wall_sides(self):
        """An export without wall sides (klayout_em_export before it wrote them): the sides its export made port
        walls, recorded once, before setupEM derives the sides itself."""
        for row in self.rows():
            data = _load(row["simcfg"])
            if not data or WALLS_KEY in data:
                continue
            bc = _side_values(data.get("saved_values", {}).get("boundary"), 6, "ABC")
            data[WALLS_KEY] = [side for side, v in zip(SIDES, bc) if str(v).upper() == PORTWALL]
            _write(row["simcfg"], data)

    def wall_sides(self, row):
        """The sides of row that are port walls: its cuts with a line across them (the export's record), and the
        region's port walls with a port of row on that edge. The full model has all of the region's."""
        region = self.region_sides()
        region_walls = {side for side in MARGIN_SIDES if region[side][0] == PORTWALL}
        if row.get("full"):
            return region_walls
        data = _load(row["simcfg"])
        recorded = set(data.get(WALLS_KEY) or [])
        cuts = set(self.cut_sides(row))
        values = data.get("saved_values", {})
        layers = {int(p["source_layernum"]) for p in data.get("ports", []) if p.get("source_layernum") is not None}
        on_edge = ports_on_edge(values.get("GdsFile") or "", values.get("cellname"), layers)
        if on_edge is None:     # the layout isn't there: what the export recorded
            on_edge = recorded
        return (recorded & cuts) | ((region_walls - cuts) & on_edge)

    def _moved_walls(self, row):
        """{side: length} for row's stretches of a region port wall without a port of row, where the region moves
        that port wall outward by a length (port_wall_extension): the PEC wall of row goes with it."""
        if row.get("full"):
            return {}
        walls, cuts, region = self.wall_sides(row), self.cut_sides(row), self.region_sides()
        extension = self.data.get("saved_values", {}).get("port_wall_extension") or {}
        out = {}
        for side in MARGIN_SIDES:
            value = extension.get(side)
            if region[side][0] == PORTWALL and side not in walls and side not in cuts \
                    and isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                out[side] = float(value)
        return out

    def cut_sides(self, row):
        """{side: the piece(s) on the other side} for the sides of row's box that are cuts (shared with other boxes;
        the boxes fill a rectangle, so a cut side is shared along its whole length, maybe with two pieces)."""
        entry = self._entry(row)
        if entry is None or not entry.get("box") or row.get("full"):
            return {}
        x1, y1, x2, y2 = entry["box"]
        out = {}
        for other in self.section["pieces"]:
            if other is entry or not other.get("box"):
                continue
            a1, b1, a2, b2 = other["box"]
            overlap_y = min(y2, b2) - max(y1, b1) > TOL
            overlap_x = min(x2, a2) - max(x1, a1) > TOL
            if overlap_y and abs(a2 - x1) < TOL:
                out["xmin"] = ", ".join(filter(None, (out.get("xmin"), other["name"])))
            if overlap_y and abs(a1 - x2) < TOL:
                out["xmax"] = ", ".join(filter(None, (out.get("xmax"), other["name"])))
            if overlap_x and abs(b2 - y1) < TOL:
                out["ymin"] = ", ".join(filter(None, (out.get("ymin"), other["name"])))
            if overlap_x and abs(b1 - y2) < TOL:
                out["ymax"] = ", ".join(filter(None, (out.get("ymax"), other["name"])))
        return out

    def shared(self):
        """The region's settings, the same for every piece (box sides and the piece's own keys left out)."""
        return {k: v for k, v in self.data.get("saved_values", {}).items() if k not in PIECE_KEYS}

    def region_sides(self):
        """{side: (boundary, margin or None for z, air)} of the whole region"""
        values = self.data.get("saved_values", {})
        bc = _side_values(values.get("boundary"), 6, "ABC")
        margin = _side_values(values.get("margin"), 4, 200.0)
        air = _side_values(values.get("air_around"), 6, margin[0])
        return {side: (str(bc[i]).upper(), margin[i] if i < 4 else None, air[i]) for i, side in enumerate(SIDES)}

    def locked_sides(self, row):
        """{side: why} for the sides of row that follow from the split and can't be set for the piece"""
        walls, cuts, region = self.wall_sides(row), self.cut_sides(row), self.region_sides()
        moved = self._moved_walls(row)
        out = {}
        for side in SIDES:
            if side in cuts:
                out[side] = (f"cut to {cuts[side]}: port wall" if side in walls
                             else f"cut to {cuts[side]}: PEC (no line crosses it)")
            elif region[side][0] == PORTWALL and side not in walls:
                out[side] = f"PEC: the region's port wall at {side}, without a port of {row.get('piece')}"
                if side in moved:
                    out[side] += f", moved {moved[side]:g} outward with it"
                elif (self.data.get("saved_values", {}).get("port_wall_extension") or {}).get(side) == "auto":
                    out[side] += " (the port wall's extension 'auto' is not known here: with a length, this wall moves too)"
        return out

    def box_sides(self, row):
        """row's boundary, margin and air_around, as saved settings: the region's, with the cuts and the
        stretches of a port wall without ports of this piece as walls at the layout edge (moved outward like the
        region's port wall there, wall_extension)"""
        walls, cuts, region = self.wall_sides(row), self.cut_sides(row), self.region_sides()
        bc, margin, air = [], [], []
        for side in SIDES:
            b, m, a = region[side]
            if side in cuts or (b == PORTWALL and side not in walls):
                b, m, a = (PORTWALL if side in walls else "PEC"), (0.0 if m is not None else None), 0.0
            bc.append(b)
            if m is not None:
                margin.append(m)
            air.append(a)
        out = {"boundary": bc, "margin": _compact(margin), "air_around": _compact(air)}
        values = self.data.get("saved_values", {})
        for key in SIDE_SETTINGS:
            out[key] = {side: v for side, v in (values.get(key) or {}).items()
                        if side not in cuts and bc[SIDES.index(side)] == PORTWALL}
        out[WALL_EXTENSION] = self._moved_walls(row)
        return out

    def own(self, row):
        return dict(_load(row["simcfg"]).get(OWN_KEY) or {})

    def set_own(self, row, values):
        """row's own mesh settings (MESH_KEYS of values), or None: the shared mesh again"""
        data = _load(row["simcfg"])
        if not data:
            return
        if values:
            data[OWN_KEY] = {k: values[k] for k in MESH_KEYS if k in values}
        else:
            data.pop(OWN_KEY, None)
        _write(row["simcfg"], data)

    def effective_values(self, row):
        """The settings row is built with: its .simcfg, the shared settings, its own mesh, its box sides."""
        values = dict(_load(row["simcfg"]).get("saved_values", {}))
        values.update(self.shared())
        values.update(self.own(row))
        values.update(self.box_sides(row))
        for key in SIDE_SETTINGS + (WALL_EXTENSION,):
            if not values.get(key):
                values.pop(key, None)
        return values

    def store(self, row, values):
        """The settings in the tabs while row is loaded: the shared ones (with an own mesh: the mesh ones into
        row's own) into the set, the sides of row on the region's edge into the region's sides; then every piece's
        .simcfg gets its settings again. Returns the names of the shared settings that changed."""
        own = bool(self.own(row))
        saved = self.data.setdefault("saved_values", {})
        changed = []
        for key, value in values.items():
            if key in PIECE_KEYS or (own and key in MESH_KEYS):
                continue
            if saved.get(key) != value:
                if key in saved:            # a setting the tabs add (a default) is no change to report
                    changed.append(key)
                saved[key] = value
        if own:
            self.set_own(row, values)
        locked = self.locked_sides(row)
        region = self.region_sides()
        bc = _side_values(values.get("boundary"), 6, "ABC")
        margin = _side_values(values.get("margin"), 4, 200.0)
        air = _side_values(values.get("air_around"), 6, margin[0])
        new = {}
        for i, side in enumerate(SIDES):
            if side in locked:
                new[side] = region[side]
            else:
                new[side] = (str(bc[i]).upper(), margin[i] if i < 4 else None, air[i])
        cuts = self.cut_sides(row)
        for key in SIDE_SETTINGS:
            old = dict(saved.get(key) or {})
            mine = values.get(key) or {}
            merged = dict(old)
            for side in MARGIN_SIDES:
                if side in cuts or side in locked:
                    continue            # a cut has none; a stretch without this piece's ports isn't its to set
                if new[side][0] == PORTWALL and mine.get(side):
                    merged[side] = mine[side]
                else:
                    merged.pop(side, None)
            if merged != old:
                changed.append(key)
                if merged:
                    saved[key] = merged
                else:
                    saved.pop(key, None)
        if new != region:
            changed.append("box sides")
            saved["boundary"] = [new[s][0] for s in SIDES]
            saved["margin"] = _compact([new[s][1] for s in MARGIN_SIDES])
            saved["air_around"] = _compact([new[s][2] for s in SIDES])
        data = _load(self.path)
        data["saved_values"] = saved
        _write(self.path, data)
        self.write_pieces()
        return changed

    def write_pieces(self):
        """Every piece's .simcfg (and full.simcfg) with the settings it is built with."""
        for r in self.rows():
            data = _load(r["simcfg"])
            if not data:
                continue
            values = self.effective_values(r)
            if data.get("saved_values") != values:
                data["saved_values"] = values
                _write(r["simcfg"], data)

    # ---------- the joined result ----------

    def joined_results(self, kinds=("_dc_deembedded", "_deembedded")):
        """The joined result files that exist, in the order of kinds (join_pieces writes it where the whole
        export's own model would put it; only calibrated results are joined, so there is no raw one)."""
        ports = len(self.section.get("external", []))
        paths = [joined_path(self.path, self.data, ports, kind) for kind in kinds]
        return [p.replace("\\", "/") for p in paths if os.path.isfile(p)]

    # ---------- the one frequency grid ----------

    def frequency(self):
        values = self.data.get("saved_values", {})
        return {k: values[k] for k in FREQUENCY_KEYS if k in values}

    def set_frequency(self, values):
        """Write the frequency settings into the set's .simcfg and every piece's (and full.simcfg). Returns the
        files changed."""
        new = {k: values[k] for k in FREQUENCY_KEYS if k in values}
        changed = []
        for path in [self.path] + [r["simcfg"] for r in self.rows()]:
            data = _load(path)
            if not data:
                continue
            saved = data.setdefault("saved_values", {})
            if all(saved.get(k) == v for k, v in new.items()):
                continue
            saved.update(new)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            changed.append(path)
        self.data.setdefault("saved_values", {}).update(new)
        return changed

    def frequency_differs(self, values):
        own = self.frequency()
        return any(k in values and own.get(k) != values[k] for k in FREQUENCY_KEYS)

    # ---------- reuse ----------

    def run_key(self, row, values=None):
        """The run key of a row: its fingerprint, its settings (values: the settings to use, default its .simcfg's)
        with the set's frequency, and the versions; None for a row without a fingerprint (full)."""
        if not row.get("fingerprint"):
            return None
        if values is None:
            values = self.effective_values(row)
        settings = {k: v for k, v in values.items() if k not in NOT_IN_KEY}
        settings.update(self.frequency())
        ports = _load(row["simcfg"]).get("ports", [])
        h = hashlib.sha256()
        h.update(row["fingerprint"].encode())
        h.update(json.dumps(settings, sort_keys=True, default=str).encode())
        h.update(json.dumps(ports, sort_keys=True, default=str).encode())
        h.update(json.dumps(_versions()).encode())
        return h.hexdigest()

    @staticmethod
    def key_path(row):
        return os.path.join(row["sim_path"], KEY_FILE)

    def mesh_current(self, row):
        """Whether row was meshed with the settings it has now (its model files there): Start Simulation can go
        straight to the simulation. The full check model has no key: it is always meshed."""
        key = self.run_key(row)
        try:
            with open(os.path.join(row["sim_path"], MESH_KEY_FILE), encoding="utf-8") as f:
                stored = f.read().strip()
        except OSError:
            return False
        return bool(key) and stored == key and os.path.isfile(os.path.join(row["sim_path"], "config.json"))

    def store_mesh_key(self, row):
        key = self.run_key(row)
        if key and os.path.isdir(row["sim_path"]):
            with open(os.path.join(row["sim_path"], MESH_KEY_FILE), "w", encoding="utf-8") as f:
                f.write(key + "\n")

    @staticmethod
    def result(row, kind="_deembedded"):
        return result_path(row["model_dir"], row["model_basename"], row["ports"], kind)

    def stored_key(self, row):
        try:
            with open(self.key_path(row), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return None

    def store_key(self, row, key=None):
        key = key or self.run_key(row)
        if key and os.path.isdir(row["sim_path"]):
            with open(self.key_path(row), "w", encoding="utf-8") as f:
                f.write(key + "\n")

    def reusable(self, row, key=None):
        """Whether row's result can be reused: same run key as stored, and its de-embedded result there. A result
        of another piece with the same key (the box was redrawn under a new number) is copied over first."""
        key = key or self.run_key(row)
        if key is None:
            return False
        if self.stored_key(row) == key and os.path.isfile(self.result(row)):
            return True
        # every model folder next to it, also of pieces no longer in the set (a box redrawn under a new number)
        models = os.path.join(row["model_dir"], "palace_model")
        for folder in sorted(os.listdir(models)) if os.path.isdir(models) else []:
            name = folder[:-len("_data")] if folder.endswith("_data") else None
            if not name or name in (row["model_basename"], "full"):
                continue
            other = dict(row, sim_path=os.path.join(models, folder).replace("\\", "/"), model_basename=name)
            if self.stored_key(other) == key and os.path.isfile(self.result(other)):
                self._copy_result(other, row)
                self.store_key(row, key)
                return True
        return False

    def _copy_result(self, source, target):
        for kind in ("_deembedded", "_dc_deembedded", "", "_dc"):
            src = self.result(source, kind)
            if os.path.isfile(src):
                dst = self.result(target, kind)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
