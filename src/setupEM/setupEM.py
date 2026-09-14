########################################################################
#
# Copyright 2025 Volker Muehlhaus and IHP PDK Authors
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


import sys, json, os, ast, webbrowser, argparse, shutil, re, glob, subprocess
import numpy as np
import importlib.metadata
import importlib.util
import requests
import gdspy
from scipy.interpolate import interp1d
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit,QComboBox,QTableWidget,QHeaderView,
    QPushButton, QFileDialog, QTabWidget, QMessageBox, QGroupBox,
    QCheckBox, QAbstractItemView,QStyleFactory,QTableWidgetItem, QPlainTextEdit, QDialog,
    QDialogButtonBox,
    )
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QFont, QFontMetrics, QSyntaxHighlighter, QPainter, QPen, QActionGroup
from PySide6.QtCore import Qt, QRegularExpression, QProcess, QRect, QStandardPaths, QTimer


# Local dev: if the gds2palace_ihp_sg13g2 fork is checked out as a sibling repo next to
# this one (.../setupEM and .../gds2palace_ihp_sg13g2 sharing a parent directory), prefer
# its workflow/gds2palace over any pip-installed gds2palace, so local fork changes are
# picked up without a separate editable install. No-op (falls through to the installed
# module) if that sibling checkout isn't present.
_dev_gds2palace_dir = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'gds2palace_ihp_sg13g2', 'workflow'))
if os.path.isdir(os.path.join(_dev_gds2palace_dir, 'gds2palace')):
    sys.path.insert(0, _dev_gds2palace_dir)
from gds2palace import *

# shared building blocks used by both setupEM.py and setupThermal.py
# __package__ is None/"" when this file is run directly (e.g. `python setupEM.py`)
# rather than imported as part of the setupEM package, so relative import fails.
if __package__ in (None, ""):
    from setup_common import (
        EDIT_STYLE_OPTIONAL, EDIT_STYLE_REQUIRED, COMBO_STYLE_REQUIRED, COMBO_STYLE_OPTIONAL,
        SECONDARY_BUTTON_WIDTH,
        FileDropLineEdit, FileInputTab, PythonHighlighter, CodeEditor,
        VectorWidget, PopUpWindow, CreateModelTabBase, MainWindowBase,
        epsilon_to_color, default_stackup_dielectric_label, default_stackup_metal_label,
        next_available_source_layer, update_missing_layer_column,
        get_preference, get_preference_bool, set_preference, clear_preferences,
        eval_simple_python_expression, collect_module_level_constants,
        find_paraview_exe,
    )
    from palace_results import build_results_summary, find_output_dir, find_paraview_files
else:
    from .setup_common import (
        EDIT_STYLE_OPTIONAL, EDIT_STYLE_REQUIRED, COMBO_STYLE_REQUIRED, COMBO_STYLE_OPTIONAL,
        SECONDARY_BUTTON_WIDTH,
        FileDropLineEdit, FileInputTab, PythonHighlighter, CodeEditor,
        VectorWidget, PopUpWindow, CreateModelTabBase, MainWindowBase,
        epsilon_to_color, default_stackup_dielectric_label, default_stackup_metal_label,
        next_available_source_layer, update_missing_layer_column,
        get_preference, get_preference_bool, set_preference, clear_preferences,
        eval_simple_python_expression, collect_module_level_constants,
        find_paraview_exe,
    )
    from .palace_results import build_results_summary, find_output_dir, find_paraview_files


'''
Ubuntu 24.04 Notice:

Error message:
qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform plugin. qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.

Solution:
sudo apt update
sudo apt install libxcb-cursor0 libxcb-xinerama0 libxcb-xkb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xinput0 libxcb-xv0 libxcb-util1 libxkbcommon-x11-0

'''


CONFIG_SUFFIX = "simcfg"  # file suffix for native config file used here
APP_NAME = "setupEM" # name of this application

DEFAULT_SETTINGS_FILE = os.path.join(os.path.expanduser("~"),"default." + CONFIG_SUFFIX)


saved_values = {} # dictionary of user input in this application
simulation_ports = simulation_setup.all_simulation_ports() # store port settings



def simulation_ports_to_struct (simulation_ports):
    # convert simulation ports to a strcuture that can be serialized to JSON output
    all_ports_list = []
    for sim_port in simulation_ports.ports:
        port = {}
        port['portnumber'] = sim_port.portnumber
        port['source_layernum'] = sim_port.source_layernum
        port['target_layername'] = sim_port.target_layername
        port['from_layername'] = sim_port.from_layername
        port['to_layername'] = sim_port.to_layername
        port['direction'] = sim_port.direction
        port['port_Z0'] = sim_port.port_Z0
        port['voltage'] = sim_port.voltage
        all_ports_list.append(port)
    return all_ports_list



# ---------- OTHER TABS ----------
class FrequenciesTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow # parent = MainWindow

        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignTop)

        # ---------- SWEEP GROUP ----------
        self.sweep_group = QGroupBox("Adaptive frequency sweep")
        self.sweep_layout = QHBoxLayout()

        self.start_layout = QVBoxLayout()
        self.start_layout.addWidget(QLabel("fstart [GHz]"))
        self.start_edit = QLineEdit("0")
        self.start_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.start_layout.addWidget(self.start_edit)
        self.sweep_layout.addLayout(self.start_layout)

        self.stop_layout = QVBoxLayout()
        self.stop_layout.addWidget(QLabel("fstop [GHz]"))
        self.stop_edit = QLineEdit("50")
        self.stop_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.stop_layout.addWidget(self.stop_edit)
        self.sweep_layout.addLayout(self.stop_layout)

        self.step_layout = QVBoxLayout()
        self.step_layout.addWidget(QLabel("fstep [GHz], optional"))
        self.step_edit = QLineEdit("")
        self.step_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.step_layout.addWidget(self.step_edit)
        self.sweep_layout.addLayout(self.step_layout)

        self.sweep_group.setLayout(self.sweep_layout)


        # ---------- DISCRETE GROUP ----------
        self.discrete_group = QGroupBox("Optional list of fixed frequencies")
        self.discrete_layout = QHBoxLayout()

        self.fpoint_layout = QVBoxLayout()
        self.fpoint_layout.addWidget(QLabel("fpoint [GHz], values separated by comma"))
        self.fpoint_edit = QLineEdit("")
        self.fpoint_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.fpoint_layout.addWidget(self.fpoint_edit)
        self.discrete_layout.addLayout(self.fpoint_layout)

        self.discrete_group.setLayout(self.discrete_layout)


        # ---------- DUMP GROUP ----------
        self.dump_group = QGroupBox("Optional list of fixed frequencies creating field dump data for visualization (Paraview files))")
        self.dump_layout = QHBoxLayout()

        self.fdump_layout = QVBoxLayout()
        self.fdump_label = QLabel("fdump [GHz], values separated by comma")
        self.fdump_layout.addWidget(self.fdump_label)
        self.fdump_edit = QLineEdit("")
        self.fdump_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.fdump_layout.addWidget(self.fdump_edit)

        # Elmer has no per-sample SaveStep like Palace - any fdump value turns on
        # field-dump output at every solved frequency (sweep + fpoint together), so a
        # per-frequency list is misleading there. Elmer mode shows this checkbox instead
        # of fdump_edit; only one of the two is ever visible, toggled in setPalaceMode()/
        # setElmerMode(). Both widgets always exist so mode switches are just a visibility
        # flip, no rebuilding.
        self.fdump_enabled_checkbox = QCheckBox("Enable field dump at all frequencies")
        self.fdump_layout.addWidget(self.fdump_enabled_checkbox)

        self.dump_layout.addLayout(self.fdump_layout)
        self.dump_group.setLayout(self.dump_layout)

        self.main_layout.addWidget(self.sweep_group)
        self.main_layout.addSpacing(20)
        self.main_layout.addWidget(self.discrete_group)
        self.main_layout.addSpacing(20)
        self.main_layout.addWidget(self.dump_group)
        self.main_layout.addStretch()
        self.setLayout(self.main_layout)


    def save_values(self):
        # fstart/fstop may be left empty only if fpoint or fdump supplies at least
        # one frequency instead (gds2palace treats fstart/fstop as fully optional)
        fdump_given = (self.fdump_enabled_checkbox.isChecked() if self.MainWindow.ElmerMode
                       else self.fdump_edit.text() != "")
        discrete_freqs_given = (self.fpoint_edit.text() != "" or fdump_given)

        fstart = None
        if self.start_edit.text() == "":
            if discrete_freqs_given:
                saved_values.pop("fstart", None) # don't leave stale sweep data behind
            else:
                QMessageBox.warning(self, "Error", "Not a valid value for fstart")
                self.start_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "fstart", "0")))
                return False
        else:
            try:
                fstart = float(self.start_edit.text())
            except Exception:
                QMessageBox.warning(self, "Error", "Not a valid value for fstart")
                self.start_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "fstart", "0")))
                return False
            saved_values ["fstart"] = fstart

        fstop = None
        if self.stop_edit.text() == "":
            if discrete_freqs_given:
                saved_values.pop("fstop", None) # don't leave stale sweep data behind
            else:
                QMessageBox.warning(self, "Error", "Not a valid value for fstop")
                self.stop_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "fstop", "50")))
                return False
        else:
            try:
                fstop = float(self.stop_edit.text())
            except Exception:
                QMessageBox.warning(self, "Error", "Not a valid value for fstop")
                self.stop_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "fstop", "50")))
                return False
            saved_values ["fstop"] = fstop

        if self.step_edit.text() != "":
            try:
                fstep = float(self.step_edit.text())
                zerocheck = 1 / fstep # raise an exception if zero
            except Exception:
                QMessageBox.warning(self, "Error", "Not a valid value for fstep")
                self.step_edit.setText("")
                return False
            saved_values ["fstep"] = float(fstep)
        else:
            saved_values.pop("fstep",None) # delete key

        text = self.fpoint_edit.text()
        if text != "":
            # save as list of comma separated values
            saved_values ["fpoint"] = ast.literal_eval('['+text+']')
        else:
            saved_values.pop("fpoint",None) # delete key

        if self.MainWindow.ElmerMode:
            saved_values["fdump_enabled"] = self.fdump_enabled_checkbox.isChecked()
            # don't touch saved_values["fdump"] here - it's Palace-only data (a real
            # frequency list) and must survive a round-trip through Elmer mode untouched
            have_sweep = ("fstart" in saved_values and "fstop" in saved_values)
            have_fpoint = bool(saved_values.get("fpoint"))
            if self.fdump_enabled_checkbox.isChecked() and not have_sweep and not have_fpoint:
                QMessageBox.warning(self, "Error",
                    "Field dump needs at least one frequency: set fstart/fstop or fpoint first.")
                self.fdump_enabled_checkbox.setChecked(False)
                saved_values["fdump_enabled"] = False
                return False
        else:
            text = self.fdump_edit.text()
            if text != "":
                saved_values ["fdump"] = ast.literal_eval('['+text+']')
            else:
                saved_values.pop("fdump",None)
            # don't touch saved_values["fdump_enabled"] here either, same reasoning
        # "View fields ..." is only relevant once fdump is set
        self.MainWindow.create_model_tab._update_viewer_button()

        # if fstart == fstop == fdump or fstart == fstop == fstep, then remove fstart, fstop
        if fstart is not None and fstop is not None and fstart == fstop:
            discrete_list1 = saved_values.get("fpoint", [])
            # fdump no longer holds real per-frequency data in Elmer mode, and collapsing
            # the sweep away here would remove the very fstop value the field-dump
            # checkbox's generated code depends on (see ModelEditorTab.create_model_text)
            discrete_list2 = [] if self.MainWindow.ElmerMode else saved_values.get("fdump", [])
            if fstart in discrete_list1 or fstart in discrete_list2:
                saved_values.pop("fstart", None)
                saved_values.pop("fstop", None)
                saved_values.pop("fstep", None)
                self.start_edit.setText("")
                self.stop_edit.setText("")
                self.step_edit.setText("")


        return True  # Tab change only possible when returning True

    def load_values(self):
        # if fstart/fstop were intentionally omitted in favor of fpoint/fdump, keep the
        # fields blank on reload instead of repopulating the "0"/"50" sweep defaults
        fdump_given = saved_values.get("fdump_enabled", False) if self.MainWindow.ElmerMode \
                      else ("fdump" in saved_values)
        discrete_freqs_given = ("fpoint" in saved_values) or fdump_given
        fstart_default = "" if discrete_freqs_given else str(get_preference(self.MainWindow.APP_NAME, "fstart", "0"))
        fstop_default  = "" if discrete_freqs_given else str(get_preference(self.MainWindow.APP_NAME, "fstop", "50"))
        self.start_edit.setText(str(saved_values.get("fstart",fstart_default)))
        self.stop_edit.setText(str(saved_values.get("fstop",fstop_default)))
        self.step_edit.setText(str(saved_values.get("fstep","")))

        float_list  = saved_values.get("fpoint","")
        self.fpoint_edit.setText(','.join(map(str, float_list)))

        float_list  = saved_values.get("fdump","")
        self.fdump_edit.setText(','.join(map(str, float_list)))
        self.fdump_enabled_checkbox.setChecked(bool(saved_values.get("fdump_enabled", False)))


class PortsTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow # parent = MainWindow
        self.main_layout = QVBoxLayout()


        self.top_group = QGroupBox("Port settings")
        self.top_layout = QVBoxLayout()

        self.bottom_group = QGroupBox("Port overview")
        # self.left_group.setFixedWidth(100)
        self.bottom_layout = QVBoxLayout()


        # port list is bottom group

        self.portslist = QTableWidget()
        self.portslist.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.portslist.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.portslist.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bottom_layout.addWidget(self.portslist)

        self.portslist.setColumnCount(8)
        self.portslist.setHorizontalHeaderLabels(["Z0", "Voltage", "Source layer", "Target layer", "From layer", "To layer","Direction",""])
        header = self.portslist.horizontalHeader()
        for col in range(self.portslist.columnCount()-1):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

        self.portslist.setRowCount(32)
        self.portslist.selectRow(0)


        # details rigis top group
        self.details_layout = QVBoxLayout()

        left_label_width = 230

        self.sourcelayer_layout =  QHBoxLayout()
        label = QLabel("Port geometry on layer number")
        self.sourcelayer_layout.addWidget(label)
        label.setFixedWidth(left_label_width)
        # no hardcoded "201" default - selectRow(0) below fires before the
        # itemSelectionChanged connection even exists, so this would otherwise
        # sit unrefreshed (looking like a real suggestion) until the user
        # happens to select a different row and back; update_layers() below
        # recomputes it for real once a GDS file is actually loaded
        self.sourcelayer_edit = QLineEdit("")
        self.sourcelayer_edit.setFixedWidth(80)
        self.sourcelayer_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.sourcelayer_layout.addWidget(self.sourcelayer_edit)
        label = QLabel(" in GDSII file")
        self.sourcelayer_layout.addWidget(label)
        self.sourcelayer_layout.addStretch()
        self.details_layout.addLayout(self.sourcelayer_layout)

        self.direction_layout =  QHBoxLayout()
        label = QLabel("Port direction")
        self.direction_layout.addWidget(label)
        label.setFixedWidth(left_label_width)
        self.direction_box = QComboBox()
        self.direction_box.setFixedWidth(80)
        self.direction_box.setStyleSheet(COMBO_STYLE_REQUIRED)
        self.direction_box.addItems(["X", "Y", "Z", "-X", "-Y", "-Z"])
        self.direction_layout.addWidget(self.direction_box)
        label2 = QLabel(" (negative for reversed polarity)")
        self.direction_layout.addWidget(label2)
        self.direction_layout.addStretch()
        self.details_layout.addLayout(self.direction_layout)

        self.targetlayer_layout =  QHBoxLayout()
        self.target_label = QLabel("Target layer for in-plane port ")
        self.targetlayer_layout.addWidget(self.target_label)
        self.target_label.setFixedWidth(left_label_width)
        self.target_box = QComboBox()
        self.target_box.setFixedWidth(150)
        self.target_box.setStyleSheet(COMBO_STYLE_REQUIRED)
        self.target_box.addItems(["XML stackup missing"])
        self.targetlayer_layout.addWidget(self.target_box)

        self.targetlayer_layout.addStretch()
        self.details_layout.addLayout(self.targetlayer_layout)


        self.viaport_layout =  QHBoxLayout()
        self.from_label = QLabel("Via port from layer")
        self.viaport_layout.addWidget(self.from_label)
        self.from_label.setFixedWidth(left_label_width)
        self.from_box = QComboBox()
        self.from_box.setFixedWidth(150)
        self.from_box.setStyleSheet(COMBO_STYLE_REQUIRED)
        self.from_box.addItems(["XML stackup missing"])
        self.viaport_layout.addWidget(self.from_box)
        self.to_label = QLabel(" to layer")
        self.viaport_layout.addWidget(self.to_label)
        # label2.setFixedWidth(200)
        self.to_box = QComboBox()
        self.to_box.setFixedWidth(150)
        self.to_box.setStyleSheet(COMBO_STYLE_REQUIRED)
        self.viaport_layout.addWidget(self.to_box)


        self.viaport_layout.addStretch()
        self.details_layout.addLayout(self.viaport_layout)


        self.impedance_layout =  QHBoxLayout()
        label = QLabel("Port impedance")
        self.impedance_layout.addWidget(label)
        label.setFixedWidth(left_label_width)
        self.impedance_edit = QLineEdit("50")
        self.impedance_edit.setFixedWidth(80)
        self.impedance_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.impedance_layout.addWidget(self.impedance_edit)

        self.impedance_layout.addStretch()
        self.details_layout.addLayout(self.impedance_layout)

        self.voltage_layout =  QHBoxLayout()
        label = QLabel("Port voltage")
        self.voltage_layout.addWidget(label)
        label.setFixedWidth(left_label_width)
        self.voltage_edit = QLineEdit("1")
        self.voltage_edit.setFixedWidth(80)
        self.voltage_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.voltage_layout.addWidget(self.voltage_edit)

        self.voltage_layout.addWidget(QLabel(" (1=active, 0=passive)"))
        self.details_layout.addLayout(self.voltage_layout)


        self.buttons_layout = QHBoxLayout()

        button_width = 100

        self.apply_button = QPushButton(text="Apply ↓")
        self.buttons_layout.addWidget(self.apply_button)
        self.buttons_layout.setAlignment(Qt.AlignRight)
        self.apply_button.setFixedWidth(button_width)
        self.apply_button.clicked.connect(self.apply_port_values_to_table)

        self.remove_button = QPushButton(text="Remove")
        self.buttons_layout.addWidget(self.remove_button)
        self.remove_button.setFixedWidth(button_width)
        self.remove_button.clicked.connect(self.remove_port_values_from_table)

        self.details_layout.addLayout(self.buttons_layout)

        self.top_layout.addLayout(self.details_layout)

        self.bottom_group.setLayout(self.bottom_layout)
        self.top_group.setLayout(self.top_layout)

        self.main_layout.addWidget(self.top_group)
        self.main_layout.addSpacing(20)

        self.main_layout.addWidget(self.bottom_group)
        self.setLayout(self.main_layout)


        # callback when direction changed, so that we can show/hide layer choices
        def on_direction_changed(direction):
            if "Z" in direction:
                # hide target layer label and edit
                self.target_label.hide()
                self.target_box.hide()
                self.from_label.show()
                self.from_box.show()
                self.to_label.show()
                self.to_box.show()
            else:
                # show target layer label and edit
                self.target_label.show()
                self.target_box.show()
                self.from_label.hide()
                self.from_box.hide()
                self.to_label.hide()
                self.to_box.hide()


        self.direction_box.currentTextChanged.connect(on_direction_changed)
        self.direction_box.setCurrentIndex(2)

        # set this AFTER apply_port_values_to_table(), not earlier!
        self.portslist.itemSelectionChanged.connect(self.portslist_selection_changed)


    # callback when applying changes to the selected port
    def apply_port_values_to_table(self):
        selected_indexes = self.portslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell
            # "Z0", "Voltage", "Source layer", "Target layer", "From layer", "To layer","Direction"

            if "Z" in self.direction_box.currentText():
                target_layer = ""
                from_layer = self.from_box.currentText()
                to_layer = self.to_box.currentText()
            else:
                target_layer = self.target_box.currentText()
                from_layer = ""
                to_layer = ""

            data = [self.impedance_edit.text(),
                    self.voltage_edit.text(),
                    self.sourcelayer_edit.text(),
                    target_layer,
                    from_layer,
                    to_layer,
                    self.direction_box.currentText()]

            for col, value in enumerate(data):
                self.portslist.setItem(selected_row, col, QTableWidgetItem(str(value)))

            self.refresh_missing_layer_annotations()


    # callback when applying changes to the selected port
    def get_port_values_from_table(self):
        selected_indexes = self.portslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell
            # "Z0", "Voltage", "Source layer", "Target layer", "From layer", "To layer","Direction"

            def safe_get_for_lineedit (index, target, default):
                item =  self.portslist.item(selected_row, index)
                if item is None:
                    itemvalue = default
                else:
                    itemvalue = item.text()
                target.setText(itemvalue)

            def safe_get_for_combobox (index, target, default):
                item =  self.portslist.item(selected_row, index)
                if item is None:
                    index = default
                else:
                    index = target.findText(item.text())
                    if not index >= 0:
                        index = default
                target.setCurrentIndex(index)

            safe_get_for_lineedit(0,self.impedance_edit,"50")
            safe_get_for_lineedit(1,self.voltage_edit,"1")
            safe_get_for_lineedit(2,self.sourcelayer_edit,"")

            safe_get_for_combobox(3,self.target_box,0)
            safe_get_for_combobox(4,self.from_box,0)
            safe_get_for_combobox(5,self.to_box,0)
            safe_get_for_combobox(6,self.direction_box,2)


    # callback when removing selected port settings
    def remove_port_values_from_table(self):
        selected_indexes = self.portslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell

            data = ["","","","","","",""]

            for col, value in enumerate(data):
                # self.portslist.setItem(selected_row, col, QTableWidgetItem(str(value)))
                self.portslist.setItem(selected_row, col, None)
            self.portslist.setItem(selected_row, 7, None)  # clear any "(missing in layout)" note too


    def portslist_selection_changed(self):
        selected_indexes = self.portslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell
            item =  self.portslist.item(selected_row, 0) # first item is Z0
            if item is not None:
                # assume that we have an line that is not empty, so get port details from this line
                if item.text() != "":
                    self.get_port_values_from_table()
            else:
                suggestion = self._suggest_source_layer()
                if suggestion is not None:
                    self.sourcelayer_edit.setText(str(suggestion))
                else:
                    # no GDS-backed candidate - don't fabricate a number that
                    # may not exist in the layout, leave it visibly empty
                    self.sourcelayer_edit.clear()
                    self.sourcelayer_edit.setPlaceholderText("no unused GDS layer found")

    def _used_source_layers(self):
        """Layer numbers (column 2) already entered in the table, across all
        rows - used to avoid suggesting a layer another port already uses.
        """
        used = set()
        for row in range(self.portslist.rowCount()):
            item = self.portslist.item(row, 2)
            if item is not None and item.text():
                try:
                    used.add(int(item.text()))
                except ValueError:
                    pass
        return used

    def _port_layer_range(self):
        """Auto-assign source layer range, configurable via File > Preferences
        > Ports (falls back to the historical 201-299 range if unset/invalid).
        """
        app_name = self.MainWindow.APP_NAME
        try:
            layer_min = int(get_preference(app_name, "port_layer_min", "201"))
        except (TypeError, ValueError):
            layer_min = 201
        try:
            layer_max = int(get_preference(app_name, "port_layer_max", "299"))
        except (TypeError, ValueError):
            layer_max = 299
        return layer_min, layer_max

    def _suggest_source_layer(self):
        """Next GDS layer number in the auto-assign range that actually has
        geometry and isn't already a stackup layer or another port's source
        layer, or None if no such layer can be determined (e.g. no GDS file
        loaded yet, or every present layer is already spoken for).
        """
        layer_min, layer_max = self._port_layer_range()
        gds_layers = self.MainWindow.get_gds_layers_in_range(layer_min, layer_max)
        xml_layers = set(self.MainWindow.metals_list.getlayernumbers()) if self.MainWindow.metals_list else set()
        excluded = xml_layers | self._used_source_layers()
        return next_available_source_layer(gds_layers, excluded, start=layer_min)

    def refresh_missing_layer_annotations(self):
        """Flag any port row whose source layer has no geometry in the
        currently loaded GDS - see update_missing_layer_column()."""
        layer_min, layer_max = self._port_layer_range()
        gds_layers = self.MainWindow.get_gds_layers_in_range(layer_min, layer_max)
        update_missing_layer_column(self.portslist, source_col=2, comment_col=7, gds_layers_present=gds_layers)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_missing_layer_annotations()


    def save_values(self):
        # clear previous port data in simulation_ports instance
        simulation_ports.ports.clear()
        # loop over rows in ports table
        for row in range(self.portslist.rowCount()):
            portnumber = row+1
            testvalue = self.portslist.item(row, 0)
            if testvalue != None: # not an empty port line
                if testvalue.text() != "":
                    try:
                        Z0 = float(testvalue.text())
                        voltage = float(self.portslist.item(row, 1).text())
                        source_layer_num = int(self.portslist.item(row, 2).text())
                        target_name = self.portslist.item(row, 3).text()
                        from_name = self.portslist.item(row, 4).text()
                        to_name = self.portslist.item(row, 5).text()
                        direction = self.portslist.item(row, 6).text()
                    except Exception:
                        QMessageBox.warning(self, "Error", "Invalid input for port " + str(portnumber))
                        return False
                    # create port
                    if "Z" in direction:
                        # via port
                        simulation_ports.add_port(simulation_setup.simulation_port(portnumber=portnumber,
                                                                                voltage=voltage,
                                                                                port_Z0=Z0,
                                                                                source_layernum=source_layer_num,
                                                                                from_layername=from_name,
                                                                                to_layername=to_name,
                                                                                direction=direction))
                    else:
                        # in-plane port
                        simulation_ports.add_port(simulation_setup.simulation_port(portnumber=portnumber,
                                                                                voltage=voltage,
                                                                                port_Z0=Z0,
                                                                                source_layernum=source_layer_num,
                                                                                target_layername=target_name,
                                                                                direction=direction))

        return True

    def load_values(self):
        ...
        # self.log.setText(data.get("log", ""))

    def update_layers(self, metals_list):
        self.target_box.clear()
        self.from_box.clear()
        self.to_box.clear()
        for metal in metals_list.metals:
            self.target_box.addItems([metal.name])
            self.from_box.addItems([metal.name])
            self.to_box.addItems([metal.name])
        # try to preset useful values for SG13G2 technology
        index = self.target_box.findText('TopMetal2')  # returns -1 if not found
        if index != -1:
            self.target_box.setCurrentIndex(index)
            self.to_box.setCurrentIndex(index)
        index = self.from_box.findText('Metal1')  # returns -1 if not found
        if index != -1:
            self.from_box.setCurrentIndex(index)
        self.refresh_missing_layer_annotations()
        # re-evaluate the currently selected row's source-layer suggestion too -
        # a GDS/XML (re)load is exactly when a stale/unset suggestion (e.g. an
        # unapplied new row selected before any file was loaded) needs it most
        self.portslist_selection_changed()


    def update_port_from_import (self, ports):
        # update ports from imported model code in our native JSON format

        self.portslist.clearContents()
        for port in ports:
            # each port is a dictionary
            portnum = port.get("portnumber", None)
            if portnum is not None:
                portnum = int(portnum)

                self.portslist.setItem(portnum-1, 0, QTableWidgetItem(str(port.get("port_Z0",50))))
                self.portslist.setItem(portnum-1, 1, QTableWidgetItem(str(port.get("voltage",1.0))))
                self.portslist.setItem(portnum-1, 2, QTableWidgetItem(str(port.get("source_layernum",""))))

                direction = str(port.get("direction","")).upper()
                if direction == 'Z':
                    self.portslist.setItem(portnum-1, 4, QTableWidgetItem(str(port.get("from_layername",""))))
                    self.portslist.setItem(portnum-1, 5, QTableWidgetItem(str(port.get("to_layername",""))))
                    self.portslist.setItem(portnum-1, 3, QTableWidgetItem(""))
                else:
                    self.portslist.setItem(portnum-1, 4, QTableWidgetItem(""))
                    self.portslist.setItem(portnum-1, 5, QTableWidgetItem(""))
                    self.portslist.setItem(portnum-1, 3, QTableWidgetItem(str(port.get("target_layername",""))))
                self.portslist.setItem(portnum-1, 6, QTableWidgetItem(direction))


        self.portslist.selectRow(1)
        self.portslist.selectRow(0)



class RefinedCellsizeOverrideDialog(QDialog):
    """Popup editor for settings['refined_cellsize_override'], opened from
    MeshTab's "Advanced..." button. Lets the user set a different mesh
    refinement cell size for specific metal/sheet layers, instead of the
    single global "Mesh refinement at metal edges" value. The Layer column
    is restricted to metals_list.getallplanarmetals() (conductor/sheet
    layers) - vias and dielectrics never produce boundary curves in
    gds2palace's meshing code, so a name outside that list would silently
    have no effect if it were allowed to be typed in.
    """

    def __init__(self, parent, layer_choices, current_overrides):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Mesh Refinement Overrides")
        self.resize(420, 400)
        self.setModal(True)

        self._layer_choices = layer_choices
        self._result = []

        layout = QVBoxLayout()

        info = QLabel("Override the mesh refinement cell size for specific layers\n"
                      "(all other layers keep using the default set above).")
        layout.addWidget(info)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Layer", "Cell size (µm)", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        for name, value in current_overrides:
            self._add_row(name, value)

        add_row_layout = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.clicked.connect(lambda: self._add_row())
        add_row_layout.addWidget(add_btn)
        add_row_layout.addStretch()
        layout.addLayout(add_row_layout)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._on_ok)
        button_layout.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _add_row(self, name="", value=""):
        row = self.table.rowCount()
        self.table.insertRow(row)

        combo = QComboBox()
        combo.setStyleSheet(COMBO_STYLE_OPTIONAL)
        combo.addItem("")
        combo.addItems(self._layer_choices)
        if name and name not in self._layer_choices:
            # tolerate a saved override for a layer no longer in the current
            # stackup, same tolerant style as the Cellname combo box
            combo.addItem(name)
        if name:
            combo.setCurrentText(name)
        self.table.setCellWidget(row, 0, combo)

        value_text = "" if value == "" else str(value)
        self.table.setItem(row, 1, QTableWidgetItem(value_text))

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.clicked.connect(lambda: self._remove_row_containing(remove_btn))
        self.table.setCellWidget(row, 2, remove_btn)

    def _remove_row_containing(self, button):
        # look up the row by identity rather than a captured index, since
        # row indices shift whenever an earlier row is removed
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 2) is button:
                self.table.removeRow(row)
                return

    def _on_ok(self):
        overrides = []
        seen_layers = set()
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 0)
            layer = combo.currentText().strip() if combo else ""
            if not layer:
                continue  # blank row - not yet configured, skip silently

            value_item = self.table.item(row, 1)
            value_text = value_item.text().strip() if value_item else ""
            try:
                value = float(value_text)
                if value <= 0:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Error", f"Not a valid cell size for layer '{layer}'")
                return

            if layer in seen_layers:
                QMessageBox.warning(self, "Error", f"Layer '{layer}' is selected more than once")
                return
            seen_layers.add(layer)
            overrides.append([layer, value])

        self._result = overrides
        self.accept()

    def get_overrides(self):
        return self._result


class MeshTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow # parent = MainWindow

        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignTop)

        label_width = 250
        edit_width = 170

        # every row label below is appended here as it's created, then
        # widened at the end of __init__ to fit the longest one's actual
        # rendered text - label_width above is only a starting point during
        # construction; a fixed pixel guess doesn't survive different
        # fonts/DPI scaling (labels were truncating on Linux at high DPI)
        self._mesh_labels = []

        # ---------- MESH GROUP ----------
        self.mesh_group = QGroupBox("Mesh settings")
        self.mesh_layout = QVBoxLayout()

        self.refinement_layout = QHBoxLayout()
        self.label2 = QLabel("Mesh refinement at metal edges (µm)")
        self.label2.setFixedWidth(label_width)
        self._mesh_labels.append(self.label2)
        self.refinement_layout.addWidget(self.label2)
        self.refinement_edit = QLineEdit("5")
        self.refinement_edit.setFixedWidth(edit_width)
        self.refinement_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.refinement_layout.addWidget(self.refinement_edit)
        self.refined_override_btn = QPushButton("Advanced...")
        self.refined_override_btn.clicked.connect(self.open_refined_cellsize_override_dialog)
        self.refinement_layout.addWidget(self.refined_override_btn)
        self.refinement_layout.addStretch()
        self.mesh_layout.addLayout(self.refinement_layout)

        # in-memory copy of settings['refined_cellsize_override'] (list of
        # [layername, value] pairs), edited via the "Advanced..." dialog;
        # harvested into saved_values by save_values() like every other field
        self._refined_cellsize_override = []

        self.cells_lambda_layout = QHBoxLayout()
        self.label4 = QLabel("Mesh cells per wavelength (min 10)")
        self.label4.setFixedWidth(label_width)
        self._mesh_labels.append(self.label4)
        self.cells_lambda_layout.addWidget(self.label4)
        self.cells_lambda_edit = QLineEdit("10")
        self.cells_lambda_edit.setFixedWidth(edit_width)
        self.cells_lambda_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.cells_lambda_layout.addWidget(self.cells_lambda_edit)
        self.cells_lambda_layout.addStretch()
        self.mesh_layout.addLayout(self.cells_lambda_layout)


        self.cells_maxsize_layout = QHBoxLayout()
        self.label6 = QLabel("Mesh cell maximum size absolute (µm)")
        self.label6.setFixedWidth(label_width)
        self._mesh_labels.append(self.label6)
        self.cells_maxsize_layout.addWidget(self.label6)
        self.cells_maxsize_edit = QLineEdit("100")
        self.cells_maxsize_edit.setFixedWidth(edit_width)
        self.cells_maxsize_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.cells_maxsize_layout.addWidget(self.cells_maxsize_edit)
        self.cells_maxsize_layout.addStretch()
        self.mesh_layout.addLayout(self.cells_maxsize_layout)


        self.meshorder_layout = QHBoxLayout()
        self.label1 = QLabel("Mesh basis function")
        self.label1.setFixedWidth(label_width)
        self._mesh_labels.append(self.label1)
        self.meshorder_layout.addWidget(self.label1)

        self.mesh_order_box = QComboBox()
        self.mesh_order_box.setFixedWidth(edit_width)
        self.mesh_order_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.mesh_order_box.addItems(["faster, less accurate (N=1)","recommended (N=2)", "slower, most accurate (N=3)"])
        self.meshorder_layout.addWidget(self.mesh_order_box)
        self.mesh_order_box.setCurrentIndex(0)
        self.meshorder_layout.addStretch()
        self.mesh_layout.addLayout(self.meshorder_layout)

        self.mesh_group.setLayout(self.mesh_layout)
        self.main_layout.addWidget(self.mesh_group)
        self.main_layout.addSpacing(20)

        self.mesh_order_box.currentTextChanged.connect(self.on_meshorder_changed)
        self.mesh_order_box.setCurrentIndex(1)

        # ---------- ELMER SOLVER GROUP ----------
        self.Elmer_group = QGroupBox("Elmer solver settings")
        self.Elmer_layout = QVBoxLayout()

        self.solver_layout = QHBoxLayout()
        self.solverlabel = QLabel("Solver")
        self.solverlabel.setFixedWidth(label_width)
        self._mesh_labels.append(self.solverlabel)
        self.solver_layout.addWidget(self.solverlabel)

        self.solver_box = QComboBox()
        self.solver_box.setFixedWidth(250)
        self.solver_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.solver_box.addItems(["direct","iterative"])
        self.solver_layout.addWidget(self.solver_box)
        self.solver_box.setCurrentIndex(0)
        self.solver_layout.addStretch()
        self.Elmer_layout.addLayout(self.solver_layout)

        # number of Threads input, only for Elmer
        self.threads_layout = QHBoxLayout()
        self.labelthreads = QLabel("Multithreading:")
        self.labelthreads.setFixedWidth(label_width)
        self._mesh_labels.append(self.labelthreads)
        self.threads_layout.addWidget(self.labelthreads)
        self.threads_box = QComboBox()
        self.threads_box.setFixedWidth(250)
        self.threads_box.setStyleSheet(COMBO_STYLE_REQUIRED)
        self.threads_box.addItems(["1 thread running ElmerSolver","2 threads using MPI","4 threads using MPI","8 threads using MPI","16 threads using MPI"])
        self.threads_layout.addWidget(self.threads_box)
        self.threads_box.setCurrentIndex(2)
        self.threads_layout.addStretch()
        self.Elmer_layout.addLayout(self.threads_layout)

        # disable unless Elmer mode is enabled
        self.Elmer_group.setVisible(False)


        self.Elmer_group.setLayout(self.Elmer_layout)
        self.main_layout.addWidget(self.Elmer_group)

        # ---------- MESH GROUP ----------
        self.AMR_group = QGroupBox("Adaptive mesh refinement (AMR)")
        self.AMR_layout = QVBoxLayout()

        # AMR goal/maximum DOF are rarely tuned away from their defaults -
        # hidden until this is set to "Yes", to keep the common case
        # (just choosing how many AMR iterations to run) uncluttered.
        # Resets to "No" every time this tab is (re)constructed, same as
        # e.g. the "at xmin, xmax/..." air-margin fields below, which also
        # aren't persisted - this is a display toggle, not a simulation
        # setting, and the AMR goal/max DOF values themselves are still
        # saved/loaded normally regardless of whether they're shown.
        self.show_advanced_layout = QHBoxLayout()
        self.label_show_advanced = QLabel("Show advanced configuration")
        self.label_show_advanced.setFixedWidth(label_width)
        self._mesh_labels.append(self.label_show_advanced)
        self.show_advanced_layout.addWidget(self.label_show_advanced)
        self.show_advanced_box = QComboBox()
        self.show_advanced_box.setFixedWidth(edit_width)
        self.show_advanced_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.show_advanced_box.addItems(["No", "Yes"])
        self.show_advanced_layout.addWidget(self.show_advanced_box)
        self.show_advanced_layout.addStretch()
        self.AMR_layout.addLayout(self.show_advanced_layout)

        self.cells_AMRiterations_layout = QHBoxLayout()
        self.labelAMR1 = QLabel("Adaptive mesh iterations")
        self.labelAMR1.setFixedWidth(label_width)
        self._mesh_labels.append(self.labelAMR1)
        self.cells_AMRiterations_layout.addWidget(self.labelAMR1)
        self.AMR_iterations_edit = QLineEdit("0")
        self.AMR_iterations_edit.setFixedWidth(edit_width)
        self.AMR_iterations_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.cells_AMRiterations_layout.addWidget(self.AMR_iterations_edit)
        self.labelAMR2 = QLabel(" (default is 0, no adaptive mesh refinement)")
        self.cells_AMRiterations_layout.addWidget(self.labelAMR2)
        self.cells_AMRiterations_layout.addStretch()
        self.AMR_layout.addLayout(self.cells_AMRiterations_layout)

        self.amr_goal_layout = QHBoxLayout()
        self.labelAMRgoal1 = QLabel("AMR goal (relative error tolerance)")
        self.labelAMRgoal1.setFixedWidth(label_width)
        self._mesh_labels.append(self.labelAMRgoal1)
        self.amr_goal_layout.addWidget(self.labelAMRgoal1)
        self.amr_goal_edit = QLineEdit("0.01")
        self.amr_goal_edit.setFixedWidth(edit_width)
        self.amr_goal_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.amr_goal_layout.addWidget(self.amr_goal_edit)
        self.amr_goal_layout.addStretch()
        self.AMR_layout.addLayout(self.amr_goal_layout)

        self.amr_maxdof_layout = QHBoxLayout()
        self.labelAMRmaxdof1 = QLabel("AMR maximum DOF")
        self.labelAMRmaxdof1.setFixedWidth(label_width)
        self._mesh_labels.append(self.labelAMRmaxdof1)
        self.amr_maxdof_layout.addWidget(self.labelAMRmaxdof1)
        self.amr_maxdof_edit = QLineEdit("2000000")
        self.amr_maxdof_edit.setFixedWidth(edit_width)
        self.amr_maxdof_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.amr_maxdof_layout.addWidget(self.amr_maxdof_edit)
        self.amr_maxdof_layout.addStretch()
        self.AMR_layout.addLayout(self.amr_maxdof_layout)

        def on_show_advanced_changed(value):
            show = (value == "Yes")
            for item in [self.labelAMRgoal1, self.amr_goal_edit,
                         self.labelAMRmaxdof1, self.amr_maxdof_edit]:
                item.setVisible(show)

        self.show_advanced_box.currentTextChanged.connect(on_show_advanced_changed)
        on_show_advanced_changed(self.show_advanced_box.currentText())

        self.AMR_group.setLayout(self.AMR_layout)
        self.main_layout.addWidget(self.AMR_group)
        self.main_layout.addSpacing(20)


        # ---------- BOUNDARY GROUP ----------

        self.mesh_group = QGroupBox("Boundary settings")
        self.mesh_layout = QVBoxLayout()

        self.boundary_layout = QHBoxLayout()
        self.label6 = QLabel("Boundary conditions")
        self.label6.setFixedWidth(label_width)
        self._mesh_labels.append(self.label6)
        self.boundary_layout.addWidget(self.label6)
        self.boundary_box = QComboBox()
        self.boundary_box.setFixedWidth(edit_width)
        self.boundary_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.boundary_box.addItems(["Absorbing","PEC","PMC"])
        self.boundary_layout.addWidget(self.boundary_box)
        self.boundary_layout.addStretch()
        self.mesh_layout.addLayout(self.boundary_layout)

        self.margins_layout = QHBoxLayout()
        self.label7 = QLabel("Dielectric stackup: oversize by")
        self.label7.setFixedWidth(label_width)
        self._mesh_labels.append(self.label7)
        self.margins_layout.addWidget(self.label7)
        self.margins_edit = QLineEdit("200")
        self.margins_edit.setFixedWidth(edit_width)
        self.margins_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.margins_layout.addWidget(self.margins_edit)
        self.label8 = QLabel(" µm from metal drawing")
        self.margins_layout.addWidget(self.label8)
        self.margins_layout.addStretch()
        self.mesh_layout.addLayout(self.margins_layout)


        self.airaround_layout = QHBoxLayout()
        self.label9 = QLabel("Air layer thickness around stackup is")
        self.label9.setFixedWidth(label_width)
        self._mesh_labels.append(self.label9)
        self.airaround_layout.addWidget(self.label9)

        self.airaround_box = QComboBox()
        self.airaround_box.setFixedWidth(edit_width)
        self.airaround_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.airaround_box.addItems(["same on all sides","different per side"])
        self.airaround_layout.addWidget(self.airaround_box)

        air_edit_width = 100

        self.airaround_edit = QLineEdit("200")
        self.airaround_edit.setFixedWidth(air_edit_width)
        self.airaround_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.airaround_layout.addWidget(self.airaround_edit)
        self.label10 = QLabel(" µm")
        self.airaround_layout.addWidget(self.label10)
        self.airaround_layout.addStretch()
        self.mesh_layout.addLayout(self.airaround_layout)

        self.airx_layout = QHBoxLayout()
        self.label11 = QLabel("at xmin, xmax")
        self.label11.setFixedWidth(label_width)
        self._mesh_labels.append(self.label11)
        self.label11.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.airx_layout.addWidget(self.label11)
        self.airxmin_edit = QLineEdit("200")
        self.airxmin_edit.setFixedWidth(air_edit_width)
        self.airxmin_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.airx_layout.addWidget(self.airxmin_edit)
        self.airxmax_edit = QLineEdit("200")
        self.airxmax_edit.setFixedWidth(air_edit_width)
        self.airxmax_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.airx_layout.addWidget(self.airxmax_edit)
        self.label12 = QLabel(" µm")
        self.airx_layout.addWidget(self.label12)
        self.airx_layout.addStretch()
        self.mesh_layout.addLayout(self.airx_layout)

        self.airy_layout = QHBoxLayout()
        self.label13 = QLabel("at ymin, ymax")
        self.label13.setFixedWidth(label_width)
        self._mesh_labels.append(self.label13)
        self.label13.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.airy_layout.addWidget(self.label13)
        self.airymin_edit = QLineEdit("200")
        self.airymin_edit.setFixedWidth(air_edit_width)
        self.airymin_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.airy_layout.addWidget(self.airymin_edit)
        self.airymax_edit = QLineEdit("200")
        self.airymax_edit.setFixedWidth(air_edit_width)
        self.airymax_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.airy_layout.addWidget(self.airymax_edit)
        self.label14 = QLabel(" µm")
        self.airy_layout.addWidget(self.label14)
        self.airy_layout.addStretch()
        self.mesh_layout.addLayout(self.airy_layout)

        self.airz_layout = QHBoxLayout()
        self.label15 = QLabel("at zmin, zmax")
        self.label15.setFixedWidth(label_width)
        self._mesh_labels.append(self.label15)
        self.label15.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.airz_layout.addWidget(self.label15)
        self.airzmin_edit = QLineEdit("200")
        self.airzmin_edit.setFixedWidth(air_edit_width)
        self.airzmin_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.airz_layout.addWidget(self.airzmin_edit)
        self.airzmax_edit = QLineEdit("200")
        self.airzmax_edit.setFixedWidth(air_edit_width)
        self.airzmax_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.airz_layout.addWidget(self.airzmax_edit)
        self.label16 = QLabel(" µm")
        self.airz_layout.addWidget(self.label16)
        self.airz_layout.addStretch()
        self.mesh_layout.addLayout(self.airz_layout)

        # callback when air_around dropdown changed, so that we can show/hide edit fields
        def on_airaround_changed(value):
            if "different" in value:
                # hide target layer label and edit
                for item in [self.label11,self.label12,self.label13,self.label14,self.label15,self.label16,self.airxmin_edit, self.airxmax_edit, self.airymin_edit, self.airymax_edit, self.airzmin_edit, self.airzmax_edit]:
                    item.show()
                self.airaround_edit.hide()
                self.label10.hide()
            else:
                for item in [self.label11,self.label12,self.label13,self.label14,self.label15,self.label16,self.airxmin_edit, self.airxmax_edit, self.airymin_edit, self.airymax_edit, self.airzmin_edit, self.airzmax_edit]:
                    item.hide()
                self.airaround_edit.show()
                self.label10.show()

        self.airaround_box.currentTextChanged.connect(on_airaround_changed)
        self.airaround_box.setCurrentIndex(1)
        self.airaround_box.setCurrentIndex(0)

        self.mesh_group.setLayout(self.mesh_layout)
        self.main_layout.addWidget(self.mesh_group)

        # widen every row label to fit the longest one's own rendered text.
        # sizeHint() is no use here - once a label has an explicit
        # setFixedWidth() (already applied above) and sits in a layout, Qt
        # reports that fixed value back as its sizeHint() instead of the
        # text's natural width, so every label would appear identically
        # "already wide enough". Measure the actual glyph width directly via
        # QFontMetrics instead, which reflects the real font/DPI regardless
        # of any size already imposed on the widget - a fixed pixel guess
        # doesn't survive different fonts/DPI scaling (labels were
        # truncating on Linux at high DPI).
        widest = max(QFontMetrics(lbl.font()).horizontalAdvance(lbl.text())
                     for lbl in self._mesh_labels)
        for lbl in self._mesh_labels:
            lbl.setFixedWidth(widest + 10)

        self.setLayout(self.main_layout)


    def _update_refined_override_button_label(self):
        n = len(self._refined_cellsize_override)
        self.refined_override_btn.setText(f"Advanced... ({n})" if n > 0 else "Advanced...")

    def open_refined_cellsize_override_dialog(self):
        metals_list = self.MainWindow.metals_list
        if metals_list is None:
            QMessageBox.warning(self, "Error", "Load a GDSII file and XML stackup first")
            return

        layer_choices = [metal.name for metal in metals_list.getallplanarmetals()]
        dialog = RefinedCellsizeOverrideDialog(self, layer_choices, self._refined_cellsize_override)
        if dialog.exec() == QDialog.Accepted:
            self._refined_cellsize_override = dialog.get_overrides()
            self._update_refined_override_button_label()

    def on_meshorder_changed(self, value):
    # callback when mesh order changed, so that we can show/hide edit fields
        try:
            self.solver_box.setCurrentIndex(0)
            if  "faster" in value:
                self.solver_box.setDisabled(True)
            else:
                self.solver_box.setDisabled(False)
        except:
            pass

        # order=3 (ultra accurate) is Palace-only: Elmer has no cubic-order solver
        # templates in util_elmer.write_case_and_solver_files(), so it would silently
        # fall back to first-order there. Disable that option under Elmer mode, and
        # fall back to the default order if it was selected when switching into Elmer.
        try:
            ultra_accurate_index = 2
            item = self.mesh_order_box.model().item(ultra_accurate_index)
            if self.MainWindow.ElmerMode:
                item.setEnabled(False)
                if self.mesh_order_box.currentIndex() == ultra_accurate_index:
                    self.mesh_order_box.setCurrentIndex(1)
            else:
                item.setEnabled(True)
        except:
            pass


    def save_values(self):
        try:
            value = float(self.refinement_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for mesh refinement")
            self.refinement_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "refined_cellsize", "5")))
            return False
        saved_values ["refined_cellsize"] = float(value)
        saved_values ["refined_cellsize_override"] = self._refined_cellsize_override

        saved_values ["order"] = self.mesh_order_box.currentIndex()+1

        try:
            value = float(self.cells_lambda_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for cells/wavelength")
            self.cells_lambda_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "cells_per_wavelength", "10")))
            return False
        saved_values ["cells_per_wavelength"] = float(value)

        try:
            value = float(self.cells_maxsize_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for max. meshsize")
            self.cells_maxsize_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "meshsize_max", "100")))
            return False
        saved_values ["meshsize_max"] = float(value)

        try:
            value = int(self.AMR_iterations_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for AMR iterations")
            self.AMR_iterations_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "adaptive_mesh_iterations", "0")))
            return False
        saved_values ["adaptive_mesh_iterations"] = int(value)

        try:
            value = float(self.amr_goal_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for AMR goal")
            self.amr_goal_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "amr_tol", "0.01")))
            return False
        saved_values ["amr_tol"] = float(value)

        try:
            value = int(self.amr_maxdof_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for AMR maximum DOF")
            self.amr_maxdof_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "amr_max_dof", "2000000")))
            return False
        saved_values ["amr_max_dof"] = int(value)


        # iterative or direct solver for Elmer
        saved_values["iterative"] = "iterative" in self.solver_box.currentText()

        BC = self.boundary_box.currentText()
        if BC=="PEC":
            saved_values ["boundary"] = ['PEC','PEC','PEC','PEC','PEC','PEC']
        elif BC=="PMC":
            saved_values ["boundary"] = ['PMC','PMC','PMC','PMC','PMC','PMC']
        else: # Absorbing
            saved_values ["boundary"] = ['ABC','ABC','ABC','ABC','ABC','ABC']


        try:
            value = float(self.margins_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for dielectric oversize margin")
            self.margins_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "margin", "200")))
            return False
        saved_values ["margin"] = float(value)

        airsides = self.airaround_box.currentText()
        if not "different" in airsides:
            # one air margin for all
            try:
                value = float(self.airaround_edit.text())
            except Exception:
                QMessageBox.warning(self, "Error", "Not a valid value for AMR iterations")
                # default air thickness =  margins
                self.airaround_edit.setText(self.margins_edit.text())
                return False
            saved_values ["air_around"] = float(value)
        else:
            # one air margin per side, total 6 values
            try:
                xmin = float(self.airxmin_edit.text())
                xmax = float(self.airxmax_edit.text())
                ymin = float(self.airymin_edit.text())
                ymax = float(self.airymax_edit.text())
                zmin = float(self.airzmin_edit.text())
                zmax = float(self.airzmax_edit.text())
            except Exception:
                QMessageBox.warning(self, "Error", "Not a valid value for air margins, reset to default")
                # default air thickness =  margins
                self.airxmin_edit.setText(self.margins_edit.text())
                self.airxmax_edit.setText(self.margins_edit.text())
                self.airymin_edit.setText(self.margins_edit.text())
                self.airymax_edit.setText(self.margins_edit.text())
                self.airzmin_edit.setText(self.margins_edit.text())
                self.airzmax_edit.setText(self.margins_edit.text())
                return False
            saved_values ["air_around"] = [xmin, xmax, ymin, ymax, zmin, zmax]

        # check value for number of threads
        if self.MainWindow.ElmerMode:
            index = self.threads_box.currentIndex()
            if index==1:
                n = 2
            elif index==2:
                n = 4
            elif index==3:
                n = 8
            elif index==4:
                n = 16
            else:
                n = 1
            saved_values['ELMER_MPI_THREADS'] = n

        # all saved
        return True


    def load_values(self):
        app_name = self.MainWindow.APP_NAME
        self.refinement_edit.setText(str(saved_values.get("refined_cellsize", get_preference(app_name, "refined_cellsize", "5"))))
        self._refined_cellsize_override = list(saved_values.get("refined_cellsize_override", []))
        self._update_refined_override_button_label()
        self.cells_lambda_edit.setText(str(saved_values.get("cells_per_wavelength", get_preference(app_name, "cells_per_wavelength", "10"))))
        self.cells_maxsize_edit.setText(str(saved_values.get("meshsize_max", get_preference(app_name, "meshsize_max", "100"))))
        self.AMR_iterations_edit.setText(str(saved_values.get("adaptive_mesh_iterations", get_preference(app_name, "adaptive_mesh_iterations", "0"))))
        self.amr_goal_edit.setText(str(saved_values.get("amr_tol", get_preference(app_name, "amr_tol", "0.01"))))
        self.amr_maxdof_edit.setText(str(saved_values.get("amr_max_dof", get_preference(app_name, "amr_max_dof", "2000000"))))
        self.margins_edit.setText(str(saved_values.get("margin", get_preference(app_name, "margin", "200"))))

        self.mesh_order_box.setCurrentIndex(int(saved_values.get("order", 2))-1)

        if saved_values.get("iterative", False):
            self.solver_box.setCurrentIndex(1)
        else:
            self.solver_box.setCurrentIndex(0)

        if 'PEC' in saved_values.get("boundary",""):
            self.boundary_box.setCurrentIndex(1)
        elif 'PMC' in saved_values.get("boundary",""):
            self.boundary_box.setCurrentIndex(2)
        else:
            # default is absorbing
            self.boundary_box.setCurrentIndex(0)

        # check if air layer is defined at all, or single value or list
        air = saved_values.get("air_around","")
        if air == "":
            # no value defined: an explicit air_around preference wins, otherwise
            # fall back to the (also preference-aware) dielectric margin value -
            # this mirrors the tab's original "same as margins" convenience default
            air_pref = get_preference(self.MainWindow.APP_NAME, "air_around", "")
            if str(air_pref) != "":
                self.airaround_edit.setText(str(air_pref))
            else:
                self.airaround_edit.setText(str(saved_values.get("margin", get_preference(self.MainWindow.APP_NAME, "margin", "200"))))
            self.airaround_box.setCurrentIndex(0)
        else:
            # native JSON round-trip stores this as a real list of floats;
            # .py import stores it as a comma-separated string instead
            if isinstance(air, list):
                air_as_list = [str(v) for v in air]
            elif "," in str(air):
                air_as_list = [v.strip() for v in str(air).split(',')]
            else:
                air_as_list = None

            if air_as_list and len(air_as_list) == 6:
                self.airaround_box.setCurrentIndex(1)
                self.airxmin_edit.setText(air_as_list[0])
                self.airxmax_edit.setText(air_as_list[1])
                self.airymin_edit.setText(air_as_list[2])
                self.airymax_edit.setText(air_as_list[3])
                self.airzmin_edit.setText(air_as_list[4])
                self.airzmax_edit.setText(air_as_list[5])
            else:
                # we have air_around defined as a single value
                self.airaround_box.setCurrentIndex(0)
                self.airaround_edit.setText(str(air))

        # MPI multithreading for Elmer
        n = saved_values.get('ELMER_MPI_THREADS',4)
        if n<2:
            self.threads_box.setCurrentIndex(0)
        elif n<4:
            self.threads_box.setCurrentIndex(1)
        elif n<8:
            self.threads_box.setCurrentIndex(2)
        elif n<16:
            self.threads_box.setCurrentIndex(3)
        else:
            self.threads_box.setCurrentIndex(4)



