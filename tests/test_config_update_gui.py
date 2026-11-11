# GUI test (offscreen) for updating a script edited in place from a *.simcfg of the same layout:
#  - opening a .simcfg linked to a model script (written_with / model_script): continue with the script, asked only
#    when the two have other settings; a script that only reads the same GDS is not linked; Create Model links;
#    the command line options --continue-with-script / --start-from-config,
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

    def write_config(name, ports, age, **more):
        data = {"application": "setupEM", "data_format": "1.0",
                "saved_values": {"unit": 1e-06, "GdsFile": "chip_em.gds", "SubstrateFile": os.path.abspath(XML),
                                 "cellname": "", "purpose": [0], "fstart": 0.0, "fstop": 7.0, "fstep": 1.0},
                "ports": ports}
        for key, value in more.items():
            if key in data["saved_values"] or key in ("margin", "boundary"):
                data["saved_values"][key] = value
            else:
                data[key] = value
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


def write_snapshot(folder, script_name, age, **values):
    """The settings Create Model wrote with the script (<model>.simcfg, written_with the script)."""
    saved = {"unit": 1e-06, "GdsFile": "chip_em.gds", "fstart": 0.0, "fstop": 100.0, "fstep": 1.0,
             "model_basename": os.path.splitext(script_name)[0]}
    saved.update(values)
    path = folder / (os.path.splitext(script_name)[0] + ".simcfg")
    path.write_text(json.dumps({"application": "setupEM", "saved_values": saved, "ports": [],
                                "written_with": script_name, "elmer_mode": False}), encoding="utf-8")
    set_age(path, age)
    return str(path)


def asking(win, monkeypatch, answer):
    asked = []
    monkeypatch.setattr(win, "_ask_continue_with_script",
                        lambda c, s, d: asked.append((os.path.basename(s), d)) or answer)
    return asked


def test_linked_script_with_other_settings_asks(win, layout, monkeypatch):
    folder, write_config = layout
    # the usual order: Create Model wrote the script (and its settings), then the layout tool exported again
    set_age(folder / "chip_em.py", 100)
    write_snapshot(folder, "chip_em.py", 100)
    config = write_config("export.simcfg", [via(1, 201), via(2, 202, "-Z"), via(3, 203)], age=10,
                          model_script="chip_em.py")
    asked = asking(win, monkeypatch, "script")
    win.load_configuration_from_file(config)
    assert asked == [("chip_em.py", ["fstop: 100 in the script, 7 in the config"])]
    assert win.script_model is not None and os.path.basename(win.script_model.path) == "chip_em.py"
    code = code_of(win)
    assert "settings['fstop'] = 100.0e9   # my own note" in code      # the script's settings and comment stay
    lines = port_lines(code)
    assert len(lines) == 3
    assert "source_layernum=202" in lines[1] and "'-Z'" in lines[1]
    assert "source_layernum=203" in lines[2]


def test_linked_script_with_the_same_settings_continues(win, layout, monkeypatch):
    """Only ports and layout settings differ: continuing with the script loses nothing, no question."""
    folder, write_config = layout
    set_age(folder / "chip_em.py", 100)
    write_snapshot(folder, "chip_em.py", 100, margin=[50.0, 50.0, 50.0, 50.0])
    config = write_config("export.simcfg", [via(1, 201), via(2, 202, "-Z")], age=10, fstop=100.0, margin=50,
                          model_script="chip_em.py")
    monkeypatch.setattr(win, "_ask_continue_with_script", lambda *a: pytest.fail("asked"))
    win.load_configuration_from_file(config)
    assert win.script_model is not None and os.path.basename(win.script_model.path) == "chip_em.py"
    assert len(port_lines(code_of(win))) == 2
    assert win.user_inputs_file == config, "Create Model keeps the link"


def test_linked_script_changed_since_its_settings_asks(win, layout, monkeypatch):
    """The script was changed after Create Model saved its settings: they can't be compared, the user decides."""
    folder, write_config = layout
    write_snapshot(folder, "chip_em.py", 100, fstop=7.0)
    set_age(folder / "chip_em.py", 50)
    config = write_config("export.simcfg", [via(1, 201)], age=10, model_script="chip_em.py")
    asked = asking(win, monkeypatch, "cancel")
    win.load_configuration_from_file(config)
    assert asked == [("chip_em.py", None)]
    assert win.script_model is None and getattr(win, "user_inputs_file", None) != config, "cancel: nothing loaded"


def test_unlinked_script_is_not_asked(win, layout, monkeypatch):
    """A script that only reads the same GDS file is not taken for the model: the config, and a note in the log."""
    folder, write_config = layout
    set_age(folder / "chip_em.py", 100)
    config = write_config("export.simcfg", [via(1, 201)], age=10)
    monkeypatch.setattr(win, "_ask_continue_with_script", lambda *a: pytest.fail("asked"))
    win.load_configuration_from_file(config)
    assert win.script_model is None and win.user_inputs_file == config
    log = win.create_model_tab.log_area.toPlainText()
    assert "chip_em.py also reads this layout" in log and "File > Edit *.py model in place" in log


