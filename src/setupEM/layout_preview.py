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
layout_preview.py

Layout Preview window (Tools > Layout Preview...): draws the 2D GDSII shapes
gds2palace's own reader would process for the current Input Files tab
settings (GDS file, cell name, datatype/purpose filter), in z (stackup)
order, colored per the stackup XML's material colors. "Marker" shapes - from
MainWindow.get_layout_preview_markers(): EM ports for setupEM, thermal
sources/constant-temperature boundaries for setupThermal - are drawn on top,
highlighted, labeled, and grouped separately (own legend section per kind).
"""

import os, io, contextlib
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel,
    QScrollArea, QCheckBox, QMessageBox, QGraphicsView, QGraphicsScene,
    QGraphicsItem, QGraphicsPolygonItem, QGraphicsSimpleTextItem,
    QGraphicsEllipseItem, QGraphicsPathItem, QSlider, QSplitter, QApplication,
    )
from PySide6.QtGui import (
    QColor, QBrush, QPen, QPolygonF, QPainter, QFont, QPainterPath, QTransform,
    QShortcut, QKeySequence,
    )
from PySide6.QtCore import Qt, QPointF, Signal

from gds2palace import gds_reader, stackup_reader

DEFAULT_LAYER_COLOR = "#a0a0a0"   # stackup_material.color has no default (None) if XML omits Color=
PEC_LAYER_COLOR = "#b4dcff"       # matches setup_common.PEC_MATERIAL_COLOR's soft blue
PORT_OUTLINE_COLOR = "#ff33ff"
PORT_FILL_COLOR = QColor(255, 51, 255, 100)
SOURCE_OUTLINE_COLOR = "#ff8800"      # orange: thermal heat source
SOURCE_FILL_COLOR = QColor(255, 136, 0, 100)
BOUNDARY_OUTLINE_COLOR = "#33ccff"    # cyan: thermal constant-temperature boundary
BOUNDARY_FILL_COLOR = QColor(51, 204, 255, 100)
# a z-directed via port's (or a thermal source/boundary's) GDS marker polygon
# is often a zero-width sliver in one axis (a line, not a rectangle) - draw its
# outline with this fixed device-pixel width (a cosmetic pen, so it does not
# grow/shrink with canvas zoom) so it stays visible instead of vanishing,
# without faking the underlying geometry
MARKER_OUTLINE_WIDTH = 3
# initial layer-opacity slider position
DEFAULT_LAYER_OPACITY_PERCENT = 70

# cross-window highlight (see set_highlighted_layer()): a layer selected in the
# Stackup Preview/Editor gets this outline + hatch fill, above every regular
# layer but *below* every port/source/boundary marker (those must stay
# visible on top no matter what) - its actual z-value is computed per refresh()
# in self._highlight_zvalue, since it depends on how many layers are drawn.
# White (not red) since it's the one color none of the layer/marker palettes
# above use, so it reads clearly against any of them and the dark background.
HIGHLIGHT_COLOR = "white"
HIGHLIGHT_WIDTH = 3
HIGHLIGHT_ZVALUE = 100000

# legend section order for "marker" groups (see get_layout_preview_markers());
# a group only appears if the current app/data actually has items for it
MARKER_GROUP_ORDER = ["Ports", "Sources", "Boundaries"]
# per-marker-kind (fill, outline) colors, keyed by the "kind" tag each marker
# dict carries (see MainWindowBase.get_layout_preview_markers() docstring)
MARKER_STYLES = {
    "port": (PORT_FILL_COLOR, PORT_OUTLINE_COLOR),
    "source": (SOURCE_FILL_COLOR, SOURCE_OUTLINE_COLOR),
    "boundary": (BOUNDARY_FILL_COLOR, BOUNDARY_OUTLINE_COLOR),
}


def _format_value(value, unit):
    return f"{float(value):g} {unit}"

# in-plane port directions -> (dx, dy) unit vector in *scene* coordinates (y
# already flipped vs. GDS, matching _polygon_points()'s float(-y) convention -
# GDS +Y is "up", which is -y in scene/screen space). Z/-Z (via ports) have no
# entry here - a through-plane direction has no meaningful in-plane arrow.
_DIRECTION_VECTORS = {
    "X": (1, 0), "-X": (-1, 0),
    "Y": (0, -1), "-Y": (0, 1),
}


def _direction_vector(direction):
    return _DIRECTION_VECTORS.get(str(direction).strip().upper())


def _signed_direction(direction):
    """Compact direction with an explicit sign, e.g. "Z" -> "+Z" (a stored
    direction always has a sign for the negative case already, but not for
    the implied-positive case) - kept short for the legend list.
    """
    direction = str(direction).strip().upper()
    if direction.startswith("-") or direction.startswith("+"):
        return direction
    return "+" + direction if direction else direction


def _arrow_path(dx, dy, length=27, head_size=9):
    """An open arrow shape (shaft + V head) centered on (0, 0) and pointing
    towards (dx, dy), in local item coordinates - paired with
    ItemIgnoresTransformations, this keeps the arrow a fixed pixel size
    regardless of canvas zoom, same as the port label.
    """
    half = length / 2
    tail_x, tail_y = -dx * half, -dy * half
    tip_x, tip_y = dx * half, dy * half
    back_x, back_y = tip_x - dx * head_size, tip_y - dy * head_size
    perp_x, perp_y = -dy * head_size * 0.5, dx * head_size * 0.5

    path = QPainterPath()
    path.moveTo(tail_x, tail_y)
    path.lineTo(tip_x, tip_y)
    path.lineTo(back_x + perp_x, back_y + perp_y)
    path.moveTo(tip_x, tip_y)
    path.lineTo(back_x - perp_x, back_y - perp_y)
    return path


VIA_POSITIVE_COLOR = PORT_OUTLINE_COLOR  # pink/magenta: +Z, current out of the page
VIA_NEGATIVE_COLOR = "#3399ff"           # blue: -Z, current into the page


def _via_marker_items(negative):
    """Graphics item for a Z/-Z via port's marker: a filled circle at the port
    location - pink for current out of the page (+Z), blue for into the page
    (-Z). Positioned/parented by the caller, same as the in-plane marker.
    """
    color = QColor(VIA_NEGATIVE_COLOR if negative else VIA_POSITIVE_COLOR)
    circle = QGraphicsEllipseItem(-9, -9, 18, 18)
    circle.setBrush(QBrush(color))
    circle.setPen(Qt.NoPen)
    return [circle]


def _plain_marker_items(color):
    """Graphics item for a thermal source/boundary's marker: a plain filled
    circle at the location, no direction/polarity indicator - thermal objects
    have no orientation to show, unlike EM ports.
    """
    circle = QGraphicsEllipseItem(-9, -9, 18, 18)
    circle.setBrush(QBrush(QColor(color)))
    circle.setPen(Qt.NoPen)
    return [circle]


def _material_qcolor(material, materialname=None):
    if material is None and materialname is not None and \
            materialname.strip().upper() == stackup_reader.PEC_MATERIAL_NAME.upper():
        return QColor(PEC_LAYER_COLOR)
    color = getattr(material, "color", None) if material is not None else None
    if not color:
        return QColor(DEFAULT_LAYER_COLOR)
    return QColor(color if color.startswith("#") else "#" + color)


class _VisibilityGroup:
    """A plain (non-scene) grouping of graphics items that a single legend
    checkbox shows/hides together. Deliberately not a QGraphicsItemGroup: its
    marker/label items use ItemIgnoresTransformations (see refresh() below),
    and Qt documents that flag as unreliable on an item whose parent doesn't
    also carry it - so members stay direct top-level scene items instead, and
    this class just applies setVisible()/setZValue() to each individually.
    """

    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)

    def setVisible(self, visible):
        for item in self.items:
            item.setVisible(visible)

    def setZValue(self, z):
        for item in self.items:
            item.setZValue(z)


class _ClickableLegendRow(QWidget):
    """A legend row for a drawn layer (not a marker group) that can be clicked
    to select/deselect that layer - anywhere except the visibility checkbox,
    which keeps its own click for show/hide. The swatch/label children are
    WA_TransparentForMouseEvents so a click on them still reaches this widget.
    """

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class LayoutCanvas(QGraphicsView):
    """Pan/zoom GDS canvas - plain PySide6 QGraphicsView, no new dependency.
    Polygon/label points are stored with y already negated (see
    LayoutPreviewWindow.refresh), so GDS "up" (+y) renders near the top of
    the view without a separate view-level flip transform.
    """

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#303030"))

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)


class LayoutPreviewWindow(QDialog):
    """Own top-level window (WA_DeleteOnClose, non-modal) - same lifecycle as
    StackupEditorWindow/ResultViewerWindow, so the user can keep it open while
    tweaking the Input Files/Ports tabs and click Refresh.
    """

    # emitted when a Layers-section item is selected/deselected directly in
    # this window's own legend (see _on_legend_layer_clicked()) - the GDS
    # layer number, or None when cleared. MainWindow resolves this to a real
    # metal/via <Layer> or a Dielectric's Boundary layer and mirrors it into
    # the Stackup Preview/Editor - the reverse of the existing Stackup->Layout
    # direction (set_highlighted_layer(), called by MainWindow). Selecting a
    # marker (port/thermal source/boundary) never emits this - there's no
    # stackup element for those to sync to.
    layerSelected = Signal(object)

    def __init__(self, MainWindow, gds_override_path=None, title_suffix=""):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Layout Preview" + (f" - {title_suffix}" if title_suffix else ""))
        self.resize(1100, 750)
        self.MainWindow = MainWindow
        # when set, read this GDS file instead of MainWindow.file_tab's live
        # value/saved_values["GdsFile"] - used by Tools > Simplify GDS... to
        # show the simplified output next to the original, both still colored
        # by the (unchanged) stackup XML the MainWindow already has loaded
        self._gds_override_path = gds_override_path

        self.canvas = LayoutCanvas()

        self.legend_layout = QVBoxLayout()
        self.legend_layout.setAlignment(Qt.AlignTop)
        # compact spacing so more layers/markers fit before the legend needs its
        # own vertical scrollbar - the default style spacing (6px) is generous for
        # a list that can run to dozens of rows (every drawn layer, plus a row per
        # port/thermal source/boundary); a fully tight 1px read as too cramped, so
        # this splits the difference
        self.legend_layout.setSpacing(3)
        legend_widget = QWidget()
        legend_widget.setLayout(self.legend_layout)
        legend_scroll = QScrollArea()
        legend_scroll.setWidgetResizable(True)
        legend_scroll.setWidget(legend_widget)

        legend_all_btn = QPushButton("All")
        legend_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        legend_none_btn = QPushButton("None")
        legend_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        legend_buttons_layout = QHBoxLayout()
        legend_buttons_layout.addWidget(legend_all_btn)
        legend_buttons_layout.addWidget(legend_none_btn)

        # layer opacity (not applied to port highlights, which stay fully
        # opaque so they keep standing out) - lets overlapping layers below
        # show through instead of being fully covered by the one on top
        self._layer_items = []
        # highlight: which item (if any) to outline in white, keyed by layer
        # name for a drawn layer or by the marker's own tooltip text for a port/
        # thermal source/boundary (unique enough in practice, and there's no
        # other persistent per-marker identity to key on) - set either via
        # set_highlighted_layer() (MainWindow, when a layer is selected in the
        # Stackup Preview/Editor) or by clicking the item's own legend row
        # (_on_legend_layer_clicked() below). One shared highlight slot for
        # everything in this window - selecting a marker clears a selected
        # layer and vice versa. Persists across refresh().
        self._layer_items_by_name = {}
        # z-value the highlight should render at for a given key - below every
        # marker for a layer (so the hatch never covers a port/source/boundary
        # sitting on top of it), above a marker's own label for a marker (so
        # the hatch is actually visible instead of hidden under it)
        self._highlight_zvalue_by_name = {}
        self._highlighted_layer_name = None
        self._highlight_items = []
        self._highlight_zvalue = 0  # recomputed each refresh() from the layer count
        # legend rows for every selectable item - drawn layers and markers alike
        # (name/tooltip -> _ClickableLegendRow), so a click can select/deselect
        # it directly from Layout Preview's own legend, independent of the
        # Stackup Preview/Editor cross-window highlight below
        self._legend_layer_rows = {}
        # Layers-section name -> GDS layer number, for resolving a click there
        # into the layerSelected signal above (markers are never in this dict)
        self._layer_layernum_by_name = {}
        self._info_base_text = ""  # set by refresh(); _update_info_label() appends selection info
        self.opacity_label = QLabel()
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(DEFAULT_LAYER_OPACITY_PERCENT)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._on_opacity_changed(self.opacity_slider.value())

        legend_panel = QWidget()
        legend_panel.setMinimumWidth(120)
        legend_panel_layout = QVBoxLayout(legend_panel)
        legend_panel_layout.setContentsMargins(0, 0, 0, 0)
        legend_panel_layout.addLayout(legend_buttons_layout)
        legend_panel_layout.addWidget(self.opacity_label)
        legend_panel_layout.addWidget(self.opacity_slider)
        legend_panel_layout.addWidget(legend_scroll, 1)

        # QSplitter (not a plain layout) so the user can drag the divider to
        # resize the legend panel width - it was getting too tight for longer
        # layer/port names once direction/inactive tags were added to them
        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.addWidget(self.canvas)
        content_splitter.addWidget(legend_panel)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 0)
        content_splitter.setSizes([900, 200])
        # a drag can otherwise shrink a pane past its minimumWidth down to 0,
        # collapsing it entirely - keep the legend panel from disappearing
        content_splitter.setCollapsible(1, False)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.info_label, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        button_layout.addWidget(refresh_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        main_layout = QVBoxLayout()
        main_layout.addWidget(content_splitter, 1)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

        # Ctrl+C copies the canvas (the drawn layout, not the legend/buttons) to
        # the clipboard as an image - window-scoped (default QShortcut context)
        # so it fires regardless of which child widget currently has focus, since
        # nothing in this window has its own competing text-copy behavior
        QShortcut(QKeySequence.Copy, self).activated.connect(self._copy_canvas_to_clipboard)

        # Up/Down cycles the highlighted selection through the Layers section (not
        # markers) in the same top-to-bottom order shown in the legend, when a
        # layer is currently selected - see _layer_names_ordered/_cycle_highlighted_layer().
        # Plain Key_Up/Key_Down as a window-scoped QShortcut (not a keyPressEvent
        # override) reliably wins over the canvas/slider's own default arrow-key
        # handling (scrolling/value-stepping) regardless of which child has focus -
        # confirmed empirically: Qt's ambient shortcut system intercepts the key
        # before QGraphicsView/QSlider's own keyPressEvent ever sees it, unless a
        # widget explicitly opts out via ShortcutOverride (neither does here).
        self._layer_names_ordered = []  # top-to-bottom legend order, set in refresh()
        QShortcut(QKeySequence(Qt.Key_Up), self).activated.connect(
            lambda: self._cycle_highlighted_layer(-1))
        QShortcut(QKeySequence(Qt.Key_Down), self).activated.connect(
            lambda: self._cycle_highlighted_layer(1))

        self.refresh()

    def _copy_canvas_to_clipboard(self):
        QApplication.clipboard().setPixmap(self.canvas.grab())

    def _cycle_highlighted_layer(self, step):
        # only cycles an active Layers-section selection - a marker (port/source/
        # boundary) selection, or no selection at all, is left alone since
        # "the list of layers" doesn't include those
        if self._highlighted_layer_name not in self._layer_names_ordered:
            return
        index = self._layer_names_ordered.index(self._highlighted_layer_name)
        new_name = self._layer_names_ordered[(index + step) % len(self._layer_names_ordered)]
        self.set_highlighted_layer(new_name)
        self._notify_layer_selection_changed()

    def _clear_legend(self):
        self._checkboxes = []
        self._layer_items = []
        self._layer_items_by_name = {}
        self._highlight_zvalue_by_name = {}
        self._legend_layer_rows = {}
        self._layer_layernum_by_name = {}
        self._layer_names_ordered = []
        # the highlight items themselves were just destroyed by scene.clear()
        # in refresh() (called right before this) - drop the stale references,
        # but keep _highlighted_layer_name itself so it survives a refresh
        self._highlight_items = []
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_all_checked(self, checked):
        for checkbox in self._checkboxes:
            checkbox.setChecked(checked)

    def _on_opacity_changed(self, value):
        self.opacity_label.setText(f"Layer opacity: {value}%")
        for item in self._layer_items:
            item.setOpacity(value / 100.0)

    def set_highlighted_layer(self, name):
        """Outline (+ hatch-fill) every drawn shape for `name` in white - a
        drawn layer's name, or a marker's (port/thermal source/boundary) own
        tooltip text. Rendered above every regular layer but below every
        marker for a layer, or above a marker's own label for a marker (see
        _highlight_zvalue_by_name) - called by MainWindow when a layer is
        selected in the Stackup Preview/Editor (None/an unknown name clears
        it), and also by clicking any item's own legend row (see
        _on_legend_layer_clicked()) - either way this is the single source of
        truth for "what's highlighted right now" across the whole window.
        Persists across refresh() via self._highlighted_layer_name. Also
        updates info_label with the selection and its polygon count.
        """
        self._highlighted_layer_name = name
        scene = self.canvas.scene()
        for item in self._highlight_items:
            scene.removeItem(item)
        self._highlight_items = []
        highlight_zvalue = self._highlight_zvalue_by_name.get(name, self._highlight_zvalue)
        for polygon_item in self._layer_items_by_name.get(name, []):
            outline = QGraphicsPolygonItem(polygon_item.polygon())
            # diagonal hatch fill (built into Qt, no new dependency) in
            # addition to the outline - draws the eye even on small/thin
            # regions where a border alone is easy to miss
            outline.setBrush(QBrush(QColor(HIGHLIGHT_COLOR), Qt.BDiagPattern))
            pen = QPen(QColor(HIGHLIGHT_COLOR), HIGHLIGHT_WIDTH)
            pen.setCosmetic(True)  # stays a thin fixed-pixel line at any zoom
            outline.setPen(pen)
            outline.setZValue(highlight_zvalue)
            scene.addItem(outline)
            self._highlight_items.append(outline)
        self._update_info_label()
        self._update_legend_selection_styling()

    def _update_legend_selection_styling(self):
        # visually mark whichever legend row (if any) matches the current
        # highlight, so clicking a row to select/deselect it has obvious
        # feedback beyond the canvas outline itself
        for name, row in self._legend_layer_rows.items():
            row.setStyleSheet("background-color: palette(highlight);" if name == self._highlighted_layer_name else "")

    def _on_legend_layer_clicked(self, name):
        # click the already-selected layer's row again to deselect it, same
        # convention as the canvas/Stackup Preview highlight
        if self._highlighted_layer_name == name:
            self.set_highlighted_layer(None)
        else:
            self.set_highlighted_layer(name)
        self._notify_layer_selection_changed()

    def _notify_layer_selection_changed(self):
        # forward a Layers-section selection/deselection made directly here to
        # the Stackup Preview/Editor (MainWindow._forward_layout_selection_to_stackup,
        # the reverse of set_highlighted_layer() above) - a marker selection
        # never reaches here (see _add_legend_row(), only Layers-section rows
        # populate _layer_layernum_by_name), so it leaves the Stackup Preview/
        # Editor's own selection untouched
        name = self._highlighted_layer_name
        if name is None:
            self.layerSelected.emit(None)
            return
        layernum = self._layer_layernum_by_name.get(name)
        if layernum is None:
            return
        self.layerSelected.emit(layernum)

    def _update_info_label(self):
        text = self._info_base_text
        if self._highlighted_layer_name:
            count = len(self._highlight_items)
            text += (f"   Selected: {self._highlighted_layer_name} "
                     f"({count} polygon{'s' if count != 1 else ''})")
        self.info_label.setText(text)

    def _section_label(self, text):
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        # a little extra breathing room above each section, since the legend's
        # own row/list spacing is otherwise kept tight (see legend_layout above)
        label.setContentsMargins(0, 4, 0, 0)
        return label

    def _add_legend_row(self, color_name, text, group, layer_name=None):
        # layer_name is the row's highlight key when it's selectable - a drawn
        # layer's name, or a marker's own tooltip text (see the marker loop
        # below) - omit it for a non-selectable row (there currently isn't one)
        selectable = layer_name is not None
        row = _ClickableLegendRow() if selectable else QWidget()
        row_layout = QHBoxLayout(row)
        # small vertical margin (original was 2px, fully compact was 0) - keeps
        # rows short enough to fit more of them, without feeling cramped
        row_layout.setContentsMargins(2, 1, 2, 1)

        checkbox = QCheckBox()
        checkbox.setChecked(True)
        checkbox.toggled.connect(lambda checked, g=group: g.setVisible(checked))
        self._checkboxes.append(checkbox)
        row_layout.addWidget(checkbox)

        swatch = QLabel()
        swatch.setFixedSize(14, 14)
        swatch.setStyleSheet(f"background-color: {color_name}; border: 1px solid #000000;")
        row_layout.addWidget(swatch)

        label = QLabel(text)
        row_layout.addWidget(label, 1)

        if selectable:
            # let clicks on the swatch/label reach the row itself (the
            # checkbox is left alone so it still toggles visibility)
            swatch.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            row.setCursor(Qt.PointingHandCursor)
            row.setToolTip("Click to select/deselect this item")
            row.clicked.connect(lambda name=layer_name: self._on_legend_layer_clicked(name))
            self._legend_layer_rows[layer_name] = row

        self.legend_layout.addWidget(row)

    def _polygon_points(self, poly):
        return QPolygonF([QPointF(float(x), float(-y)) for x, y in zip(poly.pts_x, poly.pts_y)])

    def _polygon_has_area(self, poly):
        # a Z/-Z via port is often exported as a degenerate (zero-area) point
        # or sliver rather than a real drawn shape - a hatch-fill highlight on
        # that is inherently invisible (there's nothing to fill), so callers
        # use this to skip making such a marker selectable in the first place
        # rather than offering a highlight that can never actually show
        x, y = poly.pts_x, poly.pts_y
        n = len(x)
        area2 = 0.0
        for i in range(n):
            j = (i + 1) % n
            area2 += float(x[i]) * float(y[j]) - float(x[j]) * float(y[i])
        return abs(area2) > 1e-9

    def refresh(self):
        # check the live field text, not just MainWindow.metals_list below - that
        # can be stale (holding a *previous* successful load) if the XML field was
        # since changed to a path that doesn't exist, since read_XML() silently
        # no-ops on a missing file rather than clearing the old stackup data
        gdsfile = self._gds_override_path or self.MainWindow.file_tab.gds_file_edit.text()
        xmlfile = self.MainWindow.file_tab.XML_file_edit.text()
        if not os.path.isfile(gdsfile) or not os.path.isfile(xmlfile):
            QMessageBox.warning(self, "Error", "Load a GDSII file and XML stackup first")
            return

        if not self.MainWindow.file_tab.save_values():
            return  # save_values() already showed its own warning

        metals_list = self.MainWindow.metals_list
        dielectrics_list = self.MainWindow.dielectrics_list
        materials_list = self.MainWindow.materials_list
        if metals_list is None or dielectrics_list is None or materials_list is None:
            # both files exist but read_XML() couldn't parse the stackup - it
            # already showed a detailed QMessageBox.critical with the parse error
            return

        marker_dicts = self.MainWindow.get_layout_preview_markers()
        marker_by_layernum = {int(m["source_layernum"]): m for m in marker_dicts}

        saved_values = self.MainWindow.saved_values
        layernumbers = metals_list.getlayernumbers()
        layernumbers.extend(marker_by_layernum.keys())

        gds_path_to_read = self._gds_override_path or saved_values["GdsFile"]

        # reading/preprocessing the GDS and building every polygon item below
        # can take a while for a large layout - show a wait cursor for the
        # whole stretch, not just the read_gds() call, since building the
        # scene/legend afterward can itself be slow for a high polygon count.
        # processEvents() forces the cursor to actually paint before this
        # thread blocks on the (synchronous, no other yield point) work below.
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            captured_stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(captured_stdout):
                    allpolygons = gds_reader.read_gds(
                        gds_path_to_read, layernumbers,
                        cellname=saved_values["cellname"],
                        purposelist=saved_values["purpose"],
                        metals_list=metals_list,
                        preprocess=saved_values["preprocess_gds"],
                        merge_polygon_size=saved_values["merge_polygon_size"],
                        gds_boundary_layers=dielectrics_list.get_boundary_layers(),
                        mirror=False, offset_x=0, offset_y=0, layernumber_offset=0)
            except (Exception, SystemExit) as e:
                details = captured_stdout.getvalue().strip() or str(e)
                QMessageBox.critical(self, "Error", f"Could not read GDSII layout:\n\n{details}")
                return

            polygons_by_layer = {}
            for poly in allpolygons.polygons:
                polygons_by_layer.setdefault(poly.layernum, []).append(poly)

            scene = self.canvas.scene()
            scene.clear()
            self._clear_legend()

            # regular stackup layers, bottom (lowest zmin) to top - so later (higher)
            # layers are drawn last and are not hidden by lower ones underneath
            regular_layers = []
            for layernum, polys in polygons_by_layer.items():
                if layernum in marker_by_layernum:
                    continue
                metal = metals_list.getbylayernumber(layernum)
                regular_layers.append((metal, layernum, polys))
            regular_layers.sort(key=lambda entry: entry[0].zmin if entry[0] is not None else 0.0)
            # sits strictly above every regular layer's zValue (0..len-1) and
            # strictly below the marker tier (len+1 and up, see marker_zvalue
            # below) - the highlight must never cover a port/source/boundary
            self._highlight_zvalue = len(regular_layers)

            self.legend_layout.addWidget(self._section_label("Layers"))
            layer_legend_rows = []
            for zindex, (metal, layernum, polys) in enumerate(regular_layers):
                if metal is not None:
                    material = materials_list.get_by_name(metal.material)
                    color = _material_qcolor(material, metal.material)
                    name = metal.name
                else:
                    color = QColor(DEFAULT_LAYER_COLOR)
                    name = f"Layer {layernum}"
                self._layer_layernum_by_name[name] = layernum

                group = _VisibilityGroup()

                tooltip = f"{name} [{layernum}]"
                for poly in polys:
                    item = QGraphicsPolygonItem(self._polygon_points(poly))
                    item.setBrush(QBrush(color))
                    item.setPen(Qt.NoPen)
                    item.setToolTip(tooltip)
                    item.setAcceptHoverEvents(True)
                    item.setZValue(zindex)
                    item.setOpacity(self.opacity_slider.value() / 100.0)
                    scene.addItem(item)
                    group.add(item)
                    self._layer_items.append(item)
                    self._layer_items_by_name.setdefault(name, []).append(item)
                    self._highlight_zvalue_by_name[name] = self._highlight_zvalue

                zmin = metal.zmin if metal is not None else 0.0
                zmax = metal.zmax if metal is not None else 0.0
                layer_legend_rows.append((zmin, zmax, color.name(), tooltip, group, name))

            # legend lists layers top-to-bottom by z position, largest first (the
            # physically topmost layer at the top of the list) - sorted explicitly
            # here rather than just reversing the ascending draw-order list above
            # (needed there for correct on-canvas layering), so identical-zmin ties
            # break consistently by zmax instead of arbitrarily
            layer_legend_rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
            for zmin, zmax, color_name, tooltip, group, name in layer_legend_rows:
                self._add_legend_row(color_name, tooltip, group, layer_name=name)
            # same top-to-bottom order as the legend - see _cycle_highlighted_layer()
            self._layer_names_ordered = [name for *_, name in layer_legend_rows]
            self._update_legend_selection_styling()

            # marker shapes (EM ports / thermal sources / thermal boundaries),
            # always drawn on top of every regular layer, highlighted and always
            # labeled (not just on hover), grouped into their own legend sections
            present_groups = [name for name in MARKER_GROUP_ORDER
                               if any(m["group"] == name for m in marker_by_layernum.values())]
            marker_zvalue = len(regular_layers) + 1
            for group_name in present_groups:
                self.legend_layout.addWidget(self._section_label(group_name))
                for layernum, polys in polygons_by_layer.items():
                    marker = marker_by_layernum.get(layernum)
                    if marker is None or marker["group"] != group_name:
                        continue

                    kind = marker["kind"]
                    fill_color, outline_color = MARKER_STYLES[kind]
                    group = _VisibilityGroup()

                    if kind == "port":
                        portnumber = marker["portnumber"]
                        label_text = f"P{portnumber}"
                        tooltip = f"Port {portnumber} [{layernum}] {_signed_direction(marker.get('direction', ''))}"
                        if float(marker.get("voltage", 1)) == 0:
                            tooltip += " (inactive)"
                    elif kind == "source":
                        label_text = _format_value(marker["power"], "W")
                        tooltip = f"Source [{layernum}] {label_text}"
                    else:  # "boundary"
                        label_text = _format_value(marker["temp"], "K")
                        tooltip = f"Boundary [{layernum}] {label_text}"

                    for poly in polys:
                        item = QGraphicsPolygonItem(self._polygon_points(poly))
                        item.setBrush(QBrush(fill_color))
                        # cosmetic pen: stroke stays MARKER_OUTLINE_WIDTH device
                        # pixels regardless of canvas zoom, instead of scaling
                        # with it - what keeps a near-zero-width marker visible
                        marker_pen = QPen(QColor(outline_color), MARKER_OUTLINE_WIDTH)
                        marker_pen.setCosmetic(True)
                        item.setPen(marker_pen)
                        item.setToolTip(tooltip)
                        item.setAcceptHoverEvents(True)
                        item.setZValue(marker_zvalue)
                        scene.addItem(item)
                        group.add(item)
                        # keyed by tooltip (this marker's own, unique-in-practice
                        # label) rather than name - a marker has no other stable
                        # identity to select it by. Highlight z sits above this
                        # marker's own label tier (marker_zvalue+2, see below) so
                        # the hatch is actually visible instead of hidden under it.
                        # Skip a degenerate (zero-area) shape - typically a Z/-Z via
                        # port with no real drawn geometry - a hatch-fill highlight
                        # on it could never actually be visible
                        if self._polygon_has_area(poly):
                            self._layer_items_by_name.setdefault(tooltip, []).append(item)
                            self._highlight_zvalue_by_name[tooltip] = marker_zvalue + 3

                        center_x = (poly.xmin + poly.xmax) / 2
                        center_y = -(poly.ymin + poly.ymax) / 2

                        # a real marker polygon is often a thin sliver that all but
                        # disappears at normal zoom, so also mark its centroid with
                        # a fixed-pixel-size symbol - stays visible at any zoom
                        # level, same trick as the label below. In-plane ports
                        # (X/Y/-X/-Y) get a direction arrow; Z/-Z via ports get a
                        # polarity marker; thermal sources/boundaries have no
                        # direction to show, so just a plain marker dot.
                        if kind == "port":
                            direction_text = marker.get("direction", "")
                            vector = _direction_vector(direction_text)
                            if vector is not None:
                                arrow = QGraphicsPathItem(_arrow_path(*vector))
                                arrow.setPen(QPen(QColor(outline_color), 3))
                                marker_items = [arrow]
                            else:
                                negative = str(direction_text).strip().upper() == "-Z"
                                marker_items = _via_marker_items(negative)
                        else:
                            marker_items = _plain_marker_items(outline_color)

                        for marker_item in marker_items:
                            marker_item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                            marker_item.setPos(center_x, center_y)
                            marker_item.setZValue(marker_zvalue + 1)
                            marker_item.setToolTip(tooltip)
                            scene.addItem(marker_item)
                            group.add(marker_item)

                        text = QGraphicsSimpleTextItem(label_text)
                        font = QFont()
                        font.setBold(True)
                        text.setFont(font)
                        text.setBrush(QBrush(QColor("white")))
                        text.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
                        # setPos() below places the *item's own origin* (top-left of
                        # its bounding rect, not its center) at the marker location -
                        # use a local setTransform() to center the glyph on that
                        # origin first; it combines independently of the setPos()
                        # that follows
                        text_rect = text.boundingRect()
                        text.setTransform(QTransform.fromTranslate(-text_rect.width() / 2, -text_rect.height() / 2))
                        text.setPos(center_x, center_y)
                        # a *uniform* z-value across every marker (not just "above
                        # this marker's own symbol") - two nearby markers' items
                        # interleave by scene insertion order at equal z, so
                        # without this a later-drawn marker could cover an
                        # earlier one's label
                        text.setZValue(marker_zvalue + 2)
                        scene.addItem(text)
                        group.add(text)

                    # only selectable if at least one of its polygons actually has
                    # area to highlight (see _polygon_has_area()) - otherwise the
                    # row would look clickable but could never show anything
                    highlightable = tooltip in self._layer_items_by_name
                    self._add_legend_row(outline_color, tooltip, group,
                                          layer_name=tooltip if highlightable else None)

            self._info_base_text = (
                f"GDS: {os.path.basename(gds_path_to_read)}   "
                f"Cell: {saved_values['cellname'] or '(top cell)'}   "
                f"Purpose: {saved_values['purpose']}")

            # re-apply any active cross-window highlight - the polygon items it
            # outlines were just rebuilt from scratch above; this also refreshes
            # info_label (base text + selection, if any) via _update_info_label()
            self.set_highlighted_layer(self._highlighted_layer_name)

            rect = scene.itemsBoundingRect()
            if not rect.isEmpty():
                scene.setSceneRect(rect)
                self.canvas.fitInView(rect, Qt.KeepAspectRatio)
        finally:
            QApplication.restoreOverrideCursor()
