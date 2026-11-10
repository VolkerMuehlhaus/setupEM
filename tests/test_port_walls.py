# Port walls in setupEM: the box side type "Port wall" (Simulation Box Sides dialog, Mesh and Boundaries tab), the
# Ports tab's Port walls group and De-embedding column, the question for a port on an edge that is not a port wall,
# the script, and the layout preview's box. Needs gds2palace 0.10.0 or later.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import gdspy
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from gds2palace import utilities  # noqa: E402

pytestmark = pytest.mark.skipif(not hasattr(__import__("gds2palace"), "calibration"),
                                reason="needs gds2palace 0.10.0 with port walls")
STACKUP = utilities.get_stackup_path("SG13G2_FEM_200um.xml")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    shown = []
    for kind in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda parent, title, text, *a, k=kind: shown.append((k, text))))
    return shown


def answer(monkeypatch, button_text):
    """the next question boxes are answered with this button"""
    asked = []

    def exec_(box):
        asked.append(box.text())
        box._chosen = next(b for b in box.buttons() if b.text() == button_text)
        return 0
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box._chosen)
    return asked


def gssg_gds(path, stub_on_ymax=False):
    """GSSG lines from the xmin edge, ports 1 um deep on 201/202; optionally a stub with a port on the ymax edge"""
    lib = gdspy.GdsLibrary()
    cell = gdspy.Cell("line", exclude_from_current=True)
    lib.add(cell)
    for k, (y0, y1) in enumerate([(-15, -5), (5, 15)]):
        cell.add(gdspy.Rectangle((0, y0), (300, y1), layer=134))
        cell.add(gdspy.Rectangle((0, y0), (1, y1), layer=201 + k))
    for y0, y1 in ((-65, -25), (25, 65)):
        cell.add(gdspy.Rectangle((0, y0), (300, y1), layer=134))
    if stub_on_ymax:
        cell.add(gdspy.Rectangle((150, 65), (160, 80), layer=134))
        cell.add(gdspy.Rectangle((150, 79), (160, 80), layer=203))
    lib.write_gds(str(path))
    return str(path)


def open_window(monkeypatch, gds=None, ports=(1, 2)):
    module = __import__("setupEM.setupEM", fromlist=["MainWindow"])
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    module.saved_values.clear()             # module-wide: no settings from an earlier test
    window = module.MainWindow()
    if gds:
        window.file_tab.gds_file_edit.setText(gds)
        window.file_tab.XML_file_edit.setText(STACKUP)
        assert window.file_tab.save_values()
        window.frequencies_tab.start_edit.setText("0")
        window.frequencies_tab.stop_edit.setText("100")
        window.frequencies_tab.step_edit.setText("1")
        window.ports_tab.update_port_from_import([
            {"portnumber": n, "port_Z0": 50, "voltage": 1, "source_layernum": 200 + n, "target_layername": "TopMetal2",
             "direction": "X"} for n in ports])
    return window, module


def close_window(window):
    window._script_dirty = False
    window.close()


# ---------- the box side type ----------

def test_dialog_offers_port_wall_on_x_and_y_sides_and_locks_the_cells(monkeypatch, app, messages):
    window, module = open_window(monkeypatch)
    dialog = module.BoxSidesOverrideDialog(None, "Absorbing", "50", {}, default_margin_text="100", allow_port_wall=True)
    for side in ("xmin", "ymax"):
        assert dialog._bc_boxes[side].findData("PORTWALL") >= 0
    assert dialog._bc_boxes["zmin"].findData("PORTWALL") < 0
    combo = dialog._bc_boxes["xmin"]
    combo.setCurrentIndex(combo.findData("PORTWALL"))
    assert dialog._margin_edits["xmin"].text() == "0" and not dialog._margin_edits["xmin"].isEnabled()
    assert dialog._air_edits["xmin"].text() == "0" and not dialog._air_edits["xmin"].isEnabled()
    dialog._on_ok()
    assert dialog.get_overrides() == {"xmin": {"bc": "PORTWALL", "margin": 0.0, "air": 0.0}}
    combo.setCurrentIndex(combo.findData("PEC"))        # back to a plain side: unlocked and empty
    assert dialog._margin_edits["xmin"].isEnabled() and dialog._margin_edits["xmin"].text() == ""
    assert not messages
    close_window(window)


