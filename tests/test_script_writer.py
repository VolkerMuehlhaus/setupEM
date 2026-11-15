# The model script that setupEM / setupThermal write (Code tab, File > Export, Create Model), from settings, ports
# and solver mode. Cases are loaded into the GUI offscreen; the script text and the inputs it was made from are
# compared with tests/golden_scripts/. UPDATE_GOLDEN=1 rewrites the records after an intended change.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
GOLDEN = os.path.join(HERE, "golden_scripts")
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


def via(n, layer, z0=50, voltage=1, direction="z"):
    return {"portnumber": n, "source_layernum": layer, "target_layername": None, "from_layername": "Metal1",
            "to_layername": "TopMetal2", "direction": direction, "port_Z0": z0, "voltage": voltage}


def inplane(n, layer, direction="x", target="TopMetal2"):
    return {"portnumber": n, "source_layernum": layer, "target_layername": target, "from_layername": None,
            "to_layername": None, "direction": direction, "port_Z0": 50, "voltage": 1}


INPUT_FILES = {"GdsFile": "D:/designs/chip.gds", "SubstrateFile": "D:/stackups/SG13G2_FEM_200um.xml",
               "cellname": "", "purpose": [0], "preprocess_gds": False, "merge_polygon_size": 0}

# name: (application, elmer mode, settings put into the tabs, ports or thermal objects)
CASES = {
    "palace_basic": ("setupEM", False, dict(INPUT_FILES, fstart=0.0, fstop=50.0, fstep=1.0),
                     [via(1, 201), via(2, 202, voltage=0)]),
    "palace_amr_skin": ("setupEM", False,
                        dict(INPUT_FILES, fstart=1.0, fstop=110.0, fstep=0.5, fpoint=[24.0], fdump=[60.0],
                             adaptive_mesh_iterations=2, amr_tol=0.02, conductor_model="solve_inside_skin",
                             margin=[10.0, 20.0, 30.0, 40.0], refined_cellsize=2.0, order=2,
                             merge_polygon_size=1.0, fill_factor_correction=True),
                        [inplane(1, 201), inplane(2, 202, direction="-x"), via(3, 203, direction="-z")]),
    "elmer_fdump": ("setupEM", True, dict(INPUT_FILES, fstart=1.0, fstop=20.0, fstep=1.0, fdump_enabled=True,
                                          iterative=True, ELMER_MPI_THREADS=4),
                    [via(1, 201), via(2, 202)]),
    "thermal": ("setupThermal", False, dict(INPUT_FILES, refined_cellsize=1.0, margin=100.0),
                [{"source_layernum": 401, "target_layername": "HEAT_TRANSISTOR", "type": "source", "power": 0.05},
                 {"source_layernum": 402, "target_layername": "HEAT_RESISTOR", "type": "source", "power": 0.01},
                 {"source_layernum": 250, "target_layername": "BACKSIDEGND", "type": "constanttemp", "temp": 300.0}]),
}
PARAMS = [(name, export) for name in CASES for export in (True, False)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def golden_path(name, export):
    return os.path.join(GOLDEN, f"{name}_{'export' if export else 'model'}.json")


def gui_script(monkeypatch, name, export):
    """Load a case into the GUI and return (inputs, text) of its script."""
    application, elmer_mode, values, objects = CASES[name]
    module = __import__(f"setupEM.{application}", fromlist=["MainWindow"])
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    for kind in ("warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    window = module.MainWindow()
    try:
        data = {"application": application, "saved_values": dict(values)}
        data["thermal" if application == "setupThermal" else "ports"] = objects
        if application == "setupEM":
            data["elmer_mode"] = elmer_mode
        module.saved_values.clear()
        module.saved_values.update(values)
        window.apply_native_config_data(data)
        window.load_all_tabs()
        window.modeleditor_tab.create_model_text(forExport=export)
        text = window.modeleditor_tab.model_edit.toPlainText()
        inputs = {"application": application, "elmer_mode": elmer_mode, "for_export": export,
                  "saved_values": json.loads(json.dumps(module.saved_values)),
                  **window.native_config_extra_struct()}
        return inputs, text
    finally:
        window._script_dirty = False
        window.close()


@pytest.mark.parametrize("name,export", PARAMS)
def test_gui_script_matches_golden(monkeypatch, app, name, export):
    inputs, text = gui_script(monkeypatch, name, export)
    path = golden_path(name, export)
    if UPDATE or not os.path.exists(path):
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"inputs": inputs, "text": text}, f, indent=1)
        if not UPDATE:
            pytest.skip(f"golden record written: {os.path.basename(path)}")
    with open(path, encoding="utf-8") as f:
        golden = json.load(f)
    assert inputs == golden["inputs"]
    assert text == golden["text"]


@pytest.mark.parametrize("name,export", PARAMS)
def test_script_writer_matches_golden(name, export):
    """The Qt-free module gives the same script from the same inputs as the GUI."""
    from setupEM import script_writer
    with open(golden_path(name, export), encoding="utf-8") as f:
        golden = json.load(f)
    inputs = golden["inputs"]
    if inputs["application"] == "setupThermal":
        text = script_writer.thermal_script_text(
            inputs["saved_values"], script_writer.thermal_objects_from_struct(inputs["thermal"]),
            for_export=inputs["for_export"])
    else:
        text = script_writer.em_script_text(
            inputs["saved_values"], script_writer.ports_from_struct(inputs["ports"]), inputs["elmer_mode"],
            for_export=inputs["for_export"])
    assert text == golden["text"]


def test_ports_from_struct_refuses_what_the_ports_tab_refuses():
    from setupEM import script_writer
    with pytest.raises(ValueError, match="Port 2 is incomplete"):
        script_writer.ports_from_struct([via(1, 201), dict(via(2, 202), to_layername="")])
    with pytest.raises(ValueError, match="Invalid input for port 1"):
        script_writer.ports_from_struct([dict(via(1, 201), source_layernum="abc")])
    with pytest.raises(ValueError, match="Invalid input in row 1"):
        script_writer.thermal_objects_from_struct([{"source_layernum": 401, "type": "source"}])
    ports = script_writer.ports_from_struct([via(2, 202), via(1, 201)])
    assert [p.portnumber for p in ports.ports] == [1, 2], "in the order of their numbers, as the table rows"
