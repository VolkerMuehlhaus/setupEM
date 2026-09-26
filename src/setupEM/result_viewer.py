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
result_viewer.py

GUI S-parameter result viewer: a PySide6 equivalent of the standalone
plot_snp.py script, embedded in one window instead of popping up separate
matplotlib windows per plot type. Lists Touchstone (.sNp) files found
recursively under a target directory, lets the user check any number of
them to overlay, pick which S-parameters to plot from a dynamically sized
grid, and choose whether reflection (Snn) parameters show phase, a Smith
chart, or a zoomed Smith chart in place of the rectangular phase plot.

Normally opened from setupEM's Create Model tab (View Results button, see
MainWindow.open_result_viewer() in setupEM.py) - but also runnable
standalone, either directly (`python result_viewer.py [target_dir]`) or via
the `resultViewer` console script installed with this package (see main()
below and pyproject.toml).
"""

import argparse
import cmath
import math
import os
import re
import sys
from types import SimpleNamespace

import numpy as np
import skrf as rf

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Circle
from matplotlib.ticker import MultipleLocator

from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QTreeWidget, QTreeWidgetItem, QPushButton,
    QRadioButton, QButtonGroup, QCheckBox, QSizePolicy, QStyleFactory,
    QFileDialog, QMessageBox, QMenu,
)
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtCore import Qt, QTimer, QProcess

# __package__ is None/"" when this file is run directly rather than imported as part
# of the setupEM package, so relative import fails - same dual-mode pattern used
# throughout setupEM.py/setup_common.py for sibling imports.
if __package__ in (None, ""):
    from palace_results import find_output_dir, find_live_iteration_dirs, read_port_s_data
else:
    from .palace_results import find_output_dir, find_live_iteration_dirs, read_port_s_data


# ------------------------------------------------------------------
# Plot data helpers (ported from plot_snp.py)
# ------------------------------------------------------------------

COLORS = ['r', 'b', 'm', 'c', 'g', 'y', 'k', 'w']
LINESTYLES = ['solid', 'dashed', 'dashdot', 'dotted', 'solid', 'dashed', 'dashdot', 'dotted']

# marker size for the fat dot marking a network with a single frequency point
# (no line can be drawn between points, so it would otherwise be invisible)
SINGLE_POINT_MARKERSIZE = 7

# reflection coefficient magnitude shown in the zoomed Smith chart, vs. the full one
ZOOM_GAMMA = 0.5
FULL_GAMMA = 1.0
GRID_COLOR = 'lightgrey'
GRID_LW = 0.8
# constant-resistance/-reactance grid values: denser for the zoomed view, skrf's own
# default labeled grid for the full one - both drawn by the same draw_smith_grid(),
# not skrf's plot_s_smith(), so the full Smith chart works for live-preview data too
# (see result_viewer.py's live-preview handling - the raw-CSV stand-in network has no
# skrf-specific methods, only .s/.frequency.f/.nports).
ZOOM_GRID_VALUES = [0.2, 0.5, 1.0, 1.5, 2.0, 3.0]
FULL_GRID_VALUES = [0.2, 0.5, 1.0, 2.0, 5.0]

TOUCHSTONE_RE = re.compile(r'\.s(\d+)p$', re.IGNORECASE)


def dB(value):
    return 20.0 * np.log10(np.abs(value))


def phase_deg(value):
    return np.angle(value, deg=True)


def Sxx(network, m, n):
    return network.s[:, m-1, n-1]


def draw_smith_grid(ax, gamma, grid_values, draw_boundary):
    """Draw a Smith chart grid (constant-resistance/-reactance circles, plus the
    outer |Gamma|=1 boundary when draw_boundary is set) into ax - used for both the
    full chart (gamma=1, grid_values=FULL_GRID_VALUES, draw_boundary=True - the
    physical edge of the valid reflection-coefficient region) and the zoomed chart
    (gamma=ZOOM_GAMMA, grid_values=ZOOM_GRID_VALUES, draw_boundary=False - gamma
    there is just where the window happens to crop the view, not a real boundary,
    so drawing a circle there would falsely look like one - it'd sit exactly
    inscribed in the axis limits, tangent to all four edges). Plain matplotlib
    circle geometry, not skrf's own smith()/plot_s_smith() grid - this way the full
    chart works for live-preview networks too (see draw_smith()), and this file
    doesn't depend on skrf's internal grid-label placement, which is designed for
    gamma=1 and would fall outside a zoomed view's axis limits.
    """
    ax.axhline(0, color='grey', lw=0.5)
    boundary_patch = None
    if draw_boundary:
        boundary_patch = ax.add_patch(Circle((0, 0), gamma, ec=GRID_COLOR, fc='none', lw=GRID_LW))

    def add_grid_circle(center, radius):
        patch = ax.add_patch(Circle(center, radius, ec=GRID_COLOR, fc='none', lw=GRID_LW))
        if boundary_patch is not None:
            # Full chart only: each constant-r/x circle extends beyond |Gamma|=1 (a
            # circle of radius 1/(1+r) or 1/x centered off-origin isn't contained in
            # the unit circle), so without clipping, the parts outside the physical
            # boundary would show up in the square viewport's corners. Not needed
            # for the zoomed view - gamma there is just where the window happens to
            # crop, not a real boundary, so there's nothing to clip to.
            patch.set_clip_path(boundary_patch)
        return patch

    for r in grid_values:
        center = (r/(1+r), 0)
        radius = 1/(1+r)
        add_grid_circle(center, radius)
        label_pos = center[0] - radius
        if abs(label_pos) < gamma:
            ax.annotate(f"{r:g}", xy=(label_pos, 0), xytext=(label_pos, 0.01),
                        fontsize=8, color='dimgrey', ha='center', va='bottom')

    for sign in (1, -1):
        for x in grid_values:
            xv = sign * x
            center = (1, 1/xv)
            radius = abs(1/xv)
            add_grid_circle(center, radius)
            if radius >= 1:
                # crossing point with the imaginary axis nearest the origin
                y0 = 1/xv - math.copysign(math.sqrt(radius**2 - 1), 1/xv)
                if abs(y0) < gamma:
                    ax.annotate(f"{xv:g}j", xy=(0, y0), xytext=(0.01, y0),
                                fontsize=8, color='dimgrey', ha='left', va='center')

    ax.plot(gamma*np.array([-1.1, 1.1]), gamma*np.array([-1.1, 1.1]), 'w.', markersize=0)


def draw_rectangular(ax, m, n, plotted, mode):
    """Draw dB magnitude (mode="db") or phase (mode="phase") of Smn, one line per
    (network, color, linestyle, label) tuple in plotted, into the given ax. No
    per-axis legend - every subplot shows the same file set, so redraw_plot()
    draws one shared legend for the whole figure instead."""
    func = dB if mode == "db" else phase_deg
    label_prefix = 'dB' if mode == "db" else 'phase'
    for network, color, linestyle, label in plotted:
        data = func(Sxx(network, m, n))
        freq = network.frequency.f / 1e9
        if len(freq) == 1:
            # a single frequency point has no line to draw between points and
            # would otherwise be invisible - mark it with a fat dot instead
            ax.plot(freq, data, color=color, linestyle=linestyle, label=label,
                     marker='o', markersize=SINGLE_POINT_MARKERSIZE)
        else:
            ax.plot(freq, data, color=color, linestyle=linestyle, label=label)
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel(f"{label_prefix} S{m}{n}")
    ax.set_xmargin(0)
    if mode == "phase":
        # phase_deg() is np.angle(..., deg=True), always wrapped to (-180, 180] -
        # fix the axis to that full range with a clean 45-degree grid instead of
        # leaving it to matplotlib's autoscale, which picks an arbitrary spacing
        # (e.g. 50/100) that doesn't divide the natural -180..180 range evenly
        ax.set_ylim(-180, 180)
        ax.yaxis.set_major_locator(MultipleLocator(45))
    ax.grid()


def draw_smith(ax, m, n, plotted, zoomed):
    """Draw a Smith chart (or zoomed Smith chart) of reflection parameter Smm/Snn,
    one trace per (network, color, linestyle, label) tuple in plotted, into ax. Grid
    and trace are both plain matplotlib (draw_smith_grid() + Sxx()), not skrf's
    plot_s_smith() - works the same for a live-preview network (see the module
    docstring / ResultViewerWindow's live-preview handling) as for a real one."""
    gamma = ZOOM_GAMMA if zoomed else FULL_GAMMA
    grid_values = ZOOM_GRID_VALUES if zoomed else FULL_GRID_VALUES
    draw_smith_grid(ax, gamma, grid_values, draw_boundary=not zoomed)
    for network, color, linestyle, label in plotted:
        data = Sxx(network, m, n)
        if len(data) == 1:
            # a single frequency point has no line to draw between points and
            # would otherwise be invisible - mark it with a fat dot instead
            ax.plot(data.real, data.imag, color=color, linestyle=linestyle,
                     label=label, marker='o', markersize=SINGLE_POINT_MARKERSIZE)
        else:
            ax.plot(data.real, data.imag, color=color, linestyle=linestyle, label=label)
    ax.set_xlim(-gamma, gamma)
    ax.set_ylim(-gamma, gamma)
    ax.set_xticks([])
    ax.set_yticks([])

    ax.set_title(f"S{m}{n}")
    ax.set_aspect('equal')
    # no per-axis legend here - every Smith column shows the same file set, so
    # redraw_plot() draws one shared legend for the whole figure instead (a
    # per-axis legend placed outside each small subplot got clipped by the
    # figure edge or the next subplot once more than one column was shown)


def find_touchstone_files(target_dir):
    """Recursively find every Touchstone (.sNp, any port count) file at or below
    target_dir. Returns a sorted list of absolute paths, or [] for any bad input
    (empty/missing directory) - never raises."""
    if not target_dir or not os.path.isdir(target_dir):
        return []
    matches = []
    for root, dirnames, files in os.walk(target_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fn in files:
            if TOUCHSTONE_RE.search(fn):
                matches.append(os.path.join(root, fn))
    return sorted(matches)


# AWS Palace adaptive mesh refinement writes one result snapshot per
# iteration<N>/ subfolder alongside the final, fully-refined result in the
# parent directory itself (see _ITERATION_RE in palace_results.py, reverse
# engineered from real Palace output) - same convention, applied per path
# component so it matches regardless of how deep target_dir's own scan went.
_AMR_ITERATION_DIR_RE = re.compile(r'^iteration\d+$')


def is_amr_iteration_snapshot(path):
    """True if path sits inside an AMR "iterationN" output folder, i.e. it's
    a per-iteration snapshot rather than the final result."""
    parts = os.path.normpath(path).split(os.sep)
    return any(_AMR_ITERATION_DIR_RE.match(part) for part in parts)


def _network_from_port_s_data(port_s_data):
    """Build a minimal, skrf-free stand-in for the network object draw_rectangular()/
    draw_smith() expect (just .s, .frequency.f and .nports - see Sxx()), directly
    from read_port_s_data()'s return value: plain numpy + cmath/math only, no
    skrf.Network / DC-extrapolation / de-embedding - this is raw, not-yet-combined
    Palace output (a live AMR-iteration preview), and those are combine_snp/
    combine_extend_snp features that don't apply here. Returns None if port_s_data
    is None or empty. NOT a real skrf.Network, but draw_smith() (both the full and
    zoomed chart) only ever needs .s/.frequency.f/.nports - see draw_smith_grid()
    - so this plots in every display mode, same as a real Touchstone-loaded one.
    """
    if not port_s_data:
        return None
    freq, S_dB, S_arg, num_ports = port_s_data
    if not freq or num_ports < 1:
        return None
    f_hz = np.array([float(f) for f in freq]) * 1e9
    s = np.zeros((len(freq), num_ports, num_ports), dtype=complex)
    for idx, (dB_row, arg_row) in enumerate(zip(S_dB, S_arg)):
        for key, dB_str in dB_row.items():
            i, j = (int(x) for x in key.split())
            try:
                mag = 10 ** (float(dB_str) / 20.0)
                s[idx, i - 1, j - 1] = cmath.rect(mag, math.radians(float(arg_row[key])))
            except (ValueError, KeyError):
                continue  # unparsable entry - leave as 0, matches _max_delta_s's tolerance
    return SimpleNamespace(s=s, frequency=SimpleNamespace(f=f_hz), nports=num_ports)


def pick_final_result_file(paths):
    """Given a list of touchstone file paths, prefer the final result (any
    path with no "iterationN" component) over AMR per-iteration snapshots;
    break ties (or fall back, if every path is a snapshot) by newest mtime.
    Returns None for an empty list."""
    if not paths:
        return None
    final_candidates = [p for p in paths if not is_amr_iteration_snapshot(p)] or paths
    return max(final_candidates, key=os.path.getmtime)


# ------------------------------------------------------------------
# Result Viewer window
# ------------------------------------------------------------------

class ResultViewerWindow(QDialog):
    """Own top-level window (no Qt parent, WA_DeleteOnClose - same lifecycle as
    StackupEditorWindow in stackupEditor.py) that lists Touchstone files under
    MainWindow.saved_values['sim_path'], plus any externally added via the
    "Add..." button, and plots the ones checked."""

    def __init__(self, MainWindow):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.MainWindow = MainWindow

        self._target_dir = ''
        self._master_files = []          # sorted absolute paths, last scan
        self._external_paths = []        # paths added via "Add...", session-only, kept across rescans
        self._checked_paths = set()      # subset of _master_files currently checked
        self._checked_params = {(1, 1)}  # set of (m, n) S-parameters to plot
        self._network_cache = {}         # path -> (mtime, network-like object | None)
        self._last_n = None              # common port count as of last parameter-grid rebuild
        self.smith_mode = "phase"        # "phase" | "smith" | "zoom"
        self._updating_checks = False    # re-entrancy guard for group<->leaf checkbox propagation

        # Live preview of Palace's raw port-S.csv, one per completed AMR iteration,
        # while a run hasn't produced real Touchstone files yet - see _rescan_files(),
        # _is_palace_run_active(), _network_from_port_s_data().
        self._live_paths = set()         # subset of _master_files sourced from a live port-S.csv
        self._live_timer = QTimer(self)  # polls while a Palace run is active; starts inactive
        self._live_timer.timeout.connect(self._rescan_files)

        self._build_ui()
        self._rescan_files()

    def closeEvent(self, event):
        self._live_timer.stop()
        super().closeEvent(event)

    # ---------- UI construction ----------

    def _build_ui(self):
        self.setWindowTitle("Result Viewer")
        self.resize(1200, 800)
        main_layout = QVBoxLayout(self)

        controls_layout = QHBoxLayout()

        files_group = QGroupBox("Files")
        files_layout = QVBoxLayout()
        filter_layout = QHBoxLayout()
        self.include_all_models_cb = QCheckBox("Include all models in directory")
        self.include_all_models_cb.setChecked(False)  # start restricted to the current model
        self.include_all_models_cb.toggled.connect(self._rescan_files)
        filter_layout.addWidget(self.include_all_models_cb)
        self.include_dc_cb = QCheckBox("Include _dc files")
        self.include_dc_cb.setChecked(False)  # start showing only the raw result file
        self.include_dc_cb.toggled.connect(self._rescan_files)
        filter_layout.addWidget(self.include_dc_cb)
        self.include_deembedded_cb = QCheckBox("Include _deembedded files")
        self.include_deembedded_cb.setChecked(False)  # start showing only the raw result file
        self.include_deembedded_cb.toggled.connect(self._rescan_files)
        filter_layout.addWidget(self.include_deembedded_cb)
        filter_layout.addStretch()
        self.add_external_btn = QPushButton("Add...")
        self.add_external_btn.setToolTip(
            "Add an external Touchstone (.sNp) file, e.g. measured data, for comparison")
        # match the row's checkbox height rather than the taller Qt default push
        # button height, so it sits visually level with the filter checkboxes
        self.add_external_btn.setFixedHeight(self.include_dc_cb.sizeHint().height())
        self.add_external_btn.clicked.connect(self._on_add_external_clicked)
        filter_layout.addWidget(self.add_external_btn)
        files_layout.addLayout(filter_layout)
        self.file_list = QTreeWidget()
        self.file_list.setHeaderHidden(True)
        self.file_list.itemChanged.connect(self._on_file_item_changed)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._on_file_context_menu)
        files_layout.addWidget(self.file_list)
        files_group.setLayout(files_layout)
        controls_layout.addWidget(files_group, 2)

        param_group = QGroupBox("S-Parameters")
        self.param_grid_layout = QGridLayout()
        param_group.setLayout(self.param_grid_layout)
        controls_layout.addWidget(param_group, 1)

        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout()
        self.phase_radio = QRadioButton("dB + Phase")
        self.smith_radio = QRadioButton("Smith chart")
        self.zoom_radio = QRadioButton("Smith chart (zoomed)")
        self.phase_radio.setChecked(True)
        self.display_button_group = QButtonGroup(self)
        for rb in (self.phase_radio, self.smith_radio, self.zoom_radio):
            self.display_button_group.addButton(rb)
            display_layout.addWidget(rb)
            rb.toggled.connect(self._on_mode_changed)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._rescan_files)
        display_layout.addWidget(self.refresh_btn)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b00000;")
        display_layout.addWidget(self.warning_label)
        display_group.setLayout(display_layout)
        controls_layout.addWidget(display_group, 1)

        main_layout.addLayout(controls_layout)

        # Full-width banner for live (in-progress or stopped-early) AMR-iteration
        # preview data, separate from warning_label (load failures) - see
        # _get_checked_plotted(). Styled distinctly (amber) so it reads as "heads up
        # about data provenance", not an error.
        self.live_banner_label = QLabel("")
        self.live_banner_label.setWordWrap(True)
        self.live_banner_label.setStyleSheet(
            "background-color: #fff3cd; color: #664d03; padding: 4px; font-weight: bold;"
        )
        self.live_banner_label.setVisible(False)
        main_layout.addWidget(self.live_banner_label)

        # constrained layout (not tight_layout()) recomputes margins on every draw,
        # including window resizes - tight_layout() only computes them once at the
        # call site and goes stale (clipped axis labels) as the Qt widget is resized
        self.figure = Figure(figsize=(10, 6), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.nav_toolbar = NavigationToolbar2QT(self.canvas, self)
        main_layout.addWidget(self.nav_toolbar)
        main_layout.addWidget(self.canvas, 1)

        # Ctrl+C copies the plot itself (not the toolbar/file list/controls) to
        # the clipboard as an image - window-scoped (default QShortcut context)
        # so it fires regardless of which child widget currently has focus
        QShortcut(QKeySequence.Copy, self).activated.connect(
            lambda: QApplication.clipboard().setPixmap(self.canvas.grab()))

    # ---------- Qt event hooks ----------

    def showEvent(self, event):
        super().showEvent(event)
        self._rescan_files()

    # ---------- Live preview (raw port-S.csv, before combine_snp has run) ----------

    def _is_palace_run_active(self):
        """True only while this window's associated CreateModelTab is actively
        running a real Palace simulation (not mesh creation / model fit / snp2le
        install). False in standalone mode (_StandaloneMainWindow has no
        create_model_tab/PalaceMode at all) and for Elmer mode (this feature is
        Palace-only). Used only to word the live banner and arm/disarm the polling
        timer - NOT to decide whether live data is offered at all, see
        _rescan_files() (a crashed/stopped run keeps showing its last completed
        iteration until real results appear)."""
        create_model_tab = getattr(self.MainWindow, 'create_model_tab', None)
        if create_model_tab is None or not getattr(self.MainWindow, 'PalaceMode', False):
            return False
        return (create_model_tab.process.state() == QProcess.Running
                and create_model_tab._process_purpose == "run_simulation")

    def _sync_live_timer(self):
        """Poll for newly-completed iterations only while a run is actually active -
        an idle, already-open viewer shouldn't keep scanning the filesystem forever."""
        should_poll = self._is_palace_run_active()
        if should_poll and not self._live_timer.isActive():
            self._live_timer.start(5000)  # iterations take tens of seconds to minutes
        elif not should_poll and self._live_timer.isActive():
            self._live_timer.stop()

    # ---------- File list ----------

    def _relpath_for_path(self, path):
        """Full relative-to-target-dir path, untruncated - used for the file list,
        which has room to show it in full (or scroll) rather than shortening it."""
        if path in self._external_paths:
            # usually lives nowhere near target_dir - a relpath() would just be an
            # ugly, long "../../.." string, so show the plain filename instead
            return os.path.basename(path)
        rel = os.path.relpath(path, self._target_dir) if self._target_dir else path
        return rel.replace('\\', '/')

    def _legend_label_for_path(self, path):
        """Shortened label for plot legends, which have much less room than the
        file list."""
        if path in self._live_paths:
            return f"{os.path.basename(os.path.dirname(path))} (live)"
        rel = self._relpath_for_path(path)
        if len(rel) <= 17:
            return rel
        return rel[:10] + '..' + rel[-20:]

    def _filtered_files(self, files):
        include_dc = self.include_dc_cb.isChecked()
        include_deembedded = self.include_deembedded_cb.isChecked()
        include_all_models = self.include_all_models_cb.isChecked()

        # "<model_basename>_data" is the run-folder-naming convention used for both
        # palace_model/ and elmer_model/ (see run_model()/find_output_dir() in
        # setupEM.py/palace_results.py) - checking for it as a path component,
        # rather than branching on PalaceMode, restricts to the current model
        # regardless of which solver produced it.
        saved_values = self.MainWindow.saved_values
        model_basename = saved_values.get('model_basename', '') if isinstance(saved_values, dict) else ''
        current_model_dir = f"{model_basename}_data" if model_basename else None

        result = []
        for path in files:
            name = os.path.basename(path)
            if not include_dc and '_dc' in name:
                continue
            if not include_deembedded and '_deembedded' in name:
                continue
            if not include_all_models and current_model_dir:
                if current_model_dir not in os.path.normpath(path).split(os.sep):
                    continue
            result.append(path)
        return result

    def _rescan_files(self):
        saved_values = self.MainWindow.saved_values
        target_dir = saved_values.get('sim_path', '') if isinstance(saved_values, dict) else ''
        self._target_dir = target_dir.replace('\\', '/') if target_dir else ''

        self.file_list.blockSignals(True)
        self.file_list.clear()
        self._live_paths = set()

        # target_dir_error is only a fallback message for when there's nothing else
        # to show - it does NOT stop externally-added files from being offered
        # below, so comparing two "Add..."-ed files works even with no target
        # directory set at all (e.g. a fresh/standalone viewer).
        target_dir_error = None
        all_files = []
        self._master_files = []

        if not target_dir:
            target_dir_error = "No Target Directory set (see Create Model tab)."
        elif not os.path.isdir(target_dir):
            target_dir_error = f"Target Directory does not exist: {target_dir}"
        else:
            all_files = find_touchstone_files(target_dir)
            self._master_files = self._filtered_files(all_files)

            # Live preview: as long as this run's own Palace output directory has no
            # real "final" Touchstone file yet (combine_snp hasn't run, or the run
            # crashed/was stopped before it could), offer each already-completed AMR
            # iteration's raw port-S.csv instead. Scoped to THIS model's own output
            # dir (not all of target_dir) so an unrelated other model's leftover
            # results in the same sim_path can't wrongly suppress or feed this.
            model_basename = saved_values.get('model_basename', '') if isinstance(saved_values, dict) else ''
            if model_basename:
                run_path = os.path.join(target_dir, "palace_model", model_basename + "_data")
                output_dir = os.path.normpath(find_output_dir(run_path, model_basename))
                has_final_result = any(
                    not is_amr_iteration_snapshot(p)
                    for p in all_files
                    if os.path.normpath(p).startswith(output_dir)
                )
                if not has_final_result:
                    for iteration_dir in find_live_iteration_dirs(run_path, model_basename):
                        self._live_paths.add(os.path.join(iteration_dir, "port-S.csv"))
                    if self._live_paths:
                        self._master_files = sorted(set(self._master_files) | self._live_paths)

        # Externally-added files ("Add..." button) are folded in regardless of
        # target_dir - they don't come from the directory scan at all, and aren't
        # subject to the _dc/_deembedded/model-name filters above either, since the
        # user explicitly picked them.
        if self._external_paths:
            self._master_files = sorted(set(self._master_files) | set(self._external_paths))

        if not self._master_files:
            if target_dir_error:
                message = target_dir_error
            elif all_files:
                message = "No files match the current _dc/_deembedded/model filters " \
                           f"under {target_dir}"
            else:
                message = f"No Touchstone (.sNp) files found under {target_dir}"
            item = QTreeWidgetItem([message])
            item.setFlags(Qt.NoItemFlags)
            self.file_list.addTopLevelItem(item)
        else:
            # drop checked paths that no longer exist; auto-check the final
            # result (preferring it over any AMR per-iteration snapshot) if
            # nothing is checked (e.g. first open), so the window isn't blank
            self._checked_paths &= set(self._master_files)
            if not self._checked_paths:
                self._checked_paths = {pick_final_result_file(self._master_files)}

            # group by each file's immediate parent directory (relative to
            # target_dir), not a Palace/Elmer-specific convention like
            # <model>_data, so this stays correct for either output layout.
            # Files sitting directly in target_dir (parent == "") get no
            # wrapper group node - they're added straight to the tree. Externally
            # added files are excluded here (they typically don't live under
            # target_dir at all) and rendered as their own group below instead.
            groups = {}
            for path in self._master_files:
                if path in self._external_paths:
                    continue
                parent = os.path.dirname(self._relpath_for_path(path))
                groups.setdefault(parent, []).append(path)

            for path in groups.pop("", []):
                self.file_list.addTopLevelItem(self._make_file_item(path))
            for parent in sorted(groups):
                group_item = QTreeWidgetItem([parent])
                # checkable so the whole group can be checked/unchecked at once
                # (propagated to/from its children in _on_file_item_changed);
                # not given Qt.ItemIsAutoTristate - propagation is done manually
                # below so exactly one _on_control_changed()/redraw happens per
                # user action, not one per child
                group_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                self.file_list.addTopLevelItem(group_item)
                for path in groups[parent]:
                    group_item.addChild(self._make_file_item(path))
                self._refresh_group_checkstate(group_item)

            if self._external_paths:
                external_group = QTreeWidgetItem(["External files (for comparison)"])
                external_group.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                self.file_list.addTopLevelItem(external_group)
                for path in self._external_paths:
                    external_group.addChild(self._make_file_item(path))
                self._refresh_group_checkstate(external_group)

            self.file_list.expandAll()

        self.file_list.blockSignals(False)
        self._on_control_changed()
        self._sync_live_timer()

    def _make_file_item(self, path):
        if path in self._live_paths:
            text = f"⚡ {os.path.basename(os.path.dirname(path))} (live preview, not yet combined)"
        elif path in self._external_paths:
            text = f"📄 {os.path.basename(path)} (external)"
        else:
            text = os.path.basename(path)
        item = QTreeWidgetItem([text])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setData(0, Qt.UserRole, path)
        item.setCheckState(0, Qt.Checked if path in self._checked_paths else Qt.Unchecked)
        return item

    def _on_file_item_changed(self, item, column=0):
        if self._updating_checks:
            return  # this change is itself a propagation side-effect below - ignore it
        path = item.data(0, Qt.UserRole)
        # placeholder rows are never checkable, so itemChanged never fires for them;
        # anything checkable with no path data here is a group header
        is_group = path is None

        self._updating_checks = True
        try:
            if is_group:
                state = item.checkState(0)
                for i in range(item.childCount()):
                    child = item.child(i)
                    child.setCheckState(0, state)
                    child_path = child.data(0, Qt.UserRole)
                    if state == Qt.Checked:
                        self._checked_paths.add(child_path)
                    else:
                        self._checked_paths.discard(child_path)
            else:
                if item.checkState(0) == Qt.Checked:
                    self._checked_paths.add(path)
                else:
                    self._checked_paths.discard(path)
                parent = item.parent()
                if parent is not None:
                    self._refresh_group_checkstate(parent)
        finally:
            self._updating_checks = False

        self._on_control_changed()

    def _refresh_group_checkstate(self, group_item):
        """Set a group header's own checkbox to reflect its children: checked if
        all children are checked, unchecked if none are, partially-checked if mixed."""
        states = [group_item.child(i).checkState(0) for i in range(group_item.childCount())]
        if all(s == Qt.Checked for s in states):
            group_item.setCheckState(0, Qt.Checked)
        elif all(s == Qt.Unchecked for s in states):
            group_item.setCheckState(0, Qt.Unchecked)
        else:
            group_item.setCheckState(0, Qt.PartiallyChecked)

    # ---------- External files ("Add..." button, for comparing to measured data) ----------

    def _on_add_external_clicked(self):
        """Browse for and add an arbitrary Touchstone file, e.g. lab-measured data,
        for overlay comparison against the current model's results. No port-count
        matching is enforced - _current_common_nports()/_rebuild_parameter_grid()
        already tolerate mixed port counts among checked files today (they just use
        the smallest one), and that's the desired behavior here too."""
        start_dir = self._target_dir if os.path.isdir(self._target_dir) else ""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Add External Touchstone File", start_dir, "Touchstone files (*.s*p);;*.*")
        if not filename:
            return
        filename = filename.replace('\\', '/')
        try:
            rf.Network(filename)  # sanity check it's actually a loadable Touchstone file
        except Exception as exc:
            QMessageBox.warning(self, "Failed to load file",
                                 f"Could not read Touchstone file:\n{filename}\n\n{exc}")
            return
        if filename not in self._external_paths:
            self._external_paths.append(filename)
        self._checked_paths.add(filename)  # auto-check on add, like a freshly-picked final result
        self._rescan_files()

    def _on_file_context_menu(self, pos):
        """Right-click menu on the file list - only externally-added rows get a
        "Remove from list" action; generated results, live-preview rows and group
        headers aren't removable this way."""
        item = self.file_list.itemAt(pos)
        if item is None:
            return
        path = item.data(0, Qt.UserRole)
        if path is None or path not in self._external_paths:
            return
        menu = QMenu(self)
        remove_action = menu.addAction("Remove from list")
        if menu.exec(self.file_list.viewport().mapToGlobal(pos)) == remove_action:
            self._external_paths.remove(path)
            self._checked_paths.discard(path)
            self._network_cache.pop(path, None)
            self._rescan_files()

    # ---------- Network loading ----------

    def _load_network_cached(self, path):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._network_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        if path in self._live_paths:
            network = _network_from_port_s_data(read_port_s_data(os.path.dirname(path)))
        else:
            try:
                network = rf.Network(path)
            except Exception:
                network = None
        self._network_cache[path] = (mtime, network)
        return network

    def _get_checked_plotted(self):
        """Return [(network, color, linestyle, label), ...] for currently checked,
        successfully-loaded files, and update the warning label for any that failed
        to load. Color/linestyle are keyed to position among the checked, loaded
        files (master-list order), so the first curve is always COLORS[0]/
        LINESTYLES[0] (red, solid) - matches plot_snp.py's own convention. Colors
        can shift for other files as check state changes; that trade-off is
        accepted so "the first curve" always means red/solid."""
        checked_in_order = [path for path in self._master_files if path in self._checked_paths]
        networks = []
        warnings = []
        live_labels = []
        for path in checked_in_order:
            label = self._legend_label_for_path(path)
            network = self._load_network_cached(path)
            if network is None:
                warnings.append(label)
            else:
                networks.append((network, label))
                if path in self._live_paths:
                    live_labels.append(os.path.basename(os.path.dirname(path)))
        self.warning_label.setText("Failed to load: " + ", ".join(warnings) if warnings else "")
        if live_labels:
            if self._is_palace_run_active():
                self.live_banner_label.setText(
                    "⚡ LIVE PREVIEW - simulation running. Showing raw, not-yet-combined "
                    f"Palace results from: {', '.join(live_labels)}. Final combined results "
                    "will replace this automatically once the run finishes."
                )
            else:
                self.live_banner_label.setText(
                    "⚠ Simulation is not currently running - showing last available raw "
                    f"iteration data from: {', '.join(live_labels)}."
                )
        self.live_banner_label.setVisible(bool(live_labels))
        return [
            (network, COLORS[i % len(COLORS)], LINESTYLES[i % len(LINESTYLES)], label)
            for i, (network, label) in enumerate(networks)
        ]

    def _current_common_nports(self):
        plotted = self._get_checked_plotted()
        if not plotted:
            return 0
        return min(network.nports for network, _, _, _ in plotted)

    # ---------- S-parameter picker ----------

    def _rebuild_parameter_grid(self, n):
        while self.param_grid_layout.count():
            child = self.param_grid_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

        self._checked_params = {(m, k) for (m, k) in self._checked_params if m <= n and k <= n}
        if n >= 1 and not self._checked_params:
            self._checked_params = {(1, 1)}  # never let the plot go silently empty

        if n == 0:
            self.param_grid_layout.addWidget(QLabel("Check a file to choose S-parameters"), 0, 0)
        else:
            for m in range(1, n + 1):
                for k in range(1, n + 1):
                    btn = QPushButton(f"S{m}{k}")
                    btn.setCheckable(True)
                    btn.setChecked((m, k) in self._checked_params)
                    btn.setFixedWidth(50)
                    btn.toggled.connect(lambda checked, m=m, k=k: self._on_param_toggled(m, k, checked))
                    self.param_grid_layout.addWidget(btn, m - 1, k - 1)

        self.redraw_plot()

    def _on_param_toggled(self, m, n, checked):
        if checked:
            self._checked_params.add((m, n))
        else:
            self._checked_params.discard((m, n))
        self.redraw_plot()

    # ---------- Display mode ----------

    def _on_mode_changed(self, checked):
        if not checked:
            return  # QButtonGroup fires toggled(False) for the button losing the selection too
        if self.smith_radio.isChecked():
            self.smith_mode = "smith"
        elif self.zoom_radio.isChecked():
            self.smith_mode = "zoom"
        else:
            self.smith_mode = "phase"
        self.redraw_plot()

    # ---------- Redraw ----------

    def _on_control_changed(self):
        n = self._current_common_nports()
        if n != self._last_n:
            self._last_n = n
            self._rebuild_parameter_grid(n)  # rebuilds grid and redraws
        else:
            self.redraw_plot()

    def redraw_plot(self):
        self.figure.clear()
        plotted = self._get_checked_plotted()  # also sets warning_label for load failures
        params = sorted(self._checked_params)

        if not plotted or not params:
            ax = self.figure.add_subplot(111)
            ax.axis('off')
            ax.text(0.5, 0.5, "Check a file and at least one S-parameter to plot",
                     ha='center', va='center', transform=ax.transAxes)
            self.canvas.draw_idle()
            return

        if self.smith_mode in ("smith", "zoom"):
            # Smith/zoomed-Smith replaces dB+phase entirely - only reflection (Snn)
            # parameters have a Smith representation, so non-reflection selections
            # are left out of this view (noted in the warning label) rather than
            # shown as an empty/meaningless chart
            reflection_params = [(m, n) for (m, n) in params if m == n]
            excluded = [(m, n) for (m, n) in params if m != n]
            if excluded:
                note = "Not shown in Smith view (not reflection): " + \
                    ", ".join(f"S{m}{n}" for m, n in excluded)
                current = self.warning_label.text()
                self.warning_label.setText((current + "   " if current else "") + note)

            if not reflection_params:
                ax = self.figure.add_subplot(111)
                ax.axis('off')
                ax.text(0.5, 0.5, "No reflection (Snn) parameter selected for Smith view",
                         ha='center', va='center', transform=ax.transAxes)
                self.canvas.draw_idle()
                return

            axes = self.figure.subplots(1, len(reflection_params), squeeze=False)
            for a, (m, n) in enumerate(reflection_params):
                draw_smith(axes[0][a], m, n, plotted, zoomed=(self.smith_mode == "zoom"))
        else:
            axes = self.figure.subplots(2, len(params), squeeze=False)
            for a, (m, n) in enumerate(params):
                draw_rectangular(axes[0][a], m, n, plotted, mode="db")
                draw_rectangular(axes[1][a], m, n, plotted, mode="phase")

        self.figure.suptitle("S Parameters")
        self._draw_shared_legend(plotted)
        self.canvas.draw_idle()

    def _draw_shared_legend(self, plotted):
        """One legend below the whole figure instead of one per subplot - every
        subplot shows the same file set, and a per-axis legend got clipped by the
        figure edge or a neighboring subplot once more than one column was shown.
        Proxy handles (not pulled from an axis) so this doesn't depend on which
        internal artists a particular draw helper (e.g. skrf's plot_s_smith)
        created."""
        handles = [Line2D([0], [0], color=color, linestyle=linestyle)
                   for _, color, linestyle, _ in plotted]
        labels = [label for _, _, _, label in plotted]
        # "outside lower center" (not "lower center") is what makes the constrained
        # layout engine reserve room for this legend below the axes on every draw,
        # instead of the legend floating over/under-clipped by the figure edge
        self.figure.legend(handles, labels, loc='outside lower center',
                            ncol=min(len(plotted), 4), fontsize=8)


# ------------------------------------------------------------------
# Standalone launch (python result_viewer.py [target_dir], or the
# resultViewer console script - see pyproject.toml)
# ------------------------------------------------------------------

class _StandaloneMainWindow:
    """Minimal stand-in for the real setupEM MainWindow, used only when this module
    is run on its own rather than opened from within the full app (Create Model
    tab's View Results button). Provides just the saved_values dict
    ResultViewerWindow needs from its MainWindow argument."""
    APP_NAME = "Result Viewer"

    def __init__(self, target_dir):
        self.saved_values = {'sim_path': target_dir}


def main():
    app = QApplication(sys.argv)

    # Pin a light color scheme so the explicit light backgrounds set on
    # QLineEdit/QComboBox fields elsewhere aren't fighting an inherited dark
    # auto-palette on accounts where Windows' per-user dark-mode setting is
    # on (PySide6 6.5+ only; older versions just skip this and rely on the
    # explicit "color:" rules already set on those field stylesheets).
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass

    if sys.platform.startswith("win"):
        # matches setupEM.py's/setupThermal.py's main() - without this, Qt's default
        # style on Windows looks visibly different (fonts/widget chrome) from the full app
        app.setStyle(QStyleFactory.create("Windows"))

    parser = argparse.ArgumentParser(description="Standalone S-parameter result viewer")
    parser.add_argument("target_dir", nargs="?", default=os.getcwd(),
                         help="directory to search recursively for Touchstone (.sNp) "
                              "files (default: current directory)")
    args = parser.parse_args()

    window = ResultViewerWindow(_StandaloneMainWindow(args.target_dir))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
