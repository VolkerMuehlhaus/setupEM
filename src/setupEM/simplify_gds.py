########################################################################
#
# Copyright 2025-2026 Volker Muehlhaus and IHP PDK Authors
#
# Licensed under the GNU General Public License, Version 3.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.gnu.org/licenses/gpl-3.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
########################################################################

"""
simplify_gds.py

Tools > Simplify GDS... : removes floating (unconnected) metal fill, fills
in small cutouts and/or merges via arrays on the currently loaded GDS file,
writing the result to a new GDS file. The pipeline itself is
gds_prepare_for_EM's gds_simplify_for_em.run_simplify() (shared with the
gds2palace MCP server), called in-process - no subprocess/CLI involved.

No technology-specific layer numbers are hardcoded here: the metal layers to
process, the via layers and the metal layers above/below each via are derived
entirely from the XML stackup already parsed into MainWindow.metals_list
(gds2palace's stackup_reader), with gds_simplify_for_em.layers_from_stackup().
"""

import os, io, contextlib, traceback
from gds_simplify_for_em import run_simplify, layers_from_stackup
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QPlainTextEdit, QFileDialog, QMessageBox, QCheckBox,
    QApplication,
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

# get_preference() itself has no dependency on this module, so importing it
# here is not circular - setup_common.py only ever imports this module
# lazily, at runtime (see MainWindowBase.open_simplify_gds())
if __package__ in (None, ""):
    from setup_common import get_preference, get_preference_bool, shorten_path_for_display
else:
    from .setup_common import get_preference, get_preference_bool, shorten_path_for_display

# fixed width for the first label in every single-label row, so their edit
# boxes all start in the same column - matches the label_width convention
# used in PreferencesDialog
_LABEL_WIDTH = 130

# same look as setup_common.py's EDIT_STYLE_OPTIONAL - kept as a local copy
# rather than importing setup_common here, since setup_common.py is the one
# that lazily imports this module (to open the dialog), not the other way
# around, and this avoids the __package__ relative-vs-absolute import
# juggling that a two-way import would need
_EDIT_STYLE = """
            QLineEdit {
                background-color: white;
                color: black;
                border: 1px solid gray;
                border-radius: 4px;
                padding: 4px;
            }
        """


def _parse_optional_float(text, field_label):
    """Blank -> None (meaning "no limit"/"disabled"). Otherwise a float, or
    raises ValueError with a field-specific message for the caller to show."""
    text = text.strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"'{field_label}' is not a valid number: {text!r}")


def _port_layer_range(app_name):
    """Same auto-assign source layer range (Preferences > Ports), and same
    201-299 fallback, as setupEM's/setupThermal's own _port_layer_range() -
    duplicated here (rather than reached through a tab object) since it's
    just two preference reads, and the two are otherwise unrelated widgets."""
    try:
        layer_min = int(get_preference(app_name, "port_layer_min", "201"))
    except (TypeError, ValueError):
        layer_min = 201
    try:
        layer_max = int(get_preference(app_name, "port_layer_max", "299"))
    except (TypeError, ValueError):
        layer_max = 299
    return layer_min, layer_max


def _parse_layer_list(text):
    """Blank -> empty set. Otherwise a set of ints from a comma-separated
    list of GDS layer numbers, or raises ValueError."""
    text = text.strip()
    if text == "":
        return set()
    try:
        return {int(part.strip()) for part in text.split(",") if part.strip() != ""}
    except ValueError:
        raise ValueError(f"'Layers excluded from simplification' is not a valid "
                          f"comma-separated list of layer numbers: {text!r}")