class CreateModelTab(CreateModelTabBase):
    """Palace/Elmer specific model-building and run behavior.

    UI construction, log panel, preview/create-mesh, and the QProcess
    plumbing all live in CreateModelTabBase (setup_common.py). Only the
    "is the model complete?" check and how the solver is actually launched
    are specific to this app.
    """

    def __init__(self, MainWindow):
        super().__init__(MainWindow)

        # Tracks which action self.process is currently running, so on_finished() can tell a
        # simulation run apart from a mesh-creation run (self.process is reused for both).
        self._process_purpose = None

        # S-parameter result viewer + model fit: appended here (not in the shared
        # CreateModelTabBase) since setupThermal has no S-parameters and must not
        # show these buttons. Added as a row of the base class's buttons_grid (not a
        # separate layout) so this row's column widths line up exactly with
        # Preview/Create Mesh/Start Simulation above, in the same Actions group.
        row = self.buttons_grid.rowCount()
        self.view_results_btn = QPushButton("📈 View S-Parameters...")
        self.view_results_btn.clicked.connect(self.MainWindow.open_result_viewer)
        self.buttons_grid.addWidget(self.view_results_btn, row, 0)
        self.model_fit_btn = QPushButton("🧩 Model Fit...")
        self.model_fit_btn.setFixedWidth(SECONDARY_BUTTON_WIDTH)
        self.model_fit_btn.clicked.connect(self.open_model_fit)
        self.buttons_grid.addWidget(self.model_fit_btn, row, 1)

        # "View fields ..." opens field-dump data (Palace fdump / Elmer EM
        # fields*.vtu) in whichever viewer Preferences > Viewer > "3D field
        # viewer" selects - built-in (embedded PyVista) or external ParaView, see
        # open_viewer(). A separate row since it's independent of the S-parameter
        # viewer/model fit above. Only meaningful when fdump is set (otherwise
        # there's never any field data to open), so it's hidden rather than shown
        # greyed-out - kept in sync with saved_values['fdump'] via
        # _update_viewer_button(), called from here, from load_values() (project/
        # model import), and from FrequenciesTab.save_values() (live edits to the
        # fdump field). Its label is also kept in sync there and in
        # apply_preference_visibility() (Preferences dialog live-update).
        row = self.buttons_grid.rowCount()
        self.viewer_btn = QPushButton()
        self.viewer_btn.clicked.connect(self.open_viewer)
        self.buttons_grid.addWidget(self.viewer_btn, row, 0)
        self._update_viewer_button()

        # Live solver-progress status line, below the log area. Palace-only: visibility is
        # driven by MainWindow.setPalaceMode()/setElmerMode() combined with the
        # "enable_status_bar" preference (see apply_preference_visibility()); this is
        # just a placeholder default, replaced immediately below.
        self.status_line = QLabel()
        self.actions_layout.addWidget(self.status_line)
        self._init_status_state()
        self.apply_preference_visibility()

    def apply_preference_visibility(self):
        # Called from __init__, from MainWindow.setPalaceMode()/setElmerMode() (mode
        # switch also affects status_line visibility), and from MainWindow's Preferences
        # dialog on accept (live update, no restart needed).
        enable_fit = get_preference_bool(self.MainWindow.APP_NAME, "enable_model_fit_button", True)
        self.model_fit_btn.setVisible(enable_fit)
        enable_status = get_preference_bool(self.MainWindow.APP_NAME, "enable_status_bar", True)
        self.status_line.setVisible(enable_status and self.MainWindow.PalaceMode)
        self._update_viewer_button_label()

    # --- Live Palace solver status line ------------------------------------------------
    #
    # Regexes matched against real Palace 0.16.0 stdout (see palace-x86_64.bin console
    # output), one AMR iteration's worth of an 8-port sweep:
    #   "Running with 16 MPI processes"
    #   "Estimated current per-rank memory usage is: Min. 84.8M, Max. 87.1M, Avg. 85.7M, Total 1.3G"
    #   "Estimated peak per-rank memory usage is: Min. 1.5G, Max. 1.6G, Avg. 1.5G, Total 24.2G"
    #   "Sweeping excitation index 2 (2/8):"
    #   "It 1/1: ω/2π = 9.300e+01 GHz (total elapsed time = 1.52e+01 s, solve 1/8)"
    #   "Completed 1 iteration of adaptive mesh refinement (AMR):"
    # "Estimated ... memory usage" appears both early (current, post mesh-partition) and
    # again per AMR iteration (peak); the parser just keeps the latest value seen, whichever
    # wording it came from. Deliberately per-rank, not per-node: per-rank Total is the sum
    # of every individual rank's own estimate, i.e. the actual total memory footprint of the
    # whole job, regardless of how ranks are distributed across nodes (per-node Total is only
    # numerically the same thing when everything happens to run on a single node).
    # "Sweeping excitation" marks a port in a uniform sweep; "Adding excitation" is the
    # equivalent during PROM/adaptive offline construction (Beginning PROM construction
    # offline phase: / Adding excitation index 1 (1/2):) - both mean "now on port N/M".
    _RE_MPI = re.compile(r"Running with (\d+) MPI processes")
    # "current" vs "peak" is NOT current-vs-forecast: real testing (AMR run, limit set
    # to 4GB) showed "current" stays low while "peak" reports each iteration's real,
    # already-incurred high-water mark (3.79 -> 5.08 -> 9.85 GB) - a current-only RAM
    # check never saw those numbers and never fired. _check_ram_limit() now checks
    # self._status_mem_gb, the same latest-of-either-kind value already shown on the
    # live status line, instead of singling out one kind - see _parse_palace_status_line().
    _RE_MEM_TOTAL = re.compile(r"Estimated (?:current|peak) per-rank memory usage is:.*Total\s+([\d.]+)([MG])")
    _RE_EXCITATION = re.compile(r"(?:Sweeping|Adding) excitation index \d+ \((\d+)/(\d+)\):")
    # "It i/n: ... (total elapsed time = t s, solve k/N)" in a uniform sweep, but only
    # "It i/n: ... (total elapsed time = t s)" - no trailing solve k/N - during PROM's online
    # (interpolated-evaluation) phase, so the solve suffix is matched separately and is
    # optional; its presence is what distinguishes a real full-order solve from a PROM
    # evaluation (see _parse_palace_status_line).
    _RE_FREQ = re.compile(r"It (\d+)/(\d+):")
    _RE_SOLVE_SUFFIX = re.compile(r"solve (\d+)/(\d+)\)")
    # PROM's offline phase (building the reduced-order model) has no "It i/n" progress at
    # all - only these per-port greedy-sampling steps, with no fixed total to divide by:
    #   "Greedy iteration 1 (n = 4): ω* = 2.716e+01 GHz (4.986e-01), error = 7.573e-03, memory = 1/2"
    _RE_GREEDY = re.compile(r"Greedy iteration (\d+) \(n = (\d+)\):")
    # Different Palace releases report each just-finished AMR pass differently, but both
    # number 1-based from the very first (unrefined-mesh) solve - there is no "iteration 0"
    # in either wording. Confirmed two ways: the "global unknowns" figure on these lines
    # matches the DOF count of the solve that just finished (not a next/refined mesh), and
    # palace_results.py's own output-folder naming (iteration1/, iteration2/, ...) uses the
    # same 1-based scheme.
    #   "Completed 1 iteration of adaptive mesh refinement (AMR): Indicator norm=..., global unknowns=..."   (older release)
    #   "Adaptive mesh refinement (AMR) iteration 1: Indicator norm=..., global unknowns=..."                 (v0.16.0-34-gea2e7b23)
    # The newer release also prints "Proceeding with solve/estimate iteration 2..." right
    # after refining/rebalancing, announcing the *next* iteration before its ports start
    # solving - matched separately so the display updates immediately instead of lagging one
    # iteration behind until that next iteration's own report line finally appears.
    _RE_AMR_ITER_A = re.compile(r"Completed (\d+) iteration.*adaptive mesh refinement \(AMR\)")
    _RE_AMR_ITER_B = re.compile(r"Adaptive mesh refinement \(AMR\) iteration (\d+):")
    _RE_AMR_PROCEEDING = re.compile(r"Proceeding with solve/estimate iteration (\d+)")

    def _init_status_state(self):
        """Reset the tracked fields to unknown ('n/a'). Split out from
        _reset_status_for_run() so __init__ can establish the attributes without
        touching disk (config.json may not exist yet at construction time)."""
        self._status_mpi = None
        self._status_mem_gb = None
        self._status_port_cur = None
        self._status_port_total = None
        self._status_freq_display = None
        self._status_solve_display = ""
        self._status_amr_cur = None
        self._status_amr_max = None
        self._ram_kill_triggered = False
        self._ram_kill_message = None
        self._update_status_line()

    def _reset_status_for_run(self):
        """Called from run_model() right after the log is cleared. Re-reads MaxIts out
        of the just-generated config.json so the AMR field has a known ceiling from the
        start, instead of only appearing once the first iteration-report line shows up
        in the log."""
        self._init_status_state()
        if self.MainWindow.PalaceMode:
            run_path = saved_values['sim_path'] + "/palace_model/" + saved_values['model_basename'] + "_data"
            try:
                with open(os.path.join(run_path, "config.json")) as f:
                    config_data = json.load(f)
                max_its = config_data.get("Model", {}).get("Refinement", {}).get("MaxIts", 0)
                # 0 (AMR off) is a known value, distinct from None (unknown, e.g. config
                # unreadable) - _update_status_line() shows "0/0" for the former, "n/a" for
                # the latter.
                self._status_amr_max = max_its
                self._status_amr_cur = 0  # the initial, unrefined-mesh solve displays as "0"
            except (OSError, ValueError, KeyError):
                pass
            self._update_status_line()

    def _on_stdout_line(self, line):
        if self.MainWindow.PalaceMode:
            self._parse_palace_status_line(line)

    def _reset_live_status(self):
        self._init_status_state()

    def _parse_palace_status_line(self, line):
        m = self._RE_MPI.search(line)
        if m:
            self._status_mpi = int(m.group(1))
            self._update_status_line()
            return

        m = self._RE_MEM_TOTAL.search(line)
        if m:
            value, unit = float(m.group(1)), m.group(2)
            self._status_mem_gb = value / 1024 if unit == "M" else value
            self._check_ram_limit()
            self._update_status_line()
            return

        m = self._RE_EXCITATION.search(line)
        if m:
            self._status_port_cur = int(m.group(1))
            self._status_port_total = int(m.group(2))
            # New port: the previous port's frequency/solve position no longer applies.
            self._status_freq_display = None
            self._status_solve_display = ""
            self._update_status_line()
            return

        m = self._RE_FREQ.search(line)
        if m:
            solve_m = self._RE_SOLVE_SUFFIX.search(line)
            if solve_m:
                # Real per-frequency full-order solve (uniform sweep) - meaningful progress.
                self._status_freq_display = f"{m.group(1)}/{m.group(2)}"
                self._status_solve_display = f" (solve {solve_m.group(1)}/{solve_m.group(2)})"
            else:
                # PROM online phase: cheap interpolated evaluation of the already-built
                # reduced-order model, not a real solve (the whole sweep runs in ~1-2s) -
                # still real progress through the output frequency list, just far cheaper
                # than "It i/n" would mean for a uniform sweep, hence the distinct label.
                self._status_freq_display = f"{m.group(1)}/{m.group(2)} (PROM eval)"
                self._status_solve_display = ""
            self._update_status_line()
            return

        m = self._RE_GREEDY.search(line)
        if m:
            # PROM offline phase: building the reduced-order model. No fixed total to show
            # progress against, so just the greedy-sampling iteration and sample count.
            self._status_freq_display = f"ROM build: iter {m.group(1)} (n={m.group(2)})"
            self._status_solve_display = ""
            self._update_status_line()
            return

        for amr_re in (self._RE_AMR_ITER_A, self._RE_AMR_ITER_B, self._RE_AMR_PROCEEDING):
            m = amr_re.search(line)
            if m and self._status_amr_max:
                # Palace's own log numbers the initial, unrefined-mesh solve as "iteration
                # 1" (confirmed earlier against DOF counts and palace_results.py's own
                # iteration1/iteration2/... folder naming). Displayed here shifted down by
                # 1 instead, so the initial solve reads "0" and each subsequent number
                # counts completed *refinements* - e.g. the 2nd refinement (Palace's
                # "iteration 3") reads "2/2" against a MaxIts=2 config, not "3/2". Not
                # clamped to amr_max: MaxIts caps refinement actions, not solve passes, so
                # the last legitimate pass is Palace's "iteration MaxIts+1" (displayed
                # "MaxIts") - showing whatever Palace actually reports is more honest than
                # silently capping it.
                self._status_amr_cur = int(m.group(1)) - 1
                self._update_status_line()
                return

    def _check_ram_limit(self):
        """Called every time self._status_mem_gb updates - the same latest-
        of-either-("current"-or-"peak")-kind value already shown on the live
        status line (see _parse_palace_status_line()/_update_status_line()).
        If it exceeds Preferences > Palace's "Stop Palace if memory exceeds"
        limit, kill the solver and let run_sim's own postprocessing step run
        on whatever it already computed (see
        _kill_palace_process_for_ram_limit()). Guarded by _ram_kill_triggered
        so this only ever fires once per run, even though more memory lines
        may still arrive before the kill actually takes effect.
        """
        if self._ram_kill_triggered or self._status_mem_gb is None:
            return
        if self.process.state() != QProcess.Running:
            return
        try:
            limit_gb = float(get_preference(self.MainWindow.APP_NAME, "palace_max_ram_gb", "100"))
        except (TypeError, ValueError):
            limit_gb = 100.0
        if limit_gb <= 0 or self._status_mem_gb <= limit_gb:
            return

        self._ram_kill_triggered = True
        # also re-appended at the very end of the log once the run actually
        # finishes (on_finished(), after the results summary) - by then
        # Palace's own remaining output plus combine_snp's could easily push
        # this first appearance out of view
        self._ram_kill_message = (
            f"⚠️ Palace memory usage ({self._status_mem_gb:.2f} GB) exceeded the "
            f"configured limit ({limit_gb:.0f} GB) - terminated the solver."
        )
        self.log_area.appendPlainText(
            f"\n{self._ram_kill_message}\n"
            "Running S-parameter postprocessing on whatever results were already computed...\n"
        )
        self._kill_palace_process_for_ram_limit()

    def _kill_palace_process_for_ram_limit(self):
        """Kill only the actual Palace solver process, not run_sim's own
        wrapper shell (self.process) - run_sim is a plain 2-line script
        (run_palace, then combine_snp) with no 'set -e', so bash continues
        to the combine_snp line regardless of how the first one exited,
        same as it already does today when run_palace simply isn't found
        (see _check_run_script_ready()'s docstring). Leaving self.process
        itself alone means that still happens here: on_finished() fires
        normally once run_sim's whole script (both lines) completes, same
        as any other run - no separate postprocessing step to launch.

        self.process (wsl.exe, or the shell directly on Linux/Mac) is only
        the wrapper - the real Palace binary(ies) run one or more layers
        deeper (inside WSL, and/or under mpirun for multi-rank runs), so
        this reaches in with pkill by process name instead of signaling
        self.process. Best-effort: if the actual binary/launcher name ever
        changes, or Palace is otherwise unkillable this way (hung mpirun,
        etc.), _escalate_ram_kill_if_still_running() force-terminates
        self.process (the whole wrapper) after a grace period instead -
        at the cost of combine_snp not getting to run in that fallback.
        """
        try:
            if os.name == "nt":
                run_path = saved_values['sim_path'] + "/palace_model/" + saved_values['model_basename'] + "_data"
                wsl_run_path = self._windows_to_wsl_path(run_path)
                subprocess.run(
                    ["wsl.exe", "--cd", wsl_run_path, "--", "bash", "-lc",
                     "pkill -f palace-x86_64 || pkill -f run_palace"],
                    capture_output=True, text=True, timeout=10)
            else:
                subprocess.run(["pkill", "-f", "palace-x86_64"], capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError) as e:
            self.log_area.appendPlainText(f"⚠️ Could not signal the Palace process directly: {e}\n")

        QTimer.singleShot(10000, self._escalate_ram_kill_if_still_running)

    def _escalate_ram_kill_if_still_running(self):
        if self.process.state() == QProcess.Running:
            self.log_area.appendPlainText(
                "⚠️ Palace did not stop within 10s of the memory-limit kill signal - "
                "forcefully terminating the run (S-parameter postprocessing will be skipped).\n"
            )
            self.terminate_run()

    def _update_status_line(self):
        mpi = self._status_mpi if self._status_mpi is not None else "n/a"
        try:
            mem_limit_gb = float(get_preference(self.MainWindow.APP_NAME, "palace_max_ram_gb", "100"))
        except (TypeError, ValueError):
            mem_limit_gb = 100.0
        mem = (f"{self._status_mem_gb:.2f}/{mem_limit_gb:.0f} GB"
               if self._status_mem_gb is not None else "n/a")
        port = (f"{self._status_port_cur}/{self._status_port_total}"
                if self._status_port_cur is not None else "n/a")
        freq = self._status_freq_display if self._status_freq_display is not None else "n/a"
        solve = self._status_solve_display
        amr = "n/a" if self._status_amr_max is None else f"{self._status_amr_cur}/{self._status_amr_max}"

        self.status_line.setText(
            f"MPI processes: {mpi}    |    Est. memory: {mem}    |    "
            f"Port: {port}    |    Freq: {freq}{solve}    |    AMR iteration: {amr}"
        )

    def open_model_fit(self):
        SNP2LE_URL = "https://github.com/iic-jku/snp2le"

        # invalidate_caches(): after a just-completed "pip install snp2le" run (see
        # on_finished() below), the import system's directory-listing cache for
        # site-packages can still be stale in this same process, so a find_spec()
        # right after install could still report "not found" without this.
        importlib.invalidate_caches()
        if importlib.util.find_spec("snp2le") is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Model Fit")
            box.setText("snp2le is not installed. Install it now with pip?")
            install_btn = box.addButton("Install", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(install_btn)
            box.exec()

            if box.clickedButton() is install_btn:
                self.log_area.appendPlainText("Installing snp2le with pip ...")
                self._process_purpose = "install_snp2le"
                self.process.start(sys.executable, ["-m", "pip", "install", "snp2le"])
            else:
                self.log_area.appendPlainText("⚠️ snp2le is not installed. Install it with: pip install snp2le")
                self.log_area.appendPlainText(SNP2LE_URL)
            return

        # local import: only needed once snp2le is actually launched, same lazy-import
        # style used by open_result_viewer() for its own heavy imports.
        if __package__ in (None, ""):
            from result_viewer import find_touchstone_files, is_amr_iteration_snapshot
        else:
            from .result_viewer import find_touchstone_files, is_amr_iteration_snapshot

        if self.MainWindow.PalaceMode:
            run_path = saved_values['sim_path'] + "/palace_model/" + saved_values['model_basename'] + "_data"
        else:
            run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"

        raw_files = [
            path for path in find_touchstone_files(run_path)
            if '_dc' not in os.path.basename(path) and '_deembedded' not in os.path.basename(path)
        ]

        if not raw_files:
            QMessageBox.warning(self, "Model Fit",
                                 f"No raw S-parameter result file found in {run_path}.\nRun a simulation first.")
            return

        # adaptive mesh refinement leaves one snapshot touchstone file per
        # iteration<N>/ subfolder alongside the final, fully-refined result
        # directly in run_path - prefer that final result over the snapshots.
        final_candidates = [p for p in raw_files if not is_amr_iteration_snapshot(p)] or raw_files
        if len(final_candidates) > 1:
            self.log_area.appendPlainText("⚠️ Multiple raw S-parameter result files found, using the newest one:")
            for path in final_candidates:
                self.log_area.appendPlainText("  " + path)
        raw_file = max(final_candidates, key=os.path.getmtime)

        # naming a .sNp file on the command line opens snp2le's GUI on it directly
        # (see https://github.com/iic-jku/snp2le/blob/main/doc/architecture.md);
        # still start it in that file's directory, so any relative paths it uses
        # (e.g. for exporting a fit) resolve there instead of wherever setupEM
        # happened to be launched from.
        raw_dir = os.path.dirname(raw_file)
        self.log_area.appendPlainText(f"Starting snp2le on {raw_file} ...")
        self.log_area.appendPlainText(SNP2LE_URL)
        self._process_purpose = "model_fit"
        self.process.setWorkingDirectory(raw_dir)
        self.process.start(sys.executable, ["-m", "snp2le", raw_file])

    def _append_results_summary(self):
        # Palace-only: parse palace.json / error-indicators.csv and append a results summary
        # to the log. Called from on_finished() after a real simulation run.
        run_path = saved_values['sim_path'] + "/palace_model/" + saved_values['model_basename'] + "_data"
        summary = build_results_summary(run_path, saved_values['model_basename'])
        self.log_area.appendPlainText("\n" + summary + "\n")

    def _update_viewer_button(self):
        if self.MainWindow.ElmerMode:
            visible = bool(saved_values.get('fdump_enabled'))
        else:
            visible = bool(saved_values.get('fdump'))
        self.viewer_btn.setVisible(visible)
        self._update_viewer_button_label()

    def _update_viewer_button_label(self):
        viewer_label = "ParaView" if self._viewer_preference() == "paraview" else "Built-in"
        self.viewer_btn.setText(f"🖼️ View fields ({viewer_label})...")

    def _viewer_preference(self):
        return get_preference(self.MainWindow.APP_NAME, "viewer_3d", "builtin")

    def load_values(self):
        super().load_values()
        self._update_viewer_button()

    def _resolve_palace_field_files(self):
        """(file_paths, not_found_message) for Palace's field-dump output - shared
        by launch_paraview() (external ParaView) and open_field_viewer() (in-app
        PyVista viewer), so both use the exact same file resolution."""
        run_path = saved_values['sim_path'] + "/palace_model/" + saved_values['model_basename'] + "_data"
        file_paths = find_paraview_files(run_path, saved_values['model_basename'])
        not_found = (
            f"⚠️ No Palace field-dump output found under "
            f"{find_output_dir(run_path, saved_values['model_basename'])}\n"
            "(set fdump to specific frequencies before running the simulation)\n"
        )
        return file_paths, not_found

    def _resolve_elmer_field_files(self):
        """(file_paths, not_found_message) for Elmer-as-EM-solver's field-dump
        output - shared by launch_paraview() (external ParaView) and
        open_field_viewer() (in-app PyVista viewer), same as
        _resolve_palace_field_files() for Palace. Usually resolves to more than
        one file: Elmer has no per-frequency .pvd collection like Palace, so
        each solved frequency's fields_t000N.vtu/.pvtu is its own separate file -
        the viewer's Result File picker lets the user choose between them."""
        run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"
        # Output File Name = File "fields" has no path prefix, so Elmer resolves it
        # relative to the Mesh DB directory ("mesh" under run_path) rather than
        # run_path itself - confirmed against a real run (same resolution mechanism
        # found for thermal_results.vtu). Check run_path too, defensively.
        search_dirs = [os.path.join(run_path, "mesh"), run_path]
        file_paths = []
        for pattern in ("fields*.pvd", "fields*.pvtu", "fields*.vtu"):
            for d in search_dirs:
                file_paths = sorted(glob.glob(os.path.join(d, pattern)))
                if file_paths:
                    break
            if file_paths:
                break
        not_found = (
            f"⚠️ No Elmer field-dump output found under {run_path}\n"
            "(enable field dump before running the simulation)\n"
        )
        return file_paths, not_found

    def open_viewer(self):
        """Dispatch "View fields ..." to whichever viewer applies - the single
        entry point the merged button calls (see _update_viewer_button_label()
        for how its text is kept in sync with this same logic)."""
        if self._viewer_preference() == "paraview":
            if find_paraview_exe() is not None:
                self.launch_paraview()
                return
            self.log_area.appendPlainText(
                "⚠️ ParaView is selected as the 3D viewer in Preferences, but wasn't "
                "found on this system (checked PATH and the usual install locations). "
                "Falling back to the built-in viewer. Install ParaView, add it to "
                "PATH, or switch back to \"Built-in\" under Preferences > Viewer > "
                "3D field viewer to avoid this message.\n"
            )
        self.open_field_viewer()

    def launch_paraview(self):
        if self.MainWindow.PalaceMode:
            file_paths, not_found = self._resolve_palace_field_files()
        else:
            file_paths, not_found = self._resolve_elmer_field_files()
        self._open_in_paraview(file_paths, not_found)

    def open_field_viewer(self):
        """Open the in-app PyVista 3D field viewer on the current mode's
        field-dump output - Palace's fdump or Elmer-as-EM-solver's fields*.vtu,
        see _resolve_palace_field_files()/_resolve_elmer_field_files()."""
        if __package__ in (None, ""):
            from field_viewer import FieldViewerWindow
        else:
            from .field_viewer import FieldViewerWindow

        if self.MainWindow.field_viewer_window is not None:
            self.MainWindow.field_viewer_window.raise_()
            self.MainWindow.field_viewer_window.activateWindow()
            return

        if self.MainWindow.PalaceMode:
            file_paths, not_found = self._resolve_palace_field_files()
            source = "palace"
        else:
            file_paths, not_found = self._resolve_elmer_field_files()
            source = "elmer_em"
        if not file_paths:
            self.log_area.appendPlainText(not_found)
            return

        # More than one file is common here: Palace can write a separate
        # "driven_boundary" collection alongside the main "driven" one, and Elmer
        # has one file per solved frequency instead of Palace's single .pvd
        # collection. Neither case has an inherently "right" default, so hand the
        # whole list to the viewer and let it offer a picker rather than silently
        # guessing which one the user wants.
        self.MainWindow.field_viewer_window = FieldViewerWindow(self.MainWindow, file_paths, source)
        self.MainWindow.field_viewer_window.destroyed.connect(
            lambda: setattr(self.MainWindow, "field_viewer_window", None))
        self.MainWindow.field_viewer_window.show()

    def on_finished(self, exit_code, exit_status):
        super().on_finished(exit_code, exit_status)
        # Auto-append the results summary after a real simulation run (not after mesh creation)
        if self._process_purpose == "run_simulation" and self.MainWindow.PalaceMode:
            self._append_results_summary()
            if self._ram_kill_triggered and self._ram_kill_message:
                # repeat the warning after the summary/combine_snp output that
                # followed it, so it's not lost above everything else logged
                # since the kill actually happened
                self.log_area.appendPlainText(f"\n{self._ram_kill_message}\n")
        elif self._process_purpose == "install_snp2le":
            importlib.invalidate_caches()
            if exit_code == 0 and importlib.util.find_spec("snp2le") is not None:
                self.log_area.appendPlainText("snp2le installed successfully.\n")
                # re-enter open_model_fit(): this time find_spec() succeeds, so it
                # goes straight to finding the raw result file and launching snp2le.
                self.open_model_fit()
            else:
                self.log_area.appendPlainText(
                    f"⚠️ snp2le installation failed (exit code {exit_code}). "
                    f"Try manually: pip install snp2le\n"
                )
        # self.process here is run_sim's whole script (run_palace THEN combine_snp,
        # run sequentially), so "finished" firing guarantees combine_snp has already
        # run - an already-open result viewer can safely rescan for real Touchstone
        # files now, seamlessly replacing any live port-S.csv preview it was showing.
        if self._process_purpose == "run_simulation" and self.MainWindow.result_viewer_window is not None:
            self.MainWindow.result_viewer_window._rescan_files()

    def on_process_error(self, error):
        super().on_process_error(error)
        # Covers QProcess.FailedToStart/Crashed, where "finished" may not fire the
        # same way - an open viewer left showing a "still running" live preview
        # should switch to the "stopped" wording rather than get stuck.
        if self.MainWindow.result_viewer_window is not None:
            self.MainWindow.result_viewer_window._rescan_files()

    def create_model(self):
        # Request all tabs to save values again,
        # which can do some update to saved_values
        self.MainWindow.save_all_tabs()

        if simulation_ports.portcount > 0 or saved_values['preview_only']:
            # save settings on this page to internal data structure
            self.save_values()
            # clear log
            self.log_area.clear()

            # get code from model editor tab
            self.MainWindow.modeleditor_tab.create_model_text()
            code = self.MainWindow.modeleditor_tab.model_edit.toPlainText().strip()
            if not code:
                self.log_area.appendPlainText("⚠️ No code to run.\n")
                return

            # Write code to Python file
            pymodel_filename = os.path.abspath(os.path.join(saved_values['sim_path'], saved_values['model_basename']+'.py'))

            # Refuse to overwrite an imported openEMS model script - setupEM can only
            # generate Palace/Elmer code and has no way to regenerate an openEMS model.
            # Normally load_configuration_from_file() already steers the output
            # elsewhere for such an import (see protected_source_model_path), so this
            # is a second-layer guard for the case where the user manually re-picks
            # the same name/directory on the Create Model(s) tab afterwards.
            protected_path = getattr(self.MainWindow, 'protected_source_model_path', None)
            if protected_path and os.path.normcase(pymodel_filename) == os.path.normcase(protected_path):
                QMessageBox.warning(
                    self, "Create Model",
                    "This would overwrite the imported openEMS model script:\n\n"
                    f"{pymodel_filename}\n\n"
                    "setupEM cannot regenerate an openEMS model, so this write was "
                    "blocked. Choose a different model name or output directory on "
                    "the Create Model(s) tab.")
                return

            # General overwrite protection: ask once per session before clobbering a
            # pre-existing file we haven't already confirmed/written ourselves. Always
            # on (not a preference) - once confirmed (or written), later Create Model
            # clicks to the same path in this session don't ask again, so normal
            # iterative tuning (tweak -> Create Model -> tweak -> Create Model ...)
            # isn't interrupted every time.
            normalized_path = os.path.normcase(pymodel_filename)
            confirmed_paths = self.MainWindow.confirmed_overwrite_paths
            if normalized_path not in confirmed_paths and os.path.exists(pymodel_filename):
                overwrite = QMessageBox.question(
                    self, "Create Model",
                    f"This will overwrite the existing file:\n\n{pymodel_filename}\n\nContinue?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                ) == QMessageBox.Yes
                if not overwrite:
                    return

            with open(pymodel_filename, "w", encoding="utf-8") as f:
                f.write(code)
                f.close()
            confirmed_paths.add(normalized_path)

            # Run Python interpreter on that file
            python_exe = sys.executable  # Use the same Python interpreter
            self._process_purpose = "create_mesh"
            self.process.start(python_exe, [pymodel_filename])


        else:
            QMessageBox.warning(self, "Error", "Model incomplete, there are no simulation ports defined!")



    def _confirm_clear_previous_results(self, run_path):
        """If run_path already holds solver output from a previous run, ask the user
        (default: delete) whether to remove it before starting a new simulation. Only
        ever touches solver-OUTPUT data - never the mesh/config/run script that "Create
        Mesh" just wrote, since those are still needed to start this run.

        Palace writes all of its results into one dedicated subdirectory (named in
        config.json's Problem.Output, resolved via find_output_dir()), so that whole
        subtree is the deletion target. Elmer's SaveScalars/ResultOutputSolver write
        bare filenames (no path prefix) in their .sif config, so Elmer resolves them
        relative to the Mesh DB directory ("mesh" under run_path) rather than run_path
        itself - confirmed against real runs (same resolution mechanism found for
        thermal_results.vtu, and combine_snp.py itself expects "scalar_results" under
        a "mesh" parent and writes the resulting .sNp file there too). run_path itself
        is also checked, defensively, in case some variant writes there directly.
        """
        if self.MainWindow.PalaceMode:
            output_dir = find_output_dir(run_path, saved_values['model_basename'])
            if not os.path.isdir(output_dir) or not os.listdir(output_dir):
                return
            targets = [output_dir]
        else:
            targets = []
            for search_dir in (os.path.join(run_path, "mesh"), run_path):
                if not os.path.isdir(search_dir):
                    continue
                for fn in os.listdir(search_dir):
                    full_path = os.path.join(search_dir, fn)
                    if not os.path.isfile(full_path):
                        continue
                    if fn in ("scalar_results", "scalar_results.names") or \
                       fn.startswith("fields") or re.search(r'\.s\d+p$', fn, re.IGNORECASE):
                        targets.append(full_path)
            if not targets:
                return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Previous simulation results found")
        box.setText(
            f"This run's output directory already contains results from a previous "
            f"simulation:\n\n{run_path}\n\nDelete the existing results before starting?"
        )
        delete_btn = box.addButton("Delete", QMessageBox.AcceptRole)
        box.addButton("Keep", QMessageBox.RejectRole)
        box.setDefaultButton(delete_btn)
        box.exec()

        if box.clickedButton() is delete_btn:
            for target in targets:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)

    def run_model(self):
        # Run model that we created before

        if self.MainWindow.PalaceMode:
            run_path = saved_values['sim_path'] + "/palace_model/" + saved_values['model_basename'] + "_data"
        else:
            run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"

        # ---------- pre-flight checks: fail fast, before asking to delete
        # previous results or touching QProcess at all - see setup_common.py's
        # _check_*/_check_wsl_* helpers for the shared checking logic ----------
        if self.MainWindow.PalaceMode:
            if self._check_run_script_ready(run_path, "run_sim") is None:
                return
            if os.name == "nt":
                if not self._check_wsl_ready():
                    return
                wsl_run_path = self._windows_to_wsl_path(run_path)
                if not self._check_wsl_commands_ready(wsl_run_path, ["run_palace", "combine_snp"]):
                    return
            else:
                palace_hint = ("Install Palace and make sure it is on PATH "
                                "(see the gds2palace scripts/README.md).")
                if self._check_command_on_path("run_palace", palace_hint) is None:
                    return
                if self._check_command_on_path("combine_snp", palace_hint) is None:
                    return
        else:
            elmer_script = "run_elmer.bat" if os.name == "nt" else "run_elmer"
            if self._check_run_script_ready(run_path, elmer_script) is None:
                return
            elmer_hint = "Install Elmer FEM and make sure ElmerSolver is on PATH."
            if self._check_command_on_path("ElmerSolver", elmer_hint) is None:
                return
            if saved_values.get('ELMER_MPI_THREADS', 1) > 1:
                if os.name == "nt":
                    mpi_hint = (
                        "On Windows, Elmer uses Microsoft MPI - download and install it "
                        "from https://learn.microsoft.com/en-us/message-passing-interface/microsoft-mpi "
                        "and restart setupEM, or switch to 1 thread (no multithreading) on "
                        "the Mesh and Boundaries tab."
                    )
                    if self._check_command_on_path("mpiexec", mpi_hint,
                                                    reason="This model requests MPI multithreading") is None:
                        return
                else:
                    mpi_hint = (
                        "Install an MPI implementation (e.g. OpenMPI or MPICH), "
                        "or switch to 1 thread (no multithreading) on the "
                        "Mesh and Boundaries tab."
                    )
                    if self._check_command_on_path("mpirun", mpi_hint,
                                                    reason="This model requests MPI multithreading") is None:
                        return

        try:
            self._confirm_clear_previous_results(run_path)

            # clear log
            self.log_area.clear()
            self._reset_status_for_run()
            self._process_purpose = "run_simulation"

            if self.MainWindow.PalaceMode:
                self.log_area.appendPlainText("Trying to start Palace using script ./run_sim now")

                if os.name == "nt":
                    #  Windows
                    wsl_run_path = self._windows_to_wsl_path(run_path)
                    self.log_area.appendPlainText("Running on Windows with WSL: starting ./run_sim ...")
                    self.log_area.appendPlainText("Note that this works for LOCAL drives only, we can't open WSL on network drive.\n")
                    # Run wsl.exe directly as self.process (no terminal emulator in between), so Palace's
                    # stdout/stderr stream into this log via the existing on_stdout/on_stderr handlers, and
                    # on_finished fires with the real exit code when ./run_sim actually completes -- instead
                    # of opening a detached terminal window, whose own quoting/tokenization rules (cmd's
                    # "start", and wt.exe's own use of ";" as a pane/command separator) make embedding a
                    # multi-step shell command fragile, and whose completion can't be tracked anyway.
                    # bash -lc: login shell so ~/.profile (where PATH additions for run_palace/combine_snp
                    # usually live, per gds2palace's scripts/README.md) gets sourced, same as a manually
                    # typed ./run_sim in a fresh WSL login shell would.
                    self.process.start("wsl.exe", [
                        "--cd", wsl_run_path,
                        "--", "bash", "-lc", "./run_sim"
                    ])
                else:
                    # Linux
                    self.log_area.appendPlainText('Setting work directory ' + run_path)
                    # make file executable
                    run_file = os.path.join(run_path, 'run_sim')
                    os.chmod(run_file, 0o755)

                    self.process.setWorkingDirectory(run_path)
                    # start simulation
                    self.process.start(".//run_sim")

            else:
                # Elmer mode
                self.log_area.appendPlainText('Setting work directory ' + run_path)

                if os.name == "nt":
                    #  Windows
                    self.process.setWorkingDirectory(run_path)
                    # start simulation - full path, not just "run_elmer.bat": Windows'
                    # CreateProcess resolves a bare relative program name against the
                    # CALLING process's own cwd/PATH, not the child's setWorkingDirectory(),
                    # so a bare filename here silently fails with FailedToStart even though
                    # setWorkingDirectory() is set correctly.
                    self.process.start(os.path.join(run_path, "run_elmer.bat"))
                else:
                    # Linux
                    # make file executable
                    run_file = os.path.join(run_path, 'run_elmer')
                    os.chmod(run_file, 0o755)

                    self.process.setWorkingDirectory(run_path)
                    # start simulation
                    self.process.start(".//run_elmer")

            # If the result viewer is already open, arm its live-preview polling
            # immediately rather than waiting for some other trigger (e.g. the window's
            # own showEvent(), which won't fire again for an already-visible window).
            if self.MainWindow.result_viewer_window is not None:
                self.MainWindow.result_viewer_window._rescan_files()
        except Exception as e:
            # defense in depth: the checks above cover every known missing-
            # prerequisite case, this is a last-resort net against anything
            # unanticipated, so it never surfaces as an uncaught traceback
            self.log_area.appendPlainText(f"⚠️ Unexpected error while starting the simulation: {e}\n")


class ModelEditorTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow # parent = MainWindow

        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignTop)

        self.model_edit = CodeEditor()
        self.main_layout.addWidget(self.model_edit)
        self.model_edit.setReadOnly(True)
        self.setLayout(self.main_layout)

    def create_model_text(self, forExport=False):
        # Create model text in editor from in-memory data
        # This can look different from imported Python model code

        def add_text(text):
            self.model_edit.appendPlainText(text)

        def add_key (key):
            value = str(saved_values[key])
            if '\\' in value:
                # make sure we don't run into escape character issue with Windows paths
                value = value.replace('\\','/')

            if key in ['fstart','fstop','fstep']:
                # special case: we have unit Hz in Python code but unit GHz in this GUI program internally
                value = str(value) + 'e9'

            if key in ['fdump','fpoint']:
                # special case: we have unit Hz in Python code but unit GHz in this GUI program internally
                # value is string representation of a list, so make it a list now
                flist = ast.literal_eval(value)
                new   = "["
                for n,f in enumerate(flist):
                    if n>0:
                        new = new + ","
                    new = new + str(f) + "e9"
                new = new + "]"
                value = new


            if isinstance(saved_values[key],str):
                # value is a string, enclose in quotes and check for backslash (Windows path!)
                add_text("settings['" + key + "'] = '" + value+ "'")
            else:
                add_text("settings['" + key + "'] = " + str(value) )


        # get folder where this GUI application is running and assume we have gds2palace modules there
        # this_app_path = os.path.abspath(os.path.join(os.path.dirname(__file__))).replace('\\','/')

        # for port tab to update data
        self.MainWindow.save_all_tabs()

        self.model_edit.clear()
        add_text("# Model for IHP OpenPDK EM workflow created using " + APP_NAME)
        add_text("import os, sys, subprocess")

        add_text("\nfrom gds2palace import *")

        add_text("\n# get path for this simulation file")
        add_text("script_path = utilities.get_script_path(__file__)")
        add_text("# use script filename as model basename")
        add_text("model_basename = utilities.get_basename(__file__)")
        add_text("# set and create directory for simulation output")

        if self.MainWindow.ElmerMode:
            add_text("sim_path = utilities.create_sim_path (script_path,model_basename,dirname='elmer_model')")
        else:
            add_text("sim_path = utilities.create_sim_path (script_path,model_basename)")

        add_text("\n# ========================= workflow settings ==========================")
        if forExport:
            add_text("# preview model/mesh only, without running solver?")
            add_text("start_simulation = False")
            add_text("\n# Command to start simulation")

            if self.MainWindow.PalaceMode:
                add_text("# run_command = ['start', 'wsl.exe']  # Windows Subsystem for Linux")
                add_text("run_command = ['./run_sim']         # Linux")
            elif self.MainWindow.ElmerMode:
                add_text("run_command = ['./run_elmer']     # Linux")

        add_text("\n# ===================== input files and settings =======================")
        add_text("settings={}")


        # List of keys that must be included in Python code AFTER reading stackups and GDSII, not before
        special_keylist = ['simulation_ports','materials_list','dielectrics_list','metals_list',
                           'layernumbers','allpolygons']
        # List of keys that we don't write to Python model code editor
        # fdump_enabled is a GUI-only toggle (Elmer mode), never a real gds2palace setting
        ignore_list     = ['model_basename','sim_path','fdump_enabled']


        # Keywords that are excluded in Palace mode
        if self.MainWindow.PalaceMode:
            ignore_list.append('iterative')

        if self.MainWindow.ElmerMode:
            # Elmer mode shows a plain on/off checkbox (fdump_enabled) instead of Palace's
            # per-frequency fdump list - suppress the generic per-key emission of fdump
            # here so a stale Palace-mode list saved earlier in the same session doesn't
            # leak into an Elmer-mode script; the synthesized settings['fdump'] line
            # (below, after special_keylist) is emitted instead when the checkbox is on.
            ignore_list.append('fdump')

        if forExport:
            # these commands are only used within this GUI application to control gmsh
            ignore_list.extend(['preview_only','no_preview'])

        for key in saved_values.keys():
            if not key in special_keylist:
                if not key in ignore_list:
                    add_key(key)

        if self.MainWindow.ElmerMode and bool(saved_values.get('fdump_enabled')):
            # Elmer has no per-frequency SaveStep like Palace - it dumps fields at every
            # solved frequency once any fdump value is set, so reuse an already-solved
            # frequency here rather than asking the user to pick one. This is harmless
            # (adds no extra solve) as long as util_elmer.write_elmer_frequencies()
            # dedupes the combined frequency list, which it does.
            # Known limitation: this line's RHS references "settings", so
            # parse_assignments() (setup_common.py) skips it entirely on Import -
            # re-importing an exported Elmer+fdump script loses fdump_enabled (loads
            # unchecked). Not fixed here since it's a silent no-op, not a crash.
            add_text("\n# 'Enable field dump' checkbox: Elmer dumps fields at every solved")
            add_text("# frequency once fdump is non-empty, so reuse an already-solved one")
            if 'fstop' in saved_values:
                add_text("settings['fdump'] = [settings['fstop']]")
            elif saved_values.get('fpoint'):
                add_text("settings['fdump'] = [settings['fpoint'][0]]")


        add_text("\n# ===================== port definitions =======================")
        add_text("simulation_ports = simulation_setup.all_simulation_ports()")
        for sim_port in simulation_ports.ports:
            if "Z" in sim_port.direction.upper():
                add_text(f"simulation_ports.add_port(simulation_setup.simulation_port("
                         f"portnumber={str(sim_port.portnumber)}, "
                         f"voltage={str(sim_port.voltage)}, "
                         f"port_Z0={str(sim_port.port_Z0)}, "
                         f"source_layernum={str(sim_port.source_layernum)}, "
                         f"from_layername='{sim_port.from_layername}', "
                         f"to_layername='{sim_port.to_layername}', "
                         f"direction='{sim_port.direction}'))")
            else:
                add_text(f"simulation_ports.add_port(simulation_setup.simulation_port("
                         f"portnumber={str(sim_port.portnumber)}, "
                         f"voltage={str(sim_port.voltage)}, "
                         f"port_Z0={str(sim_port.port_Z0)}, "
                         f"source_layernum={str(sim_port.source_layernum)}, "
                         f"target_layername='{sim_port.target_layername}', "
                         f"direction='{sim_port.direction}'))")

        add_text("\n# ================= read stackup and geometries =================")
        add_text("materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate (settings['SubstrateFile'], variable_overrides=settings['variable_overrides'])")
        add_text("layernumbers = metals_list.getlayernumbers()")
        add_text("layernumbers.extend(simulation_ports.portlayers)")
        add_text("\n# read geometries from GDSII")
        add_text("allpolygons = gds_reader.read_gds(settings['GdsFile'], "
                "\n\tlayernumbers,"
                "\n\tcellname=settings['cellname'], "
                "\n\tpurposelist=settings['purpose'], "
                "\n\tmetals_list=metals_list, \n\tpreprocess=settings['preprocess_gds'], "
                "\n\tmerge_polygon_size=settings['merge_polygon_size'],"
                "\n\tgds_boundary_layers=dielectrics_list.get_boundary_layers(),"
                "\n\tmirror=False, "
                "\n\toffset_x=0, offset_y=0,"
                "\n\tlayernumber_offset=0)")
        add_text("\n")


        # Now do the special keys that we skipped before
        for key in special_keylist:
            add_text("settings['" + key + "'] = " + key)
        add_text("settings['sim_path'] = sim_path")
        add_text("settings['model_basename'] = model_basename")


        # Now create ports
        add_text("\n# list of ports that are excited (set voltage to zero in port excitation to skip an excitation!)")
        add_text("excite_ports = simulation_ports.all_active_excitations()")

        if self.MainWindow.ElmerMode:
            add_text("config_name, data_dir = simulation_setup.create_elmer (excite_ports, settings)")
        else:
            add_text("config_name, data_dir = simulation_setup.create_palace (excite_ports, settings)")

        # Palace, add helper function to start simulation from script
        if self.MainWindow.PalaceMode:
            add_text("\n# for convenience, write run script to model directory")
            add_text("utilities.create_run_script(settings['sim_path'])")

            # When running the model from setupEM GUI, we start the script differently, only write this for export
            if forExport:
                add_text("\n# run after creating mesh and Palace config.json ")
                add_text("if start_simulation:")
                add_text("  try:")
                add_text("      os.chdir(sim_path)")
                add_text("      if sys.platform.startswith('linux'):")
                add_text("          os.chmod('run_sim', 0o755)")
                add_text("      subprocess.run(run_command, shell=True)")
                add_text("  except:")
                add_text("      print(f'Unable to run Palace using command ',run_command)\n")

        # Elmer, add helper function to start simulation from script
        if self.MainWindow.ElmerMode:
            add_text("\n# for convenience, write run script to model directory")
            add_text("utilities.create_elmer_run_script(settings['sim_path'],settings)")

            # When running the model from setupEM GUI, we start the script differently, only write this for export
            if forExport:
                add_text("\n# run after creating mesh and Elmer model files ")
                add_text("if start_simulation:")
                add_text("  try:")
                add_text("      os.chdir(sim_path)")
                add_text("      if sys.platform.startswith('linux'):")
                add_text("          os.chmod('run_elmer', 0o755)")
                add_text("      subprocess.run(run_command, shell=True)")
                add_text("  except:")
                add_text("      print(f'Unable to run Elmer using command ',run_command)\n")



    def save_values(self):
        self.create_model_text(forExport=True)  # show "external" code including run from Python model
        return True

    def load_values(self):
        self.create_model_text(forExport=True)  # show "external" code including run from Python model


