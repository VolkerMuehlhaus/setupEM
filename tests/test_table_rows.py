# GUI test (offscreen): the Ports table (setupEM) and the Thermal Sources + Boundaries table (setupThermal) grow
# with the loaded entries instead of stopping at 32 rows (Qt ignores setItem beyond the last row, which used to drop
# entries 33+ on load and on the next save), and keep empty rows below the last one applied.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

N = 40


def filled_rows(table):
    return [r for r in range(table.rowCount()) if table.item(r, 0) is not None and table.item(r, 0).text()]


@pytest.fixture
def app(monkeypatch):
    application = QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Ok)
    return application


def open_window(module, monkeypatch):
    window = module.MainWindow()
    monkeypatch.setattr(window, "_add_recent_file", lambda *a: None)
    return window


def close(window, app):
    window._script_dirty = False
    window.close()
    app.processEvents()


def test_ports(app, monkeypatch, tmp_path):
    import setupEM.setupEM as setupem
    win = open_window(setupem, monkeypatch)
    ports = [{"portnumber": n, "source_layernum": 200 + n, "from_layername": "Metal1", "to_layername": "TopMetal2",
              "direction": "Z", "port_Z0": 50.0, "voltage": 1.0, "target_layername": None} for n in range(1, N + 1)]
    simcfg = tmp_path / "many_ports.simcfg"
    simcfg.write_text(json.dumps(dict({"application": "setupEM", "data_format": "1.0",
                                       "saved_values": {"GdsFile": str(tmp_path / "many_ports.gds")}},
                                      ports=ports)))
    win.load_configuration_from_file(str(simcfg))
    table = win.ports_tab.portslist
    assert filled_rows(table) == list(range(N)) and table.rowCount() >= N + 8
    assert win.ports_tab.save_values() and len(setupem.simulation_ports.ports) == N

    # round trip through File > Save and Load
    again = tmp_path / "again.simcfg"
    win.save_user_inputs_to_file(str(again))
    assert len(json.loads(again.read_text())["ports"]) == N
    win.new_configuration()
    win.load_configuration_from_file(str(again))
    assert filled_rows(table) == list(range(N))

    # a port applied in the last row leaves empty rows below it
    rows = table.rowCount()
    table.selectRow(rows - 1)
    win.ports_tab.apply_port_values_to_table()
    assert table.rowCount() == rows + 8
    close(win, app)


def test_thermal_objects(app, monkeypatch, tmp_path):
    import setupEM.setupThermal as setupthermal
    win = open_window(setupthermal, monkeypatch)
    objects = ([{"source_layernum": 200 + n, "target_layername": "Activ", "type": "source", "power": 0.01}
                for n in range(1, 31)] +
               [{"source_layernum": 230 + n, "target_layername": "BACKSIDEGND", "type": "constanttemp",
                 "temp": 300.0} for n in range(1, 11)])
    tsimcfg = tmp_path / "many_sources.tsimcfg"
    tsimcfg.write_text(json.dumps({"application": "setupThermal", "data_format": "1.0",
                                   "saved_values": {"GdsFile": str(tmp_path / "many_sources.gds")},
                                   "thermal": objects}))
    win.load_configuration_from_file(str(tsimcfg))
    table = win.thermal_tab.thermalobjectslist
    assert filled_rows(table) == list(range(N)) and table.rowCount() >= N + 8
    assert win.thermal_tab.save_values() and len(setupthermal.thermal_objects.objects) == N

    again = tmp_path / "again.tsimcfg"
    win.save_user_inputs_to_file(str(again))
    assert len(json.loads(again.read_text())["thermal"]) == N

    rows = table.rowCount()
    table.selectRow(rows - 1)
    win.thermal_tab.apply_port_values_to_table()
    assert table.rowCount() == rows + 8
    close(win, app)
