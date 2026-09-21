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

Cheap, pure-gdspy GDS cell-hierarchy walkers that never call cell.flatten().
gds2palace's own gds_reader.read_gds() flattens the whole chosen cell (every
array/reference expanded into real, individual polygon objects) before doing
anything else - fine for actually building a model, but for a densely-arrayed
layout (fill patterns, via arrays) that flatten alone can take tens of
seconds, which is far too slow for something that only needs a yes/no or a
count. The functions here answer those questions by walking the
cell/reference tree directly instead, recursing into referenced cells
without expanding them, so cost scales with the (small) number of distinct
cells rather than the (potentially huge) number of instantiated copies.

No PySide6/setup_common/layout_preview dependency here - keeps this a leaf
module importable from both without any circular-import concern (setup_common.py
and layout_preview.py already need a deferred import between each other).
"""

import gdspy


def open_gds_cell(gds_path, cellname):
    """Open gds_path and resolve the target cell: cellname if given and
    found, else the file's first top-level cell. Shared first step for
    every walker below. Raises on a bad file or a library with no top-level
    cell, same as gdspy itself would - callers already wrap their own call
    sites in a broad try/except.
    """
    library = gdspy.GdsLibrary(infile=gds_path)
    top_level = library.top_level()
    if not top_level:
        raise ValueError(f"no top-level cell in {gds_path}")
    cell = library.cells.get(cellname, top_level[0])
    return library, cell


def estimate_polygon_count(gds_path, cellname, layernumbers, purposelist):
    """Fast pre-check of how many polygons a full read_gds()-based load
    would end up processing, without flattening the GDS hierarchy. Walks
    the cell/reference tree directly: a plain CellReference contributes its
    referenced cell's own (memoized) count once, a CellArray contributes it
    columns*rows times - the same arithmetic flatten() would otherwise do
    by actually generating that many polygon objects. Returns None if the
    file can't even be opened here (the real read_gds() call is expected to
    surface that error properly instead).
    """
    try:
        library, cell = open_gds_cell(gds_path, cellname)
    except Exception:
        return None

    layernumbers = set(int(n) for n in layernumbers)
    purposeset = set(int(p) for p in purposelist) if purposelist else None

    memo = {}

    def count(c):
        if c.name in memo:
            return memo[c.name]
        total = 0
        for polygonset in c.polygons:
            for layer, datatype in zip(polygonset.layers, polygonset.datatypes):
                if int(layer) in layernumbers and (purposeset is None or int(datatype) in purposeset):
                    total += 1
        for ref in c.references:
            ref_cell = ref.ref_cell
            if isinstance(ref_cell, str):
                ref_cell = library.cells.get(ref_cell)
            if ref_cell is None:
                continue
            sub = count(ref_cell)
            total += sub * ref.columns * ref.rows if isinstance(ref, gdspy.CellArray) else sub
        memo[c.name] = total
        return total

    return count(cell)


def layers_present_in_range(gds_path, cellname, layer_min, layer_max, purposelist):
    """Set of GDS layer numbers in [layer_min, layer_max] with at least one
    polygon whose datatype is in purposelist (any datatype if purposelist is
    falsy), anywhere in the cell hierarchy - without flattening. Recursive,
    memoized-per-cell-name, set-union based: unlike estimate_polygon_count,
    presence needs no CellArray columns*rows multiplication (N copies of a
    present layer is still just "present"), so CellReference and CellArray
    are treated identically here. Raises on open/resolve failure, same as
    open_gds_cell(); callers wrap in their own try/except.
    """
    library, cell = open_gds_cell(gds_path, cellname)
    wanted = set(range(layer_min, layer_max + 1))
    purposeset = set(int(p) for p in purposelist) if purposelist else None

    memo = {}

    def scan(c):
        if c.name in memo:
            return memo[c.name]
        found = set()
        for polygonset in c.polygons:
            for layer, datatype in zip(polygonset.layers, polygonset.datatypes):
                layer = int(layer)
                if layer in wanted and (purposeset is None or int(datatype) in purposeset):
                    found.add(layer)
        for ref in c.references:
            if found >= wanted:
                break  # already covers every requested layer - nothing left to gain
            ref_cell = ref.ref_cell
            if isinstance(ref_cell, str):
                ref_cell = library.cells.get(ref_cell)
            if ref_cell is None:
                continue
            found |= scan(ref_cell)
        memo[c.name] = found
        return found

    return scan(cell)
