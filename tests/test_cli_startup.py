# The command lines at start (issue #52): --help and --version of the five commands before Qt is started, files
# that cannot be opened reported on stderr with exit code 2, the solver of a settings file kept (Elmer), the
# modules without Qt importable without it, and the command lines of join_pieces / thermal_results.
#
# Run from the repo root:   python -m pytest tests

import json
import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import setupEM  # noqa: E402
from setupEM import cli_common, join_pieces  # noqa: E402

COMMANDS = {"setupEM": "setupEM", "setupThermal": "setupThermal", "stackupEditor": "stackupEditor",
            "result_viewer": "resultViewer", "field_viewer": "fieldViewer"}


def no_display(*args, **kwargs):
    raise AssertionError("QApplication created: this needs a display")


@pytest.mark.parametrize("module_name", COMMANDS)
@pytest.mark.parametrize("option", ["--help", "--version"])
def test_help_and_version_before_qt(monkeypatch, capsys, module_name, option):
    module = __import__(f"setupEM.{module_name}", fromlist=["main"])
    monkeypatch.setattr(module, "QApplication", no_display)
    monkeypatch.setattr(sys, "argv", [module_name, option])
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    if option == "--version":
        assert out.strip() == f"{COMMANDS[module_name]} {setupEM.__version__}"
    else:
        assert out.startswith(f"usage: {COMMANDS[module_name]}")


@pytest.mark.parametrize("module_name", COMMANDS)
def test_wrong_argument_before_qt(monkeypatch, capsys, module_name):
    module = __import__(f"setupEM.{module_name}", fromlist=["main"])
    monkeypatch.setattr(module, "QApplication", no_display)
    monkeypatch.setattr(sys, "argv", [module_name, "--no-such-option"])
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 2
    assert "--no-such-option" in capsys.readouterr().err


def test_field_viewer_screenshot_needs_no_display(monkeypatch, tmp_path):
    """--screenshot renders off-screen: Qt is told so before it starts, unless the caller chose a platform."""
    from setupEM import field_viewer

    class Started(Exception):
        pass

    def started(*args):
        raise Started(os.environ.get("QT_QPA_PLATFORM"))

    field = tmp_path / "fields.pvd"
    field.write_text("")
    monkeypatch.setattr(field_viewer, "QApplication", started)
    monkeypatch.delenv("QT_QPA_PLATFORM")
    monkeypatch.setattr(sys, "argv", ["fieldViewer", str(field), "--screenshot", str(tmp_path / "out.png")])
    with pytest.raises(Started) as started_info:
        field_viewer.main()
    assert str(started_info.value) == "offscreen"

    monkeypatch.delenv("QT_QPA_PLATFORM")
    monkeypatch.setattr(sys, "argv", ["fieldViewer", str(field)])
    with pytest.raises(Started) as started_info:
        field_viewer.main()
    assert str(started_info.value) == "None", "the interactive viewer keeps the platform's display"


# ---------------------------------------------------------------- files given at start

def write_config(path, application="setupEM", **more):
    path.write_text(json.dumps({"application": application, "saved_values": {}, **more}))
    return str(path)


def test_config_file_error(tmp_path):
    assert cli_common.config_file_error(write_config(tmp_path / "a.simcfg"), "setupEM") is None
    assert cli_common.config_file_error(write_config(tmp_path / "a.tsimcfg", "setupThermal"), "setupThermal") is None
    script = tmp_path / "model.py"
    script.write_text("settings = {}\n")
    assert cli_common.config_file_error(str(script), "setupEM") is None, "a model script is imported, as before"

    assert "file not found" in cli_common.config_file_error(str(tmp_path / "missing.simcfg"), "setupEM")
    error = cli_common.config_file_error(write_config(tmp_path / "t.tsimcfg", "setupThermal"), "setupEM")
    assert "open it with setupThermal" in error
    error = cli_common.config_file_error(write_config(tmp_path / "renamed.simcfg", "setupThermal"), "setupEM")
    assert "open it with setupThermal" in error
    error = cli_common.config_file_error(write_config(tmp_path / "e.simcfg"), "setupThermal")
    assert "open it with setupEM" in error
    broken = tmp_path / "broken.simcfg"
    broken.write_text("{ not json")
    assert "cannot be read" in cli_common.config_file_error(str(broken), "setupEM")
    assert "not a setupEM settings file" in cli_common.config_file_error(
        write_config(tmp_path / "other.simcfg", "something else"), "setupEM")
    layout = tmp_path / "layout.gds"
    layout.write_text("")
    assert "not a setupEM settings file" in cli_common.config_file_error(str(layout), "setupEM")


