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

"""
field_viewer.py

In-app 3D field result viewer, built on PyVista/pyvistaqt, embedded in one
window - an alternative to launching external ParaView
(setup_common.py's _open_in_paraview(), still available unchanged via the
"View fields in Paraview..."/"View in ParaView" buttons). Reads the same
.pvd/.pvtu/.vtu files those buttons already locate
(palace_results.find_paraview_files() / thermal_results.find_thermal_paraview_file()),
and adds a single axis-aligned clip plane (X/Y/Z + a position slider - not
PyVista's free-orientation drag-widget, a deliberate UX choice) plus a
field/array picker defaulting to a per-solver preset (E-field magnitude for
Palace, temperature for Elmer thermal).

Normally opened from setupEM's/setupThermal's Create Model tab ("View fields
(3D viewer)..." button, see CreateModelTab.open_field_viewer() in
setupEM.py/setupThermal.py) - but also runnable standalone, either directly
(`python field_viewer.py <file_path> [--source palace|elmer_em|elmer_thermal]`)
or via the `fieldViewer` console script installed with this package (see
main() below and pyproject.toml).

Three sources: Palace (setupEM Palace mode), Elmer-as-EM-solver (setupEM
Elmer mode) and Elmer thermal (setupThermal). Palace and Elmer-EM both write
field-dump VTU output in the same um-scale coordinates as the input GDSII
(confirmed against real field dumps from both), but under different point-
data array names (Palace: E_real/E_imag; Elmer EM: "electric field re"/
"electric field im", with spaces - see _E_FIELD_COMPLEX_KEYS). Elmer thermal
is the odd one out: its VTU is written in real SI meters, not um (see
_POSITION_SCALE_TO_UM), and has no vector field arrays at all, only a scalar
temperature one.
"""

import argparse
import glob
import os
import sys

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor

from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QRadioButton, QButtonGroup, QCheckBox,
    QSlider, QComboBox, QLineEdit, QStyleFactory,
)
from PySide6.QtCore import Qt, QThread, Signal

# __package__ is None/"" when this file is run directly rather than imported as part
# of the setupEM package, so relative import fails - same dual-mode pattern used
# throughout setupEM.py/setup_common.py/result_viewer.py for sibling imports.
if __package__ in (None, ""):
    from palace_results import find_paraview_files
    from thermal_results import find_thermal_paraview_file
else:
    from .palace_results import find_paraview_files
    from .thermal_results import find_thermal_paraview_file


# Axis name -> unit normal vector, for both the clip plane and the slider's bounds lookup.
_AXIS_NORMAL = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
_AXIS_BOUNDS_INDEX = {"X": (0, 1), "Y": (2, 3), "Z": (4, 5)}  # into mesh.bounds
_AXIS_POINT_INDEX = {"X": 0, "Y": 1, "Z": 2}  # into a mesh.points row

# "Up" vector for the axis-view buttons (_set_view()) - X/Y views use +Z as up
# (the natural choice when Z is still on-screen), Z views use +Y as up instead
# (since +Z can't be its own up vector when looking straight down/up the Z axis)
# - the same convention CAD tools/ParaView use for their standard axis views.
_VIEW_UP = {"X": (0.0, 0.0, 1.0), "Y": (0.0, 0.0, 1.0), "Z": (0.0, 1.0, 0.0)}

# Slider is an integer widget - this many steps across the mesh's extent on the
# selected axis gives smooth-feeling dragging without needing float-valued Qt
# sliders. High resolution matters beyond just "smooth dragging": _move_slider_
# to_max() sets the slider to the nearest step to a computed position, and that
# quantization is visible - e.g. a small Elmer thermal domain (~2.3 mm) only got
# ~2.3 um per step at 1000 steps, enough to visibly miss the actual hotspot by
# up to half a step. A million steps keeps that error under a nanometer even
# for a millimeter-scale domain; doesn't affect mouse-drag feel either way,
# since Qt interpolates a slider continuously with the pointer regardless of
# its integer step count.
_SLIDER_STEPS = 1_000_000

# Sources this viewer knows a tailored default array/colormap for. Anything else
# (not currently reachable from the app, but kept open for standalone use) falls
# back to the generic "first available array" behavior in _pick_default_array().
_PALACE = "palace"
_ELMER_EM = "elmer_em"
_ELMER_THERMAL = "elmer_thermal"

# Multiplier to convert a mesh's native point coordinates to um, for DISPLAY only
# (the clip plane's own math stays in native coordinates - only the position
# label shown to the user is rescaled). The two solvers' field-dump VTUs are NOT
# in the same native unit:
#  - Palace: config.json's Model.L0 (e.g. 1e-06) is the factor Palace itself
#    multiplies its raw mesh coordinates by to get real SI meters for the
#    physics - i.e. the raw exported coordinates are already in um (confirmed:
#    a real Palace field dump's bounds, e.g. x in [-134, 50], are exactly
#    chip-scale um numbers, not 134-meter ones), so no rescale is needed.
#  - Elmer-as-EM-solver: also um already (confirmed against a real field dump:
#    x in [-333, 543], same chip-scale magnitude as Palace's) - unlike Elmer
#    thermal below, despite both being "Elmer" output.
#  - Elmer thermal: writes real SI meters (same convention thermal_results.py's
#    own _METERS_TO_UM constant already documents and corrects for elsewhere
#    in this package), so this needs the 1e6 m->um factor.
_POSITION_SCALE_TO_UM = {_PALACE: 1.0, _ELMER_EM: 1.0, _ELMER_THERMAL: 1e6}

# Point-data array names for the complex E-field, per source - not a shared
# convention between Palace and Elmer-as-EM-solver despite both being a
# frequency-domain phasor E-field: Palace names them "E_real"/"E_imag",
# Elmer's field-dump instead uses "electric field re"/"electric field im"
# (with spaces - confirmed against a real Elmer-EM field dump). Elmer thermal
# has no vector field arrays at all, so it's absent from this dict.
_E_FIELD_COMPLEX_KEYS = {
    _PALACE: ("E_real", "E_imag"),
    _ELMER_EM: ("electric field re", "electric field im"),
}

