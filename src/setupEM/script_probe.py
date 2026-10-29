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

"""Import a gds2palace model script by running it, with gds2palace's slow
and file-writing steps replaced.

File > Import from *.py model reads the values gds2palace would get, also
where the script computes them (loops over port definitions, values from
other variables, the stackup from utilities.get_stackup_path()). The script
runs in its own process, from its own folder, with the same gds2palace the
GUI uses, and:
  - read_gds() records its arguments and returns no polygons (no GDSII read)
  - read_substrate() records its arguments and reads the stackup XML
  - simulation_port / heatsource / constanttemp record their arguments
  - create_sim_path() creates no folder, create_run_script() writes nothing
  - the first create_palace() / create_elmer() / create_elmer_thermal() /
    create_model() records the settings and ends the run: no mesh, and the
    script's own solver start after it never runs
The script's own code before that call does run (e.g. a script that
generates its GDSII file writes it).

No Qt in here. Edit in place doesn't use this: it reads the script text
(script_model.py), since it has to know where each value is written.
"""

import inspect
import json
import os
import subprocess
import sys

try:
    from .script_model import CALL_ARGUMENTS
except ImportError:
    from script_model import CALL_ARGUMENTS

PROBE_TIMEOUT = 60      # seconds

# workflow calls that build the model; the first one ends the run
CREATE_FUNCTIONS = ("create_palace", "create_elmer", "create_elmer_thermal", "create_model")


class ProbeResult:
    """What a run of the script handed to gds2palace.

    values: GUI key -> value as in the script (Hz, paths absolute)
    ports: keyword arguments of each simulation_port in the model
    thermal: (kind, keyword arguments) of each heat source / constant temperature
    create_call: the create_*() function that ended the run
    error: why the run didn't get that far ("" if it did)
    """

    def __init__(self, values=None, ports=None, thermal=None, create_call="", notes=None, error=""):
        self.values = values or {}
        self.ports = ports or []
        self.thermal = thermal or []
        self.create_call = create_call
        self.notes = notes or []       # (GUI key, reason) for values that can't be used
        self.error = error

    @property
    def ok(self):
        return not self.error