@pytest.mark.parametrize("module_name", ["setupEM", "setupThermal"])
@pytest.mark.parametrize("option", ["-simcfg", "-gdsfile", "-xmlfile"])
def test_missing_start_file_is_an_error_before_qt(monkeypatch, capsys, tmp_path, module_name, option):
    module = __import__(f"setupEM.{module_name}", fromlist=["main"])
    monkeypatch.setattr(module, "QApplication", no_display)
    missing = str(tmp_path / "missing.file")
    monkeypatch.setattr(sys, "argv", [module_name, option, missing])
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert f"file not found: {missing}" in captured.err and captured.out == ""


# ---------------------------------------------------------------- the solver of a settings file

@pytest.fixture
def em(monkeypatch):
    app = QApplication.instance() or QApplication([])
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    from setupEM import setupEM as module
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    monkeypatch.setattr(module.MainWindow, "_add_recent_file", lambda *a: None)
    windows = []

    def start(*arguments):
        window = module.open_main_window(module.parse_arguments(list(arguments)))
        windows.append(window)
        return window

    yield start
    for window in windows:
        window._script_dirty = False
        window.close()
    app.processEvents()


def elmer_config(em, tmp_path, elmer_mode):
    """A settings file as setupEM writes it, of an Elmer or a Palace model."""
    from setupEM import setupEM as module
    window = em()
    if elmer_mode:
        window.setElmerMode()
    window.save_all_tabs()
    data = {"application": "setupEM", "saved_values": dict(module.saved_values)}
    data.update(window.native_config_extra_struct())
    assert data["elmer_mode"] is elmer_mode
    path = tmp_path / ("elmer.simcfg" if elmer_mode else "palace.simcfg")
    path.write_text(json.dumps(data))
    return str(path)


def test_no_arguments_start_in_palace_mode(em):
    window = em()
    assert window.PalaceMode and not window.ElmerMode


def test_elmer_option_alone(em):
    window = em("--elmer")
    assert window.ElmerMode and not window.PalaceMode


def test_settings_file_of_an_elmer_model_opens_in_elmer_mode(em, tmp_path):
    """Was opened in Palace mode without --elmer (as the KLayout plug-in starts setupEM)."""
    window = em("-simcfg", elmer_config(em, tmp_path, True))
    assert window.ElmerMode and not window.PalaceMode
    assert window.optionElmer.isChecked()


def test_settings_file_of_a_palace_model(em, tmp_path):
    path = elmer_config(em, tmp_path, False)
    assert em("-simcfg", path).PalaceMode
    assert em("-simcfg", path, "--elmer").ElmerMode, "--elmer still sets Elmer"


def test_missing_file_in_the_gui_is_named(monkeypatch, em, tmp_path):
    """File > Load of a file that is gone (e.g. a recent file): 'File not found', not 'Unknown data format'."""
    window = em()
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: shown.append(title)))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda parent, title, text, *a: shown.append(title)))
    window.load_configuration_from_file(str(tmp_path / "gone.simcfg"))
    assert shown == ["File not found"]


# ---------------------------------------------------------------- modules without Qt

def test_modules_without_qt_do_not_load_it():
    code = ("import sys\n"
            "import setupEM, setupEM.join_pieces, setupEM.thermal_results, setupEM.palace_results\n"
            "import setupEM.script_model, setupEM.script_probe, setupEM.config_update, setupEM.piece_set\n"
            "import setupEM.cli_common, setupEM.run_with_overrides\n"
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6'))\n"
            "print(loaded)\n"
            "sys.exit(1 if loaded else 0)\n")
    env = dict(os.environ, PYTHONPATH=os.path.abspath(SRC))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_package_main_starts_the_gui_module(monkeypatch):
    from setupEM import setupEM as module
    monkeypatch.setattr(module, "main", lambda: "started")
    assert setupEM.main() == "started"


# ---------------------------------------------------------------- join_pieces command line

def test_join_pieces_command_line(capsys, tmp_path):
    for arguments, code in ((["--help"], 0), (["--version"], 0), ([], 2)):
        with pytest.raises(SystemExit) as exit_info:
            join_pieces.main(arguments)
        assert exit_info.value.code == code
    capsys.readouterr()

    assert join_pieces.main([str(tmp_path / "missing.simcfg")]) == 1
    captured = capsys.readouterr()
    assert "missing.simcfg cannot be read" in captured.err and captured.out == ""

    assert join_pieces.main([write_config(tmp_path / "single.simcfg")]) == 1
    captured = capsys.readouterr()
    assert "not a split region" in captured.err and captured.out == ""
