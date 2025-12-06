# Python GUI for gds2palace 

setupEM provides a Python-based graphical user interface (Qt based) to configure the gds2palace workflow. gds2palace enables RFIC FEM simulation workflow based on the Palace FEM solver by AWS.

An overview of the user interface is given below in chapter "Using setupEM"

# Installation
To install the setupEM, activate the venv where you want to install.

Documentation for the gds2palace workflow assumes that you have created a Python venv 
named "palace" in ~/venv/palace and installed the modules there.

If you follow this, you would first activate the venv: 
```
    source ~\venv\palace\bin\activate
```
and then install setupEM and dependencies via PyPI: 
```
    pip install setupEM    
```

To upgrade to the latest version, do 
```
    pip install setupEM --upgrade   
```

## Missing libraries on installation
If you see this error message when trying to run setupEM:

```
qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform plugin. qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.
```

you need to install additional Qt libraries:

```
sudo apt update
sudo apt install libxcb-cursor0 libxcb-xinerama0 libxcb-xkb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xinput0 libxcb-xv0 libxcb-util1 libxkbcommon-x11-0
```

# Dependencies
This module also installs these dependencies:
    gds2palace
    PySide6
    scipy
    requests
    

# Using setupEM
The user interface of setupEM is organized in multiple tabs, which guide you through the model setup and simulation process. Behind the scenes, the setupEM user interface created Python model code for gds2palace, and you can check the resulting code on the "Code" tab.

## Input Files
Here, you configure input files: the GDSII layout file that provides geometry information and the XML file that provides stackup information. 
Some pre-processing of the layout is also defined here: You can also specify a distance (in micron) which is used for **via array merging**, to speed up simulation by replacing many individual vias with one large via box. If your layout includes **polygons with holes**, you need to set the "Preprocess GDSII file" checkbox, otherwise you will get error messages during meshing.

<img src="./doc/png/inputfiles1.png" alt="input files" width="700">

Using the "Show stackup" button, you can also visualize the stackup and see material information. Note that XML files for FEM simulation in Palace 
are different in some details (e.g. MIM) from XML used for the openEMS flow. This is because each stackup is optimized 
for the specific simulation method. Using the openEMS stackup for gds2palace might result in errors durining meshing, 
using the Palace stackup for openEMS might result in slower simulation.

<img src="./doc/png/showstackup1.png" alt="stackup" width="750">

Dielectric materials are color coded, to easily identify different permittivities. For metal layers, sheet resistance and thickness and distance to other layers is displayed.

<img src="./doc/png/showstackup3.png" alt="stackup" width="750">

## Frequencies
Here, you configure the frequency sweep. Palace uses an adaptive frequency sweep, so that dense sweeps with many points 
are created from a limited number of EM simulations. However, in general, more frequency points will take more simulation time.

If you want/need to simulate **specific fixed frequencies**, you can enter them in the "fpoint" field.

If you want/need to simulate specific fixed frequencies and **store the resulting fields to disk for visualization in Paraview**, you can enter them in the "fdump" field instead. All these frequency lists will be combined before simulation.

<img src="./doc/png/frequencies1.png" alt="frequencies" width="750">

The FEM simulation can't do 0 Hz DC simulation, but you can specify start frequency 0 here and workflow will handle this behind the scenes: instead of 0 Hz, tw low frequency points of 10 MHz and 20 MHz will be simulated, and the data will be extrapolated to 0 Hz in postprocessing.

## Ports
As explained in the [gds2palace documentation](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/doc/gds2palace_workflow_userguide.pdf), ports are created by drawing rectangles on special layers to the GDSII file. For in-plane ports these must be rectangles, for via ports it must be lines (box with zero size in x or y direction). In setupEM, you then map the source layer for each of these ports, and define direction and target layer(s). Direction matters for polarity if multiple ports are connected to the same ground.

In the upper part of the tab, you make your settings and then need to apply to the port list below.

<img src="./doc/png/ports1.png" alt="ports" width="700">

