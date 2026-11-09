# A split region in setupEM (piece_set.py and the window): the pieces as rows, the settings of the whole region
# shared by all pieces (a piece may have its own mesh), the box sides of a piece derived from the region's, the run
# key and the reuse of unchanged pieces, and in the window: opening the set, selecting a piece, a change that reaches
# all pieces, the own mesh of one piece, Start Simulation with every piece unchanged (reused and joined, nothing
# run), without a gmsh window and without meshing a piece again whose mesh is current, leaving the set.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import sys

import gdspy
import numpy as np
import pytest
import skrf as rf
from skrf.media import DefinedGammaZ0

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from gds2palace import utilities  # noqa: E402
from setupEM.piece_set import PieceSet  # noqa: E402

STACKUP = utilities.get_stackup_path("SG13G2_FEM_200um.xml")
FREQ = rf.Frequency(1, 50, 50, "GHz")
LINE = DefinedGammaZ0(FREQ, z0_port=50, z0=62.0, gamma=1j * 2 * np.pi * FREQ.f * 2.0 / 3e8 + 20.0)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def messages(monkeypatch):
    shown = []
    for kind in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda parent, title, text, *a, k=kind: shown.append((k, text))))
    return shown


def _gds(path, x0, x1):
    lib = gdspy.GdsLibrary()
    cell = gdspy.Cell("line", exclude_from_current=True)
    lib.add(cell)
    cell.add(gdspy.Rectangle((x0, -5), (x1, 5), layer=134))
    for y0, y1 in ((-55, -15), (15, 55)):
        cell.add(gdspy.Rectangle((x0, y0), (x1, y1), layer=134))
    cell.add(gdspy.Rectangle((x0, -5), (x0, 5), layer=201))
    cell.add(gdspy.Rectangle((x1, -5), (x1, 5), layer=202))
    lib.write_gds(str(path))
    return str(path).replace("\\", "/")


def _simcfg(path, gds, name, folder, refined, piece=None, fingerprint=None, pieces=None):
    values = {"unit": 1e-06, "GdsFile": gds, "SubstrateFile": STACKUP.replace("\\", "/"), "cellname": "line",
              "fstart": 0.0, "fstop": 50.0, "fstep": 1.0, "refined_cellsize": refined, "order": 2,
              "boundary": ["PORTWALL", "PORTWALL", "ABC", "ABC", "ABC", "ABC"], "margin": 200.0, "air_around": 200.0}
    if folder:
        values.update({"sim_path": folder.replace("\\", "/"), "model_basename": name})
    data = {"application": "setupEM", "data_format": "1.0", "saved_values": values,
            "ports": [{"portnumber": n, "source_layernum": 200 + n, "target_layername": "TopMetal2",
                       "from_layername": None, "to_layername": None, "direction": d, "port_Z0": 50.0, "voltage": 1.0}
                      for n, d in ((1, "X"), (2, "-X"))]}
    if piece:
        data.update({"piece": piece, "piece_of": "whole.simcfg", "fingerprint": fingerprint})
    if pieces:
        data["pieces"] = pieces
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return str(path)


def make_set(tmp_path):
    folder = tmp_path / "whole_pieces"
    folder.mkdir()
    whole_gds = _gds(tmp_path / "whole.gds", 0, 480)
    for name, x0, x1, refined in (("p1", 0, 240, 5.0), ("p2", 240, 480, 3.0)):
        _simcfg(folder / f"{name}.simcfg", _gds(folder / f"{name}.gds", x0, x1), name, str(folder), refined,
                piece=name, fingerprint=f"fp-{name}")
    _simcfg(folder / "full.simcfg", whole_gds, "full", str(folder), 5.0, piece="full")
    section = {"version": 1, "folder": "whole_pieces", "cell": "line", "stackup": STACKUP,
               "pieces": [{"name": "p1", "box": [0, -55, 240, 55], "simcfg": "whole_pieces/p1.simcfg",
                           "fingerprint": "fp-p1", "ports": {"1": 1, "2": 3}},
                          {"name": "p2", "box": [240, -55, 480, 55], "simcfg": "whole_pieces/p2.simcfg",
                           "fingerprint": "fp-p2", "ports": {"1": 2, "2": 4}}],
               "links": [{"ports": [["p1", 2], ["p2", 2]], "cut": [240, -55, 240, 55], "line": [134, -5, 5]}],
               "external": [{"port": 1, "piece": "p1", "piece_port": 1, "layout_port": 1},
                            {"port": 2, "piece": "p2", "piece_port": 1, "layout_port": 2}],
               "full": "whole_pieces/full.simcfg"}
    return _simcfg(tmp_path / "whole.simcfg", whole_gds, "whole", None, 5.0, pieces=section)


