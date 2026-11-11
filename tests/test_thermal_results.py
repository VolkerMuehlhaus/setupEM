# Temperature per heat source from an Elmer thermal result (thermal_results.py): the volume-weighted average and the
# hottest node of each source_<layer> body, the instance names of a thermal export's map, the CSV for the circuit
# simulation, the command line, and setupThermal writing them after a run. The result is a small synthetic one in
# Elmer's own .vtu format (raw appended data, which meshio cannot read) with a boundary element that must not count.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import struct
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM import thermal_results  # noqa: E402

# a unit cube corner tetrahedron has volume 1/6; scaled by s it has s^3/6
POINTS = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],      # tet A (source_205), volume 1/6
                   [2, 0, 0], [4, 0, 0], [2, 2, 0], [2, 0, 2],      # tet B (source_205), volume 8/6
                   [5, 0, 0], [6, 0, 0], [5, 1, 0], [5, 0, 1]],     # tet C (SiO2)
                  dtype=np.float64) * 1e-6
TEMPERATURE = np.array([400, 400, 400, 400, 310, 310, 310, 330, 305, 305, 305, 305], dtype=np.float64)
# cells: 3 tetrahedra (VTK 10) and one boundary triangle (VTK 5) of the source, Elmer's GeometryIds = body / boundary
CELLS = [(10, [0, 1, 2, 3], 3), (10, [4, 5, 6, 7], 3), (10, [8, 9, 10, 11], 1), (5, [4, 5, 6], 3)]
NAMES = """! ----- names for bodies -----
$ SiO2 = 1
$ source_202 = 2
$ source_205 = 3
! ----- names for boundaries -----
$ constanttemp_201 = 3
"""


def write_raw_vtu(path):
    """An Elmer-style .vtu: all arrays in one <AppendedData encoding="raw"> block, each with a UInt32 byte count."""
    connectivity = np.array([n for _, nodes, _ in CELLS for n in nodes], dtype=np.int32)
    offsets = np.cumsum([len(nodes) for _, nodes, _ in CELLS]).astype(np.int32)
    types = np.array([t for t, _, _ in CELLS], dtype=np.uint8)
    ids = np.array([i for _, _, i in CELLS], dtype=np.int32)
    arrays = [("PointData", 'type="Float64" Name="temperature" NumberOfComponents="1"', TEMPERATURE),
              ("CellData", 'type="Int32" Name="GeometryIds"', ids),
              ("Points", 'type="Float64" NumberOfComponents="3"', POINTS.ravel()),
              ("Cells", 'type="Int32" Name="connectivity"', connectivity),
              ("Cells", 'type="Int32" Name="offsets"', offsets),
              ("Cells", 'type="UInt8" Name="types"', types)]
    blob, sections, offset = b"", {}, 0
    for section, attributes, data in arrays:
        raw = data.tobytes()
        sections.setdefault(section, []).append(f'<DataArray {attributes} format="appended" offset="{offset}"/>')
        blob += struct.pack("<I", len(raw)) + raw
        offset += 4 + len(raw)
    header = ('<?xml version="1.0"?>\n<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">\n'
              f'<UnstructuredGrid>\n<Piece NumberOfPoints="{len(POINTS)}" NumberOfCells="{len(CELLS)}">\n' +
              "".join(f"<{s}>\n" + "\n".join(sections[s]) + f"\n</{s}>\n"
                      for s in ("PointData", "CellData", "Points", "Cells")) +
              '</Piece>\n</UnstructuredGrid>\n<AppendedData encoding="raw">\n_')
    with open(path, "wb") as f:
        f.write(header.encode() + blob + b"\n</AppendedData>\n</VTKFile>\n")


def make_result(run_path):
    os.makedirs(os.path.join(run_path, "mesh"))
    with open(os.path.join(run_path, "mesh", "mesh.names"), "w") as f:
        f.write(NAMES)
    write_raw_vtu(os.path.join(run_path, "thermal_results_t0001.vtu"))


THERMAL = [{"type": "source", "source_layernum": 205, "power": 0.1, "target_layername": "Activ"},
           {"type": "source", "source_layernum": 202, "power": 0.01, "target_layername": "Activ"},
           {"type": "constanttemp", "source_layernum": 201, "temp": 300.0, "target_layername": "BACKSIDEGND"}]
MAP = {"format": 1, "outline_layer": 201,
       "sources": [{"layer": 205, "device": "npn13G2@-7230,590", "pcell": "npn13G2", "instance": "Q1"},
                   {"layer": 202, "device": "rhigh_CDNS_1@-28550,405", "pcell": "rhigh", "instance": None}]}
# volume-weighted: tet A (1/6, 400 K), tet B (8/6, mean of 310, 310, 310, 330 = 315 K)
T_MEAN = (400 * 1 + 315 * 8) / 9


