# GUI test (offscreen): setupThermal's "Load powers..." on a thermal export of the KLayout EM Export plug-in. The
# powers of a circuit simulation are matched to the heat sources by instance name through <layout>_thermalmap.json
# (gds2palace thermal_map); what does not match is reported, not set. The table names the device of each heat source
# and the region outline, and a heat source layer past 299 (many devices) counts as present in the layout. The export's
# placeholder boundary (no target layer) is listed as such and stops Create Model until a target is chosen.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
G2P = os.path.join(HERE, "..", "..", "gds2palace_ihp_sg13g2")
XML = os.path.join(G2P, "XML_stackup", "legacy", "SG13G2_200um.xml")
pytestmark = pytest.mark.skipif(not os.path.isfile(XML), reason="needs gds2palace_ihp_sg13g2 next to this repo")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

LAYERS = [202, 203, 204, 205, 301]      # 301: a heat source past 299
NAMES = ["R1", "R2", "R3", "XCORE.Q1", "XCORE.Q2"]


@pytest.fixture
def app(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Ok)
    return application


@pytest.fixture
def export(tmp_path):
    """A thermal export as the plug-in writes it: GDS (outline on 201, a box per heat source), .tsimcfg, map."""
    import gdspy
    lib = gdspy.GdsLibrary()
    cell = lib.add(gdspy.Cell("cascode", exclude_from_current=True)).cells["cascode"]
    cell.add(gdspy.Rectangle((0, 0), (100, 100), layer=201))
    for n, layer in enumerate(LAYERS):
        cell.add(gdspy.Rectangle((10 * n, 10), (10 * n + 5, 15), layer=layer))
    gds = tmp_path / "cascode_thermal.gds"
    lib.write_gds(str(gds))
    sources = [{"layer": layer, "device": f"dev{n}@{n},0", "pcell": "npn13G2" if "Q" in name else "rhigh",
                "instance": name, "lvs_name": "CORE." + name[-1] if "CORE" in name else name[1:]}
               for n, (layer, name) in enumerate(zip(LAYERS, NAMES))]
    (tmp_path / "cascode_thermal_thermalmap.json").write_text(json.dumps({"format": 1, "outline_layer": 201,
                                                                                 "sources": sources}))
    tsimcfg = tmp_path / "cascode_thermal.tsimcfg"
    tsimcfg.write_text(json.dumps({
        "application": "setupThermal", "data_format": "1.0",
        "saved_values": {"unit": 1e-06, "GdsFile": str(gds), "SubstrateFile": os.path.abspath(XML),
                         "cellname": "cascode"},
        "thermal": [{"source_layernum": layer, "target_layername": "Activ", "type": "source", "power": 0.0}
                    for layer in LAYERS] +
                   [{"source_layernum": 201, "target_layername": "Activ", "type": "constanttemp", "temp": 300.0}]}))
    return tsimcfg


def open_window(monkeypatch, tsimcfg):
    import setupEM.setupThermal as setupthermal
    shown = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda parent, title, text, *a: shown.append(text)))
    win = setupthermal.MainWindow()
    monkeypatch.setattr(win, "_add_recent_file", lambda *a: None)
    win.load_configuration_from_file(str(tsimcfg))
    return win, setupthermal, shown


def load(app, monkeypatch, tsimcfg, powers_text):
    win, setupthermal, shown = open_window(monkeypatch, tsimcfg)
    powers = tsimcfg.parent / "powers.csv"
    powers.write_text(powers_text)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(powers), "")))
    shown.clear()
    win.thermal_tab.load_powers()
    return win, setupthermal, shown


def column(win, col):
    table = win.thermal_tab.thermalobjectslist
    return [table.item(r, col).text() if table.item(r, col) else "" for r in range(len(LAYERS) + 1)]


