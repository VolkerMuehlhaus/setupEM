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
import math
import os
import re
import sys

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
)
from PySide6.QtCore import Qt


# ------------------------------------------------------------------
# Plot data helpers (ported from plot_snp.py)
# ------------------------------------------------------------------

COLORS = ['r', 'b', 'm', 'c', 'g', 'y', 'k', 'w']
LINESTYLES = ['solid', 'dashed', 'dashdot', 'dotted', 'solid', 'dashed', 'dashdot', 'dotted']

# marker size for the fat dot marking a network with a single frequency point
# (no line can be drawn between points, so it would otherwise be invisible)
SINGLE_POINT_MARKERSIZE = 7

# reflection coefficient magnitude shown in the zoomed Smith chart
ZOOM_GAMMA = 0.5
GRID_COLOR = 'lightgrey'
GRID_LW = 0.8
# constant-resistance/-reactance grid values for the zoomed Smith chart,
# denser than skrf's default labeled grid ([0.2, 0.5, 1, 2, 5])
ZOOM_GRID_VALUES = [0.2, 0.5, 1.0, 1.5, 2.0, 3.0]

TOUCHSTONE_RE = re.compile(r'\.s(\d+)p$', re.IGNORECASE)


def dB(value):
    return 20.0 * np.log10(np.abs(value))


def phase_deg(value):
    return np.angle(value, deg=True)


def Sxx(network, m, n):
    return network.s[:, m-1, n-1]


