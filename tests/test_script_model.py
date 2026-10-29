# Tests for preserve mode (src/setupEM/script_model.py).
#
# Run from the repo root:   python -m pytest tests
#
# Fixtures: example models from gds2palace (inductor, butler matrix, L2n0,
# Elmer thermal) and two scripts written by setupEM / setupThermal's own
# create_model_text(), so both hand-written and generated scripts are covered.

import ast
import difflib
import glob
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM import script_model  # noqa: E402
from setupEM.script_model import ScriptModel, Refused, patch_script  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
ALL_FIXTURES = sorted(glob.glob(os.path.join(FIXTURES, "*.py")))
PALACE_FIXTURES = [f for f in ALL_FIXTURES if "thermal" not in os.path.basename(f).lower()
                   and "Thermal" not in os.path.basename(f)]
THERMAL_FIXTURES = [os.path.join(FIXTURES, "elmer_thermal_simplest_typicalvalues.py"),
                    os.path.join(FIXTURES, "generated_setupThermal.py")]

# more real-world scripts, when the sibling repos are checked out next to this one
SIBLING_SCRIPTS = sorted(
    glob.glob(os.path.join(HERE, "..", "..", "gds2palace_ihp_sg13g2", "workflow", "*.py")) +
    glob.glob(os.path.join(HERE, "..", "..", "..", "github", "EMStudio", "examples", "palace", "**", "*.py"),
              recursive=True))


def load(path):
    return ScriptModel.from_file(path)


def changed_lines(before, after):
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
    return [line for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def value_of(text, key, path=None):
    site = ScriptModel(text, path).site(key)
    assert site is not None and site.resolved, key
    return site.value


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ALL_FIXTURES, ids=os.path.basename)
def test_input_files_are_found_and_writable(path):
    model = load(path)
    for key in ("GdsFile", "SubstrateFile"):
        site = model.site(key)
        assert site is not None and site.writable, (key, site and site.reason)


@pytest.mark.parametrize("path", PALACE_FIXTURES, ids=os.path.basename)
def test_ports_are_found(path):
    model = load(path)
    assert model.ports and all(p.static for p in model.ports)
    numbers = sorted(int(p.args["portnumber"]) for p in model.ports)
    assert numbers == list(range(1, len(numbers) + 1))
    assert model.tool == "palace"


@pytest.mark.parametrize("path", THERMAL_FIXTURES, ids=os.path.basename)
def test_thermal_objects_are_found(path):
    model = load(path)
    assert [c.kind for c in model.thermal] == ["heatsource", "constanttemp"]
    assert model.tool == "elmer_thermal"


def test_hash_inside_string_and_unicode():
    text = "settings = {}\nsettings['GdsFile'] = 'a#b_Müller.gds'  # comment\nsettings['fstop'] = 1e9\n"
    model = ScriptModel(text)
    assert model.site("GdsFile").value == "a#b_Müller.gds"
    model.set_value("fstop", "2e9")
    assert model.result() == text.replace("= 1e9", "= 2e9")


# ---------------------------------------------------------------------------
# writing: nothing changed means nothing written
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ALL_FIXTURES + SIBLING_SCRIPTS, ids=os.path.basename)
def test_no_changes_is_byte_identical(path):
    try:
        model = load(path)
    except SyntaxError:
        pytest.skip("not a parseable model")
    values = {"fstart": 0.0, "fstop": 100.0, "purpose": [0], "margin": 50}
    written, refused = patch_script(model, values, dict(values), baseline_ports=[], current_ports=[])
    assert written == [] and refused == []
    with open(path, encoding="utf-8", newline="") as f:
        assert model.result() == f.read()


