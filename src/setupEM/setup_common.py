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
setup_common.py

Shared building blocks for the setupEM (Palace/Elmer EM) and setupThermal
(Elmer thermal) PySide6 GUI applications. This module holds only the pieces
that are genuinely identical (or parameterizably identical) between the two
apps: style constants, small helpers, the file-input tab, the Python code
editor/highlighter, the stackup cross-section preview widget, the shared
"Create Model" tab base class, and the shared "MainWindow" base class.

Anything that differs in real behavior between the two apps (ports vs.
thermal objects data model, frequency sweep settings, Palace/Elmer specific
mesh fields, the Python model code generator bodies) is intentionally left
in setupEM.py / setupThermal.py, not here.
"""

import sys, os, json, pathlib, ast, webbrowser, io, contextlib
import xml.etree.ElementTree as ET
import numpy as np
import requests
import gdspy
from scipy.interpolate import interp1d
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QLineEdit, QComboBox,
    QPushButton, QFileDialog, QMessageBox, QGroupBox,
    QCheckBox, QPlainTextEdit, QDialog, QSizePolicy, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
    )
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QFont, QFontMetrics, QSyntaxHighlighter, QPainter, QPen, QTextDocument
from PySide6.QtCore import Qt, QRegularExpression, QProcess, QRect, QTimer, QSettings

# we expect gds2palace in the same directory as this code, or installed as module
import gds2palace
from gds2palace import *

# ------------------------------------------------------------------
# gds2palace feature-compatibility detection: an older gds2palace (e.g. a stale
# bundled copy, or an outdated pip install) may be missing modules/functions this
# app relies on for the stackup editor and the file-description display. Detect
# by capability (not __version__ string matching, which is easy to drift out of
# sync) so the app degrades exactly as far as it needs to and no further, rather
# than crashing the moment a missing symbol is touched.
#
# The stackup writer (edit/save/validate) itself lives in setupEM now, not
# gds2palace - it has no installed-version dependency, so there is nothing left
# to detect for it here. Only parse_substrate()/read_file_description() (genuine
# gds2palace-reader capabilities) still need a version guard.
# ------------------------------------------------------------------
GDS2PALACE_HAS_PARSE_SUBSTRATE = hasattr(stackup_reader, "parse_substrate")
GDS2PALACE_HAS_FILE_DESCRIPTION = hasattr(stackup_reader, "read_file_description")
GDS2PALACE_HAS_VARIABLES_LIST = hasattr(stackup_reader, "variables_list") and hasattr(stackup_reader, "variable")

# Tools > Edit Stackup XML... needs parse_substrate() (used for its live preview
# refresh); the Input Files tab's description display only needs the reader-side lookup.
GDS2PALACE_SUPPORTS_STACKUP_EDITOR = GDS2PALACE_HAS_PARSE_SUBSTRATE
GDS2PALACE_SUPPORTS_FILE_DESCRIPTION = GDS2PALACE_HAS_FILE_DESCRIPTION
GDS2PALACE_OUTDATED = not (GDS2PALACE_SUPPORTS_STACKUP_EDITOR and GDS2PALACE_SUPPORTS_FILE_DESCRIPTION)


# QSettings scope for the File menu's "Load Recent Settings"/"Import Recent Model" lists -
# per-app (organization + self.APP_NAME, i.e. "setupEM" or "setupThermal"), mirroring
# stackupEditor.py's own "Open Recent" mechanism (same org name, separate app/key there).
RECENT_FILES_ORG = "muehlhaus.com"
RECENT_SETTINGS_KEY = "recentSettingsFiles"
RECENT_MODEL_KEY = "recentModelFiles"
MAX_RECENT_FILES = 10


def _read_substrate_variables(filename):
    """Parse a stackup XML file's <Variables> block (if any) into a resolved
       stackup_reader.variables_list, independent of the full read_substrate()/
       parse_substrate() pipeline (materials/dielectrics/layers) - mirrors
       stackupEditor.py's own _build_variables_list() helper. Returns None if the
       file declares no Variables at all, or if parsing/resolving fails (e.g.
       invalid XML, a circular "="-expression) - callers treat both the same way:
       hide the override grid rather than raising into the caller.
    """
    if not GDS2PALACE_HAS_VARIABLES_LIST:
        return None
    try:
        root = ET.parse(filename).getroot()
        elements = list(root.iter("Variable"))
        if not elements:
            return None
        variables = stackup_reader.variables_list()
        for element in elements:
            variables.append(stackup_reader.variable(element))
        variables.resolve_all()
        return variables
    except (Exception, SystemExit):
        return None


def _format_resolved_variable_value(value):
    """Format a resolved Variable value (float or str) as plain text - a whole-number
       float (e.g. 200.0) is shown without a trailing ".0", matching how such values are
       normally hand-typed in the stackup XML. Mirrors stackupEditor.py's identically
       named helper.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ------------------------------------------------------------------
# Shared style constants
# ------------------------------------------------------------------

EDIT_STYLE_OPTIONAL = """
            QLineEdit {
                background-color: white;
                border: 1px solid gray;
                border-radius: 4px;
                padding: 4px;
            }
        """

EDIT_STYLE_REQUIRED = """
            QLineEdit {
                background-color: lightyellow;
                border: 1px solid gray;
                border-radius: 4px;
                padding: 4px;
            }
        """

COMBO_STYLE_REQUIRED = """
    QComboBox {
        background-color: lightyellow;
        border: 1px solid gray;
        border-radius: 4px;
        padding: 4px;
        combobox-popup: 0;
    }
"""

COMBO_STYLE_OPTIONAL = """
    QComboBox {
        background-color: white;
        border: 1px solid gray;
        border-radius: 4px;
        padding: 4px;
        combobox-popup: 0;
    }
"""


# ------------------------------------------------------------------
# Small shared helpers
# ------------------------------------------------------------------

def get_saved_value(saved_values, key, default):
    # saved_values is passed in explicitly (instead of closing over a module
    # global), so this same helper works for both apps' own saved_values dict
    data = saved_values.get('saved_values', None)
    if data is not None:
        if key in data.keys():
            return data[key]
    else:
        if key in saved_values.keys():
            return saved_values[key]
        else:
            return default


def parse_assignments(file_path):
    # parse lines from a Python model code for variable assigments
    parameters = {}

    for line in pathlib.Path(file_path).read_text().splitlines():
        # Remove comments (anything after # or //)
        line = line.split('#', 1)[0].split('//', 1)[0].strip()

        # Skip blank lines
        if not line:
            continue

        # Split only if '=' exists
        if '=' in line:
            param, value = map(str.strip, line.split('=', 1))
            param = param.replace('settings', '')
            param = param.strip("[]'").strip('"')
            value = value.strip("'").strip('"')
            if not "settings" in value:  # make sure we don't read the USE of a parameter
                parameters[param] = value

    return parameters


# ----------------------------------------

class FileDropLineEdit(QLineEdit):
    def __init__(self, allowed_extensions=None, on_file_dropped=None):
        super().__init__()
        self.setAcceptDrops(True)

        # Example: [".png", ".txt"]
        self.allowed_extensions = allowed_extensions or []

        # Function to execute after a successful drop
        # Signature: callback(path: str)
        self.on_file_dropped = on_file_dropped

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._contains_valid_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and self._contains_valid_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            files = [url.toLocalFile() for url in event.mimeData().urls()]
            valid_files = [f for f in files if self._is_valid(f)]

            if valid_files:
                file_path = valid_files[0]
                self.setText(file_path)
                event.acceptProposedAction()

                # 🚀 Call the user function if set
                if self.on_file_dropped:
                    self.on_file_dropped(file_path)

            else:
                self.setText("Invalid file type")
                event.ignore()

    def _contains_valid_files(self, event):
        for url in event.mimeData().urls():
            if self._is_valid(url.toLocalFile()):
                return True
        return False

    def _is_valid(self, path):
        if not self.allowed_extensions:
            return True
        ext = os.path.splitext(path)[1].lower()
        return ext in self.allowed_extensions


