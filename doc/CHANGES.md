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

The Log panel also now uses a monospaced font (Consolas on Windows, Ubuntu Mono/DejaVu Sans Mono on Linux), so solver output and the results table line up in neat columns instead of a proportional font.
