# GUI test (offscreen) for updating a script edited in place from a *.simcfg of the same layout:
#  - opening a .simcfg when a newer model script for the same GDS exists ("Continue with the script"),
#  - loading a .simcfg while a script is edited in place ("Update the script from it" / "Open it as a new model"),
#  - the info bar notice about a newer .simcfg, with its "Update from it" button.
# Needs the sibling repo gds2palace_ihp_sg13g2 (GDS and stackup); skipped without it.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import shutil
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
G2P = os.path.join(HERE, "..", "..", "gds2palace_ihp_sg13g2")
GDS = os.path.join(G2P, "workflow", "inductor_500pH_with_ports.gds")
XML = os.path.join(G2P, "XML_stackup", "legacy", "SG13G2_200um.xml")
pytestmark = pytest.mark.skipif(not (os.path.isfile(GDS) and os.path.isfile(XML)),
                                reason="needs gds2palace_ihp_sg13g2 next to this repo")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


def via(n, layer, direction="Z", frm="Metal1", to="TopMetal2"):
    return {"portnumber": n, "source_layernum": layer, "target_layername": None, "from_layername": frm,
            "to_layername": to, "direction": direction, "port_Z0": 50.0, "voltage": 1.0}


def set_age(path, seconds):
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


@pytest.fixture
def win(monkeypatch):
    app = QApplication.instance() or QApplication([])
    for name, answer in (("information", QMessageBox.Ok), ("warning", QMessageBox.Ok),
                         ("question", QMessageBox.Yes)):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, answer=answer, **k: answer))
    from setupEM.setupEM import MainWindow
    window = MainWindow()
    yield window
    window._script_dirty = False
    window.close()
    app.processEvents()


@pytest.fixture
def layout(tmp_path):
    """A setupEM script with its own comment, and a .simcfg exported later with other ports and frequencies."""
    gds = tmp_path / "chip_em.gds"
    shutil.copy(GDS, gds)
    script = (open(os.path.join(HERE, "fixtures", "generated_setupEM_palace.py"), encoding="utf-8").read()
              .replace("settings['GdsFile'] = 'inductor_500pH_with_ports.gds'",
                       f"settings['GdsFile'] = {str(gds).replace(os.sep, '/')!r}")
              .replace("settings['SubstrateFile'] = '../XML_stackup/legacy/SG13G2_200um.xml'",
                       f"settings['SubstrateFile'] = {os.path.abspath(XML).replace(os.sep, '/')!r}")
              .replace("settings['fstop'] = 100.0e9", "settings['fstop'] = 100.0e9   # my own note"))
    assert "# my own note" in script and str(gds).replace(os.sep, "/") in script
    (tmp_path / "chip_em.py").write_text(script, encoding="utf-8")

    def write_config(name, ports, age):
        data = {"application": "setupEM", "data_format": "1.0",
                "saved_values": {"unit": 1e-06, "GdsFile": "chip_em.gds", "SubstrateFile": os.path.abspath(XML),
                                 "cellname": "", "purpose": [0], "fstart": 0.0, "fstop": 7.0, "fstep": 1.0},
                "ports": ports}
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        set_age(path, age)
        return str(path)

    return tmp_path, write_config


def code_of(win):
    win.save_all_tabs()
    return win.preserve_mode_patch(win.script_model.path)[0]


def port_lines(code):
    return [line for line in code.splitlines() if "simulation_port(" in line and not line.lstrip().startswith("#")]


def test_open_config_continues_with_script(win, layout, monkeypatch):
    folder, write_config = layout
    # the usual order: Create Model wrote the script, then the layout tool exported again (a newer config)
    set_age(folder / "chip_em.py", 100)
    config = write_config("chip_em.simcfg", [via(1, 201), via(2, 202, "-Z"), via(3, 203)], age=10)
    asked = []
    monkeypatch.setattr(win, "_ask_continue_with_script",
                        lambda c, s: asked.append(os.path.basename(s)) or "script")
    win.load_configuration_from_file(config)
    assert asked == ["chip_em.py"]
    assert win.script_model is not None and os.path.basename(win.script_model.path) == "chip_em.py"
    code = code_of(win)
    assert "settings['fstop'] = 100.0e9   # my own note" in code      # the script's settings and comment stay
    lines = port_lines(code)
    assert len(lines) == 3
    assert "source_layernum=202" in lines[1] and "'-Z'" in lines[1]
    assert "source_layernum=203" in lines[2]


def test_open_config_without_script_asks_nothing(win, layout, monkeypatch):
    folder, write_config = layout
    os.remove(folder / "chip_em.py")                # no model script for this layout yet
    config = write_config("chip_em.simcfg", [via(1, 201)], age=10)
    monkeypatch.setattr(win, "_ask_continue_with_script", lambda c, s: pytest.fail("asked"))
    win.load_configuration_from_file(config)
    assert win.script_model is None and win.user_inputs_file == config
    # Load Config / Import / Edit in place start in the folder of the config, not the working folder
    assert os.path.normcase(win._open_dialog_folder()) == os.path.normcase(str(folder))


def test_start_from_config(win, layout, monkeypatch):
    folder, write_config = layout
    config = write_config("chip_em.simcfg", [via(1, 201)], age=100)
    set_age(folder / "chip_em.py", 10)
    monkeypatch.setattr(win, "_ask_continue_with_script", lambda c, s: "config")
    win.load_configuration_from_file(config)
    assert win.script_model is None and win.user_inputs_file == config


def test_load_config_while_editing_in_place(win, layout, monkeypatch):
    folder, write_config = layout
    script = str(folder / "chip_em.py")
    win.edit_py_model(script)
    assert win.script_model is not None
    config = write_config("chip_em.simcfg", [via(1, 201), via(2, 202), via(3, 204)], age=0)
    seen = []

    def ask(path, data):
        win.save_all_tabs()
        seen.append(win.config_update_differences(data, str(folder)))
        return "update"
    monkeypatch.setattr(win, "_ask_update_script_from_config", ask)
    win.load_configuration_from_file(config)
    assert seen and seen[0] == ["port 3: new, via Metal1 -> TopMetal2 (Z) on layer 204"]
    assert win.script_model is not None
    lines = port_lines(code_of(win))
    assert len(lines) == 3 and "source_layernum=204" in lines[2]

    monkeypatch.setattr(win, "_ask_update_script_from_config", lambda p, d: "new")
    win._script_dirty = False
    monkeypatch.setattr(win, "confirm_unsaved_script", lambda: True)
    win.load_configuration_from_file(config)
    assert win.script_model is None and win.user_inputs_file == config


def test_notice_about_newer_config(win, layout):
    folder, write_config = layout
    script = folder / "chip_em.py"
    set_age(script, 100)
    win.edit_py_model(str(script))
    win.hide_info_bar()
    write_config("chip_em.simcfg", [via(1, 201), via(2, 202, "-Z")], age=10)
    win.refresh_config_notice()
    assert not win.info_bar.isHidden() and win.info_bar_button.text() == "Update from it"
    assert "chip_em.simcfg" in win.info_bar_label.text()
    win._info_bar_action_clicked()
    lines = port_lines(code_of(win))
    assert "'-Z'" in lines[1]
    win.hide_info_bar()
    win.refresh_config_notice()                     # the settings now match the config: no notice
    assert win.info_bar.isHidden()