@pytest.mark.parametrize("path", PALACE_FIXTURES + SIBLING_SCRIPTS, ids=os.path.basename)
def test_single_edit_changes_one_line(path):
    try:
        model = load(path)
    except SyntaxError:
        pytest.skip("not a parseable model")
    site = model.site("fstop")
    if site is None or not site.writable:
        pytest.skip("no editable fstop")
    before = model.text
    written, refused = patch_script(model, {"fstop": 1.0}, {"fstop": 123.5})
    assert written == ["fstop"] and refused == []
    after = model.result()
    lines = changed_lines(before, after)
    assert len(lines) == 2 and "123.5e9" in lines[1], lines
    ast.parse(after)
    assert value_of(after, "fstop") == 123.5e9


# ---------------------------------------------------------------------------
# values in the script's own shape
# ---------------------------------------------------------------------------

def test_ghz_lists_and_scalars():
    text = "settings = {}\nsettings['fpoint'] = [1e9, 2e9]\nsettings['fdump'] = 5e9\n"
    model = ScriptModel(text)
    patch_script(model, {"fpoint": [1.0, 2.0], "fdump": [5.0]}, {"fpoint": [1.0, 3.5], "fdump": [7.0]})
    after = model.result()
    assert "settings['fpoint'] = [1e9, 3.5e9]" in after
    assert "settings['fdump'] = 7e9" in after      # was a scalar, stays a scalar


def test_purpose_list_in_settings():
    path = os.path.join(FIXTURES, "inductor_500pH_2port.py")
    model = load(path)
    patch_script(model, {"purpose": [0]}, {"purpose": [0, 35, 4]})
    after = model.result()
    assert value_of(after, "purpose") == [0, 35, 4]
    assert len(changed_lines(model.text, after)) == 2


def test_purpose_literal_in_read_gds_call():
    # the thermal example passes purposelist=[0] straight into read_gds()
    path = os.path.join(FIXTURES, "elmer_thermal_simplest_typicalvalues.py")
    model = load(path)
    site = model.site("purpose")
    assert site.kind == "argument" and site.value == [0]
    patch_script(model, {"purpose": [0]}, {"purpose": [0, 35]})
    after = model.result()
    assert "purposelist=[0, 35]" in after
    assert len(changed_lines(model.text, after)) == 2


def test_purpose_scalar_with_wrapper():
    text = ("settings = {}\nsettings['purpose'] = 0\n"
            "allpolygons = gds_reader.read_gds(f, layers, purposelist=[settings['purpose']], metals_list=m)\n")
    # one value: stays a scalar inside the script's own list
    model = ScriptModel(text)
    patch_script(model, {"purpose": [0]}, {"purpose": [4]})
    assert "settings['purpose'] = 4\n" in model.result()
    assert "purposelist=[settings['purpose']]" in model.result()
    # several values: the setting becomes a list and the wrapper goes away
    model = ScriptModel(text)
    patch_script(model, {"purpose": [0]}, {"purpose": [0, 35]})
    after = model.result()
    assert "settings['purpose'] = [0, 35]" in after
    assert "purposelist=settings['purpose']" in after


def test_purpose_scalar_without_wrapper_becomes_list():
    text = ("settings = {}\nsettings['purpose'] = 0\n"
            "allpolygons = gds_reader.read_gds(f, layers, purposelist=settings['purpose'])\n")
    model = ScriptModel(text)
    patch_script(model, {"purpose": [0]}, {"purpose": [0, 35]})
    assert "settings['purpose'] = [0, 35]" in model.result()


def test_int_versus_list_is_no_change():
    text = "settings = {}\nsettings['purpose'] = 0\n"
    model = ScriptModel(text)
    written, _ = patch_script(model, {"purpose": [0]}, {"purpose": [0]})
    assert written == [] and model.result() == text


def test_path_written_relative_to_script(tmp_path):
    script = tmp_path / "model.py"
    script.write_text("settings = {}\nsettings['GdsFile'] = \"old.gds\"\n", encoding="utf-8")
    model = load(str(script))
    new = str(tmp_path / "layouts" / "new.gds")
    patch_script(model, {"GdsFile": str(tmp_path / "old.gds")}, {"GdsFile": new})
    assert 'settings[\'GdsFile\'] = "layouts/new.gds"' in model.result()   # keeps the quote style


