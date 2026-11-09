# The de-embedded result in setupEM: View S-Parameters lists it and shows it next to the raw result; Model Fit takes
# the calibrated result (_dc_deembedded, else _deembedded) for a model with port walls or a moved reference plane,
# the raw one otherwise.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import numpy as np
import pytest
import skrf as rf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def results(tmp_path, kinds):
    """A model's output folder with the result files of the kinds given ('', '_deembedded', ...)."""
    out = tmp_path / "palace_model" / "amp_data" / "output" / "amp"
    out.mkdir(parents=True)
    net = rf.Network(frequency=rf.Frequency(1, 10, 10, "GHz"), s=np.zeros((10, 2, 2)) + 0.1, z0=50)
    for kind in kinds:
        net.write_touchstone(str(out / f"amp{kind}"))
    return out


def window(monkeypatch, tmp_path, boundary):
    module = __import__("setupEM.setupEM", fromlist=["MainWindow"])
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    module.saved_values.clear()
    win = module.MainWindow()
    win.saved_values.update(sim_path=str(tmp_path).replace("\\", "/"), model_basename="amp", boundary=boundary)
    return win


def test_viewer_shows_raw_and_deembedded(monkeypatch, app, tmp_path):
    out = results(tmp_path, ["", "_deembedded", "_dc", "_dc_deembedded"])
    win = window(monkeypatch, tmp_path, ["ABC"] * 6)
    from setupEM.result_viewer import ResultViewerWindow
    viewer = ResultViewerWindow(win)
    checked = {os.path.basename(p) for p in viewer._checked_paths}
    assert checked == {"amp.s2p", "amp_deembedded.s2p"}, checked
    assert viewer.include_deembedded_cb.isChecked() and not viewer.include_dc_cb.isChecked()
    viewer.close()
    win._script_dirty = False
    win.close()


@pytest.mark.parametrize("boundary, expected", [
    (["PORTWALL", "PORTWALL", "ABC", "ABC", "ABC", "ABC"], "amp_dc_deembedded.s2p"),
    (["ABC"] * 6, "amp.s2p")])
def test_model_fit_takes_the_calibrated_result_with_port_walls(monkeypatch, app, tmp_path, boundary, expected):
    results(tmp_path, ["", "_deembedded", "_dc", "_dc_deembedded"])
    win = window(monkeypatch, tmp_path, boundary)
    tab = win.create_model_tab
    import importlib.util
    started = []
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object())
    monkeypatch.setattr(tab.process, "start", lambda program, args: started.append(args))
    tab.open_model_fit()
    assert started and os.path.basename(started[0][-1]) == expected, started
    win._script_dirty = False
    win.close()
