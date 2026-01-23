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


import sys, json, os, pathlib, ast, webbrowser, argparse
import numpy as np
import importlib.metadata
import requests
from scipy.interpolate import interp1d
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit,QComboBox,QTableWidget,QHeaderView,
    QPushButton, QFileDialog, QTabWidget, QMessageBox, QGroupBox,  
    QCheckBox, QAbstractItemView,QStyleFactory,QTableWidgetItem, QPlainTextEdit, QDialog
    )
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QFont, QSyntaxHighlighter, QPainter, QPen, QActionGroup
from PySide6.QtCore import Qt, QRegularExpression, QProcess, QRect, QStandardPaths


# we expect gds2palace in the same directory as this code, or installed as module
# sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'gds2palace')))
from gds2palace import *


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


saved_values = {} # dictionary of user input in this application
simulation_ports = simulation_setup.all_simulation_ports() # store port settings

def get_saved_value (key, default):
    data = saved_values.get('saved_values',None)
    if data is not None:
        if key in data.keys():
            return data [key]
    else:
        if key in saved_values.keys():
            return saved_values [key]
        else:
            return default



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

        self.MainWindow = MainWindow # parent = MainWindow        

        self.main_layout = QVBoxLayout()
        self.main_layout.setAlignment(Qt.AlignTop)

        # ---------- GDSII FILE GROUP ----------
        self.gds_group = QGroupBox("GDSII Layout File")
        self.gds_layout = QVBoxLayout()

        self.gds_file_layout = QHBoxLayout()
        self.gds_file_edit = FileDropLineEdit([".gds",".GDS"], self.set_gds_file)
        self.gds_file_edit.setText( "Please choose a file ===>")
        self.gds_file_edit.setStyleSheet(EDIT_STYLE_REQUIRED)

        self.browse_gds_btn = QPushButton("Browse ...")
        self.browse_gds_btn.setFixedWidth(150)  # narrower
        self.browse_gds_btn.clicked.connect(self.browse_gds_file)

        self.gds_file_layout.addWidget(self.gds_file_edit)
        self.gds_file_layout.addWidget(self.browse_gds_btn)

        self.gds_layout.addLayout(self.gds_file_layout)


        self.purpose_layout = QHBoxLayout()
        self.purpose_label1    = QLabel("Read this datatype (purpose):  ")
        self.purpose_edit = QLineEdit("0")
        self.purpose_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.purpose_edit.setFixedWidth(70)
        self.purpose_label2    = QLabel(" (default=0, multiple values can be separated by comma)")
        self.purpose_layout.addWidget(self.purpose_label1)
        self.purpose_layout.addWidget(self.purpose_edit)
        self.purpose_layout.addWidget(self.purpose_label2)
        self.purpose_layout.addStretch()
        self.gds_layout.addLayout(self.purpose_layout)



        self.viamerge_layout = QHBoxLayout()
        self.viamerge_label1    = QLabel("Merge via arrays with spacing ")
        self.viamerge_edit = QLineEdit("0")
        self.viamerge_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.viamerge_edit.setFixedWidth(70)
        self.viamerge_label2    = QLabel(" micron or more, value 0 disables via array merging")
        self.viamerge_layout.addWidget(self.viamerge_label1)
        self.viamerge_layout.addWidget(self.viamerge_edit)
        self.viamerge_layout.addWidget(self.viamerge_label2)
        self.viamerge_layout.addStretch()
        self.gds_layout.addLayout(self.viamerge_layout)

        self.preprocess_layout = QHBoxLayout()
        self.preprocess_gds_checkbox = QCheckBox()
        self.preprocess_gds_checkbox.setFixedWidth(20)
        self.preprocess_gds_label = QLabel("Preprocess GDSII file (required for polygons with holes/cutouts)")
        self.preprocess_layout.addWidget(self.preprocess_gds_checkbox)
        self.preprocess_layout.addWidget(self.preprocess_gds_label)
        self.gds_layout.addLayout(self.preprocess_layout)


        self.gds_group.setLayout(self.gds_layout)


        # ---------- XML FILE GROUP ----------

        self.XML_group = QGroupBox("XML Stackup File")
        self.XML_layout = QVBoxLayout()

        self.XML_file_layout = QHBoxLayout()
        self.XML_file_edit = FileDropLineEdit([".xml",".XML"], self.set_XML_file)
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
        

        filename, _ = QFileDialog.getOpenFileName(self, "Select GDSII File",previous_directory,"*.gds;;*.*")
        if filename:
            self.set_gds_file(filename)


    def set_gds_file(self, filename):
        # clear model name and target dir if they were auto-generated from previous model
        self.MainWindow.clear_modelname_and_targetdir()
        self.gds_file_edit.setText(filename)
        saved_values ["GdsFile"] = filename.replace('\\','/')
        # file is read when leaving the files tab

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

        filename, _ = QFileDialog.getOpenFileName(self, "Select XML Stackup File",previous_directory,"*.xml;;*.*")
        if filename:
            self.set_XML_file(filename)


    def set_XML_file (self, filename):
        self.XML_file_edit.setText(filename)
        saved_values ["SubstrateFile"] = filename.replace('\\','/')
        self.MainWindow.read_XML() # safe if invalid filename
        # file is read when leaving the files tab


    def load_values(self):
        self.gds_file_edit.setText(get_saved_value("GdsFile","Please choose a file ===>"))
        XML = get_saved_value("SubstrateFile","Please choose a file ===>")
        self.XML_file_edit.setText(XML)
        self.viamerge_edit.setText(str(get_saved_value("merge_polygon_size","0.5")))
        self.preprocess_gds_checkbox.setChecked(bool(get_saved_value("preprocess_gds",True)))

        int_list  = saved_values.get("purpose","0")
        purpose_string = str(int_list).replace('[','').replace(']','')
        self.purpose_edit.setText(purpose_string)
        # self.purpose_edit.setText(','.join(map(str, int_list)))

        # read_XML(self.XML_file_edit.text()) # safe if invalid filename


    def save_values(self):
        saved_values ["GdsFile"] = self.gds_file_edit.text().replace('\\','/')
        saved_values ["SubstrateFile"] = self.XML_file_edit.text().replace('\\','/')
        saved_values ["preprocess_gds"] = self.preprocess_gds_checkbox.isChecked()
        
        try:
            merge_polygon_size = float(self.viamerge_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", f"Not a valid value for via array merging")
            self.viamerge_edit.setText("0")
            return False
        saved_values ["merge_polygon_size"] = float(merge_polygon_size)


        text = self.purpose_edit.text()
        if text != "":
            # save as list of comma separated values
            saved_values ["purpose"] = ast.literal_eval('['+text+']')
        else:
            saved_values ["purpose"] = [0] # safe default

        # also trigger the load function of CreateModelTab, because that uses gds file info
        self.MainWindow.create_model_tab.load_values()

        # read Substrate file, which also updates port target layer choices
        self.MainWindow.read_XML()

        return True  # Tab change only possible when returning True
        

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
        self.fdump_layout.addWidget(QLabel("fdump [GHz], values separated by comma"))
        self.fdump_edit = QLineEdit("")
        self.fdump_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.fdump_layout.addWidget(self.fdump_edit)
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
        try:
            fstart = float(self.start_edit.text())
        except Exception:
            # the only case when this field can be empty is when fpoint or fdump are defined
            if self.start_edit.text()=="" and (self.fpoint_edit.text() != "" or self.fdump_edit.text() != ""):
                return True
            else:
                QMessageBox.warning(self, "Error", "Not a valid value for fstart")
                self.start_edit.setText("0")
                return False
        saved_values ["fstart"] = float(fstart)

        try:
            fstop = float(self.stop_edit.text())
        except Exception:
            # the only case when this field can be empty is when fpoint or fdump are defined
            if self.stop_edit.text()=="" and (self.fpoint_edit.text() != "" or self.fdump_edit.text() != ""):
                return True
            else:            
                QMessageBox.warning(self, "Error", "Not a valid value for fstop")
                self.stop_edit.setText("50")
                return False
        saved_values ["fstop"] = float(fstop)

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

        text = self.fdump_edit.text()
        if text != "":
            saved_values ["fdump"] = ast.literal_eval('['+text+']')
        else:    
            saved_values.pop("fdump",None)

        # if fstart == fstop == fdump or fstart == fstop == fstep, then remove fstart, fstop
        if saved_values ["fstart"] == saved_values ["fstop"]:
            discrete_list1 = saved_values.get("fpoint", [])
            discrete_list2 = saved_values.get("fdump", [])
            if saved_values ["fstart"] in discrete_list1 or saved_values ["fstart"] in discrete_list2:
                self.start_edit.setText("")
                self.stop_edit.setText("")
                self.step_edit.setText("")


        return True  # Tab change only possible when returning True

    def load_values(self):
        self.start_edit.setText(str(saved_values.get("fstart","0")))
        self.stop_edit.setText(str(saved_values.get("fstop","50")))
        self.step_edit.setText(str(saved_values.get("fstep","")))

        float_list  = saved_values.get("fpoint","")
        self.fpoint_edit.setText(','.join(map(str, float_list)))

        float_list  = saved_values.get("fdump","") 
        self.fdump_edit.setText(','.join(map(str, float_list)))


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
        self.sourcelayer_edit = QLineEdit("201")
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
                self.sourcelayer_edit.setText(str(201+selected_row))


    def save_values(self):
        # clear previous port data in simulation_ports instance
        simulation_ports.ports.clear()
        # loop over rows in ports table
        for row in range(self.portslist.rowCount()):
            portnumber = row+1
            testvalue = self.portslist.item(row, 0)
            if testvalue != None: # not an empty port line
                if testvalue.text != "":
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
                self.portslist.setItem(portnum-1, 3, QTableWidgetItem(str(port.get("target_layername",""))))
                self.portslist.setItem(portnum-1, 4, QTableWidgetItem(str(port.get("from_layername",""))))
                self.portslist.setItem(portnum-1, 5, QTableWidgetItem(str(port.get("to_layername",""))))
                self.portslist.setItem(portnum-1, 6, QTableWidgetItem(str(port.get("direction","")).upper()))

        self.portslist.selectRow(1)
        self.portslist.selectRow(0)



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

        self.cells_lambda_layout = QHBoxLayout()
        self.label4 = QLabel("Mesh cells per wavelength")
        self.label4.setFixedWidth(label_width)  
        self.cells_lambda_layout.addWidget(self.label4)
        self.cells_lambda_edit = QLineEdit("10")
        self.cells_lambda_edit.setFixedWidth(edit_width)
        self.cells_lambda_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.cells_lambda_layout.addWidget(self.cells_lambda_edit)
        self.label5 = QLabel(" (min 10)")
        self.cells_lambda_layout.addWidget(self.label5)
        self.cells_lambda_layout.addStretch()
        self.mesh_layout.addLayout(self.cells_lambda_layout)        


        self.cells_maxsize_layout = QHBoxLayout()
        self.label6 = QLabel("Mesh cell maximum size absolute")
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


        self.meshorder_layout = QHBoxLayout()
        self.label1 = QLabel("Mesh basis function")
        self.label1.setFixedWidth(label_width)  
        self.meshorder_layout.addWidget(self.label1)

        self.mesh_order_box = QComboBox()
        self.mesh_order_box.setFixedWidth(edit_width)
        self.mesh_order_box.setStyleSheet(COMBO_STYLE_OPTIONAL)
        self.mesh_order_box.addItems(["faster, less accurate","most accurate"])
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

        self.cells_AMRiterations_layout = QHBoxLayout()
        self.labelAMR1 = QLabel("Adaptive mesh iterations")
        self.labelAMR1.setFixedWidth(label_width)  
        self.cells_AMRiterations_layout.addWidget(self.labelAMR1)
        self.AMR_iterations_edit = QLineEdit("0")
        self.AMR_iterations_edit.setFixedWidth(edit_width)
        self.AMR_iterations_edit.setStyleSheet(EDIT_STYLE_OPTIONAL)
        self.cells_AMRiterations_layout.addWidget(self.AMR_iterations_edit)
        self.labelAMR2 = QLabel(" (default is 0, no adaptive mesh refinement)")
        self.cells_AMRiterations_layout.addWidget(self.labelAMR2)
        self.cells_AMRiterations_layout.addStretch()
        self.AMR_layout.addLayout(self.cells_AMRiterations_layout)        
   
        self.AMR_group.setLayout(self.AMR_layout)
        self.main_layout.addWidget(self.AMR_group)
        self.main_layout.addSpacing(20)


        # ---------- BOUNDARY GROUP ----------

        self.mesh_group = QGroupBox("Boundary settings")
        self.mesh_layout = QVBoxLayout()

        self.boundary_layout = QHBoxLayout()
        self.label6 = QLabel("Boundary conditions")
        self.label6.setFixedWidth(label_width)  
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


        self.setLayout(self.main_layout)


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


    def save_values(self):
        try:
            value = float(self.refinement_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for mesh refinement")
            self.refinement_edit.setText("5")
            return False
        saved_values ["refined_cellsize"] = float(value)

        saved_values ["order"] = self.mesh_order_box.currentIndex()+1

        try:
            value = float(self.cells_lambda_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for cells/wavelength")
            self.cells_lambda_edit.setText("10")
            return False
        saved_values ["cells_per_wavelength"] = float(value)

        try:
            value = float(self.cells_maxsize_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for max. meshsize")
            self.cells_maxsize_edit.setText("100")
            return False
        saved_values ["meshsize_max"] = float(value)

        try:
            value = int(self.AMR_iterations_edit.text())
        except Exception:
            QMessageBox.warning(self, "Error", "Not a valid value for AMR iterations")
            self.AMR_iterations_edit.setText("0")
            return False
        saved_values ["adaptive_mesh_iterations"] = int(value)

        
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
            self.margins_edit.setText("200")
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
        self.refinement_edit.setText(str(saved_values.get("refined_cellsize","5")))        
        self.cells_lambda_edit.setText(str(saved_values.get("cells_per_wavelength","10")))        
        self.cells_maxsize_edit.setText(str(saved_values.get("meshsize_max","100")))        
        self.AMR_iterations_edit.setText(str(saved_values.get("adaptive_mesh_iterations","0")))        
        self.margins_edit.setText(str(saved_values.get("margin","200")))        

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
            # no value defined, use same value as dielectric margins
            self.airaround_edit.setText(saved_values.get("margin","200"))
            self.airaround_box.setCurrentIndex(0)
        else:
            if "," in str(air):
                # we have a list of 6 values
                air_as_list = air.split(',')
                if len(air_as_list) == 6:
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



class CreateModelTab(QWidget):
    def __init__(self, MainWindow):
        super().__init__()

        self.MainWindow = MainWindow # parent = MainWindow

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
        saved_values['model_basename']= self.modelname_edit.text()


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
            saved_values['sim_path'] = str(directory)


    def save_values(self):
        saved_values['model_basename'] = self.modelname_edit.text()
        saved_values['sim_path'] = self.targetdir_edit.text().replace('\\','/')

        return True # Tab change only possible when returning True


    def load_values(self):
        # set target dir to GDSII directory by default
        gdsfile = saved_values.get("GdsFile","")

        model_basename = saved_values.get('model_basename','')
        if model_basename == "":
            if gdsfile != "":
                model_basename = os.path.basename(gdsfile).replace('.gds', '')
                if "===" in model_basename:
                    model_basename = ""

        # no simulator name prefix
        model_basename = model_basename.replace('palace_','')
        model_basename = model_basename.replace('elmer_','')
            
        self.modelname_edit.setText(model_basename)

        sim_path = saved_values.get('sim_path','')
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
                self.log_area.appendPlainText("⚠️ Cannot load XML stackup file!\n"+saved_values.get("SubstrateFile")+"\n")
        else:
            self.log_area.appendPlainText("⚠️ Cannot load GDSII layout stackup file!\n"+saved_values.get("GdsFile")+"\n")



    def create_mesh(self):
        # create model and run gmsh, but skip the final mesh and output file creation

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
                self.log_area.appendPlainText("⚠️ Cannot load XML stackup file!\n"+saved_values.get("SubstrateFile")+"\n")
        else:
            self.log_area.appendPlainText("⚠️ Cannot load GDSII layout stackup file!\n"+saved_values.get("GdsFile")+"\n")



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
            with open(pymodel_filename, "w", encoding="utf-8") as f:
                f.write(code)
                f.close()

            # Run Python interpreter on that file
            python_exe = sys.executable  # Use the same Python interpreter
            self.process.start(python_exe, [pymodel_filename])

          
        else:
            QMessageBox.warning(self, "Error", "Model incomplete, there are no simulation ports defined!")            



    def terminate_run(self):
        if self.process.state() == QProcess.Running:
            self.process.terminate()
            if not self.process.waitForFinished(2000):
                self.process.kill()



    def run_model(self):
        # Run model that we created before

        # clear log
        self.log_area.clear()

        if self.MainWindow.PalaceMode:
            self.log_area.appendPlainText("Trying to start Palace using script ./run_sim now")
            run_path = saved_values['sim_path'] + "/palace_model/" + saved_values['model_basename'] + "_data"

            if os.name == "nt":
                #  Windows

                def windows_to_wsl_path(win_path: str) -> str:
                    """
                    Convert a Windows-style path like:
                        C:\\Users\\Volker\\Projects\\SimApp
                    into a WSL-style path like:
                        /mnt/c/Users/Volker/Projects/SimApp
                    """
                    win_path = win_path.strip()
                    if not win_path or ":" not in win_path:
                        return win_path  # Already looks like a Linux path or invalid
                    drive, rest = win_path.split(":", 1)
                    drive = drive.lower()
                    rest = rest.replace("\\", "/").lstrip("/")
                    return f"/mnt/{drive}/{rest}"


                wsl_run_path = windows_to_wsl_path(run_path)
                # tell user what to do, we can open WSL in the right place but not start simulation
                self.log_area.appendPlainText("Running on Windows with WSL, you need to type ./run_sim in the terminal yourself!")
                self.log_area.appendPlainText("Note that this works for LOCAL drives only, we can't open WSL on network drive.")
                self.process.start("cmd.exe", ["/c", "start", "wt.exe", "wsl", "--cd", wsl_run_path])       
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

            # try to start from output directory
            run_path = saved_values['sim_path'] + "/elmer_model/" + saved_values['model_basename'] + "_data"

            if os.name == "nt":
                #  Windows

                self.log_area.appendPlainText('Setting work directory ' + run_path)
                
                # rename file to batch file
                run_file_orig = os.path.join(run_path, 'run_elmer')
                run_file = run_file_orig.replace('run_elmer','run_elmer.bat')
                os.rename(run_file_orig, run_file)

                self.process.setWorkingDirectory(run_path)
                # start simulation
                self.process.start("run_elmer.bat")
            else:
                # Linux
                self.log_area.appendPlainText('Setting work directory ' + run_path)
                # make file executable
                run_file = os.path.join(run_path, 'run_elmer')
                os.chmod(run_file, 0o755)

                self.process.setWorkingDirectory(run_path)
                # start simulation
                self.process.start(".//run_elmer")
      

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
        ignore_list     = ['model_basename','sim_path']


        # Keywords that are excluded in Palace mode
        if self.MainWindow.PalaceMode:
            ignore_list.append('iterative')

        if forExport:
            # these commands are only used within this GUI application to control gmsh
            ignore_list.append(['preview_only','no_preview'])

        for key in saved_values.keys():
            if not key in special_keylist: 
                if not key in ignore_list:
                    add_key(key)


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
        add_text("materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate (settings['SubstrateFile'])")
        add_text("layernumbers = metals_list.getlayernumbers()")
        add_text("layernumbers.extend(simulation_ports.portlayers)")
        add_text("\n# read geometries from GDSII")
        add_text("allpolygons = gds_reader.read_gds(settings['GdsFile'], "
                "\n\tlayernumbers,"
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
                add_text("      subprocess.run(run_command, shell=True)")
                add_text("  except:")
                add_text("      print(f'Unable to run Elmer using command ',run_command)\n")



    def save_values(self):
        self.create_model_text(forExport=True)  # show "external" code including run from Python model
        return True

    def load_values(self):
        self.create_model_text(forExport=True)  # show "external" code including run from Python model


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


class VectorWidget(QWidget):
    """This widget actually draws the stackup preview"""

    def __init__(self, materials_list,dielectrics_list,metals_list): 
        super().__init__()
        self.materials_list = materials_list
        self.dielectrics_list = dielectrics_list
        self.metals_list = metals_list


    def paintEvent(self, event):

        # utility: flip y to have y=0 at bottom
        def flipy(y):
            return self.height()-y

        # utility to draw text with alignment on right side
        def drawText_right (x, y, w, h, text):
            rect = QRect(x, y-h, w, h)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, text)

        def drawText_left (x, y, w, h, text):
            rect = QRect(x, y-h, w, h)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)

        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        painter.setRenderHint(QPainter.Antialiasing)
        
        xmin = int(self.width()*0.02)
        xmax = int(self.width()*0.98)

        ymin = int(self.height()*0.025)
        ymax = int(self.height()*0.975)

        penBlack = QPen(Qt.black, 1)
        penGray  = QPen(QColor(134, 132, 130))
        penDarkGray = QPen(QColor(53, 50, 47))

        # get total dielectric parts, where each metal in a dielectric adds one part 
        dielectric_shapes = []
        total_parts = 0
        dielectrics_bottom_up = self.dielectrics_list.dielectrics[::-1]
        for dielectric in dielectrics_bottom_up: # bottom up
            painter.setPen(penBlack)

            metals_inside = dielectric.get_planar_metals_inside()
            # get number of unique zmin values in that list
            zmin_list = []
            for metal in metals_inside:
                if not metal.zmin in zmin_list:
                    zmin_list.append(metal.zmin)
            metals_count = len(zmin_list)

            # first metal not aligned with dielectric?
            if len(metals_inside)>0:
                if metals_inside[0].zmin > dielectric.zmin:
                    metals_count = metals_count+ 0.5

            parts = max (1,metals_count)
            dielectric_shape = {}
            dielectric_shape['name'] = dielectric.name
            dielectric_shape['dielectric'] = dielectric
            dielectric_shape['numparts'] = parts

            # dielectric color is calculated from permittivity
            materialname = dielectric.material
            material = self.materials_list.get_by_name(materialname)
            eps = material.eps
            dielectric_shape['color'] =  epsilon_to_color(eps, 95) # QColor(Qt.white) 
            dielectric_shape['material'] = material
         
            total_parts = total_parts + parts
            dielectric_shapes.append(dielectric_shape)

        # calculate height of one dielectric shape
        total_parts = max(total_parts, 1)
        part_height = int((ymax-ymin)/(total_parts))

        y = ymin
        w = xmax-ymin

        # we need to store data for original z position and the displayed y position 
        stored_z = np.array([0])
        stored_y = np.array([ymin])


        for dielectric_shape in dielectric_shapes:
            h = part_height * dielectric_shape['numparts']
            dielectric = dielectric_shape['dielectric']
            color = dielectric_shape['color']
            material = dielectric_shape['material']
            
            material_string  = f'εr={material.eps:.1f}'
            if material.sigma > 1e-3:
                material_string = material_string + f' σ={material.sigma:.1f}'
            material_string = material_string + f'\n{dielectric.thickness:.2f}µm'

            painter.setPen(penBlack)
            painter.setBrush(color)
            painter.drawRect(xmin, flipy(y), w, -h)   
            drawText_left  (xmin+5, flipy(y), w, h, dielectric.name)   
            drawText_right (xmin, flipy(y), w-5, h, material_string)   

            if not dielectric.zmax in stored_z:
                stored_z = np.append(stored_z, dielectric.zmax)
                stored_y = np.append(stored_y, y+h)


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
                    elevation = metal.zmin-dielectric.zmin
                    if n==0 and (abs(elevation) > 0.001):
                        # draw some vertical offset, not aligned with dielectric
                        ymetal = ymetal + part_height*0.5 # slight offset

                    # check if next metal is at same zmin
                    next_at_same_zmin = False
                    previous_at_same_zmin = False
                    xmetal = xmin+120
                    wmetal = w-200

                    if n < len(metals_inside)-1:
                        next_metal = metals_inside[n+1]
                        if abs(next_metal.zmin - metal.zmin) < 0.001:
                            next_at_same_zmin = True
                            xmetal = xmin+120
                            wmetal = int(w/2)-100
                    else:
                        next_metal = None        
                    
                    if n > 0:
                        previous_metal = metals_inside[n-1]
                        if abs(previous_metal.zmin - metal.zmin) < 0.001:
                            xmetal = xmin+int(w/2)+20
                            wmetal = int(w/2)-100
                            previous_at_same_zmin = True
                   
                    material = self.materials_list.get_by_name(metal.material)
                    if material is not None:
                        if metal.is_sheet:
                            # sheet metal that is simulated with zero extrusion
                            height = 3
                            resistance_string = f'Rs={material.Rs*1e3:.1f}mΩ'
                        else:  
                            # regular extruded metal
                            height = part_height/2
                            if (material.sigma > 0) and (metal.thickness > 0):
                                Rs = 1 / (material.sigma*metal.thickness*1e-6)
                                if Rs < 1:
                                    resistance_string = f'Rs={Rs*1e3:.1f} mΩ'
                                else:    
                                    resistance_string = f'Rs={Rs:.2f} Ω'
                            else:
                                resistance_string = '? ' + material.type + ' ?'        

                        # the box for this metal
                        if material.type.upper() == "CONDUCTOR":
                            painter.setBrush(QColor(230,230,230, 90))
                            painter.drawRect(xmetal, flipy(ymetal), wmetal, -int(height))
                        else:    
                            painter.setBrush(QColor(230,130,130, 90))
                            painter.drawRect(xmetal, flipy(ymetal), wmetal, -int(height))
                    else:
                        # material assignment is invalid
                        height = part_height/2
                        painter.setBrush(QColor(255,0,0, 80))
                        painter.drawRect(xmetal, flipy(ymetal), wmetal, -int(height))
                        resistance_string = 'INVALID MATERIAL REFERENCE: ' + metal.material 

                    painter.setPen(penBlack)
                    drawText_left(xmetal+10, flipy(ymetal), wmetal, part_height/2, f"{metal.name} ({metal.layernum})" )   
                    painter.setPen(penGray)
                    drawText_right(xmetal, flipy(ymetal), wmetal-10, part_height/2, resistance_string)   
                    # store the drawing position, because vias will refer to that
                    if not metal.zmin in stored_z:
                        stored_z = np.append(stored_z, metal.zmin)
                        stored_y = np.append(stored_y, ymetal)
                    if not metal.zmax in stored_z:
                        stored_z = np.append(stored_z, metal.zmax)
                        stored_y = np.append(stored_y, ymetal+height)

                    painter.setPen(penGray)
                    painter.drawLine(xmetal-60, flipy(ymetal), xmetal-10, flipy(ymetal))
                    # draw line at top side of metal
                    if not metal.is_sheet:
                        painter.drawLine(xmetal-60, flipy(ymetal+height), xmetal-10, flipy(ymetal+height))
                        heightstring = f'{metal.thickness:.3f}µm'
                        painter.setPen(penDarkGray)
                        drawText_left(xmetal-60,  flipy(ymetal), 50, height, heightstring)


                    if not previous_at_same_zmin:
                        # draw height to metal above
                        if next_metal is not None:
                            dz = abs(next_metal.zmin - metal.zmax)
                            heightstring = f'{dz:.3f}µm'
                            painter.setPen(penGray)
                            drawText_left(xmetal-60,  flipy(ymetal+height), 50, height, heightstring)

                    if n==len(metals_inside)-1:
                        # last metal (top metal)
                        # place text for distance to dielectric boundary
                    
                        painter.setPen(penBlack)
                        dz = dielectric.zmax - metal.zmax
                        if dz > 10:
                            heightstring = f'{dz:.1f}µm'
                        else:    
                            heightstring = f'{dz:.3f}µm'
                        painter.setPen(penGray)
                        painter.drawText(xmetal-60,  flipy(ymetal+height+5), heightstring)
                    
                    if n==0 and elevation > 0.001:
                        # metal not aligned with bottom of dielectric, add a label for offset value
                        heightstring = f'{elevation:.3f}µm'
                        painter.setPen(penGray)
                        painter.drawText(xmetal-60,  flipy(ymetal-10), heightstring)


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
            w = (xmax-xmin)/10

            painter.setBrush(QColor(136,192,200,80))
            for metal in self.metals_list.metals:
                if metal.is_via or metal.is_dielectric:
                    y1 = z_to_y(metal.zmin)
                    y2 = z_to_y(metal.zmax)
                    h  = abs(y2-y1)

                    if pos == 1:
                        xvia = (xmax+xmin)/2-4*w/2
                        pos = 2
                    elif pos == 2:
                        xvia = (xmax+xmin)/2-w/2
                        pos = 3
                    else:
                        xvia = (xmax+xmin)/2+w
                        pos = 1

                    painter.setPen(penBlack)
                    painter.drawRect(xvia, flipy(y1), w, -h)
                    painter.drawText(xvia+5, flipy(y1+5), f"{metal.name} ({metal.layernum})")   

        painter.end()


class PopUpWindow(QDialog):
    """This window shows the substrate stackup preview"""
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
                                          self.MainWindow.metals_list)
        layout.addWidget(self.vector_widget)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.setLayout(layout)
        self.setModal(True)



# ---------- MAIN WINDOW ----------


class MainWindow(QMainWindow):
    TAB_HEADER_COLORS = ["#FFCDD2", "#C8E6C9", "#BBDEFB", "#FFF9C4", "#D1C4E9"]

    def __init__(self):
        super().__init__()

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

        # Do not auto-load default values at this early startup stage, 
        # instead this is done from File menu
        # self.user_inputs_file = DEFAULT_SETTINGS_FILE
        # self.user_inputs = self.load_user_inputs(DEFAULT_SETTINGS_FILE)
        # saved_values.update(self.user_inputs)

        # Load all saved data into tabs
        self.load_all_tabs()

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
        self.load_default_action.triggered.connect(lambda: self.load_configuration_from_file(DEFAULT_SETTINGS_FILE))
        self.save_action.triggered.connect(lambda: self.save_ask_filenamefile())
        self.savedefault_action.triggered.connect(lambda: self.save_user_inputs_to_file(DEFAULT_SETTINGS_FILE))

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



    # ---------- Menu actions ----------
    def setPalaceMode(self):
        self.optionPalace.setChecked(True)
        self.PalaceMode = True
        self.ElmerMode  = False
        self.setWindowTitle(APP_NAME + ' Palace')
        self.frequencies_tab.dump_group.setVisible(True)
        self.mesh_tab.AMR_group.setVisible(True)
        self.mesh_tab.Elmer_group.setVisible(False)

        # update mesh settings that are not always visible
        self.mesh_tab.on_meshorder_changed(self.mesh_tab.mesh_order_box.currentText())
      

    def setElmerMode(self):
        self.optionElmer.setChecked(True)
        self.PalaceMode = False
        self.ElmerMode  = True
        self.setWindowTitle(APP_NAME + ' Elmer')
        self.frequencies_tab.dump_group.setVisible(False)
        self.mesh_tab.AMR_group.setVisible(False)
        self.mesh_tab.Elmer_group.setVisible(True)

        # update mesh settings that are not always visible
        self.mesh_tab.on_meshorder_changed(self.mesh_tab.mesh_order_box.currentText())


    def show_version(self):
        setupEM_version = importlib.metadata.version("setupEM")
        gds2palace_version = importlib.metadata.version("gds2palace")
        version_info = f"Installed:\nsetupEM {setupEM_version}\ngds2palace {gds2palace_version}"

        # get latest available version information
        latest_setupEM = self.get_latest_version("setupEM")
        latest_gds2palace = self.get_latest_version("gds2palace")
        latest_info = f"Latest version:\nsetupEM {latest_setupEM}\ngds2palace : {latest_gds2palace}"
        version_info = version_info + '\n\n' + latest_info
        upgrade_info = "\n\nYou can update using\n  pip install gds2palace --upgrade\n  pip install setupEM --upgrade\nafter exiting this program"

        QMessageBox.information(self,"Version information",version_info + upgrade_info)


    def get_latest_version(self, package_name: str) -> str:
        url = f"https://pypi.org/pypi/{package_name}/json"
        data = requests.get(url).json()
        return data["info"]["version"]



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
        if index == 5:
            self.save_all_tabs()

        # Save model code only when model tab active
        self.export_model_action.setEnabled(index == 5)



    # ---------- User input persistence ----------

    def load_all_tabs(self):
        self.file_tab.load_values()
        self.frequencies_tab.load_values()
        self.ports_tab.load_values()
        self.mesh_tab.load_values()
        self.create_model_tab.load_values()
        self.modeleditor_tab.load_values()

    def save_all_tabs(self):
        self.file_tab.save_values()
        self.frequencies_tab.save_values()
        self.ports_tab.save_values()
        self.mesh_tab.save_values()
        self.create_model_tab.save_values()


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
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Settings File", filter=f"*.{CONFIG_SUFFIX};;Python model code *.py")
        # we can load JSON or Python models, decide which suffix we have
        if file_path:
            self.load_configuration_from_file(file_path)


    def load_configuration_from_file(self, file_path):
        if file_path:
            extension = pathlib.Path(file_path).suffix
            if CONFIG_SUFFIX.upper() in extension.upper():
                # regular data storage
                self.user_inputs_file = file_path
                # call the native config file loading function
                data = self.load_user_inputs(file_path)
                if data.get("application","") == "setupEM":
                    # update internal data structure
                    saved_values.clear()
                    saved_values.update(data.get("saved_values"))
                    # update ports, there are separate from the other internal data 
                    self.ports_tab.update_port_from_import (data.get("ports"))
                    self.load_all_tabs()
                    QMessageBox.information(self, "Loaded", f"Settings loaded from {file_path}")
                    self.create_model_tab.log_area.clear()
                else:
                    QMessageBox.information(self, "Failed", "Unknown data format")
            elif extension.upper() == ".PY":
                import_mapping = {
                    "gds_filename":"GdsFile",
                    "XML_filename":"SubstrateFile",
                    "GdsFile":"GdsFile",
                    "purpose":"purpose",
                    "SubstrateFile":"SubstrateFile",
                    "merge_polygon_size":"merge_polygon_size",
                    "preprocess_gds":"preprocess_gds",
                    "purpose":"purpose",
                    "margin":"margin",
                    "air_around":"air_around",
                    "boundary":"boundary",
                    "fstart":"fstart",
                    "fstop":"fstop",
                    "fstep":"fstep",
                    "fpoint":"fpoint",
                    "fdump":"fdump",
                    "refined_cellsize":"refined_cellsize",
                    "cells_per_wavelength":"cells_per_wavelength",
                    "meshsize_max":"meshsize_max",
                    "adaptive_mesh_iterations":"adaptive_mesh_iterations",
                    "order":"order",
                    "iterative":"iterative",
                    "elmer":"elmer"
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
                            if import_key not in import_value: # skip the section where key might appear in different context
                                # get the internal name for this variable
                                varname = import_mapping.get(import_key, '')
                                if varname == "fpoint" or varname == "fdump":
                                    saved_values [varname] = ast.literal_eval(import_value)
                                elif varname in ["gds_filename","XML_filename","GdsFile","SubstrateFile"]:
                                    # check if we have full path for files in imported Python script, 
                                    # otherwise prefix from *.py path assuming that it was local to the *.py model script
                                    value_path = os.path.dirname(import_value)    
                                    if value_path == '':
                                        import_value = os.path.join(modelcode_path,import_value)
                                    saved_values [varname] = import_value 
                                elif varname != '':
                                    raw = import_value.strip("[]")
                                    if varname in ['fstart','fstop','fstep']:
                                        saved_values [varname] = float(raw)/1e9
                                    else:    
                                        saved_values [varname] = raw 


                # read port assignments in workflow syntax for gds2palace Python code
                ports = parse_python_ports_definitions(file_path)
                self.ports_tab.update_port_from_import (ports)

                # set simulator
                if saved_values.get("elmer", False):
                    self.setElmerMode()
                else:
                    self.setPalaceMode()                        

                self.load_all_tabs()
                QMessageBox.information(self, "Loaded", f"Settings loaded from {file_path}")
                self.create_model_tab.log_area.clear()

            else:
                QMessageBox.information(self, "Error", f"Could not load file {file_path}")


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
            struct = {"application":"setupEM", 
                        "data_format":"1.0"}
            struct["saved_values"] = saved_values
            struct["ports"] = simulation_ports_to_struct (simulation_ports)

            with open(filename, "w") as f:
                json.dump(struct, f, indent=4)
            QMessageBox.information(self, "Saved", f"Settings saved to {filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save settings to {filename}: {e}")


    def save_ask_filenamefile(self):
        # make sure all tabs save their values
        # set gds filename as default for saving config 
        gds_name = saved_values.get("GdsFile")
        default_config = gds_name.replace('.gds','.simcfg')
        file_path, _ = QFileDialog.getSaveFileName(self, "Select Settings File", default_config, filter=f"{APP_NAME} (*.{CONFIG_SUFFIX})")
        # Ensure filename ends with CONFIG_SUFFIX
        if file_path:
            if not file_path.lower().endswith('.' + CONFIG_SUFFIX):
                file_path = file_path + '.' + CONFIG_SUFFIX
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
        saved_values['model_basename']=''
        # clear target directory if is the gds directory, but keep if other value
        gds_dir = os.path.dirname(saved_values['GdsFile'])
        target_dir = saved_values['sim_path']
        if target_dir.upper()==gds_dir.upper():
            saved_values['sim_path']=''


    # load technology stackup data
    def read_XML(self):
        filename = saved_values ["SubstrateFile"]
        if pathlib.Path(filename).exists():
            self.materials_list, self.dielectrics_list, self.metals_list = stackup_reader.read_substrate (filename)
            self.ports_tab.update_layers(self.metals_list)


    def open_popup(self):
        if os.path.isfile(saved_values ["SubstrateFile"]):
            self.popup = PopUpWindow(self)
            self.popup.show()
        else:
            QMessageBox.warning(self, "Error", "Substrate file not found")




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
            param = param.replace('settings','')
            param = param.strip("[]'").strip('"')
            value = value.strip("'").strip('"') 
            if not "settings" in value: # make sure we don't read the USE of a parameter
                parameters[param] = value

    return parameters

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

    # Function to parse the arguments inside simulation_port(...)
    def parse_port_args(arg_str):
        args = {}
        # Wrap the arguments into a fake function call so AST can parse it
        expr = ast.parse(f"f({arg_str})", mode='eval')
        for kw in expr.body.keywords:
            args[kw.arg] = ast.literal_eval(kw.value)  # safely evaluate literals
        return args

    # List to store parsed ports
    ports = []

    # Read your input file line by line
    with open(file_path) as f:
        for line in f:
            if "simulation_port(" in line:
                start = line.index("simulation_port(") + len("simulation_port(")
                inside = line[start:].rstrip(") \n")  # remove trailing ')'
                ports.append(parse_port_args(inside))

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