def test_mesh_tab_keeps_a_plain_default_and_writes_the_script(monkeypatch, app):
    window, module = open_window(monkeypatch)
    module.saved_values["boundary"] = ["PORTWALL", "PORTWALL", "PORTWALL", "PORTWALL", "PEC", "ABC"]
    module.saved_values["margin"] = [0.0, 0.0, 0.0, 0.0]
    module.saved_values["air_around"] = [0.0, 0.0, 0.0, 0.0, 0.0, 50.0]
    window.mesh_tab.load_values()
    assert window.mesh_tab.boundary_box.currentText() in ("Absorbing", "PEC")
    assert window.mesh_tab.port_wall_sides() == ["xmin", "xmax", "ymin", "ymax"]
    module.saved_values["reference_plane_shift"] = {"xmin": 30.0}
    window.ports_tab.load_values()
    assert window.mesh_tab.save_values() and window.ports_tab.save_values()
    assert module.saved_values["boundary"][:4] == ["PORTWALL"] * 4
    window.modeleditor_tab.create_model_text()
    text = window.modeleditor_tab.model_edit.toPlainText()
    assert "'PORTWALL'" in text and "settings['reference_plane_shift'] = {'xmin': 30.0}" in text
    close_window(window)


# ---------- the Ports tab ----------

def test_check_fills_the_port_wall_rows_and_the_deembedding_column(monkeypatch, app, messages, tmp_path):
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds"))
    window.mesh_tab.set_port_wall("xmin")
    assert window.ports_tab.check_port_walls(ask=True)
    table = window.ports_tab.portwall_table
    assert table.rowCount() == 1 and table.item(0, 0).text() == "xmin" and table.item(0, 1).text() == "1, 2"
    assert table.item(0, 3).text() == "50" and table.item(0, 4).text() == "200, 400"
    assert table.item(0, 8).text().startswith("OK")
    ports = window.ports_tab.portslist
    assert ports.item(0, 8).text() == "port wall xmin, calibrated" and ports.item(1, 6).text() == "X"
    window.ports_tab.portslist.selectRow(1)
    assert not window.ports_tab.direction_box.isEnabled() and "port wall xmin" in window.ports_tab.direction_note.text()
    assert not messages
    close_window(window)


def test_a_port_on_another_edge_is_offered_a_port_wall(monkeypatch, app, messages, tmp_path):
    """port 3 sits on the ymax edge; making ymax a port wall then fails the ground check, which stops the model"""
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds", stub_on_ymax=True), ports=(1, 2, 3))
    window.mesh_tab.set_port_wall("xmin")
    asked = answer(monkeypatch, "Keep as is")
    assert window.ports_tab.check_port_walls(ask=True)
    assert len(asked) == 1 and "ymax, which is not a port wall" in asked[0]
    assert "which is not a port wall: estimated port inductance" in window.ports_tab.portslist.item(2, 8).text()
    assert window.ports_tab.check_port_walls(ask=True) and len(asked) == 1, "asked once per side and session"

    window.ports_tab._keep_sides.clear()
    answer(monkeypatch, "Set port wall")
    assert not window.ports_tab.check_port_walls(ask=True)
    assert "ymax" in window.mesh_tab.port_wall_sides()
    assert messages and messages[-1][0] == "critical" and "no ground metal reaches the wall" in messages[-1][1]
    close_window(window)


def test_model_creation_reads_nothing_without_a_port_wall(monkeypatch, app, tmp_path):
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds"))
    monkeypatch.setattr(window.ports_tab, "_read_layout_for_port_walls", lambda: pytest.fail("layout read"))
    assert window.ports_tab.check_port_walls(ask=True, only_with_port_walls=True)
    close_window(window)


