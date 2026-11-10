# The dielectric oversize (settings['margin']) per side: one value, or [xmin, xmax, ymin, ymax].
# Helpers, setupEM's box sides dialog, setupThermal's dialog, save/load, script import and in-place patching.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from setupEM.script_model import ScriptModel, patch_script  # noqa: E402
from setupEM.setup_common import MarginSidesDialog, as_side_values, margin_from_saved, margin_to_saved  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def warnings_shown(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: shown.append(text)))
    return shown


def open_window(monkeypatch, module_name):
    module = __import__(f"setupEM.{module_name}", fromlist=["MainWindow"])
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    window = module.MainWindow()
    return window, module


def close_window(window):
    window._script_dirty = False
    window.close()


# ---------- helpers ----------

def test_as_side_values():
    assert as_side_values([1, 2, 3, 4], 4) == ["1", "2", "3", "4"]
    assert as_side_values((1, 2, 3, 4), 4) == ["1", "2", "3", "4"]
    assert as_side_values("[1, 2, 3, 4]", 4) == ["1", "2", "3", "4"]
    assert as_side_values([1, 2, 3, 4]) is None     # 6 values by default
    assert as_side_values(["ABC"] * 6) == ["ABC"] * 6
    assert as_side_values(50, 4) is None and as_side_values("50", 4) is None


@pytest.mark.parametrize("saved, text, overrides", [
    (50.0, "50.0", {}),                                   # a single value stays as it was
    ("50", "50", {}),
    ("margin_var", "margin_var", {}),                     # a script variable stays in the field
    ([10, 20, 30, 40], "10", {"xmax": 20, "ymin": 30, "ymax": 40}),
    ([20, 10, 20, 20], "20", {"xmax": 10}),               # the most common value is the default
    ([10, 20, 10, 20], "10", {"xmax": 20, "ymax": 20}),   # on a tie the first one
    ((0, 50, 50, 50), "50", {"xmin": 0}),
    ([50, 50, 50, 50], "50", {}),
    ("[a, b, c, d]", "[a, b, c, d]", {}),
])
def test_margin_from_saved(saved, text, overrides):
    assert margin_from_saved(saved) == (text, overrides)


def test_margin_to_saved():
    assert margin_to_saved(50.0, {}) == 50.0
    assert margin_to_saved(10.0, {"xmax": 20.0, "ymax": 40.0}) == [10.0, 20.0, 10.0, 40.0]
    assert margin_to_saved(20.0, {"xmax": 20.0}) == 20.0    # no side really differs


@pytest.mark.parametrize("saved", [[10.0, 20.0, 30.0, 40.0], [0.0, 50.0, 50.0, 50.0], [10.0, 20.0, 10.0, 20.0], 50.0])
def test_margin_round_trip(saved):
    text, overrides = margin_from_saved(saved)
    assert margin_to_saved(float(text), overrides) == saved


# ---------- setupEM box sides dialog ----------

def box_dialog(monkeypatch, app, overrides=None, editable=True):
    window, module = open_window(monkeypatch, "setupEM")
    dialog = module.BoxSidesOverrideDialog(None, "Absorbing", "200", overrides or {}, default_margin_text="100",
                                           margin_editable=editable)
    return window, dialog


def test_box_dialog_margin_column(monkeypatch, app, warnings_shown):
    window, dialog = box_dialog(monkeypatch, app, {"xmin": {"margin": 5.0, "air": 1.0}, "zmin": {"bc": "PEC"}})
    assert set(dialog._margin_edits) == {"xmin", "xmax", "ymin", "ymax"}, "no oversize for the z sides"
    assert dialog.table.cellWidget(4, 2) is None and dialog.table.cellWidget(5, 2) is None
    assert dialog._margin_edits["xmin"].text() == "5"
    assert dialog._margin_edits["xmax"].placeholderText() == "100"

    dialog._margin_edits["xmax"].setText("0")      # 0 is allowed
    dialog._margin_edits["ymin"].setText("100")    # equal to the default: no override
    dialog._on_ok()
    assert dialog.get_overrides() == {"xmin": {"margin": 5.0, "air": 1.0}, "xmax": {"margin": 0.0}, "zmin": {"bc": "PEC"}}
    assert not warnings_shown
    close_window(window)