# ---------- FILE INPUT TAB ----------
class FileInputTab(QWidget):
    # File definitions go here
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow  # parent = MainWindow

        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignTop)

        # ---------- GDSII FILE GROUP ----------
        left_label_width = 220

        self.gds_group = QGroupBox("GDSII Layout File")
        self.gds_layout = QVBoxLayout()

        self.gds_file_layout = QHBoxLayout()
        self.gds_file_edit = FileDropLineEdit([".gds", ".GDS"], self.set_gds_file)
        self.gds_file_edit.setText("Please choose a file ===>")
        self.gds_file_edit.setStyleSheet(EDIT_STYLE_REQUIRED)

        self.browse_gds_btn = QPushButton("Browse ...")
        self.browse_gds_btn.setFixedWidth(150)  # narrower
        self.browse_gds_btn.clicked.connect(self.browse_gds_file)

        self.gds_file_layout.addWidget(self.gds_file_edit)
        self.gds_file_layout.addWidget(self.browse_gds_btn)

        self.gds_layout.addLayout(self.gds_file_layout)

        self.cellname_layout = QHBoxLayout()
        label = QLabel("Cellname")
        label.setFixedWidth(left_label_width)
        self.cellname_layout.addWidget(label)
        self.cellname_box = QComboBox()
        self.cellname_box.setFixedWidth(250)
        self.cellname_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.cellname_box.addItems([""])
        self.cellname_layout.addWidget(self.cellname_box)
        self.cellname_label2 = QLabel(" (leave empty for default)")
        self.cellname_layout.addWidget(self.cellname_label2)
        self.cellname_layout.addStretch()
        self.gds_layout.addLayout(self.cellname_layout)

        self.purpose_layout = QHBoxLayout()
        self.purpose_label1 = QLabel("Read this datatype (purpose):  ")
        self.purpose_label1.setFixedWidth(left_label_width)
        self.purpose_edit = QLineEdit("0")
        self.purpose_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.purpose_edit.setFixedWidth(70)
        self.purpose_label2 = QLabel(" (default=0, multiple values can be separated by comma)")
        self.purpose_layout.addWidget(self.purpose_label1)
        self.purpose_layout.addWidget(self.purpose_edit)
        self.purpose_layout.addWidget(self.purpose_label2)
        self.purpose_layout.addStretch()
        self.gds_layout.addLayout(self.purpose_layout)

        self.viamerge_layout = QHBoxLayout()
        self.viamerge_label1 = QLabel("Merge via arrays with spacing ")
        self.viamerge_label1.setFixedWidth(left_label_width)
        self.viamerge_edit = QLineEdit("0.5")
        self.viamerge_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.viamerge_edit.setFixedWidth(70)
        self.viamerge_label2 = QLabel(" micron or more, value 0 disables via array merging")
        self.viamerge_layout.addWidget(self.viamerge_label1)
        self.viamerge_layout.addWidget(self.viamerge_edit)
        self.viamerge_layout.addWidget(self.viamerge_label2)
        self.viamerge_layout.addStretch()
        self.gds_layout.addLayout(self.viamerge_layout)

        self.preprocess_layout = QHBoxLayout()
        self.preprocess_gds_checkbox = QCheckBox()
        self.preprocess_gds_checkbox.setFixedWidth(20)
        self.preprocess_gds_label = QLabel("Preprocess GDSII file (required for polygons with holes/cutouts)")
        # only relevant as a manual workaround for an outdated gds2palace; a
        # current gds2palace handles this natively, so hide it in that case
        self.preprocess_gds_checkbox.setVisible(GDS2PALACE_OUTDATED)
        self.preprocess_gds_label.setVisible(GDS2PALACE_OUTDATED)
        self.preprocess_layout.addWidget(self.preprocess_gds_checkbox)
        self.preprocess_layout.addWidget(self.preprocess_gds_label)
        self.gds_layout.addLayout(self.preprocess_layout)

        self.gds_group.setLayout(self.gds_layout)

        # ---------- XML FILE GROUP ----------

        self.XML_group = QGroupBox("XML Stackup File")
        self.XML_layout = QVBoxLayout()

        self.XML_file_layout = QHBoxLayout()
        self.XML_file_edit = FileDropLineEdit([".xml", ".XML"], self.set_XML_file)
        self.XML_file_edit.setText("Please choose a file ===>")
        self.XML_file_edit.setStyleSheet(EDIT_STYLE_REQUIRED)

        self.browse_XML_btn = QPushButton("Browse ...")
        self.browse_XML_btn.setFixedWidth(150)  # narrower
        self.browse_XML_btn.clicked.connect(self.browse_XML_file)

        self.XML_file_layout.addWidget(self.XML_file_edit)
        self.XML_file_layout.addWidget(self.browse_XML_btn)
        self.XML_layout.addLayout(self.XML_file_layout)

        self.XML_show_layout = QHBoxLayout()
        self.XML_show_layout.setAlignment(Qt.AlignRight)
        self.show_XML_btn = QPushButton("Show Stackup")
        self.show_XML_btn.setFixedWidth(150)
        self.show_XML_btn.clicked.connect(self.show_stackup)
        self.XML_show_layout.addWidget(self.show_XML_btn)
        self.XML_layout.addLayout(self.XML_show_layout)

        # optional free-text description read from the file (see
        # gds2palace.stackup_reader.read_file_description()); hidden entirely
        # when the file has none, and placed below the Show Stackup button (not
        # between it and the file field) so that button's position never shifts
        # when the description appears/disappears. The trailing fixed-width
        # spacer mirrors browse_XML_btn's width/position above it, so the
        # label's own width (Expanding policy fills what's left in its row)
        # matches XML_file_edit's.
        self.XML_description_container = QWidget()
        self.XML_description_layout = QHBoxLayout()
        self.XML_description_layout.setContentsMargins(0, 0, 0, 0)
        # QPlainTextEdit instead of a plain QLabel: a QLabel has no cap on how tall
        # word-wrap can grow it, so a long description silently stretched the whole
        # window taller. This wraps/scrolls within a max ~10-line height instead
        # (actual height is sized to the content by _resize_XML_description_label(),
        # so a short description doesn't leave a tall empty gap below the text),
        # styled to still read as a plain label (no frame, no editable background).
        self.XML_description_label = QPlainTextEdit("")
        self.XML_description_label.setReadOnly(True)
        self.XML_description_label.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.XML_description_label.setFrameStyle(QFrame.NoFrame)
        self.XML_description_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.XML_description_label.setStyleSheet(
            "color: #666666; font-style: italic; background: transparent;")
        _description_line_height = QFontMetrics(self.XML_description_label.font()).lineSpacing()
        self.XML_description_label.setFixedHeight(_description_line_height * 10 + 6)
        self.XML_description_layout.addWidget(self.XML_description_label)
        self.XML_description_spacer = QWidget()
        self.XML_description_spacer.setFixedWidth(self.browse_XML_btn.width())
        self.XML_description_layout.addWidget(self.XML_description_spacer)
        self.XML_description_container.setLayout(self.XML_description_layout)
        self.XML_description_container.setVisible(False)
        self.XML_layout.addWidget(self.XML_description_container)

        # editable grid of the file's <Variable>s (if any) - lets a user override a
        # stackup Variable's value (e.g. total_thickness, air_thickness) from the GUI,
        # without hand-editing the XML or the generated model script. Fed into the
        # generated model's stackup_reader.read_substrate(..., variable_overrides=...)
        # call. Hidden entirely when the chosen file declares no Variables.
        self.variable_overrides_container = QWidget()
        self.variable_overrides_layout = QVBoxLayout()
        self.variable_overrides_layout.setContentsMargins(0, 0, 0, 0)
        self.variable_overrides_label = QLabel("Override stackup Variables:")
        self.variable_overrides_layout.addWidget(self.variable_overrides_label)
        self.variable_overrides_table = QTableWidget()
        self.variable_overrides_table.setColumnCount(3)
        self.variable_overrides_table.setHorizontalHeaderLabels(["Variable", "XML value", "Override value"])
        self.variable_overrides_table.horizontalHeaderItem(2).setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.variable_overrides_table.verticalHeader().setVisible(False)
        self.variable_overrides_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.variable_overrides_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        variable_overrides_header = self.variable_overrides_table.horizontalHeader()
        variable_overrides_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        variable_overrides_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        variable_overrides_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        _variable_row_height = QFontMetrics(self.variable_overrides_table.font()).lineSpacing() + 10
        self.variable_overrides_table.setFixedHeight(_variable_row_height * 6)  # header + ~5 rows, then scroll
        self.variable_overrides_layout.addWidget(self.variable_overrides_table)
        self.variable_overrides_container.setLayout(self.variable_overrides_layout)
        self.variable_overrides_container.setVisible(False)
        self.XML_layout.addWidget(self.variable_overrides_container)

        self.XML_group.setLayout(self.XML_layout)

        self.main_layout.addWidget(self.gds_group)
        self.main_layout.addSpacing(20)
        self.main_layout.addWidget(self.XML_group)
        self.main_layout.addStretch()
        self.setLayout(self.main_layout)

    def browse_gds_file(self):
        # start browsing from previous file location, if valid
        previous_file = self.gds_file_edit.text()
        previous_directory = os.path.dirname(previous_file)
        if not os.path.isdir(previous_directory):
            previous_directory = ""

        filename, _ = QFileDialog.getOpenFileName(self, "Select GDSII File", previous_directory, "*.gds;;*.*")
        if filename:
            self.set_gds_file(filename)
            self.update_cellnames_from_gds(filename)

    def update_cellnames_from_gds(self, filename):
        if filename:
            # get top level cellnames now
            lib = gdspy.GdsLibrary()
            lib.read_gds(filename)
            cellnames = list(lib.cells.keys())
            self.cellname_box.clear()
            self.cellname_box.addItem("")  # blank for default
            for cellname in cellnames:
                self.cellname_box.addItem(cellname)

    def set_gds_file(self, filename):
        # clear model name and target dir if they were auto-generated from previous model
        self.MainWindow.clear_modelname_and_targetdir()
        self.gds_file_edit.setText(filename)
        self.MainWindow.saved_values["GdsFile"] = filename.replace('\\', '/')
        # file is read when leaving the files tab
        self.update_cellnames_from_gds(filename)

    def browse_XML_file(self):
        # start browsing from previous file location, if valid
        previous_file = self.XML_file_edit.text()
        previous_directory = os.path.dirname(previous_file)
        if not os.path.isdir(previous_directory):
            # try to get XML files bundled in setupEM package
            package_data = os.path.join(os.path.dirname(__file__), "data")
            if os.path.exists(package_data):
                previous_directory = package_data
            else:
                previous_directory = ""

        filename, _ = QFileDialog.getOpenFileName(self, "Select XML Stackup File", previous_directory, "*.xml;;*.*")
        if filename:
            self.set_XML_file(filename)

    def set_XML_file(self, filename):
        self.XML_file_edit.setText(filename)
        self.MainWindow.saved_values["SubstrateFile"] = filename.replace('\\', '/')
        self.update_XML_description(filename)
        self.update_variable_overrides_grid(filename)
        self.MainWindow.read_XML()  # shows an error dialog and keeps prior data on invalid/unparseable XML
        # file is read when leaving the files tab

    def update_XML_description(self, filename):
        if not GDS2PALACE_SUPPORTS_FILE_DESCRIPTION:
            return  # older gds2palace has no read_file_description() - stay hidden
        # collapse any line breaks from the file itself - wrapping here is purely
        # width-driven (setWordWrap), not a reflow of the author's original lines
        description = " ".join(stackup_reader.read_file_description(filename).split())
        self.XML_description_label.setPlainText(description)
        self.XML_description_container.setVisible(bool(description))
        self._resize_XML_description_label(description)

    def _resize_XML_description_label(self, description):
        # size the box to how many lines this description actually wraps to (1 line
        # up to the ~10-line cap), instead of always reserving the full 10 lines -
        # a one-line description otherwise leaves a tall empty gap below the text.
        # Uses QFontMetrics.boundingRect() with word-wrap, not
        # self.XML_description_label.document().size() - the latter goes through
        # QPlainTextEdit's own QPlainTextDocumentLayout, which does not reliably honor
        # an externally-set textWidth outside of the widget's own resize handling, and
        # under-measured the real wrapped height in testing (collapsed even multi-line
        # descriptions to one line and clipped the text).
        line_height = QFontMetrics(self.XML_description_label.font()).lineSpacing()
        min_height = line_height + 6
        max_height = line_height * 10 + 6
        if not description:
            self.XML_description_label.setFixedHeight(min_height)
            return
        width = self.XML_description_label.viewport().width()
        if width <= 0:
            width = self.XML_description_label.width()
        if width <= 0:
            # not laid out yet (e.g. called before the window is shown) - can't
            # measure wrapping reliably, so fall back to the old always-max-height
            # behavior rather than risk collapsing to one line and clipping text.
            self.XML_description_label.setFixedHeight(max_height)
            return
        # the editable area is narrower than the viewport by the document's own left/
        # right margins (default 4px each).
        usable_width = width - 2 * self.XML_description_label.document().documentMargin()
        # measured with a throwaway QTextDocument, not self.XML_description_label's own
        # document - QPlainTextEdit's document uses QPlainTextDocumentLayout, which does
        # not reliably report size() for a textWidth set outside the widget's own resize
        # handling (confirmed in testing: always came back as one line, regardless of
        # actual content, clipping multi-line descriptions). A plain QTextDocument uses
        # the standard QTextDocumentLayout instead, which measures correctly on demand.
        measuring_doc = QTextDocument()
        measuring_doc.setDefaultFont(self.XML_description_label.font())
        measuring_doc.setTextWidth(usable_width)
        measuring_doc.setPlainText(description)
        new_height = max(min_height, min(measuring_doc.size().height() + 6, max_height))
        self.XML_description_label.setFixedHeight(int(new_height))

    def update_variable_overrides_grid(self, filename):
        # re-populates from scratch every time - no attempt to preserve in-progress,
        # not-yet-saved edits across a file switch, matching how the description label
        # above it is refreshed unconditionally too.
        variables = _read_substrate_variables(filename)
        self.variable_overrides_table.setRowCount(0)
        if not variables:
            self.variable_overrides_container.setVisible(False)
            return
        # exclude "="-expression Variables (e.g. bulk_thickness = total_thickness-20) -
        # only plain literal values make sense as an override starting point here, since
        # a computed value is meant to follow whatever it depends on, not be pinned itself.
        plain_variables = [var for var in variables.variables if not var.is_expression]
        if not plain_variables:
            self.variable_overrides_container.setVisible(False)
            return

        persisted_overrides = self.MainWindow.saved_values.get("variable_overrides") or {}
        self.variable_overrides_table.setRowCount(len(plain_variables))
        for row, var in enumerate(plain_variables):
            xml_value_text = _format_resolved_variable_value(var.value)
            override_value = persisted_overrides.get(var.name, var.value)
            override_text = override_value if isinstance(override_value, str) \
                else _format_resolved_variable_value(override_value)

            name_item = QTableWidgetItem(var.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            xml_item = QTableWidgetItem(xml_value_text)
            xml_item.setFlags(xml_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            override_item = QTableWidgetItem(override_text)

            self.variable_overrides_table.setItem(row, 0, name_item)
            self.variable_overrides_table.setItem(row, 1, xml_item)
            self.variable_overrides_table.setItem(row, 2, override_item)
        self.variable_overrides_container.setVisible(True)

    def get_variable_overrides(self):
        """Read the Override value column back out as a dict of only the rows whose
           override text differs from the file's own XML value column - e.g.
           {'total_thickness': 500.0}. A value that parses as a number becomes float
           (matching what stackup_reader.variable.apply_override() expects for a
           numeric variable); anything else is kept as the typed string.
        """
        overrides = {}
        table = self.variable_overrides_table
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            xml_item = table.item(row, 1)
            override_item = table.item(row, 2)
            if not name_item or not xml_item or not override_item:
                continue
            xml_text = xml_item.text()
            override_text = override_item.text()
            if override_text == xml_text:
                continue
            try:
                overrides[name_item.text()] = float(override_text)
            except ValueError:
                overrides[name_item.text()] = override_text
        return overrides

    def load_values(self):
        saved_values = self.MainWindow.saved_values
        self.gds_file_edit.setText(get_saved_value(saved_values, "GdsFile", "Please choose a file ===>"))
        XML = get_saved_value(saved_values, "SubstrateFile", "Please choose a file ===>")
        self.XML_file_edit.setText(XML)
        self.update_XML_description(XML)
        self.update_variable_overrides_grid(XML)
        self.cellname_box.clear()
        self.cellname_box.addItem(get_saved_value(saved_values, "cellname", ""))
        self.viamerge_edit.setText(str(get_saved_value(saved_values, "merge_polygon_size", "0.5")))
        self.preprocess_gds_checkbox.setChecked(bool(get_saved_value(saved_values, "preprocess_gds", True)))

        int_list = saved_values.get("purpose", "0")
        purpose_string = str(int_list).replace('[', '').replace(']', '')
        self.purpose_edit.setText(purpose_string)
        # self.purpose_edit.setText(','.join(map(str, int_list)))

        # read_XML(self.XML_file_edit.text()) # safe if invalid filename

    def save_values(self):
        saved_values = self.MainWindow.saved_values
        saved_values["GdsFile"] = self.gds_file_edit.text().replace('\\', '/')
        saved_values["SubstrateFile"] = self.XML_file_edit.text().replace('\\', '/')
        saved_values["preprocess_gds"] = self.preprocess_gds_checkbox.isChecked()
        saved_values["cellname"] = self.cellname_box.currentText()
        saved_values["variable_overrides"] = self.get_variable_overrides()

        try:
            merge_polygon_size = float(self.viamerge_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", f"Not a valid value for via array merging")
            self.viamerge_edit.setText("0.5")
            return False
        saved_values["merge_polygon_size"] = float(merge_polygon_size)

        text = self.purpose_edit.text()
        if text != "":
            # save as list of comma separated values
            saved_values["purpose"] = ast.literal_eval('[' + text + ']')
        else:
            saved_values["purpose"] = [0]  # safe default

        # also trigger the load function of CreateModelTab, because that uses gds file info
        self.MainWindow.create_model_tab.load_values()

        # read Substrate file, which also updates port target layer choices
        self.MainWindow.read_XML()

        return True  # Tab change only possible when returning True

    def show_stackup(self):
        # "Show Stackup" is a plain button click, not a tab change, so nothing
        # would otherwise flush the override grid into saved_values/materials_list
        # before the popup reads them - save first so edited overrides not yet
        # committed by switching tabs still show up in the preview.
        if not self.save_values():
            return
        self.MainWindow.open_popup()


# ---- Python Syntax Highlighter ----
class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, parent):
        super().__init__(parent)
        self.highlighting_rules = []

        # --- Keyword format ---
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("blue"))
        keyword_format.setFontWeight(QFont.Bold)
        keywords = [
            "and", "as", "assert", "break", "class", "continue", "def",
            "del", "elif", "else", "except", "False", "finally", "for",
            "from", "global", "if", "import", "in", "is", "lambda", "None",
            "nonlocal", "not", "or", "pass", "raise", "return", "True",
            "try", "while", "with", "yield"
        ]
        for keyword in keywords:
            pattern = QRegularExpression(rf"\b{keyword}\b")
            self.highlighting_rules.append((pattern, keyword_format))

        # --- Strings format (Blue) ---
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("darkGreen"))
        # Single and double quoted strings
        self.highlighting_rules.append((QRegularExpression(r'".*?"'), string_format))
        self.highlighting_rules.append((QRegularExpression(r"'.*?'"), string_format))
        # Multi-line triple-quoted strings (both """ and ''')
        self.highlighting_rules.append((QRegularExpression(r'""".*?"""', QRegularExpression.DotMatchesEverythingOption), string_format))
        self.highlighting_rules.append((QRegularExpression(r"'''.*?'''", QRegularExpression.DotMatchesEverythingOption), string_format))

        # --- Comments format (Green, Italic) ---
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("gray"))
        comment_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"#.*"), comment_format))

        # --- Numbers format (Orange) ---
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("darkOrange"))
        number_regex = QRegularExpression(r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")
        self.highlighting_rules.append((number_regex, number_format))

        # --- Class name format (Cyan) ---
        class_format = QTextCharFormat()
        class_format.setForeground(QColor("darkCyan"))
        class_format.setFontWeight(QFont.Bold)
        self.highlighting_rules.append((QRegularExpression(r"\bclass\s+([A-Z]\w*)"), class_format))

        # --- Function name format (Yellow) ---
        function_format = QTextCharFormat()
        function_format.setForeground(QColor("darkGoldenrod"))
        function_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"\bdef\s+([a-zA-Z_]\w*)"), function_format))

    def highlightBlock(self, text):
        for pattern, fmt in self.highlighting_rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                # If there's a capture group, highlight only it (for class/function names)
                if match.lastCapturedIndex() > 0:
                    start = match.capturedStart(1)
                    length = match.capturedLength(1)
                else:
                    start = match.capturedStart()
                    length = match.capturedLength()
                self.setFormat(start, length, fmt)