def fake_result(row, network):
    """a piece's de-embedded result where setupEM puts it, with calibrated port wall headers"""
    path = PieceSet.result(row)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    network.write_touchstone(os.path.splitext(path)[0])
    text = open(path).read()
    open(path, "w").write("!port 1: calibrated at port wall xmin, reference plane at the wall\n"
                          "!port 2: calibrated at port wall xmax, reference plane at the wall\n" + text)


# ---------- piece_set.py ----------

def test_rows_frequency_and_run_key(tmp_path):
    s = PieceSet(make_set(tmp_path))
    rows = s.rows()
    assert [r["piece"] for r in rows] == ["p1", "p2", "full"] and rows[2]["full"] and not rows[0]["full"]
    assert rows[0]["label"].startswith("p1   x 0..240") and rows[0]["sim_path"].endswith("whole_pieces/palace_model/p1_data")
    assert s.row_for(rows[1]["simcfg"].replace(".simcfg", ".py"))["piece"] == "p2"     # its script counts too
    key = s.run_key(rows[0])
    assert key and s.run_key(rows[2]) is None                                        # full: never reused
    values = s.effective_values(rows[0])
    assert s.run_key(rows[0], dict(values, sim_path="elsewhere")) == key             # where it is: no change
    assert s.run_key(rows[0], dict(values, cellname="amp_v2")) == key                # a renamed cell: no change
    assert s.run_key(rows[0], dict(values, refined_cellsize=2.0)) != key             # its mesh: a change
    changed = s.set_frequency({"fstop": 60.0})
    assert len(changed) == 4 and all(json.load(open(p))["saved_values"]["fstop"] == 60.0 for p in changed)
    assert s.run_key(rows[0]) != key                                                 # the frequencies: a change


def test_box_sides_of_the_pieces_follow_the_region(tmp_path):
    s = PieceSet(make_set(tmp_path))
    p1, p2, full = s.rows()
    assert s.cut_sides(p1) == {"xmax": "p2"} and s.cut_sides(p2) == {"xmin": "p1"} and s.cut_sides(full) == {}
    assert s.wall_sides(p1) == {"xmin", "xmax"}, "recorded from the export's port walls"
    assert set(s.locked_sides(p1)) == {"xmax"} and "cut to p2" in s.locked_sides(p1)["xmax"]
    assert s.box_sides(p1) == {"boundary": ["PORTWALL", "PORTWALL", "ABC", "ABC", "ABC", "ABC"],
                               "margin": [200.0, 0.0, 200.0, 200.0], "air_around": [200.0, 0.0, 200.0, 200.0, 200.0, 200.0]}
    # the region's ymin becomes PEC, set while p1 is loaded: p2 gets it too, its cut stays a port wall
    values = dict(s.effective_values(p1), boundary=["PORTWALL", "PORTWALL", "PEC", "ABC", "ABC", "ABC"])
    assert "box sides" in s.store(p1, values)
    assert s.effective_values(p2)["boundary"] == ["PORTWALL", "PORTWALL", "PEC", "ABC", "ABC", "ABC"]
    # a try to make p1's cut absorbing is ignored: the split sets it
    s.store(p1, dict(s.effective_values(p1), boundary=["PORTWALL", "ABC", "PEC", "ABC", "ABC", "ABC"]))
    assert s.effective_values(p1)["boundary"][1] == "PORTWALL"
    # a region port wall without a port of this piece is PEC at the layout edge, as in the full model
    data = json.load(open(p2["simcfg"]))
    data["wall_sides"] = ["xmin"]
    json.dump(data, open(p2["simcfg"], "w"))
    assert s.box_sides(p2)["boundary"][1] == "PEC" and s.box_sides(p2)["margin"][1] == 0.0
    assert "without a port of p2" in s.locked_sides(p2)["xmax"]


