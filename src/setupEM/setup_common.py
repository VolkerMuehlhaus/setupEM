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

import sys, os, json, pathlib, ast, webbrowser, io, contextlib, subprocess, shutil, glob, re
import importlib.metadata
import xml.etree.ElementTree as ET
import numpy as np
import requests
import gdspy
import shiboken6
from scipy.interpolate import interp1d
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QLineEdit, QComboBox,
    QPushButton, QFileDialog, QMessageBox, QGroupBox,
    QCheckBox, QPlainTextEdit, QDialog, QSizePolicy, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsRectItem, QToolTip,
    )
from PySide6.QtGui import (
    QAction, QColor, QTextCharFormat, QFont, QFontMetrics, QSyntaxHighlighter,
    QPainter, QPen, QBrush, QPolygonF, QTextDocument, QShortcut, QKeySequence,
    )
from PySide6.QtCore import Qt, QRegularExpression, QProcess, QRect, QRectF, QPointF, QTimer, QSettings, Signal

# we expect gds2palace in the same directory as this code, or installed as module
import gds2palace
from gds2palace import *

if __package__ in (None, ""):
    import gds_hierarchy_scan
else:
    from . import gds_hierarchy_scan

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


# QSettings scope for the File menu's "Load Recent Config"/"Import Recent Model" lists -
# per-app (organization + self.APP_NAME, i.e. "setupEM" or "setupThermal"), mirroring
# stackupEditor.py's own "Open Recent" mechanism (same org name, separate app/key there).
RECENT_FILES_ORG = "muehlhaus.com"
RECENT_SETTINGS_KEY = "recentSettingsFiles"
RECENT_MODEL_KEY = "recentModelFiles"
MAX_RECENT_FILES = 10

# Preferences (File > Preferences...): per-user, per-app defaults for fields
# that used to be plain hardcoded literals (e.g. FrequenciesTab's fstart/fstop
# QLineEdit("0")/("50")). Deliberately a separate store from the *.simcfg /
# *.tsimcfg project files and from the existing "Save as Default Config"
# mechanism (DEFAULT_SETTINGS_FILE) - this is about what a brand-new/blank
# field starts out showing, not a full saved project snapshot. Reuses the
# same QSettings org/app scope as the recent-files lists above, just under
# its own sub-group so the keys never collide.
PREFERENCES_GROUP = "preferences"


def get_preference(app_name, key, default):
    """Read a user preference, falling back to `default` (the app's own
    built-in default, e.g. "50" for fstop) if never explicitly set. `default`
    is returned as-is (same type) when unset; QSettings otherwise round-trips
    whatever type was last stored via set_preference().
    """
    settings = QSettings(RECENT_FILES_ORG, app_name)
    settings.beginGroup(PREFERENCES_GROUP)
    try:
        return settings.value(key, default)
    finally:
        settings.endGroup()


def get_preference_bool(app_name, key, default):
    """Bool-safe variant of get_preference() - some QSettings backends (e.g.
    the Windows registry) round-trip a stored bool back as the string "true"/
    "false" instead of a real bool, the same well-known quirk noted for the
    recent-files lists above.
    """
    value = get_preference(app_name, key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def set_preference(app_name, key, value):
    settings = QSettings(RECENT_FILES_ORG, app_name)
    settings.beginGroup(PREFERENCES_GROUP)
    try:
        settings.setValue(key, value)
    finally:
        settings.endGroup()


def clear_preferences(app_name):
    """Delete every stored preference for app_name (the "Reset all to
    default" button in PreferencesDialog) - after this, every get_preference()/
    get_preference_bool() call for this app falls back to its own built-in
    default again, same as a user who has never opened Preferences at all.
    """
    settings = QSettings(RECENT_FILES_ORG, app_name)
    settings.beginGroup(PREFERENCES_GROUP)
    try:
        settings.remove("")
    finally:
        settings.endGroup()


def find_paraview_exe():
    """Locate a ParaView executable: PATH first, then (Windows only) the usual
    install locations under Program Files, newest version first. Returns None
    if not found. Read-only lookup - does not launch anything - so this is safe
    to call just to check availability (e.g. for the "3D viewer" preference's
    ParaView-not-installed fallback), not just from _open_in_paraview().
    """
    paraview_exe = shutil.which("paraview")
    if paraview_exe is None and os.name == "nt":
        candidates = (
            glob.glob(r"C:\Program Files\ParaView*\bin\paraview.exe")
            + glob.glob(r"C:\Program Files (x86)\ParaView*\bin\paraview.exe")
        )
        if candidates:
            paraview_exe = max(candidates, key=os.path.getmtime)
    return paraview_exe


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
                color: black;
                border: 1px solid gray;
                border-radius: 4px;
                padding: 4px;
            }
        """

EDIT_STYLE_REQUIRED = """
            QLineEdit {
                background-color: lightyellow;
                color: black;
                border: 1px solid gray;
                border-radius: 4px;
                padding: 4px;
            }
        """

COMBO_STYLE_REQUIRED = """
    QComboBox {
        background-color: lightyellow;
        color: black;
        border: 1px solid gray;
        border-radius: 4px;
        padding: 4px;
        combobox-popup: 0;
    }
"""

COMBO_STYLE_OPTIONAL = """
    QComboBox {
        background-color: white;
        color: black;
        border: 1px solid gray;
        border-radius: 4px;
        padding: 4px;
        combobox-popup: 0;
    }