# Editor widget
class CodeEditor(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        # self.setFont(QFont("Courier", 12))
        self.highlighter = PythonHighlighter(self.document())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)


# ---------- STACKUP PREVIEW COLOR/LABEL DEFAULTS (EM / permittivity-based) ------------------
#
# Shared here (rather than living only in setupEM.py, where they originated) so that any
# MainWindow-like object passed to VectorWidget's dielectric_color_fn/dielectric_label_fn/
# metal_label_fn - including stackupEditor.py's standalone-launch stand-in - gets the same
# real, permittivity/sheet-resistance-based preview instead of a flat placeholder. setupThermal.py
# does NOT use these: its thermal-conductivity-based preview is genuinely different information,
# not just a simplification of this one, so it keeps its own implementation.

def epsilon_to_color(erel, transparency):
    # Compute raw float components
    red   = 250 - 30 * (erel - 1)
    green = 255 - 20 * (erel - 1) + (20 / erel) + 10 * erel
    blue  = 100 + 15 * erel + (250 / erel)

    # Extra adjustment
    if 3.8 < erel < 4.5:
        red   += 50 * (erel - 3.8)
        green -= 100 * (erel - 3.8)

    # Clamp to range 0–255
    red   = min(max(red,   0), 255)
    green = min(max(green, 0), 255)
    blue  = min(max(blue,  0), 255)

    # Convert to integer RGB
    r = int(round(red))
    g = int(round(green))
    b = int(round(blue))

    return QColor(r, g, b, transparency)