def draw_zoomed_smith_grid(ax, gamma):
    # draw a denser Smith chart grid for the zoomed view, with labels placed where
    # each grid circle crosses the real (for r) or imaginary (for x) axis, since
    # skrf's own label placement is designed for the full chart and would fall
    # outside the zoomed axis limits
    ax.axhline(0, color='grey', lw=0.5)

    for r in ZOOM_GRID_VALUES:
        center = (r/(1+r), 0)
        radius = 1/(1+r)
        ax.add_patch(Circle(center, radius, ec=GRID_COLOR, fc='none', lw=GRID_LW))
        label_pos = center[0] - radius
        if abs(label_pos) < gamma:
            ax.annotate(f"{r:g}", xy=(label_pos, 0), xytext=(label_pos, 0.01),
                        fontsize=8, color='dimgrey', ha='center', va='bottom')

    for sign in (1, -1):
        for x in ZOOM_GRID_VALUES:
            xv = sign * x
            center = (1, 1/xv)
            radius = abs(1/xv)
            ax.add_patch(Circle(center, radius, ec=GRID_COLOR, fc='none', lw=GRID_LW))
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
    one trace per (network, color, linestyle, label) tuple in plotted, into ax."""
    if zoomed:
        draw_zoomed_smith_grid(ax, ZOOM_GAMMA)
        for network, color, linestyle, label in plotted:
            data = Sxx(network, m, n)
            if len(data) == 1:
                # a single frequency point has no line to draw between points
                # and would otherwise be invisible - mark it with a fat dot
                ax.plot(data.real, data.imag, color=color, linestyle=linestyle,
                         label=label, marker='o', markersize=SINGLE_POINT_MARKERSIZE)
            else:
                ax.plot(data.real, data.imag, color=color, linestyle=linestyle, label=label)
        ax.set_xlim(-ZOOM_GAMMA, ZOOM_GAMMA)
        ax.set_ylim(-ZOOM_GAMMA, ZOOM_GAMMA)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        for network, color, linestyle, label in plotted:
            marker_kwargs = {'marker': 'o', 'markersize': SINGLE_POINT_MARKERSIZE} if Sxx(network, m, n).size == 1 else {}
            network.plot_s_smith(m-1, n-1, ax=ax, show_legend=False, draw_labels=True,
                                  color=color, linestyle=linestyle, label=label, **marker_kwargs)
        # skrf draws the grid circles with hardcoded colors (black for r=0/1, x=+-1);
        # recolor them to match the zoomed chart's uniform light grid style
        for patch in ax.patches:
            patch.set_edgecolor(GRID_COLOR)
            patch.set_linewidth(GRID_LW)

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
    MainWindow.saved_values['sim_path'] and plots the ones checked."""

    def __init__(self, MainWindow):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.MainWindow = MainWindow

        self._target_dir = ''
        self._master_files = []          # sorted absolute paths, last scan
        self._checked_paths = set()      # subset of _master_files currently checked
        self._checked_params = {(1, 1)}  # set of (m, n) S-parameters to plot
        self._network_cache = {}         # path -> (mtime, skrf.Network | None)
        self._last_n = None              # common port count as of last parameter-grid rebuild
        self.smith_mode = "phase"        # "phase" | "smith" | "zoom"
        self._updating_checks = False    # re-entrancy guard for group<->leaf checkbox propagation

        self._build_ui()
        self._rescan_files()

    # ---------- UI construction ----------

    def _build_ui(self):
        self.setWindowTitle("Result Viewer")
        self.resize(1200, 800)
        main_layout = QVBoxLayout(self)

        controls_layout = QHBoxLayout()

        files_group = QGroupBox("Files")
        files_layout = QVBoxLayout()
        filter_layout = QHBoxLayout()
        self.include_dc_cb = QCheckBox("Include _dc files")
        self.include_dc_cb.setChecked(False)  # start showing only the raw result file
        self.include_dc_cb.toggled.connect(self._rescan_files)
        filter_layout.addWidget(self.include_dc_cb)
        self.include_deembedded_cb = QCheckBox("Include _deembedded files")
        self.include_deembedded_cb.setChecked(False)  # start showing only the raw result file
        self.include_deembedded_cb.toggled.connect(self._rescan_files)
        filter_layout.addWidget(self.include_deembedded_cb)
        filter_layout.addStretch()
        files_layout.addLayout(filter_layout)
        self.file_list = QTreeWidget()
        self.file_list.setHeaderHidden(True)
        self.file_list.itemChanged.connect(self._on_file_item_changed)
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

        # constrained layout (not tight_layout()) recomputes margins on every draw,
        # including window resizes - tight_layout() only computes them once at the
        # call site and goes stale (clipped axis labels) as the Qt widget is resized
        self.figure = Figure(figsize=(10, 6), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.nav_toolbar = NavigationToolbar2QT(self.canvas, self)
        main_layout.addWidget(self.nav_toolbar)
        main_layout.addWidget(self.canvas, 1)

    # ---------- Qt event hooks ----------

    def showEvent(self, event):
        super().showEvent(event)
        self._rescan_files()

    # ---------- File list ----------

    def _relpath_for_path(self, path):
        """Full relative-to-target-dir path, untruncated - used for the file list,
        which has room to show it in full (or scroll) rather than shortening it."""
        rel = os.path.relpath(path, self._target_dir) if self._target_dir else path
        return rel.replace('\\', '/')

    def _legend_label_for_path(self, path):
        """Shortened label for plot legends, which have much less room than the
        file list."""
        rel = self._relpath_for_path(path)
        if len(rel) <= 17:
            return rel
        return rel[:10] + '..' + rel[-20:]

    def _filtered_files(self, files):
        include_dc = self.include_dc_cb.isChecked()
        include_deembedded = self.include_deembedded_cb.isChecked()
        result = []
        for path in files:
            name = os.path.basename(path)
            if not include_dc and '_dc' in name:
                continue
            if not include_deembedded and '_deembedded' in name:
                continue
            result.append(path)
        return result

    def _rescan_files(self):
        saved_values = self.MainWindow.saved_values
        target_dir = saved_values.get('sim_path', '') if isinstance(saved_values, dict) else ''
        self._target_dir = target_dir.replace('\\', '/') if target_dir else ''

        self.file_list.blockSignals(True)
        self.file_list.clear()

        if not target_dir:
            self._master_files = []
            item = QTreeWidgetItem(["No Target Directory set (see Create Model tab)."])
            item.setFlags(Qt.NoItemFlags)
            self.file_list.addTopLevelItem(item)
        elif not os.path.isdir(target_dir):
            self._master_files = []
            item = QTreeWidgetItem([f"Target Directory does not exist: {target_dir}"])
            item.setFlags(Qt.NoItemFlags)
            self.file_list.addTopLevelItem(item)
        else:
            all_files = find_touchstone_files(target_dir)
            self._master_files = self._filtered_files(all_files)
            if not self._master_files:
                if all_files:
                    message = "No files match the current _dc/_deembedded filters " \
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
                # wrapper group node - they're added straight to the tree.
                groups = {}
                for path in self._master_files:
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

                self.file_list.expandAll()

        self.file_list.blockSignals(False)
        self._on_control_changed()

    def _make_file_item(self, path):
        item = QTreeWidgetItem([os.path.basename(path)])
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

    # ---------- Network loading ----------

    def _load_network_cached(self, path):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._network_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
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
        for path in checked_in_order:
            network = self._load_network_cached(path)
            label = self._legend_label_for_path(path)
            if network is None:
                warnings.append(label)
            else:
                networks.append((network, label))
        self.warning_label.setText("Failed to load: " + ", ".join(warnings) if warnings else "")
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