# ---------- PREFERENCES DIALOG ----------

class PreferencesDialog(QDialog):
    """File > Preferences ...: per-user defaults for fields that used to be plain
    hardcoded literals (e.g. FrequenciesTab's fstart/fstop). Persisted via
    get_preference()/set_preference() (setup_common.py) - a dedicated QSettings
    store, separate from *.simcfg project files and from "Save as Default Config".
    Editing a value here only changes what a brand-new/blank field starts out
    showing; it never touches the currently open project's saved_values.
    """

    def __init__(self, MainWindow):
        super().__init__(MainWindow)
        self.MainWindow = MainWindow
        self.app_name = MainWindow.APP_NAME
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(420)

        outer_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer_layout.addWidget(self.tabs)

        label_width = 260

        # (widget, key, default, kind) for every preference-backed field in this
        # dialog, so "Reset all to default" (see _reset_to_defaults()) can put
        # every widget back to its built-in default without hand-listing them
        # again separately - add_row() below appends "text" entries itself;
        # each standalone QCheckBox appends its own "bool" entry.
        self._reset_targets = []

        def add_row(form_layout, label_text, key, default, tooltip=None):
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(label_width)
            row.addWidget(label)
            edit = QLineEdit(str(get_preference(self.app_name, key, default)))
            edit.setStyleSheet(EDIT_STYLE_REQUIRED)
            row.addWidget(edit)
            if tooltip:
                label.setToolTip(tooltip)
                edit.setToolTip(tooltip)
            form_layout.addLayout(row)
            self._reset_targets.append((edit, key, default, "text"))
            return edit

        # ---------- Files tab ----------
        files_widget = QWidget()
        files_form = QVBoxLayout(files_widget)
        files_form.setAlignment(Qt.AlignTop)

        xml_dir_row = QHBoxLayout()
        xml_dir_label = QLabel("XML file browser starts from")
        xml_dir_label.setFixedWidth(label_width)
        xml_dir_row.addWidget(xml_dir_label)
        self.xml_browse_dir_edit = QLineEdit(str(get_preference(self.app_name, "xml_browse_directory", "")))
        self.xml_browse_dir_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.xml_browse_dir_edit.setPlaceholderText("(bundled with setupEM)")
        self.xml_browse_dir_edit.setToolTip(
            "Folder the XML Stackup File browse dialog starts from. "
            "Leave empty to use the XML files bundled with setupEM."
        )
        xml_dir_row.addWidget(self.xml_browse_dir_edit, 1)
        self.xml_browse_dir_btn = QPushButton("Browse ...")
        self.xml_browse_dir_btn.clicked.connect(self._browse_xml_default_dir)
        xml_dir_row.addWidget(self.xml_browse_dir_btn)
        files_form.addLayout(xml_dir_row)
        self._reset_targets.append((self.xml_browse_dir_edit, "xml_browse_directory", "", "text"))

        self.purpose_edit = add_row(files_form, "Default GDS layer purpose", "purpose", "0")
        self.viamerge_edit = add_row(files_form, "Default via array merge distance (µm)", "merge_polygon_size", "0.5")
        self.confirm_reuse_checkbox = QCheckBox("Ask before reusing an imported model's filename as the output file")
        self.confirm_reuse_checkbox.setChecked(get_preference_bool(self.app_name, "confirm_reuse_import_filename", False))
        files_form.addWidget(self.confirm_reuse_checkbox)
        self._reset_targets.append((self.confirm_reuse_checkbox, "confirm_reuse_import_filename", False, "bool"))

        files_form.addStretch()
        self.tabs.addTab(files_widget, "Files")

        # ---------- Ports tab ----------
        ports_widget = QWidget()
        ports_form = QVBoxLayout(ports_widget)
        ports_form.setAlignment(Qt.AlignTop)
        self.port_layer_min_edit = add_row(ports_form, "Auto-assign source layer range: min", "port_layer_min", "201")
        self.port_layer_max_edit = add_row(ports_form, "Auto-assign source layer range: max", "port_layer_max", "299")
        ports_form.addStretch()
        self.tabs.addTab(ports_widget, "Ports")

        # ---------- Frequencies tab ----------
        freq_widget = QWidget()
        freq_form = QVBoxLayout(freq_widget)
        freq_form.setAlignment(Qt.AlignTop)
        self.fstart_edit = add_row(freq_form, "Default fstart (GHz)", "fstart", "0")
        self.fstop_edit = add_row(freq_form, "Default fstop (GHz)", "fstop", "50")
        freq_form.addStretch()
        self.tabs.addTab(freq_widget, "Frequencies")

        # ---------- Mesh tab ----------
        mesh_widget = QWidget()
        mesh_form = QVBoxLayout(mesh_widget)
        mesh_form.setAlignment(Qt.AlignTop)
        self.refined_cellsize_edit = add_row(mesh_form, "Mesh refinement at metal edges (µm)", "refined_cellsize", "5")
        self.cells_per_wavelength_edit = add_row(mesh_form, "Mesh cells per wavelength", "cells_per_wavelength", "10")
        self.meshsize_max_edit = add_row(mesh_form, "Mesh cell maximum size (µm)", "meshsize_max", "100")
        self.adaptive_mesh_iterations_edit = add_row(mesh_form, "Adaptive mesh iterations", "adaptive_mesh_iterations", "0")
        self.margin_edit = add_row(mesh_form, "Dielectric stackup oversize margin (µm)", "margin", "200")
        self.air_around_edit = add_row(mesh_form, "Air layer thickness around stackup (µm)", "air_around", "")
        self.air_around_edit.setPlaceholderText("same as dielectric margin")
        self.air_around_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        mesh_form.addStretch()
        self.tabs.addTab(mesh_widget, "Mesh")

        # ---------- Palace tab ----------
        palace_widget = QWidget()
        palace_form = QVBoxLayout(palace_widget)
        palace_form.setAlignment(Qt.AlignTop)
        self.amr_goal_edit = add_row(
            palace_form, "AMR goal (relative error tolerance)", "amr_tol", "0.01",
            tooltip=("Target relative error of Palace's own mesh error estimator "
                     "(Norm/Max/Mean indicators) - not a change in S-parameters "
                     "between AMR iterations"))
        self.amr_maxdof_edit = add_row(palace_form, "AMR maximum DOF", "amr_max_dof", "2000000")
        self.palace_max_ram_edit = add_row(
            palace_form, "Stop Palace if memory exceeds (GB)", "palace_max_ram_gb", "100",
            tooltip=("Terminates the solver once its reported memory usage exceeds this, "
                     "then runs S-parameter postprocessing on whatever results were "
                     "already computed, same as a normal completed run."))
        palace_form.addStretch()
        self.tabs.addTab(palace_widget, "Palace")

        # ---------- Viewer tab ----------
        create_widget = QWidget()
        create_form = QVBoxLayout(create_widget)
        create_form.setAlignment(Qt.AlignTop)
        self.enable_model_fit_checkbox = QCheckBox('Show "Model Fit ..." button')
        self.enable_model_fit_checkbox.setChecked(get_preference_bool(self.app_name, "enable_model_fit_button", True))
        create_form.addWidget(self.enable_model_fit_checkbox)
        self._reset_targets.append((self.enable_model_fit_checkbox, "enable_model_fit_button", True, "bool"))
        self.enable_status_bar_checkbox = QCheckBox("Show live solver status line (Palace mode)")
        self.enable_status_bar_checkbox.setChecked(get_preference_bool(self.app_name, "enable_status_bar", True))
        create_form.addWidget(self.enable_status_bar_checkbox)
        self._reset_targets.append((self.enable_status_bar_checkbox, "enable_status_bar", True, "bool"))

        # Which viewer "View fields ..." opens - built-in (embedded PyVista, Palace
        # mode only for now) or external ParaView (works for both Palace and Elmer-
        # as-EM-solver field dumps). Falls back to the built-in viewer, with a
        # message, if ParaView is set here but isn't actually found on this system
        # (see CreateModelTab.open_viewer()) - not enforced here, since ParaView
        # availability can change without reopening Preferences.
        viewer_row = QHBoxLayout()
        viewer_label = QLabel("3D field viewer")
        viewer_label.setFixedWidth(label_width)
        viewer_row.addWidget(viewer_label)
        self.viewer_3d_combo = QComboBox()
        self.viewer_3d_combo.addItem("Built-in", "builtin")
        self.viewer_3d_combo.addItem("ParaView", "paraview")
        current_viewer = get_preference(self.app_name, "viewer_3d", "builtin")
        combo_index = self.viewer_3d_combo.findData(current_viewer)
        self.viewer_3d_combo.setCurrentIndex(combo_index if combo_index >= 0 else 0)
        viewer_row.addWidget(self.viewer_3d_combo)
        create_form.addLayout(viewer_row)
        self._reset_targets.append((self.viewer_3d_combo, "viewer_3d", "builtin", "combo"))

        create_form.addStretch()
        self.tabs.addTab(create_widget, "Viewer")

        # ---------- Simplify GDS tab ----------
        simplify_widget = QWidget()
        simplify_form = QVBoxLayout(simplify_widget)
        simplify_form.setAlignment(Qt.AlignTop)
        self.simplify_max_hole_area_edit = add_row(
            simplify_form, "Maximum cutout area to remove (µm²)", "simplify_max_hole_area", "1")
        self.simplify_max_hole_area_edit.setPlaceholderText("blank = remove all cutouts")
        self.simplify_max_hole_area_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.simplify_fill_maxsize_edit = add_row(
            simplify_form, "Maximum floating fill size (µm)", "simplify_fill_maxsize", "20")
        self.simplify_fill_maxsize_edit.setPlaceholderText("blank = no size limit")
        self.simplify_fill_maxsize_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.simplify_excluded_layers_edit = add_row(
            simplify_form, "Layers excluded from simplification", "simplify_excluded_layers", "")
        self.simplify_excluded_layers_edit.setPlaceholderText("e.g. 10,11 - blank = none")
        self.simplify_excluded_layers_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.simplify_merge_per_layer_checkbox = QCheckBox("Merge polygons per layer (final step)")
        self.simplify_merge_per_layer_checkbox.setChecked(
            get_preference_bool(self.app_name, "simplify_merge_per_layer", True))
        simplify_form.addWidget(self.simplify_merge_per_layer_checkbox)
        self._reset_targets.append((self.simplify_merge_per_layer_checkbox, "simplify_merge_per_layer", True, "bool"))
        simplify_form.addStretch()
        self.tabs.addTab(simplify_widget, "Simplify GDS")

        # widen enough that every tab label fits without scroll arrows - a fixed
        # pixel guess doesn't survive different fonts/DPI scaling, so measure the
        # actual tab bar instead, now that every tab has been added
        self.setMinimumWidth(max(420, self.tabs.tabBar().sizeHint().width() + 40))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # ResetRole: Qt places this on the opposite side from Ok/Cancel's
        # Accept/Reject roles (the left, on every style this app runs under),
        # matching the "very left side, near Ok/Cancel" placement asked for -
        # no separate layout needed, the button box's own role logic handles it
        self.reset_button = buttons.addButton("Reset all to default", QDialogButtonBox.ResetRole)
        self.reset_button.clicked.connect(self._reset_to_defaults)
        outer_layout.addWidget(buttons)

    def _browse_xml_default_dir(self):
        start = self.xml_browse_dir_edit.text() or os.path.join(os.path.dirname(__file__), "data")
        directory = QFileDialog.getExistingDirectory(self, "Select Default XML Folder", start)
        if directory:
            self.xml_browse_dir_edit.setText(directory)

    def _reset_to_defaults(self):
        confirm = QMessageBox.question(
            self, "Reset Preferences",
            "Reset all Preferences to their built-in defaults?\n\n"
            "This clears every value you've changed here - it cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return
        clear_preferences(self.app_name)
        for widget, _key, default, kind in self._reset_targets:
            if kind == "text":
                widget.setText(str(default))
            elif kind == "bool":
                widget.setChecked(bool(default))
            elif kind == "combo":
                combo_index = widget.findData(default)
                widget.setCurrentIndex(combo_index if combo_index >= 0 else 0)
        # live-apply immediately (matches what accept() does), so the running
        # app reflects the reset right away rather than needing OK afterward
        self.MainWindow.create_model_tab.apply_preference_visibility()

    def accept(self):
        # via-merge distance and the two mesh sizes must parse as numbers; everything
        # else here (purpose, fstart/fstop, AMR iterations, air_around) is stored as
        # free text, same tolerant convention already used by the tabs themselves
        try:
            float(self.viamerge_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for via array merge distance")
            return
        try:
            port_layer_min = int(self.port_layer_min_edit.text())
            port_layer_max = int(self.port_layer_max_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value in the Ports tab")
            return
        if port_layer_min > port_layer_max:
            QMessageBox.warning(self, "Error", "Ports: min layer must not be greater than max layer")
            return
        try:
            float(self.refined_cellsize_edit.text())
            float(self.cells_per_wavelength_edit.text())
            float(self.meshsize_max_edit.text())
            float(self.margin_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value in the Mesh tab")
            return
        if self.air_around_edit.text() != "":
            try:
                float(self.air_around_edit.text())
            except Exception:
                QMessageBox.warning(self, "Error", "Not a valid value for air layer thickness")
                return
        try:
            float(self.amr_goal_edit.text())
            int(self.amr_maxdof_edit.text())
            max_ram_gb = float(self.palace_max_ram_edit.text())
            if max_ram_gb <= 0:
                raise ValueError
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value in the Palace tab")
            return

        set_preference(self.app_name, "purpose", self.purpose_edit.text())
        set_preference(self.app_name, "confirm_reuse_import_filename", self.confirm_reuse_checkbox.isChecked())
        set_preference(self.app_name, "xml_browse_directory", self.xml_browse_dir_edit.text())
        set_preference(self.app_name, "merge_polygon_size", self.viamerge_edit.text())
        set_preference(self.app_name, "port_layer_min", self.port_layer_min_edit.text())
        set_preference(self.app_name, "port_layer_max", self.port_layer_max_edit.text())
        set_preference(self.app_name, "fstart", self.fstart_edit.text())
        set_preference(self.app_name, "fstop", self.fstop_edit.text())
        set_preference(self.app_name, "refined_cellsize", self.refined_cellsize_edit.text())
        set_preference(self.app_name, "cells_per_wavelength", self.cells_per_wavelength_edit.text())
        set_preference(self.app_name, "meshsize_max", self.meshsize_max_edit.text())
        set_preference(self.app_name, "adaptive_mesh_iterations", self.adaptive_mesh_iterations_edit.text())
        set_preference(self.app_name, "margin", self.margin_edit.text())
        set_preference(self.app_name, "air_around", self.air_around_edit.text())
        set_preference(self.app_name, "amr_tol", self.amr_goal_edit.text())
        set_preference(self.app_name, "amr_max_dof", self.amr_maxdof_edit.text())
        set_preference(self.app_name, "palace_max_ram_gb", self.palace_max_ram_edit.text())
        set_preference(self.app_name, "enable_model_fit_button", self.enable_model_fit_checkbox.isChecked())
        set_preference(self.app_name, "enable_status_bar", self.enable_status_bar_checkbox.isChecked())
        set_preference(self.app_name, "viewer_3d", self.viewer_3d_combo.currentData())
        set_preference(self.app_name, "simplify_max_hole_area", self.simplify_max_hole_area_edit.text())
        set_preference(self.app_name, "simplify_fill_maxsize", self.simplify_fill_maxsize_edit.text())
        set_preference(self.app_name, "simplify_excluded_layers", self.simplify_excluded_layers_edit.text())
        set_preference(self.app_name, "simplify_merge_per_layer", self.simplify_merge_per_layer_checkbox.isChecked())

        # live update, no restart needed
        self.MainWindow.create_model_tab.apply_preference_visibility()

        super().accept()


# ---------- MAIN WINDOW ----------


class MainWindow(MainWindowBase):
    APP_NAME = APP_NAME
    CONFIG_SUFFIX = CONFIG_SUFFIX
    DEFAULT_SETTINGS_FILE = DEFAULT_SETTINGS_FILE

    def __init__(self):
        super().__init__()

        # this MainWindow's saved_values dict, shared with the module-level
        # saved_values global (same dict object) so both stay in sync
        self.saved_values = saved_values

        self.PalaceMode = True
        self.ElmerMode = False

        Title = APP_NAME

        if self.PalaceMode:
            Title = Title + ' Palace'
        elif self.ElmerMode:
            Title = Title + ' Elmer'

        self.setWindowTitle(Title)
        self.setGeometry(100, 100, 750, 700)

        # --- Menu Bar ---
        self.create_menu_bar()

        # --- Central Widget ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        self.tabs_widget = QTabWidget()
        self.tabs_widget.setTabPosition(QTabWidget.North)
        self.tabs_widget.setMovable(False)

        # Tabs
        self.file_tab = FileInputTab(self)
        self.frequencies_tab = FrequenciesTab(self)
        self.ports_tab = PortsTab(self)
        self.mesh_tab = MeshTab(self)
        self.create_model_tab = CreateModelTab(self)
        self.modeleditor_tab = ModelEditorTab(self)

        # Add tabs
        self.tabs_widget.addTab(self.file_tab, "Input Files")
        self.tabs_widget.addTab(self.frequencies_tab, "Frequencies")
        self.tabs_widget.addTab(self.ports_tab, "Ports")
        self.tabs_widget.addTab(self.mesh_tab, "Mesh and Boundaries")
        self.tabs_widget.addTab(self.create_model_tab, "Create Model")
        self.tabs_widget.addTab(self.modeleditor_tab, "Code")

        self.apply_tab_header_colors()
        self._previous_index = 0
        self.tabs_widget.currentChanged.connect(self.on_tab_change)
        main_layout.addWidget(self.tabs_widget)

        # Stackup data from XML, used when showing stackup viewer
        self.materials_list = None
        self.dielectrics_list = None
        self.metals_list = None

        # S-parameter Result Viewer window, lazily created - see open_result_viewer()
        self.result_viewer_window = None

        # 3D Field Result Viewer window (PyVista), lazily created - see
        # CreateModelTab.open_field_viewer(). Palace mode only for now - see that
        # method's docstring.
        self.field_viewer_window = None

        # Do not auto-load default values at this early startup stage,
        # instead this is done from File menu
        # self.user_inputs_file = DEFAULT_SETTINGS_FILE
        # self.user_inputs = self.load_user_inputs(DEFAULT_SETTINGS_FILE)
        # saved_values.update(self.user_inputs)

        # Load all saved data into tabs
        self.load_all_tabs()

    # ---------- Menu Bar (Simulator menu is EM-specific) ----------
    def create_additional_menus(self, menu_bar):
        # menu to choose simulator
        self.simulator_menu = menu_bar.addMenu("&Simulator")
        simulator_group = QActionGroup(self)

        self.optionPalace = QAction("Palace FEM", self, checkable=True)
        self.optionElmer  = QAction("Elmer FEM", self, checkable=True)

        self.optionPalace.triggered.connect(lambda: self.setPalaceMode())
        self.optionElmer.triggered.connect(lambda: self.setElmerMode())

        simulator_group.addAction(self.optionPalace)
        simulator_group.addAction(self.optionElmer)
        # Palace is default
        self.optionPalace.setChecked(True)
        self.simulator_menu.addActions(simulator_group.actions())


    # ---------- Menu actions ----------
    def setPalaceMode(self):
        self.optionPalace.setChecked(True)
        self.PalaceMode = True
        self.ElmerMode  = False
        self.setWindowTitle(APP_NAME + ' Palace')
        self.frequencies_tab.dump_group.setVisible(True)
        self.frequencies_tab.dump_group.setTitle("Optional list of fixed frequencies creating field dump data for visualization (Paraview files))")
        self.frequencies_tab.fdump_label.setVisible(True)
        self.frequencies_tab.fdump_edit.setVisible(True)
        self.frequencies_tab.fdump_enabled_checkbox.setVisible(False)
        self.mesh_tab.AMR_group.setVisible(True)
        self.mesh_tab.Elmer_group.setVisible(False)
        self.create_model_tab.apply_preference_visibility()

        # update mesh settings that are not always visible
        self.mesh_tab.on_meshorder_changed(self.mesh_tab.mesh_order_box.currentText())


    def setElmerMode(self):
        self.optionElmer.setChecked(True)
        self.PalaceMode = False
        self.ElmerMode  = True
        self.setWindowTitle(APP_NAME + ' Elmer')
        # Elmer has no per-sample SaveStep like Palace - any fdump value turns on
        # field-dump output at every solved frequency, so it shows a plain on/off
        # checkbox here instead of Palace's per-frequency fdump list.
        self.frequencies_tab.dump_group.setVisible(True)
        self.frequencies_tab.dump_group.setTitle("Create field dump data for visualization (Paraview files)")
        self.frequencies_tab.fdump_label.setVisible(False)
        self.frequencies_tab.fdump_edit.setVisible(False)
        self.frequencies_tab.fdump_enabled_checkbox.setVisible(True)
        self.mesh_tab.AMR_group.setVisible(False)
        self.mesh_tab.Elmer_group.setVisible(True)
        self.create_model_tab.apply_preference_visibility()

        # update mesh settings that are not always visible
        self.mesh_tab.on_meshorder_changed(self.mesh_tab.mesh_order_box.currentText())


    def open_result_viewer(self):
        # local import: matplotlib/skrf are only needed once the viewer is actually
        # opened, so this keeps them off setupEM's startup path. __package__ is
        # None/"" when this module was loaded outside the setupEM package (e.g.
        # setupEM.py run directly), so relative import fails - same dual-mode
        # pattern used throughout this file/setup_common.py for sibling imports.
        if __package__ in (None, ""):
            from result_viewer import ResultViewerWindow
        else:
            from .result_viewer import ResultViewerWindow

        if self.result_viewer_window is not None:
            self.result_viewer_window.raise_()
            self.result_viewer_window.activateWindow()
            return

        self.result_viewer_window = ResultViewerWindow(self)
        self.result_viewer_window.destroyed.connect(lambda: setattr(self, "result_viewer_window", None))
        self.result_viewer_window.show()


    def show_version(self):
        setupEM_version = self.get_setupEM_version()
        gds2palace_version = self.get_gds2palace_version()
        # snp2le (used by Model Fit) is an optional dependency, not installed by default
        try:
            snp2le_version = importlib.metadata.version("snp2le")
        except importlib.metadata.PackageNotFoundError:
            snp2le_version = "not installed"
        version_info = f"Installed:\nsetupEM {setupEM_version}\ngds2palace {gds2palace_version}\nsnp2le {snp2le_version}"

        # get latest available version information
        latest_setupEM = self.get_latest_version("setupEM")
        latest_gds2palace = self.get_latest_version("gds2palace")
        latest_info = f"Latest version:\nsetupEM {latest_setupEM}\ngds2palace : {latest_gds2palace}"
        upgrade_info = "\n\nYou can update using\n  pip install gds2palace --upgrade\n  pip install setupEM --upgrade"
        if snp2le_version != "not installed":
            # only worth checking/offering an upgrade for a package the user actually has
            latest_snp2le = self.get_latest_version("snp2le")
            latest_info += f"\nsnp2le {latest_snp2le}"
            upgrade_info += "\n  pip install snp2le --upgrade"
        upgrade_info += '\nafter exiting this program\n'    
        version_info = version_info + '\n\n' + latest_info

        QMessageBox.information(self,"Version information",version_info + upgrade_info)


    # get_latest_version() lives in MainWindowBase (setup_common.py), which
    # also bounds the PyPI request with a timeout and try/except


    # ---------- Native config (*.simcfg) / Python import hooks ----------
    def apply_native_config_data(self, data):
        # update ports, they are separate from the other internal data
        self.ports_tab.update_port_from_import(data.get("ports"))
        # restore simulator mode (not part of saved_values, see native_config_extra_struct)
        if data.get("elmer_mode", False):
            self.setElmerMode()
        else:
            self.setPalaceMode()

    def apply_python_import_data(self, file_path):
        # read port assignments in workflow syntax for gds2palace Python code
        ports = parse_python_ports_definitions(file_path)
        self.ports_tab.update_port_from_import(ports)

        # Elmer/Palace mode isn't captured by the general settings-dict import
        # (parse_assignments() skips lines whose value contains "settings", and
        # 'elmer' isn't in import_mapping anyway), so detect it directly from the
        # source text instead. Two valid ways a script selects Elmer mode:
        #  - calling simulation_setup.create_elmer(...) (what create_model_text()
        #    itself generates, with a space before "(" - a plain "create_elmer("
        #    substring check never matches that)
        #  - setting settings['elmer'] = True directly and calling create_model()
        #    itself (create_elmer() is only a thin wrapper that sets this same flag)
        with open(file_path) as f:
            text = f.read()
        elmer_call = re.search(r'create_elmer\s*\(', text)
        elmer_flag = re.search(r'settings\s*\[\s*[\'"]elmer[\'"]\s*\]\s*=\s*True\b', text, re.IGNORECASE)
        if elmer_call or elmer_flag:
            self.setElmerMode()
        else:
            self.setPalaceMode()

    def native_config_extra_struct(self):
        return {"ports": simulation_ports_to_struct(simulation_ports), "elmer_mode": self.ElmerMode}

    def update_target_layer_choices(self, metals_list):
        self.ports_tab.update_layers(metals_list)

    def refresh_source_layer_hints(self):
        self.ports_tab.refresh_missing_layer_annotations()
        self.ports_tab.portslist_selection_changed()


    # ---------- Layout Preview hook ----------
    def get_layout_preview_markers(self):
        # flush the Ports tab's current table edits into simulation_ports first,
        # so the preview reflects unsaved edits without requiring a tab switch
        self.ports_tab.save_values()
        markers = simulation_ports_to_struct(simulation_ports)
        for marker in markers:
            marker["kind"] = "port"
            marker["group"] = "Ports"
        return markers


    # ---------- Preferences dialog hook ----------
    def open_preferences_dialog(self):
        dialog = PreferencesDialog(self)
        dialog.exec()


    # ---------- Stackup preview hooks (permittivity / sheet resistance) ----------
    def stackup_dielectric_color(self, material):
        return epsilon_to_color(material.eps, 95)

    def stackup_metal_color(self, material):
        return None  # no override - compute_stackup_layout()'s default type-based color

    def stackup_dielectric_label(self, dielectric, material):
        return default_stackup_dielectric_label(dielectric, material)

    def stackup_metal_label(self, metal, material, is_sheet):
        return default_stackup_metal_label(metal, material, is_sheet)

    def stackup_via_label_suffix(self, metal, material):
        return ""


def parse_python_ports_definitions (file_path):
    # parse the port assignment from Python code for Palace or openEMS workflow
    #
    # example input:
    # simulation_ports = simulation_setup.all_simulation_ports()
    # simulation_ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1, port_Z0=50, source_layernum=201, from_layername='Metal3', to_layername='TopMetal2', direction='z'))
    # simulation_ports.add_port(simulation_setup.simulation_port(portnumber=2, voltage=0, port_Z0=50, source_layernum=202, from_layername='Metal3', to_layername='TopMetal2', direction='z'))
    #
    # return value is a list of dictionaries, one dict for each port
    # [{'portnumber': 1, 'voltage': 1, 'port_Z0': 50, 'source_layernum': 201,
    # 'from_layername': 'Metal3', 'to_layername': 'TopMetal2', 'direction': 'z'},
    # {'portnumber': 2, 'voltage': 0, 'port_Z0': 50, 'source_layernum': 202,
    # 'from_layername': 'Metal3', 'to_layername': 'TopMetal2', 'direction': 'z'}, ... ]
    #
    # Simple module-level constants referenced inside simulation_port(...) (e.g.
    # port_Z0=2*Z0 where "Z0 = 50" is assigned earlier at module top level) are also
    # resolved - see collect_module_level_constants(). Anything more complex (function
    # calls, values from loops/conditionals, imports) still falls through to the
    # "skipped" path below.

    known_constants = collect_module_level_constants(file_path)

    # Function to parse the arguments inside simulation_port(...)
    def parse_port_args(arg_str):
        args = {}
        # Wrap the arguments into a fake function call so AST can parse it
        expr = ast.parse(f"f({arg_str})", mode='eval')
        for kw in expr.body.keywords:
            args[kw.arg] = eval_simple_python_expression(kw.value, known_constants)
        return args

    # List to store parsed ports
    ports = []
    skipped_count = 0

    # Read your input file line by line
    with open(file_path) as f:
        for line in f:
            # strip a trailing comment first (same convention as parse_assignments()
            # in setup_common.py) - otherwise a note like "# single-ended (target 80
            # ohm)" ends up inside the extracted argument text, and rstrip(") \n")
            # below stops at the first non-')'/space/newline character it hits from
            # the right (here, the 'm' in "ohm"), never reaching the real closing
            # parens right after the call's actual last argument
            line = line.split('#', 1)[0]
            if "simulation_port(" in line:
                start = line.index("simulation_port(") + len("simulation_port(")
                inside = line[start:].rstrip(") \n")  # remove trailing ')'
                try:
                    ports.append(parse_port_args(inside))
                except (SyntaxError, ValueError, TypeError, ZeroDivisionError):
                    # this is a best-effort static text parser, not a real
                    # interpreter - a port built from a variable or computed
                    # expression that eval_simple_python_expression() can't
                    # resolve (e.g. portnumber=portnumber inside a loop, as in
                    # some GDS/inductor synthesis scripts, or a function call)
                    # is skipped so the rest of the import still succeeds,
                    # rather than crashing the whole model load.
                    skipped_count += 1

    if skipped_count:
        QMessageBox.warning(
            None, "Import Model",
            f"{skipped_count} port definition(s) use variables, function calls, "
            "or computed expressions that could not be resolved automatically "
            "(only simple module-level constants and basic arithmetic like "
            "2*Z0 are supported) and could not be imported.\n\n"
            "Add them manually on the Ports tab.")

    return ports




# ---------- RUN APP ----------

def main():
    app = QApplication(sys.argv)

    if sys.platform.startswith("win"):
        app.setStyle(QStyleFactory.create("Windows"))

    # evaluate commandline
    parser = argparse.ArgumentParser()
    parser.add_argument("-gdsfile",  type=str, default = '', help="GDSII file to read")
    parser.add_argument("-xmlfile",  type=str, default = '', help="XML stackup file to read")
    parser.add_argument("-simcfg",   type=str, default = '', help="*.simcfg file that is loaded prior to reading files")
    # Optional argument --elmer to enable menu with solver choices
    parser.add_argument("--elmer",   action="store_true", help="Set Elmer as default simulator")
    args = parser.parse_args()

    # evaluate optional parameters
    gdsfile = args.gdsfile
    xmlfile = args.xmlfile
    simcfg  = args.simcfg
    elmer   = args.elmer

    win = MainWindow()
    win.show()

    if simcfg != '':
        # read configuration first, before reading the other files
        win.load_configuration_from_file(simcfg)

    if xmlfile !=  '':
        win.file_tab.set_XML_file(xmlfile)
        win.read_XML()

    if gdsfile !=  '':
        win.file_tab.set_gds_file(gdsfile)

    if elmer:
        # start in Elmer mode (instead of default choice Palace)
        win.setElmerMode()
    else:
        win.setPalaceMode()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