"""

# Width shared by every narrow/secondary action button (targetdir_btn's "Browse ...",
# CreateModelTab's Terminate/Model Fit) so their column lines up consistently across
# the Output Files and Actions group boxes, instead of each guessing its own width.
SECONDARY_BUTTON_WIDTH = 150


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


# The Cellname dropdown shows this in place of a blank entry for "use the GDS
# file's default/top cell" - internally that's still just "" everywhere else
# (saved_values["cellname"], gds_reader.read_gds()'s cellname= argument, the
# generated Python model's settings['cellname']), only the combo box display
# uses this label. Translate at the two boundaries with cellname_for_display()/
# cellname_from_display() rather than special-casing "" throughout.
CELLNAME_DEFAULT_LABEL = "(default)"


def cellname_for_display(cellname):
    return cellname if cellname else CELLNAME_DEFAULT_LABEL


def cellname_from_display(text):
    return "" if text == CELLNAME_DEFAULT_LABEL else text


def shorten_path_for_display(path, head_len=14):
    # A full network path can run to 100+ characters, unreadable crammed into a
    # dialog box next to a second equally long path. Keep just enough of the head
    # to hint at the drive/share, plus the filename - drop the unreadable middle.
    path = path.replace('\\', '/')
    filename = path.rsplit('/', 1)[-1]
    if len(path) <= head_len + len(filename) + 5:
        return path  # already short enough, don't bother truncating
    return f"{path[:head_len]}.../{filename}"


def resolve_missing_file_paths(saved_values, reference_dir, keys=("GdsFile", "SubstrateFile")):
    """After loading a model (.py) or settings (.simcfg/.tsimcfg) file, GdsFile/
    SubstrateFile paths stored for one OS/network-drive mapping often don't exist
    verbatim on another (e.g. a Windows drive letter vs. a Linux mount point for
    the same network share) - saved_values still holds the old, unreachable path.

    For each key whose stored path doesn't resolve, look for a same-named file in
    reference_dir (the folder the just-loaded file itself came from) and, if found,
    switch saved_values to point at that instead. Returns a list of human-readable
    messages describing each substitution made, for the caller to show the user -
    silently swapping in a different file (even same-named) without saying so could
    be confusing if it's actually a stale/different copy.
    """
    messages = []
    for key in keys:
        old_path = saved_values.get(key)
        if not old_path or os.path.isfile(old_path):
            continue
        candidate = os.path.join(reference_dir, os.path.basename(old_path))
        if os.path.isfile(candidate):
            saved_values[key] = candidate.replace('\\', '/')
            messages.append(
                f"{key}: {shorten_path_for_display(old_path)} not found, "
                f"using {shorten_path_for_display(candidate)} instead"
            )
    return messages


def eval_simple_python_expression(node, known_constants):
    # Evaluate a single AST expression node against a symbol table of already-known
    # module-level constants. This is intentionally NOT a general interpreter - it only
    # understands literals (delegated to ast.literal_eval for plain Constant nodes, the
    # same grammar callers used before this function existed, so anything that already
    # worked keeps working unchanged), bare Name lookups against known_constants, simple
    # arithmetic (BinOp/UnaryOp) combining those, and literal-ish List/Tuple/Set/Dict
    # containers whose elements may themselves reference known constants (e.g.
    # "[ftarget]"). Anything else (calls, attributes, subscripts, comprehensions, ...)
    # raises so the caller can fall back to its own "could not resolve this" handling.
    if isinstance(node, ast.Name):
        if node.id in known_constants:
            return known_constants[node.id]
        raise ValueError(f"unknown name '{node.id}'")

    if isinstance(node, ast.BinOp):
        left = eval_simple_python_expression(node.left, known_constants)
        right = eval_simple_python_expression(node.right, known_constants)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise ValueError(f"unsupported operator {type(node.op).__name__}")

    if isinstance(node, ast.UnaryOp):
        operand = eval_simple_python_expression(node.operand, known_constants)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"unsupported unary operator {type(node.op).__name__}")

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [eval_simple_python_expression(elt, known_constants) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(values)
        if isinstance(node, ast.Set):
            return set(values)
        return values

    if isinstance(node, ast.Dict):
        return {
            eval_simple_python_expression(k, known_constants): eval_simple_python_expression(v, known_constants)
            for k, v in zip(node.keys, node.values)
        }

    # plain literals: numbers, strings, etc. - same code path used before
    # Name/BinOp/UnaryOp/container support was added above
    return ast.literal_eval(node)


def collect_module_level_constants(file_path):
    # Build a symbol table of simple module-level constants (e.g. "Z0 = 50" or
    # "ftarget = 30e9"), so that references to them elsewhere in the same script (e.g.
    # simulation_port(port_Z0=2*Z0) or settings['fstart'] = ftarget) can be resolved
    # instead of skipped/mis-parsed. Only true top-level (module-body) "NAME = <expr>"
    # assignments count - anything inside a function/loop/if/class body is deliberately
    # excluded (iterating only tree.body, not ast.walk(tree), does this for free, since
    # those bodies are children of that node rather than of Module.body directly). This
    # matters because a name reassigned inside a loop (common in this codebase's own
    # inductor-synthesis-style scripts) must never be treated as one fixed constant.
    known_constants = {}
    try:
        source = pathlib.Path(file_path).read_text()
        tree = ast.parse(source)
    except (SyntaxError, OSError, UnicodeDecodeError):
        return known_constants

    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue  # skip tuple/multi-target/attribute assignments - out of scope
        name = stmt.targets[0].id
        try:
            # evaluated against the symbol table built so far, so later constants can
            # reference earlier ones in source order (e.g. "B = 2*A" after "A = 1")
            known_constants[name] = eval_simple_python_expression(stmt.value, known_constants)
        except (ValueError, TypeError, ZeroDivisionError, SyntaxError):
            # not resolvable with what we know so far - silently skip; a later
            # reference to this name will just fail to resolve on its own
            continue

    return known_constants


def is_openems_model_script(file_path):
    # Detect whether an imported .py model script is an openEMS model rather than a
    # gds2palace/Palace/Elmer one. setupEM/setupThermal can only ever GENERATE
    # Palace/Elmer model scripts via "Create Model" - there is no way to write an
    # openEMS model back out. If "Create Model" were allowed to reuse an imported
    # openEMS script's own file path as its output (see the "reuse" logic in
    # load_configuration_from_file() below), the next "Create Model" click would
    # silently overwrite the user's real openEMS solver script with generated Palace
    # code, which setupEM has no way to regenerate.
    #
    # openEMS models import the openEMS/CSXCAD Python bindings directly
    # ("from openEMS import openEMS", "import CSXCAD") - markers that never appear in
    # a gds2palace-based script (which does "from gds2palace import *" instead), so
    # this is a reliable, low-false-positive textual check, in the same spirit as the
    # existing Elmer-vs-Palace source-text detection in apply_python_import_data().
    try:
        text = pathlib.Path(file_path).read_text()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(re.search(r'^\s*(?:from|import)\s+(?:openEMS|CSXCAD)\b', text, re.MULTILINE))


def resolve_value_text(value_text, known_constants):
    # Parse a raw right-hand-side text (as captured by parse_assignments() below, e.g.
    # "50", "ftarget", "2*Z0", "[ftarget]") as a Python expression and evaluate it
    # against known_constants via eval_simple_python_expression(). Raises the same way
    # ast.literal_eval()/float()/int() already did on unparseable input - callers that
    # relied on that failure mode (to fall back or skip) keep working unchanged.
    node = ast.parse(value_text, mode='eval').body
    return eval_simple_python_expression(node, known_constants)


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


def next_available_source_layer(gds_layers_present, excluded_layers, start=201):
    """Smallest layer number >= start that actually has geometry in the GDS
    (per gds_layers_present) and isn't already spoken for (per
    excluded_layers - callers pass in both already-used port/thermal-object
    layers and real stackup metal/via layer numbers, so this never suggests
    a layer that means something else). Returns None if no such layer
    exists, so callers can fall back to their own default.
    """
    candidates = sorted(l for l in gds_layers_present if l >= start and l not in excluded_layers)
    return candidates[0] if candidates else None


def update_missing_layer_column(table, source_col, comment_col, gds_layers_present):
    """Set comment_col to "(missing in layout)" for every row whose
    source_col holds a layer number not in gds_layers_present, and clear it
    otherwise. Purely a computed display hint, not real row data - existing
    save/export logic in both apps' Ports/Thermal tabs only ever reads
    source_col and the columns before it, so writing into this trailing
    (already otherwise-unused) column doesn't affect anything else.
    """
    for row in range(table.rowCount()):
        item = table.item(row, source_col)
        comment = ""
        if item is not None and item.text():
            try:
                layernum = int(item.text())
            except ValueError:
                pass
            else:
                if layernum not in gds_layers_present:
                    comment = "(missing in layout)"
        existing = table.item(row, comment_col)
        if existing is None or existing.text() != comment:
            table.setItem(row, comment_col, QTableWidgetItem(comment))


def _derived_layer_range_is_safe(metals_list, layer_min, layer_max):
    """True only if MainWindowBase.get_gds_layers_in_range()'s fast,
    non-flattening hierarchy scan cannot possibly miss a layer that "exists"
    solely via boolean derivation from other real layers - i.e. no stackup
    DerivedLayer's output layer number falls inside [layer_min, layer_max].
    A derived layer's polygons are computed by gds_reader.resolve_derived_layers()
    from other real layers and never exist as literal geometry in the raw
    GDS, so a hierarchy walk that only looks at actual polygons would wrongly
    report one "absent" if it lands in the queried range.

    Deliberately conservative: any failure to introspect derived_layers
    safely returns False (use the slow/exact read_gds()-based path), never
    the reverse. In the common case - no <DerivedLayers> section in the
    stackup XML at all - metals_list.derived_layers is None and this returns
    True immediately.
    """
    derived_layers = getattr(metals_list, "derived_layers", None)
    if derived_layers is None:
        return True
    try:
        layernums = derived_layers.getlayernumbers()
    except Exception:
        return False
    return not any(layer_min <= n <= layer_max for n in layernums)


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
        self.cellname_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.cellname_box.addItems([CELLNAME_DEFAULT_LABEL])
        # stretch to fill the row like gds_file_edit above, so its right edge lines
        # up with gds_file_edit's - and show_layout_btn below matches browse_gds_btn
        self.cellname_layout.addWidget(self.cellname_box, 1)
        self.show_layout_btn = QPushButton("Show layout")
        self.show_layout_btn.setFixedWidth(150)  # matches browse_gds_btn above
        self.show_layout_btn.clicked.connect(self.MainWindow.open_layout_preview)
        self.cellname_layout.addWidget(self.show_layout_btn)
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
        self.variable_overrides_table.itemChanged.connect(self._on_variable_override_item_changed)
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
            try:
                lib = gdspy.GdsLibrary()
                lib.read_gds(filename)
                cellnames = list(lib.cells.keys())
            except Exception:
                # called on a just-resolved path during load_values() now, not only after
                # the user explicitly picked a file via the Browse dialog - be defensive
                return False
            self.cellname_box.clear()
            self.cellname_box.addItem(CELLNAME_DEFAULT_LABEL)
            for cellname in cellnames:
                self.cellname_box.addItem(cellname)
            return True
        return False

    def set_gds_file(self, filename):
        # clear model name and target dir if they were auto-generated from previous model
        self.MainWindow.clear_modelname_and_targetdir()
        self.gds_file_edit.setText(filename)
        self.MainWindow.saved_values["GdsFile"] = filename.replace('\\', '/')
        # file is read when leaving the files tab
        self.update_cellnames_from_gds(filename)
        self.MainWindow.refresh_source_layer_hints()

    def browse_XML_file(self):
        # start browsing from previous file location, if valid
        previous_file = self.XML_file_edit.text()
        previous_directory = os.path.dirname(previous_file)
        if not os.path.isdir(previous_directory):
            # user-configured default (Preferences > Files), falling back to
            # the XML files bundled with setupEM if unset/invalid
            custom_dir = get_preference(self.MainWindow.APP_NAME, "xml_browse_directory", "")
            if custom_dir and os.path.isdir(custom_dir):
                previous_directory = custom_dir
            else:
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
        self._close_stackup_editor_if_clean()

    def _close_stackup_editor_if_clean(self):
        # a different substrate file was just selected here - if the Stackup Editor
        # is open and editing some (other) file with nothing unsaved in it, close it
        # rather than leave it showing content that no longer matches what's selected
        # here. Leave it open (silently - no prompt) if it has edits that would be lost.
        editor = getattr(self.MainWindow, "stackup_editor_window", None)
        if editor is not None and not editor.has_unsaved_changes():
            editor.close()

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
        # populating the table below fires itemChanged for every cell just like a real user
        # edit would - block it here so the live-update handler doesn't re-enter read_XML()
        # (and its own call back into this same method) while this method is still running.
        self.variable_overrides_table.blockSignals(True)
        try:
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
        finally:
            self.variable_overrides_table.blockSignals(False)
        self.variable_overrides_container.setVisible(True)

    def _on_variable_override_item_changed(self, item):
        # only column 2 ("Override value") is ever user-editable - columns 0/1 have
        # ItemIsEditable cleared, so this only fires here from a genuine user edit,
        # never from update_variable_overrides_grid()'s own repopulation (blockSignals above).
        if item.column() != 2:
            return
        # read_XML() rebuilds this table (update_variable_overrides_grid() does
        # setRowCount(0) then repopulates) - running that synchronously from within
        # itemChanged would tear down the very item/editor the view is still in the
        # middle of committing, which Qt reports as "commitData called with an editor
        # that does not belong to this view". Deferred via QTimer.singleShot(0, ...),
        # the same pattern used elsewhere in this codebase for this exact hazard
        # (see StackupEditorWindow._guarded() in stackupEditor.py).
        QTimer.singleShot(0, self._apply_variable_override_change)

    def _apply_variable_override_change(self):
        if not shiboken6.isValid(self):
            return
        self.MainWindow.saved_values["variable_overrides"] = self.get_variable_overrides()
        self.MainWindow.read_XML()

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
        gdsfile = get_saved_value(saved_values, "GdsFile", "Please choose a file ===>")
        self.gds_file_edit.setText(gdsfile)
        XML = get_saved_value(saved_values, "SubstrateFile", "Please choose a file ===>")
        self.XML_file_edit.setText(XML)
        self.update_XML_description(XML)
        self.update_variable_overrides_grid(XML)

        # Repopulate the full cellname picker from the GDS file (matching what
        # browse_gds_file()/set_gds_file() already do), not just the one saved value -
        # otherwise a resolved/substituted GdsFile path (see resolve_missing_file_paths)
        # leaves the dropdown showing only the previously-saved cellname (blank, if none
        # was set) with no way to pick a different one without re-browsing for the file.
        saved_cellname = get_saved_value(saved_values, "cellname", "")
        if os.path.isfile(gdsfile) and self.update_cellnames_from_gds(gdsfile):
            index = self.cellname_box.findText(cellname_for_display(saved_cellname))
            self.cellname_box.setCurrentIndex(index if index >= 0 else 0)
        else:
            self.cellname_box.clear()
            self.cellname_box.addItem(cellname_for_display(saved_cellname))
        viamerge_default = get_preference(self.MainWindow.APP_NAME, "merge_polygon_size", "0.5")
        self.viamerge_edit.setText(str(get_saved_value(saved_values, "merge_polygon_size", viamerge_default)))
        self.preprocess_gds_checkbox.setChecked(bool(get_saved_value(saved_values, "preprocess_gds", True)))

        purpose_default = get_preference(self.MainWindow.APP_NAME, "purpose", "0")
        int_list = saved_values.get("purpose", purpose_default)
        purpose_string = str(int_list).replace('[', '').replace(']', '')
        self.purpose_edit.setText(purpose_string)
        # self.purpose_edit.setText(','.join(map(str, int_list)))

        # read_XML(self.XML_file_edit.text()) # safe if invalid filename

    def save_values(self):
        saved_values = self.MainWindow.saved_values
        saved_values["GdsFile"] = self.gds_file_edit.text().replace('\\', '/')
        saved_values["SubstrateFile"] = self.XML_file_edit.text().replace('\\', '/')
        saved_values["preprocess_gds"] = self.preprocess_gds_checkbox.isChecked()
        saved_values["cellname"] = cellname_from_display(self.cellname_box.currentText())
        saved_values["variable_overrides"] = self.get_variable_overrides()

        try:
            merge_polygon_size = float(self.viamerge_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", f"Not a valid value for via array merging")
            self.viamerge_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "merge_polygon_size", "0.5")))
            return False
        saved_values["merge_polygon_size"] = float(merge_polygon_size)

        text = self.purpose_edit.text()
        if text != "":
            # save as list of comma separated values
            saved_values["purpose"] = ast.literal_eval('[' + text + ']')
        else:
            purpose_default = get_preference(self.MainWindow.APP_NAME, "purpose", "0")
            saved_values["purpose"] = ast.literal_eval('[' + str(purpose_default) + ']')

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

INVALID_MATERIAL_COLOR = QColor(255, 0, 0, 80)

# Distinct from the regular conductor fill (QColor(230,230,230,90)), the resistor/sheet fill
# (QColor(230,130,130,90)), and INVALID_MATERIAL_COLOR above - a PEC layer is valid, just
# unlike any of those, so it gets its own recognizable "ideal conductor" look.
PEC_MATERIAL_COLOR = QColor(180, 220, 255, 140)


def _is_pec_material(materialname):
    """True if materialname is the reserved PEC keyword (case-insensitive) - a Layer that
       compute_stackup_layout() below must draw as an ideal conductor even though
       materials_list.get_by_name() deliberately returns None for it (see
       stackup_reader.PEC_MATERIAL_NAME).
    """
    return materialname is not None and materialname.strip().upper() == stackup_reader.PEC_MATERIAL_NAME.upper()


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

def _build_dielectric_tooltip(dielectric, overlap_partner_names=None):
    tooltip = (
        f"{dielectric.name}\n"
        f"Type: Dielectric\n"
        f"Material: {dielectric.material}\n"
        f"Zmin: {dielectric.zmin:.4f} µm\n"
        f"Zmax: {dielectric.zmax:.4f} µm\n"
        f"Thickness: {dielectric.thickness:.4f} µm"
    )
    if overlap_partner_names:
        # see dielectric_layers_list.find_z_overlap_pairs() / InteractiveRegionItem's
        # always-on red dashed outline - names exactly what this slab conflicts with,
        # so clicking the highlighted shape immediately explains why it's highlighted
        tooltip += "\n⚠ Overlaps: " + ", ".join(overlap_partner_names)
    return tooltip


def _build_layer_tooltip(metal):
    lines = [
        f"{metal.name} (GDSII layer {metal.layernum})",
        f"Type: {metal.type.capitalize()}",
        f"Material: {metal.material}",
        f"Zmin: {metal.zmin:.4f} µm",
        f"Zmax: {metal.zmax:.4f} µm",
    ]
    if not metal.is_sheet:
        lines.append(f"Thickness: {metal.thickness:.4f} µm")
    return "\n".join(lines)


def compute_stackup_layout(materials_list, dielectrics_list, metals_list, width, height,
                            dielectric_color_fn, dielectric_label_fn,
                            metal_label_fn, via_label_suffix_fn, metal_color_fn,
                            active_chiplet_id=None):
    """Pure layout computation for the stackup cross-section preview - no QPainter/
    widget/scene involved. Returns (draw_calls, interactive_entries):

    draw_calls: an ordered list of (QPainter method name, args) tuples. Replaying them
    in order (see render_stackup_layout()) reproduces exactly what the previous
    QWidget/paintEvent-based VectorWidget drew directly - this function is a mechanical
    move of that drawing code (same loop structure, same schematic layout math: dielectric
    slab height by metal-level count rather than physical thickness, same-zmin metals split
    side by side, linear-interpolated via/drawn-dielectric placement, rotating via x-slots),
    not a re-derivation of it, specifically to avoid subtly changing the visual layout.

    interactive_entries: one {"kind": "dielectric"|"layer", "key": name, "rect": QRectF,
    "ref": dielectric_layer/metal_layer, "tooltip": str, "chiplet_id": str|None} dict per
    dielectric slab, metal, or via box - used to build the transparent hoverable/selectable
    overlay items. "key" is always the element's Name (unique *within one chiplet's shown
    subtree plus the interposer* - not guaranteed unique across different chiplets, which is
    why VectorWidget's selection keying includes "chiplet_id" too), matching what the stackup
    editor's row_elements look up by. "chiplet_id" is None for an interposer-sourced entry,
    else the currently active chiplet's id (see active_chiplet_id below).

    active_chiplet_id (str, optional): which chiplet (dielectrics_list.chiplet_groups.chiplets[i].id)
    to show, for a stackup where detect_chiplet_groups() found more than one chiplet sharing a
    common interposer base - the interposer's own Dielectrics/Layers are always shown in
    addition, regardless of this argument. None picks the first detected chiplet. Ignored
    (as if no chiplets existed) when dielectrics_list.chiplet_groups is missing or has no
    chiplets - the ordinary, non-chiplet case, which renders exactly as before this parameter
    was added.
    """
    draw_calls = []
    interactive_entries = []

    # chiplet-aware filtering: with no branching detected (the ordinary case) or no
    # chiplet_groups at all (e.g. an in-memory dielectrics_list built by hand rather than via
    # read_substrate()/parse_substrate()), dielectrics_source/visible_layers preserve exactly
    # today's behavior - every dielectric/metal is shown, nothing is filtered.
    chiplet_groups = getattr(dielectrics_list, "chiplet_groups", None)
    dielectrics_source = dielectrics_list.dielectrics
    visible_layers = None   # None = no filtering; a set means "only these metals are visible"
    active_chiplet = None
    if chiplet_groups is not None and chiplet_groups.chiplets:
        active_chiplet = next((c for c in chiplet_groups.chiplets if c.id == active_chiplet_id),
                               chiplet_groups.chiplets[0])
        visible_dielectrics = set(chiplet_groups.interposer_dielectrics) | set(active_chiplet.dielectrics)
        visible_layers = set(chiplet_groups.interposer_layers) | set(active_chiplet.layers)
        dielectrics_source = [d for d in dielectrics_list.dielectrics if d in visible_dielectrics]

    def entry_chiplet_id(dielectric_or_metal):
        # None (interposer) unless this element is part of the currently active chiplet's
        # own subtree - deliberately checked by identity against the active chiplet's own
        # lists rather than just "a chiplet exists", so an interposer-sourced entry (shown
        # alongside the active chiplet) still correctly gets None
        if active_chiplet is None:
            return None
        if dielectric_or_metal in active_chiplet.dielectrics or dielectric_or_metal in active_chiplet.layers:
            return active_chiplet.id
        return None

    # dielectrics involved in an unexpected same-scope z-overlap (see
    # dielectric_layers_list.find_z_overlap_pairs()) - drawn with a red dashed outline
    # (InteractiveRegionItem._OVERLAP_PEN) regardless of the current chiplet selection, so
    # the problem is visible without having to switch to whichever chiplet happens to be
    # involved. Keyed by name (not object identity) to match dielectric_or_metal.name
    # elsewhere in this function; overlap_partners_by_name additionally names *which*
    # dielectric(s) it conflicts with, for the tooltip.
    overlap_partners_by_name = {}
    for a, b in dielectrics_list.find_z_overlap_pairs():
        overlap_partners_by_name.setdefault(a.name, []).append(b.name)
        overlap_partners_by_name.setdefault(b.name, []).append(a.name)

    # utility: flip y to have y=0 at bottom
    def flipy(y):
        return height - y

    def setPen(pen):
        draw_calls.append(("setPen", (pen,)))

    def setBrush(brush):
        draw_calls.append(("setBrush", (brush,)))

    def drawRect(x, y, w, h):
        draw_calls.append(("drawRect", (x, y, w, h)))

    def drawLine(x1, y1, x2, y2):
        draw_calls.append(("drawLine", (x1, y1, x2, y2)))

    def drawTextAt(x, y, text):
        draw_calls.append(("drawText", (x, y, text)))

    # utility to draw text with alignment on right side
    def drawText_right(x, y, w, h, text):
        rect = QRect(x, y - h, w, h)
        draw_calls.append(("drawText", (rect, Qt.AlignVCenter | Qt.AlignRight, text)))

    def drawText_left(x, y, w, h, text):
        rect = QRect(x, y - h, w, h)
        draw_calls.append(("drawText", (rect, Qt.AlignVCenter | Qt.AlignLeft, text)))

    # other chiplets sharing the active one's branch point (interposer dielectric) -
    # non-empty exactly when there's a sibling chiplet not currently shown, which is
    # when the "something sits beside this" gutter/stub below applies
    sibling_chiplets = []
    if active_chiplet is not None:
        sibling_chiplets = [c for c in chiplet_groups.chiplets
                            if c is not active_chiplet and c.branch_point is active_chiplet.branch_point]

    xmin = int(width * 0.02)
    # sibling-chiplet stub geometry, decided here (not down where it's drawn) since
    # xmax itself needs to leave exactly enough room for it - a fixed size relative
    # to width, not "whatever's left of a separately-chosen gutter", so there's no
    # left-over dead space between the stub and the canvas edge
    STUB_WIDTH = int(width * 0.06)
    STUB_RIGHT_MARGIN = int(width * 0.02)
    # narrow the whole drawing slightly (a touch more on the right) to leave room for
    # the sibling-chiplet stub below - applied globally (not just to the active
    # chiplet's own rows) so every x-coordinate downstream (slab width, metal
    # x-splits, via slots, text placement) stays consistent with no other change
    xmax = int(width - STUB_WIDTH - STUB_RIGHT_MARGIN) if sibling_chiplets else int(width * 0.98)

    ymin = int(height * 0.025)
    ymax = int(height * 0.975)

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
    # dielectrics_list.dielectrics' own array order
    dielectrics_bottom_up = sorted(dielectrics_source, key=lambda d: d.zmin)
    for dielectric in dielectrics_bottom_up:  # bottom up
        setPen(penBlack)

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
        material = materials_list.get_by_name(materialname)
        if material is not None:
            # dielectric color/label are app-specific (permittivity vs. thermal conductivity)
            dielectric_shape['color'] = dielectric_color_fn(material)
        else:
            # unresolved Material reference (typo, or a transient state while the user is
            # still typing a new value in the editor) - PEC is never valid here (rejected by
            # stackup_writer.validate_stackup()), so this is always a genuine error, unlike
            # the metal/sheet branch below which also has a legitimate PEC case to handle
            dielectric_shape['color'] = INVALID_MATERIAL_COLOR
        dielectric_shape['material'] = material

        total_parts = total_parts + parts
        dielectric_shapes.append(dielectric_shape)

    # calculate height of one dielectric shape
    total_parts = max(total_parts, 1)
    part_height = int((ymax - ymin) / (total_parts))

    y = ymin
    w = xmax - xmin

    # we need to store data for original z position and the displayed y position
    stored_z = np.array([0])
    stored_y = np.array([ymin])

    for dielectric_shape in dielectric_shapes:
        h = part_height * dielectric_shape['numparts']
        dielectric = dielectric_shape['dielectric']
        color = dielectric_shape['color']
        material = dielectric_shape['material']

        if material is not None:
            material_string = dielectric_label_fn(dielectric, material)
        else:
            material_string = 'INVALID MATERIAL REFERENCE: ' + dielectric.material

        # adaptive left margin for this dielectric's metal boxes/side-labels: the
        # dielectric name is drawn at xmin+5, and the per-metal "distance to
        # boundary" labels below default to starting at xmetal-60 - for a short
        # name (the common case, e.g. "SiO2"/"EPI") that's already well clear of
        # the default xmin+120 metal-box margin, but a longer name (e.g. an
        # auto-generated chiplet dielectric name) can run into that label and
        # visually merge with it, especially in a short slab with few rows where
        # the name's own vertically-centered position lands on the same row as
        # one of those labels. Estimating the name's rendered width and widening
        # the margin only when needed keeps every existing short-name stackup
        # pixel-identical while fixing the long-name case generally, rather than
        # special-casing this one dielectric. A plain character-count estimate
        # (not QFontMetrics) deliberately keeps this function usable with no
        # QApplication/QGuiApplication instance yet constructed - QFontMetrics
        # requires one and otherwise crashes the process outright (not a
        # catchable Python exception) - which every other part of this "pure,
        # no live Qt app needed" layout function already relies on (see its own
        # docstring), including headless test scripts that call it directly.
        name_width = len(dielectric.name) * 7 + 10
        metal_box_left_margin = max(120, name_width + 75)
        extra_margin = metal_box_left_margin - 120

        setPen(penBlack)
        setBrush(color)
        drawRect(xmin, flipy(y), w, -h)
        interactive_entries.append({
            "kind": "dielectric",
            "key": dielectric.name,
            "rect": QRectF(xmin, flipy(y), w, -h).normalized(),
            "ref": dielectric,
            "tooltip": _build_dielectric_tooltip(dielectric, overlap_partners_by_name.get(dielectric.name)),
            "chiplet_id": entry_chiplet_id(dielectric),
            "has_overlap": dielectric.name in overlap_partners_by_name,
        })
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

                setPen(penBlack)

                # check if metal is aligned with dielectric zmin
                elevation = metal.zmin - dielectric.zmin
                if n == 0 and (abs(elevation) > 0.001):
                    # draw some vertical offset, not aligned with dielectric
                    ymetal = ymetal + part_height * 0.5  # slight offset

                # check if next metal is at same zmin
                next_at_same_zmin = False
                previous_at_same_zmin = False
                xmetal = xmin + metal_box_left_margin
                wmetal = w - 200 - extra_margin

                if n < len(metals_inside) - 1:
                    next_metal = metals_inside[n + 1]
                    if abs(next_metal.zmin - metal.zmin) < 0.001:
                        next_at_same_zmin = True
                        xmetal = xmin + metal_box_left_margin
                        wmetal = int(w / 2) - 100 - extra_margin
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

                material = materials_list.get_by_name(metal.material)
                if material is not None:
                    if metal.is_sheet:
                        # sheet metal that is simulated with zero extrusion
                        # (named height_box, not height, to avoid shadowing the
                        # outer "height" parameter that flipy() closes over)
                        height_box = 3
                        label_string = metal_label_fn(metal, material, True)
                    else:
                        # regular extruded metal
                        height_box = part_height / 2
                        label_string = metal_label_fn(metal, material, False)

                    # the box for this metal - metal_color_fn(material) can
                    # override the default type-based color below (e.g.
                    # setupThermal's thermal-conductivity scale); None means
                    # "no override", i.e. every caller except setupThermal
                    override_color = metal_color_fn(material)
                    if override_color is not None:
                        setBrush(override_color)
                        drawRect(xmetal, flipy(ymetal), wmetal, -int(height_box))
                    elif material.type.upper() == "CONDUCTOR":
                        setBrush(QColor(230, 230, 230, 90))
                        drawRect(xmetal, flipy(ymetal), wmetal, -int(height_box))
                    else:
                        setBrush(QColor(230, 130, 130, 90))
                        drawRect(xmetal, flipy(ymetal), wmetal, -int(height_box))
                elif _is_pec_material(metal.material):
                    # reserved PEC keyword: valid (no <Materials> entry needed/expected -
                    # materials_list.get_by_name() deliberately returns None for it), draw as
                    # an ideal conductor instead of falling into the invalid-reference case below
                    height_box = 3 if metal.is_sheet else part_height / 2
                    setBrush(PEC_MATERIAL_COLOR)
                    drawRect(xmetal, flipy(ymetal), wmetal, -int(height_box))
                    label_string = 'PEC (ideal conductor)'
                else:
                    # material assignment is invalid
                    height_box = part_height / 2
                    setBrush(INVALID_MATERIAL_COLOR)
                    drawRect(xmetal, flipy(ymetal), wmetal, -int(height_box))
                    label_string = 'INVALID MATERIAL REFERENCE: ' + metal.material

                interactive_entries.append({
                    "kind": "layer",
                    "key": metal.name,
                    "rect": QRectF(xmetal, flipy(ymetal), wmetal, -int(height_box)).normalized(),
                    "ref": metal,
                    "tooltip": _build_layer_tooltip(metal),
                    "chiplet_id": entry_chiplet_id(metal),
                })

                setPen(penBlack)
                drawText_left(xmetal + 10, flipy(ymetal), wmetal, part_height / 2, f"{metal.name} ({metal.layernum})")
                setPen(penGray)
                drawText_right(xmetal, flipy(ymetal), wmetal - 10, part_height / 2, label_string)
                # store the drawing position, because vias will refer to that
                if not metal.zmin in stored_z:
                    stored_z = np.append(stored_z, metal.zmin)
                    stored_y = np.append(stored_y, ymetal)
                if not metal.zmax in stored_z:
                    stored_z = np.append(stored_z, metal.zmax)
                    stored_y = np.append(stored_y, ymetal + height_box)

                setPen(penGray)
                drawLine(xmetal - 60, flipy(ymetal), xmetal - 10, flipy(ymetal))
                # draw line at top side of metal
                if not metal.is_sheet:
                    drawLine(xmetal - 60, flipy(ymetal + height_box), xmetal - 10, flipy(ymetal + height_box))
                    heightstring = f'{metal.thickness:.3f}µm'
                    setPen(penDarkGray)
                    drawText_left(xmetal - 60, flipy(ymetal), 50, height_box, heightstring)

                if not previous_at_same_zmin:
                    # draw height to metal above
                    if next_metal_above is not None:
                        dz = abs(next_metal_above.zmin - metal.zmax)
                        heightstring = f'{dz:.3f}µm'
                        setPen(penGray)
                        # sheet metals draw at height_box=3px, too short to fit this
                        # label without vertical clipping - give the text its own
                        # minimum box height, independent of the drawn box height
                        text_height = max(height_box, 14)
                        drawText_left(xmetal - 60, flipy(ymetal + height_box), 50, text_height, heightstring)

                if n == len(metals_inside) - 1:
                    # last metal (top metal)
                    # place text for distance to dielectric boundary

                    setPen(penBlack)
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
                    setPen(penGray)
                    drawTextAt(xmetal - 60, flipy(ymetal + height_box + 5), heightstring)

                if n == 0 and elevation > 0.001:
                    # metal not aligned with bottom of dielectric, add a label for offset value
                    heightstring = f'{elevation:.3f}µm'
                    setPen(penGray)
                    drawTextAt(xmetal - 60, flipy(ymetal - 10), heightstring)

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

        if sibling_chiplets:
            # visual reminder that another chiplet sits beside the one currently shown,
            # starting at their shared interface (the branch point dielectric's top) -
            # deliberately schematic: a short, empty, open-topped outline in the gutter
            # reserved above (xmax narrowed for this), not a to-scale/detailed rendering
            # of the sibling. Open top (no top line) reads as "truncated - continues
            # beyond view" rather than a small closed box that happens to be there. "+n"
            # (n = other sibling chiplets not currently shown) is the only content inside -
            # no interior geometry, matching the "not in detail" ask. STUB_WIDTH itself
            # is set earlier, alongside xmax - see the comment there.
            STUB_HEIGHT = 56
            stub_x = xmax   # flush against the main column's right edge - reads as
                             # growing out of it, right at the shared boundary line
                             # already drawn there, rather than a disconnected box
            stub_y_bottom = flipy(z_to_y(active_chiplet.branch_point.zmax))
            stub_y_top = stub_y_bottom - STUB_HEIGHT
            setPen(QPen(penGray.color(), 1, Qt.DashLine))
            setBrush(Qt.NoBrush)
            drawLine(stub_x, stub_y_bottom, stub_x, stub_y_top)                              # left
            drawLine(stub_x + STUB_WIDTH, stub_y_bottom, stub_x + STUB_WIDTH, stub_y_top)     # right
            drawLine(stub_x, stub_y_bottom, stub_x + STUB_WIDTH, stub_y_bottom)               # bottom
            # top edge deliberately omitted - see docstring above

            setPen(penGray)
            text_rect = QRect(int(stub_x), int(stub_y_top), int(STUB_WIDTH), int(STUB_HEIGHT / 2))
            draw_calls.append(("drawText", (text_rect, Qt.AlignCenter, f"+{len(sibling_chiplets)}")))

            # sketched (outline-only, no filled arrowhead) left/right chevrons hinting
            # at the Left/Right keyboard shortcut that steps through chiplets
            # (VectorWidget.keyPressEvent) - purely a discoverability nudge, not
            # clickable itself, so plain open "<"/">" strokes are enough; no need for
            # a filled/solid arrow that would suggest a button.
            setPen(QPen(penGray.color(), 1))
            chevron_cy = stub_y_bottom - 14   # bottom band, clear of the "+n" text above
            chevron_size = 5
            chevron_gap = 8
            left_cx = stub_x + STUB_WIDTH / 2 - chevron_gap
            right_cx = stub_x + STUB_WIDTH / 2 + chevron_gap
            drawLine(left_cx + chevron_size, chevron_cy - chevron_size, left_cx, chevron_cy)
            drawLine(left_cx, chevron_cy, left_cx + chevron_size, chevron_cy + chevron_size)
            drawLine(right_cx - chevron_size, chevron_cy - chevron_size, right_cx, chevron_cy)
            drawLine(right_cx, chevron_cy, right_cx - chevron_size, chevron_cy + chevron_size)

        # next we draw the vias, based on the screen position of metals that we have stored
        # via position alternates between 3 positions along x axis
        pos = 1
        w = (xmax - xmin) / 10

        for metal in metals_list.metals:
            if metal.is_via or metal.is_dielectric:
                if visible_layers is not None and metal not in visible_layers:
                    continue

                material = materials_list.get_by_name(metal.material)
                label_suffix = via_label_suffix_fn(metal, material)

                # metal_color_fn(material) can override this box's default
                # color too (e.g. setupThermal's thermal-conductivity scale) -
                # None means "no override", same default color as before
                override_color = metal_color_fn(material)
                setBrush(override_color if override_color is not None else QColor(136, 192, 200, 80))

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

                setPen(penBlack)
                drawRect(xvia, flipy(y1), w, -h)
                interactive_entries.append({
                    "kind": "layer",
                    "key": metal.name,
                    "rect": QRectF(xvia, flipy(y1), w, -h).normalized(),
                    "ref": metal,
                    "tooltip": _build_layer_tooltip(metal),
                    "chiplet_id": entry_chiplet_id(metal),
                })
                drawTextAt(xvia + 5, flipy(y1 + 5), f"{metal.name} ({metal.layernum})" + label_suffix)

    return draw_calls, interactive_entries


def compute_topology_overview_layout(dielectrics_list, materials_list, width, height,
                                      dielectric_color_fn, dielectric_label_fn):
    """Pure layout computation for the "topology overview" sketch: the shared/interposer
    dielectrics drawn as one full-width slab stack, with each detected chiplet's own
    dielectrics drawn as an equal-width column placed side by side on top of it - a
    NOT-to-GDS-scale sketch of "small chiplets sitting side by side on a shared dielectric
    base". Deliberately limited to dielectric blocks only (no metal/via boxes, no
    z-interpolation) - see compute_stackup_layout() for that fuller, more fragile rendering
    this is a simplified sibling of, not a variant of.

    Only meaningful for a stackup with 2+ detected chiplets (dielectrics_list.chiplet_groups.
    chiplets) - returns ([], []) otherwise, so a caller that forgets to gate on chiplet count
    gets an empty scene rather than a crash. Real gating is in the UI (see VectorWidget.
    set_topology_mode() / StackupPreviewWindow's topology toggle, which only becomes visible
    at the same 2+ chiplets threshold ChipletSwitcher already uses).

    Returns (draw_calls, interactive_entries) - same contract as compute_stackup_layout()/
    render_stackup_layout(), so this is a drop-in alternative source of draw_calls for
    StackupBackgroundItem, and interactive_entries follow the same {"kind": "dielectric",
    "key", "rect", "ref", "tooltip", "chiplet_id"} shape (kind is always "dielectric" here -
    there is no "layer" kind in this mode) so InteractiveRegionItem/VectorWidget's existing
    click/hover/selection machinery works unchanged.
    """
    chiplet_groups = getattr(dielectrics_list, "chiplet_groups", None)
    if chiplet_groups is None or len(chiplet_groups.chiplets) < 2:
        return [], []

    draw_calls = []
    interactive_entries = []

    # same draw-call-recorder-closure pattern as compute_stackup_layout() (setPen/setBrush/
    # drawRect/drawLine/flipy), duplicated locally rather than factored into a shared helper -
    # keeps compute_stackup_layout() completely untouched, zero regression risk there
    def flipy(y):
        return height - y

    def setPen(pen):
        draw_calls.append(("setPen", (pen,)))

    def setBrush(brush):
        draw_calls.append(("setBrush", (brush,)))

    def drawRect(x, y, w, h):
        draw_calls.append(("drawRect", (x, y, w, h)))

    def drawLine(x1, y1, x2, y2):
        draw_calls.append(("drawLine", (x1, y1, x2, y2)))

    def drawText_center(x, y, w, h, text):
        rect = QRect(int(x), int(y - h), int(w), int(h))
        draw_calls.append(("drawText", (rect, Qt.AlignVCenter | Qt.AlignHCenter, text)))

    penBlack = QPen(Qt.black, 1)
    penDivider = QPen(QColor(134, 132, 130), 1, Qt.DashLine)

    def dielectric_color(dielectric):
        material = materials_list.get_by_name(dielectric.material)
        return dielectric_color_fn(material) if material is not None else INVALID_MATERIAL_COLOR

    # purely cosmetic pseudo-3D extrusion: a right-side face (darker shade) on every slab,
    # and a top face (lighter shade) only on the topmost slab of each "tower" (an
    # interposer stack or one chiplet column) - a top face on every slab would just get
    # overdrawn by the slab stacked directly above it, since there's no gap there to show
    # it in; the right-side face has no such conflict since nothing else is drawn to the
    # right of a slab within its own tower. DEPTH is in pixels, not model units - purely a
    # fixed visual bevel size regardless of zoom/window size.
    DEPTH = 6

    def draw_side_face(x, y, w, h, color):
        top, bottom = (y, y + h) if h >= 0 else (y + h, y)
        right = x + w
        face = QPolygonF([
            QPointF(right, top), QPointF(right, bottom),
            QPointF(right + DEPTH, bottom - DEPTH), QPointF(right + DEPTH, top - DEPTH),
        ])
        setPen(penBlack)
        setBrush(QBrush(color.darker(130)))
        draw_calls.append(("drawPolygon", (face,)))

    def draw_top_face(x, y, w, h, color):
        top = y if h >= 0 else y + h
        face = QPolygonF([
            QPointF(x, top), QPointF(x + w, top),
            QPointF(x + w + DEPTH, top - DEPTH), QPointF(x + DEPTH, top - DEPTH),
        ])
        setPen(penBlack)
        setBrush(QBrush(color.lighter(130)))
        draw_calls.append(("drawPolygon", (face,)))

    interposer = sorted(chiplet_groups.interposer_dielectrics, key=lambda d: d.zmin)
    chiplets = chiplet_groups.chiplets

    xmin = width * 0.02
    xmax = width * 0.98 - DEPTH  # leave room for the side-face extrusion on the right edge
    w_total = xmax - xmin
    n_chiplets = len(chiplets)
    gutter = width * 0.035  # a bit more breathing room between chiplets on the carrier
    col_w = (w_total - gutter * (n_chiplets - 1)) / n_chiplets

    # HEADER_HEIGHT is reserved *above* ymax for the chiplet column header labels (drawn
    # below), not squeezed into the top margin - the header band needs real budget of its
    # own, or it clips against the scene's own top edge (y=0) at typical preview window
    # heights, since the margin alone is only ~2.5% of height. DEPTH is reserved on top of
    # that so each column's top-face extrusion has room too, above the columns but below
    # the header text (see the header draw call below, shifted up by DEPTH to clear it).
    HEADER_HEIGHT = 20
    ymin = height * 0.025
    ymax = height * 0.975 - HEADER_HEIGHT - DEPTH

    # schematic sizing: unlike compute_stackup_layout()'s "1 part per metal level" rule
    # (parts = max(1, metals_count), setup_common.py:1373), no metals are drawn here at all,
    # so every dielectric simply gets 1 part
    base_parts = max(1, len(interposer))
    chiplet_parts = max(1, max(len(chiplet.dielectrics) for chiplet in chiplets))
    total_parts = base_parts + chiplet_parts
    part_height = (ymax - ymin) / total_parts
    base_height = part_height * base_parts
    # every chiplet's column gets the SAME total height (driven by whichever chiplet has the
    # most dielectrics), so all columns reach the same top y and visually "sit side by side" -
    # a chiplet with fewer dielectrics gets a few taller slabs instead of a shorter column
    column_height = part_height * chiplet_parts

    # draw interposer slabs bottom-up, full width - every slab gets a side-face extrusion,
    # but only the topmost one also gets a top face (a lower slab's "top" is immediately
    # covered by the slab stacked on it, so there's no gap there to show one in)
    y = ymin
    for i, dielectric in enumerate(interposer):
        color = dielectric_color(dielectric)
        draw_side_face(xmin, flipy(y), w_total, -part_height, color)
        setPen(penBlack)
        setBrush(color)
        drawRect(xmin, flipy(y), w_total, -part_height)
        if i == len(interposer) - 1:
            draw_top_face(xmin, flipy(y), w_total, -part_height, color)
        interactive_entries.append({
            "kind": "dielectric",
            "key": dielectric.name,
            "rect": QRectF(xmin, flipy(y), w_total, -part_height).normalized(),
            "ref": dielectric,
            "tooltip": _build_dielectric_tooltip(dielectric),
            "chiplet_id": None,
        })
        drawText_center(xmin + 5, flipy(y), w_total - 10, part_height, dielectric.name)
        y += part_height

    setPen(penDivider)
    drawLine(xmin, flipy(y), xmax, flipy(y))

    # draw each chiplet's column, side by side, starting where the interposer stack ends
    for i, chiplet in enumerate(chiplets):
        x = xmin + i * (col_w + gutter)
        chip_dielectrics = sorted(chiplet.dielectrics, key=lambda d: d.zmin)
        col_part_height = column_height / len(chip_dielectrics)

        setPen(penBlack)
        # shifted up by DEPTH so the topmost slab's top-face extrusion (drawn below)
        # doesn't overlap the label text
        drawText_center(x, flipy(y + column_height) - DEPTH, col_w, HEADER_HEIGHT, chiplet.id)

        cy = y
        for j, dielectric in enumerate(chip_dielectrics):
            color = dielectric_color(dielectric)
            draw_side_face(x, flipy(cy), col_w, -col_part_height, color)
            setPen(penBlack)
            setBrush(color)
            drawRect(x, flipy(cy), col_w, -col_part_height)
            if j == len(chip_dielectrics) - 1:
                draw_top_face(x, flipy(cy), col_w, -col_part_height, color)
            interactive_entries.append({
                "kind": "dielectric",
                "key": dielectric.name,
                "rect": QRectF(x, flipy(cy), col_w, -col_part_height).normalized(),
                "ref": dielectric,
                "tooltip": _build_dielectric_tooltip(dielectric),
                "chiplet_id": chiplet.id,
            })
            # narrow columns have no room for the wide slab's left-name/right-material
            # two-sided label - centered name only, material stays available via tooltip
            drawText_center(x + 2, flipy(cy), col_w - 4, col_part_height, dielectric.name)
            cy += col_part_height

    return draw_calls, interactive_entries


def render_stackup_layout(draw_calls, painter, width, height):
    """Replays draw_calls (from compute_stackup_layout()) onto painter - the exact
    same QPainter calls the previous paintEvent()-based VectorWidget made directly."""
    painter.fillRect(QRectF(0, 0, width, height), Qt.white)
    painter.setRenderHint(QPainter.Antialiasing)
    for method_name, args in draw_calls:
        getattr(painter, method_name)(*args)


class StackupBackgroundItem(QGraphicsItem):
    """Renders the stackup cross-section preview's static visuals (dielectric slabs,
    metal/via boxes, labels, connector lines) by replaying draw_calls captured by
    compute_stackup_layout(). Kept separate from InteractiveRegionItem so hover/
    selection support never has to re-derive any of that layout math.
    """

    def __init__(self, draw_calls, width, height):
        super().__init__()
        self._draw_calls = draw_calls
        self._width = width
        self._height = height
        self.setZValue(-1)  # stay behind the interactive overlay items

    def boundingRect(self):
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter, option, widget=None):
        render_stackup_layout(self._draw_calls, painter, self._width, self._height)


class InteractiveRegionItem(QGraphicsRectItem):
    """Transparent overlay for one dielectric slab / metal / via box: gives it a
    click-triggered info flyout and native click-to-select highlighting, without
    StackupBackgroundItem's rendering having to know anything about interactivity.

    The info flyout is shown explicitly from mousePressEvent() (QToolTip.showText()),
    not via setToolTip() - setToolTip() would make Qt show it automatically on mere
    hover, which is deliberately not wanted here: the flyout should only appear when
    a shape is actually clicked.
    """

    _HIGHLIGHT_PEN = QPen(QColor(255, 140, 0), 3)
    # a dielectric whose resolved z-range overlaps another same-scope dielectric (see
    # dielectric_layers_list.find_z_overlap_pairs()) - distinct red/dashed vs. the orange/
    # solid selection highlight so both are visually distinguishable if a slab is both
    # selected and overlapping at once. Always drawn (not just on hover/click), unlike the
    # selection highlight - this needs to be visible without the user doing anything, since
    # it's the primary, contextual signal for what would otherwise only be a status-bar line
    _OVERLAP_PEN = QPen(QColor(220, 0, 0), 2, Qt.DashLine)

    def __init__(self, rect, kind, key, ref, tooltip, chiplet_id=None, has_overlap=False):
        super().__init__(rect)
        self.setPen(Qt.NoPen)
        self.setBrush(Qt.NoBrush)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.info_text = tooltip
        self.kind = kind          # "dielectric" or "layer"
        self.key = key            # element Name, matching row_elements lookup in the editor
        self.ref = ref            # dielectric_layer or metal_layer instance
        # None (interposer) or the active chiplet's id - only used to build VectorWidget's
        # _item_lookup compound key (see _rebuild_scene()), not part of the external
        # elementSelected/select_element(kind, name) contract, which stays plain-name
        self.chiplet_id = chiplet_id
        self.has_overlap = has_overlap

    def paint(self, painter, option, widget=None):
        # unselected/non-overlapping: draw nothing, StackupBackgroundItem already drew the
        # real colors/labels underneath.
        if self.has_overlap:
            painter.setPen(self._OVERLAP_PEN)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.rect())
        # Selected: a highlight outline instead of Qt's default dashed selection rectangle,
        # which would look wrong here - drawn last/on top so it stays visually dominant.
        if self.isSelected():
            painter.setPen(self._HIGHLIGHT_PEN)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.rect())

    def mousePressEvent(self, event):
        # just record pre-click state here; deciding to deselect has to wait
        # until mouseReleaseEvent (see below) - QGraphicsScene re-selects a
        # lone already-selected item on release (to support dragging a multi-
        # selection), which would silently undo a deselect made here on press
        self._was_selected_before_press = self.isSelected()
        super().mousePressEvent(event)  # keeps native click-to-select behavior

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if getattr(self, "_was_selected_before_press", False) and self.isSelected():
            # clicking (press+release) an already-selected shape deselects it,
            # instead of Qt's default of leaving a lone selected item selected
            # - matches Layout Preview's legend row click-to-toggle behavior
            self.setSelected(False)
        if self.isSelected() and self.info_text:
            QToolTip.showText(event.screenPos(), self.info_text)


class VectorWidget(QGraphicsView):
    """This widget draws the stackup preview, and supports hovering a shape for a
    tooltip and clicking a shape to select it (see elementSelected/select_element).

    The color/label logic for dielectrics and metals is genuinely different
    between setupEM (permittivity/sheet resistance) and setupThermal
    (thermal conductivity), so those bits are injected as callables instead
    of being hardcoded here:

        dielectric_color_fn(material) -> QColor
        dielectric_label_fn(dielectric, material) -> str
        metal_label_fn(metal, material, is_sheet) -> str
        via_label_suffix_fn(metal, material) -> str
        metal_color_fn(material) -> QColor | None (None = use the default
            hardcoded type-based color - setupEM/stackupEditor's standalone
            stand-in both return None; only setupThermal overrides this)
    """

    # emitted when a shape is clicked/selected in the preview: (kind, key), where
    # kind is "dielectric" or "layer" and key is the element's Name
    elementSelected = Signal(str, str)

    def __init__(self, materials_list, dielectrics_list, metals_list,
                 dielectric_color_fn, dielectric_label_fn,
                 metal_label_fn, via_label_suffix_fn, metal_color_fn):
        super().__init__()
        self.materials_list = materials_list
        self.dielectrics_list = dielectrics_list
        self.metals_list = metals_list
        self.dielectric_color_fn = dielectric_color_fn
        self.dielectric_label_fn = dielectric_label_fn
        self.metal_label_fn = metal_label_fn
        self.via_label_suffix_fn = via_label_suffix_fn
        self.metal_color_fn = metal_color_fn

        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        # StrongFocus (not the QGraphicsView default of NoFocus/ClickFocus ambiguity
        # across styles) so a click into the view reliably grabs keyboard focus - needed
        # for keyPressEvent()'s Left/Right chiplet-switching below to ever fire
        self.setFocusPolicy(Qt.StrongFocus)

        self._item_lookup = {}         # (kind, chiplet_id, key) -> InteractiveRegionItem
        self._item_by_kind_name = {}   # (kind, key) -> InteractiveRegionItem, for select_element()
        # which chiplet to show, for a stackup with more than one detected (see
        # compute_stackup_layout()'s active_chiplet_id) - None means "let
        # compute_stackup_layout() pick the first one", not "no chiplets"; irrelevant
        # (ignored) for an ordinary, non-chiplet stackup
        self._active_chiplet_id = None
        # True shows the simplified "topology overview" sketch (compute_topology_overview_
        # layout()) instead of the normal detailed cross-section (compute_stackup_layout())
        # - see set_topology_mode()
        self._topology_mode = False
        # True only while _rebuild_scene() is restoring a previous selection
        # programmatically (see below) - distinguishes that from a genuine user click, so
        # _on_scene_selection_changed()'s "clicking a chiplet in topology mode switches to
        # it" behavior doesn't misfire just because a chiplet-owned item happened to carry
        # its selection over into a freshly-entered topology view
        self._restoring_selection = False
        # set via set_chiplet_switcher() by whoever constructs this widget, once its
        # own ChipletSwitcher exists too - lets Left/Right (see keyPressEvent below)
        # drive the same single source of truth (the switcher's combo index) as
        # clicking the combo directly does, instead of a second, separately-tracked
        # "current chiplet" that could drift out of sync with the combo's own display
        self._chiplet_switcher = None
        scene = QGraphicsScene(self)
        self.setScene(scene)
        scene.selectionChanged.connect(self._on_scene_selection_changed)

        self._rebuild_scene()

    def refresh(self, materials_list, dielectrics_list, metals_list):
        """Replaces the stackup data and rebuilds the scene - the refresh entry point
        used by the editor every time the underlying XML changes (replaces the old
        "mutate materials_list/dielectrics_list/metals_list then call .update()"
        pattern, since there's no per-shape geometry to mutate in place anymore).
        """
        self.materials_list = materials_list
        self.dielectrics_list = dielectrics_list
        self.metals_list = metals_list
        self._rebuild_scene()

    def set_active_chiplet(self, chiplet_id):
        """Switches which chiplet is shown (called by a ChipletSwitcher combo box) and
        rebuilds the scene. A no-op-safe call on a stackup with 0 or 1 chiplets - it just
        gets ignored by compute_stackup_layout(), same as any other chiplet_id would be
        for such a file.
        """
        self._active_chiplet_id = chiplet_id
        self._rebuild_scene()

    def set_chiplet_switcher(self, chiplet_switcher):
        """Links this widget to its ChipletSwitcher combo box, so Left/Right (see
        keyPressEvent()) can step it - called once by whichever window constructs both
        (see PopUpWindow/StackupEditorWindow), right after building the switcher itself
        with this widget's set_active_chiplet as its callback.
        """
        self._chiplet_switcher = chiplet_switcher

    def set_topology_mode(self, enabled):
        """Switches between the normal cross-section preview and the "topology overview"
        sketch (see compute_topology_overview_layout()) and rebuilds the scene - called by
        ChipletSwitcher when its "Topology Overview" entry (always item 0) is selected/
        deselected.
        """
        self._topology_mode = bool(enabled)
        self._rebuild_scene()

    def keyPressEvent(self, event):
        if self._chiplet_switcher is not None and event.key() in (Qt.Key_Left, Qt.Key_Right):
            # steps through the switcher's combo - "Topology Overview" plus every real
            # chiplet, in that order - as one unified list
            self._chiplet_switcher.step(-1 if event.key() == Qt.Key_Left else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _rebuild_scene(self):
        # keep whatever was selected (by identity of (kind, chiplet_id, key), not by
        # item, since every item is recreated below) selected across the rebuild, so an
        # edit to the currently-selected layer doesn't make its preview highlight
        # vanish. Using the chiplet_id-qualified triple (not just (kind, key)) means a
        # selection made in one chiplet is deliberately NOT restored after switching to
        # a different chiplet - even if both happen to have a same-named element -
        # since re-selecting "the same name in a different, unrelated chiplet" would be
        # surprising, not helpful.
        previously_selected = self._selected_key()

        # compute_stackup_layout() is computed directly against the viewport's actual
        # pixel size (matching what the old paintEvent()-based widget did with
        # self.width()/self.height()), not a fixed logical canvas scaled to fit via
        # fitInView() - text is drawn at a plain, unscaled font size, and scaling a
        # smaller fixed canvas up/down to fit the actual (usually smaller) preview
        # window would have shrunk that text along with the boxes. Rebuilding the
        # layout on every resize (see resizeEvent below) costs a bit more than just
        # re-scaling a cached scene, but keeps text legible at any window size.
        width = max(self.viewport().width(), 1)
        height = max(self.viewport().height(), 1)

        scene = self.scene()
        scene.clear()
        self._item_lookup = {}
        self._item_by_kind_name = {}

        if self._topology_mode:
            draw_calls, interactive_entries = compute_topology_overview_layout(
                self.dielectrics_list, self.materials_list, width, height,
                self.dielectric_color_fn, self.dielectric_label_fn)
        else:
            draw_calls, interactive_entries = compute_stackup_layout(
                self.materials_list, self.dielectrics_list, self.metals_list,
                width, height,
                self.dielectric_color_fn, self.dielectric_label_fn,
                self.metal_label_fn, self.via_label_suffix_fn, self.metal_color_fn,
                active_chiplet_id=self._active_chiplet_id)

        scene.addItem(StackupBackgroundItem(draw_calls, width, height))

        for entry in interactive_entries:
            item = InteractiveRegionItem(entry["rect"], entry["kind"], entry["key"],
                                          entry["ref"], entry["tooltip"], entry["chiplet_id"],
                                          has_overlap=entry.get("has_overlap", False))
            scene.addItem(item)
            self._item_lookup[(entry["kind"], entry["chiplet_id"], entry["key"])] = item
            self._item_by_kind_name[(entry["kind"], entry["key"])] = item

        scene.setSceneRect(0, 0, width, height)

        if previously_selected is not None and previously_selected in self._item_lookup:
            self._restoring_selection = True
            try:
                self._item_lookup[previously_selected].setSelected(True)
            finally:
                self._restoring_selection = False

    def _selected_key(self):
        for triple, item in self._item_lookup.items():
            if item.isSelected():
                return triple
        return None

    def _on_scene_selection_changed(self):
        selected = self.scene().selectedItems()
        if not selected:
            # clicking empty background clears selection - dismiss any flyout left
            # showing from the previously-selected shape rather than stranding it
            QToolTip.hideText()
            # emit the "nothing selected" sentinel too, so listeners outside this
            # editor (e.g. Layout Preview's cross-window highlight) can tell a
            # clear apart from "no signal yet" - existing consumers of a real
            # (kind, key) pair already no-op safely on empty strings
            self.elementSelected.emit("", "")
            return
        item = selected[0]
        kind, key, chiplet_id = item.kind, item.key, item.chiplet_id
        if self._restoring_selection:
            # _rebuild_scene() is merely restoring whatever was already selected before
            # the rebuild - not a fresh user click - so this must not re-fire
            # elementSelected: a listener like StackupEditorWindow._on_preview_element_
            # selected() switches the editor's active tab to Dielectrics/Layers on every
            # emission, which would otherwise happen after *any* unrelated edit anywhere
            # in the editor (e.g. adding a Variable) as long as some shape was ever
            # selected in the preview earlier - yanking the user back out of whichever
            # tab they're actually working in. Nor does it qualify for the topology-mode
            # click-switch behavior below, for the same reason.
            return
        if (self._topology_mode and chiplet_id is not None
                and self._chiplet_switcher is not None):
            # clicking a chiplet's own slab in the topology overview switches straight to
            # that chiplet's detail view (dropdown included), instead of just selecting it
            # in place (the _restoring_selection case above already excludes a
            # programmatic selection-carryover from triggering this - e.g. a chiplet-owned
            # item selected just before switching into topology mode, and also present
            # there, would otherwise immediately bounce back out of the topology view the
            # user just chose). Deferred via QTimer.singleShot(0, ...): this handler is running
            # from inside the very item/scene the switch is about to tear down (switching
            # mode rebuilds the scene), so acting immediately would touch already-deleted
            # Qt objects once control returns to InteractiveRegionItem.mouseReleaseEvent()'s
            # own remaining code. elementSelected still ends up emitted for cross-window
            # sync (e.g. Layout Preview) once the deferred re-select below runs, via the
            # normal (non-topology) path this same handler takes for that new selection.
            QTimer.singleShot(0, self._deferred_switch_to_chiplet(chiplet_id, kind, key))
            return
        self.elementSelected.emit(kind, key)

    def _deferred_switch_to_chiplet(self, chiplet_id, kind, key):
        """Returns a zero-arg callable for QTimer.singleShot(0, ...) (see
        _on_scene_selection_changed()): switches out of topology mode into chiplet_id's
        detail view via the ChipletSwitcher (so its combo reflects the change too), then
        re-selects the same (kind, key) element there, carrying the highlight across the
        mode switch. No-ops safely if this widget was destroyed (window closed) before the
        deferred call fires.
        """
        def run():
            if not shiboken6.isValid(self):
                return
            self._chiplet_switcher.set_current_chiplet(chiplet_id)
            self.select_element(kind, key)
        return run

    def select_element(self, kind, name):
        """Selects/highlights the shape for (kind, name) - kind is "dielectric" or
        "layer". Called by the editor when a table row is selected, to keep the
        preview in sync with the table (also reached from a cross-window click in the
        Layout Preview - see _forward_stackup_selection_to_layout_preview()'s reverse
        direction). A no-op if that shape is already the sole selection, so this
        doesn't bounce back into elementSelected/the editor's own selection-changed
        handling.

        The Dielectrics/Layers tables list every element in the file regardless of
        chiplet, so (kind, name) may refer to an element that belongs to a chiplet
        other than the one currently shown - in that case, switch to the chiplet that
        actually contains it first (via the attached ChipletSwitcher, so its combo box
        stays the single source of truth - see ChipletSwitcher.set_current_chiplet()),
        then select it there.
        """
        item = self._item_by_kind_name.get((kind, name))
        if item is None:
            owning_chiplet_id = self._find_owning_chiplet_id(kind, name)
            if owning_chiplet_id is not None and owning_chiplet_id != self._active_chiplet_id:
                if self._chiplet_switcher is not None:
                    self._chiplet_switcher.set_current_chiplet(owning_chiplet_id)
                else:
                    self.set_active_chiplet(owning_chiplet_id)
                item = self._item_by_kind_name.get((kind, name))
        if self.scene().selectedItems() == ([item] if item is not None else []):
            return
        self.scene().clearSelection()
        if item is not None:
            item.setSelected(True)

    def _find_owning_chiplet_id(self, kind, name):
        """Which chiplet's subtree (kind, name) belongs to, or None if it's an
        interposer element (always shown, no switch needed) or doesn't exist at all -
        used by select_element() to jump to the right chiplet before selecting.
        """
        chiplet_groups = getattr(self.dielectrics_list, "chiplet_groups", None)
        if chiplet_groups is None:
            return None
        attr = "dielectrics" if kind == "dielectric" else "layers"
        for chiplet in chiplet_groups.chiplets:
            if any(element.name == name for element in getattr(chiplet, attr)):
                return chiplet.id
        return None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_scene()


class ChipletSwitcher(QWidget):
    """Combo box + "Chiplet N of M" label for picking which chiplet a Stackup Preview
    shows in detail, for a stackup where dielectric_layers_list.detect_chiplet_groups()
    found more than one chiplet sharing a common interposer base (see
    util_stackup_reader.py). "Topology Overview" (see compute_topology_overview_layout())
    is listed as the combo's first entry, alongside the real chiplets, rather than as a
    separate control - selecting it shows every chiplet at once instead of one in detail.
    Hidden entirely (never even shown) when there are 0 or 1 chiplet groups, since neither
    per-chiplet detail switching nor a topology overview is meaningful then - same "only
    offer a picker when there's a real choice" convention as field_viewer.py's Result File
    combo.
    """

    TOPOLOGY_LABEL = "Topology Overview"
    # unique per-class sentinel (not a chiplet id, which is always a real dielectric name)
    # marking "Topology Overview is/was the selection" - see set_groups()
    _TOPOLOGY = object()

    def __init__(self, on_chiplet_changed, on_topology_changed):
        """Args:
            on_chiplet_changed (callable): called with a chiplet id (str) whenever a real
              chiplet is selected - wire this straight to a VectorWidget's
              set_active_chiplet().
            on_topology_changed (callable): called with True when "Topology Overview" is
              selected, and False whenever switching away from it to a real chiplet - wire
              this straight to a VectorWidget's set_topology_mode().
        """
        super().__init__()
        self._on_chiplet_changed = on_chiplet_changed
        self._on_topology_changed = on_topology_changed
        self._chiplet_ids = []  # real chiplet ids only - combo index 0 is always Topology
                                 # Overview, so combo index i>=1 maps to _chiplet_ids[i-1]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("")
        layout.addWidget(self.label)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_index_changed)
        layout.addWidget(self.combo, 1)
        self.setVisible(False)

    def set_groups(self, groups):
        """Call whenever the stackup is (re)loaded - groups is a stackup's
        dielectrics_list.chiplet_groups (may be None, or have zero/one chiplets, in which
        case this stays/becomes hidden and does nothing else).

        In the Stackup Editor this runs after every single edit (see _refresh_preview()),
        not just an actual file (re)load - so it keeps showing whichever selection was
        already active - a chiplet by id, or Topology Overview - if it's still valid in the
        new grouping, rather than always snapping back to the first chiplet and disorienting
        the user mid-edit. A genuinely fresh start (no previous selection at all - opening/
        creating a multi-chiplet file for the first time in this widget) instead leads with
        Topology Overview, so the viewer sees the whole picture before drilling into any one
        chiplet. Falls back to the first real chiplet (not Topology Overview) when the
        previously-active chiplet no longer exists (e.g. renamed/removed), or when Topology
        Overview was active but chiplets just dropped below 2 (no longer a meaningful choice
        - matches Topology Overview effectively "closing" the same way this whole combo
        already hides itself in that case).
        """
        chiplets = groups.chiplets if groups is not None else []
        new_ids = [chiplet.id for chiplet in chiplets]

        old_index = self.combo.currentIndex()
        if self._chiplet_ids and old_index == 0:
            previous = self._TOPOLOGY
        elif self._chiplet_ids and 1 <= old_index <= len(self._chiplet_ids):
            previous = self._chiplet_ids[old_index - 1]
        else:
            previous = None

        self._chiplet_ids = new_ids

        if previous is self._TOPOLOGY and len(self._chiplet_ids) >= 2:
            index = 0
        elif previous in self._chiplet_ids:
            index = self._chiplet_ids.index(previous) + 1
        elif previous is None and len(self._chiplet_ids) >= 2:
            # a genuinely fresh start (no previous selection at all - e.g. this file was
            # just opened/created) leads with Topology Overview, so the viewer sees the
            # whole multi-chiplet picture before drilling into any one chiplet's detail.
            # Deliberately distinct from the next branch below: a previous selection that
            # existed but became invalid mid-edit (e.g. the active chiplet was just
            # renamed/removed) still falls back to a real chiplet, not Topology Overview -
            # jumping to a whole different view mode as a side effect of an unrelated edit
            # would be more surprising than helpful there.
            index = 0
        else:
            index = 1 if self._chiplet_ids else 0

        # setCurrentIndex() is also kept inside the signals-blocked region (not just
        # clear()/addItems()) - whether it actually changes anything is unpredictable
        # (Qt only emits when the resulting index differs from whatever clear()/
        # addItems() already left it at), so relying on it to reach _on_index_changed()
        # would sometimes fire the update and sometimes silently not; the explicit calls
        # below every time this method runs are the one reliable path instead.
        self.combo.blockSignals(True)
        self.combo.clear()
        if self._chiplet_ids:
            self.combo.addItem(self.TOPOLOGY_LABEL)
            self.combo.addItems(self._chiplet_ids)
            self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)

        self.setVisible(len(chiplets) > 1)
        if chiplets:
            self._update_label(index, len(chiplets))
            self._fire(index)
        elif previous is self._TOPOLOGY:
            # no chiplets left at all (not just fewer than 2) - there's no real chiplet
            # entry left to fire _fire() with, but we still owe VectorWidget an explicit
            # exit from Topology Overview, or it would stay stuck showing a sketch for a
            # chiplet grouping that no longer exists
            self._on_topology_changed(False)

    def _on_index_changed(self, index):
        if index < 0 or index > len(self._chiplet_ids):
            return
        self._update_label(index, len(self._chiplet_ids))
        self._fire(index)

    def _fire(self, index):
        if index == 0:
            self._on_topology_changed(True)
        else:
            self._on_topology_changed(False)
            self._on_chiplet_changed(self._chiplet_ids[index - 1])

    def _update_label(self, index, total):
        if index == 0:
            self.label.setText(f"{self.TOPOLOGY_LABEL} ({total} chiplets):")
        else:
            self.label.setText(f"Chiplet {index} of {total}:")

    def step(self, direction):
        """Moves the combo box by one entry (Topology Overview counts as one), wrapping
        around at either end - called by VectorWidget.keyPressEvent() for Left(-1)/
        Right(+1). Goes through setCurrentIndex() (not a direct call to the on_*_changed
        callbacks), so this stays the single source of truth: the combo's own display
        always matches what's actually shown, however the switch was triggered. A no-op
        with 0-1 chiplets, same as the combo being hidden then.
        """
        if len(self._chiplet_ids) < 2:
            return
        total_items = len(self._chiplet_ids) + 1  # +1 for the Topology Overview entry
        new_index = (self.combo.currentIndex() + direction) % total_items
        self.combo.setCurrentIndex(new_index)

    def set_current_chiplet(self, chiplet_id):
        """Programmatically switches to a specific real chiplet by id (never to Topology
        Overview) - called by VectorWidget.select_element() when a selection made
        elsewhere (a table row, or a cross-window click from the Layout Preview) refers to
        an element that isn't part of the currently active chiplet, or is part of one while
        Topology Overview is active. Goes through setCurrentIndex(), same
        single-source-of-truth reasoning as step(). A no-op if chiplet_id isn't a currently
        known chiplet.
        """
        if chiplet_id not in self._chiplet_ids:
            return
        self.combo.setCurrentIndex(self._chiplet_ids.index(chiplet_id) + 1)


class PopUpWindow(QDialog):
    """This window shows the substrate stackup preview.

    Uses MainWindow.stackup_dielectric_color / stackup_dielectric_label /
    stackup_metal_label / stackup_via_label_suffix hooks so the same window
    class works for both the EM (permittivity/Rs) and thermal (thermal
    conductivity) apps.

    Non-modal (see open_popup()'s lazy-singleton guard) so the user can keep
    it open side by side with the Layout Preview window - e.g. to pan/zoom
    there while clicking through layers here to see them highlighted.
    MainWindowBase.read_XML() pushes fresh materials_list/dielectrics_list/
    metals_list into this window's vector_widget (via VectorWidget.refresh(),
    the same call the Stackup Editor uses to stay live during its own edits)
    whenever the stackup is reloaded elsewhere, so it doesn't go stale while
    left open.
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
                                          via_label_suffix_fn=self.MainWindow.stackup_via_label_suffix,
                                          metal_color_fn=self.MainWindow.stackup_metal_color)
        self.chiplet_switcher = ChipletSwitcher(self.vector_widget.set_active_chiplet,
                                                 self.vector_widget.set_topology_mode)
        self.vector_widget.set_chiplet_switcher(self.chiplet_switcher)
        layout.addWidget(self.chiplet_switcher)

        self.chiplet_switcher.set_groups(self.MainWindow.dielectrics_list.chiplet_groups)
        layout.addWidget(self.vector_widget)

        # optional color-scale legend (e.g. setupThermal's thermal-conductivity
        # colorbar) - getattr'd rather than required on every MainWindow-like
        # object, since this is purely additive and most callers have nothing
        # to show here (color already comes straight from the XML's Color=)
        legend_fn = getattr(self.MainWindow, "stackup_color_legend", None)
        legend_widget = legend_fn() if legend_fn is not None else None
        if legend_widget is not None:
            layout.addWidget(legend_widget)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.setLayout(layout)

        # Ctrl+C copies the stackup cross-section (not the legend/Close button) to
        # the clipboard as an image - window-scoped (default QShortcut context) so
        # it fires regardless of which child widget currently has focus
        QShortcut(QKeySequence.Copy, self).activated.connect(
            lambda: QApplication.clipboard().setPixmap(self.vector_widget.grab()))


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
        self.targetdir_btn.setFixedWidth(SECONDARY_BUTTON_WIDTH)
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
        # Reserve the same width targetdir_btn ("Browse ...") occupies in the row
        # above, so modelname_edit's right edge lines up with targetdir_edit's -
        # and, in turn, with the Actions buttons' right edge - instead of stretching
        # further right just because this row has no trailing button of its own.
        # Uses an invisible placeholder widget (addWidget), not addSpacing(): a bare
        # addSpacing() doesn't get the automatic inter-item gap a real widget would,
        # so it ends up one layout-spacing() short of targetdir_btn's actual reserved
        # width, letting modelname_edit stretch a few pixels past targetdir_edit.
        self.modelname_spacer = QLabel("")
        self.modelname_spacer.setFixedWidth(SECONDARY_BUTTON_WIDTH)
        self.modelname_layout.addWidget(self.modelname_spacer)
        self.file_layout.addLayout(self.modelname_layout)

        self.file_group.setLayout(self.file_layout)

        # Actions group - kept visually separate (its own framed group) from the
        # input fields above. Preview/Create Mesh/Start Simulation/Terminate (plus,
        # in setupEM's subclass, View Results/Model Fit) all share this grid - a
        # QGridLayout keeps columns aligned across rows; independent QHBoxLayouts
        # can't guarantee that once some rows have two widgets (e.g. Start
        # Simulation/Terminate) and others have one (Preview/Create Mesh). Column 0
        # (primary actions) stretches to fill the remaining width so its right edge
        # lines up with targetdir_edit's right edge in the Output Files group above;
        # column 1 (secondary actions: Terminate/Model Fit) is a fixed
        # SECONDARY_BUTTON_WIDTH, matching targetdir_btn's "Browse ..." button, so
        # both group boxes present the same "wide field/button + narrow button"
        # proportions instead of an unrelated stretch ratio.
        self.actions_group = QGroupBox("Actions")
        self.actions_layout = QVBoxLayout()
        self.buttons_grid = QGridLayout()
        self.buttons_grid.setColumnStretch(0, 1)  # primary column: fills remaining width
        self.buttons_grid.setColumnStretch(1, 0)  # secondary column: fixed-width buttons only

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
        self.kill_btn.setFixedWidth(SECONDARY_BUTTON_WIDTH)
        self.kill_btn.clicked.connect(self.terminate_run)
        self.buttons_grid.addWidget(self.kill_btn, 2, 1)

        self.actions_layout.addLayout(self.buttons_grid)

        # Log area follows directly, no "Log file:" label - kept inside the Actions
        # frame (not its own group box) since it is the direct output of the actions
        # above (Preview/Create Mesh/Start Simulation), not an independent input
        # section, and the log content itself is self-explanatory.
        self.actions_layout.addSpacing(10)
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
        self.process.errorOccurred.connect(self.on_process_error)

    def on_modelname_edit_done(self):
        # Model name edit field has changed
        self.MainWindow.saved_values['model_basename'] = self.modelname_edit.text()

    def on_stdout(self):
        data = self.process.readAllStandardOutput().data().decode()
        for line in data.splitlines():
            if line.strip():  # Skip empty lines
                self.log_area.appendPlainText(line)
                self._on_stdout_line(line)

    def _on_stdout_line(self, line):
        """Hook called with each stdout line as it arrives, after it's appended to
        log_area. No-op here; overridden by setupEM.py's CreateModelTab to parse
        live solver progress (MPI/memory/port/AMR) out of Palace's log output.
        """
        pass

    def _reset_live_status(self):
        """Hook called whenever the loaded model changes (new *.py/*.simcfg loaded,
        or a fresh mesh/config is about to be created) - anywhere the previous
        run's status is no longer relevant to what's now loaded. No-op here;
        overridden by setupEM.py's CreateModelTab to clear the live solver-status
        line (MPI/memory/port/AMR) back to "n/a" rather than leave it showing a
        stale run's data for a model that's no longer the one on screen.
        """
        pass

    def on_stderr(self):
        data = self.process.readAllStandardError().data().decode()
        for line in data.splitlines():
            if line.strip():  # Skip empty lines
                self.log_area.appendPlainText(f"[Error] {line}")

    def on_finished(self, exit_code, exit_status):
        """Handle process completion."""
        self.log_area.appendPlainText(f"\n--- Process finished with exit code {exit_code} ---\n")

    def on_process_error(self, error):
        """Handle QProcess itself failing to launch or run - most importantly
        FailedToStart (program not found, or not executable, e.g. a .bat with content
        the current shell can't run). Without this, such failures were silent: none of
        readyReadStandardOutput/readyReadStandardError/finished ever fire for a process
        that never actually started, leaving the log blank with no indication anything
        went wrong.
        """
        messages = {
            QProcess.FailedToStart: "the program could not be started (not found, or not executable)",
            QProcess.Crashed: "the process crashed",
            QProcess.Timedout: "the process timed out",
            QProcess.WriteError: "an error occurred while writing to the process",
            QProcess.ReadError: "an error occurred while reading from the process",
            QProcess.UnknownError: "an unknown error occurred",
        }
        detail = messages.get(error, f"error code {error}")
        self.log_area.appendPlainText(f"\n⚠️ Process error: {detail}\n")

    def _open_in_paraview(self, file_paths, not_found_message):
        """Locate ParaView and open it on file_paths (a list of .pvd/.vtu paths), or
        log not_found_message if file_paths is empty. Shared by setupEM.py (Palace
        field dumps / Elmer EM fields) and setupThermal.py (thermal_results.vtu) -
        each caller is responsible for finding its own result files and wording its
        own "nothing found" message; this only handles locating/launching ParaView.
        Detached, non-blocking launch (like klayout_setupEM.py's setupEM launch) -
        this is a fire-and-forget external GUI viewer, unrelated to self.process's
        solver-run lifecycle.
        """
        if not file_paths:
            self.log_area.appendPlainText(not_found_message)
            return

        paraview_exe = find_paraview_exe()
        if paraview_exe is None:
            self.log_area.appendPlainText(
                "⚠️ ParaView not found on PATH. Install it, or add it to PATH, "
                "then open manually:\n" + "\n".join(file_paths) + "\n"
            )
            return

        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        try:
            subprocess.Popen([paraview_exe, *file_paths], env=env)
            self.log_area.appendPlainText("Starting ParaView on:\n" + "\n".join(file_paths) + "\n")
        except OSError as e:
            self.log_area.appendPlainText(f"⚠️ Failed to launch ParaView: {e}\n")

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

        # Strip a stale simulator-name prefix left over from a different solver choice
        # (e.g. "elmer_..." after switching to Palace mode) - but never strip a prefix
        # that already matches the current choice, otherwise importing an existing
        # model and reusing its filename would silently rename it to a different file
        # (setupThermal has no PalaceMode attribute at all; it's always Elmer, so only
        # a "palace_" prefix would ever be considered stale there).
        palace_mode = getattr(self.MainWindow, 'PalaceMode', False)
        mismatched_prefix = 'elmer_' if palace_mode else 'palace_'
        model_basename = model_basename.replace(mismatched_prefix, '')

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
                self._reset_live_status()  # a fresh mesh/config invalidates the last run's status
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

    # ---------- Start Simulation pre-flight checks ----------
    #
    # Shared by both apps' run_model() overrides (setupEM.py / setupThermal.py):
    # confirm the solver/toolchain is actually reachable *before* calling
    # QProcess.start(), with a clear, actionable log message and an early abort
    # if not - instead of an uncaught exception (missing run_sim/run_elmer) or
    # a generic "the program could not be started" line that doesn't say which
    # program or why. Log-panel-only (no QMessageBox), matching the one
    # pre-existing check of this kind (the Elmer/Windows MPI check this
    # replaces) - log_area sits in the same group box as the Start Simulation
    # button itself, so there's no visibility gap a modal would fix.

    @staticmethod
    def _windows_to_wsl_path(win_path):
        """Convert a Windows-style path like C:\\Users\\... into a WSL-style
        path like /mnt/c/Users/... . Needed both for the WSL pre-flight checks
        below and for the actual wsl.exe launch (setupEM.py's run_model()).
        """
        win_path = win_path.strip()
        if not win_path or ":" not in win_path:
            return win_path  # already looks like a Linux path, or invalid
        drive, rest = win_path.split(":", 1)
        drive = drive.lower()
        rest = rest.replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"

    def _check_run_script_ready(self, run_path, script_name):
        """Return the full path to script_name inside run_path if it's a real
        file, else log a "run Create Mesh first" message and return None.
        Fixes an uncaught FileNotFoundError os.chmod() would otherwise raise
        on Linux/Mac when "Create Mesh" was never run for this target dir/
        model name, and gives the same-quality message on Windows too
        (previously only caught generically, post-hoc, by on_process_error's
        "could not be started" line).
        """
        script_path = os.path.join(run_path, script_name)
        if not os.path.isfile(script_path):
            self.log_area.appendPlainText(
                f"⚠️ '{script_name}' not found in {run_path}.\n"
                "Click 'Create mesh and simulation settings file' first.\n"
            )
            return None
        return script_path

    def _check_command_on_path(self, command, hint, reason=None):
        """shutil.which() wrapper: return the resolved path if command is on
        PATH, else log a message and return None. `hint` is free-form
        guidance text (install link, PATH instructions, etc.). `reason`, if
        given, is prepended as "<reason>, but '<command>' was not found on
        PATH." instead of the bare "'<command>' was not found on PATH." -
        lets a check that only fires under an extra condition (e.g. MPI
        threads > 1) explain why it ran.
        """
        resolved = shutil.which(command)
        if resolved is None:
            lead = f"{reason}, but '{command}'" if reason else f"'{command}'"
            self.log_area.appendPlainText(
                f"⚠️ {lead} was not found on PATH.\n{hint}\n"
            )
            return None
        return resolved

    def _check_wsl_ready(self):
        """Windows-only: confirm wsl.exe is on PATH and at least one WSL
        distro is installed and enumerable. Returns True if both hold, else
        logs a message with the WSL install docs/command and returns False.
        Never raises: a hung/misbehaving wsl.exe is caught and logged, not
        left to crash the app or freeze it indefinitely.
        """
        if shutil.which("wsl.exe") is None and shutil.which("wsl") is None:
            self.log_area.appendPlainText(
                "⚠️ WSL (Windows Subsystem for Linux) was not found. Palace on "
                "Windows runs inside WSL - install it from an elevated PowerShell "
                "with 'wsl --install', restart Windows, then set up Palace inside "
                "WSL (see https://learn.microsoft.com/en-us/windows/wsl/install).\n"
            )
            return False

        env = os.environ.copy()
        # wsl.exe writes its own diagnostic/list output (like this -l -q
        # listing) as UTF-16LE by default when stdout isn't a real console -
        # WSL_UTF8=1 switches it to plain UTF-8. This only affects wsl.exe's
        # own text, not the piped stdout of a command run *inside* WSL (see
        # _check_wsl_commands_ready below, which needs no such handling).
        env["WSL_UTF8"] = "1"
        try:
            result = subprocess.run(
                ["wsl.exe", "-l", "-q"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, timeout=10,
            )
        except subprocess.TimeoutExpired:
            self.log_area.appendPlainText(
                "⚠️ 'wsl.exe -l -q' timed out; WSL may be in a bad state. Try "
                "'wsl --shutdown' in PowerShell, then retry.\n"
            )
            return False
        except OSError as e:
            self.log_area.appendPlainText(f"⚠️ Could not run wsl.exe: {e}\n")
            return False

        has_distro = bool(result.stdout.strip())
        if result.returncode != 0 or not has_distro:
            self.log_area.appendPlainText(
                "⚠️ WSL is installed, but no Linux distribution is set up in it. "
                "Run 'wsl --install' (or 'wsl --install -d Ubuntu') in an elevated "
                "PowerShell, then set up Palace inside that distro (see "
                "https://learn.microsoft.com/en-us/windows/wsl/install).\n"
            )
            return False
        return True

    def _check_wsl_commands_ready(self, wsl_path, commands):
        """Windows+Palace only: confirm each name in `commands` resolves
        inside a WSL login shell rooted at wsl_path, via the same 'bash -lc'
        invocation run_model() itself uses to launch run_sim - so PATH/
        ~/.profile is checked under the exact conditions the real run will
        use. Returns True only if every command resolves; else logs one
        combined message and returns False.
        """
        missing = []
        for command in commands:
            try:
                result = subprocess.run(
                    ["wsl.exe", "--cd", wsl_path, "--", "bash", "-lc", f"command -v {command}"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                self.log_area.appendPlainText(f"⚠️ Checking for '{command}' inside WSL timed out.\n")
                missing.append(command)
                continue
            except OSError as e:
                self.log_area.appendPlainText(f"⚠️ Could not check for '{command}' inside WSL: {e}\n")
                missing.append(command)
                continue
            if result.returncode != 0 or not result.stdout.strip():
                missing.append(command)

        if missing:
            self.log_area.appendPlainText(
                "⚠️ The following required command(s) were not found on PATH inside "
                "WSL: " + ", ".join(f"'{c}'" for c in missing) + ".\n"
                "Install Palace inside WSL (via apptainer/~/palace.sif, see "
                "https://awslabs.github.io/palace/stable/install/, or 'spack load "
                "palace'), and add the gds2palace scripts folder (run_palace, "
                "combine_snp) to PATH via ~/.profile inside WSL - see "
                "gds2palace's scripts/README.md.\n"
            )
            return False
        return True

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
        # set by load_configuration_from_file() when the imported *.py model script
        # is detected as an openEMS model (see is_openems_model_script()) - create_model()
        # in setupEM.py/setupThermal.py refuses to write its generated Palace/Elmer
        # code to this exact path, since setupEM can never regenerate an openEMS
        # script. Cleared/reset on every import (of either kind), not just set once.
        self.protected_source_model_path = None
        # normalized (os.path.normcase) output paths that create_model() has already
        # either confirmed overwriting (always asked, see create_model()) or itself
        # written to in this session - so the normal iterative workflow
        # (tweak -> Create Model -> tweak -> Create Model ...) against the same output
        # file only prompts once, not on every click. Session-only, not persisted.
        self.confirmed_overwrite_paths = set()

    # ---------- Drag & drop native config (*.simcfg/*.tsimcfg) or *.py model file
    # onto the window. Restricted to the "Input Files" tab so it doesn't fire while
    # the user is on another tab; within that tab, the GdsFile/XML fields have their
    # own FileDropLineEdit handling for .gds/.xml and ignore other extensions, so
    # drops on those fields still bubble up here for .simcfg/.tsimcfg/.py.
    def _droppable_file_from_drop(self, event):
        if not event.mimeData().hasUrls():
            return None
        tabs_widget = getattr(self, "tabs_widget", None)
        file_tab = getattr(self, "file_tab", None)
        if tabs_widget is None or file_tab is None or tabs_widget.currentWidget() is not file_tab:
            return None
        valid_suffixes = {"." + self.CONFIG_SUFFIX.upper(), ".PY"}
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and pathlib.Path(path).suffix.upper() in valid_suffixes:
                return path
        return None

    def dragEnterEvent(self, event):
        if self._droppable_file_from_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._droppable_file_from_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        file_path = self._droppable_file_from_drop(event)
        if file_path:
            event.acceptProposedAction()
            self.load_configuration_from_file(file_path)
        else:
            event.ignore()

    # ---------- Menu Bar ----------
    def create_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")

        # browse_action = QAction("Browse Config File...", self)
        self.new_action = QAction("New", self)
        self.new_action.setShortcut(QKeySequence.New)
        self.load_settings_action = QAction("Load Config ...", self)
        self.save_action = QAction("Save Config ...", self)
        self.load_default_action = QAction("Load Default Config", self)
        self.savedefault_action = QAction("Save as Default Config", self)
        self.import_model_action = QAction("Import from *.py model ...", self)
        self.export_model_action = QAction("Export to *.py model ...", self)
        self.preferences_action = QAction("Preferences ...", self)
        exit_action = QAction("Exit", self)

        # disable export by default, only enable when on Code tab
        self.export_model_action.setEnabled(False)

        self.new_action.triggered.connect(lambda: self.new_configuration())
        self.load_settings_action.triggered.connect(lambda: self.load_configuration_dialog())
        self.load_default_action.triggered.connect(lambda: self.load_configuration_from_file(self.DEFAULT_SETTINGS_FILE))
        self.save_action.triggered.connect(lambda: self.save_ask_filenamefile())
        self.savedefault_action.triggered.connect(lambda: self.save_user_inputs_to_file(self.DEFAULT_SETTINGS_FILE))

        self.import_model_action.triggered.connect(lambda: self.import_from_python())
        self.export_model_action.triggered.connect(lambda: self.export_to_python())
        self.preferences_action.triggered.connect(lambda: self.open_preferences_dialog())
        exit_action.triggered.connect(self.close)

        file_menu.addAction(self.new_action)
        file_menu.addSeparator()
        file_menu.addAction(self.load_settings_action)
        self.recent_settings_menu = file_menu.addMenu("Load Recent Config")
        file_menu.addAction(self.save_action)
        file_menu.addSeparator()
        file_menu.addAction(self.import_model_action)
        self.recent_model_menu = file_menu.addMenu("Import Recent Model")
        file_menu.addAction(self.export_model_action)
        file_menu.addSeparator()
        file_menu.addAction(self.load_default_action)
        file_menu.addAction(self.savedefault_action)
        file_menu.addSeparator()
        file_menu.addAction(self.preferences_action)
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

        self.layout_preview_action = QAction("Layout Preview...", self)
        self.layout_preview_action.triggered.connect(lambda: self.open_layout_preview())
        tools_menu.addAction(self.layout_preview_action)

        self.simplify_gds_action = QAction("Simplify GDS...", self)
        self.simplify_gds_action.triggered.connect(lambda: self.open_simplify_gds())
        tools_menu.addAction(self.simplify_gds_action)

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
    def get_setupEM_version(self):
        """Live __version__ of the setupEM package (this distribution) - not
           importlib.metadata.version("setupEM"), which is a static snapshot of
           the dist-info written at install time. For an editable
           ("pip install -e .") install, that snapshot goes stale the moment
           __version__ is bumped in the source afterward without reinstalling -
           confirmed in this workspace: metadata reported "0.3.13" while the
           live source was already at "0.6.2". That silently under-reports how
           current a local dev checkout is, and the version-check dialog would
           offer a "pip install --upgrade" that's actively wrong advice for an
           editable install. Falls back to importlib.metadata in the unlikely
           case setupEM can't be self-imported (e.g. setupEM.py run directly,
           unpackaged, with no setupEM package importable at all).
        """
        try:
            from . import __version__
            return __version__
        except ImportError:
            try:
                return importlib.metadata.version("setupEM")
            except importlib.metadata.PackageNotFoundError:
                return "unknown"

    def get_gds2palace_version(self):
        """Live gds2palace.__version__ - see get_setupEM_version() for why this
           is preferred over importlib.metadata.version("gds2palace") (the same
           staleness issue applies to an editable gds2palace install; confirmed
           in this workspace: metadata reported "0.3.6" while the live source
           was already at "0.4.1")."""
        version = getattr(gds2palace, "__version__", None)
        if version:
            return version
        try:
            return importlib.metadata.version("gds2palace")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

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

    def new_configuration(self):
        """File > New: reset to the same blank state as a freshly started app.
        Mirrors __init__'s startup sequence (empty saved_values, no ports/thermal
        objects, no cached stackup data) rather than loading any file, so every
        field falls back to its built-in/Preferences default exactly like a
        first launch would.
        """
        self.saved_values.clear()
        self.materials_list = None
        self.dielectrics_list = None
        self.metals_list = None
        self.update_target_layer_choices(None)
        self.apply_native_config_data({})
        self.load_all_tabs()
        self.create_model_tab.log_area.clear()
        self.create_model_tab._reset_live_status()

    def load_user_inputs(self, filename):
        # load of native configuration file
        if os.path.exists(filename):
            try:
                with open(filename, "r") as f:
                    return json.load(f)
            except Exception:
                QMessageBox.warning(self, "Error", f"Failed to load config from {filename}")
                return {}
        return {}

    def load_configuration_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Config File", filter=f"*.{self.CONFIG_SUFFIX};;Python model code *.py")
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
                    # GdsFile/SubstrateFile paths saved on a different OS/network-drive
                    # mapping often don't resolve here - fall back to a same-named file
                    # next to this settings file before populating the tabs with them
                    path_messages = resolve_missing_file_paths(saved_values, os.path.dirname(file_path))
                    # update ports/thermal objects, separate from the other internal data
                    self.apply_native_config_data(data)
                    self.load_all_tabs()
                    self._add_recent_file(RECENT_SETTINGS_KEY, file_path)
                    loaded_message = f"Config loaded from {shorten_path_for_display(file_path)}"
                    if path_messages:
                        loaded_message += "\n\n" + "\n".join(path_messages)
                    QMessageBox.information(self, "Loaded", loaded_message)
                    self.create_model_tab.log_area.clear()
                    self.create_model_tab._reset_live_status()
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
                    "amr_tol": "amr_tol",
                    "amr_max_dof": "amr_max_dof",
                    "order": "order",
                    "filled_metals": "filled_metals",
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
                # resolve simple module-level variables/expressions (e.g.
                # settings['fstart'] = ftarget, or settings['fpoint'] = [ftarget]) to
                # their literal value before the per-key type coercion below - see
                # collect_module_level_constants() / resolve_value_text()
                known_constants = collect_module_level_constants(file_path)
                imported_parameters = parse_assignments(file_path)
                for import_key, import_value in imported_parameters.items():
                        if import_key in import_mapping.keys():
                            if import_key not in import_value:  # skip the section where key might appear in different context
                                # get the internal name for this variable
                                varname = import_mapping.get(import_key, '')
                                if varname in ("fpoint", "fdump"):
                                    # same Hz-in-code / GHz-in-GUI unit split as fstart/fstop/fstep below,
                                    # just per-element since these are lists - but a script may also
                                    # assign a bare scalar here (e.g. "settings['fpoint'] = faked_dc"
                                    # instead of "[faked_dc]"), so normalize to a list first
                                    values = resolve_value_text(import_value, known_constants)
                                    if not isinstance(values, (list, tuple)):
                                        values = [values]
                                    saved_values[varname] = [f / 1e9 for f in values]
                                elif varname in ("variable_overrides", "refined_cellsize_override"):
                                    saved_values[varname] = resolve_value_text(import_value, known_constants)
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
                                        saved_values[varname] = float(resolve_value_text(raw, known_constants)) / 1e9
                                    elif varname == 'ELMER_MPI_THREADS':
                                        # MeshTab.load_values() does numeric comparisons
                                        # on this value directly, so it must be an int,
                                        # not the raw string parsed from the .py file
                                        saved_values[varname] = int(resolve_value_text(raw, known_constants))
                                    else:
                                        try:
                                            saved_values[varname] = resolve_value_text(raw, known_constants)
                                        except (SyntaxError, ValueError, TypeError, ZeroDivisionError):
                                            # not resolvable even with known module-level constants
                                            # (e.g. a value computed by a function call) - fall back to
                                            # the raw text exactly as before this resolution was added
                                            saved_values[varname] = raw

                # GdsFile/SubstrateFile paths saved on a different OS/network-drive mapping
                # often don't resolve here even as a full absolute path (the bare-relative-
                # path fallback above only fires when there's no directory component at
                # all) - fall back to a same-named file next to this model script instead
                path_messages = resolve_missing_file_paths(saved_values, modelcode_path)

                # openEMS models can never be reused as Create Model's output target -
                # see is_openems_model_script() / protected_source_model_path above
                is_openems_import = is_openems_model_script(file_path)
                self.protected_source_model_path = os.path.abspath(file_path) if is_openems_import else None

                # ask whether future "Create Model" output should overwrite this same
                # file, or start a fresh model (today's GDS-derived default) - only if
                # the user has turned this question on in Preferences > Files; by
                # default, silently agree (reuse the imported file) without asking.
                # Never offered for an openEMS import - setupEM can only ever generate
                # Palace/Elmer code, so reusing that path would silently destroy the
                # user's real openEMS solver script the next time "Create Model" runs
                # (create_model() also refuses the write directly, as a second layer,
                # in case the user manually re-selects the same path later).
                if is_openems_import:
                    reuse = False
                elif get_preference_bool(self.APP_NAME, "confirm_reuse_import_filename", False):
                    reuse = QMessageBox.question(
                        self, "Import Model",
                        f"Use '{os.path.basename(file_path)}' as the output file for this model too?\n\n"
                        "Yes: Create Model / Start Simulation will overwrite this file.\n"
                        "No: pick a model name and target directory on the Create Model(s) tab.",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    ) == QMessageBox.Yes
                else:
                    reuse = True
                if reuse:
                    saved_values['sim_path'] = os.path.dirname(file_path).replace('\\', '/')
                    saved_values['model_basename'] = pathlib.Path(file_path).stem
                    # the user has now explicitly designated this exact file as Create
                    # Model's output target (either by answering "Yes" above, or via
                    # the default silent-reuse when "Ask before reusing..." is off) -
                    # trust it immediately, so the very next Create Model click doesn't
                    # ALSO trigger the general "this will overwrite an existing file"
                    # confirmation in create_model() for the very file we were just
                    # told to use. Computed the same way create_model()
                    # builds pymodel_filename, so the two match exactly. Only reachable
                    # for non-openEMS imports (is_openems_import forces reuse=False
                    # above) - a file the user never explicitly pointed Create Model at
                    # this way still gets that confirmation.
                    reused_output_path = os.path.abspath(os.path.join(saved_values['sim_path'], saved_values['model_basename'] + '.py'))
                    self.confirmed_overwrite_paths.add(os.path.normcase(reused_output_path))

                # read port/thermal assignments in workflow syntax for gds2palace Python code, and
                # apply any app-specific post-import state (e.g. setupEM's simulator mode)
                self.apply_python_import_data(file_path)

                self.load_all_tabs()
                self._add_recent_file(RECENT_MODEL_KEY, file_path)
                loaded_message = f"Config loaded from {shorten_path_for_display(file_path)}"
                if is_openems_import:
                    loaded_message += (
                        "\n\nThis looks like an openEMS model script. Ports and settings "
                        "were imported for editing, but setupEM can only generate Palace/"
                        "Elmer models - Create Model will NOT overwrite this file. Pick a "
                        "new model name/output directory on the Create Model(s) tab."
                    )
                if path_messages:
                    loaded_message += "\n\n" + "\n".join(path_messages)
                QMessageBox.information(self, "Loaded", loaded_message)
                self.create_model_tab.log_area.clear()
                self.create_model_tab._reset_live_status()

            else:
                QMessageBox.information(self, "Error", f"Could not load file {file_path}")

    # ---------- recent files (Load Config / Import Model) ----------

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
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Model File", filter=f"*.py model code")
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
            QMessageBox.information(self, "Saved", f"Config saved to {filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save config to {filename}: {e}")

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
        file_path, _ = QFileDialog.getSaveFileName(self, "Select Config File", default_config, filter=f"{self.APP_NAME} (*.{self.CONFIG_SUFFIX})")
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
                # GUI with no attached console) can be shown to the user. SystemExit
                # itself carries no useful message (that's only ever in what was
                # printed), but a genuinely raised exception's own message must not
                # be silently dropped just because something was also printed first.
                printed = captured_stdout.getvalue().strip()
                if isinstance(e, SystemExit):
                    details = printed or str(e)
                else:
                    details = (printed + "\n\n" + str(e)) if printed else str(e)
                QMessageBox.critical(self, "Error", f"Could not load stackup {filename}:\n\n{details}")
                return  # keep last-known-good materials_list/dielectrics_list/metals_list
            self.materials_list, self.dielectrics_list, self.metals_list = materials_list, dielectrics_list, metals_list
            self.update_target_layer_choices(self.metals_list)
            self.file_tab.update_XML_description(filename)
            self.file_tab.update_variable_overrides_grid(filename)
            # keep an open Stackup Preview popup in sync - it otherwise reads
            # this data only once at construction and would silently go stale
            # if the stackup is reloaded (e.g. a different model/settings file
            # loaded, or the XML field edited) while it's still open
            if getattr(self, "popup", None) is not None:
                self.popup.vector_widget.refresh(materials_list, dielectrics_list, metals_list)
                self.popup.chiplet_switcher.set_groups(dielectrics_list.chiplet_groups)

    def get_gds_layers_in_range(self, layer_min, layer_max):
        """Return the set of GDS layer numbers in [layer_min, layer_max] that
        have at least one polygon on a datatype in the current purpose filter
        - "present" here means the same thing it would during a real model
        build (same cellname/purpose). Returns an empty set if the GDS file
        or stackup isn't loaded/valid, rather than raising - this is only
        used for Ports/Thermal tab UI hints (next-available-layer suggestion,
        "(missing in layout)" annotations), never anything simulation-critical.

        Reads the Input Files tab's *live* widgets rather than saved_values,
        which only gets populated once that tab has been left at least once -
        a Ports/Thermal tab reached before that would otherwise see an empty
        GdsFile and silently find nothing.

        Answered via gds_hierarchy_scan.layers_present_in_range() (walks the
        cell hierarchy directly, no flatten) rather than gds2palace's own
        gds_reader.read_gds(), which unconditionally flattens the whole
        chosen cell first - fine for building a real model, but this is
        called automatically and repeatedly just from browsing to a GDS file
        or switching to the Ports/Thermal tab (see refresh_source_layer_hints()/
        showEvent() in setupEM.py/setupThermal.py), so a densely-arrayed
        layout (fill patterns, via arrays) made that flatten cost tens of
        seconds per call, multiple times, before the user did anything else.
        Falls back to the original read_gds()-based computation only when a
        stackup "derived layer" (util_stackup_reader.derived_layer - a
        synthetic layer computed via boolean ops on other real layers, whose
        polygons don't exist as literal raw-GDS geometry) could have its
        output layer number inside [layer_min, layer_max] - the fast
        hierarchy walk can't see those, so it isn't safe to use there.
        """
        gdsfile = self.file_tab.gds_file_edit.text()
        if not os.path.isfile(gdsfile) or self.metals_list is None:
            return set()
        cellname = cellname_from_display(self.file_tab.cellname_box.currentText())
        purpose_text = self.file_tab.purpose_edit.text().strip()
        try:
            purposelist = ast.literal_eval('[' + purpose_text + ']') if purpose_text else [0]
        except Exception:
            purposelist = [0]

        if _derived_layer_range_is_safe(self.metals_list, layer_min, layer_max):
            try:
                return gds_hierarchy_scan.layers_present_in_range(
                    gdsfile, cellname, layer_min, layer_max, purposelist)
            except Exception:
                return set()

        # slow/exact path: only reached when a derived layer's synthetic
        # output number could fall inside [layer_min, layer_max] - unchanged
        # from the original implementation
        preprocess = self.file_tab.preprocess_gds_checkbox.isChecked()
        layernumbers = list(range(layer_min, layer_max + 1))
        captured_stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured_stdout):
                allpolygons = gds_reader.read_gds(
                    gdsfile, layernumbers,
                    cellname=cellname,
                    purposelist=purposelist,
                    metals_list=self.metals_list,
                    preprocess=preprocess,
                    merge_polygon_size=0, mirror=False, offset_x=0, offset_y=0,
                    layernumber_offset=0)
        except (Exception, SystemExit):
            return set()
        return {int(poly.layernum) for poly in allpolygons.polygons}

    def update_target_layer_choices(self, metals_list):
        # Hook: push the metal list to the app-specific tab that offers
        # target-layer choices (ports tab / thermal objects tab).
        raise NotImplementedError

    def refresh_source_layer_hints(self):
        """Hook: re-check GDS-layer-derived hints (missing-in-layout
        annotations, next-available-source-layer suggestion) on the
        app-specific ports/thermal tab. update_target_layer_choices() already
        covers this after a stackup (re)load, but setting the GDS file alone
        doesn't trigger that - FileInputTab.set_gds_file() calls this
        separately so those hints aren't left stale until the user happens to
        leave/re-enter the Input Files tab (or never, e.g. after the -gdsfile
        CLI startup flag). No-op by default.
        """
        pass

    def open_preferences_dialog(self):
        # Hook: build and show the app-specific Preferences dialog (its tabs/
        # fields differ enough between setupEM and setupThermal - e.g. only
        # setupEM has a Frequencies tab - that each app implements its own
        # PreferencesDialog class rather than sharing one here).
        raise NotImplementedError

    def open_popup(self):
        if getattr(self, "popup", None) is not None:
            self.popup.raise_()
            self.popup.activateWindow()
            return

        if os.path.isfile(self.saved_values["SubstrateFile"]):
            self.popup = PopUpWindow(self)
            self.popup.vector_widget.elementSelected.connect(self._forward_stackup_selection_to_layout_preview)
            self.popup.destroyed.connect(lambda: setattr(self, "popup", None))
            # also clear the Layout Preview highlight when this window goes
            # away, since there's no longer a visible "what's selected"
            # context once it's closed
            self.popup.destroyed.connect(lambda: self._forward_stackup_selection_to_layout_preview("", ""))
            self.popup.show()
        else:
            QMessageBox.warning(self, "Error", "Substrate file not found")

    def _forward_stackup_selection_to_layout_preview(self, kind, key):
        """Slot for VectorWidget.elementSelected, connected from both the Stackup
        Preview popup and the Stackup Editor (they share the same VectorWidget
        class/signal shape) - mirrors the selected metal/via layer as a white
        outline in the Layout Preview window, if one is currently open. A
        Dielectric has no GDS polygon of its own, UNLESS it declares an
        optional Boundary layer (its lateral extent, drawn in GDSII rather
        than defaulting to the full simulation domain) - resolved to that
        layer's Layout Preview name via _dielectric_boundary_layer_name().
        Anything else (a plain Dielectric, or an empty selection) resolves to
        "no highlight" same as an empty selection.
        """
        self._stackup_selection = (kind, key)
        if getattr(self, "layout_preview_window", None) is None:
            return
        name = None
        if kind == "layer" and key:
            name = key
        elif kind == "dielectric" and key:
            name = self._dielectric_boundary_layer_name(key)
        self.layout_preview_window.set_highlighted_layer(name)

    def _dielectric_boundary_layer_name(self, dielectric_name):
        """Resolve a Dielectric's optional Boundary GDS layer number to the
        display name Layout Preview's legend uses for it: a real metal/via
        <Layer>'s own name, if the same GDS layer also happens to be drawn as
        one, else the "Layer N" fallback Layout Preview falls back to for a
        layer with no <Layer> entry of its own. None if the dielectric has no
        Boundary, or isn't found. Shared by both highlight-forwarding
        directions between Stackup Preview/Editor and Layout Preview.
        """
        if self.dielectrics_list is None:
            return None
        dielectric = next((d for d in self.dielectrics_list.dielectrics if d.name == dielectric_name), None)
        if dielectric is None or dielectric.gdsboundary is None:
            return None
        layernum = int(dielectric.gdsboundary)
        metal = self.metals_list.getbylayernumber(layernum) if self.metals_list is not None else None
        return metal.name if metal is not None else f"Layer {layernum}"

    def _forward_layout_selection_to_stackup(self, layernum):
        """Slot for LayoutPreviewWindow.layerSelected - mirrors a layer
        selected directly in Layout Preview's own legend into the Stackup
        Preview/Editor, the reverse of _forward_stackup_selection_to_layout_preview()
        above. Resolves the GDS layer to a real metal/via <Layer> if it has
        one, else to a Dielectric that uses it as its lateral Boundary, if
        any - more than one Dielectric can share the same Boundary layer
        (e.g. several dielectrics all bounded by the same "die outline"
        layer), so pick the one with the smallest zmin (the lowest one in the
        stack) rather than an arbitrary/file-order match - only applied if the
        Stackup Preview and/or Editor is already open (mirroring the other
        direction, which never auto-opens Layout Preview either); does not
        open either one on its own.
        """
        kind, key = "", ""
        if layernum is not None:
            metal = self.metals_list.getbylayernumber(layernum) if self.metals_list is not None else None
            if metal is not None:
                kind, key = "layer", metal.name
            elif self.dielectrics_list is not None:
                candidates = [d for d in self.dielectrics_list.dielectrics
                              if d.gdsboundary is not None and int(d.gdsboundary) == layernum]
                if candidates:
                    dielectric = min(candidates, key=lambda d: d.zmin)
                    kind, key = "dielectric", dielectric.name

        if getattr(self, "popup", None) is not None:
            self.popup.vector_widget.select_element(kind, key)
        if getattr(self, "stackup_editor_window", None) is not None:
            self.stackup_editor_window.vector_widget.select_element(kind, key)

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
        self.stackup_editor_window.vector_widget.elementSelected.connect(self._forward_stackup_selection_to_layout_preview)
        self.stackup_editor_window.destroyed.connect(lambda: setattr(self, "stackup_editor_window", None))
        self.stackup_editor_window.destroyed.connect(lambda: self._forward_stackup_selection_to_layout_preview("", ""))
        self.stackup_editor_window.show()

    def get_layout_preview_markers(self):
        """Hook: return the current list of "marker" objects to highlight in the
        Layout Preview window on top of the GDS layers - EM ports for setupEM,
        thermal sources/constant-temperature boundaries for setupThermal. Each
        item is a dict with at least "source_layernum" (the GDS layer its
        marker geometry lives on), "kind" (a short tag - "port", "source",
        "boundary" - that layout_preview.py uses to pick a label/marker style),
        and "group" (the legend section label to place it under, e.g. "Ports",
        "Sources", "Boundaries" - kept separate per the app's own concept, not
        merged into one section). Remaining keys are kind-specific: ports carry
        the same fields as simulation_ports_to_struct() in setupEM.py
        (portnumber, direction, voltage, ...); thermal objects carry the same
        fields as thermal_objects_to_struct() in setupThermal.py (type, plus
        power or temp).

        Default here is "no markers"; setupEM.py's and setupThermal.py's
        MainWindow both override this with their real data - same injection
        pattern as VectorWidget's dielectric_color_fn/metal_label_fn hooks
        above.
        """
        return []

    def open_layout_preview(self):
        # local import: layout_preview.py imports from this module (MainWindowBase),
        # so importing it at module load time here would be circular.
        if __package__ in (None, ""):
            from layout_preview import LayoutPreviewWindow
        else:
            from .layout_preview import LayoutPreviewWindow

        if getattr(self, "layout_preview_window", None) is not None:
            # re-read everything on every deliberate "go check the layout"
            # action, rather than silently showing whatever it last had -
            # unlike the Stackup Preview popup, this re-reads the whole GDS
            # file from disk, so it only happens here (an explicit menu
            # click), not automatically on every stackup/tab change
            self.layout_preview_window.refresh()
            self.layout_preview_window.raise_()
            self.layout_preview_window.activateWindow()
            return

        self.layout_preview_window = LayoutPreviewWindow(self)
        self.layout_preview_window.destroyed.connect(lambda: setattr(self, "layout_preview_window", None))
        self.layout_preview_window.layerSelected.connect(self._forward_layout_selection_to_stackup)
        # sync immediately to whatever's already selected in an open Stackup
        # Preview/Editor, rather than waiting for the next selection change
        kind, key = getattr(self, "_stackup_selection", ("", ""))
        self._forward_stackup_selection_to_layout_preview(kind, key)
        self.layout_preview_window.show()

    def open_simplify_gds(self):
        # local import: simplify_gds.py imports gds_prepare_for_EM lazily
        # inside run_simplify() too, but import the dialog module itself here
        # (not at module load time) for the same circular-import reason as
        # open_stackup_editor()/open_layout_preview() above
        if __package__ in (None, ""):
            from simplify_gds import SimplifyGdsDialog
        else:
            from .simplify_gds import SimplifyGdsDialog

        if getattr(self, "simplify_gds_window", None) is not None:
            self.simplify_gds_window.raise_()
            self.simplify_gds_window.activateWindow()
            return

        gds_path = self.saved_values.get("GdsFile") if isinstance(self.saved_values, dict) else None
        if not gds_path or not os.path.isfile(gds_path):
            QMessageBox.warning(self, "Error", "Load a GDSII file on the Input Files tab first")
            return
        if self.metals_list is None:
            QMessageBox.warning(self, "Error", "Load an XML stackup file on the Input Files tab first")
            return

        self.simplify_gds_window = SimplifyGdsDialog(self)
        self.simplify_gds_window.destroyed.connect(lambda: setattr(self, "simplify_gds_window", None))
        self.simplify_gds_window.show()
