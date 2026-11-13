# Return in a text field of the port / thermal object form applies it to the selected row, like the Apply button.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def open_window(monkeypatch, module_name):
    module = __import__(f"setupEM.{module_name}", fromlist=["MainWindow"])
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    return module.MainWindow()


def close_window(window):
    window._script_dirty = False
    window.close()


def text(table, row, col):
    item = table.item(row, col)
    return None if item is None else item.text()


def test_setupem_port(monkeypatch, app):
    window = open_window(monkeypatch, "setupEM")
    try:
        tab, table = window.ports_tab, window.ports_tab.portslist
        # no row selected: nothing is written, the status bar says why
        table.clearSelection()
        tab.impedance_edit.setText("75")
        tab.impedance_edit.returnPressed.emit()
        assert text(table, 0, 0) is None and "Select a row" in window.statusBar().currentMessage()

        # an empty row: Return adds the port
        table.selectRow(0)
        tab.sourcelayer_edit.setText("201")
        tab.impedance_edit.setText("75")
        tab.impedance_edit.returnPressed.emit()
        assert (text(table, 0, 0), text(table, 0, 2)) == ("75", "201")

        # an existing port: Return in another field changes it
        tab.voltage_edit.setText("0")
        tab.voltage_edit.returnPressed.emit()
        assert text(table, 0, 1) == "0" and text(table, 0, 0) == "75"
        tab.sourcelayer_edit.setText("202")
        tab.sourcelayer_edit.returnPressed.emit()
        assert text(table, 0, 2) == "202"
        assert "Return" in tab.apply_button.toolTip()
    finally:
        close_window(window)


def test_setupthermal_object(monkeypatch, app):
    window = open_window(monkeypatch, "setupThermal")
    try:
        tab, table = window.thermal_tab, window.thermal_tab.thermalobjectslist
        table.clearSelection()
        tab.power_edit.setText("0.5")
        tab.power_edit.returnPressed.emit()
        assert text(table, 0, 0) is None and "Select a row" in window.statusBar().currentMessage()

        table.selectRow(0)
        tab.sourcelayer_edit.setText("401")
        tab.thermaltype_box.setCurrentIndex(tab.thermaltype_box.findText("source") if tab.thermaltype_box.findText("source") >= 0
                                            else next(i for i in range(tab.thermaltype_box.count())
                                                      if "SOURCE" in tab.thermaltype_box.itemText(i).upper()))
        tab.power_edit.setText("0.5")
        tab.power_edit.returnPressed.emit()
        assert (text(table, 0, 0), text(table, 0, 2), text(table, 0, 3)) == ("401", "source", "0.5")

        tab.power_edit.setText("0.25")
        tab.power_edit.returnPressed.emit()
        assert text(table, 0, 3) == "0.25"
    finally:
        close_window(window)