@pytest.mark.parametrize("bad", ["-1", "abc"])
def test_box_dialog_rejects_bad_margin(monkeypatch, app, warnings_shown, bad):
    window, dialog = box_dialog(monkeypatch, app)
    dialog._margin_edits["ymax"].setText(bad)
    dialog._on_ok()
    assert dialog.result() != QDialog.Accepted and len(warnings_shown) == 1 and "ymax" in warnings_shown[0]
    close_window(window)


def test_box_dialog_reset_all(monkeypatch, app):
    window, dialog = box_dialog(monkeypatch, app, {"xmin": {"margin": 5.0}})
    dialog._reset_all()
    dialog._on_ok()
    assert dialog.get_overrides() == {}
    close_window(window)


def test_box_dialog_script_sets_margin(monkeypatch, app):
    """A script edited in place sets the margin itself: the column is read only and keeps its values."""
    window, dialog = box_dialog(monkeypatch, app, {"xmin": {"margin": 5.0}}, editable=False)
    assert not dialog._margin_edits["xmin"].isEnabled()
    dialog._reset_all()
    dialog._air_edits["zmax"].setText("7")
    dialog._on_ok()
    assert dialog.get_overrides() == {"xmin": {"margin": 5.0}, "zmax": {"air": 7.0}}
    close_window(window)


# ---------- setupEM mesh tab ----------

def test_mesh_tab_save_and_load(monkeypatch, app, warnings_shown):
    window, module = open_window(monkeypatch, "setupEM")
    mesh = window.mesh_tab
    module.saved_values["margin"] = [10.0, 20.0, 30.0, 40.0]
    module.saved_values["air_around"] = 50.0
    mesh.load_values()
    assert mesh.margins_edit.text() == "10"
    assert mesh._box_side_overrides == {"xmax": {"margin": 20.0}, "ymin": {"margin": 30.0}, "ymax": {"margin": 40.0}}
    assert mesh.boundary_override_btn.text() == "Advanced... (3)"
    assert "oversize 20 µm" in mesh.boundary_override_btn.toolTip()
    assert mesh.airaround_edit.text() == "50.0"

    assert mesh.save_values()
    assert module.saved_values["margin"] == [10.0, 20.0, 30.0, 40.0]
    assert module.saved_values["air_around"] == 50.0

    # a tab value that makes all sides the same is a single value again
    mesh._box_side_overrides = {}
    assert mesh.save_values() and module.saved_values["margin"] == 10.0

    mesh.margins_edit.setText("-5")
    assert not mesh.save_values() and warnings_shown
    close_window(window)


def test_mesh_tab_air_default_follows_margin(monkeypatch, app):
    """No air_around with a margin per side: what gds2palace does without it, so the script keeps its meaning."""
    window, module = open_window(monkeypatch, "setupEM")
    mesh = window.mesh_tab
    module.saved_values["margin"] = [10.0, 20.0, 30.0, 40.0]
    module.saved_values.pop("air_around", None)
    mesh.load_values()
    assert mesh.save_values()
    assert module.saved_values["air_around"] == [10.0, 20.0, 30.0, 40.0, 40.0, 40.0]

    # a list with the same value on all sides is the same as a single value
    module.saved_values["margin"] = [25.0, 25.0, 25.0, 25.0]
    module.saved_values.pop("air_around", None)
    mesh.load_values()
    assert mesh.margins_edit.text() == "25" and mesh.airaround_edit.text() == "25"
    assert mesh._box_side_overrides == {}
    close_window(window)


def test_mesh_tab_single_margin_unchanged(monkeypatch, app):
    window, module = open_window(monkeypatch, "setupEM")
    mesh = window.mesh_tab
    module.saved_values["margin"] = 50.0
    module.saved_values["air_around"] = [50.0, 50.0, 50.0, 50.0, 0.0, 50.0]
    mesh.load_values()
    assert mesh.margins_edit.text() == "50.0"
    assert mesh._box_side_overrides == {"zmin": {"air": 0.0}}
    assert mesh.save_values()
    assert module.saved_values["margin"] == 50.0
    assert module.saved_values["air_around"] == [50.0, 50.0, 50.0, 50.0, 0.0, 50.0]
    close_window(window)


