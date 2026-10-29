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

"""Run a gds2palace model script with some settings[] values overridden.

    python run_with_overrides.py model.py --set preview_only=True --set no_preview=False
    python run_with_overrides.py model.py --source unsaved.py --set preview_only=True
    python run_with_overrides.py model.py --record models.json --set no_preview=True

Used by setupEM's edit-in-place mode, so Preview / Create Mesh don't have to
write their control flags into the user's script. The script runs unchanged,
as __main__ with its own __file__ and its own folder first on sys.path,
exactly like "python model.py". The overrides are applied to the settings dict
when the script calls the workflow's create_model() (also through
create_palace(), create_elmer() or create_elmer_thermal()). That function is
patched when the workflow module is imported, from wherever the script
imports it (installed package or a local copy put on sys.path by the script).

A script may build several models, e.g. a parameter sweep calling
create_palace() in a loop. --record writes every model it creates (run folder,
name, solver, stackup variable overrides, plain settings values) to a JSON
file, so setupEM knows which models to run. --first-only stops the script
right after its first model is built (a preview only needs one). --no-solver
keeps a script with start_simulation = True from starting the solver itself
(setupEM's Start Simulation does that).
"""

import argparse
import ast
import importlib.abc
import importlib.machinery
import json
import os
import runpy
import sys

WORKFLOW_MODULE_SUFFIX = "util_simulation_setup"
STACKUP_MODULE_SUFFIX = "util_stackup_reader"

# settings values that are workflow objects or setupEM's own run control, not
# parameters of a model
_NOT_PARAMETERS = {"sim_path", "model_basename", "preview_only", "no_preview", "no_gui"}


class _Run:
    """What this run of the script does and records."""

    def __init__(self, overrides, record=None, first_only=False, gui_first_only=False):
        self.overrides = overrides
        self.record = record
        self.first_only = first_only
        self.gui_first_only = gui_first_only
        self.models = []
        self.models_started = 0
        self.variable_overrides = None


# programs that start the solver; a script with start_simulation = True runs
# one of these (e.g. run_command = ['./run_sim']) after building each model
_SOLVER_PROGRAMS = ("run_sim", "run_elmer", "run_palace", "elmersolver", "palace")


def _starts_solver(command):
    if isinstance(command, (list, tuple)):
        words = [str(w) for w in command]
    else:
        words = str(command).replace("&&", " ").replace(";", " ").split()
    for word in words:
        name = os.path.basename(word.strip("'\"")).lower()
        if name.endswith((".bat", ".sh", ".exe")):
            name = name.rsplit(".", 1)[0]
        if name.startswith(_SOLVER_PROGRAMS[:4]) or name == "palace":
            return True
    return False


def _skip_solver_starts():
    """setupEM starts the solver itself (Start Simulation, one model after the
    other): while it runs a script, the script's own solver start is skipped
    instead of running every simulation inside Create Mesh."""
    import subprocess

    def wrap(original, result):
        def call(*args, **kwargs):
            command = args[0] if args else kwargs.get("args", kwargs.get("command", ""))
            if _starts_solver(command):
                print(f"setupEM: not starting {command!r} from the script, Start Simulation runs the solver.")
                return result(command)
            return original(*args, **kwargs)
        return call

    subprocess.run = wrap(subprocess.run, lambda c: subprocess.CompletedProcess(c, 0, "", ""))
    subprocess.call = wrap(subprocess.call, lambda c: 0)
    subprocess.check_call = wrap(subprocess.check_call, lambda c: 0)
    os.system = wrap(os.system, lambda c: 0)


