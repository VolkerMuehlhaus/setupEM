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

Tools > Simplify GDS... : removes floating (unconnected) metal fill and/or
fills in small cutouts on the currently loaded GDS file, writing the result
to a new GDS file. Both operations come from gds_prepare_for_EM (a sibling
package, already an editable install in this venv) and are called in-process
- no subprocess/CLI involved.

No technology-specific layer numbers are hardcoded here: the list of metal
layers to process is derived entirely from the XML stackup already parsed
into MainWindow.metals_list (gds2palace's stackup_reader), same as every
other setupEM feature that needs to know "which GDS layers are metal".
"""

import os, io, contextlib, traceback, tempfile
import gdspy
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


def _flatten_cell(lib, cellname):
    """Return (lib, top_cell) with top_cell flattened in place. Round-trips
    through a temporary GDS file first: gdspy's flatten()/get_polygonsets()
    hits an internal bug on some in-memory-only reference structures ('tuple'
    object does not support item assignment) that only a GDS write+read
    cycle avoids - same workaround gds_prepare_for_EM.py's own CLI pipeline
    uses before every flatten() call in its main()."""
    with tempfile.TemporaryDirectory(prefix="setupEM_simplify_") as tmp_dir:
        tmp_path = os.path.join(tmp_dir, "pre_flatten.gds")
        lib.write_gds(tmp_path)
        lib = gdspy.GdsLibrary(infile=tmp_path)
    top_cell = lib.cells.get(cellname, lib.top_level()[0])
    top_cell.flatten()
    return lib, top_cell


def run_simplify(gds_path, metal_layers, output_path, cellname="",
                  do_cutouts=False, max_hole_area=None,
                  do_floating=False, fill_minsize=1.0, fill_maxsize=None, fill_mincount=20,
                  do_merge=False):
    """Load gds_path, optionally fill small cutouts, remove floating metal
    fill, and/or merge (boolean OR) polygons per layer as a final pass, on
    the given metal_layers, write the result to output_path. Mirrors the
    step order gds_prepare_for_EM.py's own main() uses: cutout removal first
    (works on the hierarchical design), floating-fill removal after that
    (needs a flattened, per-layer-merged cell first so that touching fill
    tiles aren't mistaken for isolated ones - see
    find_isolated_same_size_polygons_by_layer()'s own docstring), per-layer
    merge last.
    """
    from gds_prepare_for_EM import (
        remove_cutout_keep_hierarchy,
        merge_polygons_by_layer,
        find_isolated_same_size_polygons_by_layer,
    )
    from gds_geometry_utils import validate_and_repair_polygons

    # merge_polygons_by_layer()/find_isolated_same_size_polygons_by_layer()
    # create cells via the bare gdspy.Cell(...) constructor, which
    # auto-registers each one into gdspy's persistent global
    # gdspy.current_library - harmless for the CLI (a fresh process every
    # run) but in this long-running GUI, a second Run on the same file
    # would reuse the same cell names and gdspy raises "already present in
    # library". Starting from a fresh current_library each call clears that
    # accumulated state without touching gds_prepare_for_EM itself.
    gdspy.current_library = gdspy.GdsLibrary()

    lib = gdspy.GdsLibrary()
    lib.read_gds(gds_path)
    top_cell = lib.cells.get(cellname, lib.top_level()[0])

    if do_cutouts:
        design_bbox = top_cell.get_bounding_box()
        lib = remove_cutout_keep_hierarchy(lib, metal_layers, design_bbox=design_bbox,
                                            max_hole_area=max_hole_area)
        top_cell = lib.cells.get(cellname, lib.top_level()[0])

    # floating-fill removal already ends with a flattened, per-layer-merged
    # cell (it needs that itself, to tell a floating fill cluster apart from
    # touching real metal) - so a separate final merge pass is only extra
    # work when floating-fill removal did NOT already run
    already_merged_flat = False

    if do_floating:
        lib, top_cell = _flatten_cell(lib, cellname)
        merged_lib = merge_polygons_by_layer(top_cell, layers_list=metal_layers)
        merged_top = merged_lib.top_level()[0]
        lib = find_isolated_same_size_polygons_by_layer(
            merged_top, metal_layers,
            minsize=fill_minsize, maxsize=fill_maxsize, mincount=fill_mincount)
        already_merged_flat = True

    if do_merge and not already_merged_flat:
        lib, top_cell = _flatten_cell(lib, cellname)
        lib = merge_polygons_by_layer(top_cell, layers_list=metal_layers)

    # Final polygon validity check/repair - always runs, regardless of which
    # operations above were selected. Catches any polygon still geometrically
    # invalid (self-intersecting) no matter the reason - e.g. a cutout
    # deliberately left alone by remove_cutout_keep_hierarchy() for being
    # larger than max_hole_area, or a merge step re-introducing an invalid
    # "bridge" hole encoding while fusing polygons back together. A
    # solid-body mesher like gds2palace cannot handle this even though
    # gdspy/GDSII themselves tolerate it.
    repaired_count, unresolved = validate_and_repair_polygons(lib, metal_layers)
    if repaired_count:
        print(f'Final validity check: repaired {repaired_count} invalid polygon(s)')
    if unresolved:
        print(f'Final validity check: WARNING - {len(unresolved)} polygon(s) could not be '
              f'auto-repaired, still geometrically invalid in the output file:')
        for cell_name, n, layer, reason in unresolved:
            print(f'  {cell_name} polygon #{n} layer {layer}: {reason}')
    if not repaired_count and not unresolved:
        print('Final validity check: no invalid polygons found')

    lib.write_gds(output_path)


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
        self._metal_layers = sorted(
            int(m.layernum) for m in metals_list.getallplanarmetals()
            if not (port_layer_min <= int(m.layernum) <= port_layer_max)
        ) if metals_list is not None else []

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

        # ---- Merge per layer ----
        # remembers the user's own choice while the checkbox is forced/greyed
        # out by "Remove floating (unconnected) metal" (see
        # _on_floating_group_toggled() below), so it's restored rather than
        # lost once that group is unchecked again
        self._merge_user_choice = get_preference_bool(app_name, "simplify_merge_per_layer", True)
        self.merge_per_layer_checkbox = QCheckBox("Merge polygons per layer (final step)")
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
        if not do_floating and not do_cutouts and not do_merge:
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
                    do_merge=do_merge)
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
