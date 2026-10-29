# Tests for Import from *.py model by running the script (src/setupEM/script_probe.py).
#
# Run from the repo root:   python -m pytest tests
#
# Each test writes a small model script into a temp folder. read_gds() is not
# really called, so no GDSII file is needed; the stackup is one that ships
# with gds2palace (utilities.get_stackup_path()).

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM import script_model  # noqa: E402
from setupEM.script_model import ScriptModel  # noqa: E402
from setupEM.script_probe import probe_script  # noqa: E402

if script_model._bundled_stackup("SG13G2_FEM_200um.xml") is None:
    pytest.skip("the installed gds2palace doesn't ship SG13G2_FEM_200um.xml", allow_module_level=True)

HEAD = '''import os
from gds2palace import *
XML_filename = utilities.get_stackup_path("SG13G2_FEM_200um.xml")
script_path = utilities.get_script_path(__file__)
model_basename = utilities.get_basename(__file__)
sim_path = utilities.create_sim_path(script_path, model_basename)
'''

READ = '''materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate(
    XML_filename, variable_overrides={'total_thickness': 300})
layernumbers = metals_list.getlayernumbers()
layernumbers.extend(simulation_ports.portlayers)
allpolygons = gds_reader.read_gds("layout.gds", layernumbers, purposelist=[0], metals_list=metals_list,
                                  merge_polygon_size=2, cellname="TOP")
settings['simulation_ports'] = simulation_ports
settings['allpolygons'] = allpolygons
settings['sim_path'] = sim_path
settings['model_basename'] = model_basename
'''


def write(tmp_path, body, name="model.py"):
    path = tmp_path / name
    path.write_text(HEAD + body, encoding="utf-8")
    return str(path)


def test_ports_from_a_loop_and_computed_values(tmp_path):
    # the gds2palace_mcp style: ports made in a loop, a value from another variable
    path = write(tmp_path, '''f0 = 50e9
settings = {}
settings['fstart'] = 0
settings['fstop'] = 2*f0
settings['refined_cellsize'] = 3
simulation_ports = simulation_setup.all_simulation_ports()
for number, layer in ((1, 201), (2, 202), (3, 203), (4, 204)):
    if number == 3:
        continue
    simulation_ports.add_port(simulation_setup.simulation_port(
        portnumber=number, voltage=1, port_Z0=50, source_layernum=layer,
        from_layername='Metal3', to_layername='TopMetal2', direction='z'))
''' + READ + '''simulation_setup.create_palace(simulation_ports.all_active_excitations(), settings)
''')
    result = probe_script(path)
    assert result.ok, result.error
    assert result.create_call == "create_palace"
    assert [p["portnumber"] for p in result.ports] == [1, 2, 4]
    assert result.ports[2] == {"portnumber": 4, "voltage": 1, "port_Z0": 50, "source_layernum": 204,
                               "from_layername": "Metal3", "to_layername": "TopMetal2", "direction": "z"}
    assert result.values["fstop"] == 100e9 and result.values["refined_cellsize"] == 3
    # arguments of read_gds() / read_substrate(), the GDSII path made absolute
    assert result.values["GdsFile"] == str(tmp_path / "layout.gds")
    assert result.values["SubstrateFile"] == script_model._bundled_stackup("SG13G2_FEM_200um.xml")
    assert result.values["cellname"] == "TOP" and result.values["purpose"] == [0]
    assert result.values["merge_polygon_size"] == 2
    assert result.values["variable_overrides"] == {"total_thickness": 300}


def test_nothing_after_the_model_runs_and_no_folders(tmp_path):
    path = write(tmp_path, '''settings = {}
simulation_ports = simulation_setup.all_simulation_ports()
simulation_ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1, port_Z0=50,
    source_layernum=201, target_layername='TopMetal2', direction='x'))
''' + READ + '''simulation_setup.create_palace(simulation_ports.all_active_excitations(), settings)
utilities.create_run_script(sim_path)
open(os.path.join(script_path, "solver_started.txt"), "w").write("start_simulation")
''')
    result = probe_script(path)
    assert result.ok, result.error
    assert not (tmp_path / "palace_model").exists()
    assert not (tmp_path / "solver_started.txt").exists()


def test_thermal_objects_from_a_loop(tmp_path):
    path = write(tmp_path, '''settings = {}
thermal_objects = simulation_setup.all_thermal_objects()
for layer, power in ((201, 0.1), (202, 0.2)):
    thermal_objects.add_heatsource(simulation_setup.heatsource(power=power, source_layernum=layer,
                                                                target_layername='TopMetal2'))
thermal_objects.add_consttemp(simulation_setup.constanttemp(temp=300, source_layernum=210,
                                                             target_layername='TopMetal1'))
simulation_ports = simulation_setup.all_simulation_ports()
''' + READ + '''settings['thermal_objects'] = thermal_objects
simulation_setup.create_elmer_thermal(settings)
''')
    result = probe_script(path)
    assert result.ok, result.error
    assert result.create_call == "create_elmer_thermal"
    assert [kind for kind, _args in result.thermal] == ["heatsource", "heatsource", "constanttemp"]
    assert result.thermal[1][1] == {"power": 0.2, "source_layernum": 202, "target_layername": "TopMetal2"}


def test_a_sweep_gives_its_first_model(tmp_path):
    path = write(tmp_path, '''simulation_ports = simulation_setup.all_simulation_ports()
for fstop in (50e9, 100e9):
    settings = {}
    settings['fstop'] = fstop
''' + "".join("    " + line + "\n" for line in READ.splitlines()) +
                 '''    simulation_setup.create_palace(simulation_ports.all_active_excitations(), settings)
''')
    result = probe_script(path)
    assert result.ok, result.error
    assert result.values["fstop"] == 50e9
    assert ScriptModel.from_file(path).builds_in_loop


def test_read_gds_with_different_files_is_left_out(tmp_path):
    path = write(tmp_path, '''settings = {}
simulation_ports = simulation_setup.all_simulation_ports()
''' + READ + '''more = gds_reader.read_gds("other.gds", layernumbers, purposelist=[0], metals_list=metals_list)
simulation_setup.create_palace(simulation_ports.all_active_excitations(), settings)
''')
    result = probe_script(path)
    assert result.ok, result.error
    assert "GdsFile" not in result.values
    assert ("GdsFile", "read_gds() is called 2 times with different values") in [tuple(n) for n in result.notes]


def test_a_script_error_is_reported(tmp_path):
    path = write(tmp_path, "raise ValueError('no layout for this corner')\n")
    result = probe_script(path)
    assert not result.ok
    assert result.error == "ValueError: no layout for this corner"


def test_an_exit_says_what_was_printed_last(tmp_path):
    path = write(tmp_path, '''print("Stackup file not found: missing.xml")
exit(1)
''')
    result = probe_script(path)
    assert result.error == "it stopped (exit 1) before building the model: Stackup file not found: missing.xml"


def test_no_model_built(tmp_path):
    path = write(tmp_path, "settings = {}\n")
    assert probe_script(path).error.startswith("it doesn't call create_palace()")


def test_a_script_that_runs_too_long(tmp_path):
    path = write(tmp_path, "while True:\n    pass\n")
    assert probe_script(path, timeout=3).error == "it ran longer than 3 s"
