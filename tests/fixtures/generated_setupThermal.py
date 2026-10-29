# Thermal model for IHP OpenPDK workflow created using setupThermal
import os, sys, subprocess

from gds2palace import *

# get path for this simulation file
script_path = utilities.get_script_path(__file__)
# use script filename as model basename
model_basename = utilities.get_basename(__file__)
# set and create directory for simulation output
sim_path = utilities.create_sim_path (script_path,model_basename,dirname='elmer_model')

# ========================= workflow settings ==========================
# preview model/mesh only, without running solver?
start_simulation = False

# Command to start simulation
run_command = ['ElmerSolver']

# ===================== input files and settings =======================
settings={}
settings['unit'] = 1e-06
settings['purpose'] = [0]
settings['GdsFile'] = 'simplest_with_source.gds'
settings['SubstrateFile'] = 'SG13_interposer_thermal_typicalvalues.xml'
settings['cellname'] = 'HeatSpreader01B_M'
settings['preprocess_gds'] = True
settings['merge_polygon_size'] = 0.5
settings['margin'] = 100.0
settings['refined_cellsize'] = 5.0
settings['meshsize_max'] = 100.0
settings['variable_overrides'] = {}
settings['fill_factor_correction'] = False
settings['iterative'] = False
settings['elmer_thermal'] = True # create all metals as volumes, enable Elmer thermal output

# ===================== port definitions =======================
thermal_objects = simulation_setup.all_thermal_objects()
thermal_objects.add_heatsource(simulation_setup.heatsource(power=0.65, source_layernum=201, target_layername='TFR'))
thermal_objects.add_consttemp(simulation_setup.constanttemp(temp=298.0, source_layernum=202, target_layername='BACKSIDEGND'))

# ================= read stackup and geometries =================
materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate (settings['SubstrateFile'], variable_overrides=settings['variable_overrides'])
layernumbers = metals_list.getlayernumbers()
layernumbers.extend(thermal_objects.layers)

# read geometries from GDSII
allpolygons = gds_reader.read_gds(settings['GdsFile'], 
	layernumbers,
	cellname=settings['cellname'], 
	purposelist=settings['purpose'], 
	metals_list=metals_list, 
	preprocess=settings['preprocess_gds'], 
	merge_polygon_size=settings['merge_polygon_size'],
	gds_boundary_layers=dielectrics_list.get_boundary_layers())


settings['thermal_objects'] = thermal_objects
settings['materials_list'] = materials_list
settings['dielectrics_list'] = dielectrics_list
settings['metals_list'] = metals_list
settings['layernumbers'] = layernumbers
settings['allpolygons'] = allpolygons
settings['sim_path'] = sim_path
settings['model_basename'] = model_basename


config_name, data_dir = simulation_setup.create_elmer_thermal (settings)

# run after creating mesh and Elmer model files 
if start_simulation:
  try:
      os.chdir(sim_path)
      subprocess.run(run_command, shell=True)
  except:
      print(f'Unable to run Elmer using command ',run_command)

