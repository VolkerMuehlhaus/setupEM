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


import sys, json, os, pathlib, ast, webbrowser, argparse, math
import numpy as np
import importlib.metadata
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
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QFont, QSyntaxHighlighter, QPainter, QPen, QActionGroup, QLinearGradient
from PySide6.QtCore import Qt, QRegularExpression, QProcess, QRect, QStandardPaths


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
# __package__ is None/"" when this file is run directly (e.g. `python setupThermal.py`)
# rather than imported as part of the setupEM package, so relative import fails.
if __package__ in (None, ""):
    from setup_common import (
        EDIT_STYLE_OPTIONAL, EDIT_STYLE_REQUIRED, COMBO_STYLE_REQUIRED, COMBO_STYLE_OPTIONAL,
        FileDropLineEdit, FileInputTab, PythonHighlighter, CodeEditor,
        VectorWidget, PopUpWindow, CreateModelTabBase, MainWindowBase,
        next_available_source_layer, update_missing_layer_column,
        get_preference, get_preference_bool, set_preference, clear_preferences,
        find_paraview_exe,
    )
    from thermal_results import build_thermal_summary, format_source_table, find_thermal_paraview_file
else:
    from .setup_common import (
        EDIT_STYLE_OPTIONAL, EDIT_STYLE_REQUIRED, COMBO_STYLE_REQUIRED, COMBO_STYLE_OPTIONAL,
        FileDropLineEdit, FileInputTab, PythonHighlighter, CodeEditor,
        VectorWidget, PopUpWindow, CreateModelTabBase, MainWindowBase,
        next_available_source_layer, update_missing_layer_column,
        get_preference, get_preference_bool, set_preference, clear_preferences,
        find_paraview_exe,
    )
    from .thermal_results import build_thermal_summary, format_source_table, find_thermal_paraview_file


'''
Ubuntu 24.04 Notice:

Error message:
qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform plugin. qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.

Solution:
sudo apt update
sudo apt install libxcb-cursor0 libxcb-xinerama0 libxcb-xkb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xinput0 libxcb-xv0 libxcb-util1 libxkbcommon-x11-0

'''


CONFIG_SUFFIX = "tsimcfg"  # file suffix for native config file used here
APP_NAME = "setupThermal" # name of this application

DEFAULT_SETTINGS_FILE = os.path.join(os.path.expanduser("~"),"default." + CONFIG_SUFFIX)


saved_values = {} # dictionary of user input in this application
thermal_objects = simulation_setup.all_thermal_objects() # store thermal sources and thermal boundaries


def thermal_objects_to_struct (thermal_objects):
    # convert thermal object (source, const temp boundary) to a strcuture that can be serialized to JSON output
    all_thermal_objects_list = []
    for object in thermal_objects.objects:
        to = {}
        to['source_layernum'] = object.source_layernum
        to['target_layername'] = object.target_layername
        to['type'] = object.type
        if isinstance(object, simulation_setup.heatsource):
            to['power'] = object.power
        if isinstance(object, simulation_setup.constanttemp):
            to['temp'] = object.temp
        all_thermal_objects_list.append(to)
    return all_thermal_objects_list



# ---------- OTHER TABS ----------

class PortsTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow # parent = MainWindow
        self.main_layout = QVBoxLayout()


        self.top_group = QGroupBox("Thermal object settings")
        self.top_layout = QVBoxLayout()

        self.bottom_group = QGroupBox("Thermal object overview")
        self.bottom_layout = QVBoxLayout()


        # objects list is bottom group

        self.thermalobjectslist = QTableWidget()
        self.thermalobjectslist.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.thermalobjectslist.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.thermalobjectslist.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bottom_layout.addWidget(self.thermalobjectslist)

        self.thermalobjectslist.setColumnCount(6)
        self.thermalobjectslist.setHorizontalHeaderLabels(["GDSII layer", "Target layer", "Thermal object type", "Power (W)", "Const. Temp (K)", ""])
        header = self.thermalobjectslist.horizontalHeader()
        for col in range(self.thermalobjectslist.columnCount()-1):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

        self.thermalobjectslist.setRowCount(32)
        self.thermalobjectslist.selectRow(0)


        # details is top group
        self.details_layout = QVBoxLayout()

        left_label_width = 230

        self.sourcelayer_layout =  QHBoxLayout()
        label = QLabel("Geometry on layer number")
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

        self.targetlayer_layout =  QHBoxLayout()
        self.target_label = QLabel("Target layer for thermal object")
        self.targetlayer_layout.addWidget(self.target_label)
        self.target_label.setFixedWidth(left_label_width)
        self.target_box = QComboBox()
        self.target_box.setFixedWidth(150)
        self.target_box.setStyleSheet(COMBO_STYLE_REQUIRED)
        self.target_box.addItems(["XML stackup missing"])
        self.targetlayer_layout.addWidget(self.target_box)

        self.targetlayer_layout.addStretch()
        self.details_layout.addLayout(self.targetlayer_layout)


        self.thermaltype =  QHBoxLayout()
        label = QLabel("Thermal source or const temp?")
        self.thermaltype.addWidget(label)
        label.setFixedWidth(left_label_width)
        self.thermaltype_box = QComboBox()
        self.thermaltype_box.setFixedWidth(150)
        self.thermaltype_box.setStyleSheet(COMBO_STYLE_REQUIRED)
        self.thermaltype_box.addItems(["source", "constanttemp"])
        self.thermaltype.addWidget(self.thermaltype_box)
        label = QLabel(" (Models need both!)")
        self.thermaltype.addWidget(label)
        self.thermaltype.addStretch()
        self.details_layout.addLayout(self.thermaltype)


        self.power_layout =  QHBoxLayout()
        label = QLabel("Thermal power source (W)")
        self.power_layout.addWidget(label)
        label.setFixedWidth(left_label_width)
        self.power_edit = QLineEdit("0.01")
        self.power_edit.setFixedWidth(80)
        self.power_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.power_layout.addWidget(self.power_edit)
        self.power_layout.addStretch()
        self.details_layout.addLayout(self.power_layout)

        self.temp_layout =  QHBoxLayout()
        label = QLabel("Constant temp. boundary (K)")
        self.temp_layout.addWidget(label)
        label.setFixedWidth(left_label_width)
        self.temp_edit = QLineEdit("300")
        self.temp_edit.setFixedWidth(80)
        self.temp_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.temp_layout.addWidget(self.temp_edit)
        self.temp_layout.addStretch()
        self.details_layout.addLayout(self.temp_layout)

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

        # utility
        def set_layout_enabled(layout, enabled):
            for i in range(layout.count()):
                item = layout.itemAt(i)

                if item.widget():
                    item.widget().setEnabled(enabled)
                elif item.layout():
                    set_layout_enabled(item.layout(), enabled)


        # callback when direction changed, so that we can show/hide layer choices
        def on_thermaltype_changed(text):
            is_source = "SOURCE" in text.upper()
            set_layout_enabled(self.power_layout, is_source)
            set_layout_enabled(self.temp_layout, not is_source)


        self.thermaltype_box.currentTextChanged.connect(on_thermaltype_changed)
        self.thermaltype_box.setCurrentIndex(1)
        self.thermaltype_box.setCurrentIndex(0)

        # set this AFTER apply_port_values_to_table(), not earlier!
        self.thermalobjectslist.itemSelectionChanged.connect(self.portslist_selection_changed)


    # callback when applying changes to the selected port
    def apply_port_values_to_table(self):
        selected_indexes = self.thermalobjectslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell
            # "GDSII layer", "Target layer", "Thermal object type", "Power (W)", "Const. Temp (K)"

            if "SOURCE" in self.thermaltype_box.currentText().upper():
                thermaltype = "source"
                power = self.power_edit.text()
                temp  = ""
            else:
                thermaltype = "constanttemp"
                power = ""
                temp  = self.temp_edit.text()

            data = [self.sourcelayer_edit.text(),
                    self.target_box.currentText(),
                    thermaltype,
                    power,
                    temp]

            for col, value in enumerate(data):
                self.thermalobjectslist.setItem(selected_row, col, QTableWidgetItem(str(value)))

            self.refresh_missing_layer_annotations()


    # callback when applying changes to the selected port
    def get_port_values_from_table(self):
        selected_indexes = self.thermalobjectslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell
             # "GDSII layer", "Target layer", "Thermal object type", "Power (W)", "Const. Temp (K)"

            def safe_get_for_lineedit (index, target, default):
                item =  self.thermalobjectslist.item(selected_row, index)
                if item is None:
                    itemvalue = default
                else:
                    itemvalue = item.text()
                target.setText(itemvalue)

            def safe_get_for_combobox (index, target, default):
                item =  self.thermalobjectslist.item(selected_row, index)
                if item is None:
                    index = default
                else:
                    index = target.findText(item.text())
                    if not index >= 0:
                        index = default
                target.setCurrentIndex(index)

            safe_get_for_lineedit(0,self.sourcelayer_edit,"")
            safe_get_for_combobox(1,self.target_box,0)
            safe_get_for_combobox(2,self.thermaltype_box,2)
            safe_get_for_lineedit(3,self.power_edit,"50")
            safe_get_for_lineedit(4,self.temp_edit,"1")



    # callback when removing selected port settings
    def remove_port_values_from_table(self):
        selected_indexes = self.thermalobjectslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell

            data = ["","","","","",""]

            for col, value in enumerate(data):
                # self.portslist.setItem(selected_row, col, QTableWidgetItem(str(value)))
                self.thermalobjectslist.setItem(selected_row, col, None)


    def portslist_selection_changed(self):
        selected_indexes = self.thermalobjectslist.selectedIndexes()
        if selected_indexes:
            selected_row = selected_indexes[0].row()  # row of the first selected cell
            item =  self.thermalobjectslist.item(selected_row, 0) # first item is Z0
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
        """Layer numbers (column 0) already entered in the table, across all
        rows - used to avoid suggesting a layer another thermal object uses.
        """
        used = set()
        for row in range(self.thermalobjectslist.rowCount()):
            item = self.thermalobjectslist.item(row, 0)
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
        geometry and isn't already a stackup layer or another thermal
        object's source layer, or None if no such layer can be determined
        (e.g. no GDS file loaded yet, or every present layer is already
        spoken for).
        """
        layer_min, layer_max = self._port_layer_range()
        gds_layers = self.MainWindow.get_gds_layers_in_range(layer_min, layer_max)
        xml_layers = set(self.MainWindow.metals_list.getlayernumbers()) if self.MainWindow.metals_list else set()
        excluded = xml_layers | self._used_source_layers()
        return next_available_source_layer(gds_layers, excluded, start=layer_min)

    def refresh_missing_layer_annotations(self):
        """Flag any thermal object row whose source layer has no geometry in
        the currently loaded GDS - see update_missing_layer_column()."""
        layer_min, layer_max = self._port_layer_range()
        gds_layers = self.MainWindow.get_gds_layers_in_range(layer_min, layer_max)
        update_missing_layer_column(self.thermalobjectslist, source_col=0, comment_col=5, gds_layers_present=gds_layers)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_missing_layer_annotations()


    def save_values(self):

        # clear previous thermal objects
        thermal_objects.objects.clear()
        # loop over rows in ports table
        for row in range(self.thermalobjectslist.rowCount()):
            testvalue = self.thermalobjectslist.item(row, 0)
            if testvalue != None: # not an empty port line
                if testvalue.text() != "":
                    try:
                        # "GDSII layer", "Target layer", "Thermal object type", "Power (W)", "Const. Temp (K)"
                        source_layer_num = int(self.thermalobjectslist.item(row, 0).text())
                        target_name = self.thermalobjectslist.item(row, 1).text()
                        thermaltype = self.thermalobjectslist.item(row, 2).text()
                        power = self.thermalobjectslist.item(row, 3).text()
                        temp  = self.thermalobjectslist.item(row, 4).text()
                        if "SOURCE" in thermaltype.upper():
                            power = float(power)
                        else:
                            temp = float(temp)
                    except Exception:
                        QMessageBox.warning(self, "Error", "Invalid input in row " + str(row+1))
                        return False
                    # create port
                    if "SOURCE" in thermaltype.upper():
                        thermal_objects.add_heatsource(simulation_setup.heatsource(
                                                                                power=power,
                                                                                source_layernum=source_layer_num,
                                                                                target_layername=target_name))
                    else:
                        thermal_objects.add_consttemp(simulation_setup.constanttemp(
                                                                                temp=temp,
                                                                                source_layernum=source_layer_num,
                                                                                target_layername=target_name))

        return True

    def load_values(self):
        ...
        # self.log.setText(data.get("log", ""))

    def update_layers(self, metals_list):
        self.target_box.clear()
        for metal in metals_list.metals:
            self.target_box.addItems([metal.name])
        # try to preset useful values for SG13G2 technology
        index = self.target_box.findText('Activ')  # returns -1 if not found
        if index != -1:
            self.target_box.setCurrentIndex(index)
        self.refresh_missing_layer_annotations()
        # re-evaluate the currently selected row's source-layer suggestion too -
        # a GDS/XML (re)load is exactly when a stale/unset suggestion (e.g. an
        # unapplied new row selected before any file was loaded) needs it most
        self.portslist_selection_changed()


    def update_thermalobjects_from_python (self, heatsource_defs, consttemp_defs ):
        # update thermal definitions from Python model code

        self.thermalobjectslist.clearContents()
        n=-1
        for n, heatsource_def in enumerate(heatsource_defs):
            # each definition is a dictionary
            # input example: power=0.05,source_layernum=201, target_layername='Activ'
            # table items: "GDSII layer", "Target layer", "Thermal object type", "Power (W)", "Const. Temp (K)"
                self.thermalobjectslist.setItem(n, 0, QTableWidgetItem(str(heatsource_def.get("source_layernum",""))))
                self.thermalobjectslist.setItem(n, 1, QTableWidgetItem(str(heatsource_def.get("target_layername",""))))
                self.thermalobjectslist.setItem(n, 2, QTableWidgetItem("source"))
                self.thermalobjectslist.setItem(n, 3, QTableWidgetItem(str(heatsource_def.get("power","0"))))
                self.thermalobjectslist.setItem(n, 4, QTableWidgetItem(""))
        n=n+1

        for m, heatsource_def in enumerate(consttemp_defs):
            # each definition is a dictionary
            # input example: temp=300,source_layernum=202, target_layername='ABOVE_PASSIVATION'
            # table items: "GDSII layer", "Target layer", "Thermal object type", "Power (W)", "Const. Temp (K)"
                self.thermalobjectslist.setItem(m+n, 0, QTableWidgetItem(str(heatsource_def.get("source_layernum",""))))
                self.thermalobjectslist.setItem(m+n, 1, QTableWidgetItem(str(heatsource_def.get("target_layername",""))))
                self.thermalobjectslist.setItem(m+n, 2, QTableWidgetItem("constanttemp"))
                self.thermalobjectslist.setItem(m+n, 3, QTableWidgetItem(""))
                self.thermalobjectslist.setItem(m+n, 4, QTableWidgetItem(str(heatsource_def.get("temp","0"))))

        self.thermalobjectslist.selectRow(1)
        self.thermalobjectslist.selectRow(0)


    def update_thermalobjects_from_JSON (self, thermal_defs ):
        # update thermal definitions from our native JSON format

        self.thermalobjectslist.clearContents()
        n=-1
        for n, thermal_def in enumerate(thermal_defs):
            # each definition is a dictionary
            # input example: power=0.05,source_layernum=201, target_layername='Activ'
            # table items: "GDSII layer", "Target layer", "Thermal object type", "Power (W)", "Const. Temp (K)"
                self.thermalobjectslist.setItem(n, 0, QTableWidgetItem(str(thermal_def.get("source_layernum",""))))
                self.thermalobjectslist.setItem(n, 1, QTableWidgetItem(str(thermal_def.get("target_layername",""))))
                thermaltype = thermal_def.get("type","")
                if "SOURCE" in thermaltype.upper():
                    self.thermalobjectslist.setItem(n, 3, QTableWidgetItem(str(thermal_def.get("power","0"))))
                    self.thermalobjectslist.setItem(n, 4, QTableWidgetItem(""))
                else:
                    self.thermalobjectslist.setItem(n, 3, QTableWidgetItem(""))
                    self.thermalobjectslist.setItem(n, 4, QTableWidgetItem(str(thermal_def.get("temp","0"))))
                self.thermalobjectslist.setItem(n, 2, QTableWidgetItem(thermaltype))


        self.thermalobjectslist.selectRow(1)
        self.thermalobjectslist.selectRow(0)



class MeshTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow # parent = MainWindow

        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignTop)

        label_width = 250
        edit_width = 150


        # ---------- MESH GROUP ----------
        self.mesh_group = QGroupBox("Mesh settings")
        self.mesh_layout = QVBoxLayout()

        self.refinement_layout = QHBoxLayout()
        self.label2 = QLabel("Mesh refinement at metal edges")
        self.label2.setFixedWidth(label_width)
        self.refinement_layout.addWidget(self.label2)
        self.refinement_edit = QLineEdit("5")
        self.refinement_edit.setFixedWidth(edit_width)
        self.refinement_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.refinement_layout.addWidget(self.refinement_edit)
        self.label3 = QLabel(" µm ")
        self.refinement_layout.addWidget(self.label3)
        self.refinement_layout.addStretch()
        self.mesh_layout.addLayout(self.refinement_layout)

        self.cells_maxsize_layout = QHBoxLayout()
        self.label6 = QLabel("Mesh cell maximum size")
        self.label6.setFixedWidth(label_width)
        self.cells_maxsize_layout.addWidget(self.label6)
        self.cells_maxsize_edit = QLineEdit("100")
        self.cells_maxsize_edit.setFixedWidth(edit_width)
        self.cells_maxsize_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.cells_maxsize_layout.addWidget(self.cells_maxsize_edit)
        self.label5 = QLabel(" µm ")
        self.cells_maxsize_layout.addWidget(self.label5)
        self.cells_maxsize_layout.addStretch()
        self.mesh_layout.addLayout(self.cells_maxsize_layout)

        self.mesh_group.setLayout(self.mesh_layout)
        self.main_layout.addWidget(self.mesh_group)


        # ---------- ELMER SOLVER GROUP ----------
        self.Elmer_group = QGroupBox("Elmer solver settings")
        self.Elmer_layout = QVBoxLayout()

        self.solver_layout = QHBoxLayout()
        self.solverlabel = QLabel("Solver")
        self.solverlabel.setFixedWidth(label_width)
        self.solver_layout.addWidget(self.solverlabel)

        self.solver_box = QComboBox()
        self.solver_box.setFixedWidth(edit_width)
        self.solver_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.solver_box.addItems(["iterative","direct"])
        self.solver_layout.addWidget(self.solver_box)
        self.solver_box.setCurrentIndex(1)
        self.solver_layout.addStretch()
        self.Elmer_layout.addLayout(self.solver_layout)

        self.Elmer_group.setLayout(self.Elmer_layout)
        self.main_layout.addWidget(self.Elmer_group)


        # ---------- BOUNDARY GROUP ----------
        self.mesh_group = QGroupBox("Boundary settings")
        self.mesh_layout = QVBoxLayout()

        self.margins_layout = QHBoxLayout()
        self.label7 = QLabel("Dielectric stackup: oversize by")
        self.label7.setFixedWidth(label_width)
        self.margins_layout.addWidget(self.label7)
        self.margins_edit = QLineEdit("100")
        self.margins_edit.setFixedWidth(edit_width)
        self.margins_edit.setStyleSheet(EDIT_STYLE_REQUIRED)
        self.margins_layout.addWidget(self.margins_edit)
        self.label8 = QLabel(" µm from metal drawing")
        self.margins_layout.addWidget(self.label8)
        self.margins_layout.addStretch()
        self.mesh_layout.addLayout(self.margins_layout)

        self.mesh_group.setLayout(self.mesh_layout)
        self.main_layout.addWidget(self.mesh_group)


        self.setLayout(self.main_layout)


    def save_values(self):
        try:
            value = float(self.refinement_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for mesh refinement")
            self.refinement_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "refined_cellsize", "5")))
            return False
        saved_values ["refined_cellsize"] = float(value)

        try:
            value = float(self.cells_maxsize_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for max. meshsize")
            self.cells_maxsize_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "meshsize_max", "100")))
            return False
        saved_values ["meshsize_max"] = float(value)


        try:
            value = float(self.margins_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for dielectric oversize margin")
            self.margins_edit.setText(str(get_preference(self.MainWindow.APP_NAME, "margin", "100")))
            return False
        saved_values ["margin"] = float(value)

        # iterative or direct solver for Elmer
        saved_values["iterative"] = "iterative" in self.solver_box.currentText()

        # all saved
        return True


    def load_values(self):
        app_name = self.MainWindow.APP_NAME
        self.refinement_edit.setText(str(saved_values.get("refined_cellsize", get_preference(app_name, "refined_cellsize", "5"))))
        self.cells_maxsize_edit.setText(str(saved_values.get("meshsize_max", get_preference(app_name, "meshsize_max", "100"))))
        self.margins_edit.setText(str(saved_values.get("margin", get_preference(app_name, "margin", "100"))))

        if saved_values.get("iterative", False):
            self.solver_box.setCurrentIndex(0)
        else:
            self.solver_box.setCurrentIndex(1)



class CreateModelTab(CreateModelTabBase):
    """Elmer thermal specific model-building and run behavior.

    UI construction, log panel, preview/create-mesh, and the QProcess
    plumbing all live in CreateModelTabBase (setup_common.py). Only the
    "is the model complete?" check and how ElmerSolver is actually launched
    are specific to this app.
    """

    def __init__(self, MainWindow):
        super().__init__(MainWindow)

        # Tracks which action self.process is currently running, so on_finished() can tell a
        # simulation run apart from a mesh-creation run (self.process is reused for both).
        self._process_purpose = None

        # "View fields ..." opens the thermal result in whichever viewer
        # Preferences > Create Model > "3D field viewer" selects - built-in
        # (embedded PyVista) or external ParaView, see open_viewer(). Always
        # visible (Elmer thermal steady-state solves always produce a
        # temperature field, unlike Palace's optional per-frequency fdump).
        # Added as its own row of the base class's buttons_grid so it lines up
        # with Preview/Create Mesh/Start Simulation above, in the same Actions
        # group.
        row = self.buttons_grid.rowCount()
        self.viewer_btn = QPushButton()
        self.viewer_btn.clicked.connect(self.open_viewer)
        self.buttons_grid.addWidget(self.viewer_btn, row, 0)
        self._update_viewer_button_label()

    def _append_thermal_results_summary(self):
        # Parse thermal_results.dat / thermal_results.vtu and append a results summary
        # to the log. Called from on_finished() after a real simulation run.
        run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"

        # Heat sources first, then constant-temperature boundaries, regardless of the
        # order they were added in the GUI.
        rows = []
        for thermal_object in thermal_objects.objects:
            if isinstance(thermal_object, simulation_setup.heatsource):
                rows.append(["Heat source", str(thermal_object.source_layernum),
                             str(thermal_object.target_layername), f"{thermal_object.power} W"])
        for thermal_object in thermal_objects.objects:
            if isinstance(thermal_object, simulation_setup.constanttemp):
                rows.append(["Const. temp", str(thermal_object.source_layernum),
                             str(thermal_object.target_layername), f"{thermal_object.temp} K"])
        source_lines = format_source_table(rows) if rows else None

        summary = build_thermal_summary(run_path, source_lines=source_lines)
        self.log_area.appendPlainText("\n" + summary + "\n")

    def on_finished(self, exit_code, exit_status):
        super().on_finished(exit_code, exit_status)
        # Auto-append the results summary after a real simulation run (not after mesh creation)
        if self._process_purpose == "run_simulation":
            self._append_thermal_results_summary()

    def _viewer_preference(self):
        return get_preference(self.MainWindow.APP_NAME, "viewer_3d", "builtin")

    def _update_viewer_button_label(self):
        viewer_label = "ParaView" if self._viewer_preference() == "paraview" else "Built-in"
        self.viewer_btn.setText(f"🖼️ View fields ({viewer_label})...")

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
                "PATH, or switch back to \"Built-in\" under Preferences > Create "
                "Model > 3D field viewer to avoid this message.\n"
            )
        self.open_field_viewer()

    def launch_paraview(self):
        run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"
        vtu_path = find_thermal_paraview_file(run_path)
        self._open_in_paraview(
            [vtu_path] if vtu_path else [],
            f"⚠️ No thermal results .vtu found yet under {run_path}\n"
        )

    def open_field_viewer(self):
        """Open the in-app PyVista 3D field viewer on Elmer's thermal .vtu result."""
        if __package__ in (None, ""):
            from field_viewer import FieldViewerWindow
        else:
            from .field_viewer import FieldViewerWindow

        if self.MainWindow.field_viewer_window is not None:
            self.MainWindow.field_viewer_window.raise_()
            self.MainWindow.field_viewer_window.activateWindow()
            return

        run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"
        vtu_path = find_thermal_paraview_file(run_path)
        if not vtu_path:
            self.log_area.appendPlainText(f"⚠️ No thermal results .vtu found yet under {run_path}\n")
            return

        self.MainWindow.field_viewer_window = FieldViewerWindow(self.MainWindow, [vtu_path], "elmer_thermal")
        self.MainWindow.field_viewer_window.destroyed.connect(
            lambda: setattr(self.MainWindow, "field_viewer_window", None))
        self.MainWindow.field_viewer_window.show()

    def create_model(self):
        # Request all tabs to save values again,
        # which can do some update to saved_values
        self.MainWindow.save_all_tabs()

        # check if we have at leat one thermal source and one constant temp boundary
        thermal_source_exists = False
        thermal_boundary_exists = False
        for thermal_object in thermal_objects.objects:
            if isinstance(thermal_object, simulation_setup.heatsource):
                thermal_source_exists = True
            elif isinstance(thermal_object, simulation_setup.constanttemp):
                thermal_boundary_exists = True

        thermal_fully_defined = thermal_boundary_exists and thermal_source_exists

        if thermal_fully_defined or saved_values['preview_only']:
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

            # Refuse to overwrite an imported openEMS model script - setupThermal can
            # only generate Palace/Elmer code and has no way to regenerate an openEMS
            # model. Normally load_configuration_from_file() already steers the output
            # elsewhere for such an import (see protected_source_model_path), so this
            # is a second-layer guard for the case where the user manually re-picks
            # the same name/directory on the Create Model(s) tab afterwards.
            protected_path = getattr(self.MainWindow, 'protected_source_model_path', None)
            if protected_path and os.path.normcase(pymodel_filename) == os.path.normcase(protected_path):
                QMessageBox.warning(
                    self, "Create Model",
                    "This would overwrite the imported openEMS model script:\n\n"
                    f"{pymodel_filename}\n\n"
                    "setupThermal cannot regenerate an openEMS model, so this write was "
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
            # self.process is shared with run_model(), which sets a working directory
            # of its own (a model's *_data folder) and never clears it afterwards -
            # it's sticky on the QProcess instance. If that leftover directory no
            # longer exists, Windows' CreateProcess refuses to launch ANY process,
            # even python_exe given by absolute path here, and QProcess reports it
            # as the generic FailedToStart. Reset it so this launch never depends on
            # what a previous, unrelated action last pointed it at.
            self.process.setWorkingDirectory("")
            self.process.start(python_exe, [pymodel_filename])


        else:
            if not thermal_source_exists:
                QMessageBox.warning(self, "Error", "Model incomplete, no thermal source in model!")
            elif not thermal_boundary_exists:
                QMessageBox.warning(self, "Error", "Model incomplete, no constant temperature boundary in model!")



    def run_model(self):
        # Run model that we created before

        # try to start from output directory
        run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"

        # ---------- pre-flight checks: fail fast, before touching QProcess ----------
        # ELMERSOLVER_STARTINFO (written by "Create Mesh", alongside case.sif) is
        # the exact file a bare "ElmerSolver" invocation reads from its working
        # directory - a more precise proxy than just checking run_path exists,
        # which could be a stale/empty leftover directory.
        startinfo_path = os.path.join(run_path, "ELMERSOLVER_STARTINFO")
        if not os.path.isfile(startinfo_path):
            self.log_area.appendPlainText(
                f"⚠️ No simulation settings found in {run_path}.\n"
                "Click 'Create mesh and simulation settings file' first.\n"
            )
            return
        if self._check_command_on_path(
            "ElmerSolver", "Install Elmer FEM and make sure ElmerSolver is on PATH."
        ) is None:
            return

        try:
            # clear log
            self.log_area.clear()
            self._process_purpose = "run_simulation"

            # Windows and Linux/Mac both just resolve ElmerSolver via PATH
            self.log_area.appendPlainText('Setting work directory ' + run_path)
            self.process.setWorkingDirectory(run_path)
            # start simulation
            self.process.start("ElmerSolver")
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
        add_text("# Thermal model for IHP OpenPDK workflow created using " + APP_NAME)
        add_text("import os, sys, subprocess")

        add_text("\nfrom gds2palace import *")

        add_text("\n# get path for this simulation file")
        add_text("script_path = utilities.get_script_path(__file__)")
        add_text("# use script filename as model basename")
        add_text("model_basename = utilities.get_basename(__file__)")
        add_text("# set and create directory for simulation output")

        add_text("sim_path = utilities.create_sim_path (script_path,model_basename,dirname='elmer_model')")

        add_text("\n# ========================= workflow settings ==========================")
        if forExport:
            add_text("# preview model/mesh only, without running solver?")
            add_text("start_simulation = False")
            add_text("\n# Command to start simulation")

            add_text("run_command = ['ElmerSolver']")

        add_text("\n# ===================== input files and settings =======================")
        add_text("settings={}")


        # List of keys that must be included in Python code AFTER reading stackups and GDSII, not before
        special_keylist = ['thermal_objects','materials_list','dielectrics_list','metals_list',
                           'layernumbers','allpolygons']
        # List of keys that we don't write to Python model code editor
        ignore_list     = ['model_basename','sim_path']


        if forExport:
            # these commands are only used within this GUI application to control gmsh
            ignore_list.extend(['preview_only','no_preview'])

        for key in saved_values.keys():
            if not key in special_keylist:
                if not key in ignore_list:
                    add_key(key)

        # tag for Elmer thermal modcel
        add_text("settings['elmer_thermal'] = True # create all metals as volumes, enable Elmer thermal output")



        add_text("\n# ===================== port definitions =======================")
        add_text("thermal_objects = simulation_setup.all_thermal_objects()")

        for thermalobject in thermal_objects.objects:
            if isinstance(thermalobject, simulation_setup.heatsource):
                add_text(f"thermal_objects.add_heatsource(simulation_setup.heatsource("
                         f"power={str(thermalobject.power)}, "
                         f"source_layernum={str(thermalobject.source_layernum)}, "
                         f"target_layername='{thermalobject.target_layername}'))")
            if isinstance(thermalobject, simulation_setup.constanttemp):
                add_text(f"thermal_objects.add_consttemp(simulation_setup.constanttemp("
                         f"temp={str(thermalobject.temp)}, "
                         f"source_layernum={str(thermalobject.source_layernum)}, "
                         f"target_layername='{thermalobject.target_layername}'))")



        add_text("\n# ================= read stackup and geometries =================")
        add_text("materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate (settings['SubstrateFile'], variable_overrides=settings['variable_overrides'])")
        add_text("layernumbers = metals_list.getlayernumbers()")
        add_text("layernumbers.extend(thermal_objects.layers)")
        add_text("\n# read geometries from GDSII")


        add_text("allpolygons = gds_reader.read_gds(settings['GdsFile'], "
                "\n\tlayernumbers,"
                "\n\tcellname=settings['cellname'], "
                "\n\tpurposelist=settings['purpose'], "
                "\n\tmetals_list=metals_list, \n\tpreprocess=settings['preprocess_gds'], "
                "\n\tmerge_polygon_size=settings['merge_polygon_size'],"
                "\n\tgds_boundary_layers=dielectrics_list.get_boundary_layers())")
        add_text("\n")


        # Now do the special keys that we skipped before
        for key in special_keylist:
            add_text("settings['" + key + "'] = " + key)
        add_text("settings['sim_path'] = sim_path")
        add_text("settings['model_basename'] = model_basename")

        add_text("\n")
        add_text("config_name, data_dir = simulation_setup.create_elmer_thermal (settings)")


        # When running the model from setupEM GUI, we start the script differently, only write this for export
        if forExport:
            add_text("\n# run after creating mesh and Elmer model files ")
            add_text("if start_simulation:")
            add_text("  try:")
            add_text("      os.chdir(sim_path)")
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
    hardcoded literals. Persisted via get_preference()/set_preference()
    (setup_common.py) - a dedicated QSettings store, separate from *.tsimcfg
    project files and from "Save as Default Config". Editing a value here only
    changes what a brand-new/blank field starts out showing; it never touches
    the currently open project's saved_values. Smaller than setupEM's version
    of this dialog - no Frequencies tab (thermal has no frequency sweep), and
    its own Viewer tab only has the 3D field viewer preference (no Model Fit
    button or Palace solver status line here, unlike setupEM's fuller version
    of that tab).
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

        def add_row(form_layout, label_text, key, default):
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(label_width)
            row.addWidget(label)
            edit = QLineEdit(str(get_preference(self.app_name, key, default)))
            edit.setStyleSheet(EDIT_STYLE_REQUIRED)
            row.addWidget(edit)
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

        # ---------- Mesh tab ----------
        mesh_widget = QWidget()
        mesh_form = QVBoxLayout(mesh_widget)
        mesh_form.setAlignment(Qt.AlignTop)
        self.refined_cellsize_edit = add_row(mesh_form, "Mesh refinement at metal edges (µm)", "refined_cellsize", "5")
        self.meshsize_max_edit = add_row(mesh_form, "Mesh cell maximum size (µm)", "meshsize_max", "100")
        self.margin_edit = add_row(mesh_form, "Dielectric stackup oversize margin (µm)", "margin", "100")
        mesh_form.addStretch()
        self.tabs.addTab(mesh_widget, "Mesh")

        # ---------- Viewer tab ----------
        # Just the one preference for now (unlike setupEM.py's fuller version of
        # this tab, which also has Model Fit / solver status line toggles that
        # don't apply here) - which viewer "View fields ..." opens.
        create_widget = QWidget()
        create_form = QVBoxLayout(create_widget)
        create_form.setAlignment(Qt.AlignTop)
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
        self.MainWindow.create_model_tab._update_viewer_button_label()

    def accept(self):
        # via-merge distance and the two mesh sizes must parse as numbers; purpose is
        # stored as free text, same tolerant convention the Files tab itself uses
        try:
            float(self.viamerge_edit.text())
            float(self.refined_cellsize_edit.text())
            float(self.meshsize_max_edit.text())
            float(self.margin_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid numeric value")
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

        set_preference(self.app_name, "purpose", self.purpose_edit.text())
        set_preference(self.app_name, "merge_polygon_size", self.viamerge_edit.text())
        set_preference(self.app_name, "confirm_reuse_import_filename", self.confirm_reuse_checkbox.isChecked())
        set_preference(self.app_name, "xml_browse_directory", self.xml_browse_dir_edit.text())
        set_preference(self.app_name, "port_layer_min", self.port_layer_min_edit.text())
        set_preference(self.app_name, "port_layer_max", self.port_layer_max_edit.text())
        set_preference(self.app_name, "refined_cellsize", self.refined_cellsize_edit.text())
        set_preference(self.app_name, "meshsize_max", self.meshsize_max_edit.text())
        set_preference(self.app_name, "margin", self.margin_edit.text())
        set_preference(self.app_name, "simplify_max_hole_area", self.simplify_max_hole_area_edit.text())
        set_preference(self.app_name, "simplify_fill_maxsize", self.simplify_fill_maxsize_edit.text())
        set_preference(self.app_name, "simplify_excluded_layers", self.simplify_excluded_layers_edit.text())
        set_preference(self.app_name, "simplify_merge_per_layer", self.simplify_merge_per_layer_checkbox.isChecked())
        set_preference(self.app_name, "viewer_3d", self.viewer_3d_combo.currentData())

        # live update, no restart needed
        self.MainWindow.create_model_tab._update_viewer_button_label()

        super().accept()


# ---------- Thermal conductivity color scale (stackup preview) ----------
#
# Log10 scale clipped to [THERMAL_COND_MIN, THERMAL_COND_MAX] W/(m*K) - covers
# typical metals/vias/silicon (~50-400); materials below THERMAL_COND_MIN
# (air ~0.026, many dielectrics ~0.3-0.5) clip to the same pale color as
# THERMAL_COND_MIN itself, since nothing below ~1 W/(m*K) needed to be told
# apart in practice. Warm-family hue sweep (yellow -> orange -> red) plus a
# saturation ramp, rather than a cold-to-hot two-color gradient - value is
# kept at maximum and saturation moderate throughout so the black text
# labels drawn on top of these shapes (metal/dielectric name, kappa value)
# stay legible everywhere on the scale, not just at the pale end.
#
# A plain linear map of log10(k) to hue/saturation left 50-400 W/(m*K) (most
# real metals/vias/silicon) looking nearly identical: that whole decade-and-a-
# bit only covers about the top third of the full log range, so a linear ramp
# spends most of its visual range on values that don't need distinguishing.
# _WEIGHT bends the normalized position with a power curve (t**_WEIGHT)
# before mapping to hue/saturation, shifting more of the visible change into
# the upper end of the range at the cost of compressing the low end further.

THERMAL_COND_MIN = 1.0
THERMAL_COND_MAX = 400.0
_THERMAL_HUE_LOW = 45 / 360.0    # yellow-orange - mild/insulating
_THERMAL_HUE_HIGH = 0 / 360.0    # red - highly conductive
_WEIGHT = 2.0  # >1 biases the color change toward the high end of the range

DIELECTRIC_ALPHA = 95   # dielectric slabs stay fairly translucent
METAL_ALPHA = 210       # metals/vias render more solid/opaque than dielectrics

def thermal_conductivity_to_color(thermalcond, alpha=DIELECTRIC_ALPHA):
    """QColor for a material's thermalcond (W/(m*K)) on the log10 scale above.
    thermalcond <= 0 (util_stackup_reader.py's default when a material's XML
    has no ThermalConductivity= attribute - never None, but not a real
    measurement either) gets a distinct flat white instead of being silently
    clamped into the scale as if it were an extreme insulator.
    """
    if thermalcond is None or thermalcond <= 0:
        return QColor(255, 255, 255, alpha)
    k = min(max(thermalcond, THERMAL_COND_MIN), THERMAL_COND_MAX)
    t = ((math.log10(k) - math.log10(THERMAL_COND_MIN))
         / (math.log10(THERMAL_COND_MAX) - math.log10(THERMAL_COND_MIN)))
    t_weighted = t ** _WEIGHT
    hue = _THERMAL_HUE_LOW + (_THERMAL_HUE_HIGH - _THERMAL_HUE_LOW) * t_weighted
    saturation = 0.12 + 0.55 * t_weighted
    color = QColor.fromHsvF(hue, saturation, 1.0)
    color.setAlpha(alpha)
    return color


# room temperature reference point for coloring a table-based material (its
# thermalcond is temperature-dependent, not one fixed number) - distinct from
# THERMAL_COND_MAX above despite the same numeral: this is a temperature in
# Kelvin, that one a conductivity ceiling in W/(m*K).
THERMAL_TABLE_REFERENCE_TEMPERATURE_K = 300.0

def _thermal_conductivity_for_color(material):
    """The effective thermalcond (W/(m*K)) to color `material` by: its own
    scalar value, or - for a temperature-dependent table
    (thermaltablename set) - the table's value at/near room temperature,
    linearly interpolated and clamped to the table's own range (never
    extrapolated beyond the measured points)."""
    if material is None:
        return None
    if material.thermaltablename and material.thermaltable is not None and material.thermaltable.points:
        points = sorted(material.thermaltable.points)
        if len(points) == 1:
            return points[0][1]
        temps = [p[0] for p in points]
        values = [p[1] for p in points]
        interp = interp1d(temps, values, bounds_error=False,
                           fill_value=(values[0], values[-1]))
        return float(interp(THERMAL_TABLE_REFERENCE_TEMPERATURE_K))
    return material.thermalcond


class ThermalConductivityLegend(QWidget):
    """Compact colorbar + tick labels for the stackup preview's conductivity
    color scale, shown next to it (setupThermal only - see
    MainWindow.stackup_color_legend())."""

    TICK_VALUES = [1, 10, 100, 400]

    # explicit row heights, stacked top to bottom with no overlap - the title
    # previously shared the same vertical band as the tick labels (both
    # centered on the widget), so a tick label near the horizontal middle
    # (e.g. "10" or "100") visually collided with the title text
    _TOP_MARGIN = 4
    _BAR_HEIGHT = 16
    _TICK_MARK_HEIGHT = 4
    _TICK_LABEL_HEIGHT = 14
    _ROW_GAP = 2
    _TITLE_HEIGHT = 14

    def __init__(self):
        super().__init__()
        self.setFixedHeight(
            self._TOP_MARGIN + self._BAR_HEIGHT + self._TICK_MARK_HEIGHT
            + self._TICK_LABEL_HEIGHT + self._ROW_GAP + self._TITLE_HEIGHT + self._TOP_MARGIN)
        self.setMinimumWidth(220)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bar_top = self._TOP_MARGIN
        bar_bottom = bar_top + self._BAR_HEIGHT
        tick_mark_bottom = bar_bottom + self._TICK_MARK_HEIGHT
        tick_label_top = tick_mark_bottom
        title_top = tick_label_top + self._TICK_LABEL_HEIGHT + self._ROW_GAP

        # horizontal inset wide enough that the end ticks' centered labels
        # ("1", "400") stay fully inside the widget instead of overhanging
        # past its left/right edges
        bar_rect = self.rect().adjusted(18, 0, -18, 0)
        bar_rect.setTop(bar_top)
        bar_rect.setBottom(bar_bottom)
        gradient = QLinearGradient(bar_rect.left(), 0, bar_rect.right(), 0)
        steps = 20
        for i in range(steps + 1):
            t = i / steps
            k = THERMAL_COND_MIN * (THERMAL_COND_MAX / THERMAL_COND_MIN) ** t
            color = thermal_conductivity_to_color(k)
            color.setAlpha(255)  # opaque in the legend - no shape underneath to blend with
            gradient.setColorAt(t, color)
        painter.fillRect(bar_rect, gradient)
        painter.setPen(QPen(Qt.black))
        painter.drawRect(bar_rect)

        log_min, log_max = math.log10(THERMAL_COND_MIN), math.log10(THERMAL_COND_MAX)
        for value in self.TICK_VALUES:
            t = (math.log10(value) - log_min) / (log_max - log_min)
            x = bar_rect.left() + t * bar_rect.width()
            painter.drawLine(int(x), bar_bottom, int(x), tick_mark_bottom)
            label = f"{value:g}"
            painter.drawText(int(x) - 15, tick_label_top, 30, self._TICK_LABEL_HEIGHT,
                              Qt.AlignHCenter | Qt.AlignTop, label)

        painter.drawText(QRect(0, title_top, self.width(), self._TITLE_HEIGHT),
                          Qt.AlignHCenter | Qt.AlignTop, "Thermal conductivity κ, W/(m·K)")


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

        Title = APP_NAME + ' for Elmer Thermal'

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
        self.thermal_tab = PortsTab(self)
        self.mesh_tab = MeshTab(self)
        self.create_model_tab = CreateModelTab(self)
        self.modeleditor_tab = ModelEditorTab(self)

        # Add tabs
        self.tabs_widget.addTab(self.file_tab, "Input Files")
        self.tabs_widget.addTab(self.thermal_tab, "Thermal Sources + Boundaries")
        self.tabs_widget.addTab(self.mesh_tab, "Mesh")
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

        # 3D Field Result Viewer window (PyVista), lazily created - see
        # CreateModelTab.open_field_viewer()
        self.field_viewer_window = None

        # Do not auto-load default values at this early startup stage,
        # instead this is done from File menu
        # self.user_inputs_file = DEFAULT_SETTINGS_FILE
        # self.user_inputs = self.load_user_inputs(DEFAULT_SETTINGS_FILE)
        # saved_values.update(self.user_inputs)

        # Load all saved data into tabs
        self.load_all_tabs()

    # ---------- Menu actions ----------

    def show_version(self):
        setupEM_version = self.get_setupEM_version()
        gds2palace_version = self.get_gds2palace_version()
        version_info = f"Installed:\nsetupEM {setupEM_version}\ngds2palace {gds2palace_version}"

        # get latest available version information
        latest_setupThermal = self.get_latest_version("setupThermal")
        latest_gds2palace = self.get_latest_version("gds2palace")
        latest_info = f"Latest version:\nsetupEM {latest_setupThermal}\ngds2palace : {latest_gds2palace}"
        version_info = version_info + '\n\n' + latest_info
        upgrade_info = "\n\nYou can update using\n  pip install gds2palace --upgrade\n  pip install setupThermal --upgrade\nafter exiting this program"

        QMessageBox.information(self,"Version information",version_info + upgrade_info)


    # get_latest_version() lives in MainWindowBase (setup_common.py), which
    # also bounds the PyPI request with a timeout and try/except


    # ---------- Native config (*.tsimcfg) / Python import hooks ----------
    def apply_native_config_data(self, data):
        # update thermal objects, they are separate from the other internal data
        self.thermal_tab.update_thermalobjects_from_JSON (data.get("thermal"))

    def apply_python_import_data(self, file_path):
        # read thermal object assignments in workflow syntax for gds2palace Python code
        heatsource_defs, consttemp_defs  = parse_python_thermal_definitions(file_path)
        self.thermal_tab.update_thermalobjects_from_python (heatsource_defs, consttemp_defs)

    def native_config_extra_struct(self):
        return {"thermal": thermal_objects_to_struct(thermal_objects)}

    def update_target_layer_choices(self, metals_list):
        self.thermal_tab.update_layers(metals_list)

    def refresh_source_layer_hints(self):
        self.thermal_tab.refresh_missing_layer_annotations()
        self.thermal_tab.portslist_selection_changed()


    # ---------- Layout Preview hook ----------
    def get_layout_preview_markers(self):
        # flush the thermal tab's current table edits first, so the preview
        # reflects unsaved edits without requiring a tab switch
        self.thermal_tab.save_values()
        markers = thermal_objects_to_struct(thermal_objects)
        for marker in markers:
            if marker["type"] == "source":
                marker["kind"] = "source"
                marker["group"] = "Sources"
            else:
                marker["kind"] = "boundary"
                marker["group"] = "Boundaries"
        return markers


    # ---------- Preferences dialog hook ----------
    def open_preferences_dialog(self):
        dialog = PreferencesDialog(self)
        dialog.exec()


    # ---------- Stackup preview hooks (thermal conductivity) ----------
    def stackup_dielectric_color(self, material):
        return thermal_conductivity_to_color(_thermal_conductivity_for_color(material))

    def stackup_metal_color(self, material):
        return thermal_conductivity_to_color(_thermal_conductivity_for_color(material), alpha=METAL_ALPHA)

    def stackup_color_legend(self):
        return ThermalConductivityLegend()

    def stackup_dielectric_label(self, dielectric, material):
        if material.thermaltablename == "":
            material_string  = f'κ={material.thermalcond:.1f}'
        else:
            material_string = "κ=table"
        return material_string

    def stackup_metal_label(self, metal, material, is_sheet):
        if is_sheet:
            return ""
        else:
            return f'κ={material.thermalcond:.1f}'

    def stackup_via_label_suffix(self, metal, material):
        if material is not None:
            kappa_string = f'κ={material.thermalcond:.1f}'
        else:
            kappa_string = ""
        return "   " + kappa_string


def parse_python_thermal_definitions (file_path):
    # parse the port assignment from Python code for ElmerThermal
    #
    # thermal_objects = simulation_setup.all_thermal_objects()
    # thermal_objects.add_heatsource(simulation_setup.heatsource(power=0.05,source_layernum=201, target_layername='Activ'))
    # thermal_objects.add_consttemp(simulation_setup.constanttemp(temp=300,source_layernum=202, target_layername='ABOVE_PASSIVATION'))target_name
    #
    # return value are two lists of dictionaries, one dict for each thermal object

    # Function to parse the arguments inside simulation_port(...)
    def parse_args(arg_str):
        args = {}
        # Wrap the arguments into a fake function call so AST can parse it
        expr = ast.parse(f"f({arg_str})", mode='eval')
        for kw in expr.body.keywords:
            args[kw.arg] = ast.literal_eval(kw.value)  # safely evaluate literals
        return args

    # List to store parsed ports
    heatsource_defs = []
    consttemp_defs  = []

    # Read your input file line by line
    with open(file_path) as f:
        for line in f:
            if ".heatsource(" in line:
                start = line.index(".heatsource(") + len(".heatsource(")
                inside = line[start:].rstrip(") \n")  # remove trailing ')'
                heatsource_defs.append(parse_args(inside))

            if ".constanttemp(" in line:
                start = line.index(".constanttemp(") + len(".constanttemp(")
                inside = line[start:].rstrip(") \n")  # remove trailing ')'
                consttemp_defs.append(parse_args(inside))

    return heatsource_defs, consttemp_defs




# ---------- RUN APP ----------

def main():
    app = QApplication(sys.argv)

    if sys.platform.startswith("win"):
        app.setStyle(QStyleFactory.create("Windows"))

    # evaluate commandline
    parser = argparse.ArgumentParser()
    parser.add_argument("-gdsfile",  type=str, default = '', help="GDSII file to read")
    parser.add_argument("-xmlfile",  type=str, default = '', help="XML stackup file to read")
    parser.add_argument("-simcfg",   type=str, default = '', help="*.tsimcfg file that is loaded prior to reading files")
    args = parser.parse_args()

    # evaluate optional parameters
    gdsfile = args.gdsfile
    xmlfile = args.xmlfile
    simcfg  = args.simcfg

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

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