def test_shared_settings_and_an_own_mesh(tmp_path):
    s = PieceSet(make_set(tmp_path))
    p1, p2, _ = s.rows()
    assert s.effective_values(p2)["refined_cellsize"] == 5.0, "the region's mesh, not the old value in p2's file"
    key_p1, key_p2 = s.run_key(p1), s.run_key(p2)
    s.set_own(p2, dict(s.effective_values(p2), refined_cellsize=2.0))
    assert s.effective_values(p2)["refined_cellsize"] == 2.0 and "own mesh" in s.rows()[1]["label"]
    assert s.run_key(p1) == key_p1 and s.run_key(p2) != key_p2, "only p2 changes"
    # a mesh change while p2 (own mesh) is loaded stays p2's; a frequency change is everyone's
    changed = s.store(p2, dict(s.effective_values(p2), refined_cellsize=1.5, fstop=40.0))
    assert changed == ["fstop"]
    assert s.effective_values(p2)["refined_cellsize"] == 1.5 and s.effective_values(p1)["refined_cellsize"] == 5.0
    assert json.load(open(p1["simcfg"]))["saved_values"]["fstop"] == 40.0
    s.set_own(p2, None)
    assert s.effective_values(p2)["refined_cellsize"] == 5.0


def test_reuse_needs_the_same_key_and_the_result(tmp_path):
    s = PieceSet(make_set(tmp_path))
    p1 = s.rows()[0]
    assert not s.reusable(p1)
    fake_result(p1, LINE.line(100e-6, "m"))
    assert not s.reusable(p1)                    # result, but no key from a run
    s.store_key(p1)
    assert s.reusable(p1)
    s.set_frequency({"fstep": 2.0})
    assert not s.reusable(p1)                    # the frequencies changed since


def test_reuse_finds_the_same_piece_under_another_name(tmp_path):
    path = make_set(tmp_path)
    data = json.load(open(path))
    s = PieceSet(path)
    p1 = s.rows()[0]
    fake_result(p1, LINE.line(100e-6, "m"))
    s.store_key(p1)
    # p1 redrawn as p3: same fingerprint and settings, another folder
    folder = tmp_path / "whole_pieces"
    _simcfg(folder / "p3.simcfg", json.load(open(p1["simcfg"]))["saved_values"]["GdsFile"], "p3", str(folder), 5.0,
            piece="p3", fingerprint="fp-p1")
    data["pieces"]["pieces"][0] = dict(data["pieces"]["pieces"][0], name="p3", simcfg="whole_pieces/p3.simcfg")
    json.dump(data, open(path, "w"))
    s = PieceSet(path)
    p3 = s.rows()[0]
    assert p3["piece"] == "p3" and not os.path.isfile(PieceSet.result(p3))
    assert s.reusable(p3) and os.path.isfile(PieceSet.result(p3))


# ---------- the window ----------

def open_window(monkeypatch):
    module = __import__("setupEM.setupEM", fromlist=["MainWindow"])
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    module.saved_values.clear()
    return module.MainWindow(), module


def table_rows(tab):
    return [(tab.runs_table.item(r, 0).text().split()[0], tab.runs_table.item(r, 0).checkState() == Qt.Checked,
             tab.runs_table.item(r, 1).text() if tab.runs_table.item(r, 1) else "")
            for r in range(tab.runs_table.rowCount())]


