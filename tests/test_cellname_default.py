# The Cellname box: "(default: <cell>)" names the cell that an empty cell name stands for, the design cell that
# gds2palace's read_gds() reads (select_design_cell: the largest top-level cell, never KLayout's $$$CONTEXT_INFO$$$),
# and is still saved as "". ports_on_edge() looks at that cell too.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

import gdstk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM import piece_set  # noqa: E402
from setupEM.setup_common import (CELLNAME_DEFAULT_LABEL, cellname_default_label,  # noqa: E402
                                  cellname_for_display, cellname_from_display)


def multi_top(path):
    """$$$CONTEXT_INFO$$$ first and largest (no port), a small device cell placed nowhere (port on its right edge),
    the design with a port on its left edge (layer 201): each cell gives another answer. The old first-top-level
    choice depended on the reader's order (gdspy's even varied between runs)."""
    lib = gdstk.Library()
    lib.new_cell("$$$CONTEXT_INFO$$$").add(gdstk.rectangle((0, 0), (1000, 1000), layer=1))
    lib.new_cell("small_device").add(gdstk.rectangle((0, 0), (10, 10), layer=134),
                                     gdstk.rectangle((10, 4), (10, 6), layer=201))      # a port on its right edge
    design = lib.new_cell("DESIGN")
    design.add(gdstk.rectangle((0, -50), (300, 50), layer=134), gdstk.rectangle((0, -5), (0, 5), layer=201))
    lib.write_gds(str(path))
    return str(path)


def test_default_label_round_trip():
    label = cellname_default_label("DESIGN")
    assert label == "(default: DESIGN)"
    assert cellname_from_display(label) == ""
    assert cellname_from_display(CELLNAME_DEFAULT_LABEL) == ""
    assert cellname_from_display("DESIGN") == "DESIGN"
    assert cellname_for_display("") == CELLNAME_DEFAULT_LABEL
    assert cellname_default_label("") == CELLNAME_DEFAULT_LABEL


def test_ports_on_edge_reads_the_design_cell(tmp_path):
    path = multi_top(tmp_path / "multi.gds")
    assert gdstk.read_gds(path).top_level()[0].name != "DESIGN"
    assert piece_set.ports_on_edge(path, "", {201}) == {"xmin"}
