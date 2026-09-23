
# What's New - September 22-23, 2026

Added a **Conductor meshing** option (Palace only) on the Mesh and Boundaries tab: **Surface impedance** (default, as before) or **Solve inside (volume mesh)**, which models conductors as solid bulk-conductivity volumes instead - more accurate at low frequency, at the cost of more RAM/simulation time and reduced accuracy at higher frequencies.

Better display in **Stackup Preview**'s for metals/vias that push outside their base dieelectric.  

Palace's real memory usage is now measured directly from the running solver process(es) instead of parsed from its own log output, which could be inaccurate - this also makes the **"Stop Palace if memory exceeds"** kill switch more reliable.

Added **File > New**, which resets setupEM/setupThermal to the same blank state as a freshly started application - useful for starting a new model without restarting the app.


# What's New - September 16-20, 2026

Fixed a native crash in the 3D field viewer that could occur when dragging the clip-plane position or opacity slider, especially on large/complex fields (mainly reported on Linux). The clip-plane, opacity, and arrow-size sliders now only update the view once you release the slider, rather than redrawing on every tick while dragging.

The Result File picker's entries are now labeled so files that share a name across different folders (e.g. per AMR iteration, per excitation) are no longer indistinguishable from each other. Adaptive mesh refinement's per-iteration copies are also hidden from the list by default, with an **Include AMR iterations** checkbox to show them, keeping the picker short for AMR runs.

Fixed an intermittent "Process error: the program could not be started" when clicking Preview, Create Mesh, or Start Simulation after a previous run left a now-deleted or otherwise stale working directory behind.

Fixed setupEM/setupThermal becoming unresponsive when loading a large, densely-arrayed GDS file. **Layout Preview** now warns (with a cancel option) if the selected layout has too many polygons to load smoothly. Separately, just browsing to such a file (or switching to the Ports/Thermal tab) no longer hangs while computing port/thermal-source layer hints.

# What's New - September 14, 2026