def test_setupem_writes_margin_list_to_script(monkeypatch, app):
    window, module = open_window(monkeypatch, "setupEM")
    module.saved_values["margin"] = [10.0, 20.0, 30.0, 40.0]
    window.mesh_tab.load_values()
    assert window.mesh_tab.save_values()
    window.modeleditor_tab.create_model_text()
    assert "settings['margin'] = [10.0, 20.0, 30.0, 40.0]" in window.modeleditor_tab.model_edit.toPlainText()
    close_window(window)


# ---------- setupThermal ----------

def test_thermal_dialog(app, warnings_shown):
    dialog = MarginSidesDialog(None, "100", {"xmin": 5.0})
    assert dialog._edits["xmin"].text() == "5" and dialog._edits["ymax"].placeholderText() == "100"
    dialog._edits["xmax"].setText("0")
    dialog._edits["ymin"].setText("100")      # equal to the default: no override
    dialog._on_ok()
    assert dialog.get_overrides() == {"xmin": 5.0, "xmax": 0.0} and not warnings_shown

    dialog = MarginSidesDialog(None, "100", {})
    dialog._edits["ymax"].setText("-1")
    dialog._on_ok()
    assert dialog.result() != QDialog.Accepted and "ymax" in warnings_shown[0]

    dialog = MarginSidesDialog(None, "100", {"xmin": 5.0})
    dialog._reset_all()
    dialog._on_ok()
    assert dialog.get_overrides() == {}


def test_thermal_mesh_tab(monkeypatch, app, warnings_shown):
    window, module = open_window(monkeypatch, "setupThermal")
    mesh = window.mesh_tab
    assert mesh.margin_override_btn.text() == "Advanced..."

    module.saved_values["margin"] = [0.0, 100.0, 100.0, 100.0]
    mesh.load_values()
    assert mesh.margins_edit.text() == "100" and mesh._margin_overrides == {"xmin": 0.0}
    assert mesh.margin_override_btn.text() == "Advanced... (1)" and "xmin: 0 µm" in mesh.margin_override_btn.toolTip()
    assert mesh.save_values() and module.saved_values["margin"] == [0.0, 100.0, 100.0, 100.0]
    window.modeleditor_tab.create_model_text()
    assert "settings['margin'] = [0.0, 100.0, 100.0, 100.0]" in window.modeleditor_tab.model_edit.toPlainText()

    module.saved_values["margin"] = 100.0
    mesh.load_values()
    assert mesh._margin_overrides == {} and mesh.margin_override_btn.text() == "Advanced..."
    assert mesh.save_values() and module.saved_values["margin"] == 100.0

    mesh.margins_edit.setText("-5")
    assert not mesh.save_values() and warnings_shown
    close_window(window)


def test_thermal_button_grey_when_script_sets_margin(monkeypatch, app):
    window, _ = open_window(monkeypatch, "setupThermal")
    window._grey_out_script_settings({"margin": "swept: T = 25, 85"})
    assert not window.mesh_tab.margins_edit.isEnabled() and not window.mesh_tab.margin_override_btn.isEnabled()
    window._grey_out_script_settings({})
    assert window.mesh_tab.margin_override_btn.isEnabled()
    close_window(window)


def test_setupem_dialog_margin_read_only_when_script_sets_it(monkeypatch, app):
    window, module = open_window(monkeypatch, "setupEM")
    window._grey_out_script_settings({"margin": "swept: T = 25, 85"})
    captured = {}

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(module, "BoxSidesOverrideDialog", FakeDialog)
    window.mesh_tab.open_box_sides_override_dialog()
    assert captured["margin_editable"] is False
    window._grey_out_script_settings({})
    window.mesh_tab.open_box_sides_override_dialog()
    assert captured["margin_editable"] is True
    close_window(window)