def default_stackup_dielectric_label(dielectric, material):
    material_string = f'εr={material.eps:.1f}'
    if material.sigma > 1e-3:
        material_string = material_string + f' σ={material.sigma:.1f}'
    material_string = material_string + f'\n{dielectric.thickness:.2f}µm'
    return material_string


def default_stackup_metal_label(metal, material, is_sheet):
    if is_sheet:
        return f'Rs={material.Rs*1e3:.1f}mΩ'
    else:
        if (material.sigma > 0) and (metal.thickness > 0):
            Rs = 1 / (material.sigma*metal.thickness*1e-6)
            if Rs < 1:
                return f'Rs={Rs*1e3:.1f} mΩ'
            else:
                return f'Rs={Rs:.2f} Ω'
        else:
            return '? ' + material.type + ' ?'


# ---------- POP UP WINDOW TO SHOW STACKUP ------------------

class VectorWidget(QWidget):
    """This widget actually draws the stackup preview.

    The color/label logic for dielectrics and metals is genuinely different
    between setupEM (permittivity/sheet resistance) and setupThermal
    (thermal conductivity), so those bits are injected as callables instead
    of being hardcoded here:

        dielectric_color_fn(material) -> QColor
        dielectric_label_fn(dielectric, material) -> str
        metal_label_fn(metal, material, is_sheet) -> str
        via_label_suffix_fn(metal, material) -> str
    """

    def __init__(self, materials_list, dielectrics_list, metals_list,
                 dielectric_color_fn, dielectric_label_fn,
                 metal_label_fn, via_label_suffix_fn):
        super().__init__()
        self.materials_list = materials_list
        self.dielectrics_list = dielectrics_list
        self.metals_list = metals_list
        self.dielectric_color_fn = dielectric_color_fn
        self.dielectric_label_fn = dielectric_label_fn
        self.metal_label_fn = metal_label_fn
        self.via_label_suffix_fn = via_label_suffix_fn

    def paintEvent(self, event):

        # utility: flip y to have y=0 at bottom
        def flipy(y):
            return self.height() - y

        # utility to draw text with alignment on right side
        def drawText_right(x, y, w, h, text):
            rect = QRect(x, y - h, w, h)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, text)

        def drawText_left(x, y, w, h, text):
            rect = QRect(x, y - h, w, h)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)

        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        painter.setRenderHint(QPainter.Antialiasing)

        xmin = int(self.width() * 0.02)
        xmax = int(self.width() * 0.98)

        ymin = int(self.height() * 0.025)
        ymax = int(self.height() * 0.975)

        penBlack = QPen(Qt.black, 1)
        penGray = QPen(QColor(134, 132, 130))
        penDarkGray = QPen(QColor(53, 50, 47))

        # get total dielectric parts, where each metal in a dielectric adds one part
        dielectric_shapes = []
        total_parts = 0
        # sorted by resolved zmin, not just reversed file/array order: a Reference-based
        # dielectric's actual position comes from resolving its Reference by name (see
        # dielectric_layers_list.resolve_references()), entirely independent of where it
        # sits in the file - so reordering it there (e.g. Move Up/Down in the Dielectric
        # Stack tab) must not change where it's drawn here, even though it does change
        # self.dielectrics_list.dielectrics' own array order
        dielectrics_bottom_up = sorted(self.dielectrics_list.dielectrics, key=lambda d: d.zmin)
        for dielectric in dielectrics_bottom_up:  # bottom up
            painter.setPen(penBlack)

            metals_inside = dielectric.get_planar_metals_inside()
            # get number of unique zmin values in that list
            zmin_list = []
            for metal in metals_inside:
                if not metal.zmin in zmin_list:
                    zmin_list.append(metal.zmin)
            metals_count = len(zmin_list)

            # first metal not aligned with dielectric?
            if len(metals_inside) > 0:
                if metals_inside[0].zmin > dielectric.zmin:
                    metals_count = metals_count + 0.5

            parts = max(1, metals_count)
            dielectric_shape = {}
            dielectric_shape['name'] = dielectric.name
            dielectric_shape['dielectric'] = dielectric
            dielectric_shape['numparts'] = parts

            materialname = dielectric.material
            material = self.materials_list.get_by_name(materialname)
            # dielectric color/label are app-specific (permittivity vs. thermal conductivity)
            dielectric_shape['color'] = self.dielectric_color_fn(material)
            dielectric_shape['material'] = material

            total_parts = total_parts + parts
            dielectric_shapes.append(dielectric_shape)

        # calculate height of one dielectric shape
        total_parts = max(total_parts, 1)
        part_height = int((ymax - ymin) / (total_parts))

        y = ymin
        w = xmax - ymin

        # we need to store data for original z position and the displayed y position
        stored_z = np.array([0])
        stored_y = np.array([ymin])

        for dielectric_shape in dielectric_shapes:
            h = part_height * dielectric_shape['numparts']
            dielectric = dielectric_shape['dielectric']
            color = dielectric_shape['color']
            material = dielectric_shape['material']

            material_string = self.dielectric_label_fn(dielectric, material)

            painter.setPen(penBlack)
            painter.setBrush(color)
            painter.drawRect(xmin, flipy(y), w, -h)
            drawText_left(xmin + 5, flipy(y), w, h, dielectric.name)
            drawText_right(xmin, flipy(y), w - 5, h, material_string)

            if not dielectric.zmax in stored_z:
                stored_z = np.append(stored_z, dielectric.zmax)
                stored_y = np.append(stored_y, y + h)

            # get metals inside this dielectric
            metals_inside = dielectric.get_planar_metals_inside()
            # height for one dielectric segment including one metal is part_height
            if len(metals_inside) > 0:

                # there could be multiple metals starting at the same zmin

                # draw planar metals, one after another
                ymetal = y
                for n, metal in enumerate(metals_inside):

                    painter.setPen(penBlack)

                    # check if metal is aligned with dielectric zmin
                    elevation = metal.zmin - dielectric.zmin
                    if n == 0 and (abs(elevation) > 0.001):
                        # draw some vertical offset, not aligned with dielectric
                        ymetal = ymetal + part_height * 0.5  # slight offset

                    # check if next metal is at same zmin
                    next_at_same_zmin = False
                    previous_at_same_zmin = False
                    xmetal = xmin + 120
                    wmetal = w - 200

                    if n < len(metals_inside) - 1:
                        next_metal = metals_inside[n + 1]
                        if abs(next_metal.zmin - metal.zmin) < 0.001:
                            next_at_same_zmin = True
                            xmetal = xmin + 120
                            wmetal = int(w / 2) - 100
                    else:
                        next_metal = None

                    # for the "distance to metal above" label below: several metals
                    # can share this zmin (e.g. sheet resistors drawn side by side),
                    # so skip past all of them to the first one that's actually at a
                    # different (higher) zmin - next_metal above is only the very next
                    # list entry, which for a same-zmin sibling would wrongly give 0
                    next_metal_above = None
                    for candidate in metals_inside[n + 1:]:
                        if abs(candidate.zmin - metal.zmin) >= 0.001:
                            next_metal_above = candidate
                            break

                    if n > 0:
                        previous_metal = metals_inside[n - 1]
                        if abs(previous_metal.zmin - metal.zmin) < 0.001:
                            xmetal = xmin + int(w / 2) + 20
                            wmetal = int(w / 2) - 100
                            previous_at_same_zmin = True

                    material = self.materials_list.get_by_name(metal.material)
                    if material is not None:
                        if metal.is_sheet:
                            # sheet metal that is simulated with zero extrusion
                            height = 3
                            label_string = self.metal_label_fn(metal, material, True)
                        else:
                            # regular extruded metal
                            height = part_height / 2
                            label_string = self.metal_label_fn(metal, material, False)

                        # the box for this metal
                        if material.type.upper() == "CONDUCTOR":
                            painter.setBrush(QColor(230, 230, 230, 90))
                            painter.drawRect(xmetal, flipy(ymetal), wmetal, -int(height))
                        else:
                            painter.setBrush(QColor(230, 130, 130, 90))
                            painter.drawRect(xmetal, flipy(ymetal), wmetal, -int(height))
                    else:
                        # material assignment is invalid
                        height = part_height / 2
                        painter.setBrush(QColor(255, 0, 0, 80))
                        painter.drawRect(xmetal, flipy(ymetal), wmetal, -int(height))
                        label_string = 'INVALID MATERIAL REFERENCE: ' + metal.material

                    painter.setPen(penBlack)
                    drawText_left(xmetal + 10, flipy(ymetal), wmetal, part_height / 2, f"{metal.name} ({metal.layernum})")
                    painter.setPen(penGray)
                    drawText_right(xmetal, flipy(ymetal), wmetal - 10, part_height / 2, label_string)
                    # store the drawing position, because vias will refer to that
                    if not metal.zmin in stored_z:
                        stored_z = np.append(stored_z, metal.zmin)
                        stored_y = np.append(stored_y, ymetal)
                    if not metal.zmax in stored_z:
                        stored_z = np.append(stored_z, metal.zmax)
                        stored_y = np.append(stored_y, ymetal + height)

                    painter.setPen(penGray)
                    painter.drawLine(xmetal - 60, flipy(ymetal), xmetal - 10, flipy(ymetal))
                    # draw line at top side of metal
                    if not metal.is_sheet:
                        painter.drawLine(xmetal - 60, flipy(ymetal + height), xmetal - 10, flipy(ymetal + height))
                        heightstring = f'{metal.thickness:.3f}µm'
                        painter.setPen(penDarkGray)
                        drawText_left(xmetal - 60, flipy(ymetal), 50, height, heightstring)

                    if not previous_at_same_zmin:
                        # draw height to metal above
                        if next_metal_above is not None:
                            dz = abs(next_metal_above.zmin - metal.zmax)
                            heightstring = f'{dz:.3f}µm'
                            painter.setPen(penGray)
                            # sheet metals draw at height=3px, too short to fit this
                            # label without vertical clipping - give the text its own
                            # minimum box height, independent of the drawn box height
                            text_height = max(height, 14)
                            drawText_left(xmetal - 60, flipy(ymetal + height), 50, text_height, heightstring)

                    if n == len(metals_inside) - 1:
                        # last metal (top metal)
                        # place text for distance to dielectric boundary

                        painter.setPen(penBlack)
                        # a metal is registered "inside" a dielectric by its zmin alone
                        # (see util_stackup_reader.register_metals_inside()) - its zmax
                        # can legitimately extend past that dielectric's own zmax into
                        # the one(s) above (e.g. a thick metal sitting in a very thin
                        # dielectric slab), which would otherwise show as a negative,
                        # confusingly-worded "distance to the boundary above". Floor at
                        # 0 - the metal is still drawn at its correct position/height,
                        # this only affects this one label.
                        dz = max(0.0, dielectric.zmax - metal.zmax)
                        if dz > 10:
                            heightstring = f'{dz:.1f}µm'
                        else:
                            heightstring = f'{dz:.3f}µm'
                        painter.setPen(penGray)
                        painter.drawText(xmetal - 60, flipy(ymetal + height + 5), heightstring)

                    if n == 0 and elevation > 0.001:
                        # metal not aligned with bottom of dielectric, add a label for offset value
                        heightstring = f'{elevation:.3f}µm'
                        painter.setPen(penGray)
                        painter.drawText(xmetal - 60, flipy(ymetal - 10), heightstring)

                    if not next_at_same_zmin:
                        # increase screen y for next metal
                        ymetal = ymetal + part_height

            y = y + h

        # sort stored positions
        if len(stored_z) > 2:
            idx = np.argsort(stored_z)
            y_sorted = stored_y[idx]
            z_sorted = stored_z[idx]
            # linear, not cubic: the z->y mapping is a layout position (screen height
            # per dielectric is set by how many metals are stacked inside it, not by
            # its physical thickness), so slope can change drastically between
            # consecutive stored points - e.g. a thick, metal-free substrate maps to
            # almost no screen height while a thin, via-packed dielectric maps to a
            # lot. A cubic spline through data like that readily overshoots (Runge's
            # phenomenon), and with fill_value='extrapolate' that overshoot is
            # unbounded - enough to overflow the int coordinates drawRect() needs
            # below. Linear interpolation/extrapolation is bounded by construction.
            z_to_y = interp1d(z_sorted, y_sorted, kind='linear', fill_value='extrapolate')

            # next we draw the vias, based on the screen position of metals that we have stored
            # via position alternates between 3 positions along x axis
            pos = 1
            w = (xmax - xmin) / 10

            painter.setBrush(QColor(136, 192, 200, 80))
            for metal in self.metals_list.metals:
                if metal.is_via or metal.is_dielectric:

                    material = self.materials_list.get_by_name(metal.material)
                    label_suffix = self.via_label_suffix_fn(metal, material)

                    y1 = z_to_y(metal.zmin)
                    y2 = z_to_y(metal.zmax)
                    h = abs(y2 - y1)

                    if pos == 1:
                        xvia = (xmax + xmin) / 2 - 4 * w / 2
                        pos = 2
                    elif pos == 2:
                        xvia = (xmax + xmin) / 2 - w / 2
                        pos = 3
                    else:
                        xvia = (xmax + xmin) / 2 + w
                        pos = 1

                    painter.setPen(penBlack)
                    painter.drawRect(xvia, flipy(y1), w, -h)
                    painter.drawText(xvia + 5, flipy(y1 + 5), f"{metal.name} ({metal.layernum})" + label_suffix)

        painter.end()


