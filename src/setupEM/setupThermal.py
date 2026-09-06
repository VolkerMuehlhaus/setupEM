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


import sys, json, os, pathlib, ast, webbrowser, argparse
import numpy as np
import importlib.metadata
import requests
import gdspy
from scipy.interpolate import interp1d
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit,QComboBox,QTableWidget,QHeaderView,
    QPushButton, QFileDialog, QTabWidget, QMessageBox, QGroupBox,
    QCheckBox, QAbstractItemView,QStyleFactory,QTableWidgetItem, QPlainTextEdit, QDialog
    )
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QFont, QSyntaxHighlighter, QPainter, QPen, QActionGroup
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
    )
    from thermal_results import build_thermal_summary, format_source_table, find_thermal_vtu
else:
    from .setup_common import (
        EDIT_STYLE_OPTIONAL, EDIT_STYLE_REQUIRED, COMBO_STYLE_REQUIRED, COMBO_STYLE_OPTIONAL,
        FileDropLineEdit, FileInputTab, PythonHighlighter, CodeEditor,
        VectorWidget, PopUpWindow, CreateModelTabBase, MainWindowBase,
    )
    from .thermal_results import build_thermal_summary, format_source_table, find_thermal_vtu


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
        self.sourcelayer_edit = QLineEdit("201")
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
                self.sourcelayer_edit.setText(str(201+selected_row))


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
            self.refinement_edit.setText("5")
            return False
        saved_values ["refined_cellsize"] = float(value)

        try:
            value = float(self.cells_maxsize_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for max. meshsize")
            self.cells_maxsize_edit.setText("100")
            return False
        saved_values ["meshsize_max"] = float(value)


        try:
            value = float(self.margins_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for dielectric oversize margin")
            self.margins_edit.setText("100")
            return False
        saved_values ["margin"] = float(value)

        # all saved
        return True


    def load_values(self):
        self.refinement_edit.setText(str(saved_values.get("refined_cellsize","5")))
        self.cells_maxsize_edit.setText(str(saved_values.get("meshsize_max","100")))
        self.margins_edit.setText(str(saved_values.get("margin","100")))



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

        # "View in ParaView" is thermal-only (Palace/EM results are S-parameters, not a 3D
        # field to open directly), added as its own row of the base class's buttons_grid so
        # it lines up with Preview/Create Mesh/Start Simulation above, in the same Actions group.
        row = self.buttons_grid.rowCount()
        self.paraview_btn = QPushButton("🖼️ View in ParaView")
        self.paraview_btn.clicked.connect(self.launch_paraview)
        self.buttons_grid.addWidget(self.paraview_btn, row, 0)

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

    def launch_paraview(self):
        run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"
        vtu_path = find_thermal_vtu(run_path)
        self._open_in_paraview(
            [vtu_path] if vtu_path else [],
            f"⚠️ No thermal results .vtu found yet under {run_path}\n"
        )

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
            with open(pymodel_filename, "w", encoding="utf-8") as f:
                f.write(code)
                f.close()

            # Run Python interpreter on that file
            python_exe = sys.executable  # Use the same Python interpreter
            self._process_purpose = "create_mesh"
            self.process.start(python_exe, [pymodel_filename])


        else:
            if not thermal_source_exists:
                QMessageBox.warning(self, "Error", "Model incomplete, no thermal source in model!")
            elif not thermal_boundary_exists:
                QMessageBox.warning(self, "Error", "Model incomplete, no constant temperature boundary in model!")



    def run_model(self):
        # Run model that we created before

        # clear log
        self.log_area.clear()
        self._process_purpose = "run_simulation"

        # try to start from output directory
        run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"

        if os.name == "nt":
            #  Windows

            self.log_area.appendPlainText('Setting work directory ' + run_path)


            self.process.setWorkingDirectory(run_path)
            # start simulation
            self.process.start("ElmerSolver")
        else:
            # Linux
            self.log_area.appendPlainText('Setting work directory ' + run_path)

            self.process.setWorkingDirectory(run_path)
            # start simulation
            self.process.start("ElmerSolver")



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
        ignore_list     = ['model_basename','sim_path','iterative']


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


    # ---------- Stackup preview hooks (thermal conductivity) ----------
    def stackup_dielectric_color(self, material):
        return QColor(Qt.white)

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