def test_load_powers(app, monkeypatch, export):
    win, setupthermal, shown = load(app, monkeypatch, export,
                                    "instance,power_W\nR1,0.12m\nxcore.q1 2.5mW\nCORE.2;3e-3\nR3,1u\nR3,2u\nQ9,1m\n")
    assert column(win, 3) == ["0.00012", "0.0", "0.0", "0.0025", "0.003", ""]
    text = shown[0]
    assert "3 of 5 heat source(s) got a power" in text
    assert "without a power (unchanged): R2\n" in text and "match no heat source: Q9" in text
    assert "several powers (none used): R3: R3, R3" in text
    assert win.thermal_tab.save_values()
    powers = {o.source_layernum: o.power for o in setupthermal.thermal_objects.objects if o.type == "source"}
    assert powers == {202: 0.00012, 203: 0.0, 204: 0.0, 205: 0.0025, 301: 0.003}
    win._script_dirty = False
    win.close()


def test_device_names_and_layers_past_299(app, monkeypatch, export):
    win, _, shown = load(app, monkeypatch, export, "\n".join(f"{n},1m" for n in NAMES))
    assert shown == ["5 of 5 heat source(s) got a power."]
    assert column(win, 5) == ["R1 (rhigh)", "R2 (rhigh)", "R3 (rhigh)", "XCORE.Q1 (npn13G2)", "XCORE.Q2 (npn13G2)",
                              "region outline"]
    win._script_dirty = False
    win.close()


def test_placeholder_boundary(app, monkeypatch, export):
    cfg = json.loads(export.read_text())
    cfg["thermal"][-1]["target_layername"] = ""         # as the export writes it
    export.write_text(json.dumps(cfg))
    win, setupthermal, shown = open_window(monkeypatch, export)
    tab = win.thermal_tab
    boundary = len(LAYERS)
    assert column(win, 1)[boundary] == "" and column(win, 5)[boundary] == "region outline: choose the target layer"
    tab.thermalobjectslist.selectRow(boundary)
    assert tab.target_box.currentIndex() == -1, "an unchosen target is shown as such, not as the first layer"
    started = []
    monkeypatch.setattr(win.create_model_tab.process, "start", lambda *a: started.append(a))
    shown.clear()
    win.create_model_tab.create_model()
    assert not started and len(shown) == 1 and "boundary on layer 201 has no target layer" in shown[0]
    tab.target_box.setCurrentIndex(tab.target_box.findText("Activ"))
    tab.apply_port_values_to_table()
    assert column(win, 1)[boundary] == "Activ" and column(win, 5)[boundary] == "region outline"
    win._script_dirty = False
    win.close()

def test_elmer_environment(monkeypatch):
    """ElmerSolver runs with hwloc's GL plug-in off (it hangs probing a display that does not accept connections),
    unless HWLOC_COMPONENTS is set already."""
    import setupEM.setupThermal as setupthermal
    monkeypatch.delenv("HWLOC_COMPONENTS", raising=False)
    assert setupthermal.elmer_environment().value("HWLOC_COMPONENTS") == "-gl"
    monkeypatch.setenv("HWLOC_COMPONENTS", "-cuda")
    assert setupthermal.elmer_environment().value("HWLOC_COMPONENTS") == "-cuda"


