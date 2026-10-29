# Tests for src/setupEM/run_with_overrides.py with a stand-in workflow module.
#
# The fake util_simulation_setup.py records the settings create_model() gets,
# so the test checks that overrides arrive there while the script itself and
# its own values stay untouched.

import json
import os
import subprocess
import sys
import textwrap

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "setupEM", "run_with_overrides.py")

FAKE_WORKFLOW = textwrap.dedent("""
    import json, os
    def create_model(excite_ports, settings):
        with open(os.path.join(os.path.dirname(__file__), "received.json"), "w") as f:
            json.dump({k: v for k, v in settings.items() if k != "out"}, f, default=str)
        return "config", "data"
    def create_palace(excite_ports, settings):
        settings["palace"] = True
        return create_model(excite_ports, settings)
""")

SCRIPT = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "localflow"))
    import util_simulation_setup as simulation_setup
    assert __name__ == "__main__"
    settings = {}
    settings['fstop'] = 10e9
    settings['preview_only'] = False
    simulation_setup.create_palace([], settings)
""")


def run(tmp_path, *overrides, source=None):
    flow = tmp_path / "localflow"
    flow.mkdir()
    (flow / "util_simulation_setup.py").write_text(FAKE_WORKFLOW)
    script = tmp_path / "model.py"
    script.write_text(SCRIPT)
    before = script.read_bytes()
    args = [sys.executable, RUNNER, str(script)]
    if source is not None:
        args += ["--source", str(source)]
    for o in overrides:
        args += ["--set", o]
    result = subprocess.run(args, capture_output=True, text=True, cwd=str(tmp_path.parent))
    assert result.returncode == 0, result.stderr
    assert script.read_bytes() == before
    return json.loads((flow / "received.json").read_text())


def test_overrides_reach_create_model_through_create_palace(tmp_path):
    received = run(tmp_path, "preview_only=True", "no_preview=False")
    assert received == {"fstop": 10e9, "preview_only": True, "no_preview": False, "palace": True}


def test_without_overrides_script_values_are_kept(tmp_path):
    received = run(tmp_path)
    assert received["preview_only"] is False


SWEEP_SCRIPT = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "localflow"))
    import util_stackup_reader as stackup_reader
    import util_simulation_setup as simulation_setup
    for T in [25, 85]:
        stackup_reader.read_substrate("x.xml", variable_overrides={'Temp_Celsius': T})
        settings = {'fstop': 10e9, 'sim_path': os.path.join(os.path.dirname(__file__), f"m_T{T}_data"),
                    'model_basename': f"m_T{T}", 'materials_list': object()}
        simulation_setup.create_palace([], settings)
        print("built", T)
""")


def run_sweep(tmp_path, *extra):
    flow = tmp_path / "localflow"
    flow.mkdir()
    (flow / "util_simulation_setup.py").write_text(FAKE_WORKFLOW)
    (flow / "util_stackup_reader.py").write_text("def read_substrate(xml, variable_overrides=None):\n    return 1, 2, 3\n")
    script = tmp_path / "sweep.py"
    script.write_text(SWEEP_SCRIPT)
    return subprocess.run([sys.executable, RUNNER, str(script), *extra], capture_output=True, text=True)


def test_record_lists_every_model_of_a_sweep(tmp_path):
    record = tmp_path / "out" / "sweep_models.json"
    result = run_sweep(tmp_path, "--record", str(record), "--set", "no_preview=True")
    assert result.returncode == 0, result.stderr
    models = json.loads(record.read_text())
    assert [m["model_basename"] for m in models] == ["m_T25", "m_T85"]
    assert models[1]["sim_path"].endswith("/m_T85_data") and models[0]["solver"] == "palace"
    assert models[1]["variable_overrides"] == {"Temp_Celsius": 85}
    assert models[0]["settings"] == {"fstop": 10e9, "palace": True}   # objects and run flags left out


def test_gui_first_only_hides_windows_after_the_first_model(tmp_path):
    record = tmp_path / "models.json"
    result = run_sweep(tmp_path, "--record", str(record), "--gui-first-only")
    assert result.returncode == 0, result.stderr
    # the fake workflow writes what the last create_model() got
    assert json.loads((tmp_path / "localflow" / "received.json").read_text())["no_gui"] is True
    assert result.stdout.count("built without a gmsh window") == 1   # model 2 only
    # no_gui isn't a model parameter: it must not make the models look different
    assert all("no_gui" not in m["settings"] for m in json.loads(record.read_text()))


def test_first_only_stops_right_after_the_first_model(tmp_path):
    # nothing after the first create_model() runs: not the rest of the loop
    # pass ("built 25"), not the second model's setup
    record = tmp_path / "models.json"
    result = run_sweep(tmp_path, "--first-only", "--record", str(record))
    assert result.returncode == 0, result.stderr
    assert "built" not in result.stdout
    assert "done after the first model" in result.stdout
    assert [m["model_basename"] for m in json.loads(record.read_text())] == ["m_T25"]


def test_no_solver_skips_the_scripts_own_solver_start(tmp_path):
    # start_simulation = True: the script runs ./run_sim itself; other commands still run
    script = tmp_path / "model.py"
    script.write_text(textwrap.dedent("""
        import subprocess, sys, os
        subprocess.run(['./run_sim'], shell=True)
        os.system('wsl.exe --cd /tmp -- bash -lc ./run_sim')
        r = subprocess.run([sys.executable, '-c', 'print(42)'], capture_output=True, text=True)
        print('other command output:', r.stdout.strip())
    """))
    result = subprocess.run([sys.executable, RUNNER, str(script), "--no-solver"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("not starting") == 2
    assert "other command output: 42" in result.stdout


def test_source_runs_other_code_as_the_script(tmp_path):
    # unsaved changes: the code comes from another file, but __file__, the
    # folder on sys.path and the output names stay those of the script
    unsaved = tmp_path.parent / f"{tmp_path.name}_unsaved.py"
    unsaved.write_text(SCRIPT.replace("10e9", "20e9") +
                       "settings['file'] = os.path.basename(__file__)\n"
                       "simulation_setup.create_palace([], settings)\n")
    received = run(tmp_path, "preview_only=True", source=unsaved)
    assert received["fstop"] == 20e9 and received["file"] == "model.py" and received["preview_only"] is True