class SimplifyGdsDialog(QDialog):
    """Own top-level window (WA_DeleteOnClose, non-modal), same lifecycle as
    LayoutPreviewWindow/StackupEditorWindow."""

    def __init__(self, MainWindow):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Simplify GDS")
        self.resize(700, 600)
        self.MainWindow = MainWindow
        self._simplified_gds_path = None
        self._compare_windows = []

        gds_path = self.MainWindow.saved_values.get("GdsFile", "")
        default_output = ""
        if gds_path:
            base, ext = os.path.splitext(gds_path)
            default_output = f"{base}_simplified{ext or '.gds'}"

        app_name = self.MainWindow.APP_NAME
        port_layer_min, port_layer_max = _port_layer_range(app_name)
        metals_list = self.MainWindow.metals_list
        # metal layers to process, and via layers with the metal layers above/below them
        # for via array merging
        self._metal_layers, self._via_layers = layers_from_stackup(
            metals_list, (port_layer_min, port_layer_max)) if metals_list is not None else ([], [])

        layout = QVBoxLayout(self)

        def row_label(text, tooltip=None):
            label = QLabel(text)
            label.setFixedWidth(_LABEL_WIDTH)
            if tooltip:
                label.setToolTip(tooltip)
            return label

        metal_layers_tip = (
            f"Metal layers considered: {', '.join(str(n) for n in self._metal_layers) or '(none)'}\n"
            f"Port/marker layers {port_layer_min}-{port_layer_max} are always passed through untouched."
        )

        info_label = QLabel(
            f"Source: {shorten_path_for_display(gds_path) if gds_path else '(none loaded)'}")
        info_label.setToolTip(metal_layers_tip)
        layout.addWidget(info_label)

        excluded_row = QHBoxLayout()
        excluded_row.addWidget(row_label("Exclude layers", metal_layers_tip))
        self.excluded_layers_edit = QLineEdit(
            str(get_preference(app_name, "simplify_excluded_layers", "")))
        self.excluded_layers_edit.setStyleSheet(_EDIT_STYLE)
        self.excluded_layers_edit.setPlaceholderText("e.g. 10,11")
        self.excluded_layers_edit.setToolTip(metal_layers_tip)
        excluded_row.addWidget(self.excluded_layers_edit, 1)
        layout.addLayout(excluded_row)

        output_row = QHBoxLayout()
        output_row.addWidget(row_label("Output file"))
        self.output_edit = QLineEdit(default_output)
        self.output_edit.setStyleSheet(_EDIT_STYLE)
        output_row.addWidget(self.output_edit, 1)
        output_browse_btn = QPushButton("Browse ...")
        output_browse_btn.clicked.connect(self._browse_output_file)
        output_row.addWidget(output_browse_btn)
        layout.addLayout(output_row)

        # ---- Remove floating (unconnected) metal ----
        floating_group = QGroupBox("Remove floating (unconnected) metal")
        floating_group.setCheckable(True)
        floating_group.setChecked(True)
        floating_group.setToolTip(
            "A same-size group of isolated (unconnected) shapes on a metal layer is "
            "treated as removable fill once it repeats at least this many times."
        )
        floating_layout = QVBoxLayout(floating_group)
        self.floating_group = floating_group

        minsize_row = QHBoxLayout()
        minsize_row.addWidget(row_label("Min size (µm)"))
        self.fill_minsize_edit = QLineEdit("1")
        self.fill_minsize_edit.setStyleSheet(_EDIT_STYLE)
        minsize_row.addWidget(self.fill_minsize_edit)
        minsize_row.addWidget(QLabel("Max size (µm)"))
        self.fill_maxsize_edit = QLineEdit(str(get_preference(app_name, "simplify_fill_maxsize", "20")))
        self.fill_maxsize_edit.setStyleSheet(_EDIT_STYLE)
        self.fill_maxsize_edit.setToolTip("Leave blank for no upper size limit")
        minsize_row.addWidget(self.fill_maxsize_edit)
        floating_layout.addLayout(minsize_row)

        mincount_row = QHBoxLayout()
        mincount_row.addWidget(row_label("Min repeat count"))
        self.fill_mincount_edit = QLineEdit("20")
        self.fill_mincount_edit.setStyleSheet(_EDIT_STYLE)
        mincount_row.addWidget(self.fill_mincount_edit)
        mincount_row.addStretch(1)
        floating_layout.addLayout(mincount_row)

        layout.addWidget(floating_group)

        # ---- Remove small cutouts ----
        cutout_group = QGroupBox("Remove small cutouts")
        cutout_group.setCheckable(True)
        cutout_group.setChecked(True)
        cutout_layout = QVBoxLayout(cutout_group)
        self.cutout_group = cutout_group

        maxarea_row = QHBoxLayout()
        maxarea_row.addWidget(row_label("Max area (µm²)"))
        self.max_hole_area_edit = QLineEdit(str(get_preference(app_name, "simplify_max_hole_area", "1")))
        self.max_hole_area_edit.setStyleSheet(_EDIT_STYLE)
        self.max_hole_area_edit.setToolTip("Leave blank to remove every cutout found, regardless of size")
        maxarea_row.addWidget(self.max_hole_area_edit)
        maxarea_row.addStretch(1)
        cutout_layout.addLayout(maxarea_row)

        layout.addWidget(cutout_group)

        # ---- Merge via arrays ----
        via_group = QGroupBox("Merge via arrays")
        via_group.setCheckable(True)
        via_group.setChecked(False)
        via_group.setToolTip(
            "Vias closer than this distance are merged into one shape, but only if they connect "
            "the same metal shapes above and below, so merging never shorts different metal "
            "shapes. Via layers and the metal layers above/below them come from the XML stackup."
        )
        via_layout = QVBoxLayout(via_group)
        self.via_group = via_group

        via_size_row = QHBoxLayout()
        via_size_row.addWidget(row_label("Distance (µm)"))
        self.via_merge_size_edit = QLineEdit(str(get_preference(app_name, "merge_polygon_size", "0.5")))
        self.via_merge_size_edit.setStyleSheet(_EDIT_STYLE)
        via_size_row.addWidget(self.via_merge_size_edit)
        via_size_row.addStretch(1)
        via_layout.addLayout(via_size_row)

        via_note = QLabel("Merged vias in the output file are treated as solid metal: fill factor "
                          "correction needs the original vias, which are then no longer in the file. "
                          "For accurate via resistance, merge vias in the model instead (via array "
                          "merge distance > 0 with fill factor correction).")
        via_note.setWordWrap(True)
        via_layout.addWidget(via_note)

        # ---- Merge per layer ----
        # remembers the user's own choice while the checkbox is forced/greyed
        # out by "Remove floating (unconnected) metal" (see
        # _on_floating_group_toggled() below), so it's restored rather than
        # lost once that group is unchecked again
        self._merge_user_choice = get_preference_bool(app_name, "simplify_merge_per_layer", True)
        self.merge_per_layer_checkbox = QCheckBox("Merge polygons per layer")
        self.merge_per_layer_checkbox.setChecked(self._merge_user_choice)
        self.merge_per_layer_checkbox.setToolTip(
            "Boolean-OR touching/overlapping polygons on the same layer into the minimal "
            "set of shapes. Runs automatically as part of 'Remove floating (unconnected) "
            "metal' already (forced on, greyed out, while that's enabled) - this only does "
            "extra work when that option is off, and it flattens the design hierarchy to do so."
        )
        self.merge_per_layer_checkbox.toggled.connect(
            lambda checked: setattr(self, "_merge_user_choice", checked)
            if self.merge_per_layer_checkbox.isEnabled() else None)
        layout.addWidget(self.merge_per_layer_checkbox)
        floating_group.toggled.connect(self._on_floating_group_toggled)
        self._on_floating_group_toggled(floating_group.isChecked())

        # after "Merge polygons per layer": via merging runs last, see run_simplify()
        layout.addWidget(via_group)

        # ---- Log area ----
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        log_font = QFont()
        log_font.setFamilies(["Consolas", "Cascadia Mono", "Ubuntu Mono", "Liberation Mono", "DejaVu Sans Mono", "monospace"])
        log_font.setStyleHint(QFont.Monospace)
        log_font.setFixedPitch(True)
        log_font.setPointSize(9)
        self.log_area.setFont(log_font)
        layout.addWidget(self.log_area, 1)

        # ---- Buttons ----
        button_layout = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._run)
        button_layout.addWidget(self.run_btn)
        self.compare_btn = QPushButton("Compare in Layout Preview")
        self.compare_btn.setEnabled(False)
        self.compare_btn.clicked.connect(self._open_comparison)
        button_layout.addWidget(self.compare_btn)
        button_layout.addStretch(1)
        self.delete_close_btn = QPushButton("Delete && Close")
        self.delete_close_btn.clicked.connect(self._delete_and_close)
        # hidden until a run actually produces something to delete - shown/
        # hidden alongside accept_close_btn's relabeling in _run()
        self.delete_close_btn.setVisible(False)
        button_layout.addWidget(self.delete_close_btn)
        # starts as a plain Close (nothing to accept yet) - _run() relabels it
        # to "Accept & Close" once a run actually produces an output file,
        # and back to "Close" at the start of the next run until it succeeds
        self.accept_close_btn = QPushButton("Close")
        self.accept_close_btn.setDefault(True)
        self.accept_close_btn.clicked.connect(self._accept_and_close)
        button_layout.addWidget(self.accept_close_btn)
        layout.addLayout(button_layout)

    def _delete_and_close(self):
        if self._simplified_gds_path and os.path.isfile(self._simplified_gds_path):
            confirm = QMessageBox.question(
                self, "Delete Output File",
                f"Delete '{os.path.basename(self._simplified_gds_path)}'?\n\n"
                "This cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if confirm != QMessageBox.Yes:
                return
            try:
                os.remove(self._simplified_gds_path)
            except OSError as e:
                QMessageBox.warning(self, "Error", f"Could not delete file:\n\n{e}")
                return
        self.close()

    def _accept_and_close(self):
        if self._simplified_gds_path and os.path.isfile(self._simplified_gds_path):
            self.MainWindow.file_tab.set_gds_file(self._simplified_gds_path)
        self.close()

    def _on_floating_group_toggled(self, checked):
        """Floating-fill removal already ends with a flattened, per-layer-merged
        cell (see run_simplify()) - force the merge checkbox to reflect that
        instead of offering a choice that has no effect, and restore the
        user's own choice once floating-fill removal is off again."""
        if checked:
            # disable first: setChecked() below would otherwise fire the
            # toggled() guard while the checkbox still reads as enabled,
            # overwriting the saved user choice with this forced value
            self.merge_per_layer_checkbox.setEnabled(False)
            self.merge_per_layer_checkbox.setChecked(True)
        else:
            self.merge_per_layer_checkbox.setEnabled(True)
            self.merge_per_layer_checkbox.setChecked(self._merge_user_choice)

    def _browse_output_file(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Select Output GDS File", self.output_edit.text(), "*.gds;;*.*")
        if filename:
            self.output_edit.setText(filename)

    def _run(self):
        gds_path = self.MainWindow.saved_values.get("GdsFile", "")
        if not gds_path or not os.path.isfile(gds_path):
            QMessageBox.warning(self, "Error", "Load a GDSII file on the Input Files tab first")
            return
        if not self._metal_layers:
            QMessageBox.warning(self, "Error", "No metal layers found - load an XML stackup file first")
            return

        do_floating = self.floating_group.isChecked()
        do_cutouts = self.cutout_group.isChecked()
        do_merge = self.merge_per_layer_checkbox.isChecked()
        do_via_merge = self.via_group.isChecked()
        if not do_floating and not do_cutouts and not do_merge and not do_via_merge:
            QMessageBox.warning(self, "Error", "Enable at least one operation")
            return

        output_path = self.output_edit.text().strip()
        if not output_path:
            QMessageBox.warning(self, "Error", "Enter an output GDS file path")
            return

        try:
            fill_minsize = _parse_optional_float(self.fill_minsize_edit.text(), "Min size")
            if fill_minsize is None:
                fill_minsize = 1.0
            fill_maxsize = _parse_optional_float(self.fill_maxsize_edit.text(), "Max size")
            mincount_text = self.fill_mincount_edit.text().strip()
            fill_mincount = int(mincount_text) if mincount_text else 20
            max_hole_area = _parse_optional_float(self.max_hole_area_edit.text(), "Max cutout area")
            excluded_layers = _parse_layer_list(self.excluded_layers_edit.text())
            via_merge_size = 0
            if do_via_merge:
                via_merge_size = _parse_optional_float(self.via_merge_size_edit.text(), "Via merge distance")
                if via_merge_size is None or via_merge_size <= 0:
                    raise ValueError("'Via merge distance' must be a number > 0")
        except ValueError as e:
            QMessageBox.warning(self, "Error", str(e))
            return

        layers_to_process = [l for l in self._metal_layers if l not in excluded_layers]
        if not layers_to_process:
            QMessageBox.warning(self, "Error", "No metal layers left to process - "
                                                "check 'Layers excluded from simplification'")
            return

        cellname = self.MainWindow.saved_values.get("cellname", "")

        self.log_area.setPlainText("Running ...")
        self.compare_btn.setEnabled(False)
        self.accept_close_btn.setText("Close")
        self.delete_close_btn.setVisible(False)
        self._simplified_gds_path = None

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Running ...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        # force a repaint now - run_simplify() below blocks the GUI thread,
        # so without this the button/cursor changes above wouldn't actually
        # show up on screen until after it's already done
        QApplication.processEvents()

        captured_stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured_stdout):
                run_simplify(
                    gds_path, layers_to_process, output_path, cellname=cellname,
                    do_cutouts=do_cutouts, max_hole_area=max_hole_area,
                    do_floating=do_floating, fill_minsize=fill_minsize,
                    fill_maxsize=fill_maxsize, fill_mincount=fill_mincount,
                    do_merge=do_merge,
                    via_layers=[v for v in self._via_layers if v[0] not in excluded_layers],
                    via_merge_size=via_merge_size)
        except (Exception, SystemExit):
            printed = captured_stdout.getvalue().strip()
            details = (printed + "\n\n" + traceback.format_exc()) if printed else traceback.format_exc()
            self.log_area.setPlainText(details)
            QMessageBox.critical(self, "Error", f"Could not simplify GDSII layout:\n\n{details}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.run_btn.setEnabled(True)
            self.run_btn.setText("Run")

        self.log_area.setPlainText(captured_stdout.getvalue().strip() or "Done - no changes were needed.")
        self._simplified_gds_path = output_path
        self.compare_btn.setEnabled(True)
        self.accept_close_btn.setText("Accept && Close")
        self.delete_close_btn.setVisible(True)

    def _open_comparison(self):
        if not self._simplified_gds_path or not os.path.isfile(self._simplified_gds_path):
            return

        # local import: layout_preview.py imports from setup_common.py
        # (MainWindowBase), so importing it at module load time here would
        # be circular, same reasoning as MainWindowBase.open_layout_preview()
        if __package__ in (None, ""):
            from layout_preview import LayoutPreviewWindow
        else:
            from .layout_preview import LayoutPreviewWindow

        original_window = LayoutPreviewWindow(self.MainWindow, title_suffix="Original")
        simplified_window = LayoutPreviewWindow(
            self.MainWindow, gds_override_path=self._simplified_gds_path, title_suffix="Simplified")
        # keep references so the windows aren't garbage-collected out from
        # under Qt while this dialog stays open
        self._compare_windows = [original_window, simplified_window]
        original_window.show()
        # cascade the second window diagonally off the first - two identically
        # sized "Layout Preview" windows opening at the same default position
        # would otherwise land exactly on top of each other, making it look
        # like only one window opened
        CASCADE_OFFSET = 60
        simplified_window.move(original_window.pos().x() + CASCADE_OFFSET,
                                original_window.pos().y() + CASCADE_OFFSET)
        simplified_window.show()