def test_window_opens_the_set_and_switches_pieces(monkeypatch, app, messages, tmp_path):
    path = make_set(tmp_path)
    window, module = open_window(monkeypatch)
    tab = window.create_model_tab
    window.load_configuration_from_file(path)
    assert window.piece_set is not None and window.current_piece["piece"] == "p1"
    assert window.saved_values["refined_cellsize"] == 5.0 and window.saved_values["model_basename"] == "p1"
    assert [(n, t) for n, t, _ in table_rows(tab)] == [("p1", True), ("p2", True), ("full", False)]
    assert not tab.runs_group.isHidden() and not tab.piece_controls.isHidden()
    assert len([m for m in messages if m[0] == "information"]) == 1      # one "Loaded" box, for the set
    assert not window.mesh_tab.piece_bar.isHidden() and "xmax cut to p2" in window.mesh_tab.piece_bar_label.text()
    assert window.mesh_tab._split_locked_sides == {"xmax": "cut to p2: port wall"}
    tab.runs_table.selectRow(1)
    assert window.current_piece["piece"] == "p2" and window.saved_values["refined_cellsize"] == 5.0
    # a frequency and a mesh change in p2 reach every piece when p2 is left
    window.frequencies_tab.stop_edit.setText("70")
    window.mesh_tab.refinement_edit.setText("4")
    tab.runs_table.selectRow(0)
    assert window.current_piece["piece"] == "p1" and float(window.saved_values["fstop"]) == 70.0
    assert window.saved_values["refined_cellsize"] == 4.0
    for name in ("p1", "p2", "full"):
        cfg = json.load(open(tmp_path / "whole_pieces" / f"{name}.simcfg"))
        assert float(cfg["saved_values"]["fstop"]) == 70.0 and cfg.get("piece") == name
        assert cfg["saved_values"]["refined_cellsize"] == 4.0
    assert float(json.load(open(path))["saved_values"]["fstop"]) == 70.0
    assert "Changed for all pieces" in tab.log_area.toPlainText()
    # p2 with its own mesh: its mesh change stays its own
    tab.runs_table.selectRow(1)
    window.mesh_tab.piece_own_check.setChecked(True)
    window.mesh_tab.refinement_edit.setText("2")
    tab.runs_table.selectRow(0)
    assert window.saved_values["refined_cellsize"] == 4.0
    assert json.load(open(tmp_path / "whole_pieces" / "p2.simcfg"))["saved_values"]["refined_cellsize"] == 2.0
    assert "own mesh" in tab.runs_table.item(1, 0).text()
    # another file closes the set
    other = _simcfg(tmp_path / "other.simcfg", _gds(tmp_path / "other.gds", 0, 100), "other", None, 5.0)
    window.load_configuration_from_file(other)
    assert window.piece_set is None and tab.runs_group.isHidden()
    window._script_dirty = False
    window.close()


def test_start_with_every_piece_unchanged_only_joins(monkeypatch, app, messages, tmp_path):
    path = make_set(tmp_path)
    s = PieceSet(path)
    rows = s.rows()
    fake_result(rows[0], LINE.line(100e-6, "m"))
    fake_result(rows[1], LINE.line(250e-6, "m").flipped())
    window, module = open_window(monkeypatch)
    tab = window.create_model_tab
    window.load_configuration_from_file(path)
    window.leave_piece(window.current_piece)      # p1's settings as the window writes them, then the keys
    for row in PieceSet(path).rows()[:2]:
        PieceSet(path).store_key(row)
    started = []
    monkeypatch.setattr(tab, "_create_mesh_now", lambda: started.append("mesh"))
    tab.start_simulation()
    assert started == [] and tab._piece_runner is None
    log = tab.log_area.toPlainText()
    assert "reused: p1, p2" in log and "joined 2 pieces" in log
    assert [st for _, _, st in table_rows(tab)][:2] == ["unchanged, reused", "unchanged, reused"]
    joined = os.path.join(str(tmp_path), "palace_model", "whole_data", "output", "whole", "whole_deembedded.s2p")
    assert np.max(abs(rf.Network(joined).s - LINE.line(350e-6, "m").s)) < 1e-8
    # View S-Parameters: the joined result first and shown, whichever piece is selected
    from setupEM.result_viewer import ResultViewerWindow
    viewer = ResultViewerWindow(window)
    top = viewer.file_list.topLevelItem(0)
    assert top.text(0).startswith("Joined result of whole") and top.child(0).text(0).startswith("🔗 whole_deembedded")
    assert viewer._checked_paths == {joined.replace("\\", "/")}
    viewer.close()
    window._script_dirty = False
    window.close()