class PopUpWindow(QDialog):
    """This window shows the substrate stackup preview.

    Uses MainWindow.stackup_dielectric_color / stackup_dielectric_label /
    stackup_metal_label / stackup_via_label_suffix hooks so the same window
    class works for both the EM (permittivity/Rs) and thermal (thermal
    conductivity) apps.
    """

    def __init__(self, MainWindow):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Stackup Preview")
        self.resize(700, 800)
        self.MainWindow = MainWindow

        layout = QVBoxLayout()

        # Add the custom painting widget
        self.vector_widget = VectorWidget(self.MainWindow.materials_list,
                                          self.MainWindow.dielectrics_list,
                                          self.MainWindow.metals_list,
                                          dielectric_color_fn=self.MainWindow.stackup_dielectric_color,
                                          dielectric_label_fn=self.MainWindow.stackup_dielectric_label,
                                          metal_label_fn=self.MainWindow.stackup_metal_label,
                                          via_label_suffix_fn=self.MainWindow.stackup_via_label_suffix)
        layout.addWidget(self.vector_widget)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.setLayout(layout)
        self.setModal(True)


# ---------- CREATE MODEL TAB (shared base) ----------

class CreateModelTabBase(QWidget):
    """Shared base for the "Create Model" tab.

    Target dir/model name fields, preview/create-mesh buttons, the log
    panel and the QProcess wiring are identical between setupEM and
    setupThermal. What differs per app:
      - create_model(): the "is the model complete enough to build" check
        (simulation ports vs. thermal source+boundary) and its warning text
      - run_model(): how the solver is actually launched (Palace via WSL,
        Elmer via a Windows .bat rename, or plain ElmerSolver for thermal)
    Both are left undefined here and implemented in each app's subclass.
    """

    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow  # parent = MainWindow

        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignTop)

        # File group

        self.file_group = QGroupBox("Output files for simulation model")
        self.file_layout = QVBoxLayout()

        self.targetdir_layout = QHBoxLayout()
        self.label2 = QLabel("Target directory:")
        self.label2.setFixedWidth(120)
        self.targetdir_layout.addWidget(self.label2)
        self.targetdir_edit = QLineEdit("")
        self.targetdir_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.targetdir_layout.addWidget(self.targetdir_edit)
        self.targetdir_btn = QPushButton("Browse ...")
        self.targetdir_btn.setFixedWidth(150)  # narrower
        self.targetdir_btn.clicked.connect(self.browse_directory)
        self.targetdir_layout.addWidget(self.targetdir_btn)
        self.file_layout.addLayout(self.targetdir_layout)

        self.modelname_layout = QHBoxLayout()
        self.label1 = QLabel("Model name:")
        self.label1.setFixedWidth(120)
        self.modelname_layout.addWidget(self.label1)
        self.modelname_edit = QLineEdit("")
        self.modelname_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        # install event filter, so we capture when edit looses focus
        self.modelname_edit.editingFinished.connect(self.on_modelname_edit_done)
        self.modelname_layout.addWidget(self.modelname_edit)
        self.file_layout.addLayout(self.modelname_layout)

        self.file_group.setLayout(self.file_layout)

        # Actions group - kept visually separate (its own framed group) from the
        # input fields above. Preview/Create Mesh/Start Simulation/Terminate (plus,
        # in setupEM's subclass, View Results/Model Fit) all share this grid so
        # their right edges line up at exactly the same two-thirds/one-third split
        # - a QGridLayout keeps columns aligned across rows; independent
        # QHBoxLayouts can't guarantee that once some rows have two widgets (e.g.
        # Start Simulation/Terminate) and others have one (Preview/Create Mesh).
        self.actions_group = QGroupBox("Actions")
        self.actions_layout = QVBoxLayout()
        self.buttons_grid = QGridLayout()
        self.buttons_grid.setColumnStretch(0, 2)  # primary column: two thirds
        self.buttons_grid.setColumnStretch(1, 1)  # secondary column: one third

        self.preview_model_btn = QPushButton("⚙️ Preview model geometry in gmsh")
        self.preview_model_btn.clicked.connect(self.preview_model)
        self.buttons_grid.addWidget(self.preview_model_btn, 0, 0)

        self.create_model_btn = QPushButton("⚙️ Create mesh and simulation settings file")
        self.create_model_btn.clicked.connect(self.create_mesh)
        self.buttons_grid.addWidget(self.create_model_btn, 1, 0)

        self.create_run_btn = QPushButton("▶️ Start Simulation")
        self.create_run_btn.clicked.connect(self.run_model)
        self.buttons_grid.addWidget(self.create_run_btn, 2, 0)
        self.kill_btn = QPushButton("🛑 Terminate ")
        self.kill_btn.clicked.connect(self.terminate_run)
        self.buttons_grid.addWidget(self.kill_btn, 2, 1)

        self.actions_layout.addLayout(self.buttons_grid)

        # Log file: kept inside the Actions frame (not its own group box) since it is
        # the direct output of the actions above (Preview/Create Mesh/Start Simulation),
        # not an independent input section.
        self.actions_layout.addSpacing(10)
        self.actions_layout.addWidget(QLabel("Log file:"))
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        log_font = QFont()
        # Consolas/Cascadia Mono: Windows. Ubuntu Mono: default on Ubuntu (this app's primary
        # Linux target, see the Ubuntu 24.04 notice below) and narrower than DejaVu Sans Mono.
        # Liberation Mono/DejaVu Sans Mono: broader Linux fallbacks. "monospace": generic
        # fontconfig alias, guaranteed to resolve to an installed monospace font on Linux.
        log_font.setFamilies(["Consolas", "Cascadia Mono", "Ubuntu Mono", "Liberation Mono", "DejaVu Sans Mono", "monospace"])
        log_font.setStyleHint(QFont.Monospace)
        log_font.setFixedPitch(True)
        log_font.setPointSize(9)
        self.log_area.setFont(log_font)
        self.actions_layout.addWidget(self.log_area)

        self.actions_group.setLayout(self.actions_layout)

        self.main_layout.addWidget(self.file_group)
        self.main_layout.addSpacing(20)
        self.main_layout.addWidget(self.actions_group)
        self.setLayout(self.main_layout)

        # --- QProcess setup ---
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.on_stdout)
        self.process.readyReadStandardError.connect(self.on_stderr)
        self.process.finished.connect(self.on_finished)

    def on_modelname_edit_done(self):
        # Model name edit field has changed
        self.MainWindow.saved_values['model_basename'] = self.modelname_edit.text()

    def on_stdout(self):
        data = self.process.readAllStandardOutput().data().decode()
        for line in data.splitlines():
            if line.strip():  # Skip empty lines
                self.log_area.appendPlainText(line)

    def on_stderr(self):
        data = self.process.readAllStandardError().data().decode()
        for line in data.splitlines():
            if line.strip():  # Skip empty lines
                self.log_area.appendPlainText(f"[Error] {line}")

    def on_finished(self, exit_code, exit_status):
        """Handle process completion."""
        self.log_area.appendPlainText(f"\n--- Process finished with exit code {exit_code} ---\n")

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Target Directory",
            "",  # Starting directory ("" = current)
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if directory:
            self.targetdir_edit.setText(str(directory))
            self.MainWindow.saved_values['sim_path'] = str(directory)

    def save_values(self):
        saved_values = self.MainWindow.saved_values
        saved_values['model_basename'] = self.modelname_edit.text()
        saved_values['sim_path'] = self.targetdir_edit.text().replace('\\', '/')

        return True  # Tab change only possible when returning True

    def load_values(self):
        saved_values = self.MainWindow.saved_values
        # set target dir to GDSII directory by default
        gdsfile = saved_values.get("GdsFile", "")

        model_basename = saved_values.get('model_basename', '')
        if model_basename == "":
            if gdsfile != "":
                model_basename = os.path.basename(gdsfile).replace('.gds', '')
                if "===" in model_basename:
                    model_basename = ""

        # no simulator name prefix
        model_basename = model_basename.replace('palace_', '')
        model_basename = model_basename.replace('elmer_', '')

        self.modelname_edit.setText(model_basename)

        sim_path = saved_values.get('sim_path', '')
        if sim_path == "":
            if gdsfile != "":
                gds_dir = os.path.normcase(os.path.dirname(gdsfile))
            else:
                gds_dir = os.getcwd()
            if os.path.isdir(gds_dir):
                self.targetdir_edit.setText(gds_dir)
            else:
                self.targetdir_edit.setText("")
        else:
            if os.path.exists(sim_path):
                self.targetdir_edit.setText(sim_path)

    def preview_model(self):
        # create model and run gmsh, but skip the final mesh and output file creation
        saved_values = self.MainWindow.saved_values

        # check if filenames are valid, maybe they are from different machine
        gdsfile = saved_values.get("GdsFile")
        XMLfile = saved_values.get("SubstrateFile")
        if os.path.isfile(gdsfile):
            if os.path.isfile(XMLfile):
                saved_values['preview_only'] = True
                saved_values['no_preview'] = False
                self.create_model()
                del saved_values['preview_only']
                del saved_values['no_preview']
            else:
                self.log_area.appendPlainText("⚠️ Cannot load XML stackup file!\n" + saved_values.get("SubstrateFile") + "\n")
        else:
            self.log_area.appendPlainText("⚠️ Cannot load GDSII layout stackup file!\n" + saved_values.get("GdsFile") + "\n")

    def create_mesh(self):
        # create model and run gmsh, but skip the final mesh and output file creation
        saved_values = self.MainWindow.saved_values

        # check if filenames are valid, maybe they are from different machine
        gdsfile = saved_values.get("GdsFile")
        XMLfile = saved_values.get("SubstrateFile")
        if os.path.isfile(gdsfile):
            if os.path.isfile(XMLfile):
                saved_values['preview_only'] = False
                saved_values['no_preview'] = True
                self.create_model()
                del saved_values['preview_only']
                del saved_values['no_preview']
            else:
                self.log_area.appendPlainText("⚠️ Cannot load XML stackup file!\n" + saved_values.get("SubstrateFile") + "\n")
        else:
            self.log_area.appendPlainText("⚠️ Cannot load GDSII layout stackup file!\n" + saved_values.get("GdsFile") + "\n")

    def terminate_run(self):
        if self.process.state() == QProcess.Running:
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self.process.kill()

    # create_model() and run_model() are app-specific and implemented in
    # each app's CreateModelTab subclass (see setupEM.py / setupThermal.py)