Added a built-in **3D field viewer**, an in-app alternative to launching external ParaView, for setupEM (Palace and Elmer-as-EM-solver) and setupThermal (Elmer thermal) field-dump results. It has a single, axis-aligned (X/Y/Z) clip plane with a **Find max.** button that jumps straight to the largest value of the current field along that axis, standard CAD/ParaView-style +/-X/Y/Z view buttons, plus opacity and mesh-edge overlay controls. For a vector array, **Show arrows** overlays direction arrows sized from an **Arrow size** slider, auto-scaled on a log magnitude scale so weak and strong regions both stay visible. When more than one equally-valid result file exists (e.g. Palace's "driven" field dump and its separate "driven_boundary" one), a picker lets you choose between them instead of guessing.

**View fields in Paraview...** and the previous separate **View fields (3D viewer)...** button are now one **View fields (...)...** button, whose label shows which viewer it opens. Choose **Built-in** (new default) or **ParaView** on the new **Preferences > Viewer** tab (renamed from **Create Model** in setupEM, which also still has the Model Fit/status-line toggles there). If ParaView is selected but not found on your system, setupEM/setupThermal fall back to the built-in viewer automatically, with a message in the Log panel explaining why.

Layout Preview now shows a via port's **effective centerline** when its drawn marker has finite size in both x and y: gds2palace's `add_ports()` collapses the shorter axis down to its minimum edge (not the true center, despite what the user guide says) when building the actual simulated port sheet, so this line makes that reduction visible before you run the simulation instead of it only showing up as a surprise in the results.

# What's New - September 8-12, 2026

Added two reserved stackup materials that need no `<Materials>` entry: `PEC` (ideal conductor, on conductor/via/sheet Layers) and `AIR` (built-in default dielectric, overridable).  

Added a **live solver-status line** below the log during a Palace run, showing MPI process count, estimated total memory, current port/frequency progress, and AMR iteration - updates as Palace's own console output streams in, without waiting for the run to finish. It clears when loading a different model/config file or creating a new mesh, instead of showing a previous run's stale data.

Added a **memory limit** for Palace runs (Preferences > Palace, "Stop Palace if memory exceeds", default 100 GB): if the solver's own reported memory usage crosses this, setupEM terminates it automatically and still runs S-parameter postprocessing on whatever results were already computed, instead of losing the whole run to an out-of-memory crash.

The **Result Viewer** can now show S-parameter results from a still-running (or crashed/stopped) multi-iteration AMR run, reading Palace's raw per-iteration output directly instead of waiting for the whole run to finish. 

Added a **Layout Preview** which can be accessed from Input Files tab or Tools menu, including display of port location and direction. Layout layers selected in Stackup Preview will be highlighted in Layout preview.

Added **Tools > Simplify GDS...** (setupEM and setupThermal), which removes floating (unconnected) metal fill and/or fills in small cutouts on the currently loaded GDS file, writing the result to a new GDS file. The metal layers it operates on come entirely from the currently loaded XML stackup. Defaults are configurable on a new Preferences > Simplify GDS tab. A **Compare in Layout Preview** button opens the original and simplified layouts side by side.  

**Layer numbers for port shapes** are now auto-detected when creating port configuration. Layer range is set in the Preferences dialog.

Added a **File > Preferences...** dialog (setupEM and setupThermal) for changing the built-in defaults of fields that were previously hardcoded. 

The **Cellname** dropdown now shows an explicit "(default)" entry instead of a blank one.



# What's New - September 1-6, 2026

The stackup cross-section preview (**Show stackup**, and the Stackup Editor's live preview) is now interactive: click a dielectric, metal, or via to see its name, material, and z-position/thickness in a flyout. In the Stackup Editor, clicking a shape also selects the matching row in the Dielectric Stack/Layers tables, and selecting a row highlights the matching shape in the preview.

The Stackup Editor now closes itself automatically when a different substrate XML is chosen in the main window, if it has no unsaved changes, instead of staying open showing a file that no longer matches what's selected.

setupThermal now has an **Elmer solver settings** group (Mesh tab), matching setupEM's, to choose between the iterative and direct linear solver for the Elmer thermal solve - defaults to direct. Previously this could only be set by hand-editing the generated model script, and the setting was silently dropped even then.

ParaView launching (Palace and Elmer EM field dumps, Elmer thermal results) now prefers a `.pvtu` file over loose `.vtu` pieces when one exists, so a multi-partition (MPI) run opens as one combined dataset instead of disconnected fragments.

The Frequencies tab's field-dump control is now solver-aware: Elmer mode shows a plain **"Enable field dump"** checkbox instead of a frequency list, since Elmer has no per-frequency `SaveStep` like Palace - any `fdump` value there dumps fields at *every* solved frequency (sweep and `fpoint` together), so listing specific frequencies was misleading. Palace mode is unchanged, keeping its per-frequency `fdump` list. This also sidesteps a gds2palace bug (see its own CHANGES.md) where a frequency listed in both the sweep and `fdump` was silently solved twice.

# What's New - September 5, 2026

Added a **View fields in Paraview...** button (Create Model tab), shown once `fdump` is set, to open Palace or Elmer EM field-dump results directly. "View Results..." is renamed to **View S-Parameters...** for clarity.

Fixed Elmer EM simulations failing to start on Windows: the run script never actually launched (silently, with no log output), and MPI-enabled runs now check that Microsoft MPI is installed first, with a clear message and download link if it's missing instead of a cryptic failure.

Fixed importing an existing model file and choosing to reuse its filename: it could silently rename the output to a different file than the one imported. Fixed `fdump`/`fpoint` showing raw Hz values instead of GHz after importing a model file. `fdump` is now usable in Elmer mode too (previously hidden).

# What's New - September 1, 2026

Added a built-in **Result Viewer** (Create Model tab > View Results...) for browsing Touchstone S-parameter results without leaving setupEM: a file tree grouped by run folder (check a whole folder or individual files), dB/phase or Smith/zoomed-Smith charts with a shared legend, and `_dc`/`_deembedded` filter checkboxes. Also runnable standalone via the `resultViewer` script.

Added a **Model Fit...** button (Create Model tab, next to View Results...) that launches [snp2le](https://github.com/iic-jku/snp2le) on the current run's raw S-parameter result to extract a lumped-element netlist - offering to install snp2le via pip automatically if it isn't already present.

**Start Simulation** now checks whether the output directory already holds results from a previous run, and asks whether to delete or keep them (default: delete) before launching the solver, so old and new results don't get mixed together.

Added a "slower, most accurate (N=3)" mesh basis function option, available in Palace mode only since Elmer doesn't support it. Fixed the Frequencies tab silently dropping (or reusing stale) `fstart`/`fstop` when left blank.


# What's New - August 21, 2026

Changes since the version from about 3 months ago, focused on features that matter to end users. For general usage, see the main [README](../README.md).

## New Stackup Editor

A new graphical **Stackup XML Editor** is available from the **Tools > Edit Stackup XML...** menu in both setupEM and setupThermal. Previously, stackup XML files had to be edited by hand in a text editor.

The editor lets you manage all parts of a stackup file:
- **Materials** (dielectrics and metals, including color coding)
- **Dielectric stack** (layer order and thickness)
- **Drawn layers** (the GDSII layers used for geometry)
- **Derived layers**: new boolean/resize operations (AND, OR, NOT, grow/shrink) that combine or modify existing drawn layers to create additional layers, without needing a separate layout preprocessing step

Edits are shown live in a cross-section preview, the same visualization used by "Show stackup" elsewhere in the app. Saving preserves any comments and formatting in the original XML file that the editor doesn't touch.

## Reference-relative stackup positioning

The Stackup Editor's Dielectric Stack and Layers tabs now support an additional way to position a layer: instead of an absolute Zmin/Zmax, a Dielectric or Layer can reference the top or bottom edge of another one, with an offset. This means a stack no longer needs every z-position hand-recomputed whenever a Dielectric's thickness changes - layers positioned this way track the change automatically. The Result columns show the resolved absolute position either way, so it's always visible regardless of which mode a row uses.

Use **Tools > Convert to Reference position format** in the Stackup Editor to convert an existing stackup file (using absolute positions) to this format in place; the physical layer positions stay exactly the same, only how they're expressed in the XML file changes.

Files using this feature require the newer `schemaVersion="3.0"` stackup format. If you save changes to an older-format file that has since been converted, the editor will ask whether to overwrite the original file or save the upgraded version separately, so an old-format file is never silently replaced. You may also see a console/log warning from gds2palace if a stackup file declares a newer schema version than your installed gds2palace version supports.

## Variables tab in the Stackup Editor

The Stackup Editor now has a **Variables** tab for defining named values (plain numbers/text, or `=expression` referencing other variables) that can be reused across the whole stackup file. Type `=` into any numeric-capable cell on the other tabs to get an autocomplete list of declared variables.

<img src="./png/variables1.png" alt="variables" width="750">

This matches the `<Variables>`/`"=expr"` XML format gds2palace's stackup reader supports as of `schemaVersion="3.1"` - see the [XML stackup format doc](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/doc/XML_stackup_format/XML_stackup_format.md) for the underlying format. Existing files and scripts that don't use Variables are unaffected.

## Override stackup Variables from setupEM / setupThermal

Choosing a substrate XML file that declares `<Variables>` now shows an editable grid of them right below the file description, on the Input Files tab of both setupEM and setupThermal. Change a value there - e.g. `total_thickness` or `air_thickness` - to override it in the generated model script, without hand-editing the XML file or the script itself. The grid only lists plain values, not ones computed from other variables, and stays hidden entirely for files that don't declare any Variables.

## setupThermal: a new companion app for thermal simulation

setupEM now installs a second program, **setupThermal**, alongside setupEM. It provides the same kind of guided, tabbed interface as setupEM, but for building thermal simulation models instead of S-parameter models.

- Uses the [Elmer](https://www.elmerfem.org/blog/) FEM solver instead of AWS Palace. Elmer must be installed separately.
- Reuses the same gds2palace stackup workflow as setupEM, so layout and stackup files work the same way.
- Start it the same way as setupEM: with your venv activated, simply type `setupThermal`.

## Thermal Tables tab in the Stackup Editor

The Stackup Editor now has a **Thermal Tables** tab for editing the temperature-dependent thermal conductivity data used by the Elmer thermal flow. It's a master/detail view: the top grid lists every named table (with a live point count), and selecting a table shows its individual Temperature/Value points below. A Material's **Thermal Table** column is now a dropdown listing the tables declared on this tab (it can still hold a `=variable` expression, e.g. to pick between a literature and a measured dataset), instead of a free-text field with no connection to the actual data.

Points don't need to be entered in temperature order - they're sorted automatically when the file is saved, since Elmer reads them as a piecewise-linear lookup curve and needs them in order to interpolate correctly.

## License correction

Corrected a license inconsistency: the repository's LICENSE file said Apache-2.0, while every source file's own header comment already said GPLv3. The code headers were correct - setupEM imports gds2palace's Python API directly in-process, and gds2palace is itself GPLv3, so GPLv3 is the license actually required here. LICENSE, `pyproject.toml`, and the two files that had no header now all agree on GPLv3.

## Faster, more informative Palace runs on Windows

**Start Simulation** on Windows no longer opens a separate WSL terminal window and waits for you to type `./run_sim` yourself. It now runs `./run_sim` directly inside WSL and streams Palace's console output live into the Log panel, exactly like the existing Linux behavior - no terminal window appears at all. This also means **Terminate** now actually stops a running Windows/WSL simulation, and the Log panel accurately reflects when the run has really finished (previously the terminal launcher returned almost immediately, long before Palace itself was done). This still only works for simulation directories on a local drive - WSL cannot reach a network drive.

When a Palace simulation finishes, a **results summary** is now appended to the Log panel automatically: degrees of freedom, mesh element count, simulation time, peak RAM, and the mesh-adaptation error indicators (Norm/Max/Mean), read directly from Palace's own `palace.json` and `error-indicators.csv` output files. For a run using adaptive mesh refinement, this is a table with one row per refinement iteration plus the final converged result, so you can see how DOF and error indicators evolved across iterations at a glance.