def test_create_model_links_the_config(win, layout, monkeypatch):
    """Create Model of a model opened from the export, under another name: the export names the script, and the
    next open continues with it."""
    folder, write_config = layout
    os.remove(folder / "chip_em.py")
    config = write_config("export.simcfg", [via(1, 201)], age=100)
    win.load_configuration_from_file(config)
    win.save_all_tabs()
    script = folder / "models" / "amp.py"
    script.parent.mkdir()
    shutil.copy(os.path.join(HERE, "fixtures", "generated_setupEM_palace.py"), script)
    model_config = win.write_model_config(str(script))
    assert json.loads(open(model_config).read())["written_with"] == "amp.py"
    data = json.loads(open(config).read())
    assert data["model_script"] == "models/amp.py"
    assert "written_with" not in data and data["ports"][0]["source_layernum"] == 201, "the rest is kept"

    # the next open: the same settings, no question, the script edited in place
    monkeypatch.setattr(win, "_ask_continue_with_script", lambda *a: pytest.fail("asked"))
    monkeypatch.setattr(win, "_in_place_problem", lambda path: "")
    assert win._model_script_for_config(config, data) == (os.path.normpath(str(script)), False)
    assert win._script_config_differences(config, data, str(script)) == []


def test_edit_in_place_after_the_note_links(win, layout):
    """The log names an unlinked script; edited in place from there, its next Create Model links the export."""
    folder, write_config = layout
    config = write_config("export.simcfg", [via(1, 201)], age=100)
    win.load_configuration_from_file(config)
    assert win.script_model is None
    script = str(folder / "chip_em.py")
    win.edit_py_model(script)
    assert win.script_model is not None
    win.write_model_config(script)
    assert json.loads(open(config).read())["model_script"] == "chip_em.py"


def test_create_model_links_only_the_same_layout(win, layout):
    folder, write_config = layout
    os.remove(folder / "chip_em.py")
    config = write_config("export.simcfg", [via(1, 201)], age=100)
    win.load_configuration_from_file(config)
    win.save_all_tabs()
    win.saved_values["GdsFile"] = str(folder / "other.gds")
    win.write_model_config(str(folder / "amp.py"))
    assert "model_script" not in json.loads(open(config).read())
    # a model named as the export: Create Model writes that file itself (written_with), no model_script
    win.saved_values["GdsFile"] = str(folder / "chip_em.gds")
    win.write_model_config(str(folder / "export.py"))
    data = json.loads(open(config).read())
    assert data["written_with"] == "export.py" and "model_script" not in data


def test_command_line_choice(win, layout, monkeypatch):
    """--continue-with-script / --start-from-config answer the question, also where setupEM would not ask."""
    from setupEM import setupEM as module
    folder, write_config = layout
    set_age(folder / "chip_em.py", 100)
    write_snapshot(folder, "chip_em.py", 100)
    config = write_config("export.simcfg", [via(1, 201), via(2, 202)], age=10, model_script="chip_em.py")
    monkeypatch.setattr(module.MainWindow, "_ask_continue_with_script", lambda *a: pytest.fail("asked"))
    monkeypatch.setattr(module.MainWindow, "_add_recent_file", lambda *a: None)
    windows = []
    try:
        window = module.open_main_window(module.parse_arguments(["-simcfg", config, "--continue-with-script"]))
        windows.append(window)
        assert window.script_model is not None and os.path.basename(window.script_model.path) == "chip_em.py"
        assert len(port_lines(code_of(window))) == 2

        written = write_config("chip_em.simcfg", [via(1, 201)], age=10, written_with="chip_em.py")
        window = module.open_main_window(module.parse_arguments(["-simcfg", written, "--start-from-config"]))
        windows.append(window)
        assert window.script_model is None and window.user_inputs_file == written
    finally:
        for window in windows:
            window._script_dirty = False
            window.close()
    with pytest.raises(SystemExit):
        module.parse_arguments(["--continue-with-script"])
    with pytest.raises(SystemExit):
        module.parse_arguments(["-simcfg", config, "--continue-with-script", "--start-from-config"])


def test_open_config_written_with_script(win, layout, monkeypatch):
    """A config that Create Model wrote with the script (written_with), then updated by the layout tool's export:
    continue the script without asking, ports from the config."""
    folder, write_config = layout
    set_age(folder / "chip_em.py", 100)
    config = write_config("chip_em.simcfg", [via(1, 201), via(2, 202, "-Z")], age=10)
    data = json.loads(open(config, encoding="utf-8").read())
    data["written_with"] = "chip_em.py"
    open(config, "w", encoding="utf-8").write(json.dumps(data))
    set_age(config, 10)
    monkeypatch.setattr(win, "_ask_continue_with_script", lambda *a: pytest.fail("asked"))
    win.load_configuration_from_file(config)
    assert win.script_model is not None and os.path.basename(win.script_model.path) == "chip_em.py"
    assert len(port_lines(code_of(win))) == 2


def test_open_config_without_script_asks_nothing(win, layout, monkeypatch):
    folder, write_config = layout
    os.remove(folder / "chip_em.py")                # no model script for this layout yet
    config = write_config("chip_em.simcfg", [via(1, 201)], age=10)
    monkeypatch.setattr(win, "_ask_continue_with_script", lambda *a: pytest.fail("asked"))
    win.load_configuration_from_file(config)
    assert win.script_model is None and win.user_inputs_file == config
    # Load Config / Import / Edit in place start in the folder of the config, not the working folder
    assert os.path.normcase(win._open_dialog_folder()) == os.path.normcase(str(folder))


def test_start_from_config(win, layout, monkeypatch):
    folder, write_config = layout
    set_age(folder / "chip_em.py", 100)
    write_snapshot(folder, "chip_em.py", 100)
    config = write_config("export.simcfg", [via(1, 201)], age=10, model_script="chip_em.py")
    asked = asking(win, monkeypatch, "config")
    win.load_configuration_from_file(config)
    assert len(asked) == 1
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
