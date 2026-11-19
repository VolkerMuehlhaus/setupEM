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
gds_hierarchy_scan.py

Cheap GDS cell-hierarchy walkers (gdstk objects) that never call cell.flatten().
gds2palace's own gds_reader.read_gds() flattens the whole chosen cell (every
array/reference expanded into real, individual polygon objects) before doing
anything else - fine for actually building a model, but for a densely-arrayed
layout (fill patterns, via arrays) that flatten alone can take tens of
seconds, which is far too slow for something that only needs a yes/no or a
count. The functions here answer those questions by walking the
cell/reference tree directly instead, recursing into referenced cells
without expanding them, so cost scales with the (small) number of distinct
cells rather than the (potentially huge) number of instantiated copies.

open_gds_cell() and estimate_polygon_count() live in gds_prepare_for_EM's
gds_simplify_for_em module (shared with the gds2palace MCP server) and are
re-exported here, so callers keep using gds_hierarchy_scan.<name>.

No PySide6/setup_common/layout_preview dependency here - keeps this a leaf
module importable from both without any circular-import concern (setup_common.py
and layout_preview.py already need a deferred import between each other).
"""

from gds_simplify_for_em import open_gds_cell, estimate_polygon_count  # noqa: F401 (re-export)


def layers_present_in_range(gds_path, cellname, layer_min, layer_max, purposelist):
    """Set of GDS layer numbers in [layer_min, layer_max] with at least one
    polygon whose datatype is in purposelist (any datatype if purposelist is
    falsy), anywhere in the cell hierarchy - without flattening. Recursive,
    memoized-per-cell-name, set-union based: unlike estimate_polygon_count,
    presence needs no array columns*rows multiplication (N copies of a
    present layer is still just "present"), so plain and array references
    are treated identically here. Raises on open/resolve failure, same as
    open_gds_cell() (gds_prepare_for_EM >= 1.8: gdstk library and cell);
    callers wrap in their own try/except.
    """
    import gdstk

    library, cell = open_gds_cell(gds_path, cellname)
    wanted = set(range(layer_min, layer_max + 1))
    purposeset = set(int(p) for p in purposelist) if purposelist else None

    memo = {}

    def scan(c):
        if c.name in memo:
            return memo[c.name]
        found = set()
        for polygon in c.polygons:
            if polygon.layer in wanted and (purposeset is None or polygon.datatype in purposeset):
                found.add(polygon.layer)
        for ref in c.references:
            if found >= wanted:
                break  # already covers every requested layer - nothing left to gain
            if not isinstance(ref.cell, gdstk.Cell):
                continue  # cell missing from the file
            found |= scan(ref.cell)
        memo[c.name] = found
        return found

    return scan(cell)