Palace does not evaluate the port voltage parameter, but we use it here to specify if ports are "active" during S-parameter simulation. To get the full S-parameters, all ports must be simulated with non-zero voltage, which means that all ports are excited one after another. If you want to simulate only selected excitations, for faster total simulation time, you can set ports to zero voltage. In this case, you will **not** get full S-parameters data and the missing paths are set to 0 in the output file. 

<img src="./doc/png/ports2.png" alt="ports" width="700">

# Mesh and Boundaries

Here, you control the mesh used for FEM simulation, which has an effect on simulation time and accuracy. Finer mesh is more accurate, because it can model the actual fields in more detail, but it takes more time to solve.

Parameter "Mesh refinement at the edges" does what the name says, this is parameter "refined_cellsize" in gds2palace code. This is the mesh size used along the edges of polygons. If the actual geometry is smaller than this value, local mesh size will result from geometry dimensions, so this value does **not** specify a lower limit for global mesh size (as done in the IHP gds2openEMS flow). In many cases, a value of 2 or 5 microns will give great results for IHP SG13G2 simulation models. For physically small layouts, you might go down to 1µm, but small details will be included anyway, no matter what your setting is.

**In the FEM workflow, we model conductors using surface impedance on the side walls, and don't need to mesh into skin effect. This is different from the IHP openEMS workflow (gds2openEMS) where solid conductors are modelled and refined_cellsize is used to (partially) mesh into skin effect! For this gds2palace FEM flow, that is not the case, and we can use much larger mesh cell size.**

<img src="./doc/png/mesh1.png" alt="mesh" width="700">

Parameter "Mesh cell maximum size absolute" works in combination with the cells/wavelength value, the mesh will use the lower of these two dimensions.

Parameter "Mesh basis function" is an expert setting that controls the order of FEM basis function. Use setting "most accurate", which means order=2 for basis functions. Only for a quick & dirty simulation, use "faster/less accurate", if you know what you are doing.

Parameter "Adaptive mesh iterations" does what the name says: Palace offers adaptive mesh refinement (AMR) but if we use mesh basis function order 2 ("most accurate") with mesh refinement of 2 micron or so, the initial mesh is usually fine enough and we don't need AMR. Starting from a coarse mesh plus AMR usually takes more simulation time than going for a finer initial mesh without AMR. If you experience something different, your feedback and example is much appreciated!

For the boundary conditions, absorbing boundary and pefect electric conductor are supported at the present time. You can specify the oversize of the dielectric layers from the metal drawing, and the additional layer of air that srrounds everything. **Both these distances must NOT be zero, otherwise you will get mesh errors!**

# Create model

Here, you sepcify the target directory where your Python model code and simulation results are stored. You also need to specify a model name, the default is the name of the GDSII file but you can change this value.

The buttons are used top down: You can first preview the resulting model geometry, then create the mesh (and inspect in gmsh viewer if you wish). Close the gmsh window after each step. 

<img src="./doc/png/createmodel1.png" alt="create" width="700">

<img src="./doc/png/createmodel2.png" alt="create" width="700">

To start simulation, use the "Run palace" button. If you are on Linux, this will start Palace using script "run_sim". If you are on Windows, this will start the Linux Subsystem for Windows (WSL) and open a command prompt in the simulation directory.

<img src="./doc/png/createmodel3.png" alt="create" width="700">

To start simulation on Linux, it is required that you have configured a script "run_sim" as described in the [gds2palace documentation](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/doc/gds2palace_workflow_userguide.pdf). You can find a template [here](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/tree/main/scripts) in the gds2palace repository.

To start simulation on Windows from the WSL terminal, type 
```
./run_sim
```


## Code 
Behind the scenes, the setupEM user interface created Python model code for gds2palace, and you can check the resulting code on the "Code" tab.

<img src="./doc/png/code1.png" alt="code" width="750">

## Help menu and Version Check

In the Help menu, you can find links to relevant documentation pages. Also, there is an item Help > Version Information that gives information on your installed versions of **gds2palace** and **setupEM** and the latest version that are available online.

<img src="./doc/png/version1.png" alt="version" width="750">


To upgrade gds2palace and its user interface setupEM to the latest version, do 
```
    pip install gds2palace --upgrade   
    pip install setupEM --upgrade   
```

