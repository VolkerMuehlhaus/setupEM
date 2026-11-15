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

"""The gds2palace model script that setupEM and setupThermal write (Code tab, File > Export, Create Model), made
from the settings, the ports or thermal objects, and the solver mode. No Qt: the GUIs call it after collecting the
tabs' values, and a command can call it with the contents of a settings file.

em_script_text(saved_values, simulation_ports, elmer_mode, for_export)   setupEM, Palace or Elmer EM
thermal_script_text(saved_values, thermal_objects, for_export)           setupThermal, Elmer thermal

saved_values holds frequencies in GHz, as the settings file does; the script gets them in Hz.
"""

import ast

from gds2palace import gds_reader, simulation_setup

# Solvers ("palace", "elmer", "elmer_thermal") for which the installed gds2palace applies
# settings['fill_factor_correction']; an older gds2palace silently ignores the setting, so
# only offer it where it has an effect. gds2palace declares this list itself since Elmer
# support was added; before that, the feature existed for Palace only.
FILL_FACTOR_CORRECTION_SOLVERS = getattr(
    simulation_setup, "FILL_FACTOR_CORRECTION_SOLVERS",
    ("palace",) if hasattr(gds_reader.all_polygons_list, "compute_via_fill_factors") else ())
# Palace linear solver settings (settings['complex_coarse_solve'/'solver_maxits'/'solver_tol'])
# the installed gds2palace understands - empty for an older gds2palace, which hardcodes them
# and would silently ignore these keys, so the Mesh tab hides its linear solver rows then.
PALACE_LINEAR_SOLVER_SETTINGS = tuple(getattr(simulation_setup, "PALACE_LINEAR_SOLVER_SETTINGS", ()))

# File > Export: the switch for running the exported script with no gmsh window, commented out (an import
# reads no settings from comments). gds2palace 0.10.4 and newer turns it on by itself without a display.
NO_GUI_EXPORT_LINE = ("# settings['no_gui'] = True   # no gmsh window, for unattended runs "
                      "(on Linux without a display, gds2palace 0.10.4 and newer skips it by itself)")

# keys written in Hz in the script, held in GHz in the settings
_GHZ_VALUES = ('fstart', 'fstop', 'fstep')
_GHZ_LISTS = ('fdump', 'fpoint')


