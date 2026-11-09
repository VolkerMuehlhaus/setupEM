"""A split EM region in setupEM: the pieces of klayout_em_export's Export All, as rows of the Models list.

<export>.simcfg has a "pieces" section (klayout_em_export pieces.py): one model per piece in <export>_pieces/
(p1.simcfg, p2.simcfg, ...) and full.simcfg, the whole region as one model to check the joined result. Each piece
keeps its own settings (mesh, solver); the frequency settings are one set for all pieces, because the join needs
one frequency grid. After the pieces are simulated, join_pieces connects their results at the cuts.

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

from .join_pieces import read_set, result_path

FREQUENCY_KEYS = ("fstart", "fstop", "fstep", "fpoint", "fdump")
# settings that don't change a piece's result (where it is, what it is called, how setupEM shows it)
NOT_IN_KEY = {"sim_path", "model_basename", "GdsFile", "SubstrateFile", "preview_only", "no_preview", "unit"}
KEY_FILE = "piece.key"


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


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

    # ---------- rows ----------

    def _row(self, name, simcfg, box=None, fingerprint=None, ports=0, full=False):
        data = _load(simcfg)
        values = data.get("saved_values", {})
        sim_path = values.get("sim_path") or os.path.dirname(simcfg)
        model = values.get("model_basename") or name
        if box:
            label = f"{name}   x {box[0]:g}..{box[2]:g}, y {box[1]:g}..{box[3]:g} µm"
        else:
            label = "full (check): the whole region as one model"
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
            values = _load(row["simcfg"]).get("saved_values", {})
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