# Vector-arrow (glyph) overlay auto-sizing: the largest arrow is scaled to span
# a percentage of the mesh's own bounding-box diagonal (the "Arrow size"
# slider, in percent - see _add_vector_glyphs()), regardless of the selected
# array's physical units/magnitude - E-field (V/m) and B-field (T) values
# differ by many orders of magnitude, so a fixed/manual arrow length would be
# either invisible or overwhelming depending on which array is selected; the
# bounding-box-relative scaling keeps arrows a sensible, consistent on-screen
# size no matter which vector field or domain scale is loaded, and the slider
# then lets the user scale that up or down to taste.
_VECTOR_ARROW_TARGET_FRACTION_PERCENT_DEFAULT = 8
# QSlider is integer-only, so a 0.5% step is represented as an integer count
# of half-percent units internally (arrow_size_slider's range/value are in
# these units) - see _on_arrow_size_changed()/_add_vector_glyphs() for the
# conversion back to a plain percentage.
_ARROW_SIZE_STEP_PERCENT = 0.5
# Shortest arrow (smallest-magnitude point actually glyphed) is still drawn at
# this fraction of the longest arrow's length, rather than shrinking toward
# zero - see _add_vector_glyphs() for why a raw linear magnitude->length
# mapping doesn't work here (confirmed empirically: real Palace E-field data
# spans ~8 orders of magnitude, same dynamic range that already forces
# _pick_default_array() to use a log color scale - a linear-scaled arrow
# length made every arrow but the single hottest point invisibly short).
_VECTOR_ARROW_MIN_LENGTH_RATIO = 0.15
# Decimation tolerance (fraction of bounding box length) passed to
# pv.DataSet.glyph() as a multiple of the current arrow size, not a fixed
# value - without any decimation, a dense field-dump mesh (tens/hundreds of
# thousands of points) would get one arrow per point, unreadable and slow to
# render. Tying it to arrow size (rather than a constant) means "Arrow size"
# also controls arrow count: smaller arrows need less spacing to stay
# readable, so shrinking them packs more in; bigger arrows need more room to
# avoid overlapping, so enlarging them thins them out. At the default 8%
# arrow size this reproduces the original fixed 0.02 tolerance exactly.
_VECTOR_ARROW_DECIMATION_RATIO = 0.25


def _load_full_mesh(file_path):
    """Read file_path (.pvd/.pvtu/.vtu) into one pv.UnstructuredGrid/PolyData.

    pv.read() on a .pvtu/.vtu returns the dataset directly. On a .pvd (Palace's
    field-dump time/cycle collection), it returns a pv.MultiBlock instead - one
    block per cycle it decided to expose (confirmed empirically: Palace's own
    .pvd only ever carries the most recent solved cycle, so this is a 1-block
    MultiBlock in practice, not a true spatial multi-block split). Take the last
    block (most recent cycle) rather than combining blocks, since different
    cycles are different solve states, not spatial partitions - combining them
    would be physically meaningless.
    """
    data = pv.read(file_path)
    if isinstance(data, pv.MultiBlock):
        for block in reversed(data):
            if block is not None:
                return block
        raise ValueError(f"No readable block found in {file_path}")
    return data


def _attach_complex_e_magnitude(mesh, source):
    """Compute and attach the complex E-field magnitude as point_data['E_magnitude'],
    for a source with a driven-frequency (phasor, not time-domain) E-field - see
    _E_FIELD_COMPLEX_KEYS for the real/imag array names per source. Per-component
    magnitude first (sqrt(real^2+imag^2)), then the vector norm across the 3
    components - this is the RMS/peak magnitude of the complex phasor, not just
    |E_real|. No-op if source isn't in _E_FIELD_COMPLEX_KEYS (e.g. Elmer thermal),
    or the expected arrays aren't actually present in this particular file - caller
    falls back to the generic "first available array" picker in that case.
    """
    keys = _E_FIELD_COMPLEX_KEYS.get(source)
    if keys is None:
        return
    real_key, imag_key = keys
    if real_key not in mesh.point_data or imag_key not in mesh.point_data:
        return
    per_component = np.sqrt(mesh[real_key] ** 2 + mesh[imag_key] ** 2)
    mesh["E_magnitude"] = np.linalg.norm(per_component, axis=1)


def _exact_clip_by_axis(mesh, axis, position, sign):
    """Exact geometric clip on an axis-aligned plane (pv.DataSet.clip()) - cuts
    every cell straddling the plane and interpolates new points to build a
    perfectly flat cut face. Precise, but expensive on a large mesh: ~22s on a
    real 5.3M-point/530K-cell Palace field dump, long enough to freeze the Qt
    event loop and trigger the OS's "not responding" warning (reported on
    Ubuntu) on every slider move or axis switch with clipping enabled -
    that's why FieldViewerWindow always runs this via _ClipWorker on a
    background thread rather than calling it directly from _redraw().

    A cheaper point-mask + extract_points() alternative (keep/drop whole
    boundary cells instead of cutting them, ~70x faster on the same dataset)
    was tried and rejected - it produced a visibly jagged/faceted cut face on
    this mesh's coarser regions instead of a clean flat one, which matters
    more here than raw speed for an inspection tool actually being looked at.
    """
    base_normal = _AXIS_NORMAL[axis]
    origin = tuple(position if i == list(base_normal).index(1.0) else 0.0 for i in range(3))
    normal = tuple(n * sign for n in base_normal)
    return mesh.clip(normal=normal, origin=origin)


class _ClipWorker(QThread):
    """Runs _exact_clip_by_axis() on a background thread - see that function's
    docstring for why this can't just run inline in _redraw(). Emits exactly
    one of succeeded/failed, tagged with the (axis, position, sign) it was
    computed for, so a result that's no longer relevant (the user has since
    moved to a different axis/position/sign) can be told apart from one
    that's still current - see FieldViewerWindow._on_clip_succeeded(), which
    compares this against the currently-desired key rather than trusting an
    opaque "is this the latest request" counter, so a result stays usable
    even if something unrelated (opacity, color scale, ...) redrew in the
    meantime while this was still computing.
    """
    succeeded = Signal(object, str, float, int)  # (clipped_mesh, axis, position, sign)
    failed = Signal(str, str, float, int)        # (error_message, axis, position, sign)

    def __init__(self, mesh, axis, position, sign):
        super().__init__()
        self._mesh = mesh
        self._axis = axis
        self._position = position
        self._sign = sign

    def run(self):
        try:
            result = _exact_clip_by_axis(self._mesh, self._axis, self._position, self._sign)
        except Exception as exc:
            self.failed.emit(str(exc), self._axis, self._position, self._sign)
            return
        self.succeeded.emit(result, self._axis, self._position, self._sign)


def _array_magnitudes(values):
    """Reduce a point-data array to per-node magnitude: the vector norm across
    components for a multi-component array (matches VTK's own default coloring
    of e.g. a 3-component field), or the values themselves for a scalar one."""
    return np.linalg.norm(values, axis=1) if values.ndim > 1 else values