def test_create_model_writes_config(app, monkeypatch, export, tmp_path):
    """Create Model writes <model>.tsimcfg next to the model script (the settings of Save Config, written_with the
    script): the layout tool's next export keeps them, the KLayout plug-in finds the layout's stackup and thermal
    objects. Opening it continues the script without asking; a foreign file of that name is not overwritten."""
    win, setupthermal, shown = open_window(monkeypatch, export)
    tab = win.create_model_tab
    monkeypatch.setattr(tab.process, "start", lambda *a: None)
    tab.create_model()
    # the model goes where Create Model puts it: here next to the export's .tsimcfg, which it updates
    import pathlib
    folder, name = pathlib.Path(win.saved_values["sim_path"]), win.saved_values["model_basename"]
    script, config = folder / (name + ".py"), folder / (name + ".tsimcfg")
    assert script.is_file() and config.is_file(), (shown, tab.log_area.toPlainText()[-600:])
    data = json.loads(config.read_text())
    assert data["application"] == "setupThermal" and data["written_with"] == name + ".py"
    assert sorted(t["source_layernum"] for t in data["thermal"]) == sorted(LAYERS + [201])
    assert "Settings saved with it" in tab.log_area.toPlainText()

    asked = []
    monkeypatch.setattr(type(win), "_ask_continue_with_script", lambda self, *a: asked.append(a) or "cancel")
    win.load_configuration_from_file(str(config))
    assert not asked, "setupThermal loads the config as before (the script-or-config question is setupEM's)"

    config.write_text(json.dumps({"application": "something else"}))
    tab.create_model()
    assert json.loads(config.read_text()) == {"application": "something else"}, "a foreign file stays"
    win._script_dirty = False
    win.close()


def test_refined_cellsize_override(app, monkeypatch, export):
    """The Mesh tab's Advanced... sets a mesh size per heat source (source_<layer>) or metal layer. It is written to
    the model script only when set, and comes back from the config file."""
    win, setupthermal, shown = open_window(monkeypatch, export)
    chosen = {}

    class FakeDialog:
        def __init__(self, parent, layer_choices, current_overrides, info_text=""):
            chosen["choices"], chosen["current"] = layer_choices, current_overrides

        def exec(self):
            return setupthermal.QDialog.Accepted

        def get_overrides(self):
            return chosen["result"]

    monkeypatch.setattr(setupthermal, "RefinedCellsizeOverrideDialog", FakeDialog)
    mesh = win.mesh_tab
    chosen["result"] = [["source_205", 0.25], ["Metal1", 1.0]]
    mesh.open_refined_cellsize_override_dialog()
    assert chosen["choices"][:len(LAYERS)] == [f"source_{layer}" for layer in LAYERS], "heat sources, not boundaries"
    assert "Metal1" in chosen["choices"] and mesh.refined_override_btn.text() == "Advanced... (2)"

    assert win.save_all_tabs()
    win.modeleditor_tab.create_model_text()
    script = win.modeleditor_tab.model_edit.toPlainText()
    assert "settings['refined_cellsize_override'] = [['source_205', 0.25], ['Metal1', 1.0]]" in script

    win.saved_values["refined_cellsize_override"] = [["source_301", 0.5]]
    mesh.load_values()
    assert mesh.refined_override_btn.text() == "Advanced... (1)"

    chosen["result"] = []
    mesh.open_refined_cellsize_override_dialog()
    assert chosen["current"] == [["source_301", 0.5]] and mesh.refined_override_btn.text() == "Advanced..."
    assert win.save_all_tabs() and "refined_cellsize_override" not in win.saved_values
    win.modeleditor_tab.model_edit.clear()
    win.modeleditor_tab.create_model_text()
    assert "settings['refined_cellsize_override']" not in win.modeleditor_tab.model_edit.toPlainText()
    win._script_dirty = False
    win.close()


def test_default_purpose(app, monkeypatch):
    """Without a saved preference, setupThermal reads datatypes 0 and 20 (IHP's Activ.mask, the silicon around each
    npn emitter), setupEM only 0."""
    import setupEM.setup_common as common
    import setupEM.setupEM as setupem
    import setupEM.setupThermal as setupthermal
    monkeypatch.setattr(common, "get_preference", lambda app_name, key, default=None: default)
    for module, expected in ((setupthermal, [0, 20]), (setupem, [0])):
        win = module.MainWindow()
        win.saved_values.pop("purpose", None)
        win.file_tab.load_values()
        assert win.file_tab.purpose_edit.text() == ", ".join(map(str, expected)), module.__name__
        win.file_tab.purpose_edit.setText("")
        win.file_tab.save_values()
        assert win.saved_values["purpose"] == expected, module.__name__
        win._script_dirty = False
        win.close()