def ports_from_struct(ports):
    """gds2palace's all_simulation_ports from the "ports" list of a settings file, as the Ports tab builds it from its
    table: numbers as float (Z0, voltage) and int (layer), ports in the order of their numbers. Raises ValueError for
    a port the Ports tab would refuse."""
    simulation_ports = simulation_setup.all_simulation_ports()
    for port in sorted(ports, key=lambda p: int(p["portnumber"])):
        number = int(port["portnumber"])
        try:
            z0, voltage = float(port.get("port_Z0", 50)), float(port.get("voltage", 1))
            layer = int(port["source_layernum"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Invalid input for port {number}") from None
        direction = str(port.get("direction") or "")
        via = "Z" in direction.upper()
        target, from_name, to_name = (port.get(k) or "" for k in ("target_layername", "from_layername",
                                                                   "to_layername"))
        # gds2palace's simulation_port() exits the whole program on a direction that does not match its layers
        if direction.strip().upper() not in ("X", "Y", "Z", "-X", "-Y", "-Z") or \
                not ((from_name and to_name) if via else target):
            raise ValueError(f"Port {number} is incomplete: direction X, Y, -X or -Y needs a target layer (in-plane "
                             "port), Z or -Z needs a from and a to layer (via port).")
        if via:
            simulation_ports.add_port(simulation_setup.simulation_port(
                portnumber=number, voltage=voltage, port_Z0=z0, source_layernum=layer,
                from_layername=from_name, to_layername=to_name, direction=direction))
        else:
            simulation_ports.add_port(simulation_setup.simulation_port(
                portnumber=number, voltage=voltage, port_Z0=z0, source_layernum=layer,
                target_layername=target, direction=direction))
    return simulation_ports


def thermal_objects_from_struct(objects):
    """gds2palace's all_thermal_objects from the "thermal" list of a settings file, as the thermal tab builds it.
    Raises ValueError for an entry the tab would refuse."""
    thermal_objects = simulation_setup.all_thermal_objects()
    for row, entry in enumerate(objects, start=1):
        is_source = "SOURCE" in str(entry.get("type", "")).upper()
        try:
            layer = int(entry["source_layernum"])
            value = float(entry["power"] if is_source else entry["temp"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Invalid input in row {row}") from None
        target = entry.get("target_layername") or ""
        if is_source:
            thermal_objects.add_heatsource(simulation_setup.heatsource(
                power=value, source_layernum=layer, target_layername=target))
        else:
            thermal_objects.add_consttemp(simulation_setup.constanttemp(
                temp=value, source_layernum=layer, target_layername=target))
    return thermal_objects


def _setting_line(saved_values, key):
    """settings['key'] = value, as a line of Python."""
    value = str(saved_values[key])
    if '\\' in value:
        # make sure we don't run into escape character issue with Windows paths
        value = value.replace('\\', '/')
    if key in _GHZ_VALUES:
        value = str(value) + 'e9'
    if key in _GHZ_LISTS:
        # value is the string representation of a list
        value = "[" + ",".join(str(f) + "e9" for f in ast.literal_eval(value)) + "]"
    if isinstance(saved_values[key], str):
        # value is a string, enclose in quotes (backslashes of Windows paths replaced above)
        return "settings['" + key + "'] = '" + value + "'"
    return "settings['" + key + "'] = " + str(value)


def _header(lines, app_name, title, dirname):
    lines.append(f"# {title} created using " + app_name)
    lines.append("import os, sys, subprocess")
    lines.append("\nfrom gds2palace import *")
    lines.append("\n# get path for this simulation file")
    lines.append("script_path = utilities.get_script_path(__file__)")
    lines.append("# use script filename as model basename")
    lines.append("model_basename = utilities.get_basename(__file__)")
    lines.append("# set and create directory for simulation output")
    if dirname:
        lines.append(f"sim_path = utilities.create_sim_path (script_path,model_basename,dirname='{dirname}')")
    else:
        lines.append("sim_path = utilities.create_sim_path (script_path,model_basename)")


def _start_simulation(lines, solver, run_script):
    """The part of an exported script that starts the solver when start_simulation is set."""
    lines.append(f"\n# run after creating mesh and {solver} ")
    lines.append("if start_simulation:")
    lines.append("  try:")
    lines.append("      os.chdir(sim_path)")
    if run_script:
        lines.append("      if sys.platform.startswith('linux'):")
        lines.append(f"          os.chmod('{run_script}', 0o755)")
    lines.append("      subprocess.run(run_command, shell=True)")
    lines.append("  except:")
    lines.append(f"      print(f'Unable to run {solver.split()[0]} using command ',run_command)\n")


def _read_geometry(lines, layers_from, em):
    lines.append("\n# ================= read stackup and geometries =================")
    lines.append("materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate "
                 "(settings['SubstrateFile'], variable_overrides=settings['variable_overrides'])")
    lines.append("layernumbers = metals_list.getlayernumbers()")
    lines.append(f"layernumbers.extend({layers_from})")
    lines.append("\n# read geometries from GDSII")
    text = ("allpolygons = gds_reader.read_gds(settings['GdsFile'], "
            "\n\tlayernumbers,"
            "\n\tcellname=settings['cellname'], "
            "\n\tpurposelist=settings['purpose'], "
            "\n\tmetals_list=metals_list, \n\tpreprocess=settings['preprocess_gds'], "
            "\n\tmerge_polygon_size=settings['merge_polygon_size'],")
    if em:
        text += ("\n\tgds_boundary_layers=dielectrics_list.get_boundary_layers(),"
                 "\n\tmirror=False, "
                 "\n\toffset_x=0, offset_y=0,"
                 "\n\tlayernumber_offset=0)")
    else:
        text += "\n\tgds_boundary_layers=dielectrics_list.get_boundary_layers())"
    lines.append(text)
    lines.append("\n")


def em_script_text(saved_values, simulation_ports, elmer_mode, for_export=False, app_name="setupEM",
                   fill_factor_solvers=None, palace_linear_solver_settings=None):
    """setupEM's model script, Palace (elmer_mode False) or Elmer EM. for_export: the File > Export form, which also
    starts the solver; without it, the form setupEM runs itself (Create Model). simulation_ports: gds2palace's
    all_simulation_ports. The two capability arguments default to what the installed gds2palace supports."""
    if fill_factor_solvers is None:
        fill_factor_solvers = FILL_FACTOR_CORRECTION_SOLVERS
    if palace_linear_solver_settings is None:
        palace_linear_solver_settings = PALACE_LINEAR_SOLVER_SETTINGS
    palace_mode = not elmer_mode
    lines = []
    _header(lines, app_name, "Model for IHP OpenPDK EM workflow", "elmer_model" if elmer_mode else None)

    lines.append("\n# ========================= workflow settings ==========================")
    if for_export:
        lines.append("# preview model/mesh only, without running solver?")
        lines.append("start_simulation = False")
        lines.append("\n# Command to start simulation")
        if palace_mode:
            lines.append("# run_command = ['start', 'wsl.exe']  # Windows Subsystem for Linux")
            lines.append("run_command = ['./run_sim']         # Linux")
        else:
            lines.append("run_command = ['./run_elmer']     # Linux")

    lines.append("\n# ===================== input files and settings =======================")
    lines.append("settings={}")
    if for_export:
        lines.append(NO_GUI_EXPORT_LINE)

    # keys that must be set AFTER reading stackup and GDSII, not before
    special_keylist = ['simulation_ports', 'materials_list', 'dielectrics_list', 'metals_list',
                       'layernumbers', 'allpolygons']
    # keys not written to the script; fdump_enabled is a GUI-only toggle (Elmer mode), never a gds2palace setting
    ignore_list = ['model_basename', 'sim_path', 'fdump_enabled']
    if palace_mode:
        ignore_list.append('iterative')
    else:
        # Elmer mode shows a plain on/off checkbox (fdump_enabled) instead of Palace's per-frequency fdump list, so a
        # stale Palace-mode list from earlier in the same session must not leak into an Elmer script; the
        # synthesized settings['fdump'] line below is written instead when the checkbox is on.
        ignore_list.append('fdump')
        # conductor_model (filled_metals with an older gds2palace) is Palace-only in the GUI: gds2palace's volume
        # conductors also cover Elmer EM, but that path is untested from setupEM so far
        ignore_list.extend(['conductor_model', 'filled_metals'])
        # an older gds2palace supports the via fill factor correction for Palace only
        if 'elmer' not in fill_factor_solvers:
            ignore_list.append('fill_factor_correction')
    if for_export:
        # these only control gmsh within the GUI
        ignore_list.extend(['preview_only', 'no_preview'])
    # Palace linear solver settings: Palace-only, and only for a gds2palace that applies them (an older one
    # hardcodes them and would silently ignore the keys)
    if elmer_mode or not palace_linear_solver_settings:
        ignore_list.extend(['complex_coarse_solve', 'solver_maxits', 'solver_tol'])
    # AMR settings only matter for Palace with at least one AMR iteration
    if elmer_mode:
        ignore_list.extend(['adaptive_mesh_iterations', 'amr_tol', 'amr_max_dof', 'adaptive_mesh_conformal'])
    elif int(saved_values.get('adaptive_mesh_iterations', 0)) == 0:
        ignore_list.extend(['amr_tol', 'amr_max_dof', 'adaptive_mesh_conformal'])

    # settings grouped by topic; keys not listed here end up in "Other", so a new setting is never silently dropped
    setting_groups = [
        ("Input files", ['GdsFile', 'SubstrateFile', 'variable_overrides', 'cellname', 'purpose',
                         'preprocess_gds', 'merge_polygon_size', 'fill_factor_correction']),
        ("Frequencies", ['fstart', 'fstop', 'fstep', 'fpoint', 'fdump']),
        ("Mesh", ['unit', 'refined_cellsize', 'refined_cellsize_override', 'cells_per_wavelength',
                  'meshsize_max', 'order', 'conductor_model', 'filled_metals']),
        ("Adaptive mesh refinement", ['adaptive_mesh_iterations', 'amr_tol', 'amr_max_dof',
                                      'adaptive_mesh_conformal']),
        ("Simulation boundary", ['boundary', 'margin', 'air_around', 'reference_plane_shift', 'port_wall_extension',
                                 'wall_extension']),
        ("Solver", ['complex_coarse_solve', 'solver_maxits', 'solver_tol', 'iterative', 'ELMER_MPI_THREADS']),
        ("Script control", ['preview_only', 'no_preview']),
    ]
    grouped_keys = {key for _, keys in setting_groups for key in keys}
    setting_groups.append(("Other", [key for key in saved_values if key not in grouped_keys]))
    for group_name, keys in setting_groups:
        keys = [key for key in keys if key in saved_values and key not in special_keylist and key not in ignore_list]
        if keys:
            lines.append(f"\n# ---- {group_name} ----")
            lines.extend(_setting_line(saved_values, key) for key in keys)

    if elmer_mode and bool(saved_values.get('fdump_enabled')):
        # Elmer has no per-frequency SaveStep like Palace: it dumps fields at every solved frequency once any fdump
        # value is set, so reuse an already-solved frequency (no extra solve: util_elmer.write_elmer_frequencies()
        # dedupes the frequency list). Known limitation: this line's right-hand side references "settings", so
        # parse_assignments() skips it on Import, and a re-imported Elmer+fdump script loads unchecked.
        lines.append("\n# 'Enable field dump' checkbox: Elmer dumps fields at every solved")
        lines.append("# frequency once fdump is non-empty, so reuse an already-solved one")
        if 'fstop' in saved_values:
            lines.append("settings['fdump'] = [settings['fstop']]")
        elif saved_values.get('fpoint'):
            lines.append("settings['fdump'] = [settings['fpoint'][0]]")

    lines.append("\n# ===================== port definitions =======================")
    lines.append("simulation_ports = simulation_setup.all_simulation_ports()")
    for port in simulation_ports.ports:
        if "Z" in port.direction.upper():
            layers = f"from_layername='{port.from_layername}', to_layername='{port.to_layername}', "
        else:
            layers = f"target_layername='{port.target_layername}', "
        lines.append(f"simulation_ports.add_port(simulation_setup.simulation_port("
                     f"portnumber={port.portnumber}, voltage={port.voltage}, port_Z0={port.port_Z0}, "
                     f"source_layernum={port.source_layernum}, {layers}direction='{port.direction}'))")

    _read_geometry(lines, "simulation_ports.portlayers", em=True)
    for key in special_keylist:
        lines.append("settings['" + key + "'] = " + key)
    lines.append("settings['sim_path'] = sim_path")
    lines.append("settings['model_basename'] = model_basename")

    lines.append("\n# list of ports that are excited (set voltage to zero in port excitation to skip an excitation!)")
    lines.append("excite_ports = simulation_ports.all_active_excitations()")
    if elmer_mode:
        lines.append("config_name, data_dir = simulation_setup.create_elmer (excite_ports, settings)")
        lines.append("\n# for convenience, write run script to model directory")
        lines.append("utilities.create_elmer_run_script(settings['sim_path'],settings)")
        if for_export:
            _start_simulation(lines, "Elmer model files", "run_elmer")
    else:
        lines.append("config_name, data_dir = simulation_setup.create_palace (excite_ports, settings)")
        lines.append("\n# for convenience, write run script to model directory")
        lines.append("utilities.create_run_script(settings['sim_path'])")
        if for_export:
            _start_simulation(lines, "Palace config.json", "run_sim")
    return "\n".join(lines)


def thermal_script_text(saved_values, thermal_objects, for_export=False, app_name="setupThermal"):
    """setupThermal's model script (Elmer thermal). for_export: the File > Export form, which also starts the solver.
    thermal_objects: gds2palace's all_thermal_objects."""
    lines = []
    _header(lines, app_name, "Thermal model for IHP OpenPDK workflow", "elmer_model")

    lines.append("\n# ========================= workflow settings ==========================")
    if for_export:
        lines.append("# preview model/mesh only, without running solver?")
        lines.append("start_simulation = False")
        lines.append("\n# Command to start simulation")
        lines.append("run_command = ['ElmerSolver']")

    lines.append("\n# ===================== input files and settings =======================")
    lines.append("settings={}")
    if for_export:
        lines.append(NO_GUI_EXPORT_LINE)

    special_keylist = ['thermal_objects', 'materials_list', 'dielectrics_list', 'metals_list',
                       'layernumbers', 'allpolygons']
    ignore_list = ['model_basename', 'sim_path']
    if for_export:
        ignore_list.extend(['preview_only', 'no_preview'])
    lines.extend(_setting_line(saved_values, key) for key in saved_values
                 if key not in special_keylist and key not in ignore_list)
    lines.append("settings['elmer_thermal'] = True # create all metals as volumes, enable Elmer thermal output")

    lines.append("\n# ===================== port definitions =======================")
    lines.append("thermal_objects = simulation_setup.all_thermal_objects()")
    for thermal_object in thermal_objects.objects:
        if isinstance(thermal_object, simulation_setup.heatsource):
            lines.append(f"thermal_objects.add_heatsource(simulation_setup.heatsource("
                         f"power={thermal_object.power}, source_layernum={thermal_object.source_layernum}, "
                         f"target_layername='{thermal_object.target_layername}'))")
        if isinstance(thermal_object, simulation_setup.constanttemp):
            lines.append(f"thermal_objects.add_consttemp(simulation_setup.constanttemp("
                         f"temp={thermal_object.temp}, source_layernum={thermal_object.source_layernum}, "
                         f"target_layername='{thermal_object.target_layername}'))")

    _read_geometry(lines, "thermal_objects.layers", em=False)
    for key in special_keylist:
        lines.append("settings['" + key + "'] = " + key)
    lines.append("settings['sim_path'] = sim_path")
    lines.append("settings['model_basename'] = model_basename")
    lines.append("\n")
    lines.append("config_name, data_dir = simulation_setup.create_elmer_thermal (settings)")
    if for_export:
        _start_simulation(lines, "Elmer model files", None)
    return "\n".join(lines)