def test_source_temperatures(tmp_path):
    run = str(tmp_path / "run")
    make_result(run)
    assert thermal_results.read_body_names(run) == {1: "SiO2", 2: "source_202", 3: "source_205"}
    temperatures = thermal_results.source_temperatures(run)
    assert list(temperatures) == [205], "a source without tetrahedra (202) has no temperature"
    t_mean, t_max = temperatures[205]
    assert t_mean == pytest.approx(T_MEAN) and t_max == 400.0


def test_device_temperatures_and_csv(tmp_path):
    run = str(tmp_path / "run")
    make_result(run)
    rows, t_ref = thermal_results.device_temperatures(run, THERMAL, MAP)
    assert t_ref == 300.0 and len(rows) == 1
    row = rows[0]
    assert (row["instance"], row["device"], row["pcell"], row["power_W"]) == ("Q1", "npn13G2@-7230,590", "npn13G2",
                                                                              0.1)
    assert row["dT_mean_K"] == pytest.approx(T_MEAN - 300) and row["dT_max_K"] == 100.0
    assert thermal_results.device_temperatures(str(tmp_path / "none"), THERMAL, MAP) == ([], None)

    csv_path = str(tmp_path / "t.csv")
    thermal_results.write_temperatures_csv(csv_path, rows, t_ref)
    lines = open(csv_path).read().splitlines()
    assert lines[0].startswith("#") and "300 K" in lines[1]
    assert lines[2] == "instance,device,pcell,layer,power_W,T_mean_K,T_max_K,dT_mean_K,dT_max_K"
    assert lines[3].startswith('Q1,"npn13G2@-7230,590",npn13G2,205,0.1,'), "the device id has a comma: quoted"
    table = thermal_results.format_temperature_table(rows)
    assert table[0].split()[0] == "Instance" and table[2].startswith("Q1")


def test_command_line(tmp_path, capsys):
    sim = tmp_path / "model"
    make_result(str(sim / "elmer_model" / "cascode_thermal_data"))
    gds = tmp_path / "cascode_thermal.gds"
    (tmp_path / "cascode_thermal_thermalmap.json").write_text(json.dumps(MAP))
    tsimcfg = tmp_path / "cascode_thermal.tsimcfg"
    tsimcfg.write_text(json.dumps({"application": "setupThermal", "thermal": THERMAL, "saved_values": {
        "sim_path": str(sim), "model_basename": "cascode_thermal", "GdsFile": str(gds)}}))
    assert thermal_results.main([str(tsimcfg)]) == 0
    out = capsys.readouterr().out
    assert "Q1" in out and "100.0 K" in out
    assert (sim / "cascode_thermal_temperatures.csv").is_file()
    capsys.readouterr()
    assert thermal_results.main([str(tmp_path / "missing.tsimcfg")]) == 1
    captured = capsys.readouterr()
    assert "missing.tsimcfg" in captured.err and captured.out == "", "a handled error goes to stderr"
    for arguments, code in (([], 2), (["--help"], 0), (["--version"], 0)):
        with pytest.raises(SystemExit) as exit_info:
            thermal_results.main(arguments)
        assert exit_info.value.code == code


def test_setupthermal_writes_temperatures(tmp_path, monkeypatch):
    """After a run, setupThermal logs the table and writes <model>_temperatures.csv next to the model script."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    import setupEM.setupThermal as setupthermal
    win = setupthermal.MainWindow()
    sim = tmp_path / "model"
    run = sim / "elmer_model" / "cascode_thermal_data"
    make_result(str(run))
    (tmp_path / "cascode_thermal_thermalmap.json").write_text(json.dumps(MAP))
    win.file_tab.gds_file_edit.setText(str(tmp_path / "cascode_thermal.gds"))
    win.saved_values["sim_path"] = str(sim)
    win.saved_values["model_basename"] = "cascode_thermal"
    monkeypatch.setattr(setupthermal.thermal_objects, "objects", [
        setupthermal.simulation_setup.heatsource(power=0.1, source_layernum=205, target_layername="Activ"),
        setupthermal.simulation_setup.constanttemp(temp=300.0, source_layernum=201, target_layername="BACKSIDEGND")])
    tab = win.create_model_tab
    tab.log_area.clear()
    tab._append_device_temperatures(str(run))
    log = tab.log_area.toPlainText()
    assert "Temperature per heat source" in log and "Q1" in log and "dT relative to 300 K" in log
    csv_path = sim / "cascode_thermal_temperatures.csv"
    assert csv_path.is_file() and 'Q1,"npn13G2@-7230,590"' in csv_path.read_text()
    win._script_dirty = False
    win.close()
