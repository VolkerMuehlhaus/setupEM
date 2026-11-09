"""join_pieces: the results of a split region joined at the cuts (ideal lines: exact), the port order of the joined
result, and the joins it refuses."""
import json
import os

import numpy as np
import pytest
import skrf as rf
from skrf.media import DefinedGammaZ0

from setupEM.join_pieces import JoinError, join, join_all

FREQ = rf.Frequency(1, 50, 50, "GHz")
LINE = DefinedGammaZ0(FREQ, z0_port=50, z0=62.0, gamma=1j * 2 * np.pi * FREQ.f * 2.0 / 3e8 + 20.0)


def _write_piece(folder, name, network, calibrated=(1, 2), where="at the wall"):
    """A piece's simcfg and its result where setupEM puts it."""
    os.makedirs(folder, exist_ok=True)
    simcfg = os.path.join(folder, name + ".simcfg")
    with open(simcfg, "w") as f:
        json.dump({"application": "setupEM", "saved_values": {"sim_path": folder, "model_basename": name}}, f)
    out = os.path.join(folder, "palace_model", f"{name}_data", "output", name)
    os.makedirs(out, exist_ok=True)
    stem = os.path.join(out, f"{name}_deembedded")
    network.write_touchstone(stem)
    path = stem + f".s{network.nports}p"
    text = open(path).read()
    header = "".join(f"!port {p}: calibrated at port wall xmin, reference plane {where}\n" for p in calibrated)
    open(path, "w").write(header + text)


def _set(tmp_path, links, external, pieces):
    data = {"application": "setupEM", "saved_values": {"sim_path": str(tmp_path), "model_basename": "whole"},
            "pieces": {"version": 1, "folder": "whole_pieces", "pieces": pieces, "links": links, "external": external}}
    path = tmp_path / "whole.simcfg"
    path.write_text(json.dumps(data))
    return str(path)


def _two_lines(tmp_path, len1=100e-6, len2=250e-6, calibrated=(1, 2), freq2=None):
    folder = str(tmp_path / "whole_pieces")
    p1 = LINE.line(len1, "m")
    line2 = LINE if freq2 is None else DefinedGammaZ0(freq2, z0_port=50, z0=62.0, gamma=1j * 2 * np.pi * freq2.f * 2 / 3e8)
    p2 = line2.line(len2, "m")
    _write_piece(folder, "p1", p1, calibrated)       # port 1 outer, port 2 at the cut
    _write_piece(folder, "p2", p2.flipped())   # p2: port 1 outer (right), port 2 at the cut (left)
    pieces = [{"name": "p1", "simcfg": "whole_pieces/p1.simcfg", "ports": {"1": 1, "2": 3}},
              {"name": "p2", "simcfg": "whole_pieces/p2.simcfg", "ports": {"1": 2, "2": 4}}]
    links = [{"ports": [["p1", 2], ["p2", 2]]}]
    external = [{"port": 1, "piece": "p1", "piece_port": 1, "layout_port": 1},
                {"port": 2, "piece": "p2", "piece_port": 1, "layout_port": 2}]
    return _set(tmp_path, links, external, pieces)


def test_two_lines_join_to_the_long_line(tmp_path):
    path = _two_lines(tmp_path)
    out, joined, _ = join(path)
    whole = LINE.line(350e-6, "m")
    assert np.max(abs(joined.s - whole.s)) < 1e-10
    assert out.endswith(os.path.join("palace_model", "whole_data", "output", "whole", "whole_deembedded.s2p"))
    head = open(out).read().splitlines()[:3]
    assert head[0].startswith("! joined by setupEM from the pieces p1, p2")
    assert head[1] == "!port 1: p1 port 1 (layout port 1)" and head[2] == "!port 2: p2 port 1 (layout port 2)"
    assert np.max(abs(rf.Network(out).s - whole.s)) < 1e-8


def test_port_order_follows_the_outer_port_numbers(tmp_path):
    path = _two_lines(tmp_path)
    data = json.load(open(path))
    data["pieces"]["external"] = [{"port": 1, "piece": "p2", "piece_port": 1, "layout_port": 2},
                                  {"port": 2, "piece": "p1", "piece_port": 1, "layout_port": 1}]
    json.dump(data, open(path, "w"))
    _, joined, _ = join(path, write=False)
    whole = LINE.line(350e-6, "m").flipped()     # port 1 now the right end
    assert np.max(abs(joined.s - whole.s)) < 1e-10


def test_three_pieces_in_a_row(tmp_path):
    folder = str(tmp_path / "whole_pieces")
    _write_piece(folder, "p1", LINE.line(100e-6, "m"))
    _write_piece(folder, "p2", LINE.line(50e-6, "m"))            # port 1 left cut, port 2 right cut
    _write_piece(folder, "p3", LINE.line(70e-6, "m").flipped())
    pieces = [{"name": n, "simcfg": f"whole_pieces/{n}.simcfg", "ports": {"1": 0, "2": 0}} for n in ("p1", "p2", "p3")]
    links = [{"ports": [["p1", 2], ["p2", 1]]}, {"ports": [["p2", 2], ["p3", 2]]}]
    external = [{"port": 1, "piece": "p1", "piece_port": 1, "layout_port": 1},
                {"port": 2, "piece": "p3", "piece_port": 1, "layout_port": 2}]
    path = _set(tmp_path, links, external, pieces)
    _, joined, _ = join(path, write=False)
    assert np.max(abs(joined.s - LINE.line(220e-6, "m").s)) < 1e-10


def test_missing_result(tmp_path):
    path = _two_lines(tmp_path)
    os.remove(os.path.join(str(tmp_path), "whole_pieces", "palace_model", "p2_data", "output", "p2", "p2_deembedded.s2p"))
    with pytest.raises(JoinError, match="p2: no result"):
        join(path)


def test_uncalibrated_cut_port_is_refused(tmp_path):
    path = _two_lines(tmp_path, calibrated=(1,))
    with pytest.raises(JoinError, match="p1 port 2 on a cut is not calibrated"):
        join(path)


def test_different_frequencies_are_refused(tmp_path):
    path = _two_lines(tmp_path, freq2=rf.Frequency(1, 50, 25, "GHz"))
    with pytest.raises(JoinError, match="frequencies of p2 differ"):
        join(path)


def test_a_port_neither_outer_nor_linked_is_refused(tmp_path):
    path = _two_lines(tmp_path)
    data = json.load(open(path))
    data["pieces"]["links"] = []
    json.dump(data, open(path, "w"))
    with pytest.raises(JoinError, match="neither outer ports nor on a cut"):
        join(path)


def test_join_all_without_dc_results(tmp_path):
    path = _two_lines(tmp_path)
    messages = join_all(path)
    assert len(messages) == 1 and messages[0].startswith("joined 2 pieces")


def test_a_shifted_reference_plane_on_a_cut_is_refused(tmp_path):
    path = _two_lines(tmp_path)
    folder = str(tmp_path / "whole_pieces")
    _write_piece(folder, "p1", LINE.line(100e-6, "m"), where="5 um into the layout")
    with pytest.raises(JoinError, match="reference plane moved"):
        join(path)