# the stackup the way gds2palace_mcp writes it: a file that ships with gds2palace
MCP_STACKUP = 'XML_filename = utilities.get_stackup_path("SG13G2_FEM_200um.xml")\n'


def bundled_stackup(name):
    path = script_model._bundled_stackup(name)
    if path is None:
        pytest.skip(f"the installed gds2palace doesn't ship {name}")
    return path


def test_bundled_stackup_is_read():
    path = bundled_stackup("SG13G2_FEM_200um.xml")
    site = ScriptModel(MCP_STACKUP).site("SubstrateFile")
    assert site.writable and site.value == path


def test_other_bundled_stackup_stays_a_call():
    old = bundled_stackup("SG13G2_FEM_200um.xml")
    new = bundled_stackup("SG13G2_FEM_200um_passi3D.xml")
    model = ScriptModel(MCP_STACKUP)
    patch_script(model, {"SubstrateFile": old}, {"SubstrateFile": new})
    assert model.result() == 'XML_filename = utilities.get_stackup_path("SG13G2_FEM_200um_passi3D.xml")\n'


def test_own_stackup_replaces_the_call(tmp_path):
    old = bundled_stackup("SG13G2_FEM_200um.xml")
    script = tmp_path / "model.py"
    script.write_bytes(MCP_STACKUP.encode())
    model = load(str(script))
    patch_script(model, {"SubstrateFile": old}, {"SubstrateFile": str(tmp_path / "my_stackup.xml")})
    assert model.result() == 'XML_filename = "my_stackup.xml"\n'


def test_unknown_bundled_stackup_is_read_only():
    site = ScriptModel('XML_filename = utilities.get_stackup_path("no_such_stackup.xml")\n').site("SubstrateFile")
    assert not site.writable and not site.resolved


# ---------------------------------------------------------------------------
# settings the script doesn't have yet
# ---------------------------------------------------------------------------

def test_missing_setting_is_inserted_after_last_setting():
    text = "settings = {}\nsettings['fstart'] = 0\nsettings['fstop'] = 10e9  # top\nprint(1)\n"
    model = ScriptModel(text)
    patch_script(model, {}, {"meshsize_max": 70})
    assert model.result() == ("settings = {}\nsettings['fstart'] = 0\nsettings['fstop'] = 10e9  # top\n"
                              "settings['meshsize_max'] = 70\nprint(1)\n")


def test_missing_call_argument_is_added_to_the_call():
    path = os.path.join(FIXTURES, "inductor_500pH_2port.py")
    model = load(path)
    assert model.site("cellname") is None
    patch_script(model, {}, {"cellname": "TOP"})
    after = model.result()
    assert "cellname='TOP')" in after
    assert ScriptModel(after).site("cellname").value == "TOP"


def test_crlf_is_kept():
    text = "settings = {}\r\nsettings['fstop'] = 10e9\r\n"
    model = ScriptModel(text)
    patch_script(model, {"fstop": 10.0}, {"fstop": 20.0, "margin": 30})
    assert model.result() == "settings = {}\r\nsettings['fstop'] = 20e9\r\nsettings['margin'] = 30\r\n"


# a script from before gds2palace 0.9.0 sets filled_metals, the GUI has conductor_model
LEGACY = "settings = {}\nsettings['filled_metals'] = True  # volumes\nsettings['fstop'] = 10e9\n"


def test_legacy_filled_metals_is_written_when_it_can_express_the_value():
    model = ScriptModel(LEGACY)
    written, refused = patch_script(model, {"conductor_model": "solve_inside"}, {"conductor_model": "surface"})
    assert written == ["filled_metals"] and refused == []
    assert model.result() == LEGACY.replace("= True  # volumes", "= False  # volumes")