def test_layout_preview_draws_the_box(monkeypatch, app, messages, tmp_path):
    from setupEM.layout_preview import LayoutPreviewWindow, _ClickableBoxLabel
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds"))
    window.mesh_tab.set_port_wall("xmin")
    opened = []
    monkeypatch.setattr(window, "open_box_sides_dialog", lambda: opened.append(1))   # before drawing: the labels keep it
    preview = LayoutPreviewWindow(window)
    preview.refresh()
    labels = [i.text() for i in preview._box_items if isinstance(i, _ClickableBoxLabel)]
    assert "port wall" in labels and not any(t.startswith(("xmin", "xmax", "ymin", "ymax")) for t in labels)
    assert any(text.startswith("Absorbing, oversize") for text in labels)
    # a boundary symbol per side, before its label
    from PySide6.QtWidgets import QGraphicsPixmapItem
    assert len([i for i in preview._box_items if isinstance(i, QGraphicsPixmapItem)]) == 4
    next(i for i in preview._box_items if isinstance(i, _ClickableBoxLabel)).mouseReleaseEvent(
        type("E", (), {"accept": lambda self: None})())
    assert opened == [1]
    preview.close()
    close_window(window)


def test_a_via_port_row_keeps_topmetal2_as_target(monkeypatch, app, tmp_path):
    """showing a via port row must not reset the target box to the first layer (LBE): switching that port to in-plane
    would then silently pick a dielectric layer"""
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds"))
    window.ports_tab.update_port_from_import([
        {"portnumber": 1, "port_Z0": 50, "voltage": 1, "source_layernum": 201, "from_layername": "Metal3",
         "to_layername": "TopMetal2", "direction": "Z"}])
    window.ports_tab.portslist.selectRow(0)
    assert window.ports_tab.target_box.currentText() == "TopMetal2"
    close_window(window)


def test_an_error_on_one_wall_still_checks_the_other(monkeypatch, app, messages, tmp_path):
    lib = gdspy.GdsLibrary()
    cell = gdspy.Cell("line", exclude_from_current=True)
    lib.add(cell)
    for k, (y0, y1) in enumerate([(-15, -5), (5, 15)]):
        cell.add(gdspy.Rectangle((0, y0), (300, y1), layer=134))
        cell.add(gdspy.Rectangle((0, y0), (1, y1), layer=201 + k))
        cell.add(gdspy.Rectangle((299, y0), (300, y1), layer=203 + k))
    for y0, y1 in ((-65, -25), (25, 65)):
        cell.add(gdspy.Rectangle((0, y0), (300, y1), layer=134))
    lib.write_gds(str(tmp_path / "line.gds"))
    window, module = open_window(monkeypatch, str(tmp_path / "line.gds"), ports=(1, 2, 3, 4))
    window.ports_tab.portslist.item(0, 3).setText("LBE")
    window.mesh_tab.set_port_wall("xmin")
    window.mesh_tab.set_port_wall("xmax")
    assert not window.ports_tab.check_port_walls(ask=True)
    table = window.ports_tab.portwall_table
    status = {table.item(r, 0).text(): table.item(r, 8).text() for r in range(table.rowCount())}
    assert "LBE is not a metal layer" in status["xmin"] and status["xmax"].startswith("OK")
    close_window(window)


@pytest.mark.parametrize("stackup, top", [
    (STACKUP, "TopMetal2"),
    (utilities.get_stackup_path("SG13CMOS5L_200um.xml"), "TopMetal1"),
    (os.path.join(os.path.dirname(utilities.get_stackup_path("SG13G2_FEM_200um.xml")), "..", "..", "legacy", "pcb_ro4003.xml"), "Top"),
])
def test_target_and_via_presets_use_the_topmost_metal(monkeypatch, app, stackup, top):
    """not IHP-specific: the topmost conductor of whatever stackup is loaded, for the target and the via port's upper end"""
    from gds2palace import stackup_reader
    if not os.path.isfile(stackup):
        pytest.skip("stackup not in this installation")
    window, module = open_window(monkeypatch)
    _, _, metals = stackup_reader.read_substrate(stackup)
    ports = window.ports_tab
    ports.update_layers(metals)
    assert ports.target_box.currentText() == top and ports.to_box.currentText() == top
    assert all(ports.target_box.itemText(i) not in ("LBE", "TopVia2") for i in range(ports.target_box.count()))
    ports.update_port_from_import([{"portnumber": 1, "port_Z0": 50, "voltage": 1, "source_layernum": 201,
                                    "from_layername": metals.metals[0].name, "to_layername": top, "direction": "Z"}])
    ports.portslist.selectRow(0)
    assert ports.target_box.currentText() == top
    close_window(window)


