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
stackup_editor.py

GUI editor for stackup XML files (see gds2palace/XML_stackup_format.md):
Materials, the Dielectric stack, drawn Layers, and DerivedLayers (boolean
layer operations), with a live cross-section preview reusing
setup_common.VectorWidget. Opened from Tools > Edit Stackup XML... in
setupEM / setupThermal (wired up in setup_common.MainWindowBase.create_menu_bar(),
so it is available in both apps for free).

Tables (thermal conductivity lookups) is not editable here. Loading goes
through gds2palace.stackup_writer.load_stackup_tree(), which preserves XML
comments, and only Material/Dielectric/Layer/Substrate/DerivedLayer elements
are ever touched, so Tables - and any comments in it - round-trips untouched
on save.
"""

import copy
import os
import xml.etree.ElementTree as ET
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QLabel, QFileDialog, QMessageBox,
    QScrollArea, QColorDialog, QMenuBar,
)
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QAction
from PySide6.QtCore import Qt, QTimer, Signal

from gds2palace import stackup_reader, stackup_writer

# __package__ is None/"" when this file is run directly rather than imported
# as part of the setupEM package, so relative import fails.
if __package__ in (None, ""):
    from setup_common import VectorWidget
else:
    from .setup_common import VectorWidget


# ------------------------------------------------------------------
# Column specs: (attribute, header label, kind)
# kind in {"text", "computed", "materialtype", "layertype", "materialref",
#          "operationtype", "operands", "color"}
# ------------------------------------------------------------------

MATERIAL_COLUMNS = [
    ("Name", "Name", "text"),
    ("Type", "Type", "materialtype"),
    ("Permittivity", "Permittivity", "text"),
    ("DielectricLossTangent", "Loss Tangent", "text"),
    ("Conductivity", "Conductivity (S/m)", "text"),
    ("Rs", "Rs (Ohm/sq)", "text"),
    ("Density", "Density", "text"),
    ("ThermalConductivity", "Thermal Cond.", "text"),
    ("ThermalConductivityTable", "Thermal Table", "text"),
    ("Color", "Color", "color"),
]

DIELECTRIC_COLUMNS = [
    ("Name", "Name", "text"),
    ("Material", "Material", "materialref"),
    ("Reference", "Reference (optional)", "dielectricref"),
    ("ReferenceEdge", "Ref. Edge", "referenceedge"),
    ("Thickness", "Thickness", "text"),
    ("Zmin", "Zmin", "text"),
    ("Zmax", "Zmax", "text"),
    ("ResultZmin", "Zmin (resulting)", "computed"),
    ("ResultZmax", "Zmax (resulting)", "computed"),
    ("Boundary", "Optional Boundary (GDS layer)", "text"),
]

LAYER_COLUMNS = [
    ("Name", "Name", "text"),
    ("Layer", "GDSII Layer #", "text"),
    ("Type", "Type", "layertype"),
    ("Material", "Material", "materialref"),
    ("Reference", "Reference (optional)", "layerref_or_dielectricref"),
    ("ReferenceEdge", "Ref. Edge", "referenceedge"),
    ("Zmin", "Zmin", "text"),
    ("Zmax", "Zmax", "text"),
    ("Thickness", "Thickness (resulting)", "computed"),
    ("ResultZmin", "Zmin (resulting)", "computed"),
    ("ResultZmax", "Zmax (resulting)", "computed"),
]

REFERENCE_EDGE_CHOICES = ["Top", "Bottom"]

# sentinel shown in the Reference combo for "no Reference set" (absolute positioning);
# mapped back to "" (removes the attribute) when selected - never written to the XML
REFERENCE_NONE_LABEL = "(none)"


def _compute_layer_thickness(elements):
    """Read-only Thickness column for the Layers tab: Zmax - Zmin, recomputed
       whenever either changes. Blank while Zmin/Zmax aren't both valid numbers yet
       (e.g. mid-edit), rather than raising.
    """
    computed = {}
    for element in elements:
        try:
            thickness = float(element.get("Zmax")) - float(element.get("Zmin"))
            text = f"{thickness:.4f}"
        except (TypeError, ValueError):
            text = ""
        computed[id(element)] = {"Thickness": text}
    return computed


def _compute_layer_zpositions(elements, dielectrics_elements, offset=0.0):
    """Read-only ResultZmin/ResultZmax columns for the Layers tab: the absolute
       resolved z-position for every row, whether it's plain absolute Zmin/Zmax or
       Reference-based (offset from a Dielectric/Layer edge) - this is the main
       point of Reference-based positioning, so it stays visible regardless of which
       kind a given row uses. Runs the same resolution the reader uses
       (dielectric_layers_list.calculate_zpositions() +
       metal_layers_list.resolve_references() + metal_layers_list.add_offset())
       over the current (possibly mid-edit) Dielectrics/Layers tab state. Returns {}
       - blanking those columns - if the current data isn't complete/valid enough to
       resolve yet (including a dangling/ambiguous/circular Reference, which the
       reader reports via exit(1) rather than a normal exception - see
       _refresh_preview() for the same pattern).
    Args:
        offset (float): the file's <Substrate Offset>, if any. Only applied when no
            Layer uses Reference, exactly like parse_substrate() does - Reference and
            a nonzero Offset are mutually exclusive (see validate_stackup()), so a
            file with any Reference-based Layer never reaches add_offset() there either.
    """
    try:
        dielectrics_list = stackup_reader.dielectric_layers_list()
        for element in dielectrics_elements:
            dielectrics_list.append(stackup_reader.dielectric_layer(element), None)
        dielectrics_list.calculate_zpositions()

        metals_list = stackup_reader.metal_layers_list()
        for element in elements:
            metals_list.append(stackup_reader.metal_layer(element))
        metals_list.resolve_references(dielectrics_list)
        if offset and not metals_list.has_references():
            metals_list.add_offset(offset)
    except (Exception, SystemExit):
        return {}

    computed = {}
    for element, metal in zip(elements, metals_list.metals):
        computed[id(element)] = {
            "ResultZmin": f"{metal.zmin:.4f}" if metal.zmin is not None else "",
            "ResultZmax": f"{metal.zmax:.4f}" if metal.zmax is not None else "",
        }
    return computed


def _layer_gray_fn(_element, attr):
    # Thickness/ResultZmin/ResultZmax are always derived/read-only here (unlike the
    # Dielectric stack's resulting Zmin/Zmax, there's no mode where they're the
    # "source of truth")
    return attr in ("Thickness", "ResultZmin", "ResultZmax")


# ------------------------------------------------------------------
# Derived Layers tab: boolean/resize operations on other layers (see
# derived_layers.md). Operands are edited as one comma-separated column rather
# than a variable number of sub-columns - AND/OR/XOR/NOT take 2+ operands
# (unbounded), SIZE takes exactly 1, so a fixed set of "Operand1/2/3..."
# columns would either not scale or waste space depending on the row. Order
# is preserved left-to-right as typed, which matters for NOT (first operand
# minus all the following ones).
# ------------------------------------------------------------------

DERIVED_LAYER_COLUMNS = [
    ("Name", "Name", "text"),
    ("Layer", "Target Layer #", "text"),
    ("Operation", "Operation", "operationtype"),
    ("Oversize", "Oversize", "text"),
    ("Operands", "Operands (GDSII layer numbers)", "operands"),
]


# ------------------------------------------------------------------
# Materials tab: some columns don't apply to every Material Type, and
# showing their (meaningless) default value is just noise. These attributes
# are silently omitted from the XML for the types they don't apply to - the
# reader already falls back to the same default (Permittivity=1,
# DielectricLossTangent=0, Conductivity=0, Density=1, ThermalConductivity=0)
# when the attribute is absent, so omitting it changes nothing functionally.
# ------------------------------------------------------------------

_MATERIAL_NOT_APPLICABLE_ATTRS = {
    "CONDUCTOR": {"Permittivity", "DielectricLossTangent", "Rs"},
    "DIELECTRIC": {"Rs","Conductivity"},
    "SEMICONDUCTOR": {"Rs"},
    "RESISTOR": {"Permittivity", "DielectricLossTangent", "Conductivity",
                 "Density", "ThermalConductivity", "ThermalConductivityTable"},
}


def _material_not_applicable(element, attr):
    mtype = (element.get("Type") or "").upper()
    return attr in _MATERIAL_NOT_APPLICABLE_ATTRS.get(mtype, ())


def _strip_not_applicable_material_attrs(element):
    mtype = (element.get("Type") or "").upper()
    for attr in _MATERIAL_NOT_APPLICABLE_ATTRS.get(mtype, ()):
        if attr in element.attrib:
            del element.attrib[attr]


def _material_blank_if_default(element, attr, value):
    # Dielectric materials always carry a Conductivity attribute in practice
    # (usually "0", the default) - not wrong, just clutter; still editable in
    # case an unusual lossy dielectric needs a nonzero value.
    mtype = (element.get("Type") or "").upper()
    if mtype == "DIELECTRIC" and attr == "Conductivity":
        return value in ("", "0", "0.0")
    return False


# ------------------------------------------------------------------
# Dielectric Stack tab: a dielectric is positioned one of three ways - absolute
# (explicit Zmin/Zmax), Reference-relative (offset from another Dielectric's edge,
# Thickness sizing it by default), or implicit (Thickness-stacked on its neighbors,
# top-to-bottom, by dielectric_layers_list.calculate_zpositions()). The "resulting"
# columns show the computed position in all three cases, so the effective z-range is
# always visible; Zmin/Zmax/Thickness are grayed out for whichever mode isn't that
# row's actual source of truth.
# ------------------------------------------------------------------

def _dielectric_position_mode(element):
    if element.get("Reference"):
        return "reference"
    if element.get("Zmin") and element.get("Zmax"):
        return "absolute"
    return "thickness"


def _dielectric_gray_fn(element, attr):
    mode = _dielectric_position_mode(element)
    if attr == "Thickness":
        return mode == "absolute"  # unused/ignored once Zmin+Zmax fix the position
    if attr in ("Zmin", "Zmax"):
        return mode == "thickness"  # unused in implicit mode; still meaningful (optional) overrides in reference mode
    if attr in ("ResultZmin", "ResultZmax"):
        return mode == "absolute"  # redundant with the already-editable Zmin/Zmax there
    return False


def _compute_dielectric_zpositions(elements):
    """Runs the same resolution the reader uses (dielectric_layers_list.calculate_zpositions(),
       covering absolute/Reference/implicit-stacked dielectrics alike) over the current (possibly
       mid-edit) Dielectric elements, so the "resulting" columns show real effective z-positions.
       Returns {} - blanking those columns - if the current data isn't complete/valid enough to
       compute yet, including a dangling/ambiguous/circular Reference, which the reader reports
       via exit(1) rather than a normal exception - see _refresh_preview() for the same pattern.
    """
    try:
        dielectrics_list = stackup_reader.dielectric_layers_list()
        for element in elements:
            dielectrics_list.append(stackup_reader.dielectric_layer(element), None)
        dielectrics_list.calculate_zpositions()
    except (Exception, SystemExit):
        return {}

    computed = {}
    for element, dielectric in zip(elements, dielectrics_list.dielectrics):
        computed[id(element)] = {
            "ResultZmin": f"{dielectric.zmin:.4f}" if dielectric.zmin is not None else "",
            "ResultZmax": f"{dielectric.zmax:.4f}" if dielectric.zmax is not None else "",
        }
    return computed


class NoScrollComboBox(QComboBox):
    """QComboBox that ignores the mouse wheel, so scrolling the table this combo
       sits in doesn't silently change the combo's value when the cursor happens
       to pass over it - only an actual dropdown click can change the selection.
    """

    def wheelEvent(self, event):
        event.ignore()


class _CommitOnFocusOutTextEdit(QPlainTextEdit):
    """QPlainTextEdit that emits editingFinished (like QLineEdit) when it loses
       focus, so a free-text field commits as one edit on focus-out rather than
       one undo-snapshot per keystroke.
    """

    editingFinished = Signal()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.editingFinished.emit()


def _unique_name(existing_names, base):
    if base not in existing_names:
        return base
    n = 2
    while f"{base}{n}" in existing_names:
        n += 1
    return f"{base}{n}"


# ------------------------------------------------------------------
# Generic table editor for one XML element type (Material / Dielectric / Layer)
# ------------------------------------------------------------------

class ElementTableEditor(QWidget):
    """Editable QTableWidget bound to a list of same-tag XML elements. Each row is
       kept in lockstep with its Element via self.row_elements (parallel list, not
       stored in the table itself, since some columns use cell widgets).
    """

    _GRAY = QColor(235, 235, 235)

    def __init__(self, columns, container_fn, add_fn, remove_fn, default_attrs_fn, on_changed,
                 move_fn=None, material_choices_fn=None, type_choices=None,
                 strip_fn=None, not_applicable_fn=None, blank_if_default_fn=None,
                 reload_on_attr_change=frozenset(), compute_fn=None, gray_fn=None,
                 pre_set_attr_fn=None, reference_choices_fn=None, header_tooltips=None,
                 operand_lookup_fn=None):
        """container_fn(root) -> list[Element]: fetches the current rows to display,
           re-called by reload() so the editor can refresh itself after any structural
           change (add/remove/move) without the caller having to re-fetch and hand
           back the list every time.

           strip_fn(element): called once per row on every reload, to silently drop
             attributes that don't apply given the row's current values (e.g. a
             Conductor material's Permittivity) so the XML doesn't accumulate
             meaningless defaults - the reader already defaults them on read.
           not_applicable_fn(element, attr) -> bool: for "text" columns, forces a
             blank, read-only display (the value is fixed at its default and not
             user-editable in this row's context).
           blank_if_default_fn(element, attr, value) -> bool: for "text" columns,
             shows blank instead of a value that's just the (uninteresting) default,
             while leaving the cell normally editable.
           reload_on_attr_change: attribute names whose change should trigger a full
             self.reload() (e.g. "Type", since it changes which columns apply).
           compute_fn(elements) -> {id(element): {attr: display_str}}: called once per
             reload() (not per row, since e.g. dielectric z-stacking needs the whole
             ordered list at once), feeding "computed" kind columns - values that are
             derived, not stored as an XML attribute, and therefore always read-only.
           gray_fn(element, attr) -> bool: tints a "text" or "computed" cell's
             background gray without affecting whether it's editable - use this
             (instead of not_applicable_fn) when a column is still meaningful/editable
             but just isn't the row's current source of truth (e.g. absolute Zmin/Zmax
             on a row that's actually using Thickness-based auto-stacking).
           pre_set_attr_fn(element, attr, new_value) -> bool: called before a "text"
             column's value is applied, with the element still unmodified (so the
             hook can read the old value itself); if it returns True it has fully
             handled applying the change (and any side effects, e.g. a confirmation
             dialog with cross-tab consequences) and the normal element.set()/attrib
             deletion is skipped; False means apply the value normally.
           reference_choices_fn() -> list[str]: choices for the "layerref_or_dielectricref"/
             "dielectricref" kinds. For Layers this combines two different element containers
             (Dielectrics + Layers) - unlike material_choices_fn, which only ever spans one; for
             Dielectrics it's Dielectric names only (a Dielectric's Reference can't target a
             Layer - Layers are resolved after Dielectrics).
           header_tooltips: {attr: tooltip text} for columns whose header label alone
             could be misread (e.g. Zmin/Zmax meaning different things depending on
             whether the row uses Reference).
           operand_lookup_fn() -> {layer number string: layer name}: for the "operands"
             kind, used to build a per-cell tooltip resolving each GDSII layer number to
             its Layers-tab Name where possible, falling back to the bare number for
             whichever operands don't resolve (e.g. a pure intermediate derived layer
             with no <Layer> entry of its own).
        """
        super().__init__()
        self.columns = columns
        self.container_fn = container_fn
        self.add_fn = add_fn
        self.remove_fn = remove_fn
        self.move_fn = move_fn
        self.default_attrs_fn = default_attrs_fn
        self.material_choices_fn = material_choices_fn
        self.operand_lookup_fn = operand_lookup_fn
        self.reference_choices_fn = reference_choices_fn
        self.type_choices = type_choices or []
        self.on_changed = on_changed
        self.strip_fn = strip_fn
        self.not_applicable_fn = not_applicable_fn
        self.blank_if_default_fn = blank_if_default_fn
        self.reload_on_attr_change = reload_on_attr_change
        self.compute_fn = compute_fn
        self.gray_fn = gray_fn
        self.pre_set_attr_fn = pre_set_attr_fn

        self.root = None
        self.row_elements = []
        self._computed = {}
        self._loading = False

        layout = QVBoxLayout()

        self.table = QTableWidget()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([c[1] for c in columns])
        if header_tooltips:
            for col, (attr, _header, _kind) in enumerate(columns):
                tooltip = header_tooltips.get(attr)
                if tooltip:
                    self.table.horizontalHeaderItem(col).setToolTip(tooltip)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        for col in range(len(columns)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.add_button.setAutoDefault(False)
        self.add_button.clicked.connect(self._on_add_clicked)
        button_row.addWidget(self.add_button)

        self.remove_button = QPushButton("Remove")
        self.remove_button.setAutoDefault(False)
        self.remove_button.clicked.connect(self._on_remove_clicked)
        button_row.addWidget(self.remove_button)

        if self.move_fn is not None:
            self.up_button = QPushButton("Move Up")
            self.up_button.setAutoDefault(False)
            self.up_button.clicked.connect(lambda: self._on_move_clicked(-1))
            button_row.addWidget(self.up_button)

            self.down_button = QPushButton("Move Down")
            self.down_button.setAutoDefault(False)
            self.down_button.clicked.connect(lambda: self._on_move_clicked(+1))
            button_row.addWidget(self.down_button)

        button_row.addStretch()
        layout.addLayout(button_row)

        self.setLayout(layout)

    # ---------- data binding ----------

    def set_root(self, root):
        self.root = root
        self.reload()

    def reload(self):
        self._loading = True
        self.table.setRowCount(0)
        self.row_elements = list(self.container_fn(self.root)) if self.root is not None else []
        self._computed = self.compute_fn(self.row_elements) if self.compute_fn else {}
        self.table.setRowCount(len(self.row_elements))
        for row, element in enumerate(self.row_elements):
            self._populate_row(row, element)
        self._loading = False

    def _populate_row(self, row, element):
        if self.strip_fn:
            self.strip_fn(element)
        for col, (attr, _header, kind) in enumerate(self.columns):
            value = element.get(attr, "")
            if kind == "text":
                not_applicable = bool(self.not_applicable_fn and self.not_applicable_fn(element, attr))
                blank = not_applicable or bool(self.blank_if_default_fn and self.blank_if_default_fn(element, attr, value))
                item = QTableWidgetItem("" if blank else value)
                if not_applicable:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setBackground(self._GRAY)
                elif self.gray_fn and self.gray_fn(element, attr):
                    item.setBackground(self._GRAY)
                self.table.setItem(row, col, item)
            elif kind == "computed":
                computed_value = self._computed.get(id(element), {}).get(attr, "")
                item = QTableWidgetItem(computed_value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if self.gray_fn and self.gray_fn(element, attr):
                    item.setBackground(self._GRAY)
                self.table.setItem(row, col, item)
            elif kind == "color":
                self._make_color_cell(row, col, element, attr, value)
            elif kind in ("materialtype", "layertype", "operationtype", "referenceedge"):
                choices = REFERENCE_EDGE_CHOICES if kind == "referenceedge" else self.type_choices
                self._make_combo_cell(row, col, element, attr, value, choices, editable=False)
            elif kind == "materialref":
                choices = self.material_choices_fn() if self.material_choices_fn else []
                self._make_combo_cell(row, col, element, attr, value, choices, editable=True)
            elif kind in ("layerref_or_dielectricref", "dielectricref"):
                choices = [REFERENCE_NONE_LABEL] + (self.reference_choices_fn() if self.reference_choices_fn else [])
                self._make_combo_cell(row, col, element, attr, value or REFERENCE_NONE_LABEL, choices, editable=True,
                                       value_out_fn=lambda text: "" if text == REFERENCE_NONE_LABEL else text)
            elif kind == "operands":
                operand_numbers = stackup_writer.get_operand_layers(element)
                item = QTableWidgetItem(", ".join(operand_numbers))
                if self.operand_lookup_fn and operand_numbers:
                    lookup = self.operand_lookup_fn()
                    item.setToolTip(", ".join(lookup.get(num, num) for num in operand_numbers))
                self.table.setItem(row, col, item)

    def _make_combo_cell(self, row, col, element, attr, value, choices, editable, value_out_fn=None):
        combo = NoScrollComboBox()
        combo.setEditable(editable)
        items = list(choices)
        if value and value not in items:
            items = [value] + items
        combo.addItems(items)
        if value:
            combo.setCurrentText(value)

        def _emit(text, el=element, a=attr):
            self._set_attr(el, a, value_out_fn(text) if value_out_fn else text)

        combo.currentTextChanged.connect(_emit)
        self.table.setCellWidget(row, col, combo)

    def _make_color_cell(self, row, col, element, attr, value):
        button = QPushButton(("#" + value) if value else "(choose)")
        button.setAutoDefault(False)
        self._style_color_button(button, value)

        def pick_color(_checked=False, el=element, a=attr, btn=button):
            initial = QColor("#" + value) if value else QColor("white")
            color = QColorDialog.getColor(initial, self)
            if color.isValid():
                hexcode = color.name().lstrip("#")
                btn.setText("#" + hexcode)
                self._style_color_button(btn, hexcode)
                self._set_attr(el, a, hexcode)

        button.clicked.connect(pick_color)
        self.table.setCellWidget(row, col, button)

    def _style_color_button(self, button, hex_value):
        if hex_value:
            button.setStyleSheet(f"background-color: #{hex_value};")
        else:
            button.setStyleSheet("")

    # ---------- edit handlers ----------

    def _set_attr(self, element, attr, value):
        if self._loading:
            return
        if self.pre_set_attr_fn and self.pre_set_attr_fn(element, attr, value):
            pass  # hook fully handled applying the value (and any side effects)
        elif value == "":
            if attr in element.attrib:
                del element.attrib[attr]
        else:
            element.set(attr, value)
        if attr in self.reload_on_attr_change:
            # deferred: this may be firing from inside the very cell widget (e.g. a
            # combo box) that reload() is about to tear down and recreate, so let the
            # current signal/event finish unwinding first
            QTimer.singleShot(0, self._reload_and_notify)
        else:
            self.on_changed()

    def _reload_and_notify(self):
        self.reload()
        self.on_changed()

    def _set_operands(self, element, text):
        if self._loading:
            return
        layer_numbers = [part.strip() for part in text.split(",") if part.strip() != ""]
        stackup_writer.set_operands(element, layer_numbers)
        self.on_changed()

    def _on_item_changed(self, item):
        if self._loading:
            return
        row = item.row()
        col = item.column()
        if row >= len(self.row_elements):
            return
        attr, _header, kind = self.columns[col]
        element = self.row_elements[row]
        if kind == "operands":
            self._set_operands(element, item.text())
        else:
            self._set_attr(element, attr, item.text())

    def _on_add_clicked(self):
        if self.root is None:
            return
        attrs = self.default_attrs_fn()
        self.add_fn(self.root, **attrs)
        self.reload()
        self.on_changed(structural=True)

    def _on_remove_clicked(self):
        selected = self.table.selectedIndexes()
        if not selected:
            return
        row = selected[0].row()
        if row >= len(self.row_elements):
            return
        element = self.row_elements[row]
        self.remove_fn(self.root, element)
        self.reload()
        self.on_changed(structural=True)

    def _on_move_clicked(self, direction):
        selected = self.table.selectedIndexes()
        if not selected or self.move_fn is None:
            return
        row = selected[0].row()
        if row >= len(self.row_elements):
            return
        element = self.row_elements[row]
        self.move_fn(self.root, element, direction)
        self.reload()
        new_row = row + direction
        if 0 <= new_row < self.table.rowCount():
            self.table.selectRow(new_row)
        self.on_changed(structural=True)


# ------------------------------------------------------------------
# Separate, resizable preview window (the editing tables need the space more
# than a squeezed-in preview pane does)
# ------------------------------------------------------------------

class StackupPreviewWindow(QWidget):
    """Own top-level window for the live stackup cross-section preview, so it
       gets real screen space instead of sharing the editor window with the
       editing tables. Reuses the same VectorWidget instance as the editor -
       updates happen by mutating that widget's data and calling .update(),
       so this window doesn't need any refresh logic of its own.

    Deliberately NOT WA_DeleteOnClose: the QScrollArea here owns the shared
    VectorWidget (setWidget() reparents it), so destroying this window on
    close would destroy that widget too, breaking the editor that still
    references it. Closing (the X button) just hides the window instead.

    Deliberately constructed with NO Qt parent (see StackupEditorWindow,
    which passes none): on Windows, any widget with a Qt parent gets a native
    "owner" window set, and an owned window always stays above its owner
    regardless of focus - the preview would permanently float over the
    editor. Neither QDialog(parent, Qt.Window) nor QWidget(parent, Qt.Window)
    stopped this in testing; only removing the parent relationship entirely
    did. Without a Qt parent there is no automatic cascade-delete when the
    editor closes, so StackupEditorWindow.closeEvent() explicitly calls
    deleteLater() on this window instead of relying on Qt object-tree cleanup.
    """

    def __init__(self, vector_widget, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Stackup Preview")
        self.resize(700, 900)

        layout = QVBoxLayout()
        scroll = QScrollArea()
        scroll.setWidget(vector_widget)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        self.setLayout(layout)

    def closeEvent(self, event):
        event.ignore()
        self.hide()


# ------------------------------------------------------------------
# Main editor window
# ------------------------------------------------------------------

class StackupEditorWindow(QDialog):
    """Non-modal editor window for stackup XML files. The live cross-section
       preview lives in its own StackupPreviewWindow (see above) rather than
       being embedded here, so both windows can be sized/moved independently.
    """

    def __init__(self, MainWindow, initial_filename=None):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Stackup Editor")
        # wide enough that the toolbar (with the filename label at its max width)
        # never needs to grow the window to fit - Qt otherwise auto-grows a
        # top-level widget up to its layout's minimum size on relayout
        self.resize(1150, 700)
        self.MainWindow = MainWindow

        self.tree = None
        self.current_filename = None

        # single-level undo: _last_snapshot is always an independent deep copy of
        # the tree as it was right before the most recent change; _undo_snapshot
        # is what Undo restores to (the snapshot before THAT change). See
        # _record_undo_point()/undo() - deliberately just one step, not a stack,
        # per explicit request to keep this simple.
        self._last_snapshot = None
        self._undo_snapshot = None

        # asked once per loaded/new file (reset in new_file()/_load_file()): whether to
        # write auto-assigned implicit-Dielectric-stacking References into the XML at
        # Save time (see _maybe_offer_explicit_dielectric_references())
        self._asked_about_implicit_dielectric_references = False

        # schemaVersion as of the last successful load/save (reset in new_file()/
        # _load_file(), refreshed in _save_to() on success) - lets save() notice when a
        # plain Save would silently upgrade an old-format ("2.0") file on disk to the
        # newer format (e.g. after Convert to Reference position format bumps it to
        # "3.0"), so it can offer Save As instead of overwriting quietly
        self._loaded_schema_version = None

        outer_layout = QVBoxLayout()

        # ---------- menu bar ----------
        # QMenuBar works as a normal widget here even though this is a QDialog, not a
        # QMainWindow - QLayout.setMenuBar() below reserves it a dedicated full-width row
        # at the very top, same visual result as QMainWindow's built-in menu bar.
        self.menu_bar = QMenuBar()

        # kept as self.*_menu (not just locals) so the Python-side wrapper stays alive
        # for the lifetime of the window, matching the QMenuBar's C++ ownership of them
        self.file_menu = self.menu_bar.addMenu("&File")

        self.new_action = QAction("&New", self)
        self.new_action.triggered.connect(self.new_file)
        self.file_menu.addAction(self.new_action)

        self.open_action = QAction("&Open...", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self.open_file_dialog)
        self.file_menu.addAction(self.open_action)

        self.file_menu.addSeparator()

        self.save_action = QAction("&Save", self)
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(lambda: self.save())
        self.file_menu.addAction(self.save_action)

        self.saveas_action = QAction("Save &As...", self)
        self.saveas_action.setShortcut(QKeySequence.SaveAs)
        self.saveas_action.triggered.connect(self.save_as_dialog)
        self.file_menu.addAction(self.saveas_action)

        self.edit_menu = self.menu_bar.addMenu("&Edit")

        self.undo_action = QAction("&Undo", self)
        self.undo_action.setShortcut(QKeySequence.Undo)
        self.undo_action.setEnabled(False)
        self.undo_action.triggered.connect(self.undo)
        self.edit_menu.addAction(self.undo_action)

        self.tools_menu = self.menu_bar.addMenu("&Tools")

        self.convert_to_reference_action = QAction("Convert to Reference position format", self)
        self.convert_to_reference_action.triggered.connect(self._on_convert_to_reference_clicked)
        self.tools_menu.addAction(self.convert_to_reference_action)

        self.view_menu = self.menu_bar.addMenu("&View")

        self.preview_action = QAction("Show &Preview", self)
        self.preview_action.triggered.connect(self._open_preview_window)
        self.view_menu.addAction(self.preview_action)

        outer_layout.setMenuBar(self.menu_bar)

        # ---------- filename row ----------
        file_row = QHBoxLayout()
        self.filename_label = QLabel("(no file loaded)")
        # cap the width so a long path can't force the window to grow; the full
        # path is still available as a tooltip and via _set_filename_label()'s eliding
        self.filename_label.setMaximumWidth(400)
        file_row.addWidget(self.filename_label)
        file_row.addStretch()
        outer_layout.addLayout(file_row)

        # ---------- materials / dielectrics / layers tabs ----------
        self.materials_editor = ElementTableEditor(
            MATERIAL_COLUMNS,
            container_fn=self._materials_container,
            add_fn=lambda root, **attrs: stackup_writer.add_material(root, **attrs),
            remove_fn=stackup_writer.remove_material,
            default_attrs_fn=self._default_material_attrs,
            on_changed=self._on_materials_changed,
            type_choices=list(stackup_writer.VALID_MATERIAL_TYPES),
            strip_fn=_strip_not_applicable_material_attrs,
            not_applicable_fn=_material_not_applicable,
            blank_if_default_fn=_material_blank_if_default,
            reload_on_attr_change={"Type"},
        )

        self.dielectrics_editor = ElementTableEditor(
            DIELECTRIC_COLUMNS,
            container_fn=self._dielectrics_container,
            add_fn=lambda root, **attrs: stackup_writer.add_dielectric(root, **attrs),
            remove_fn=stackup_writer.remove_dielectric,
            move_fn=stackup_writer.move_dielectric,
            default_attrs_fn=self._default_dielectric_attrs,
            on_changed=self._on_dielectrics_changed,
            material_choices_fn=self._material_names,
            reference_choices_fn=self._dielectric_names,
            gray_fn=_dielectric_gray_fn,
            compute_fn=_compute_dielectric_zpositions,
            # these four drive the resulting-Zmin/Zmax computation and the
            # position-mode gray-out state, so they must live-refresh
            reload_on_attr_change={"Thickness", "Zmin", "Zmax", "Reference", "ReferenceEdge"},
            pre_set_attr_fn=self._handle_dielectric_thickness_change,
            header_tooltips={
                "Zmin": "Absolute position, or offset from Reference if set",
                "Zmax": "Absolute position, or offset from Reference if set",
            },
        )

        self.layers_editor = ElementTableEditor(
            LAYER_COLUMNS,
            container_fn=self._layers_container,
            add_fn=lambda root, **attrs: stackup_writer.add_layer(root, **attrs),
            remove_fn=stackup_writer.remove_layer,
            default_attrs_fn=self._default_layer_attrs,
            on_changed=self._on_changed,
            material_choices_fn=self._material_names,
            reference_choices_fn=self._reference_target_names,
            type_choices=list(stackup_writer.VALID_LAYER_TYPES),
            compute_fn=self._compute_layer_zpositions_and_thickness,
            gray_fn=_layer_gray_fn,
            # Thickness/ResultZmin/ResultZmax are derived from these - must live-refresh
            reload_on_attr_change={"Zmin", "Zmax", "Reference", "ReferenceEdge"},
            header_tooltips={
                "Zmin": "Absolute position, or offset from Reference if set",
                "Zmax": "Absolute position, or offset from Reference if set",
            },
        )

        layers_tab = QWidget()
        layers_layout = QVBoxLayout()
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("Substrate Z-offset applied to all layers (0 = none):"))
        self.offset_edit = QLineEdit()
        self.offset_edit.setFixedWidth(100)
        self.offset_edit.editingFinished.connect(self._on_offset_edited)
        offset_row.addWidget(self.offset_edit)
        offset_row.addStretch()
        layers_layout.addLayout(offset_row)
        layers_layout.addWidget(self.layers_editor)
        layers_tab.setLayout(layers_layout)

        self.derived_layers_editor = ElementTableEditor(
            DERIVED_LAYER_COLUMNS,
            container_fn=self._derived_layers_container,
            add_fn=lambda root, **attrs: stackup_writer.add_derived_layer(root, **attrs),
            remove_fn=stackup_writer.remove_derived_layer,
            default_attrs_fn=self._default_derived_layer_attrs,
            on_changed=self._on_changed,
            type_choices=list(stackup_writer.VALID_DERIVED_OPERATIONS),
            operand_lookup_fn=self._layer_name_by_number,
        )

        derived_layers_tab = QWidget()
        derived_layers_layout = QVBoxLayout()
        operands_hint = QLabel(
            "Operands: comma-separated layer numbers (native GDSII or another "
            "Derived Layer's target). Order matters for NOT (first minus the rest).")
        operands_hint.setWordWrap(True)
        derived_layers_layout.addWidget(operands_hint)
        derived_layers_layout.addWidget(self.derived_layers_editor)
        derived_layers_tab.setLayout(derived_layers_layout)

        description_tab = QWidget()
        description_layout = QVBoxLayout()
        description_hint = QLabel(
            "Free-text description of this file. Saved as a comment at the top of "
            "the XML output, below the editor's own stamp.")
        description_hint.setWordWrap(True)
        description_layout.addWidget(description_hint)
        self.description_edit = _CommitOnFocusOutTextEdit()
        self.description_edit.editingFinished.connect(self._on_description_edited)
        description_layout.addWidget(self.description_edit)
        description_tab.setLayout(description_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.materials_editor, "Materials")
        self.tabs.addTab(self.dielectrics_editor, "Dielectric Stack")
        self.tabs.addTab(layers_tab, "Layers")
        self.tabs.addTab(derived_layers_tab, "Derived Layers")
        self.tabs.addTab(description_tab, "File Description")

        outer_layout.addWidget(self.tabs)

        # ---------- status ----------
        self.status_label = QLabel("")
        outer_layout.addWidget(self.status_label)

        self.setLayout(outer_layout)

        # the VectorWidget itself lives in the (separate, hide-on-close) preview
        # window; keep the instance here since it's what _refresh_preview() mutates
        self.vector_widget = VectorWidget(
            stackup_reader.stackup_materials_list(),
            stackup_reader.dielectric_layers_list(),
            stackup_reader.metal_layers_list(),
            dielectric_color_fn=self.MainWindow.stackup_dielectric_color,
            dielectric_label_fn=self.MainWindow.stackup_dielectric_label,
            metal_label_fn=self.MainWindow.stackup_metal_label,
            via_label_suffix_fn=self.MainWindow.stackup_via_label_suffix,
        )
        self.vector_widget.setMinimumSize(600, 800)
        # created once and kept for the editor's lifetime, but deliberately with
        # no Qt parent (see StackupPreviewWindow docstring) - cleaned up explicitly
        # in closeEvent() below rather than via Qt's parent-child auto-delete
        self.preview_window = StackupPreviewWindow(self.vector_widget)
        self.preview_window.move(self.x() + self.width() + 20, self.y())

        if initial_filename and os.path.isfile(initial_filename):
            self._load_file(initial_filename)
        else:
            self.new_file()

        self._open_preview_window()

    def _open_preview_window(self):
        self.preview_window.show()
        self.preview_window.raise_()
        self.preview_window.activateWindow()

    def closeEvent(self, event):
        # preview_window has no Qt parent (see its docstring), so it won't be
        # cascade-deleted when this dialog is destroyed - clean it up explicitly.
        # deleteLater() bypasses StackupPreviewWindow's own closeEvent override
        # (which just hides it), since that override only intercepts close(), not
        # deleteLater()
        self.preview_window.hide()
        self.preview_window.deleteLater()
        super().closeEvent(event)

    # ---------- default attrs for new rows ----------

    def _default_material_attrs(self):
        names = self._material_names()
        return {
            "Name": _unique_name(names, "NewMaterial"),
            "Type": "Conductor",
            # Permittivity/DielectricLossTangent deliberately omitted: not
            # applicable to Conductor, stripped right back out on reload anyway
            "Conductivity": "0",
            "Color": "808080",
        }

    def _default_dielectric_attrs(self):
        names = [d.get("Name") for d in self._dielectrics_container(self.tree.getroot())]
        materials = self._material_names()
        return {
            "Name": _unique_name(names, "NewDielectric"),
            "Material": materials[0] if materials else "",
            "Thickness": "1.0",
        }

    def _handle_dielectric_thickness_change(self, element, attr, new_value):
        """pre_set_attr_fn for the Dielectrics editor: when a non-absolute (implicit-stacked
           or Reference-based) dielectric's Thickness actually changes, its resulting
           Zmax moves by the same delta - offer to apply that same delta to
           every drawn Layer whose z-position is affected: layers entirely
           above the (old) Zmax are shifted (Zmin and Zmax both += delta);
           layers that straddle it (Zmin at/below, Zmax above - e.g. a via, or
           a backside connection like "LBE") are stretched instead, so their
           anchored bottom end stays put while their top end tracks the shift.
           Layers entirely below or inside this dielectric are left alone.
           Returns True once it has applied the Thickness value itself
           (handled unconditionally, whether or not there were layers to
           adjust / the user accepted).
        """
        if attr != "Thickness" or self.tree is None or _dielectric_position_mode(element) == "absolute":
            return False

        old_text = element.get("Thickness")
        try:
            old_thickness = float(old_text) if old_text else None
            new_thickness = float(new_value) if new_value else None
        except ValueError:
            return False  # not valid numbers (yet) - let normal handling/validation deal with it
        if old_thickness is None or new_thickness is None or old_thickness == new_thickness:
            # nothing to shift for (cleared, unparseable, or a no-op edit) - still
            # need to apply the raw value ourselves, since we're telling the caller
            # this hook fully handled it
            if new_value:
                element.set("Thickness", new_value)
            elif "Thickness" in element.attrib:
                del element.attrib["Thickness"]
            return True

        root = self.tree.getroot()
        old_positions = _compute_dielectric_zpositions(self._dielectrics_container(root))
        old_zmax_text = old_positions.get(id(element), {}).get("ResultZmax")

        element.set("Thickness", new_value)  # apply the actual edit either way

        if not old_zmax_text:
            return True  # couldn't determine the old boundary (invalid data elsewhere) - skip the offer
        old_zmax = float(old_zmax_text)
        delta = new_thickness - old_thickness

        # <Layer> Zmin/Zmax are in a local frame and only land in the same
        # coordinate space as the dielectric stack once the optional <Substrate
        # Offset> is applied (see util_stackup_reader.metal_layers_list.add_offset)
        # - comparing raw, un-offset values against a dielectric's Zmax would
        # misjudge which layers are actually above it whenever Offset != 0, which
        # is the common case (the whole metal stack floating above Substrate).
        offset_el = stackup_writer.get_substrate_offset_element(root)
        try:
            offset = float(offset_el.get("Offset")) if offset_el is not None else 0.0
        except (TypeError, ValueError):
            offset = 0.0

        # Three cases, classified purely by z-geometry (not Layer Type - e.g. "LBE"
        # is Type="dielectric" in some stackup files and Type="via" in others, but
        # has the same straddling geometry either way and needs the same handling):
        #   - entirely at/above the old top boundary -> shift (Zmin and Zmax both += delta)
        #   - straddles it (Zmin below/inside, Zmax above, e.g. a via or LBE-like
        #     backside connection reaching up through this dielectric into the
        #     next one) -> stretch: its far (Zmax) end tracks the shift, its near
        #     (Zmin) end stays anchored to what's below, which hasn't moved
        #   - entirely below/inside -> untouched
        epsilon = 1e-5
        layers_above = []
        layers_straddling = []
        for layer_el in self._layers_container(root):
            if layer_el.get("Reference"):
                # Reference-based layers' Zmin/Zmax are offsets from their reference
                # edge, not positions - adding Offset to them is meaningless, and they
                # already auto-track this dielectric's new Thickness via
                # resolve_references() once it's re-run, so this shift/stretch
                # heuristic must not touch them (that would double-count the movement)
                continue
            try:
                layer_zmin = float(layer_el.get("Zmin")) + offset
                layer_zmax = float(layer_el.get("Zmax")) + offset
            except (TypeError, ValueError):
                continue
            if layer_zmin >= old_zmax - epsilon:
                layers_above.append(layer_el)
            elif layer_zmax > old_zmax + epsilon:
                layers_straddling.append(layer_el)

        if not layers_above and not layers_straddling:
            return True

        length_unit = (root.find("ELayers").get("LengthUnit") or "").strip()
        message_parts = [f"This dielectric's Thickness changed by {delta:+.4f}{length_unit}.\n"]
        if layers_above:
            names = ", ".join(layer_el.get("Name") or "<unnamed>" for layer_el in layers_above)
            message_parts.append(
                f"\n{len(layers_above)} layer(s) positioned above it will be SHIFTED "
                f"by the same amount:\n{names}\n")
        if layers_straddling:
            names = ", ".join(layer_el.get("Name") or "<unnamed>" for layer_el in layers_straddling)
            message_parts.append(
                f"\n{len(layers_straddling)} layer(s) reach from below/inside it to "
                f"above it (e.g. a via) and will be STRETCHED by the same amount, "
                f"keeping their bottom (Zmin) anchored:\n{names}\n")
        message_parts.append("\nApply these changes, so they stay in the same position "
                              "relative to this dielectric?")

        reply = QMessageBox.question(
            self, "Shift/stretch layers above?", "".join(message_parts),
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            for layer_el in layers_above:
                layer_el.set("Zmin", f"{float(layer_el.get('Zmin')) + delta:.4f}")
                layer_el.set("Zmax", f"{float(layer_el.get('Zmax')) + delta:.4f}")
            for layer_el in layers_straddling:
                layer_el.set("Zmax", f"{float(layer_el.get('Zmax')) + delta:.4f}")
            self.layers_editor.reload()

        return True

    def _default_layer_attrs(self):
        names = [l.get("Name") for l in self._layers_container(self.tree.getroot())]
        materials = self._material_names()
        return {
            "Name": _unique_name(names, "NewLayer"),
            "Type": "conductor",
            "Material": materials[0] if materials else "",
            "Zmin": "0",
            "Zmax": "1",
            "Layer": "900",
        }

    def _default_derived_layer_attrs(self):
        names = [dl.get("Name") for dl in self._derived_layers_container(self.tree.getroot())]
        return {
            "Name": _unique_name(names, "NewDerivedLayer"),
            "Layer": self._next_free_target_layer(),
            "Operation": "AND",
            # Operands deliberately left for the user to fill in - there's no
            # sensible default set of layers to combine, unlike e.g. a new
            # Material where "just pick the first material" is a harmless stand-in
        }

    def _next_free_target_layer(self):
        # simple collision-avoidance so a freshly added row already has a usable
        # target number: highest layer/derived-layer number in use, plus one
        root = self.tree.getroot()
        used = set()
        for el in self._layers_container(root):
            try:
                used.add(int(el.get("Layer")))
            except (TypeError, ValueError):
                pass
        for el in self._derived_layers_container(root):
            try:
                used.add(int(el.get("Layer")))
            except (TypeError, ValueError):
                pass
        candidate = 900
        while candidate in used:
            candidate += 1
        return str(candidate)

    def _material_names(self):
        if self.tree is None:
            return []
        return [m.get("Name") for m in self._materials_container(self.tree.getroot()) if m.get("Name")]

    def _layer_names(self):
        if self.tree is None:
            return []
        return [l.get("Name") for l in self._layers_container(self.tree.getroot()) if l.get("Name")]

    def _dielectric_names(self):
        if self.tree is None:
            return []
        return [d.get("Name") for d in self._dielectrics_container(self.tree.getroot()) if d.get("Name")]

    def _layer_name_by_number(self):
        # for the Derived Layers tab's Operands tooltip: a GDSII layer number used as an
        # operand doesn't always have a Layers-tab entry (a pure intermediate derived
        # layer legitimately doesn't need one - see XML_stackup_format.md), so this is a
        # best-effort lookup, not a complete mapping
        if self.tree is None:
            return {}
        return {l.get("Layer"): l.get("Name") for l in self._layers_container(self.tree.getroot())
                if l.get("Layer") and l.get("Name")}

    def _reference_target_names(self):
        # a Layer's Reference choices span two different element containers (unlike
        # material_choices_fn, which only ever spans Materials); a Dielectric's Reference
        # is Dielectric-only, so it just uses _dielectric_names() directly (see its wiring
        # in dielectrics_editor's construction)
        return self._layer_names() + self._dielectric_names()

    def _compute_layer_zpositions_and_thickness(self, elements):
        root = self.tree.getroot() if self.tree is not None else None
        dielectrics_elements = self._dielectrics_container(root) if root is not None else []
        offset_el = stackup_writer.get_substrate_offset_element(root) if root is not None else None
        try:
            offset = float(offset_el.get("Offset")) if offset_el is not None else 0.0
        except (TypeError, ValueError):
            offset = 0.0
        computed = _compute_layer_thickness(elements)
        for key, values in _compute_layer_zpositions(elements, dielectrics_elements, offset).items():
            computed.setdefault(key, {}).update(values)
        return computed

    # ---------- container accessors (Element -> list[Element]) ----------

    @staticmethod
    def _materials_container(root):
        materials_el = stackup_writer.get_materials_element(root)
        return materials_el.findall("Material") if materials_el is not None else []

    @staticmethod
    def _dielectrics_container(root):
        dielectrics_el = stackup_writer.get_dielectrics_element(root)
        return dielectrics_el.findall("Dielectric") if dielectrics_el is not None else []

    @staticmethod
    def _layers_container(root):
        layers_el = stackup_writer.get_layers_element(root)
        return layers_el.findall("Layer") if layers_el is not None else []

    @staticmethod
    def _derived_layers_container(root):
        derived_el = stackup_writer.get_derived_layers_element(root)
        return derived_el.findall("DerivedLayer") if derived_el is not None else []

    # ---------- file actions ----------

    def _set_filename_label(self, text):
        # full text always available on hover; visible label is elided (never
        # wrapped) so a long path can't force the window/toolbar to grow
        self.filename_label.setToolTip(text)
        metrics = QFontMetrics(self.filename_label.font())
        elided = metrics.elidedText(text, Qt.ElideMiddle, self.filename_label.maximumWidth())
        self.filename_label.setText(elided)

    def new_file(self):
        self.tree = stackup_writer.new_stackup_tree()
        self.current_filename = None
        self._set_filename_label("(new, unsaved)")
        self._asked_about_implicit_dielectric_references = False
        self._loaded_schema_version = self.tree.getroot().get("schemaVersion")
        self._reload_all_editors()
        self._reset_undo_baseline()
        self._revalidate_and_refresh()

    def open_file_dialog(self):
        previous_dir = os.path.dirname(self.current_filename) if self.current_filename else ""
        filename, _ = QFileDialog.getOpenFileName(self, "Open Stackup XML File", previous_dir, "*.xml;;*.*")
        if filename:
            self._load_file(filename)

    def _load_file(self, filename):
        try:
            self.tree = stackup_writer.load_stackup_tree(filename)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load {filename}:\n{e}")
            return
        self.current_filename = filename
        self._set_filename_label(filename)
        self._asked_about_implicit_dielectric_references = False
        self._loaded_schema_version = self.tree.getroot().get("schemaVersion")
        self._reload_all_editors()
        self._reset_undo_baseline()
        self._revalidate_and_refresh()

    def _reload_all_editors(self):
        root = self.tree.getroot()
        self.materials_editor.set_root(root)
        self.dielectrics_editor.set_root(root)
        self.layers_editor.set_root(root)
        self.derived_layers_editor.set_root(root)

        offset_el = stackup_writer.get_substrate_offset_element(root)
        self.offset_edit.setText(offset_el.get("Offset") if offset_el is not None else "0")

        # setPlainText() does not touch focus, so this does not trigger
        # _on_description_edited() (which only fires on focus-out)
        self.description_edit.setPlainText(stackup_writer.get_file_description(root))

    def save(self):
        if self.tree is None:
            return False
        if not self.current_filename:
            return self.save_as_dialog()

        current_schema_version = self.tree.getroot().get("schemaVersion")
        if self._loaded_schema_version == "2.0" and current_schema_version != "2.0":
            # a plain Save would silently overwrite the original 2.0-format file on disk
            # with the newer format (typically right after Convert to Reference position
            # format bumped it) - ask once, rather than surprise the user
            choice = self._ask_overwrite_or_save_as(current_schema_version)
            if choice == "cancel":
                return False
            if choice == "save_as":
                return self.save_as_dialog()
            # choice == "overwrite": fall through to the normal save below

        return self._save_to(self.current_filename)

    def _ask_overwrite_or_save_as(self, current_schema_version):
        """This file's on-disk schemaVersion is "2.0" but the in-memory content is now a
           newer format (current_schema_version) - ask whether to overwrite the original
           file or save the upgraded content to a new file instead.
        Returns:
            string: "overwrite", "save_as", or "cancel"
        """
        box = QMessageBox(self)
        box.setWindowTitle("File format changed")
        box.setText(
            f'This file was loaded as schemaVersion="2.0". Saving now would overwrite it '
            f'with the newer schemaVersion="{current_schema_version}" format.\n\n'
            "Overwrite the original file, or save the upgraded version as a new file?")
        overwrite_btn = box.addButton("Overwrite", QMessageBox.AcceptRole)
        saveas_btn = box.addButton("Save As...", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(saveas_btn)  # non-destructive default
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite_btn:
            return "overwrite"
        if clicked is saveas_btn:
            return "save_as"
        return "cancel"

    def save_as_dialog(self):
        if self.tree is None:
            return False
        previous_dir = os.path.dirname(self.current_filename) if self.current_filename else ""
        filename, _ = QFileDialog.getSaveFileName(self, "Save Stackup XML File", previous_dir, "*.xml;;*.*")
        if not filename:
            return False
        return self._save_to(filename)

    def _compute_auto_dielectric_references(self, dielectrics_elements):
        """Returns {dielectric name: (reference, reference_edge)} for dielectrics that would
           get a Reference auto-assigned from implicit (Thickness-only) stacking - see
           util_stackup_reader.dielectric_layers_list._assign_implicit_references(). Empty
           (not an error) if the current data can't be resolved yet.
        """
        try:
            dielectrics_list = stackup_reader.dielectric_layers_list()
            for element in dielectrics_elements:
                dielectrics_list.append(stackup_reader.dielectric_layer(element), None)
            dielectrics_list.calculate_zpositions()
        except (Exception, SystemExit):
            return {}
        return {
            dielectric.name: (dielectric.reference, dielectric.reference_edge)
            for dielectric in dielectrics_list.dielectrics
            if dielectric.reference_is_auto
        }

    def _maybe_offer_explicit_dielectric_references(self, root):
        """Called once per file per editing session, right before Save actually writes the
           file: if any Dielectric would get a Reference auto-assigned from implicit
           Thickness-stacking, ask once whether to write it into the XML now - purely
           cosmetic/documentary, the computed z-positions are identical either way.
        """
        if self._asked_about_implicit_dielectric_references:
            return
        self._asked_about_implicit_dielectric_references = True

        dielectrics_elements = self._dielectrics_container(root)
        auto_refs = self._compute_auto_dielectric_references(dielectrics_elements)
        if not auto_refs:
            return

        names = ", ".join(auto_refs.keys())
        reply = QMessageBox.question(
            self, "Make implicit dielectric stacking explicit?",
            "These dielectrics use implicit (Thickness-based) stacking, which can now be "
            f"recorded explicitly with Reference:\n\n{names}\n\n"
            "Add Reference/ReferenceEdge attributes to these dielectrics now? Their computed "
            "position stays exactly the same either way - this is asked only once per file.",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            for element in dielectrics_elements:
                name = element.get("Name")
                if name in auto_refs:
                    reference, reference_edge = auto_refs[name]
                    element.set("Reference", reference)
                    element.set("ReferenceEdge", reference_edge)
            self.dielectrics_editor.reload()
            self.layers_editor.reload()

    def _save_to(self, filename):
        root = self.tree.getroot()
        errors = stackup_writer.validate_stackup(root)
        if errors:
            QMessageBox.warning(
                self, "Cannot save - validation errors",
                "Fix these problems before saving:\n\n" + "\n".join(f"- {e}" for e in errors))
            return False

        self._maybe_offer_explicit_dielectric_references(root)

        app_name = getattr(self.MainWindow, "APP_NAME", "setupEM")
        stackup_writer.stamp_header_comments(root, app_name, self.description_edit.toPlainText())

        try:
            stackup_writer.save_stackup_tree(self.tree, filename)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save {filename}:\n{e}")
            return False

        self.current_filename = filename
        self._set_filename_label(filename)
        # reflects what's now actually on disk, so save() only asks about a format
        # upgrade again if the version changes further from here, not on every save
        self._loaded_schema_version = root.get("schemaVersion")

        saved_values = getattr(self.MainWindow, "saved_values", {}) or {}
        substrate_file = saved_values.get("SubstrateFile")
        if substrate_file and os.path.normcase(os.path.abspath(substrate_file)) == os.path.normcase(os.path.abspath(filename)):
            reply = QMessageBox.question(
                self, "Reload in main window?",
                "This is the stackup file currently loaded in the main window.\n"
                "Reload it now so the change takes effect there too?",
                QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.MainWindow.read_XML()

        return True

    # ---------- substrate offset ----------

    def _on_offset_edited(self):
        if self.tree is None:
            return
        text = self.offset_edit.text().strip()
        try:
            value = float(text) if text else 0.0
        except ValueError:
            QMessageBox.warning(self, "Error", f"Not a valid offset value: '{text}'")
            offset_el = stackup_writer.get_substrate_offset_element(self.tree.getroot())
            self.offset_edit.setText(offset_el.get("Offset") if offset_el is not None else "0")
            return
        # Deferred: the mutation below can pop a modal dialog and rebuild the Layers
        # table. Doing that synchronously from inside editingFinished (itself part of
        # this QLineEdit's focus-out processing) is the same class of problem the
        # reload_on_attr_change path works around above - let the focus-out event
        # finish unwinding first.
        QTimer.singleShot(0, lambda: self._apply_offset_change(value))

    def _apply_offset_change(self, value):
        if self.tree is None:
            return
        root = self.tree.getroot()
        old_offset_el = stackup_writer.get_substrate_offset_element(root)
        try:
            old_value = float(old_offset_el.get("Offset")) if old_offset_el is not None else 0.0
        except (TypeError, ValueError):
            old_value = 0.0

        if value != 0:
            # UX improvement layered on top of the authoritative check in
            # validate_stackup() (also catches the case where Reference is added to a
            # layer after a nonzero offset already exists) - catch it here too, before
            # Save-time, since this is the point where the user is actually setting it
            referenced_layer_names = [layer_el.get("Name") or "<unnamed>"
                                       for layer_el in self._layers_container(root) if layer_el.get("Reference")]
            if referenced_layer_names:
                QMessageBox.warning(
                    self, "Cannot set Substrate Offset",
                    "Substrate Offset cannot be combined with Reference-based Layer "
                    "positioning (it would be ambiguous whether the offset applies "
                    "before or after Reference resolution). Layers using Reference:\n\n"
                    + "\n".join(referenced_layer_names))
                self.offset_edit.setText(old_offset_el.get("Offset") if old_offset_el is not None else "0")
                return

        delta = value - old_value

        stackup_writer.set_substrate_offset(root, value)

        layer_elements = self._layers_container(root)
        if delta and layer_elements:
            length_unit = (root.find("ELayers").get("LengthUnit") or "").strip()
            reply = QMessageBox.question(
                self, "Substrate Offset changed",
                f"The Substrate Offset changed by {delta:+.4f}{length_unit}.\n\n"
                "All layers are defined relative to this offset, so their effective "
                "position moves along with it unless their Zmin/Zmax are compensated.\n\n"
                "Yes - SHIFT: leave every layer's Zmin/Zmax unchanged, so the whole "
                "layer stack moves together with the new offset.\n"
                "No - KEEP POSITION: adjust every layer's Zmin/Zmax by "
                f"{-delta:+.4f}{length_unit}, so layers stay at the same effective "
                "position as before.",
                QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                for layer_el in layer_elements:
                    try:
                        zmin = float(layer_el.get("Zmin"))
                        zmax = float(layer_el.get("Zmax"))
                    except (TypeError, ValueError):
                        continue
                    layer_el.set("Zmin", f"{zmin - delta:.4f}")
                    layer_el.set("Zmax", f"{zmax - delta:.4f}")
                self.layers_editor.reload()

        self._on_changed(structural=True)

    # ---------- convert to Reference position format ----------

    def _on_convert_to_reference_clicked(self):
        if self.tree is None:
            return
        root = self.tree.getroot()

        offset_el = stackup_writer.get_substrate_offset_element(root)
        try:
            offset = float(offset_el.get("Offset")) if offset_el is not None else 0.0
        except (TypeError, ValueError):
            offset = 0.0

        message = (
            "Convert this stackup to Reference-relative positioning?\n\n"
            "Every Dielectric and Layer not already using Reference will be repositioned "
            "relative to the nearest thing below it (a Dielectric's Bottom edge, or another "
            "Layer's Top edge) instead of an absolute z-position. Resulting absolute "
            "positions stay exactly the same.")
        if offset:
            message += (
                f"\n\nSubstrate Offset is currently {offset:g} - Reference-based Layer "
                "positioning requires it to be 0, so as a first step it will be folded into "
                "every Layer's own Zmin/Zmax and then removed.")

        reply = QMessageBox.question(self, "Convert to Reference position format", message,
                                      QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        try:
            self._convert_to_reference_format()
        except (Exception, SystemExit) as e:
            QMessageBox.warning(self, "Conversion failed",
                                 f"Could not convert - current stackup could not be fully "
                                 f"resolved:\n{e}")
            return

        self._reload_all_editors()
        self._on_changed(structural=True)

    def _convert_to_reference_format(self):
        """Rewrites every Dielectric/Layer that isn't already Reference-based to use
           Reference instead of an absolute z-position, bumps schemaVersion to "3.0", and
           stamps a comment noting the minimum gds2palace reader version needed to read the
           result back (see stamp_reference_format_comment()). First folds a nonzero
           <Substrate Offset> into the Layers' own absolute Zmin/Zmax and removes it, since
           Reference-based Layers and a nonzero Offset are mutually exclusive (see
           validate_stackup()).
        """
        root = self.tree.getroot()

        offset_el = stackup_writer.get_substrate_offset_element(root)
        try:
            offset = float(offset_el.get("Offset")) if offset_el is not None else 0.0
        except (TypeError, ValueError):
            offset = 0.0
        if offset:
            for layer_el in self._layers_container(root):
                if layer_el.get("Reference"):
                    continue  # already Reference-based, never was in the +Offset frame
                try:
                    zmin = float(layer_el.get("Zmin"))
                    zmax = float(layer_el.get("Zmax"))
                except (TypeError, ValueError):
                    continue
                layer_el.set("Zmin", f"{zmin + offset:.4f}")
                layer_el.set("Zmax", f"{zmax + offset:.4f}")
            stackup_writer.set_substrate_offset(root, 0)

        # Resolve concrete absolute positions for everything exactly as the reader would,
        # now that Layers are offset-free - this is the ground truth the new Reference
        # attributes get derived from, computed once up front rather than incrementally,
        # so conversion order doesn't matter and there's no risk of compounding rounding.
        materials_list, dielectrics_list, metals_list = stackup_reader.parse_substrate(root)

        dielectric_elements = {d.get("Name"): d for d in self._dielectrics_container(root)}
        for dielectric in dielectrics_list.dielectrics:
            element = dielectric_elements.get(dielectric.name)
            if element is None or element.get("Reference"):
                continue  # already Reference-based - leave untouched
            candidate = self._nearest_reference_candidate(
                dielectric.zmin, dielectric.name, dielectrics_list.dielectrics, [],
                fallback_to_lowest_dielectric=False)
            if candidate is None:
                continue  # nothing below it - stays the natural implicit anchor at z=0
            candidate_name, candidate_edge, candidate_z = candidate

            offset_zmin = dielectric.zmin - candidate_z
            if abs(offset_zmin) > 1e-6:
                element.set("Zmin", f"{offset_zmin:.4f}")
            elif "Zmin" in element.attrib:
                del element.attrib["Zmin"]
            if "Zmax" in element.attrib:
                # Reference mode sizes from Thickness by default; a leftover absolute Zmax
                # would instead override that as an explicit offset - always wrong here
                del element.attrib["Zmax"]
            if "Thickness" not in element.attrib:
                element.set("Thickness", f"{dielectric.thickness:.4f}")
            element.set("Reference", candidate_name)
            element.set("ReferenceEdge", candidate_edge)

        layer_elements = {l.get("Name"): l for l in self._layers_container(root)}
        for metal in metals_list.metals:
            element = layer_elements.get(metal.name)
            if element is None or element.get("Reference"):
                continue  # already Reference-based - leave untouched
            candidate = self._nearest_reference_candidate(
                metal.zmin, metal.name, dielectrics_list.dielectrics, metals_list.metals,
                fallback_to_lowest_dielectric=True)
            if candidate is None:
                continue  # no dielectric in the file at all - nothing sensible to reference
            candidate_name, candidate_edge, candidate_z = candidate

            element.set("Zmin", f"{metal.zmin - candidate_z:.4f}")
            element.set("Zmax", f"{metal.zmax - candidate_z:.4f}")
            element.set("Reference", candidate_name)
            element.set("ReferenceEdge", candidate_edge)

        root.set("schemaVersion", "3.0")
        stackup_writer.stamp_reference_format_comment(root, stackup_reader.__version__)

    @staticmethod
    def _nearest_reference_candidate(zmin, exclude_name, dielectrics, metals, fallback_to_lowest_dielectric):
        """Find the best Reference target for an element positioned at `zmin`: among every
           other Dielectric's Top and Bottom edge, and every Layer's Top edge, the one with
           the largest z that is still at or below `zmin` - i.e. the nearest thing below,
           whether touching (gap 0) or not (e.g. a capacitor plate floating a small distance
           above the metal below it). Both Dielectric edges are real candidates for different
           reasons: another Dielectric's Top is where one stacks directly on the one below;
           a Dielectric's own Bottom is where the lowest Layer *inside* it naturally sits.
           `metals` should be [] when converting a Dielectric (Reference on a Dielectric can
           only target another Dielectric, never a Layer). Dielectrics are checked before
           metals, so an exact tie (both at the same z) prefers the Dielectric - more
           fundamental/stable a target than an individual Layer.
        Args:
            zmin (float): the element's own resolved absolute Zmin
            exclude_name (string): don't consider a candidate with this name (self)
            dielectrics (list of dielectric_layer): candidate Dielectric Top/Bottom edges
            metals (list of metal_layer): candidate Layer Top edges
            fallback_to_lowest_dielectric (bool): if no candidate qualifies (this element
                is the lowest thing in the whole stack), fall back to the lowest Dielectric's
                Bottom edge with whatever (possibly negative) offset that implies - used for
                Layers (e.g. a backside ground plane below the substrate); Dielectrics have
                no such fallback, since there both being asked here is what defines "lowest"
        Returns:
            (name, edge, z) of the chosen candidate, or None if there isn't one
        """
        epsilon = 1e-5
        best = None  # (z, name, edge)
        for dielectric in dielectrics:
            if dielectric.name == exclude_name:
                continue
            for edge, z in (("Top", dielectric.zmax), ("Bottom", dielectric.zmin)):
                if z <= zmin + epsilon and (best is None or z > best[0]):
                    best = (z, dielectric.name, edge)
        for metal in metals:
            if metal.name == exclude_name:
                continue
            z = metal.zmax
            if z <= zmin + epsilon and (best is None or z > best[0]):
                best = (z, metal.name, "Top")

        if best is not None:
            return best[1], best[2], best[0]
        if fallback_to_lowest_dielectric and dielectrics:
            lowest = min(dielectrics, key=lambda d: d.zmin)
            return lowest.name, "Bottom", lowest.zmin
        return None

    # ---------- file description ----------

    def _on_description_edited(self):
        if self.tree is None:
            return
        root = self.tree.getroot()
        current = stackup_writer.get_file_description(root)
        new_text = self.description_edit.toPlainText().strip()
        if new_text == current:
            return  # focus-out with no actual change - don't spend an undo step on it
        app_name = getattr(self.MainWindow, "APP_NAME", "setupEM")
        stackup_writer.stamp_header_comments(root, app_name, new_text)
        self._on_changed()

    # ---------- change propagation ----------

    def _on_materials_changed(self, structural=False):
        # material names may have changed (add/remove/rename) - refresh the
        # Material-reference dropdowns shown in the Dielectrics/Layers tabs
        self.dielectrics_editor.reload()
        self.layers_editor.reload()
        self._on_changed(structural=structural)

    def _on_dielectrics_changed(self, structural=False):
        # a Dielectric's Thickness/Zmin/Zmax/Name may have changed (add/remove/reorder
        # too) - the Layers tab's ResultZmin/ResultZmax and its Reference dropdown
        # choices both depend on the current Dielectrics tab state, so refresh it too
        self.layers_editor.reload()
        self._on_changed(structural=structural)

    def _on_changed(self, structural=False):
        # every edit path (cell edit, add/remove/move, offset edit) funnels through
        # here exactly once per logical user action, right after the mutation has
        # already been applied - which makes this the one place that needs to know
        # about undo, rather than instrumenting every mutating call site individually
        self._record_undo_point()
        self._revalidate_and_refresh()

    def _revalidate_and_refresh(self):
        if self.tree is None:
            return
        root = self.tree.getroot()
        errors = stackup_writer.validate_stackup(root)
        self._refresh_preview(root, errors)
        self._refresh_validation_status(errors)

    # ---------- undo (single level) ----------

    def _reset_undo_baseline(self):
        # called after loading/creating a tree: that's a new starting point, not
        # an "edit" - there is nothing to undo back to across a file boundary
        self._last_snapshot = copy.deepcopy(self.tree.getroot()) if self.tree is not None else None
        self._undo_snapshot = None
        self._set_undo_available(False)

    def _record_undo_point(self):
        if self.tree is None:
            return
        # self._last_snapshot is always an independent deep copy made before the
        # change that was just applied - promote it to the undo target, then take
        # a fresh independent copy of the now-current (post-change) state ready to
        # serve as the "before" snapshot for whatever gets edited next
        self._undo_snapshot = self._last_snapshot
        self._last_snapshot = copy.deepcopy(self.tree.getroot())
        self._set_undo_available(self._undo_snapshot is not None)

    def _set_undo_available(self, enabled):
        self.undo_action.setEnabled(enabled)

    def undo(self):
        if self._undo_snapshot is None or self.tree is None:
            return
        self.tree = ET.ElementTree(self._undo_snapshot)
        self._undo_snapshot = None
        # the restored state becomes the new baseline; only one level of undo,
        # so there is no redo and no further undo until another edit is made
        self._last_snapshot = copy.deepcopy(self.tree.getroot())
        self._set_undo_available(False)
        self._reload_all_editors()
        self._revalidate_and_refresh()

    def _refresh_preview(self, root, errors):
        if errors:
            # data is not fully consistent yet (e.g. mid-edit) - leave the last
            # good preview showing rather than risk parse_substrate() choking on it
            return
        try:
            materials_list, dielectrics_list, metals_list = stackup_reader.parse_substrate(root)
        except (Exception, SystemExit):
            # SystemExit is caught deliberately (not just Exception): the reader's
            # derived_layer class calls exit(1) on a handful of hard requirements
            # (invalid Operation, wrong operand count, ...) instead of raising.
            # validate_stackup()'s DerivedLayers checks mirror those requirements
            # exactly so errors should already be non-empty and we shouldn't even
            # get here, but this is cheap insurance against ending the whole
            # process over a gap in that mirroring rather than just skipping a
            # preview refresh.
            return
        self.vector_widget.materials_list = materials_list
        self.vector_widget.dielectrics_list = dielectrics_list
        self.vector_widget.metals_list = metals_list
        self.vector_widget.update()

    def _refresh_validation_status(self, errors):
        if not errors:
            self.status_label.setText("Valid.")
            self.status_label.setStyleSheet("color: green;")
        else:
            self.status_label.setText(f"{len(errors)} problem(s) - see Save for details.")
            self.status_label.setStyleSheet("color: darkred;")