def _pick_default_array(mesh, source):
    """Return (array_name, colormap, log_scale) for the initial view, per source.
    Falls back to the first available point-data array (any source, including an
    unrecognized one) if the tailored preset array isn't actually present - same
    graceful-degradation spirit as the rest of this viewer's array handling.

    E-field magnitude gets a log color scale: near-field magnitude routinely spans
    many orders of magnitude between a source/sharp-edge hotspot and the rest of
    the domain (confirmed on a real Palace field dump: 0.23 to 1.1e7, i.e. ~8
    decades) - a linear scale renders as almost entirely one color, with only the
    single hottest point visibly distinct. Temperature has no such convention
    (and Elmer thermal data doesn't show this kind of extreme spread), so it
    stays linear.
    """
    available = list(mesh.point_data.keys())
    if source in (_PALACE, _ELMER_EM) and "E_magnitude" in available:
        return "E_magnitude", "turbo", True
    if source == _ELMER_THERMAL:
        temp_key = next((k for k in available if "temp" in k.lower()), None)
        if temp_key is not None:
            return temp_key, "coolwarm", False
    return (available[0], "viridis", False) if available else (None, "viridis", False)


# ------------------------------------------------------------------
# Field Viewer window
# ------------------------------------------------------------------

class FieldViewerWindow(QDialog):
    """Own top-level window (no Qt parent, WA_DeleteOnClose - same lifecycle as
    ResultViewerWindow), showing one field-result file - picked from file_paths,
    which may hold more than one equally-valid result (e.g. Palace can write both
    a main "driven" field dump and a separate "driven_boundary" one; neither is
    inherently the "right" one to default to, so the user picks) - with a single
    axis-aligned clip plane and a field/array picker."""

    def __init__(self, MainWindow, file_paths, source):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.MainWindow = MainWindow
        self.file_paths = list(file_paths)
        self.file_path = self.file_paths[0]
        self.source = source

        self._full_mesh = None
        self._mesh_actor = None
        self._vector_actor = None
        self._current_axis = "Z"
        self._load_error = None
        # Which side of the clip plane is kept, per axis - +1 (default, matches
        # the original behavior) keeps the negative side; -1 keeps the positive
        # side instead. Updated by _set_view() to match whichever axis-view
        # button was last clicked for that axis, so the exposed cut face always
        # faces the camera: without this, the "-Z" (etc.) view button showed the
        # mesh's untouched exterior surface facing the camera instead of the cut
        # cross-section, since a fixed-direction clip's cut face pointed away
        # from a below-positioned camera (confirmed visually - "-Z" rendered as
        # a flat, featureless surface where "+Z" showed the real cross-section).
        self._clip_sign = {"X": 1, "Y": 1, "Z": 1}
        # Only the first successful render (or the first one after switching to a
        # different result file) auto-fits the camera to the mesh - every other
        # redraw (clip slider/axis, array, opacity, log scale, clim, mesh overlay
        # toggle, ...) keeps whatever pan/zoom/rotation the user currently has.
        # Without this, _redraw()'s remove_actor()-then-add_mesh() sequence
        # leaves the scene briefly actor-less, and PyVista's own "reset camera if
        # this looks like the first mesh" heuristic was firing on every redraw.
        self._camera_needs_reset = True

        # Background clip computation state - see _exact_clip_by_axis()/
        # _ClipWorker's docstrings for why the clip itself always runs off the
        # GUI thread. At most one _ClipWorker runs at a time: a _redraw() call
        # that arrives while one is already running just replaces
        # _pending_clip_request instead of starting a second thread - see
        # _request_clip()/_on_clip_succeeded()/_current_clip_key().
        self._clip_thread = None
        self._pending_clip_request = None
        self._clip_busy_cursor_active = False
        self._active_clip_key = None
        # Cache of the last background-clip result, keyed by the (axis,
        # position, sign) it was computed for. Most redraw triggers (opacity,
        # log scale, clim, mesh overlay, array selection, vector arrows) don't
        # change the clip geometry at all - only axis/position/sign do - so
        # reusing this avoids kicking off another expensive background clip
        # for a purely cosmetic change. Also closes a real crash: on Linux and
        # Windows, dragging the opacity slider (which fires many rapid
        # _redraw() calls) was starting/finishing background clip threads in
        # quick succession, occasionally destroying a _ClipWorker just before
        # Qt considered it fully stopped ("QThread: Destroyed while thread is
        # still running") - this cache means opacity changes no longer touch
        # the clip thread machinery at all once one clip result exists for
        # the current axis/position. See _redraw()/_on_clip_succeeded().
        self._clipped_mesh_cache = None
        self._clipped_mesh_cache_key = None

        self._build_ui()
        self._load_mesh()
        # Same generic starting view for every source: full mesh, no clip - the
        # "Find max." button (see _move_slider_to_max()) is one click away for
        # jumping straight to the hotspot along whichever axis, so there's no
        # need to preset/guess that at open time, for Elmer thermal or anything
        # else.
        self._on_axis_changed()  # sets slider range for the default axis, then redraws

    def closeEvent(self, event):
        # A running _ClipWorker must not outlive this window - deleting a
        # running QThread is undefined behavior in Qt. Disconnect first so its
        # result (arriving after this window is gone) doesn't try to touch
        # already-torn-down widgets, then wait for it to actually finish -
        # bounded (a clip does eventually complete) but can briefly delay
        # closing if the window is closed mid-computation on a large mesh;
        # still far better than a crash.
        if self._clip_thread is not None:
            self._clip_thread.succeeded.disconnect(self._on_clip_succeeded)
            self._clip_thread.failed.disconnect(self._on_clip_failed)
            self._clip_thread.wait()
        # Disconnecting succeeded/failed above means _on_clip_succeeded()/
        # _on_clip_failed() won't run to do this themselves.
        self._clear_busy_cursor()
        # VTK render windows need explicit teardown, not just Qt's normal widget
        # cleanup - the PyVista-specific analog of ResultViewerWindow stopping its
        # live-preview timer in closeEvent().
        self.plotter.close()
        super().closeEvent(event)

    # ---------- UI construction ----------

    def _build_ui(self):
        self.setWindowTitle("Field Viewer")
        self.resize(1300, 750)
        main_layout = QVBoxLayout(self)

        controls_layout = QHBoxLayout()

        # Only shown when there's more than one candidate result file (e.g. Palace's
        # main "driven" field dump vs. its separate "driven_boundary" one - both
        # equally valid, neither an inherently better default) - see __init__.
        if len(self.file_paths) > 1:
            file_group = QGroupBox("Result File")
            file_layout = QVBoxLayout()
            self.file_combo = QComboBox()
            self.file_combo.addItems([self._file_label(p) for p in self.file_paths])
            self.file_combo.currentIndexChanged.connect(self._on_file_changed)
            file_layout.addWidget(self.file_combo)
            file_layout.addStretch()
            file_group.setLayout(file_layout)
            controls_layout.addWidget(file_group, 1)
        else:
            self.file_combo = None

        # Clip Plane: purely "where/whether to cut" - rendering options that
        # apply regardless of clipping (opacity, mesh overlay) live in their
        # own Display group instead, rather than being bundled in here just
        # because they were added around the same time.
        clip_group = QGroupBox("Clip Plane")
        clip_layout = QVBoxLayout()
        axis_layout = QHBoxLayout()
        self.axis_radio_x = QRadioButton("X")
        self.axis_radio_y = QRadioButton("Y")
        self.axis_radio_z = QRadioButton("Z")
        self.axis_radio_z.setChecked(True)
        self.axis_button_group = QButtonGroup(self)
        for rb in (self.axis_radio_x, self.axis_radio_y, self.axis_radio_z):
            self.axis_button_group.addButton(rb)
            axis_layout.addWidget(rb)
            rb.toggled.connect(self._on_axis_radio_toggled)
        axis_layout.addStretch()
        self.find_max_btn = QPushButton("Find max.")
        self.find_max_btn.setToolTip(
            "Move the clip plane, along the currently selected axis, to the "
            "position of the largest value of the currently selected field."
        )
        # Compact height (matches the row's checkbox/radio height) rather than
        # the taller Qt default push button height - see result_viewer.py's
        # add_external_btn for the same convention.
        self.find_max_btn.setFixedHeight(self.axis_radio_z.sizeHint().height())
        self.find_max_btn.setAutoDefault(False)
        self.find_max_btn.setDefault(False)
        self.find_max_btn.clicked.connect(self._move_slider_to_max)
        axis_layout.addWidget(self.find_max_btn)
        clip_layout.addLayout(axis_layout)

        self.clip_enabled_cb = QCheckBox("Clip enabled")
        self.clip_enabled_cb.setChecked(False)  # start showing the full, unclipped mesh
        self.clip_enabled_cb.toggled.connect(self._on_redraw_needed)
        clip_layout.addWidget(self.clip_enabled_cb)

        self.clip_position_label = QLabel("Position: -")
        clip_layout.addWidget(self.clip_position_label)
        self.clip_slider = QSlider(Qt.Horizontal)
        self.clip_slider.setRange(0, _SLIDER_STEPS)
        self.clip_slider.setValue(_SLIDER_STEPS // 2)
        self.clip_slider.valueChanged.connect(self._on_redraw_needed)
        clip_layout.addWidget(self.clip_slider)

        clip_layout.addStretch()
        clip_group.setLayout(clip_layout)
        controls_layout.addWidget(clip_group, 1)

        # Display: general rendering options, independent of clipping - opacity
        # complements clipping rather than duplicating it (clipping cuts away
        # geometry to reveal a cross-section, translucency instead lets you see
        # a hotspot/feature through the surrounding material without losing the
        # outer shape as context, e.g. a thermal hotspot glowing through the
        # substrate above it). Same slider+label pattern as layout_preview.py's
        # opacity control.
        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout()
        self.opacity_label = QLabel("Opacity: 100%")
        display_layout.addWidget(self.opacity_label)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        display_layout.addWidget(self.opacity_slider)
        self.show_edges_cb = QCheckBox("Overlay mesh")
        self.show_edges_cb.setChecked(False)
        self.show_edges_cb.toggled.connect(self._on_redraw_needed)
        display_layout.addWidget(self.show_edges_cb)
        display_layout.addStretch()
        display_group.setLayout(display_layout)
        controls_layout.addWidget(display_group, 1)

        field_group = QGroupBox("Field")
        field_layout = QVBoxLayout()
        self.array_combo = QComboBox()
        self.array_combo.currentTextChanged.connect(self._on_array_changed)
        field_layout.addWidget(self.array_combo)
        self.log_scale_cb = QCheckBox("Log color scale")
        self.log_scale_cb.toggled.connect(self._on_redraw_needed)
        field_layout.addWidget(self.log_scale_cb)

        # Color range (Min/Max/Reset) directly below the array/log-scale
        # controls it applies to - vector-arrow controls (a separate concern)
        # follow below, rather than interleaving the two.
        clim_layout = QHBoxLayout()
        clim_layout.addWidget(QLabel("Min:"))
        self.clim_min_edit = QLineEdit()
        self.clim_min_edit.editingFinished.connect(self._on_redraw_needed)
        clim_layout.addWidget(self.clim_min_edit)
        clim_layout.addWidget(QLabel("Max:"))
        self.clim_max_edit = QLineEdit()
        self.clim_max_edit.editingFinished.connect(self._on_redraw_needed)
        clim_layout.addWidget(self.clim_max_edit)
        field_layout.addLayout(clim_layout)
        self.clim_reset_btn = QPushButton("Reset range to data")
        # Without this, Qt treats this as the dialog's default button (the only
        # QPushButton in the window) and fires it on Enter from *any* focused
        # widget - including clim_min_edit/clim_max_edit, so confirming a typed
        # value with Enter was immediately undoing it via an unwanted reset.
        self.clim_reset_btn.setAutoDefault(False)
        self.clim_reset_btn.setDefault(False)
        self.clim_reset_btn.clicked.connect(self._on_clim_reset_clicked)
        field_layout.addWidget(self.clim_reset_btn)

        # Only meaningful (and enabled) when the selected Field array is itself
        # a vector (e.g. E_real/E_imag/B_real/B_imag/S) rather than a scalar
        # (e.g. E_magnitude/U_e/temperature) - see _update_vector_checkbox_state().
        self.show_vectors_cb = QCheckBox("Show arrows")
        self.show_vectors_cb.setEnabled(False)
        self.show_vectors_cb.toggled.connect(self._on_redraw_needed)
        field_layout.addWidget(self.show_vectors_cb)

        self.arrow_size_label = QLabel(f"Arrow size: {_VECTOR_ARROW_TARGET_FRACTION_PERCENT_DEFAULT}%")
        self.arrow_size_label.setEnabled(False)
        field_layout.addWidget(self.arrow_size_label)
        self.arrow_size_slider = QSlider(Qt.Horizontal)
        self.arrow_size_slider.setRange(round(1 / _ARROW_SIZE_STEP_PERCENT), round(10 / _ARROW_SIZE_STEP_PERCENT))
        self.arrow_size_slider.setValue(round(_VECTOR_ARROW_TARGET_FRACTION_PERCENT_DEFAULT / _ARROW_SIZE_STEP_PERCENT))
        self.arrow_size_slider.setEnabled(False)
        self.arrow_size_slider.valueChanged.connect(self._on_arrow_size_changed)
        field_layout.addWidget(self.arrow_size_slider)

        field_layout.addStretch()
        field_group.setLayout(field_layout)
        controls_layout.addWidget(field_group, 1)

        # Standard CAD/ParaView-style axis views - jump the camera to look
        # straight down +/-X/Y/Z, rather than needing to drag-rotate to a
        # specific orientation by hand. Rightmost group in this row, so it
        # reads as the top-right pane of the window.
        view_group = QGroupBox("View")
        view_grid = QGridLayout()
        for col, axis in enumerate(("X", "Y", "Z")):
            for row, sign in enumerate((1, -1)):
                label = f"{'+' if sign > 0 else '-'}{axis}"
                btn = QPushButton(label)
                btn.setToolTip(f"Look along the {axis} axis from the {'positive' if sign > 0 else 'negative'} side")
                btn.setFixedHeight(self.axis_radio_z.sizeHint().height())
                btn.setAutoDefault(False)
                btn.setDefault(False)
                btn.clicked.connect(lambda checked=False, a=axis, s=sign: self._set_view(a, s))
                view_grid.addWidget(btn, row, col)
        view_layout = QVBoxLayout()
        view_layout.addLayout(view_grid)
        view_layout.addStretch()
        view_group.setLayout(view_layout)
        controls_layout.addWidget(view_group, 1)

        main_layout.addLayout(controls_layout)

        # Full-width banner below the control groups (not squeezed into one of
        # their columns) - load errors/clip failures/etc. aren't tied to any one
        # group, and a fixed-width column mostly sitting empty wasted space.
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b00000;")
        main_layout.addWidget(self.warning_label)

        self.plotter = QtInteractor(self)
        main_layout.addWidget(self.plotter, 1)

    # ---------- Result file picker ----------

    @staticmethod
    def _file_label(path):
        """<parent folder>/<filename> - enough to tell e.g. Palace's "driven" and
        "driven_boundary" collections apart at a glance, without the full path."""
        return f"{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}"

    def _on_file_changed(self, index):
        if index < 0 or index >= len(self.file_paths):
            return
        self.file_path = self.file_paths[index]
        self._load_error = None
        self.warning_label.setText("")
        # A different result file can have entirely different geometry/bounds
        # (e.g. Palace's "driven" vs. "driven_boundary") - unlike every other
        # control in this window, this is a good reason to re-fit the camera
        # rather than keep the previous file's pan/zoom/rotation.
        self._camera_needs_reset = True
        self._load_mesh()
        self._on_axis_changed()  # resets the clip slider for the new mesh's bounds, redraws

    # ---------- Mesh loading ----------

    def _load_mesh(self):
        self.setWindowTitle(f"Field Viewer - {self._file_label(self.file_path)}")
        # A cached clip result belongs to the mesh it was computed from - a
        # new/different file needs a fresh clip regardless of whether the
        # axis/position/sign happen to match the old cache key.
        self._clipped_mesh_cache = None
        self._clipped_mesh_cache_key = None
        try:
            self._full_mesh = _load_full_mesh(self.file_path)
        except Exception as exc:
            self._load_error = str(exc)
            self.warning_label.setText(f"Failed to load {self.file_path}:\n{exc}")
            self._full_mesh = None
            return

        _attach_complex_e_magnitude(self._full_mesh, self.source)

        available = list(self._full_mesh.point_data.keys())
        self.array_combo.blockSignals(True)
        self.array_combo.clear()
        self.array_combo.addItems(available)
        self.array_combo.blockSignals(False)

        default_array, default_cmap, default_log_scale = _pick_default_array(self._full_mesh, self.source)
        self._current_cmap = default_cmap
        self.log_scale_cb.blockSignals(True)
        self.log_scale_cb.setChecked(default_log_scale)
        self.log_scale_cb.blockSignals(False)
        if default_array is not None:
            self.array_combo.setCurrentText(default_array)
            # Explicit calls, not just relying on currentTextChanged above: that
            # signal doesn't fire if default_array happens to already be the
            # combo's current text (e.g. only one array available), so this
            # can't be the only place _reset_clim_range()/_update_vector_
            # checkbox_state() get called.
            self._reset_clim_range()
            self._update_vector_checkbox_state()
        elif not available:
            self.warning_label.setText(
                f"No point-data arrays found in {self.file_path} - nothing to color by."
            )

    # ---------- Axis / clip plane ----------

    def _on_axis_radio_toggled(self, checked):
        if not checked:
            return  # QButtonGroup fires toggled(False) for the button losing selection too
        self._on_axis_changed()

    def _current_axis_name(self):
        if self.axis_radio_x.isChecked():
            return "X"
        if self.axis_radio_y.isChecked():
            return "Y"
        return "Z"

    def _on_axis_changed(self):
        self._current_axis = self._current_axis_name()
        # Recenter the slider on the mesh's midpoint for the new axis, then redraw -
        # each axis has its own real-world extent, so the previous axis's slider
        # position has no meaningful equivalent on the new one.
        self.clip_slider.blockSignals(True)
        self.clip_slider.setValue(_SLIDER_STEPS // 2)
        self.clip_slider.blockSignals(False)
        self._redraw()

    def _slider_value_to_position(self):
        """Map the slider's integer [0, _SLIDER_STEPS] range to a real coordinate
        on the current axis, from the loaded mesh's own bounding box."""
        if self._full_mesh is None:
            return 0.0
        lo_idx, hi_idx = _AXIS_BOUNDS_INDEX[self._current_axis]
        lo, hi = self._full_mesh.bounds[lo_idx], self._full_mesh.bounds[hi_idx]
        fraction = self.clip_slider.value() / _SLIDER_STEPS
        return lo + fraction * (hi - lo)

    def _on_redraw_needed(self, _value=None):
        self._redraw()

    def _on_opacity_changed(self, value):
        self.opacity_label.setText(f"Opacity: {value}%")
        self._redraw()

    def _on_arrow_size_changed(self, value):
        percent = value * _ARROW_SIZE_STEP_PERCENT
        self.arrow_size_label.setText(f"Arrow size: {percent:g}%")
        self._redraw()

    # ---------- Axis views ----------

    def _set_view(self, axis, sign):
        """Point the camera straight down (sign=-1) or up (sign=+1) the given
        axis at the mesh's center - e.g. "+X" looks from the positive-X side
        toward the origin. Deliberately re-fits the camera (unlike every other
        control in this window, which preserves pan/zoom/rotation - see
        _camera_needs_reset) since jumping to a named axis view is itself a
        deliberate "look at it this way instead" action, not an incidental
        side effect of changing an unrelated setting.

        Also remembers this as the clip plane's preferred "kept side" for this
        axis (see _clip_sign) and re-clips accordingly if clipping is active on
        the same axis, so the exposed cut face faces the camera you just
        switched to, rather than the clip's cut face pointing away from a
        newly-viewed side and showing the mesh's untouched exterior instead.
        """
        if self._full_mesh is None:
            return
        self._clip_sign[axis] = sign
        center = np.array(self._full_mesh.center)
        direction = np.array(_AXIS_NORMAL[axis]) * sign
        # Arbitrary distance along the view direction - reset_camera() right
        # after corrects it to whatever actually fits the mesh, while keeping
        # this direction/up vector (confirmed: reset_camera() preserves the
        # camera's current viewing direction, it only refits the distance).
        distance = max(self._full_mesh.length, 1.0) * 3.0
        camera_position = tuple(center + direction * distance)
        self.plotter.camera_position = [camera_position, tuple(center), _VIEW_UP[axis]]
        self.plotter.reset_camera()
        self._redraw()  # re-clips using the (possibly just-changed) sign for this axis, then renders

    def _move_slider_to_max(self):
        """Move the clip slider, along the currently selected axis, to the
        position of the largest value of the currently selected field array,
        and enable clipping if it isn't already - so the resulting
        cross-section is immediately visible. Always searches the full
        (unclipped) mesh, not just whatever's currently displayed, so this
        finds the true global max regardless of the current clip state - e.g.
        clicking "Find max." again after clipping doesn't just find the max of
        what's left on the visible side. Used both for "Find max." (any axis,
        any array, on demand) and, from __init__, to preset the Elmer thermal
        view at the hotspot on open (Z axis, at that point). A no-op (leaves
        the view as-is) if the array/data needed isn't there for some reason,
        rather than leaving the view in a half-set state.
        """
        array_name = self.array_combo.currentText()
        if self._full_mesh is None or not array_name or array_name not in self._full_mesh.point_data:
            return
        magnitudes = _array_magnitudes(self._full_mesh[array_name])
        if magnitudes.size == 0:
            return

        point_index = _AXIS_POINT_INDEX[self._current_axis]
        max_position = self._full_mesh.points[int(np.argmax(magnitudes)), point_index]
        lo_idx, hi_idx = _AXIS_BOUNDS_INDEX[self._current_axis]
        lo, hi = self._full_mesh.bounds[lo_idx], self._full_mesh.bounds[hi_idx]
        fraction = (max_position - lo) / (hi - lo) if hi > lo else 0.5
        fraction = min(max(fraction, 0.0), 1.0)

        self.clip_slider.blockSignals(True)
        self.clip_slider.setValue(round(fraction * _SLIDER_STEPS))
        self.clip_slider.blockSignals(False)
        self.clip_enabled_cb.blockSignals(True)
        self.clip_enabled_cb.setChecked(True)
        self.clip_enabled_cb.blockSignals(False)
        self._redraw()

    # ---------- Field/array picker ----------

    def _on_array_changed(self, _text=None):
        self._reset_clim_range()
        self._update_vector_checkbox_state()
        self._redraw()

    def _update_vector_checkbox_state(self):
        """Enable "Show arrows" only when the currently selected Field array is
        a vector (point-data array with more than one component per point) -
        arrows orient/scale from that same array, so there's nothing to draw
        for a scalar one (E_magnitude, U_e, temperature, ...). Left checked
        (just disabled) when switching to a scalar array, so switching back to
        a vector array later doesn't lose the user's choice."""
        array_name = self.array_combo.currentText()
        is_vector = (
            self._full_mesh is not None and bool(array_name)
            and array_name in self._full_mesh.point_data
            and self._full_mesh.point_data[array_name].ndim > 1
        )
        self.show_vectors_cb.setEnabled(is_vector)
        self.show_vectors_cb.setToolTip(
            "Overlay direction arrows for this vector field" if is_vector
            else "Only available when the selected Field is a vector array "
                 "(e.g. E_real, B_real, S)"
        )
        self.arrow_size_label.setEnabled(is_vector)
        self.arrow_size_slider.setEnabled(is_vector)

    def _reset_clim_range(self):
        """(Re-)populate the Min/Max fields from the currently selected array's
        actual data range - always from the full, unclipped mesh (not whatever's
        currently displayed under an active clip), so the range stays a stable
        reference independent of where the clip plane happens to sit, and doesn't
        silently narrow just because the clip cropped out the array's extremes.
        Called on load and whenever the array selection changes (a different
        array has a different natural range); NOT called on every redraw, so
        manually-entered min/max values are left alone across clip/axis changes.
        """
        array_name = self.array_combo.currentText()
        if self._full_mesh is None or not array_name or array_name not in self._full_mesh.point_data:
            self.clim_min_edit.setText("")
            self.clim_max_edit.setText("")
            return
        magnitudes = _array_magnitudes(self._full_mesh[array_name])
        if magnitudes.size == 0:
            self.clim_min_edit.setText("")
            self.clim_max_edit.setText("")
            return
        self.clim_min_edit.setText(f"{magnitudes.min():.6g}")
        self.clim_max_edit.setText(f"{magnitudes.max():.6g}")

    def _on_clim_reset_clicked(self):
        self._reset_clim_range()
        self._redraw()

    def _get_clim(self):
        """(min, max) parsed from the Min/Max fields, or None to let PyVista
        auto-scale from the currently displayed mesh (matches the behavior before
        this control existed). Invalid/empty text, or min >= max, both fall back
        to None rather than raising or blocking the redraw - VTK's own clim
        requires min < max, and a typo here shouldn't break the view."""
        try:
            clim_min = float(self.clim_min_edit.text())
            clim_max = float(self.clim_max_edit.text())
        except ValueError:
            return None
        if clim_min >= clim_max:
            return None
        return (clim_min, clim_max)

    # ---------- Vector arrows ----------

    def _add_vector_glyphs(self, mesh, array_name):
        """Build and add an arrow-glyph actor oriented from mesh's array_name
        vector array, auto-scaled so the longest arrow spans the "Arrow size"
        slider's percentage of the mesh's own bounding-box diagonal -
        regardless of the field's physical units/magnitude or the domain's
        physical size (Palace um-scale vs. Elmer mm-scale), so the slider
        means the same thing (a fraction of what's on screen) no matter which
        vector field or domain is loaded - see arrow_size_slider's creation
        in _build_ui().

        Arrow length is mapped from log10(magnitude), not magnitude directly:
        a linear mapping (length proportional to raw magnitude) leaves only
        the single largest-magnitude point with a visible arrow when the
        field spans many orders of magnitude - confirmed on real Palace
        E-field data (0.07 to 1.1e7, ~8 decades), every other arrow rendered
        at an indistinguishable-from-zero length. The log mapping is then
        rescaled into [_VECTOR_ARROW_MIN_LENGTH_RATIO, 1.0] of the target
        length so even the smallest-magnitude glyphed point stays visible,
        while direction (not length) remains the primary signal for outliers.

        Arrow count follows arrow size too, via the decimation tolerance
        passed to mesh.glyph() - see _VECTOR_ARROW_DECIMATION_RATIO's comment.
        Smaller arrows pack in more densely, larger ones thin out to avoid
        overlapping, rather than count staying fixed while only size changes.

        Returns None (no actor added) if the array is all-zero or glyphing
        fails, rather than raising - same graceful-degradation spirit as the
        rest of this viewer's redraw path.
        """
        vectors = mesh.point_data[array_name]
        magnitudes = np.linalg.norm(vectors, axis=1)
        max_magnitude = magnitudes.max() if magnitudes.size else 0.0
        if max_magnitude <= 0:
            return None
        diagonal = mesh.length or 1.0
        target_fraction = (self.arrow_size_slider.value() * _ARROW_SIZE_STEP_PERCENT) / 100.0
        decimation = target_fraction * _VECTOR_ARROW_DECIMATION_RATIO

        floor = max_magnitude * 1e-6  # avoid log10(0) for exact-zero points
        log_magnitude = np.log10(np.clip(magnitudes, floor, None))
        lo, hi = log_magnitude.min(), log_magnitude.max()
        normalized = (log_magnitude - lo) / (hi - lo) if hi > lo else np.ones_like(log_magnitude)
        lengths = diagonal * target_fraction * (
            _VECTOR_ARROW_MIN_LENGTH_RATIO + (1.0 - _VECTOR_ARROW_MIN_LENGTH_RATIO) * normalized
        )

        scale_key = "_glyph_arrow_length"
        mesh.point_data[scale_key] = lengths
        try:
            glyphs = mesh.glyph(orient=array_name, scale=scale_key, factor=1.0,
                                 tolerance=decimation)
        except Exception as exc:
            self.warning_label.setText(f"Vector arrows failed: {exc}")
            return None
        finally:
            # Internal-only helper array - don't leave it in the mesh's array
            # list (would otherwise show up in the Field dropdown's arrays).
            del mesh.point_data[scale_key]
        return self.plotter.add_mesh(glyphs, color="dimgray", reset_camera=False)

    # ---------- Redraw ----------

    def _redraw(self):
        if self._full_mesh is None:
            return

        position = self._slider_value_to_position()
        position_um = position * _POSITION_SCALE_TO_UM.get(self.source, 1.0)
        self.clip_position_label.setText(f"Position: {position_um:.4g} um ({self._current_axis})")

        if not self.clip_enabled_cb.isChecked():
            # A clip that's still computing in the background (if any) is now
            # moot - nothing to hand its result to once it arrives.
            self._pending_clip_request = None
            self._apply_display_mesh(self._full_mesh)
            return

        # Sign follows the last axis-view button clicked for this axis (see
        # _set_view()/_clip_sign) - same cut location either way, but flips
        # which side is kept so the cut face faces whichever side the camera
        # was last pointed at.
        sign = self._clip_sign.get(self._current_axis, 1)
        cache_key = (self._current_axis, position, sign)
        if cache_key == self._clipped_mesh_cache_key:
            # The clip geometry itself hasn't changed since the last computed
            # result - this redraw is for something else entirely (opacity,
            # log scale, clim, mesh overlay, array selection, vector arrows),
            # so reuse it directly instead of starting another expensive
            # background clip for no geometric reason. See
            # _clipped_mesh_cache's docstring in __init__.
            self._pending_clip_request = None
            self._apply_display_mesh(self._clipped_mesh_cache)
            return

        self._request_clip(*cache_key)

    def _request_clip(self, axis, position, sign):
        """Kick off a background clip for this axis/position/sign, unless one
        is already running - in that case just remember these as the latest
        desired parameters (_pending_clip_request) instead of starting a
        second _ClipWorker. _on_clip_succeeded()/_on_clip_failed() start the
        pending one, if any, right after the current one finishes - so at most
        one clip computation is ever in flight, and rapid slider drags/axis
        switches collapse into "compute the latest state" rather than queuing
        up every intermediate one.
        """
        if self._clip_thread is not None and self._clip_thread.isRunning():
            if (axis, position, sign) == self._active_clip_key:
                # Already computing exactly this geometry - its result will
                # satisfy this request too once it lands, since
                # _on_clip_succeeded() checks against the then-current
                # desired key rather than "was this the most recent request",
                # so there's nothing to gain from queuing a duplicate.
                self._pending_clip_request = None
                return
            self._pending_clip_request = (axis, position, sign)
            return
        self._start_clip_thread(axis, position, sign)

    def _start_clip_thread(self, axis, position, sign):
        self._pending_clip_request = None
        self._active_clip_key = (axis, position, sign)
        # Scoped to this window (not QApplication.setOverrideCursor()) - only
        # this field-viewer window is actually busy; the main setupEM/
        # setupThermal window (and any other open field viewer) stays fully
        # usable and shouldn't look busy too. Set once per coalesced chain of
        # clips (guarded so a mid-chain restart in _maybe_start_pending_clip()
        # doesn't set it again), cleared once the chain truly settles - see
        # _clear_busy_cursor().
        if not self._clip_busy_cursor_active:
            self.setCursor(Qt.WaitCursor)
            self._clip_busy_cursor_active = True
        thread = _ClipWorker(self._full_mesh, axis, position, sign)
        thread.succeeded.connect(self._on_clip_succeeded)
        thread.failed.connect(self._on_clip_failed)
        self._clip_thread = thread
        thread.start()

    def _retire_clip_thread(self):
        """Drop our reference to the just-finished _ClipWorker safely. Qt's
        succeeded/failed signals are emitted from inside run(), as its very
        last statement, via a queued cross-thread connection - so by the time
        this runs on the GUI thread, the background thread has very likely
        already stopped, but Qt's own "is this thread still running" state can
        lag the emit by a hair. Deleting a QThread while Qt still considers it
        running is undefined behavior ("QThread: Destroyed while thread is
        still running") - confirmed causing real crashes on both Linux and
        Windows, triggered by dragging the opacity slider (many rapid
        redraws, each starting/finishing a clip thread in quick succession,
        made the race easy to hit). wait() here is effectively instant in the
        normal case (the thread is already done or a moment from done) and
        guarantees Qt agrees it's stopped before we drop the last reference.
        """
        self._clip_thread.wait()
        self._clip_thread = None

    def _on_clip_succeeded(self, clipped_mesh, axis, position, sign):
        self._retire_clip_thread()
        result_key = (axis, position, sign)
        # Compare against what's CURRENTLY desired (not "was this the most
        # recent request") - if nothing but the geometry key matters, a
        # result stays usable even if unrelated redraws (opacity, color
        # scale, ...) happened while this was still computing.
        if result_key == self._current_clip_key():
            self.warning_label.setText("")
            self._clipped_mesh_cache = clipped_mesh
            self._clipped_mesh_cache_key = result_key
            self._apply_display_mesh(clipped_mesh)
        self._maybe_start_pending_clip()

    def _on_clip_failed(self, message, axis, position, sign):
        self._retire_clip_thread()
        result_key = (axis, position, sign)
        if result_key == self._current_clip_key():
            self.warning_label.setText(f"Clip failed: {message}")
            self._apply_display_mesh(self._full_mesh)
        self._maybe_start_pending_clip()

    def _maybe_start_pending_clip(self):
        if self._pending_clip_request is not None:
            self._start_clip_thread(*self._pending_clip_request)
        else:
            self._clear_busy_cursor()

    def _current_clip_key(self):
        """(axis, position, sign) the clip plane is currently set to, or None
        if clipping is off - the ground truth a background clip result is
        checked against before being applied (see _on_clip_succeeded()),
        recomputed fresh rather than cached, since the whole point is to
        catch cases where the desired state has moved on since the result was
        requested."""
        if not self.clip_enabled_cb.isChecked():
            return None
        return (
            self._current_axis,
            self._slider_value_to_position(),
            self._clip_sign.get(self._current_axis, 1),
        )

    def _clear_busy_cursor(self):
        if self._clip_busy_cursor_active:
            self.unsetCursor()
            self._clip_busy_cursor_active = False

    def _apply_display_mesh(self, display_mesh):
        """Color/render display_mesh (the full mesh, or a background thread's
        clip result) - the part of a redraw that's cheap enough to always run
        directly on the GUI thread (see _redraw()/_ClipWorker for the parts
        that aren't)."""
        array_name = self.array_combo.currentText() or None

        if self._mesh_actor is not None:
            self.plotter.remove_actor(self._mesh_actor, render=False)
            self._mesh_actor = None

        if array_name and array_name in display_mesh.point_data:
            magnitudes = _array_magnitudes(display_mesh[array_name])
            clim = self._get_clim()
            # log_scale needs strictly-positive data (log of <=0 is undefined) - a
            # multi-component array (e.g. E_real) gets VTK-default-magnitude
            # colored, which is >=0, but guard the effective lower bound anyway
            # (the manual clim min if set, else the displayed data's own min)
            # rather than trust the checkbox blindly, avoiding a VTK error/blank
            # render from a manually-entered clim that includes zero/negative.
            lower_bound = clim[0] if clim is not None else (magnitudes.min() if magnitudes.size else 0)
            use_log = bool(self.log_scale_cb.isChecked() and lower_bound > 0)
            opacity = self.opacity_slider.value() / 100.0
            show_edges = self.show_edges_cb.isChecked()
            self._mesh_actor = self.plotter.add_mesh(
                display_mesh, scalars=array_name, cmap=self._current_cmap,
                show_edges=show_edges, log_scale=use_log, clim=clim, opacity=opacity,
                scalar_bar_args={"title": array_name}, reset_camera=False,
            )
        else:
            opacity = self.opacity_slider.value() / 100.0
            show_edges = self.show_edges_cb.isChecked()
            self._mesh_actor = self.plotter.add_mesh(
                display_mesh, color="lightgrey", opacity=opacity, show_edges=show_edges,
                reset_camera=False)

        if self._vector_actor is not None:
            self.plotter.remove_actor(self._vector_actor, render=False)
            self._vector_actor = None
        if (self.show_vectors_cb.isChecked() and self.show_vectors_cb.isEnabled()
                and array_name and array_name in display_mesh.point_data
                and display_mesh.point_data[array_name].ndim > 1):
            self._vector_actor = self._add_vector_glyphs(display_mesh, array_name)

        # add_mesh(..., reset_camera=False) above means PyVista never auto-fits the
        # camera on its own (it otherwise would, since remove_actor() just left the
        # scene momentarily empty) - so do it ourselves, but only once per mesh
        # (first load, or after switching to a different result file), not on every
        # redraw, so the user's pan/zoom/rotation survives toggling clip/opacity/
        # mesh-overlay/array/etc.
        if self._camera_needs_reset:
            self.plotter.reset_camera()
            self._camera_needs_reset = False

        self.plotter.render()


# ------------------------------------------------------------------
# Standalone launch (python field_viewer.py <file_path> [--source ...], or
# the fieldViewer console script - see pyproject.toml)
# ------------------------------------------------------------------

class _StandaloneMainWindow:
    """Minimal stand-in for the real setupEM/setupThermal MainWindow, used only
    when this module is run on its own. FieldViewerWindow only needs a MainWindow
    argument for parity with ResultViewerWindow's constructor shape - it doesn't
    actually read anything off it today."""
    APP_NAME = "Field Viewer"


def main():
    app = QApplication(sys.argv)
    if sys.platform.startswith("win"):
        # matches setupEM.py's/setupThermal.py's/result_viewer.py's main() - without
        # this, Qt's default style on Windows looks visibly different from the full app
        app.setStyle(QStyleFactory.create("Windows"))

    parser = argparse.ArgumentParser(description="Standalone 3D field result viewer")
    parser.add_argument("file_path", nargs="?",
                         help="path to a .pvd/.pvtu/.vtu field-result file. If omitted, "
                              "use --run-path with --source to resolve one automatically.")
    parser.add_argument("--run-path",
                         help="a *_data run directory to search for field-result files "
                              "in, instead of passing file_path directly")
    parser.add_argument("--source", choices=[_PALACE, _ELMER_EM, _ELMER_THERMAL], default=_PALACE,
                         help="which default array/colormap preset to use (default: palace)")
    args = parser.parse_args()

    file_paths = [args.file_path] if args.file_path else []
    if not file_paths and args.run_path:
        if args.source == _PALACE:
            model_basename = os.path.basename(os.path.normpath(args.run_path)).removesuffix("_data")
            candidates = find_paraview_files(args.run_path, model_basename)
        elif args.source == _ELMER_THERMAL:
            candidates = [find_thermal_paraview_file(args.run_path)]
        else:
            # Elmer-as-EM-solver: no dedicated resolver module (setupEM.py's
            # _resolve_elmer_field_files() glob-searches "fields*.pvd/pvtu/vtu"
            # directly instead) - mirror that here for standalone use.
            search_dirs = [os.path.join(args.run_path, "mesh"), args.run_path]
            candidates = []
            for pattern in ("fields*.pvd", "fields*.pvtu", "fields*.vtu"):
                for d in search_dirs:
                    candidates = sorted(glob.glob(os.path.join(d, pattern)))
                    if candidates:
                        break
                if candidates:
                    break
        file_paths = [c for c in candidates if c]

    if not file_paths:
        parser.error("no file_path given, and none could be resolved from --run-path")

    window = FieldViewerWindow(_StandaloneMainWindow(), file_paths, args.source)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