def _parse_value(text):
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def _plain(value):
    # JSON-able numbers / strings / bools, and lists or dicts of those
    if isinstance(value, (bool, int, float, str)) or value is None:
        return True
    if isinstance(value, (list, tuple)):
        return all(_plain(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _plain(v) for k, v in value.items())
    return False


def _record_model(run, settings, result):
    if run.record is None:
        return
    if settings.get("elmer_thermal"):
        solver = "elmer_thermal"
    elif settings.get("elmer"):
        solver = "elmer"
    else:
        solver = "palace"
    config_name = result[0] if isinstance(result, tuple) and result and isinstance(result[0], str) else None
    sim_path = settings.get("sim_path")
    run.models.append({
        "sim_path": os.path.abspath(sim_path).replace("\\", "/") if isinstance(sim_path, str) else None,
        "model_basename": settings.get("model_basename"),
        "solver": solver,
        "config": config_name,
        "variable_overrides": run.variable_overrides if _plain(run.variable_overrides) else None,
        "settings": {k: v for k, v in settings.items() if k not in _NOT_PARAMETERS and _plain(v)},
    })
    # written after every model, so a run that stops halfway still lists what it built
    os.makedirs(os.path.dirname(os.path.abspath(run.record)), exist_ok=True)
    with open(run.record, "w", encoding="utf-8") as f:
        json.dump(run.models, f, indent=1)


def _patch(module, run):
    name = getattr(module, "__name__", "").split(".")[-1]
    if name == STACKUP_MODULE_SUFFIX:
        original_read = getattr(module, "read_substrate", None)
        if original_read is None or getattr(original_read, "_setupEM_run", False):
            return

        def read_substrate(*args, **kwargs):
            # remember the stackup variables of the model being set up (a sweep
            # parameter like Temp_Celsius often lives here)
            run.variable_overrides = kwargs.get("variable_overrides", args[1] if len(args) > 1 else None)
            return original_read(*args, **kwargs)

        read_substrate._setupEM_run = True
        module.read_substrate = read_substrate
        return

    original = getattr(module, "create_model", None)
    if original is None or getattr(original, "_setupEM_run", False):
        return

    def create_model(*args, **kwargs):
        run.models_started += 1
        settings = kwargs.get("settings")
        if settings is None:
            settings = next((a for a in reversed(args) if isinstance(a, dict)), None)
        if settings is not None:
            settings.update(run.overrides)
            if run.gui_first_only and run.models_started > 1:
                # a sweep: the gmsh window only for the first model, the others
                # are built without one (no_gui only controls the windows)
                settings["no_gui"] = True
                print(f"setupEM: model {run.models_started} is built without a gmsh window.")
        result = original(*args, **kwargs)
        if settings is not None:
            _record_model(run, settings, result)
        if run.first_only:
            # a preview is done once the first model is built: the rest of the
            # script would only set up further models (a sweep) or start the
            # solver (start_simulation = True)
            print("\nsetupEM preview: done after the first model, the rest of the script is not run.")
            sys.stdout.flush()
            sys.stderr.flush()
            # a plain exit could be caught by the script's own try/except
            os._exit(0)
        return result

    create_model._setupEM_run = True
    module.create_model = create_model


class _PatchingLoader(importlib.abc.Loader):
    def __init__(self, loader, run):
        self.loader = loader
        self.run = run

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        _patch(module, self.run)


class _PatchingFinder(importlib.abc.MetaPathFinder):
    def __init__(self, run):
        self.run = run

    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[-1] not in (WORKFLOW_MODULE_SUFFIX, STACKUP_MODULE_SUFFIX):
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                    spec.loader = _PatchingLoader(spec.loader, self.run)
                return spec
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("script", help="model script to run")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="settings[KEY] = VALUE (a Python literal) when the model is created")
    parser.add_argument("--source", metavar="FILE",
                        help="run the code in FILE as if it were the script (same __file__, folder and "
                             "output paths), e.g. unsaved changes for a preview")
    parser.add_argument("--record", metavar="JSON",
                        help="write the models the script creates (run folders, parameters) to this file")
    parser.add_argument("--first-only", action="store_true",
                        help="stop right after the script has built its first model")
    parser.add_argument("--no-solver", action="store_true",
                        help="don't let the script start the solver itself (start_simulation = True)")
    parser.add_argument("--gui-first-only", action="store_true",
                        help="show gmsh windows only for the first model the script builds")
    args = parser.parse_args(argv)

    overrides = {}
    for item in args.set:
        key, sep, value = item.partition("=")
        if not sep or not key:
            parser.error(f"--set expects KEY=VALUE, got {item!r}")
        overrides[key.strip()] = _parse_value(value.strip())
    run = _Run(overrides, record=args.record, first_only=args.first_only, gui_first_only=args.gui_first_only)

    script = os.path.abspath(args.script)
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").split(".")[-1] in (WORKFLOW_MODULE_SUFFIX, STACKUP_MODULE_SUFFIX):
            _patch(module, run)
    sys.meta_path.insert(0, _PatchingFinder(run))
    if args.no_solver:
        _skip_solver_starts()

    # same environment as "python model.py"
    sys.argv = [script]
    sys.path[0] = os.path.dirname(script)
    if args.source:
        # other code, but run as the script itself: __file__ decides the output
        # folder names (utilities.get_basename(__file__)) and relative paths
        with open(args.source, encoding="utf-8") as f:
            code = compile(f.read(), script, "exec")
        exec(code, {"__name__": "__main__", "__file__": script, "__builtins__": __builtins__,
                    "__package__": None, "__spec__": None, "__doc__": None})
    else:
        runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