def test_a_via_port_on_a_port_wall_is_used_as_a_gap_port(monkeypatch, app, messages, tmp_path):
    """the 60 GHz PA case: TopMetal2 lines over a Metal3 plane, via ports Metal3 -> TopMetal2 along the edge"""
    lib = gdspy.GdsLibrary()
    cell = gdspy.Cell("line", exclude_from_current=True)
    lib.add(cell)
    for k, (y0, y1) in enumerate([(-15, -5), (5, 15)]):
        cell.add(gdspy.Rectangle((0, y0), (300, y1), layer=134))
        cell.add(gdspy.Rectangle((0, y0), (0, y1), layer=201 + k))
    cell.add(gdspy.Rectangle((0, -65), (300, 65), layer=30))
    lib.write_gds(str(tmp_path / "line.gds"))
    window, module = open_window(monkeypatch, str(tmp_path / "line.gds"))
    window.ports_tab.update_port_from_import([
        {"portnumber": n, "port_Z0": 50, "voltage": 1, "source_layernum": 200 + n, "from_layername": "Metal3",
         "to_layername": "TopMetal2", "direction": "Z"} for n in (1, 2)])
    window.mesh_tab.set_port_wall("xmin")
    assert window.ports_tab.check_port_walls(ask=True), messages
    ports = window.ports_tab.portslist
    assert ports.item(0, 8).text() == "port wall xmin, calibrated (via port used as a gap port on TopMetal2)"
    assert ports.item(0, 6).text() == "Z", "the row stays the via port as defined"
    ports.selectRow(0)
    assert "used as a gap port on TopMetal2" in window.ports_tab.direction_note.text()
    close_window(window)


def test_preview_draws_the_shifted_reference_plane(monkeypatch, app, messages, tmp_path):
    from setupEM.layout_preview import LayoutPreviewWindow
    from PySide6.QtWidgets import QGraphicsLineItem
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds"))
    window.mesh_tab.set_port_wall("xmin")
    monkeypatch.setattr(window, "open_box_sides_dialog", lambda: None)
    window.ports_tab._port_wall_shift["xmin"] = 40.0
    preview = LayoutPreviewWindow(window)
    preview.refresh()
    labels = [i.text() for i in preview._box_items if hasattr(i, "text")]
    assert "xmin ref. plane +40 µm" in labels
    lines = [i.line() for i in preview._box_items if isinstance(i, QGraphicsLineItem) and "reference plane" in i.toolTip()]
    assert len(lines) == 1 and abs(lines[0].x1() - 40.0) < 1e-9 and lines[0].x1() == lines[0].x2()
    preview.close()
    close_window(window)


def test_the_tab_checks_port_walls_without_a_result_by_itself(monkeypatch, app, messages, tmp_path):
    """after loading a settings file the rows are filled when the tab is shown, without questions or message boxes"""
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds", stub_on_ymax=True), ports=(1, 2, 3))
    window.mesh_tab.set_port_wall("xmin")
    asked = answer(monkeypatch, "Set port wall")
    window.ports_tab.check_port_walls(quiet=True)
    assert window.ports_tab.portwall_table.item(0, 1).text() == "1, 2" and not asked and not messages
    assert window.ports_tab.portslist.item(0, 8).text() == "port wall xmin, calibrated"
    close_window(window)


