# File > Save (Ctrl+S) without a script edited in place: it saves to the settings file the model was opened from (e.g.
# a layout tool's export), keeping that file's other keys; closing, opening another file or New ask about unsaved
# changes to it. Before, Ctrl+S did nothing there (the action only existed for a script edited in place), and changes
# such as a port wall extension were lost without a question.
#
# Run from the repo root:   python -m pytest tests

import json
import os

import pytest
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMessageBox

from test_port_walls import app, close_window, gssg_gds, messages, open_window  # noqa: F401  (fixtures)


@pytest.fixture
def export(monkeypatch, app, messages, tmp_path):
    """A settings file as a layout tool writes it: GSSG lines with a port wall at xmin, plus a key of its own."""
    window, module = open_window(monkeypatch, gssg_gds(tmp_path / "line.gds"))
    window.mesh_tab.set_port_wall("xmin")
    window.save_all_tabs()
    data = {"application": "setupEM", "data_format": "1.0", "saved_values": dict(module.saved_values),
            "model_script": "line_em.py"}
    data.update(window.native_config_extra_struct())
    close_window(window)
    path = tmp_path / "line_em.simcfg"
    path.write_text(json.dumps(data))
    return str(path)


def extension_edit(window, side):
    window.ports_tab.refresh_port_wall_rows()
    table = window.ports_tab.portwall_table
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == side)
    return table.cellWidget(row, 7).findChild(QLineEdit)


def asked(monkeypatch, answer):
    """QMessageBox.question answered with answer; returns the list of questions asked"""
    questions = []

    def question(parent, title, text, *args):
        questions.append(text)
        return answer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return questions


def test_ctrl_s_saves_the_port_wall_extension_into_the_export(monkeypatch, export, messages):
    window, module = open_window(monkeypatch)
    window.load_configuration_from_file(export)
    assert window.save_script_action.isVisible() and window.save_script_action.text() == "Save line_em.simcfg"
    edit = extension_edit(window, "xmin")
    edit.setText("20")
    edit.editingFinished.emit()
    assert window.config_has_unsaved_changes()
    window.save_script_action.trigger()                 # Ctrl+S
    assert not window.config_has_unsaved_changes()
    assert not [m for m in messages if m[0] != "information"], messages
    data = json.load(open(export))
    assert data["saved_values"]["port_wall_extension"] == {"xmin": 20.0}
    assert data["model_script"] == "line_em.py", "the layout tool's key is kept"
    assert "Config saved to" in window.create_model_tab.log_area.toPlainText()
    close_window(window)

    window, module = open_window(monkeypatch)            # opened again: the extension is there
    window.load_configuration_from_file(export)
    assert window.ports_tab._port_wall_extension == {"xmin": 20.0} and extension_edit(window, "xmin").text() == "20.0"
    close_window(window)


def test_closing_asks_only_after_a_change(monkeypatch, export):
    window, module = open_window(monkeypatch)
    window.load_configuration_from_file(export)
    questions = asked(monkeypatch, QMessageBox.Discard)
    assert window.close() and questions == [], "nothing changed: no question"

    for answer, closes, saved in ((QMessageBox.Cancel, False, False), (QMessageBox.Discard, True, False),
                                  (QMessageBox.Save, True, True)):
        window, module = open_window(monkeypatch)
        window.load_configuration_from_file(export)
        window.frequencies_tab.stop_edit.setText("123")
        questions = asked(monkeypatch, answer)
        before = open(export).read()
        assert window.close() is closes
        assert questions == ["Save the changes to line_em.simcfg?"]
        after = json.load(open(export))
        assert (after != json.loads(before)) is saved
        if saved:
            assert after["model_script"] == "line_em.py"
        close_window(window)


def test_opening_another_file_asks_first(monkeypatch, export, tmp_path):
    window, module = open_window(monkeypatch)
    window.load_configuration_from_file(export)
    window.frequencies_tab.stop_edit.setText("123")
    questions = asked(monkeypatch, QMessageBox.Cancel)
    other = tmp_path / "other.simcfg"
    other.write_text(open(export).read())
    window.load_configuration_from_file(str(other))
    assert questions == ["Save the changes to line_em.simcfg?"] and window.config_file == export, "Cancel: stays"
    close_window(window)


def test_new_model_ctrl_s_asks_for_a_file(monkeypatch, export, tmp_path):
    window, module = open_window(monkeypatch)
    window.load_configuration_from_file(export)
    asked(monkeypatch, QMessageBox.Discard)
    window.new_configuration()
    assert window.config_file is None and window.save_script_action.text() == "Save ..."
    target = tmp_path / "new_model.simcfg"
    module.saved_values["GdsFile"] = str(tmp_path / "line.gds")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    window.save_script_action.trigger()
    assert os.path.isfile(target) and window.config_file == str(target)
    assert window.save_script_action.text() == "Save new_model.simcfg"
    close_window(window)