def test_legacy_filled_metals_becomes_conductor_model_otherwise():
    # gds2palace refuses filled_metals next to a conductor_model it contradicts
    model = ScriptModel(LEGACY)
    written, refused = patch_script(model, {"conductor_model": "solve_inside"},
                                    {"conductor_model": "solve_inside_skin"})
    assert written == ["conductor_model"] and refused == []
    assert model.result() == LEGACY.replace("settings['filled_metals'] = True",
                                            "settings['conductor_model'] = 'solve_inside_skin'")


def test_legacy_filled_metals_in_a_loop_is_read_only():
    text = "settings = {}\nfor fm in [True, False]:\n    settings['filled_metals'] = fm\n"
    model = ScriptModel(text)
    written, refused = patch_script(model, {"conductor_model": "solve_inside"}, {"conductor_model": "surface"})
    assert written == [] and refused and refused[0][0] == "conductor_model"
    assert model.result() == text


# ---------------------------------------------------------------------------
# values the GUI must not overwrite
# ---------------------------------------------------------------------------

def test_values_using_other_names_are_read_only():
    text = "ftarget = 10e9\nsettings = {}\nsettings['fstop'] = 2*ftarget\n"
    model = ScriptModel(text)
    site = model.site("fstop")
    assert site.value == 20e9 and not site.writable
    written, refused = patch_script(model, {"fstop": 20.0}, {"fstop": 30.0})
    assert written == [] and refused and refused[0][0] == "fstop"
    assert model.result() == text


def test_settings_using_the_loop_variable_are_read_only():
    text = "settings = {}\nfor f in [1e9, 2e9]:\n    settings['fstop'] = f\n"
    site = ScriptModel(text).site("fstop")
    # read-only; shown with the first loop value, the reason lists them all
    assert not site.writable and site.value == 1e9 and site.reason == "swept: f = 1000000000.0, 2000000000.0"


# a sweep script: the whole model is built inside a loop over a parameter
SWEEP = ("for T in [25, 85]:\n"
         "    settings = {}\n"
         "    settings['fstop'] = 100e9  # top\n"
         "    variable_overrides = {'Temp_Celsius': T}\n"
         "    ports = simulation_setup.all_simulation_ports()\n"
         "    ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1, port_Z0=50, "
         "source_layernum=201, target_layername='TopMetal2', direction='x'))\n"
         "    simulation_setup.create_palace([], settings)\n")


def test_sweep_plain_values_in_loop_are_editable():
    model = ScriptModel(SWEEP)
    assert model.site("fstop").writable
    assert model.ports[0].static
    site = model.site("variable_overrides")
    assert not site.writable and site.value == {"Temp_Celsius": 25} and site.reason == "swept: T = 25, 85"
    patch_script(model, {"fstop": 100.0}, {"fstop": 50.0})
    assert model.result() == SWEEP.replace("100e9  # top", "50e9  # top")


def test_sweep_insertions_keep_the_loop_indentation():
    model = ScriptModel(SWEEP)
    old = [{"portnumber": 1, "voltage": 1.0, "port_Z0": 50.0, "source_layernum": 201,
            "target_layername": "TopMetal2", "direction": "x"}]
    new = old + [dict(old[0], portnumber=2, source_layernum=202)]
    written, refused = patch_script(model, {}, {"meshsize_max": 70}, baseline_ports=old, current_ports=new)
    assert refused == []
    after = model.result()
    assert "    settings['fstop'] = 100e9  # top\n    settings['meshsize_max'] = 70\n" in after
    assert ("\n    ports.add_port(simulation_setup.simulation_port(portnumber=2, voltage=1, port_Z0=50, "
            "source_layernum=202, target_layername='TopMetal2', direction='x'))\n") in after
    assert len(ScriptModel(after).ports) == 2


def test_loop_values_from_range_and_module_list():
    text = ("temps = [25, 85, 125]\nsettings = {}\n"
            "for n in range(2, 5):\n    settings['order'] = n\n"
            "for T in temps:\n    settings['margin'] = 2*T\n"
            "for x in compute():\n    settings['fstop'] = x\n")
    model = ScriptModel(text)
    assert model.site("order").value == 2 and model.site("order").reason == "swept: n = 2, 3, 4"
    assert model.site("margin").value == 50 and model.site("margin").reason == "swept: T = 25, 85, 125"
    # values the script computes at run time stay unknown
    assert not model.site("fstop").resolved and model.site("fstop").reason == "uses x"