def test_window_size_and_divider_are_kept_between_sessions(monkeypatch, app):
    """in a separate settings store, never the user's"""
    import uuid
    from PySide6.QtCore import QSettings
    import setupEM.setup_common as common
    org = f"setupEM-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(common, "RECENT_FILES_ORG", org)
    monkeypatch.setattr(common.MainWindowBase, "_keeps_window_layout", lambda self: True)
    try:
        window, module = open_window(monkeypatch)
        window.show()
        window.resize(640, 520)           # within the offscreen screen: Qt fits a restored window into the screen
        window.ports_tab.lists_splitter.setSizes([300, 300])
        app.processEvents()
        sizes = window.ports_tab.lists_splitter.sizes()
        saved = (window.width(), window.height())      # after the window's own minimum size
        window._save_window_layout()
        close_window(window)
        window, module = open_window(monkeypatch)
        # the height comes back (the first-start default would be 90 % of the 800 px offscreen screen); the width
        # is narrowed to that small screen, as Qt does on a real one
        assert window.height() == saved[1] and window.height() != 720
        assert window.ports_tab.lists_splitter.sizes() == sizes
        close_window(window)
    finally:
        QSettings(org, "setupEM").clear()


def test_extension_auto_is_checked_saved_and_shown(monkeypatch, app, messages, tmp_path):
    from setupEM.layout_preview import LayoutPreviewWindow
    lib = gdspy.GdsLibrary()
    cell = gdspy.Cell("line", exclude_from_current=True)
    lib.add(cell)
    for k, (y0, y1) in enumerate([(-15, -5), (5, 15)]):
        cell.add(gdspy.Rectangle((0, y0), (20, y1), layer=134))
        cell.add(gdspy.Rectangle((0, y0), (1, y1), layer=201 + k))
    cell.add(gdspy.Rectangle((20, -40), (120, 40), layer=134))
    cell.add(gdspy.Rectangle((0, -60), (120, 60), layer=250))
    lib.write_gds(str(tmp_path / "dev.gds"))
    window, module = open_window(monkeypatch, str(tmp_path / "dev.gds"))
    window.mesh_tab.set_port_wall("xmin")
    window.ports_tab._port_wall_extension["xmin"] = "auto"
    assert window.ports_tab.check_port_walls(ask=True), messages
    E = window.ports_tab._port_wall_result["xmin"]["extension"]
    assert E > 0 and window.ports_tab._port_wall_result["xmin"]["short"].startswith(f"OK, port wall moved {E:g} µm out")
    assert window.ports_tab.save_values() and module.saved_values["port_wall_extension"] == {"xmin": "auto"}
    window.modeleditor_tab.create_model_text()
    assert "settings['port_wall_extension'] = {'xmin': 'auto'}" in window.modeleditor_tab.model_edit.toPlainText()
    monkeypatch.setattr(window, "open_box_sides_dialog", lambda: None)
    preview = LayoutPreviewWindow(window)
    preview.refresh()
    labels = [i.text() for i in preview._box_items if hasattr(i, "text")]
    assert f"port wall, moved {E:g} µm out" in labels and f"feed +{E:g} µm" in labels
    preview.close()
    close_window(window)


def test_recorded_calibration_standards_are_not_listed_as_models(monkeypatch, tmp_path, app):
    # a models file written by an older setupEM lists the standards of a port wall next to their model (same
    # _data folder): only the model is a run, run_sim solves its standards
    import json
    window, _module = open_window(monkeypatch)
    models = tmp_path / "wall_models.json"
    data = str(tmp_path / "wall_data")
    models.write_text(json.dumps([
        {"sim_path": data, "model_basename": "wall_cal_xmin_L", "solver": "palace",
         "settings": {"boundary": ["PEC"] * 6, "_calibration_standard": True, "config_suffix": "_cal_xmin_L"}},
        {"sim_path": data, "model_basename": "wall_cal_xmin_2L", "solver": "palace",
         "settings": {"boundary": ["PEC"] * 6, "_calibration_standard": True, "config_suffix": "_cal_xmin_2L"}},
        {"sim_path": data, "model_basename": "wall", "solver": "palace",
         "settings": {"boundary": ["PORTWALL", "PORTWALL", "ABC", "ABC", "ABC", "ABC"]}},
    ]))
    monkeypatch.setattr(window, "script_models_file", lambda: str(models))
    window.load_script_runs()
    assert [run["model_basename"] for run in window.script_runs] == ["wall"]


