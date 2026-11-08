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
    assert table.item(0, 7).text().startswith("OK")
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
    assert "xmin: port wall" in labels
    assert any(text.startswith("xmax: Absorbing, oversize") for text in labels)
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
    status = {table.item(r, 0).text(): table.item(r, 7).text() for r in range(table.rowCount())}
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