def test_removing_the_only_statement_of_a_block_is_refused():
    text = ("for T in [1]:\n"
            "    ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1, port_Z0=50, "
            "source_layernum=201, target_layername='TopMetal2', direction='x'))\n")
    model = ScriptModel(text)
    old = [{"portnumber": 1, "voltage": 1.0, "port_Z0": 50.0, "source_layernum": 201,
            "target_layername": "TopMetal2", "direction": "x"}]
    written, refused = patch_script(model, {}, {}, baseline_ports=old, current_ports=[])
    assert written == [] and refused and model.result() == text


def test_variable_assigned_twice_is_read_only():
    text = "settings = {}\nfstop = 1e9\nfstop = 2e9\nsettings['fstop'] = fstop\n"
    assert not ScriptModel(text).site("fstop").writable


def test_value_through_variable_is_edited_at_the_variable():
    text = "fstop = 10e9  # stop\nsettings = {}\nsettings['fstop'] = fstop\n"
    model = ScriptModel(text)
    patch_script(model, {"fstop": 10.0}, {"fstop": 12.0})
    assert model.result() == "fstop = 12e9  # stop\nsettings = {}\nsettings['fstop'] = fstop\n"


def test_ports_in_loops_are_not_touched():
    text = ("ports = simulation_setup.all_simulation_ports()\n"
            "for n in range(2):\n"
            "    ports.add_port(simulation_setup.simulation_port(portnumber=n+1, voltage=1, port_Z0=50, "
            "source_layernum=201+n, target_layername='TopMetal2', direction='x'))\n")
    model = ScriptModel(text)
    assert len(model.ports) == 1 and not model.ports[0].static


# ---------------------------------------------------------------------------
# ports
# ---------------------------------------------------------------------------

def gui_ports(model):
    ports = []
    for cs in model.ports:
        port = dict(cs.args)
        port["voltage"] = float(port["voltage"])
        port["port_Z0"] = float(port["port_Z0"])
        ports.append(port)
    return sorted(ports, key=lambda p: p["portnumber"])


def test_port_value_edit_keeps_rest_of_the_call():
    path = os.path.join(FIXTURES, "generated_setupEM_palace.py")
    model = load(path)
    old = gui_ports(model)
    new = [dict(p) for p in old]
    new[1]["port_Z0"] = 25.0
    written, refused = patch_script(model, {}, {}, baseline_ports=old, current_ports=new)
    assert written == ["port 2"] and refused == []
    lines = changed_lines(model.text, model.result())
    assert len(lines) == 2 and "port_Z0=25," in lines[1]
    assert "voltage=1.0" in lines[1]          # unchanged values keep their spelling


def test_direction_case_is_not_a_change():
    text = ("ports = simulation_setup.all_simulation_ports()\n"
            "ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1, port_Z0=50, "
            "source_layernum=201, from_layername='Metal1', to_layername='TopMetal2', direction='z'))\n")
    model = ScriptModel(text)
    old = [{"portnumber": 1, "voltage": 1.0, "port_Z0": 50.0, "source_layernum": 201,
            "from_layername": "Metal1", "to_layername": "TopMetal2", "direction": "Z"}]
    new = [dict(old[0], port_Z0=25.0)]
    patch_script(model, {}, {}, baseline_ports=old, current_ports=new)
    assert model.result() == text.replace("port_Z0=50", "port_Z0=25")