# ---------- MAIN WINDOW (shared base) ----------

class MainWindowBase(QMainWindow):
    """Shared base for the setupEM and setupThermal MainWindow classes.

    Holds the menu bar skeleton, native config (*.simcfg / *.tsimcfg) JSON
    load/save, tab-change validation gating, the *.py model import/export
    machinery, and the PyPI version check. App-specific bits (which tabs
    exist, the Simulator menu in setupEM, ports vs. thermal objects data)
    are provided by the subclass via plain attributes/overrides or via the
    small hook methods below.
    """

    TAB_HEADER_COLORS = ["#FFCDD2", "#C8E6C9", "#BBDEFB", "#FFF9C4", "#D1C4E9"]

    def __init__(self):
        super().__init__()
        # let the whole window accept a dropped *.simcfg / *.tsimcfg file,
        # not just the individual file-path fields (see FileDropLineEdit)
        self.setAcceptDrops(True)

    # ---------- Drag & drop native config file onto the window ----------
    def _config_file_from_drop(self, event):
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and pathlib.Path(path).suffix.upper() == "." + self.CONFIG_SUFFIX.upper():
                return path
        return None

    def dragEnterEvent(self, event):
        if self._config_file_from_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._config_file_from_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        file_path = self._config_file_from_drop(event)
        if file_path:
            event.acceptProposedAction()
            self.load_configuration_from_file(file_path)
        else:
            event.ignore()

    # ---------- Menu Bar ----------
    def create_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")

        # browse_action = QAction("Browse Settings File...", self)
        self.load_settings_action = QAction("Load Settings ...", self)
        self.save_action = QAction("Save Settings ...", self)
        self.load_default_action = QAction("Load Default Settings", self)
        self.savedefault_action = QAction("Save as Default Settings", self)
        self.import_model_action = QAction("Import from *.py model ...", self)
        self.export_model_action = QAction("Export to *.py model ...", self)
        exit_action = QAction("Exit", self)

        # disable export by default, only enable when on Code tab
        self.export_model_action.setEnabled(False)

        self.load_settings_action.triggered.connect(lambda: self.load_configuration_dialog())
        self.load_default_action.triggered.connect(lambda: self.load_configuration_from_file(self.DEFAULT_SETTINGS_FILE))
        self.save_action.triggered.connect(lambda: self.save_ask_filenamefile())
        self.savedefault_action.triggered.connect(lambda: self.save_user_inputs_to_file(self.DEFAULT_SETTINGS_FILE))

        self.import_model_action.triggered.connect(lambda: self.import_from_python())
        self.export_model_action.triggered.connect(lambda: self.export_to_python())
        exit_action.triggered.connect(self.close)

        file_menu.addAction(self.load_settings_action)
        self.recent_settings_menu = file_menu.addMenu("Load Recent Settings")
        file_menu.addAction(self.save_action)
        file_menu.addSeparator()
        file_menu.addAction(self.import_model_action)
        self.recent_model_menu = file_menu.addMenu("Import Recent Model")
        file_menu.addAction(self.export_model_action)
        file_menu.addSeparator()
        file_menu.addAction(self.load_default_action)
        file_menu.addAction(self.savedefault_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        self._populate_recent_menus()

        # hook for app-specific menus (e.g. setupEM's Simulator menu); no-op by default.
        # Placed before Tools so the menu order reads File, Simulator, Tools, Help.
        self.create_additional_menus(menu_bar)

        # Tools menu: shared between setupEM and setupThermal since the stackup XML
        # format (and its Materials list) is common to both, not app-specific
        tools_menu = menu_bar.addMenu("&Tools")
        self.edit_stackup_action = QAction("Edit Stackup XML...", self)
        self.edit_stackup_action.triggered.connect(lambda: self.open_stackup_editor())
        if not GDS2PALACE_SUPPORTS_STACKUP_EDITOR:
            self.edit_stackup_action.setEnabled(False)
            self.edit_stackup_action.setToolTip(
                "Requires a newer gds2palace than is currently installed.\n"
                "Update with: pip install gds2palace --upgrade")
        tools_menu.addAction(self.edit_stackup_action)

        # one-time, non-blocking heads-up if gds2palace is too old for some features -
        # deferred so it appears after the window itself, not stalling startup
        if GDS2PALACE_OUTDATED:
            QTimer.singleShot(0, self._warn_if_gds2palace_outdated)

        help_menu = menu_bar.addMenu("&Help")
        self.web_manual1_action = QAction("Documentation gds2palace", self)
        self.web_gds2palace_action = QAction("github gds2palace", self)
        self.web_manual2_action = QAction("github setupEM", self)
        self.web_examples_action = QAction("Examples", self)
        self.version_action = QAction("Version information...", self)
        self.web_gds2palace_action.triggered.connect(lambda: webbrowser.open("https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2"))
        self.web_manual1_action.triggered.connect(lambda: webbrowser.open("https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/doc/gds2palace_workflow_userguide.pdf"))
        self.web_manual2_action.triggered.connect(lambda: webbrowser.open("https://github.com/VolkerMuehlhaus/setupEM"))
        self.web_examples_action.triggered.connect(lambda: webbrowser.open("https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/tree/main/workflow"))
        self.version_action.triggered.connect(lambda: self.show_version())
        help_menu.addAction(self.web_manual1_action)
        help_menu.addAction(self.web_gds2palace_action)
        help_menu.addAction(self.web_manual2_action)
        help_menu.addAction(self.web_examples_action)
        help_menu.addSeparator()
        help_menu.addAction(self.version_action)

    def create_additional_menus(self, menu_bar):
        # Hook for app-specific menus inserted between File and Help menus.
        # No-op by default; setupEM overrides this to add the Simulator menu.
        pass

    def _warn_if_gds2palace_outdated(self):
        missing = []
        if not GDS2PALACE_SUPPORTS_STACKUP_EDITOR:
            missing.append("- Editing stackup XML files (Tools > Edit Stackup XML...)")
        if not GDS2PALACE_SUPPORTS_FILE_DESCRIPTION:
            missing.append("- Showing a stackup file's description on the Input Files tab")
        QMessageBox.warning(
            self, "Outdated gds2palace",
            "The installed gds2palace is older than what this version of "
            + getattr(self, "APP_NAME", "this application") + " expects, so these "
            "features are disabled for now:\n\n" + "\n".join(missing)
            + "\n\nEverything else works as usual. To enable these features, update with:\n"
              "  pip install gds2palace --upgrade")

    # ---------- Version check (PyPI) ----------
    def get_latest_version(self, package_name: str) -> str:
        # Network call on the GUI thread: bounded with a short timeout and
        # wrapped in try/except so a network failure or hang can't crash or
        # freeze the app. On failure we just skip the check silently.
        url = f"https://pypi.org/pypi/{package_name}/json"
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return response.json()["info"]["version"]
        except Exception:
            return "unknown"

    # ---------- Tab header coloring ----------
    def apply_tab_header_colors(self):
        style = "QTabBar::tab { color: black; font-weight: bold; padding: 10px; }\n"
        for i, color in enumerate(self.TAB_HEADER_COLORS, start=1):
            style += f"QTabBar::tab:nth-child({i}) {{ background: {color}; }}\n"
        self.tabs_widget.setStyleSheet(style)

    # ---------- Tab change handling ----------
    def on_tab_change(self, index):
        # check if we are ready to leave the tab, i.e. all values are valid
        previous_widget = self.tabs_widget.widget(self._previous_index)
        if hasattr(previous_widget, "save_values"):
            if not previous_widget.save_values():
                self.tabs_widget.blockSignals(True)
                self.tabs_widget.setCurrentIndex(self._previous_index)
                self.tabs_widget.blockSignals(False)
                return
        self._previous_index = index

        # check if we switch to the Model editor tab, in that case store all other tabs
        # and regenerate the code preview from their current values (tab count/order
        # differs between apps, e.g. setupThermal has no Frequencies tab, so look up the
        # Code tab's index instead of hardcoding it). This overwrites any manual edits
        # made directly in the Code tab's text box - deliberate, so the preview always
        # reflects the other tabs' current settings (e.g. Input Files' Variable
        # overrides) the moment you switch to Code, not just after Preview/Create Mesh/
        # Start Simulation/Export.
        modeleditor_index = self.tabs_widget.indexOf(self.modeleditor_tab)
        if index == modeleditor_index:
            self.save_all_tabs()
            self.modeleditor_tab.create_model_text()

        # Save model code only when model tab active
        self.export_model_action.setEnabled(index == modeleditor_index)

    # ---------- Tab load/save orchestration ----------
    def load_all_tabs(self):
        # load saved_values into every tab that supports it, generic over
        # however many tabs this app's MainWindow added (apps have different tabs).
        # Skip the Model editor tab: its load_values()/save_values() regenerate the
        # code preview by calling save_all_tabs() on the OTHER tabs, so including it
        # here would recurse into itself.
        for i in range(self.tabs_widget.count()):
            widget = self.tabs_widget.widget(i)
            if widget is self.modeleditor_tab:
                continue
            if hasattr(widget, "load_values"):
                widget.load_values()
        if hasattr(self.modeleditor_tab, "load_values"):
            self.modeleditor_tab.load_values()

    def save_all_tabs(self):
        # save every tab's current values into saved_values; returns False if
        # any tab reported invalid input (mirrors the single-tab save_values() contract).
        # Skip the Model editor tab here too, for the same recursion reason.
        all_ok = True
        for i in range(self.tabs_widget.count()):
            widget = self.tabs_widget.widget(i)
            if widget is self.modeleditor_tab:
                continue
            if hasattr(widget, "save_values"):
                if not widget.save_values():
                    all_ok = False
        return all_ok

    # ---------- User input persistence ----------

    def load_user_inputs(self, filename):
        # load of native configuration file
        if os.path.exists(filename):
            try:
                with open(filename, "r") as f:
                    return json.load(f)
            except Exception:
                QMessageBox.warning(self, "Error", f"Failed to load settings from {filename}")
                return {}
        return {}

    def load_configuration_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Settings File", filter=f"*.{self.CONFIG_SUFFIX};;Python model code *.py")
        # we can load JSON or Python models, decide which suffix we have
        if file_path:
            self.load_configuration_from_file(file_path)

    def load_configuration_from_file(self, file_path):
        saved_values = self.saved_values
        if file_path:
            extension = pathlib.Path(file_path).suffix
            if self.CONFIG_SUFFIX.upper() in extension.upper():
                # regular data storage
                self.user_inputs_file = file_path
                # call the native config file loading function
                data = self.load_user_inputs(file_path)
                if data.get("application", "") == self.APP_NAME:
                    # update internal data structure
                    saved_values.clear()
                    saved_values.update(data.get("saved_values"))
                    # update ports/thermal objects, separate from the other internal data
                    self.apply_native_config_data(data)
                    self.load_all_tabs()
                    self._add_recent_file(RECENT_SETTINGS_KEY, file_path)
                    QMessageBox.information(self, "Loaded", f"Settings loaded from {file_path}")
                    self.create_model_tab.log_area.clear()
                else:
                    QMessageBox.information(self, "Failed", "Unknown data format")
            elif extension.upper() == ".PY":
                import_mapping = {
                    "gds_filename": "GdsFile",
                    "XML_filename": "SubstrateFile",
                    "GdsFile": "GdsFile",
                    "purpose": "purpose",
                    "cellname": "cellname",
                    "variable_overrides": "variable_overrides",
                    "SubstrateFile": "SubstrateFile",
                    "merge_polygon_size": "merge_polygon_size",
                    "preprocess_gds": "preprocess_gds",
                    "margin": "margin",
                    "air_around": "air_around",
                    "boundary": "boundary",
                    "fstart": "fstart",
                    "fstop": "fstop",
                    "fstep": "fstep",
                    "fpoint": "fpoint",
                    "fdump": "fdump",
                    "refined_cellsize": "refined_cellsize",
                    "refined_cellsize_override": "refined_cellsize_override",
                    "cells_per_wavelength": "cells_per_wavelength",
                    "meshsize_max": "meshsize_max",
                    "adaptive_mesh_iterations": "adaptive_mesh_iterations",
                    "order": "order",
                    "iterative": "iterative",
                    "ELMER_MPI_THREADS": "ELMER_MPI_THREADS"
                }

                # remove old settings, so that we don't keep old values that don't exist in loaded file
                saved_values.clear()
                # set values that are not included in import
                saved_values["unit"] = 1e-6
                saved_values["purpose"] = 0

                # check what directory the Python code is in, we might use that to prefix gdsfile and XML file
                modelcode_path = os.path.dirname(file_path)

                # variable assignments
                imported_parameters = parse_assignments(file_path)
                for import_key, import_value in imported_parameters.items():
                        if import_key in import_mapping.keys():
                            if import_key not in import_value:  # skip the section where key might appear in different context
                                # get the internal name for this variable
                                varname = import_mapping.get(import_key, '')
                                if varname in ("fpoint", "fdump", "variable_overrides", "refined_cellsize_override"):
                                    saved_values[varname] = ast.literal_eval(import_value)
                                elif varname in ["gds_filename", "XML_filename", "GdsFile", "SubstrateFile"]:
                                    # check if we have full path for files in imported Python script,
                                    # otherwise prefix from *.py path assuming that it was local to the *.py model script
                                    value_path = os.path.dirname(import_value)
                                    if value_path == '':
                                        import_value = os.path.join(modelcode_path, import_value)
                                    saved_values[varname] = import_value
                                elif varname != '':
                                    raw = import_value.strip("[]")
                                    if varname in ['fstart', 'fstop', 'fstep']:
                                        saved_values[varname] = float(raw) / 1e9
                                    elif varname == 'ELMER_MPI_THREADS':
                                        # MeshTab.load_values() does numeric comparisons
                                        # on this value directly, so it must be an int,
                                        # not the raw string parsed from the .py file
                                        saved_values[varname] = int(raw)
                                    else:
                                        saved_values[varname] = raw

                # ask whether future "Create Model" output should overwrite this same
                # file, or start a fresh model (today's GDS-derived default)
                reuse = QMessageBox.question(
                    self, "Import Model",
                    f"Use '{os.path.basename(file_path)}' as the output file for this model too?\n\n"
                    "Yes: Create Model / Start Simulation will overwrite this file.\n"
                    "No: pick a model name and target directory on the Create Model(s) tab.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                ) == QMessageBox.Yes
                if reuse:
                    saved_values['sim_path'] = os.path.dirname(file_path).replace('\\', '/')
                    saved_values['model_basename'] = pathlib.Path(file_path).stem

                # read port/thermal assignments in workflow syntax for gds2palace Python code, and
                # apply any app-specific post-import state (e.g. setupEM's simulator mode)
                self.apply_python_import_data(file_path)

                self.load_all_tabs()
                self._add_recent_file(RECENT_MODEL_KEY, file_path)
                QMessageBox.information(self, "Loaded", f"Settings loaded from {file_path}")
                self.create_model_tab.log_area.clear()

            else:
                QMessageBox.information(self, "Error", f"Could not load file {file_path}")

    # ---------- recent files (Load Settings / Import Model) ----------

    def _recent_files(self, key):
        files = QSettings(RECENT_FILES_ORG, self.APP_NAME).value(key, [])
        # QSettings collapses a saved one-item list back to a bare string on read -
        # a well-known quirk of the native (registry/plist) backends
        if isinstance(files, str):
            files = [files] if files else []
        return list(files)

    def _add_recent_file(self, key, filename):
        filename = os.path.abspath(filename)
        files = [f for f in self._recent_files(key) if os.path.normcase(f) != os.path.normcase(filename)]
        files.insert(0, filename)
        QSettings(RECENT_FILES_ORG, self.APP_NAME).setValue(key, files[:MAX_RECENT_FILES])
        self._populate_recent_menus()

    def _remove_recent_file(self, key, filename):
        filename = os.path.abspath(filename)
        files = [f for f in self._recent_files(key) if os.path.normcase(f) != os.path.normcase(filename)]
        QSettings(RECENT_FILES_ORG, self.APP_NAME).setValue(key, files)
        self._populate_recent_menus()

    def _clear_recent_files(self, key):
        QSettings(RECENT_FILES_ORG, self.APP_NAME).setValue(key, [])
        self._populate_recent_menus()

    def _populate_recent_menus(self):
        self._populate_recent_menu(self.recent_settings_menu, RECENT_SETTINGS_KEY)
        self._populate_recent_menu(self.recent_model_menu, RECENT_MODEL_KEY)

    def _populate_recent_menu(self, menu, key):
        menu.clear()
        files = self._recent_files(key)
        if not files:
            empty_action = QAction("(none)", self)
            empty_action.setEnabled(False)
            menu.addAction(empty_action)
            return
        for filename in files:
            action = QAction(filename, self)
            action.triggered.connect(lambda checked=False, f=filename: self._open_recent_file(key, f))
            menu.addAction(action)
        menu.addSeparator()
        clear_action = QAction("Clear Recent Files", self)
        clear_action.triggered.connect(lambda: self._clear_recent_files(key))
        menu.addAction(clear_action)

    def _open_recent_file(self, key, filename):
        if not os.path.isfile(filename):
            QMessageBox.warning(
                self, "File not found",
                f"Could not find {filename}.\n\nIt will be removed from the recent files list.")
            self._remove_recent_file(key, filename)
            return
        self.load_configuration_from_file(filename)

    def apply_native_config_data(self, data):
        # Hook: update the app-specific tab (ports / thermal objects) from
        # native *.simcfg / *.tsimcfg JSON data. Implemented in subclass.
        raise NotImplementedError

    def apply_python_import_data(self, file_path):
        # Hook: parse app-specific definitions (ports / thermal objects) out
        # of an imported *.py model and apply any other app-specific state
        # (e.g. setupEM's Palace/Elmer mode). Implemented in subclass.
        raise NotImplementedError

    def import_from_python(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Settings File", filter=f"*.py model code")
        # we can load JSON or Python models, decide which suffix we have
        if file_path:
            self.load_configuration_from_file(file_path)
        else:
            QMessageBox.information(self, "Error", f"Could not load file {file_path}")

    def save_user_inputs_to_file(self, filename):
        # make sure all tabs save their values
        self.save_all_tabs()

        try:
            struct = {"application": self.APP_NAME,
                        "data_format": "1.0"}
            struct["saved_values"] = self.saved_values
            struct.update(self.native_config_extra_struct())

            with open(filename, "w") as f:
                json.dump(struct, f, indent=4)
            self._add_recent_file(RECENT_SETTINGS_KEY, filename)
            QMessageBox.information(self, "Saved", f"Settings saved to {filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save settings to {filename}: {e}")

    def native_config_extra_struct(self):
        # Hook: extra top-level keys to merge into the saved *.simcfg /
        # *.tsimcfg struct (ports for setupEM, thermal objects for
        # setupThermal). Implemented in subclass.
        raise NotImplementedError

    def save_ask_filenamefile(self):
        # make sure all tabs save their values
        # set gds filename as default for saving config
        gds_name = self.saved_values.get("GdsFile")
        default_config = gds_name.replace('.gds', '.' + self.CONFIG_SUFFIX)
        file_path, _ = QFileDialog.getSaveFileName(self, "Select Settings File", default_config, filter=f"{self.APP_NAME} (*.{self.CONFIG_SUFFIX})")
        # Ensure filename ends with CONFIG_SUFFIX
        if file_path:
            if not file_path.lower().endswith('.' + self.CONFIG_SUFFIX):
                file_path = file_path + '.' + self.CONFIG_SUFFIX
            self.save_user_inputs_to_file(file_path)

    def export_to_python(self):
        # make sure all tabs save their values
        self.save_all_tabs()
        self.modeleditor_tab.create_model_text(forExport=True)

        file_path, _ = QFileDialog.getSaveFileName(self, "Select Python Model", filter="Python model (*.py)")
        if file_path:
            try:
                code = self.modeleditor_tab.model_edit.toPlainText()
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code)
                QMessageBox.information(self, "Saved", f"Model code saved to {file_path}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to export model code to {file_path}: {e}")

    def clear_modelname_and_targetdir(self):
        # clear model name in output settings, to avoid overwriting when changing data
        saved_values = self.saved_values
        saved_values['model_basename'] = ''
        # clear target directory if is the gds directory, but keep if other value
        gds_dir = os.path.dirname(saved_values['GdsFile'])
        target_dir = saved_values['sim_path']
        if target_dir.upper() == gds_dir.upper():
            saved_values['sim_path'] = ''

    # load technology stackup data
    def read_XML(self):
        filename = self.saved_values["SubstrateFile"]
        if pathlib.Path(filename).exists():
            captured_stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(captured_stdout):
                    materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate(
                        filename, variable_overrides=self.saved_values.get("variable_overrides"))
            except (Exception, SystemExit) as e:
                # SystemExit is caught deliberately (not just Exception): the reader
                # reports hard validation failures (circular/ambiguous Reference,
                # Offset+Reference conflict, ...) via print(...); exit(1) instead of
                # raising - see the same pattern in stackupEditor.py's _refresh_preview().
                # Capture stdout so that printed ERROR text (otherwise invisible in a
                # GUI with no attached console) can be shown to the user.
                details = captured_stdout.getvalue().strip() or str(e)
                QMessageBox.critical(self, "Error", f"Could not load stackup {filename}:\n\n{details}")
                return  # keep last-known-good materials_list/dielectrics_list/metals_list
            self.materials_list, self.dielectrics_list, self.metals_list = materials_list, dielectrics_list, metals_list
            self.update_target_layer_choices(self.metals_list)
            self.file_tab.update_XML_description(filename)
            self.file_tab.update_variable_overrides_grid(filename)

    def update_target_layer_choices(self, metals_list):
        # Hook: push the metal list to the app-specific tab that offers
        # target-layer choices (ports tab / thermal objects tab).
        raise NotImplementedError

    def open_popup(self):
        if os.path.isfile(self.saved_values["SubstrateFile"]):
            self.popup = PopUpWindow(self)
            self.popup.show()
        else:
            QMessageBox.warning(self, "Error", "Substrate file not found")

    def open_stackup_editor(self):
        # defense in depth: the menu action is already disabled/greyed out when
        # this is False, but guard the entry point itself too in case something
        # else ever calls it directly
        if not GDS2PALACE_SUPPORTS_STACKUP_EDITOR:
            QMessageBox.warning(
                self, "Outdated gds2palace",
                "Editing stackup XML files requires a newer gds2palace than is "
                "currently installed.\n\nUpdate with:\n  pip install gds2palace --upgrade")
            return

        # local import: stackupEditor.py imports from this module, so importing
        # it at module load time here would be circular.
        # __package__ is None/"" when this module was itself loaded outside the
        # setupEM package (e.g. setupEM.py run directly), so relative import fails.
        if __package__ in (None, ""):
            from stackupEditor import StackupEditorWindow
        else:
            from .stackupEditor import StackupEditorWindow

        if getattr(self, "stackup_editor_window", None) is not None:
            self.stackup_editor_window.raise_()
            self.stackup_editor_window.activateWindow()
            return

        initial_filename = self.saved_values.get("SubstrateFile") if isinstance(self.saved_values, dict) else None
        self.stackup_editor_window = StackupEditorWindow(self, initial_filename=initial_filename)
        self.stackup_editor_window.destroyed.connect(lambda: setattr(self, "stackup_editor_window", None))
        self.stackup_editor_window.show()
