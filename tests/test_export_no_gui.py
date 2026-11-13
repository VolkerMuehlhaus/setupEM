# File > Export writes the no_gui switch as a comment (issue #52): visible in the exported script, but not a
# setting, so Create Model's own script and an import of the export are unchanged.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from setupEM.setup_common import NO_GUI_EXPORT_LINE, parse_assignments  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("module_name", ["setupEM", "setupThermal"])
def test_export_has_no_gui_comment(monkeypatch, app, tmp_path, module_name):
    module = __import__(f"setupEM.{module_name}", fromlist=["MainWindow"])
    monkeypatch.setattr(module, "get_preference", lambda app_name, key, default=None: default)
    window = module.MainWindow()
    try:
        editor = window.modeleditor_tab
        editor.create_model_text(forExport=True)
        exported = editor.model_edit.toPlainText()
        assert NO_GUI_EXPORT_LINE in exported
        script = tmp_path / "model.py"
        script.write_text(exported, encoding="utf-8")
        assert "no_gui" not in parse_assignments(str(script))

        editor.create_model_text()   # Create Model: setupEM runs the script with its own window settings
        assert "no_gui" not in editor.model_edit.toPlainText()
    finally:
        window._script_dirty = False
        window.close()
