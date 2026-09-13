# setupEM FAQ

This FAQ is written for readers who already know a commercial EM tool such as HFSS, ADS Momentum, Sonnet, or AWR. It focuses on where setupEM and the underlying gds2palace workflow work differently from those tools, not on how to click through the GUI. For the full walkthrough of every tab, see the [setupEM User's Guide](setupEM_userguide.md). For the underlying Python workflow and settings reference, see the [gds2palace workflow user's guide](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/doc/userguide_md_format/gds2palace_workflow_userguide.md).

The short version of the biggest mindset shift: this is not an integrated layout-plus-EM tool with a built-in technology database. It is an open-source 3D FEM solver, AWS Palace or Elmer FEM, driven by a Python script that setupEM generates for you. GDSII geometry and an XML stackup file replace the vendor tech file. Ports are drawn as plain polygons on marker layers instead of picked with a port editor. And there is no wave-port, calibration-kit, or Optimetrics-style feature set, so a few things you may take for granted need a manual workaround here.

## Contents

[Input files](#input-files)  
[Frequencies](#frequencies)  
[Ports](#ports)  
[Simulation settings](#simulation-settings)  
[Mesh](#mesh)  
[Output and results](#output-and-results)  
[Starting from scratch for a new technology](#starting-from-scratch-for-a-new-technology)  
[Other topics](#other-topics)  

## Input files

### Why are there two separate input files instead of one project database?

A commercial layout-plus-EM tool stores geometry, stackup, and ports together in one project. Here, geometry lives in a plain GDSII file and the process stackup lives in a separate XML file. The two are combined only when the Python model script runs. This keeps the geometry reusable across tools, since the same GDSII also feeds gds_viewer, gds_prepare_for_EM, and the openEMS flow.

### Where is the technology file that a PDK usually ships?

IHP SG13G2 Open PDK links to the gds2palace repository where SG13G2 and CMOS5L EM stackups are provided. These were converted from existing EM solver stackup formats, with some manual additions for the gds2palace and gds2openEMS workflows.
There is no automatic import from a Virtuoso tech file, a Calibre deck, or a KLayout `.lyp` file. See "Starting from scratch for a new technology" below for how to build one yourself.  


### Can I reuse the openEMS stackup file for setupEM, or the other way round?

No. The Palace/Elmer FEM stackup and the openEMS FDTD stackup encode different modeling assumptions, for example how the MIM capacitor dielectric is handled and whether conductors get a surface impedance or a solid volume. Using the openEMS stackup here can cause meshing errors, and using this FEM stackup for openEMS can slow simulation down. setupEM shows a warning to this effect when you open Show Stackup.

### What does "Merge via arrays with spacing" do, and why do I need it here but not in HFSS?

Because geometry comes straight from GDSII, a large via array is hundreds of individual via boxes, and meshing each one individually is slow. This setting oversizes and merges nearby via polygons on `Type="via"` layers into one larger via box before meshing, then undersizes back. A commercial tool with native via-array primitives does this automatically at the layout level; here it is an explicit pre-processing step.

### Do I still need "Preprocess GDSII file" for polygons with holes?

Only with an outdated gds2palace install. Mesh generation was redesigned so cutouts and other self-intersecting polygon boundaries are handled natively. If your setupEM shows this checkbox at all, it means your installed gds2palace predates that change.

### What are stackup Variables, and how do they compare to parametric variables in a commercial layout tool?

A `<Variable>` in the XML stackup is a named value, or an expression built from other Variables, that other stackup attributes such as a dielectric thickness can reference. On the Input Files tab, any Variable with a plain value gets an editable override for the current run, without touching the XML. This only reaches stackup attributes, though. It is not a general parametric variable system for port impedance, geometry dimensions, or sweep ranges the way project variables work in HFSS or ADS.

### Can I simulate at different temperatures, the way some tools support a temperature sweep or a TempCo material setting?

There's no dedicated "simulation temperature" field the way a commercial tool's tech file might have one. Instead, you build it from the same Variables/expression mechanism: declare an operating-temperature Variable (e.g. `Temp_Celsius`) and write each temperature-dependent metal's Conductivity as a `"="`-expression of it, following the standard sheet-resistance temperature-coefficient equation `sigma(T) = sigma(T0) / [1 + TC1*(T-T0)]`. A stackup built this way works in setupEM like any other: override `Temp_Celsius` on the Input Files tab to simulate at one temperature. You only need the underlying Python model script directly, from the command line, if you want to loop over several temperatures automatically in one run instead of overriding and re-running by hand each time. See [`EM_temperature_coefficient`](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/tree/main/more_examples/EM_temperature_coefficient) in the gds2palace repository for a complete worked example, including real IHP SG13G2 temperature coefficients, such a temperature loop, and a Palace-simulated S21 comparison at two temperatures.

## Frequencies

### What is the adaptive frequency sweep, and how does it compare to HFSS's interpolating sweep?

It serves the same purpose: Palace simulates a limited number of actual frequency points and interpolates a dense output sweep from them. This reduced order model (ROM) method is similar in spirit to an interpolating sweep. It is enabled by default and needs no target error or convergence setup from you. More output frequency points still generally means more total time, since each interpolation point still needs solved data nearby.

### Can I simulate specific fixed frequencies, like a discrete sweep?

Yes, using the `fpoint` setting, or the "fpoint" field in setupEM's Frequencies tab. These combine with the fstart/fstop/fstep sweep rather than replacing it.

### How do I get field data at one particular frequency for Paraview?

Use `fdump`, or the "fdump" field in setupEM. Palace writes a field dump to disk at each fdump frequency, in addition to computing S-parameters there. This is the closest equivalent to requesting a field solution at a specific frequency in a commercial tool, except the viewer is external Paraview rather than a built-in 3D field plotter.

### Why can't I simulate at 0 Hz, and how is DC handled?

FEM frequency-domain solvers, Palace included, cannot solve at exactly 0 Hz. If you set the start frequency to 0, setupEM simulates two low points instead, 10 MHz and 20 MHz, and extrapolates a DC value in postprocessing. The extrapolated result lands in a separate `_dc` output file. Always check that file before trusting it, since it is an extrapolation, not a simulated data point.

### Does the Elmer flow support the same adaptive sweep?

No. Elmer's EM solver has no equivalent to Palace's adaptive interpolation, so it solves at every frequency point you list. Simulation time for Elmer scales directly with the number of frequency points, so a dense fstep here is much more expensive with Elmer than with Palace.

## Ports

### There's no port editor. How do I actually place a port?

Ports are ordinary GDSII polygons drawn on dedicated marker layers, usually numbered 201 and above, that are not part of the IHP process layer table. Each port needs its own source layer. In-plane ports are drawn as rectangles; via ports are drawn as zero-width lines, a box with zero size in x or y. On the Ports tab, you map each source layer to a port number, direction, and target technology layer, then click Apply to add it to the port list.

### How does this compare to HFSS wave ports or lumped ports?

The workflow only creates lumped ports, comparable to HFSS lumped ports, not wave ports. A lumped port introduces some physical length into the model, which shows up as extra series inductance in results, the same limitation other volumetric mesh solvers have with lumped ports. If port size is not small relative to the device under test, you may need to estimate and remove this parasitic yourself in postprocessing.

### Can I use wave ports for a more accurately calibrated reference plane?

No. Palace itself supports wave ports, but this workflow does not implement them. Lumped ports only.

### Why does a port have a "voltage" setting, and why would I set it to zero?

Palace does not use port voltage as a physical quantity. This workflow repurposes it to mark whether a port gets excited at all during simulation. Setting a port's voltage to zero skips exciting it, which speeds up the run, but the corresponding rows and columns in the output S-parameter file are then padded with zeros instead of holding real data.

### Can I solve only selected excitations to save time, the way some tools let you skip unused ports?

Yes, that is exactly what zero-voltage ports do for Palace: only ports with non-zero voltage are excited, one at a time, and you get valid results for the paths you actually excited. To get a full S-matrix, every port needs non-zero voltage.

### Can I define a balanced or differential port directly?

Not as a built-in feature. Composite ports, meaning multiple EM ports grouped into one output port, are not supported by either the Palace or the Elmer flow. Every excited port becomes its own row and column in the n-port Touchstone file, and any single-ended-to-mixed-mode conversion has to happen afterward in your own postprocessing, for example with scikit-rf.

### Can I set port impedance to something other than 50 ohms?

Yes, each port has its own reference impedance setting, independent of the others. This is closer to HFSS's per-port impedance override than to a single fixed system impedance. Note that mixed port impedances can cause issues with the Touchstone S-Parameter file header (only single Z0 value there).

### Does Elmer excite ports the same way as Palace?

No, and this is one of the more important differences. Palace runs one solver pass per active excitation, so zero-voltage ports genuinely save time. Elmer's EM solver instead uses a constraint-modes analysis that solves for every defined port in a single run, regardless of each port's voltage setting. There is no equivalent to Palace's "excite only port 1" trick for Elmer: you get the full n-port matrix, or nothing.

### Are results de-embedded to the port reference plane, like a calibration standard would do?

Not through a calibration standard such as TRL or SOLT, since there are no calibration structures in this flow. Instead, the postprocessing script estimates the parasitic series inductance the lumped port geometry introduces, from a simple flat-ribbon calculation, and cascades a negative version of it onto the result. This produces a separate `_deembedded` Touchstone file alongside the raw one. Treat it as a geometric correction, not a calibrated de-embedding.

## Simulation settings

### Where is the equivalent of a commercial tool's project-wide analysis setup?

There isn't a single dialog. SetupEM defauls are set using File > Preferences. Simulation model configuration can be saved to file, and there is an additional default configuration that can be saved and loaded.

### What do "margin" and "air_around" actually control, and how does that map to an airbox setting?

`margin` oversizes the dielectric layers, substrate, oxide, and so on, from the bounding box of the drawn geometry. `air_around` is a separate spacing for the air layer that surrounds the whole dielectric stack, and can differ per side of the model, or be set to zero on one side, for example to let a backside ground plane sit flush with the outer boundary instead of wasting an air layer below it. A commercial tool usually bundles both ideas into one airbox padding value; here they are two independent settings, and both must be non-zero somewhere or meshing fails.

### What boundary condition types are available, and how do they compare to a radiation boundary?

Each of the six outer box faces, xmin, xmax, ymin, ymax, zmin, zmax, can independently be set to ABC (absorbing boundary), PEC and PMC. ABC plays the role of HFSS's radiation boundary. PML is not implemented yet in the AWS Palace solver. PEC and PMC let you terminate a face as an ideal conductor or ideal magnetic wall, for example to represent a backside ground plane directly as the boundary.

### Is there a symmetry-plane feature to halve simulation time, like HFSS's symmetry boundary?

Not as an automatic feature. You can get the same speedup by drawing only half the layout in GDSII yourself and setting the cut face's boundary to PEC or PMC, matching the symmetry of the structure and excitation. There is no tool that cuts an existing full layout in half for you.

### Can I run Elmer instead of Palace, and when would that make sense?

Yes, this is implemented as an early beta version.  `Simulator > Elmer FEM` in setupEM switches the target solver, and generates the same kind of GDSII-plus-stackup-plus-settings model for Elmer's `VectorHelmholtz` solver instead of Palace. Elmer solves every frequency point directly rather than interpolating, has no zero-voltage excitation shortcut, does not yet support sheet-resistor layers or the side-wall thickness correction Palace uses for low-frequency conductor loss, and does not carry over the field-dump options in quite the same form. Palace is the more complete and generally faster flow for large sweeps; Elmer is useful as a second, independently implemented solver to cross-check results.

### Is there a built-in optimizer or parametric geometry sweep, like Optimetrics?

No. Stackup Variable overrides let you sweep stackup parameters such as a dielectric thickness from the GUI without editing XML, but there is no built-in sweep over geometric dimensions, port impedance, or mesh settings, and no optimizer. A geometry sweep means creating multiple GDSII variants, or writing a small script around the generated model code yourself.

Typically, the underlying gds2palace Python workflow would be used for such tasks, instead of manual work in setupEM GUI. It is also suited for agentic AI doing autonomous work on layout + EM.

## Mesh

### Why is the mesh so much coarser here than in openEMS or a solid-conductor FDTD/FEM tool?

This FEM workflow models conductors as hollow shells with a surface impedance boundary condition on their side walls, so there is no need to mesh into skin effect. openEMS, by contrast, models solid conductors and needs `refined_cellsize` fine enough to resolve skin depth, typically well under a micron. Here, 2 to 5 microns is a good starting point for most IHP SG13G2 models, and the coarser mesh is not a loss of accuracy, it is a consequence of a different conductor model.

For real numbers on how coarse you can actually go, see the [mesh convergence studies](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/more_examples/mesh_convergence/README.md) in the gds2palace repository, which compare mesh size, order, and adaptive mesh refinement across five real IHP SG13G2 structures.

### What does "refined_cellsize" actually control, since it isn't a lower bound on global mesh size?

It sets the target mesh size along polygon edges specifically. It is not a global floor the way it can behave in the openEMS flow: geometry smaller than this value still gets its own, locally smaller mesh. A per-layer override, `refined_cellsize_override`, lets you refine one layer, for example a narrow coupled-line gap, without changing the global setting.

### What does mesh basis function order mean, since there's no dedicated meshing operations list like HFSS's length-based mesh ops?

Order sets the polynomial degree of the FEM basis functions, comparable to first-order versus higher-order tetrahedra in HFSS. setupEM offers three levels: order 1, "faster, less accurate", order 2, "recommended", the default, and order 3, "slower, most accurate", which models field variation within each cell more richly and needs fewer, larger cells for the same accuracy. Order 3 is Palace-only, since Elmer has no cubic-order solver, and is disabled whenever Simulator > Elmer FEM is selected.

### Should I turn on adaptive mesh refinement, given that HFSS defaults to an adaptive solve?

Usually not, if you are already using order 2 with a roughly 2 micron initial mesh. A fine initial mesh without AMR is typically faster overall than starting coarse and refining, because Palace's default AMR here refines against all frequencies and all port excitations at once, which is expensive per iteration. AMR is more useful as a spot check, or at one or a few target frequencies with the refined mesh saved to disk, than as your everyday default.

### What does `z_thickness_factor` do, and why would I touch it?

It scales the effective thickness used for the surface impedance on a conductor's side walls, separately from the true top and bottom surfaces, to correct for over-estimating the low-frequency conductor cross-section. The default is 1.0. Testing on both an inductor and a microstrip line found real, frequency-dependent differences from this factor, particularly in the medium frequency range near a device's peak Q, so it is worth experimenting with if your low-to-mid-frequency loss or Q doesn't match measurement.

See the [Conductor loss modelling](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/doc/userguide_md_format/gds2palace_workflow_userguide.md#conductor-loss-modelling) chapter of the gds2palace workflow user's guide for the full derivation and test cases behind this correction.

### Do I need to mesh into skin effect anywhere in this flow?

No, for planar conductors. Via layers, `Type="via"`, are meshed as solid volumes rather than surfaces, and get their bulk conductivity assigned directly, with reduced in-plane conductivity to avoid unrealistic side-wall currents after via-array merging.

## Output and results

### Palace writes CSV. Where does the Touchstone file come from?

The `combine_snp` script, which runs `combine_extend_snp.py`, scans the output directory and converts Palace's or Elmer's raw S-parameter output into standard Touchstone `.sNp` files. `run_sim` already calls this automatically as its last step, so a normal run needs no manual conversion step.

### What are the `_dc` and `_deembedded` files next to my main result?

`_dc` is the DC-extrapolated variant, created when the sweep starts at or near 0 Hz, from the two low simulated points. `_deembedded` is the version with the estimated port series inductance removed, described above. The raw, un-modified result has neither suffix. setupEM's Result Viewer has checkboxes to include or exclude each variant when overlaying files.

### How do I view field data, since there's no built-in 3D field plotter?

Request a field dump at specific frequencies with `fdump`, then open the resulting file in ParaView, an external, separately installed tool. This replaces the kind of in-tool field plotting HFSS or CST provide natively.

### Is there a built-in circuit model extraction tool, comparable to a lumped-equivalent fit feature?

setupEM's Model Fit button launches snp2le, an external open-source tool, to extract a lumped-element SPICE/Spectre netlist from your S-parameter result, offering to `pip install` it automatically if missing. For narrowband, device-specific fits, such as untapped inductors, MIM capacitors, or transmission line RLGC models, the separate `lumpedmodel` project has simple calculation-based extractors, and a scikit-rf vector-fit script is available for arbitrary n-port black-box fitting.

### Can I plot and inspect results without leaving setupEM?

Yes, the built-in Result Viewer, reached from Create Model tab's View Results button, plots dB magnitude and phase, and Smith charts for reflection parameters, across one or many Touchstone files at once, grouped by run. This avoids running the standalone `plot_snp.py` script by hand.

## Starting from scratch for a new technology

### We use a different foundry PDK. How do I get started building a stackup?

Build a new XML stackup file. The Stackup Editor, `Tools > Edit Stackup XML...` in setupEM, gives you tabs for Materials, the Dielectric Stack, drawn Layers, optional Derived Layers, Variables, and Thermal Tables, with a live cross-section preview, so this does not require hand-editing raw XML. Layout geometry itself still just needs to be valid GDSII, with layer numbers matching what you map in the stackup.

If a stackup for that technology already exists for ADS Momentum, you don't have to start from a blank stackup: `File > Import` in the Stackup Editor reads a Momentum `*.subst` plus its `materials.matdb`, or a Momentum `*.ltd` file, straight into an editable stackup tree with the same Materials, Dielectric Stack, and Layers you'd otherwise enter by hand. It's a general-purpose importer with no IHP-specific assumptions, so it works for any foundry's Momentum export. Review the imported values afterward, since Momentum's open-ended top boundary and any backside ground plane get mapped onto this format's finite-stack conventions.

### What information do I actually need from the foundry to do this?

The process stackup: every dielectric's thickness and permittivity, every metal and via layer's GDSII layer number, thickness, conductivity, and z-position in the stack, and loss tangent data if available. This is the same information a commercial tool's PDK EM view would already have baked in; here you enter it yourself, once, into the Materials, Dielectric Stack, and Layers tabs.

### What if a simulation layer isn't drawn directly in the GDSII, like an on-chip resistor formed from process layers?

Use a Derived Layer, defined on the Derived Layers tab as a boolean operation, AND, OR, XOR, NOT, or SIZE, on other GDSII or derived layer numbers. This is how, for example, SG13G2 resistor geometry gets recognized from "poly AND implant AND NOT contact," without that geometry existing as its own GDSII layer. See the [`derived_layers_and_resistors`](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/tree/main/more_examples/derived_layers_and_resistors) example for a complete stackup and model script using this.

### Conductor layers stacked directly on top of each other cause meshing errors. Why?

Two `Type="conductor"` layers must always be separated by a `Type="via"` layer in between. Conductors are meshed as hollow surface shells, not solid volumes, so two of them touching directly with no via volume between them leaves the mesher nothing solid to connect through.

### Do I need a separate stackup for the openEMS flow, or does one file cover both?

A separate one. As noted above, the Palace/Elmer stackup and the openEMS stackup optimize for different solver assumptions and are not interchangeable, even for the same physical technology.

### How do I validate a brand-new stackup before trusting it on a real design?

Simulate something with a known answer first, for example a microstrip line sized for 50 ohms on a known layer pair, and check the resulting impedance and loss against hand calculation or measured data if you have it. The example scripts and testcases in the gds2palace repository, such as the microstrip line and balun examples, are a good template for this kind of sanity check on a new stackup.

### Does this only work for RFIC layouts, or can I simulate a PCB the same way?

The workflow itself doesn't care what the geometry represents. One of the shipped examples imports a PCB lowpass filter layout on RO4003 substrate into KLayout, saves it as GDSII, and simulates it with a stackup built for that PCB material. GDSII plus an XML stackup is the only requirement, not an RFIC-specific assumption. Note that default settings in setupEM assume RFIC dimensions, with small layout dimensions, and the "faked DC" frequencies are 10 and 20 MHz.

## Other topics

### Is there a schematic-and-layout co-simulation view, like ADS linking a schematic to Momentum?

No. This is a layout-in, S-parameters-out EM flow only. There is no schematic capture, and no live linking between a circuit simulator and the EM model. Touchstone results are meant to be imported into whatever circuit simulator you already use.

### What does this cost, compared to a commercial FEM or MoM license?

setupEM, gds2palace, AWS Palace, and Elmer FEM are all open source and free to use. There is no license server, node-locking, or per-seat cost. You do still need to build or install the actual Palace or Elmer solver binaries yourself, as described in the installation chapters of the User's Guide.

### Is there a scripting API beyond the setupEM GUI?

Yes, and it comes first: setupEM's Code tab always shows the underlying Python model script, and everything the GUI does is really just building `settings` dictionary entries and calling `gds2palace` functions. You can export that script, `File > Export to *.py model`, and edit or template it directly instead of driving it through the GUI at all.

### Can Palace or Elmer here scale out to a cluster, similar to HFSS HPC?

Palace supports MPI-parallel runs, and the `run_palace` wrapper script setupEM calls already passes a thread or process count. Elmer supports MPI too, via `settings['ELMER_MPI_THREADS']`, requiring an MPI implementation such as OpenMPI, MPICH, or Microsoft MPI on Windows. Scaling to a full compute cluster is possible the same way it is for any Palace or Elmer installation, but setupEM itself only manages the local or WSL run, not cluster job submission.

### I'm on Windows. Does the solver actually run here, or only the model setup?

For Palace, setupEM's Start Simulation runs the model inside WSL automatically on Windows, streaming Palace's console output live into the Log panel, provided your simulation directory is on a local drive, since WSL cannot reach a network drive. Palace itself has no native Windows build; WSL is what makes this work locally instead of needing a separate Linux machine.