def test_queue_asks_about_previous_results_once_before_it_starts(monkeypatch, tmp_path, app):
    # two models of one script, one with results from an earlier run: asked once, before the first model starts;
    # Delete removes those results now, so no question comes between the models
    window, _module = open_window(monkeypatch)
    tab = window.create_model_tab if hasattr(window, "create_model_tab") else window.run_tab
    runs = []
    for name in ("sweep_T25", "sweep_T85"):
        data = tmp_path / f"{name}_data"
        data.mkdir()
        runs.append({"sim_path": str(data), "model_basename": name, "solver": "palace", "params": {}, "label": name})
    old = tmp_path / "sweep_T85_data" / "output" / "sweep_T85"
    old.mkdir(parents=True)
    (old / "port-S.csv").write_text("old")
    asked = answer(monkeypatch, "Delete")
    assert tab._ask_queue_previous_results(runs) is True
    assert len(asked) == 1 and "1 of the 2 models" in asked[0] and "sweep_T85" in asked[0]
    assert not old.exists() and tab._queue_clear_choice is True
    assert tab._ask_queue_previous_results(runs) is True and len(asked) == 1     # nothing left to ask about
    (old / "port-S.csv").parent.mkdir(parents=True)
    (old / "port-S.csv").write_text("old")
    answer(monkeypatch, "Cancel simulation")
    assert tab._ask_queue_previous_results(runs) is False and old.exists()

def test_status_line_names_the_solve_that_runs(monkeypatch, app, messages):
    """run_sim of a port wall model solves the standards first, then the model: the status line says which runs"""
    window, module = open_window(monkeypatch)
    tab = window.create_model_tab
    tab._reset_live_status()
    assert "Running:" not in tab.status_line.text()            # a model without port walls: as before
    tab._parse_palace_status_line("calibration standard 1 of 2: cal_xmin_L, 3 frequencies")
    assert tab.status_line.text().startswith("Running: calibration standard 1/2 (cal_xmin_L)")
    tab._parse_palace_status_line("Sweeping excitation index 1 (1/2):")
    assert "Port: 1/2" in tab.status_line.text()
    tab._parse_palace_status_line("calibration standard 2 of 2: cal_xmin_2L, 3 frequencies")
    tab._parse_palace_status_line("  reused, solved Mon Nov  9 11:00:00 CET 2026")
    assert tab.status_line.text().startswith("Running: calibration standard 2/2 (cal_xmin_2L), reused")
    assert "Port: n/a" in tab.status_line.text()               # the next solve starts without the last one's port
    tab._parse_palace_status_line("device model")
    assert tab.status_line.text().startswith("Running: model    |")
    close_window(window)

def test_port_wall_settings_come_back_from_the_script(monkeypatch, app, messages, tmp_path):
    """a model script edited in place gives back its port wall extension and reference plane shift (they were written
    but not read back: the next session started with 0)"""
    gds = gssg_gds(tmp_path / "line.gds")
    window, module = open_window(monkeypatch, gds)
    window.mesh_tab.set_port_wall("xmin")
    window.ports_tab._port_wall_extension["xmin"] = 50.0
    window.ports_tab._port_wall_shift["xmin"] = 10.0
    assert window.mesh_tab.save_values() and window.ports_tab.save_values()
    module.saved_values["deembedding_length"] = 120.0
    window.modeleditor_tab.create_model_text()
    script = tmp_path / "line_model.py"
    script.write_text(window.modeleditor_tab.model_edit.toPlainText())
    close_window(window)
    window, module = open_window(monkeypatch)
    window.load_configuration_from_file(str(script), in_place=True, ask_unsaved=False)
    assert window.ports_tab._port_wall_extension == {"xmin": 50.0}
    assert window.ports_tab._port_wall_shift == {"xmin": 10.0}
    assert float(module.saved_values.get("deembedding_length")) == 120.0
    close_window(window)