# ---------- script import and in-place patching ----------

def script_with_margin(margin):
    return f"settings = {{}}\nsettings['fstop'] = 10e9\nsettings['margin'] = {margin}\n"


def test_patch_script_writes_a_list(tmp_path):
    model = ScriptModel(script_with_margin("50"))
    patch_script(model, {"margin": 50}, {"margin": [10.0, 20.0, 30.0, 40.0]})
    assert "settings['margin'] = [10.0, 20.0, 30.0, 40.0]\n" in model.result()


def test_patch_script_keeps_an_unchanged_list(tmp_path):
    """Imported as ints, saved by the GUI as floats: not a change, the script keeps its text."""
    text = script_with_margin("[10, 20, 30, 40]   # my note")
    model = ScriptModel(text)
    written, refused = patch_script(model, {"margin": [10, 20, 30, 40]}, {"margin": [10.0, 20.0, 30.0, 40.0]})
    assert written == [] and refused == [] and model.result() == text


def test_import_script_with_margin_list(monkeypatch, app, tmp_path):
    window, module = open_window(monkeypatch, "setupEM")
    monkeypatch.setattr(window, "_add_recent_file", lambda *a: None)
    script = open(os.path.join(HERE, "fixtures", "generated_setupEM_palace.py"), encoding="utf-8").read()
    assert "settings['margin'] = 50.0" in script
    script = script.replace("settings['margin'] = 50.0", "settings['margin'] = [10, 20, 30, 40]")
    lines = [line for line in script.splitlines(True) if "settings['air_around']" not in line]
    path = tmp_path / "with_list.py"
    path.write_text("".join(lines), encoding="utf-8")

    window.load_configuration_from_file(str(path))
    mesh = window.mesh_tab
    assert mesh.margins_edit.text() == "10"
    assert mesh._box_side_overrides["ymax"]["margin"] == 40.0
    assert mesh.save_values()
    assert module.saved_values["margin"] == [10.0, 20.0, 30.0, 40.0]
    assert module.saved_values["air_around"] == [10.0, 20.0, 30.0, 40.0, 40.0, 40.0]
    close_window(window)


def test_port_walls_do_not_make_the_defaults(monkeypatch, app, warnings_shown):
    """two port walls (oversize and air 0) and two sides with 200: the default is 200 and the y sides follow it, so the
    tab's fields still change something (before, 0 won the tie and every side was an override)"""
    window, module = open_window(monkeypatch, "setupEM")
    mesh = window.mesh_tab
    module.saved_values["boundary"] = ["PORTWALL", "PORTWALL", "ABC", "ABC", "ABC", "ABC"]
    module.saved_values["margin"] = [0.0, 0.0, 200.0, 200.0]
    module.saved_values["air_around"] = [0.0, 0.0, 200.0, 200.0, 200.0, 200.0]
    mesh.load_values()
    assert mesh.margins_edit.text() == "200" and float(mesh.airaround_edit.text()) == 200.0
    assert set(mesh._box_side_overrides) == {"xmin", "xmax"}
    assert mesh.margins_edit.isEnabled() and mesh.airaround_edit.isEnabled()
    mesh.margins_edit.setText("10")
    assert mesh.save_values() and module.saved_values["margin"] == [0.0, 0.0, 10.0, 10.0]
    close_window(window)


def test_a_default_field_that_sets_no_side_is_greyed_out(monkeypatch, app, warnings_shown):
    window, module = open_window(monkeypatch, "setupEM")
    mesh = window.mesh_tab
    mesh._box_side_overrides = {side: {"margin": 5.0 + k} for k, side in enumerate(("xmin", "xmax", "ymin", "ymax"))}
    mesh._update_boundary_override_button_label()
    assert not mesh.margins_edit.isEnabled() and "Advanced" in mesh.margins_edit.toolTip()
    assert mesh.airaround_edit.isEnabled() and mesh.boundary_box.isEnabled()
    mesh._box_side_overrides = {}
    mesh._update_boundary_override_button_label()
    assert mesh.margins_edit.isEnabled()
    close_window(window)
