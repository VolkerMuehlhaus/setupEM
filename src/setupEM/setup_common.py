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

import sys, os, json, pathlib, ast, webbrowser
import numpy as np
import requests
import gdspy
from scipy.interpolate import interp1d
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit, QComboBox,
    QPushButton, QFileDialog, QMessageBox, QGroupBox,
    QCheckBox, QPlainTextEdit, QDialog, QSizePolicy
    )
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QFont, QSyntaxHighlighter, QPainter, QPen
from PySide6.QtCore import Qt, QRegularExpression, QProcess, QRect, QTimer

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
# ------------------------------------------------------------------
GDS2PALACE_HAS_STACKUP_WRITER = hasattr(gds2palace, "stackup_writer")
GDS2PALACE_HAS_PARSE_SUBSTRATE = hasattr(stackup_reader, "parse_substrate")
GDS2PALACE_HAS_FILE_DESCRIPTION = hasattr(stackup_reader, "read_file_description")

# Tools > Edit Stackup XML... needs both the writer module and parse_substrate()
# (used for its live preview refresh); the Input Files tab's description display
# only needs the reader-side lookup.
GDS2PALACE_SUPPORTS_STACKUP_EDITOR = GDS2PALACE_HAS_STACKUP_WRITER and GDS2PALACE_HAS_PARSE_SUBSTRATE
GDS2PALACE_SUPPORTS_FILE_DESCRIPTION = GDS2PALACE_HAS_FILE_DESCRIPTION
GDS2PALACE_OUTDATED = not (GDS2PALACE_SUPPORTS_STACKUP_EDITOR and GDS2PALACE_SUPPORTS_FILE_DESCRIPTION)


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
        self.viamerge_edit = QLineEdit("0")
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
        self.show_XML_btn.clicked.connect(self.MainWindow.open_popup)
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
        self.XML_description_label = QLabel("")
        self.XML_description_label.setWordWrap(True)
        self.XML_description_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.XML_description_label.setStyleSheet("color: #666666; font-style: italic;")
        self.XML_description_layout.addWidget(self.XML_description_label)
        self.XML_description_spacer = QWidget()
        self.XML_description_spacer.setFixedWidth(self.browse_XML_btn.width())
        self.XML_description_layout.addWidget(self.XML_description_spacer)
        self.XML_description_container.setLayout(self.XML_description_layout)
        self.XML_description_container.setVisible(False)
        self.XML_layout.addWidget(self.XML_description_container)

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
        self.MainWindow.read_XML()  # safe if invalid filename
        # file is read when leaving the files tab

    def update_XML_description(self, filename):
        if not GDS2PALACE_SUPPORTS_FILE_DESCRIPTION:
            return  # older gds2palace has no read_file_description() - stay hidden
        # collapse any line breaks from the file itself - wrapping here is purely
        # width-driven (setWordWrap), not a reflow of the author's original lines
        description = " ".join(stackup_reader.read_file_description(filename).split())
        self.XML_description_label.setText(description)
        self.XML_description_container.setVisible(bool(description))

    def load_values(self):
        saved_values = self.MainWindow.saved_values
        self.gds_file_edit.setText(get_saved_value(saved_values, "GdsFile", "Please choose a file ===>"))
        XML = get_saved_value(saved_values, "SubstrateFile", "Please choose a file ===>")
        self.XML_file_edit.setText(XML)
        self.update_XML_description(XML)
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

        try:
            merge_polygon_size = float(self.viamerge_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", f"Not a valid value for via array merging")
            self.viamerge_edit.setText("0")
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
        dielectrics_bottom_up = self.dielectrics_list.dielectrics[::-1]
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
                        dz = dielectric.zmax - metal.zmax
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
            z_to_y = interp1d(z_sorted, y_sorted, kind='cubic', fill_value='extrapolate')

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
        self.modelname_edit.setFixedWidth(250)
        self.modelname_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        # install event filter, so we capture when edit looses focus
        self.modelname_edit.editingFinished.connect(self.on_modelname_edit_done)

        self.modelname_layout.addWidget(self.modelname_edit)
        self.modelname_layout.addStretch()
        self.file_layout.addLayout(self.modelname_layout)

        self.preview_model_btn = QPushButton("⚙️ Preview model geometry in gmsh")
        self.preview_model_btn.clicked.connect(self.preview_model)
        self.file_layout.addWidget(self.preview_model_btn)

        self.create_model_btn = QPushButton("⚙️ Create mesh and simulation settings file")
        self.create_model_btn.clicked.connect(self.create_mesh)
        self.file_layout.addWidget(self.create_model_btn)

        self.run_layout = QHBoxLayout()
        self.create_run_btn = QPushButton("▶️ Start Simulation")
        self.create_run_btn.clicked.connect(self.run_model)
        self.run_layout.addWidget(self.create_run_btn)
        self.kill_btn = QPushButton("🛑 Terminate ")
        self.kill_btn.clicked.connect(self.terminate_run)
        self.run_layout.addWidget(self.kill_btn)
        self.run_layout.setStretch(0, 5)  # index 0 = Run
        self.run_layout.setStretch(1, 1)  # index 1 = Terminate

        self.file_layout.addLayout(self.run_layout)

        self.file_group.setLayout(self.file_layout)

        # Log group

        self.log_group = QGroupBox("Log file")
        self.log_layout = QVBoxLayout()
        self.log_group.setLayout(self.log_layout)
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_layout.addWidget(self.log_area)

        self.main_layout.addWidget(self.file_group)
        self.main_layout.addSpacing(20)
        self.main_layout.addWidget(self.log_group)
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
        file_menu.addAction(self.save_action)
        file_menu.addSeparator()
        file_menu.addAction(self.import_model_action)
        file_menu.addAction(self.export_model_action)
        file_menu.addSeparator()
        file_menu.addAction(self.load_default_action)
        file_menu.addAction(self.savedefault_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

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
        # (tab count/order differs between apps, e.g. setupThermal has no Frequencies
        # tab, so look up the Code tab's index instead of hardcoding it)
        modeleditor_index = self.tabs_widget.indexOf(self.modeleditor_tab)
        if index == modeleditor_index:
            self.save_all_tabs()

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
                    "cells_per_wavelength": "cells_per_wavelength",
                    "meshsize_max": "meshsize_max",
                    "adaptive_mesh_iterations": "adaptive_mesh_iterations",
                    "order": "order",
                    "iterative": "iterative",
                    "elmer": "elmer",
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
                                if varname == "fpoint" or varname == "fdump":
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

                # read port/thermal assignments in workflow syntax for gds2palace Python code, and
                # apply any app-specific post-import state (e.g. setupEM's simulator mode)
                self.apply_python_import_data(file_path)

                self.load_all_tabs()
                QMessageBox.information(self, "Loaded", f"Settings loaded from {file_path}")
                self.create_model_tab.log_area.clear()

            else:
                QMessageBox.information(self, "Error", f"Could not load file {file_path}")

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
            self.materials_list, self.dielectrics_list, self.metals_list = stackup_reader.read_substrate(filename)
            self.update_target_layer_choices(self.metals_list)
            self.file_tab.update_XML_description(filename)

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

        # local import: stackup_editor.py imports from this module, so importing
        # it at module load time here would be circular.
        # __package__ is None/"" when this module was itself loaded outside the
        # setupEM package (e.g. setupEM.py run directly), so relative import fails.
        if __package__ in (None, ""):
            from stackup_editor import StackupEditorWindow
        else:
            from .stackup_editor import StackupEditorWindow

        if getattr(self, "stackup_editor_window", None) is not None:
            self.stackup_editor_window.raise_()
            self.stackup_editor_window.activateWindow()
            return

        initial_filename = self.saved_values.get("SubstrateFile") if isinstance(self.saved_values, dict) else None
        self.stackup_editor_window = StackupEditorWindow(self, initial_filename=initial_filename)
        self.stackup_editor_window.destroyed.connect(lambda: setattr(self, "stackup_editor_window", None))
        self.stackup_editor_window.show()