def test_a_changed_piece_runs_again(monkeypatch, app, messages, tmp_path):
    path = make_set(tmp_path)
    s = PieceSet(path)
    rows = s.rows()
    fake_result(rows[0], LINE.line(100e-6, "m"))
    fake_result(rows[1], LINE.line(250e-6, "m").flipped())
    window, module = open_window(monkeypatch)
    tab = window.create_model_tab
    window.load_configuration_from_file(path)
    window.leave_piece(window.current_piece)
    for row in PieceSet(path).rows()[:2]:
        PieceSet(path).store_key(row)
    tab.runs_table.selectRow(1)
    window.mesh_tab.piece_own_check.setChecked(True)        # a finer mesh in p2 only
    window.mesh_tab.refinement_edit.setText("2")
    started = []
    monkeypatch.setattr(tab, "_ask_queue_previous_results", lambda runs: True)    # p2's old result: keep it
    monkeypatch.setattr(tab, "_create_mesh_now", lambda: started.append(window.current_piece["piece"]))
    tab.start_simulation()
    assert started == ["p2"]                     # p1 reused, p2 meshed (the process is not started here)
    assert "reused: p1" in tab.log_area.toPlainText()
    window._script_dirty = False
    window.close()


def test_a_piece_with_a_script_keeps_its_changes_and_full_stays_unticked(monkeypatch, app, messages, tmp_path):
    path = make_set(tmp_path)
    window, module = open_window(monkeypatch)
    tab = window.create_model_tab
    window.load_configuration_from_file(path)
    # p2 gets a model script, as Create Mesh writes it (the script and its .simcfg)
    tab.runs_table.selectRow(1)
    p2 = window.current_piece
    script = os.path.join(p2["model_dir"], "p2.py")
    code = tab.model_code(script)
    tab.write_model_code(script, code)
    window.write_model_config(script)
    tab.runs_table.selectRow(0)
    tab.runs_table.selectRow(1)                  # p2 again: now from its script, edited in place
    assert window.script_model is not None and os.path.samefile(window.script_model.path, script)
    window.saved_values["refined_cellsize"] = 2.5
    window.load_all_tabs()
    tab.runs_table.selectRow(0)                  # leaving p2 writes the change into its script
    assert "settings['refined_cellsize'] = 2.5" in open(script).read()
    tab.runs_table.selectRow(1)
    assert float(window.saved_values["refined_cellsize"]) == 2.5
    assert [t for _, t, _ in table_rows(tab)] == [True, True, False]    # full check model still unticked
    window._script_dirty = False
    window.close()


def test_start_simulation_runs_unattended_and_keeps_a_current_mesh(monkeypatch, app, messages, tmp_path):
    path = make_set(tmp_path)
    window, module = open_window(monkeypatch)
    tab = window.create_model_tab
    window.load_configuration_from_file(path)
    window.leave_piece(window.current_piece)
    s = PieceSet(path)
    p1 = s.rows()[0]
    os.makedirs(p1["sim_path"], exist_ok=True)
    open(os.path.join(p1["sim_path"], "config.json"), "w").write("{}")
    s.store_mesh_key(p1)                                    # p1 was meshed with these settings (Create Mesh)
    meshed, simulated = [], []
    monkeypatch.setattr(tab, "_ask_queue_previous_results", lambda runs: True)
    monkeypatch.setattr(tab, "_create_mesh_now", lambda: meshed.append(window.current_piece["piece"]))
    monkeypatch.setattr(tab, "run_model", lambda: simulated.append((window.current_piece["piece"],
                                                                   tab.model_launch_args("p1.py"))))
    tab.start_simulation()
    assert meshed == [] and simulated and simulated[0][0] == "p1", "p1 straight to the simulation"
    assert "the mesh is up to date" in tab.log_area.toPlainText()
    # while the pieces run, a model script runs without the gmsh window
    tab._piece_runner = {"simulate": True}
    args = tab.model_launch_args("p1.py")
    assert os.path.basename(args[0]) == "run_with_overrides.py" and "no_gui=True" in args
    tab._piece_runner = None
    assert "no_gui=True" not in tab.model_launch_args("p1.py"), "Create Mesh by hand shows the mesh as before"
    window._script_dirty = False
    window.close()