def probe_script(path, timeout=PROBE_TIMEOUT):
    """Run the model script at path as described above; returns a ProbeResult."""
    path = os.path.abspath(path)
    try:
        import gds2palace
    except ImportError:
        return ProbeResult(error="gds2palace is not installed")
    # the folder that holds the gds2palace package the GUI itself uses (e.g. a
    # checkout next to setupEM, see setupEM.py), so both read the same code
    gds2palace_parent = os.path.dirname(os.path.dirname(os.path.abspath(gds2palace.__file__)))

    import tempfile
    fd, out_path = tempfile.mkstemp(suffix=".json", prefix="setupEM_probe_")
    os.close(fd)
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW   # no console flashing up
    try:
        try:
            run = subprocess.run(
                [sys.executable, os.path.abspath(__file__), path, out_path, gds2palace_parent],
                cwd=os.path.dirname(path), capture_output=True, text=True, errors="replace",
                timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired:
            return ProbeResult(error=f"it ran longer than {timeout} s")
        try:
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass
    if not data:
        lines = [line for line in (run.stderr or run.stdout or "").splitlines() if line.strip()]
        return ProbeResult(error=lines[-1].strip() if lines else f"it stopped with exit code {run.returncode}")
    error = data.get("error", "")
    if error and data.get("exited"):
        # gds2palace prints why before it exits (e.g. "XML stackup file not found: ...")
        printed = [line.strip() for line in (run.stdout or "").splitlines() if line.strip()]
        if printed:
            error += f": {printed[-1]}"
    return ProbeResult(values=data.get("values"), ports=data.get("ports"), thermal=data.get("thermal"),
                       create_call=data.get("create_call", ""), notes=data.get("notes"), error=error)


# ---------------------------------------------------------------------------
# the child process
# ---------------------------------------------------------------------------

class _ModelCreated(Exception):
    """Ends the script run at its first create_*() call."""


def _json_value(value):
    # plain values only; gds2palace objects (ports, polygons, stackup) are not settings
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return None, False
    return value, True


def _child(script, out_path, gds2palace_parent):
    # run as a file, this module's own folder (setupEM's modules) is first on
    # sys.path - not something the model script should import from
    own_folder = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != own_folder]
    sys.path.insert(0, gds2palace_parent)
    sys.path.insert(0, os.path.dirname(script))     # as "python script.py" does
    from gds2palace import gds_reader, stackup_reader, simulation_setup, utilities

    calls = {}          # function name -> list of {argument name: value}
    captured = {}

    def record(function, signature, args, kwargs):
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError:
            return
        calls.setdefault(function, []).append(dict(bound.arguments))

    # ---- the GDSII is not read: the import needs only the arguments
    read_gds_signature = inspect.signature(gds_reader.read_gds)

    def read_gds(*args, **kwargs):
        record("read_gds", read_gds_signature, args, kwargs)
        return gds_reader.all_polygons_list()
    gds_reader.read_gds = read_gds

    real_read_substrate = stackup_reader.read_substrate
    read_substrate_signature = inspect.signature(real_read_substrate)

    def read_substrate(*args, **kwargs):
        record("read_substrate", read_substrate_signature, args, kwargs)
        return real_read_substrate(*args, **kwargs)
    stackup_reader.read_substrate = read_substrate

    # ---- no folders or run scripts
    def create_sim_path(script_path, model_basename, dirname="palace_model"):
        return os.path.join(script_path, dirname, model_basename + "_data")
    utilities.create_sim_path = create_sim_path
    utilities.create_run_script = lambda *args, **kwargs: None

    # ---- ports and thermal objects remember their arguments
    def recording(cls):
        signature = inspect.signature(cls.__init__)

        class Recorded(cls):
            def __init__(self, *args, **kwargs):
                try:
                    bound = signature.bind(self, *args, **kwargs)
                    self._probe_args = {k: v for k, v in bound.arguments.items() if k != "self"}
                except TypeError:
                    self._probe_args = None
                super().__init__(*args, **kwargs)
        Recorded.__name__ = cls.__name__
        return Recorded
    for name in ("simulation_port", "heatsource", "constanttemp"):
        if hasattr(simulation_setup, name):
            setattr(simulation_setup, name, recording(getattr(simulation_setup, name)))

    # ---- the first model built ends the run
    def creating(function_name):
        def create(*args, **kwargs):
            settings = kwargs.get("settings")
            if settings is None:
                settings = next((a for a in args if isinstance(a, dict)), {})
            captured["create_call"] = function_name
            captured["settings"] = settings
            raise _ModelCreated()
        return create
    for name in CREATE_FUNCTIONS:
        if hasattr(simulation_setup, name):
            setattr(simulation_setup, name, creating(name))

    import runpy
    error, exited = "", False
    try:
        runpy.run_path(script, run_name="__main__")
        error = "it doesn't call create_palace(), create_elmer() or create_elmer_thermal()"
    except _ModelCreated:
        pass
    except SystemExit as e:
        error, exited = f"it stopped (exit {e.code}) before building the model", True
    except BaseException as e:      # anything the script itself raises
        error = f"{type(e).__name__}: {e}"

    result = {"error": error, "exited": exited, "values": {}, "ports": [], "thermal": [], "notes": [],
              "create_call": captured.get("create_call", "")}
    settings = captured.get("settings") or {}
    values, notes = result["values"], result["notes"]
    for key, value in settings.items():
        value, ok = _json_value(value)
        if ok:
            values[key] = value

    # values the script passes straight into read_gds() / read_substrate()
    for key, (function, _position, keyword) in CALL_ARGUMENTS.items():
        passed = [c[keyword] for c in calls.get(function, []) if keyword in c]
        if not passed:
            continue
        distinct = {json.dumps(v, sort_keys=True, default=str) for v in passed}
        if len(distinct) > 1:
            notes.append((key, f"{function}() is called {len(passed)} times with different values"))
            values.pop(key, None)
            continue
        value, ok = _json_value(passed[0])
        if not ok:
            continue
        if key in ("GdsFile", "SubstrateFile") and isinstance(value, str):
            # relative to the folder the script was in when it read the file
            value = os.path.abspath(value)
        values[key] = value

    ports = getattr(settings.get("simulation_ports"), "ports", None) or []
    for port in ports:
        args = getattr(port, "_probe_args", None)
        if args is not None:
            result["ports"].append(_json_value(args)[0] or {})
    thermal = getattr(settings.get("thermal_objects"), "objects", None) or []
    for obj in thermal:
        args = getattr(obj, "_probe_args", None)
        kind = type(obj).__name__
        if args is not None and kind in ("heatsource", "constanttemp"):
            result["thermal"].append((kind, _json_value(args)[0] or {}))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    _child(*sys.argv[1:4])