def test_port_direction_change_rewrites_arguments():
    path = os.path.join(FIXTURES, "generated_setupEM_palace.py")
    model = load(path)
    old = gui_ports(model)
    new = [dict(p) for p in old]
    new[0] = {"portnumber": 1, "voltage": 1.0, "port_Z0": 50.0, "source_layernum": 201,
              "target_layername": "TopMetal2", "direction": "x"}
    patch_script(model, {}, {}, baseline_ports=old, current_ports=new)
    after = model.result()
    # unchanged values keep their spelling (1.0, 50.0), the keywords follow the new direction
    assert ("simulation_ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1.0, port_Z0=50.0, "
            "source_layernum=201, target_layername='TopMetal2', direction='x'))") in after
    assert ScriptModel(after).ports[0].args["target_layername"] == "TopMetal2"


def test_port_add_and_remove():
    path = os.path.join(FIXTURES, "palace_butlermatrix.py")
    model = load(path)
    old = gui_ports(model)
    new = [p for p in old if p["portnumber"] != 8]
    new.append({"portnumber": 9, "voltage": 1.0, "port_Z0": 50.0, "source_layernum": 209,
                "target_layername": "TopMetal2", "direction": "x"})
    written, refused = patch_script(model, {}, {}, baseline_ports=old, current_ports=new)
    assert refused == [] and sorted(written) == ["port 8", "port 9"]
    after = ScriptModel(model.result())
    assert sorted(int(p.args["portnumber"]) for p in after.ports) == [1, 2, 3, 4, 5, 6, 7, 9]


def test_multiline_port_call():
    text = ("ports = simulation_setup.all_simulation_ports()\n"
            "ports.add_port(simulation_setup.simulation_port(portnumber=1,  # first\n"
            "                                                 voltage=1, port_Z0=50,\n"
            "                                                 source_layernum=201,\n"
            "                                                 target_layername='TopMetal2', direction='x'))\n")
    model = ScriptModel(text)
    old = [{"portnumber": 1, "voltage": 1.0, "port_Z0": 50.0, "source_layernum": 201,
            "target_layername": "TopMetal2", "direction": "x"}]
    new = [dict(old[0], source_layernum=205)]
    patch_script(model, {}, {}, baseline_ports=old, current_ports=new)
    assert model.result() == text.replace("source_layernum=201", "source_layernum=205")


# ---------------------------------------------------------------------------
# thermal objects
# ---------------------------------------------------------------------------

def gui_thermal(model):
    objs = []
    for kind in ("heatsource", "constanttemp"):
        for cs in model.thermal:
            if cs.kind == kind:
                objs.append(dict(cs.args, type=kind))
    return objs


@pytest.mark.parametrize("path", THERMAL_FIXTURES, ids=os.path.basename)
def test_thermal_edit_add_remove(path):
    model = load(path)
    old = gui_thermal(model)
    new = [dict(old[0], power=1.5), dict(old[1]),
           {"type": "heatsource", "power": 0.1, "source_layernum": 203, "target_layername": "TFR"}]
    written, refused = patch_script(model, {}, {}, baseline_thermal=old, current_thermal=new)
    assert refused == []
    after = ScriptModel(model.result())
    kinds = [(c.kind, c.args.get("power", c.args.get("temp"))) for c in after.thermal]
    assert ("heatsource", 1.5) in kinds and ("heatsource", 0.1) in kinds and len(kinds) == 3

    model = load(path)
    patch_script(model, {}, {}, baseline_thermal=old, current_thermal=old[:1])
    assert [c.kind for c in ScriptModel(model.result()).thermal] == ["heatsource"]


# ---------------------------------------------------------------------------
# simulator switch
# ---------------------------------------------------------------------------

def test_palace_to_elmer_renames_create_call():
    path = os.path.join(FIXTURES, "inductor_500pH_2port.py")
    model = load(path)
    patch_script(model, {}, {}, tool_change="create_elmer")
    after = model.result()
    assert len(changed_lines(model.text, after)) == 2
    assert ScriptModel(after).tool == "elmer"


def test_refused_edit_leaves_text_untouched():
    model = ScriptModel("settings = {}\nfor f in [1]:\n    settings['fstop'] = f\n")
    with pytest.raises(Refused):
        model.set_value("fstop", "2e9")
    assert not model.changed
