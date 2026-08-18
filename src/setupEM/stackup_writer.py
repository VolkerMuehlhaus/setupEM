########################################################################
#
# Copyright 2025 Volker Muehlhaus and IHP PDK Authors
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
Load/edit/save/validate stackup XML files (see XML_stackup_format.md) at the
xml.etree.ElementTree level, independent of any GUI toolkit.

This module is deliberately kept separate from gds2palace's util_stackup_reader.py:
the reader turns XML into the read-only object model (stackup_material,
dielectric_layer, metal_layer, ...) used by the rest of gds2palace, while this
module is for tools that need to load a file, edit it (materials/dielectrics/
layers), and write it back out - such as this GUI stackup editor - while leaving
any parts of the file they don't understand (e.g. Tables and XML comments)
completely untouched. It lives here in setupEM rather than in gds2palace because
setupEM is its only real consumer - gds2palace's own workflow only ever reads
stackup files, never edits/writes them.
"""

__version__ = "1.5.0"

import ast
import xml.etree.ElementTree as ET

from gds2palace import stackup_reader

VALID_MATERIAL_TYPES = ("Conductor", "Dielectric", "Semiconductor", "Resistor")
VALID_LAYER_TYPES = ("conductor", "via", "dielectric", "sheet")
VALID_DERIVED_OPERATIONS = ("AND", "OR", "XOR", "NOT", "SIZE")

# Attribute order to enforce on save for a <Layer>/<Dielectric> that has Reference set,
# so Reference/ReferenceEdge always read as "what this is positioned against" right next
# to the element's identity, ahead of the numeric Material/Layer#/Zmin/Zmax/etc. details -
# rather than landing wherever they happened to be set (typically last, since Reference is
# usually added to an element that already has its other attributes). Elements without
# Reference are left with whatever attribute order they already have.
_LAYER_REFERENCE_ATTR_ORDER = ("Name", "Type", "Reference", "ReferenceEdge", "Material", "Layer", "Zmin", "Zmax")
_DIELECTRIC_REFERENCE_ATTR_ORDER = ("Name", "Reference", "ReferenceEdge", "Material", "Thickness", "Zmin", "Zmax", "Boundary")


def _reorder_attributes(element, canonical_order):
  """Reorders element.attrib in place to match canonical_order; any attribute not listed
     there keeps its existing relative order, appended after the canonical ones (so an
     unexpected/future attribute is never silently dropped, just not specially placed).
  """
  remaining = dict(element.attrib)
  ordered = {}
  for key in canonical_order:
    if key in remaining:
      ordered[key] = remaining.pop(key)
  ordered.update(remaining)
  element.attrib.clear()
  element.attrib.update(ordered)


def _comment_preserving_parser():
  """XML parser that keeps <!-- comments --> as Comment nodes in the tree, instead of
     silently dropping them (the default xml.etree behavior), so a load/save round
     trip does not lose comments in sections this module never edits.
  """
  target = ET.TreeBuilder(insert_comments=True)
  return ET.XMLParser(target=target)


# -------------------- load / new / save --------------------

def load_stackup_tree(filename):
  """Load a stackup XML file into an editable ElementTree, preserving comments.
  Args:
      filename (string): path to the stackup XML file
  Returns:
      xml.etree.ElementTree.ElementTree
  """
  return ET.parse(filename, parser=_comment_preserving_parser())


def new_stackup_tree(length_unit="um", schema_version="2.0"):
  """Create a minimal empty stackup tree (empty Materials/Dielectrics/Layers), for
     starting a new stackup file from scratch in an editor.
  Returns:
      xml.etree.ElementTree.ElementTree
  """
  root = ET.Element("Stackup", {"schemaVersion": schema_version})
  ET.SubElement(root, "Materials")
  elayers = ET.SubElement(root, "ELayers", {"LengthUnit": length_unit})
  ET.SubElement(elayers, "Dielectrics")
  ET.SubElement(elayers, "Layers")
  return ET.ElementTree(root)


GENERATOR_COMMENT_PREFIX = "Created/modified using the XML Stackup Editor in"
DESCRIPTION_COMMENT_PREFIX = "File description:"
_HEADER_SEPARATOR_TEXT = "=" * 60


def _sanitize_comment_text(text):
  """XML comments may not contain '--' or end in '-'; make free-form user text
     safe to embed as a Comment node's .text without raising on write().
  """
  text = text.replace("--", "- -")
  return text.rstrip("-")


def _is_header_comment(comment_element):
  """True if this Comment node looks like one this module previously wrote as part
     of the header block (generator stamp / separator / file description), so a
     re-stamp can cleanly remove the old block before writing a fresh one.
  """
  text = (comment_element.text or "").strip()
  return (text.startswith(GENERATOR_COMMENT_PREFIX)
          or text.startswith(DESCRIPTION_COMMENT_PREFIX)
          or text == _HEADER_SEPARATOR_TEXT)


def stamp_header_comments(root, app_name, description=""):
  """Insert or update the header comment block at the very top of the stackup root:
     a fixed "created with" stamp, and - only if description is non-empty - a
     separator line followed by the user-supplied file description. Idempotent:
     replaces a previously-stamped header block instead of stacking a new one on
     every save; any other pre-existing comments/elements are left untouched.
  Args:
      root (xml.etree.ElementTree.Element): the <Stackup> root element
      app_name (string): name of the host application (e.g. "setupEM", "setupThermal")
      description (string): optional free-text description of the file
  """
  children = list(root)
  while children and children[0].tag is ET.Comment and _is_header_comment(children[0]):
    root.remove(children[0])
    children = list(root)

  nodes = [ET.Comment(f" {_sanitize_comment_text(GENERATOR_COMMENT_PREFIX + ' ' + app_name)} ")]
  description = _sanitize_comment_text((description or "").strip())
  if description:
    nodes.append(ET.Comment(f" {_HEADER_SEPARATOR_TEXT} "))
    nodes.append(ET.Comment(f" {DESCRIPTION_COMMENT_PREFIX} {description} "))

  for i, node in enumerate(nodes):
    root.insert(i, node)


def get_file_description(root):
  """Return the free-text file description previously stamped by the editor (see
     stamp_header_comments), or "" if none is present.
  Args:
      root (xml.etree.ElementTree.Element): the <Stackup> root element
  """
  for child in root:
    if child.tag is ET.Comment:
      text = (child.text or "").strip()
      if text.startswith(DESCRIPTION_COMMENT_PREFIX):
        return text[len(DESCRIPTION_COMMENT_PREFIX):].strip()
  return ""


REFERENCE_FORMAT_COMMENT_PREFIX = "Reference-relative positioning requires gds2palace util_stackup_reader.py version"


def stamp_reference_format_comment(root, min_reader_version):
  """Insert or update a comment noting the minimum gds2palace reader version needed to
     correctly resolve this file's Reference-relative positioning. Meant to be called once,
     right after a Dielectric/Layer set actually starts using Reference (e.g. by "Convert to
     Reference position format" in the Stackup Editor) - not on every save, since it's a
     one-time fact about the file's content, not something that needs refreshing like the
     generator/description stamp. Idempotent: replaces a previous stamp of this same comment
     rather than stacking a new one if called again (e.g. re-running the conversion).
  Args:
      root (xml.etree.ElementTree.Element): the <Stackup> root element
      min_reader_version (string): util_stackup_reader.__version__ of the reader used to
        perform the conversion, taken as the minimum version able to read the result back
  """
  for child in list(root):
    if child.tag is ET.Comment and (child.text or "").strip().startswith(REFERENCE_FORMAT_COMMENT_PREFIX):
      root.remove(child)

  # goes right after the existing header block (generator stamp / description, if any) -
  # stamp_header_comments() only ever touches its own contiguous run at index 0, so
  # inserting immediately after it keeps this comment from being disturbed by a later re-save
  insert_index = 0
  for child in root:
    if child.tag is ET.Comment:
      insert_index += 1
    else:
      break
  root.insert(insert_index, ET.Comment(f" {REFERENCE_FORMAT_COMMENT_PREFIX} {min_reader_version} or newer "))


VARIABLES_FORMAT_COMMENT_PREFIX = "<Variables>/\"=\" expressions require gds2palace util_stackup_reader.py version"


def stamp_variables_format_comment(root, min_reader_version):
  """Insert or update a comment noting the minimum gds2palace reader version needed to
     correctly resolve this file's <Variables>/"="-expressions. Same shape and placement
     as stamp_reference_format_comment() - see that function's docstring.
  Args:
      root (xml.etree.ElementTree.Element): the <Stackup> root element
      min_reader_version (string): util_stackup_reader.__version__ of the reader that
        introduced this feature, taken as the minimum version able to read the result back
  """
  for child in list(root):
    if child.tag is ET.Comment and (child.text or "").strip().startswith(VARIABLES_FORMAT_COMMENT_PREFIX):
      root.remove(child)

  insert_index = 0
  for child in root:
    if child.tag is ET.Comment:
      insert_index += 1
    else:
      break
  root.insert(insert_index, ET.Comment(f" {VARIABLES_FORMAT_COMMENT_PREFIX} {min_reader_version} or newer "))


def _uses_variables_or_expressions(root):
  """True if this stackup's current content actually needs <Variables>/"="-expression
     support: any <Variable> declared, or any attribute value anywhere in the tree starting
     with "=" (covers a pure-literal expression like Zmax="=1+2" with no <Variables> section
     at all).
  """
  variables_el = get_variables_element(root)
  if variables_el is not None and variables_el.findall("Variable"):
    return True
  for element in root.iter():
    for value in element.attrib.values():
      if isinstance(value, str) and value.startswith("="):
        return True
  return False


def required_schema_version(root):
  """The minimum schemaVersion this stackup's current content actually needs: "3.1" if it
     uses <Variables>/"="-expressions (see _uses_variables_or_expressions()), else "3.0" if
     any Dielectric or Layer uses Reference-relative positioning (the feature that bumped the
     format before that - see util_stackup_reader.SUPPORTED_SCHEMA_VERSION), "2.0" otherwise.
     Used by save_stackup_tree() to catch schemaVersion="2.0"/"3.0" silently becoming a lie
     about the file's actual content - e.g. a Dielectric/Layer gaining a Reference attribute
     through some path other than "Convert to Reference position format" (which already sets
     schemaVersion itself), such as the Stackup Editor's one-time "make implicit dielectric
     stacking explicit?" offer at save time, or a Variable/expression added directly via the
     Variables tab.
  Args:
      root (xml.etree.ElementTree.Element): the <Stackup> root element
  Returns:
      string: "2.0", "3.0", or "3.1"
  """
  if _uses_variables_or_expressions(root):
    return "3.1"
  dielectrics_el = get_dielectrics_element(root)
  if dielectrics_el is not None and any(el.get("Reference") for el in dielectrics_el.findall("Dielectric")):
    return "3.0"
  layers_el = get_layers_element(root)
  if layers_el is not None and any(el.get("Reference") for el in layers_el.findall("Layer")):
    return "3.0"
  return "2.0"


def _sort_layers_by_resulting_zmin (root):
  """Reorders <Layer> children within <Layers> by resulting (resolved absolute) Zmin,
     highest first - matching how <Dielectric> already reads top-to-bottom in the file.
     Uses the reader to resolve Reference-based offsets into real absolute z, since a
     Reference-based Layer's own Zmin attribute is just an offset relative to whatever it
     references, not a meaningful sort key by itself. Purely a save-time presentation
     choice (file order has no semantic meaning to the reader); silently leaves the
     current order untouched if the data can't be fully resolved right now (e.g. a
     mid-edit invalid state) - sort order isn't worth blocking a save over.

     Any comment in <Layers> is moved to the very beginning of the section (ahead of the
     optional <Substrate Offset=".../">, ahead of every <Layer>) rather than left in its
     original slot: sorting means whichever <Layer> a comment used to introduce may no
     longer sit next to it, so leaving it in place would misleadingly suggest it still
     describes whatever ended up there instead.
  """
  layers_el = get_layers_element(root)
  if layers_el is None:
    return
  layer_elements = layers_el.findall("Layer")
  if len(layer_elements) < 2:
    return

  try:
    _, _, metals_list = stackup_reader.parse_substrate(root)
  except (Exception, SystemExit):
    return

  zmin_by_name = {metal.name: metal.zmin for metal in metals_list.metals}
  sorted_layers = sorted(layer_elements, key=lambda el: zmin_by_name.get(el.get("Name"), float("-inf")), reverse=True)

  children = list(layers_el)
  comments = [child for child in children if child.tag is ET.Comment]
  substrate_el = layers_el.find("Substrate")
  new_children = comments + ([substrate_el] if substrate_el is not None else []) + sorted_layers

  for child in children:
    layers_el.remove(child)
  for child in new_children:
    layers_el.append(child)


def save_stackup_tree(tree, filename):
  """Write a stackup tree back to disk with consistent indentation.

  Note: this re-serializes the whole document, so exact original whitespace and
  attribute order may change (any Reference-using Layer/Dielectric has its attribute
  order deliberately normalized - see _reorder_attributes()), and <Layer> entries are
  reordered by resulting Zmin, descending - see _sort_layers_by_resulting_zmin(). Comments
  and all element content are preserved.

  Also self-corrects schemaVersion="2.0"/"3.0" up to "3.0"/"3.1" if the content now needs it
  (see required_schema_version()) - every write goes through here, so this is the one place
  that reliably catches it regardless of which code path added the Reference attribute or
  the first <Variable>/"="-expression. Deliberately one-directional: never downgrades a
  higher version back down even if nothing currently uses that version's feature, since
  that's a content decision ("Convert to legacy format" makes it explicitly for Reference),
  not just a version-string correction.
  Args:
      tree (xml.etree.ElementTree.ElementTree): tree to write, as returned by
        load_stackup_tree() or new_stackup_tree()
      filename (string): path to write to
  """
  root = tree.getroot()

  needed = required_schema_version(root)
  if needed == "3.1" and root.get("schemaVersion") != "3.1":
    root.set("schemaVersion", "3.1")
    stamp_variables_format_comment(root, stackup_reader.__version__)
  elif needed == "3.0" and root.get("schemaVersion") != "3.0":
    root.set("schemaVersion", "3.0")
    stamp_reference_format_comment(root, stackup_reader.__version__)

  layers_el = get_layers_element(root)
  if layers_el is not None:
    for layer_el in layers_el.findall("Layer"):
      if layer_el.get("Reference"):
        _reorder_attributes(layer_el, _LAYER_REFERENCE_ATTR_ORDER)

  dielectrics_el = get_dielectrics_element(root)
  if dielectrics_el is not None:
    for dielectric_el in dielectrics_el.findall("Dielectric"):
      if dielectric_el.get("Reference"):
        _reorder_attributes(dielectric_el, _DIELECTRIC_REFERENCE_ATTR_ORDER)

  _sort_layers_by_resulting_zmin(root)

  ET.indent(tree, space="  ")
  tree.write(filename, xml_declaration=True, encoding="UTF-8")


# -------------------- structural accessors --------------------

def get_materials_element(root):
  return root.find("Materials")


def get_dielectrics_element(root):
  return root.find("ELayers/Dielectrics")


def get_layers_element(root):
  return root.find("ELayers/Layers")


def get_substrate_offset_element(root):
  layers_el = get_layers_element(root)
  if layers_el is None:
    return None
  return layers_el.find("Substrate")


def get_derived_layers_element(root, create=False):
  """<DerivedLayers> is optional and, unlike Dielectrics/Layers, may not exist yet.
  Args:
      create (bool): if True and the element is missing, create (and return) it
  """
  elayers = root.find("ELayers")
  derived_layers_el = elayers.find("DerivedLayers") if elayers is not None else None
  if derived_layers_el is None and create and elayers is not None:
    derived_layers_el = ET.SubElement(elayers, "DerivedLayers")
  return derived_layers_el


def get_variables_element(root, create=False):
  """<Variables> is optional and, unlike Dielectrics/Layers, may not exist yet - but unlike
     <DerivedLayers> (appended at the end of <ELayers> when first created), it must be the
     first child of <Stackup> whenever present.
  Args:
      create (bool): if True and the element is missing, create (and insert at index 0) it
  """
  variables_el = root.find("Variables")
  if variables_el is None and create:
    variables_el = ET.Element("Variables")
    root.insert(0, variables_el)
  return variables_el


# -------------------- Material --------------------

def add_material(root, **attrs):
  """Append a new <Material> element. Keyword args become attributes (None/"" skipped).
  Returns:
      xml.etree.ElementTree.Element: the new Material element
  """
  materials_el = get_materials_element(root)
  el = ET.SubElement(materials_el, "Material")
  for key, value in attrs.items():
    if value is not None and value != "":
      el.set(key, str(value))
  return el


def remove_material(root, element):
  get_materials_element(root).remove(element)


# -------------------- Dielectric --------------------

def add_dielectric(root, index=None, **attrs):
  """Insert a new <Dielectric> element. Order in <Dielectrics> is top-to-bottom and
     meaningful, so callers can pass index to control where it lands (default: end).
  Returns:
      xml.etree.ElementTree.Element: the new Dielectric element
  """
  dielectrics_el = get_dielectrics_element(root)
  el = ET.Element("Dielectric")
  for key, value in attrs.items():
    if value is not None and value != "":
      el.set(key, str(value))
  if index is None or index >= len(dielectrics_el):
    dielectrics_el.append(el)
  else:
    dielectrics_el.insert(index, el)
  return el


def remove_dielectric(root, element):
  get_dielectrics_element(root).remove(element)


def move_dielectric(root, element, direction):
  """Move a Dielectric element within its parent, to reorder the stack.
  Args:
      element (xml.etree.ElementTree.Element): the Dielectric element to move
      direction (int): -1 to move earlier (up), +1 to move later (down)
  """
  dielectrics_el = get_dielectrics_element(root)
  children = list(dielectrics_el)
  index = children.index(element)
  new_index = index + direction
  if 0 <= new_index < len(children):
    dielectrics_el.remove(element)
    dielectrics_el.insert(new_index, element)


# -------------------- Layer / Substrate offset --------------------

def add_layer(root, **attrs):
  """Append a new <Layer> element. Keyword args become attributes (None/"" skipped).
  Returns:
      xml.etree.ElementTree.Element: the new Layer element
  """
  layers_el = get_layers_element(root)
  el = ET.SubElement(layers_el, "Layer")
  for key, value in attrs.items():
    if value is not None and value != "":
      el.set(key, str(value))
  return el


def remove_layer(root, element):
  get_layers_element(root).remove(element)


def set_substrate_offset(root, value):
  """Add, update, or remove the single optional <Substrate Offset="..."/> element.
  Args:
      value: new offset (numeric or numeric string), or None/0 to remove the element
  Returns:
      xml.etree.ElementTree.Element or None: the Substrate element, or None if removed
  """
  layers_el = get_layers_element(root)
  existing = layers_el.find("Substrate")

  if value is None or value == "" or float(value) == 0:
    if existing is not None:
      layers_el.remove(existing)
    return None

  if existing is None:
    existing = ET.Element("Substrate")
    layers_el.insert(0, existing)
  existing.set("Offset", str(value))
  return existing


# -------------------- DerivedLayer --------------------

def add_derived_layer(root, **attrs):
  """Append a new <DerivedLayer> element, creating <DerivedLayers> if this is the
     first one. Keyword args become attributes (None/"" skipped); use set_operands()
     separately to add its <Operand> children.
  Returns:
      xml.etree.ElementTree.Element: the new DerivedLayer element
  """
  derived_layers_el = get_derived_layers_element(root, create=True)
  el = ET.SubElement(derived_layers_el, "DerivedLayer")
  for key, value in attrs.items():
    if value is not None and value != "":
      el.set(key, str(value))
  return el


def remove_derived_layer(root, element):
  """Remove a <DerivedLayer> element, and drop the now-empty <DerivedLayers>
  container too if that was the last one (keeps a from-scratch file clean).
  """
  derived_layers_el = get_derived_layers_element(root)
  if derived_layers_el is None:
    return
  derived_layers_el.remove(element)
  if len(derived_layers_el) == 0:
    root.find("ELayers").remove(derived_layers_el)


# -------------------- Variable --------------------

def add_variable(root, **attrs):
  """Append a new <Variable> element, creating <Variables> (as the first child of <Stackup>)
     if this is the first one. Keyword args become attributes (None/"" skipped).
  Returns:
      xml.etree.ElementTree.Element: the new Variable element
  """
  variables_el = get_variables_element(root, create=True)
  el = ET.SubElement(variables_el, "Variable")
  for key, value in attrs.items():
    if value is not None and value != "":
      el.set(key, str(value))
  return el


def remove_variable(root, element):
  """Remove a <Variable> element, and drop the now-empty <Variables> container too if that
  was the last one (keeps a from-scratch file clean).
  """
  variables_el = get_variables_element(root)
  if variables_el is None:
    return
  variables_el.remove(element)
  if len(variables_el) == 0:
    root.remove(variables_el)


def get_operand_layers(element):
  """Layer numbers (as strings, in document order) of a DerivedLayer's <Operand> children."""
  return [operand.get("Layer") for operand in element.findall("Operand")]


def set_operands(element, layer_numbers):
  """Replace a DerivedLayer element's <Operand> children with one per given layer
  number, in order - order matters for Operation="NOT" (first operand minus the rest).
  Args:
      layer_numbers (list of str/int): GDSII or other-DerivedLayer layer numbers
  """
  for existing in element.findall("Operand"):
    element.remove(existing)
  for layernum in layer_numbers:
    ET.SubElement(element, "Operand", {"Layer": str(layernum)})


# -------------------- validation --------------------

def _is_float(value):
  try:
    float(value)
    return True
  except (TypeError, ValueError):
    return False


def _is_int(value):
  try:
    int(value)
    return True
  except (TypeError, ValueError):
    return False


def _is_expression(value):
  """True if value is an attribute string starting with "=" - the marker for a <Variables>
     expression (see XML_stackup_format.md's <Variables> section). Any such value is allowed
     wherever a plain literal is, so every numeric/integer check below needs to recognize and
     validate this case instead of just rejecting it as non-numeric.
  """
  return isinstance(value, str) and value.startswith("=")


def _expression_problem(value, known_variable_names):
  """Validate a "="-prefixed expression's syntax and that every bare identifier it references
     is a known Variable name. Reimplemented here (ast-based) rather than importing
     util_stackup_reader.py's private _eval_expression()/_expression_names() across the
     package boundary - this module is deliberately independent of the reader's internals
     (see the module docstring) and mirrors its requirements instead.
  Args:
      value (string): the raw attribute value, assumed to already start with "="
      known_variable_names (set of string): declared <Variable> Names to check against
  Returns:
      string or None: a human-readable problem description, or None if valid
  """
  try:
    tree = ast.parse(value[1:], mode="eval")
  except SyntaxError as e:
    return f"invalid expression syntax ({e})"
  names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
  undefined = sorted(name for name in names if name not in known_variable_names)
  if undefined:
    return f"references undefined variable(s) {undefined}"
  return None


def _bare_variable_name_hint(value, known_variable_names):
  """If value is exactly a known Variable's Name (missing the leading "=" that would make it
     an actual reference - the single most likely reason a numeric/integer check fails right
     after someone defines a Variable), return a hint suggesting the fix; else "".
  """
  if value in known_variable_names:
    return f' (did you mean "={value}" to reference the Variable?)'
  return ""


def _numeric_problem(value, known_variable_names):
  """None if value is a valid float literal or a valid "="-expression (see
     _expression_problem()); else a human-readable problem string. Use this instead of a bare
     _is_float() for any numeric attribute, now that "="-expressions are allowed everywhere.
  """
  if _is_expression(value):
    return _expression_problem(value, known_variable_names)
  if not _is_float(value):
    return "not a number" + _bare_variable_name_hint(value, known_variable_names)
  return None


def _int_problem(value, known_variable_names):
  """Like _numeric_problem(), but for integer (GDSII layer number) attributes. A valid
     expression is only a syntax/reference-level pass here - the writer can't evaluate values
     it doesn't have real numbers for yet mid-edit, so a genuinely non-integer expression
     result is only caught later, when the reader's resolve_int_attr() actually resolves it.
  """
  if _is_expression(value):
    return _expression_problem(value, known_variable_names)
  if not _is_int(value):
    return "not an integer" + _bare_variable_name_hint(value, known_variable_names)
  return None


def validate_stackup(root):
  """Validate Variables/Materials/Dielectrics/Layers/DerivedLayers against the rules in
     XML_stackup_format.md. Tables is intentionally not checked here (not editable yet).
  Args:
      root (xml.etree.ElementTree.Element): root <Stackup> element
  Returns:
      list of str: human-readable problems found, empty if the file is valid
  """
  errors = []

  # Variables are validated first, and their names collected into known_variable_names, since
  # every other section's attributes may reference one via a "="-prefixed expression - mirrors
  # the reader's own resolution order (util_stackup_reader.py's parse_substrate() resolves
  # <Variables> before anything else).
  variables_el = get_variables_element(root)
  variable_names = []
  variable_raw_values = {}
  if variables_el is not None:
    for el in variables_el.findall("Variable"):
      name = el.get("Name")
      value = el.get("Value")
      vtype = el.get("Type")
      label = name or "<unnamed variable>"

      if not name:
        errors.append("Variable is missing required attribute 'Name'")
      elif name in variable_names:
        # gap in util_stackup_reader.py itself (variables_list.get_by_name() silently returns
        # the first match) - checked here regardless, as additive safety
        errors.append(f"Duplicate variable Name '{name}'")
      else:
        variable_names.append(name)

      if value is None or value == "":
        errors.append(f"Variable '{label}' is missing required attribute 'Value'")
      elif name:
        variable_raw_values[name] = value

      if vtype is not None and vtype != "" and vtype.lower() not in ("number", "string"):
        errors.append(f"Variable '{label}' has invalid Type '{vtype}' (must be number or string)")

  known_variable_names = set(variable_names)

  for name, value in variable_raw_values.items():
    if _is_expression(value):
      problem = _expression_problem(value, known_variable_names)
      if problem:
        errors.append(f"Variable '{name}' has invalid Value='{value}' ({problem})")

  # circular Variable -> Variable dependency check (repeat-until-no-progress, same approach
  # as the Dielectric/Layer Reference cycle checks below)
  variable_dependency = {}
  for name, value in variable_raw_values.items():
    if _is_expression(value):
      try:
        expr_tree = ast.parse(value[1:], mode="eval")
      except SyntaxError:
        continue  # already reported above
      deps = {node.id for node in ast.walk(expr_tree) if isinstance(node, ast.Name)} & known_variable_names
      if deps:
        variable_dependency[name] = deps

  resolved_variable_names = set(variable_names) - set(variable_dependency.keys())
  remaining_variable_deps = dict(variable_dependency)
  progress = True
  while progress and remaining_variable_deps:
    progress = False
    for name, deps in list(remaining_variable_deps.items()):
      if deps <= resolved_variable_names:
        resolved_variable_names.add(name)
        del remaining_variable_deps[name]
        progress = True
  if remaining_variable_deps:
    errors.append(f"Circular Variable reference detected among: {sorted(remaining_variable_deps.keys())}")

  materials_el = get_materials_element(root)
  material_names = []
  if materials_el is not None:
    for el in materials_el.findall("Material"):
      name = el.get("Name")
      mtype = el.get("Type")
      label = name or "<unnamed material>"

      if not name:
        errors.append("Material is missing required attribute 'Name'")
      elif name in material_names:
        errors.append(f"Duplicate material Name '{name}'")
      else:
        material_names.append(name)

      if not mtype:
        errors.append(f"Material '{label}' is missing required attribute 'Type'")
      elif mtype.upper() not in [t.upper() for t in VALID_MATERIAL_TYPES]:
        errors.append(f"Material '{label}' has invalid Type '{mtype}' (must be one of {VALID_MATERIAL_TYPES})")

      for attr in ("Permittivity", "DielectricLossTangent", "Conductivity", "Rs", "Density", "ThermalConductivity"):
        value = el.get(attr)
        if value is not None and value != "":
          problem = _numeric_problem(value, known_variable_names)
          if problem:
            errors.append(f"Material '{label}' has invalid {attr}='{value}' ({problem})")

  dielectrics_el = get_dielectrics_element(root)
  dielectric_names = []
  if dielectrics_el is not None:
    dielectric_elements = dielectrics_el.findall("Dielectric")

    # collect all dielectric names up front (order in <Dielectrics> is meaningful for implicit
    # stacking, but a Reference may still point at a Dielectric defined later in the file)
    for el in dielectric_elements:
      name = el.get("Name")
      if not name:
        continue
      if name in dielectric_names:
        errors.append(f"Duplicate dielectric Name '{name}'")
      else:
        dielectric_names.append(name)

    for el in dielectric_elements:
      name = el.get("Name")
      material = el.get("Material")
      label = name or "<unnamed dielectric>"

      if not name:
        errors.append("Dielectric is missing required attribute 'Name'")

      if not material:
        errors.append(f"Dielectric '{label}' is missing required attribute 'Material'")
      elif material not in material_names:
        errors.append(f"Dielectric '{label}' references undefined Material '{material}'")

      thickness = el.get("Thickness")
      zmin = el.get("Zmin")
      zmax = el.get("Zmax")
      has_thickness = thickness is not None and thickness != ""
      has_zmin = zmin is not None and zmin != ""
      has_zmax = zmax is not None and zmax != ""
      reference = el.get("Reference")

      if reference:
        # Reference set: Zmin (optional, default 0) and Zmax (optional, default
        # Zmin+Thickness) are offsets - unlike absolute mode, Zmin alone isn't required,
        # but something has to size the dielectric (Zmax or Thickness)
        if not has_zmax and not has_thickness:
          errors.append(f"Dielectric '{label}' has Reference set but needs either Zmax or Thickness to size it")
        if has_zmin:
          problem = _numeric_problem(zmin, known_variable_names)
          if problem:
            errors.append(f"Dielectric '{label}' has invalid Zmin='{zmin}' ({problem})")
        if has_zmax:
          problem = _numeric_problem(zmax, known_variable_names)
          if problem:
            errors.append(f"Dielectric '{label}' has invalid Zmax='{zmax}' ({problem})")
        if has_thickness:
          problem = _numeric_problem(thickness, known_variable_names)
          if problem:
            errors.append(f"Dielectric '{label}' has invalid Thickness='{thickness}' ({problem})")

        if reference not in dielectric_names:
          errors.append(f"Dielectric '{label}' has Reference '{reference}' which matches no Dielectric")

        reference_edge = el.get("ReferenceEdge")
        if reference_edge is not None and reference_edge != "" and reference_edge.upper() not in ("TOP", "BOTTOM"):
          errors.append(f"Dielectric '{label}' has invalid ReferenceEdge '{reference_edge}' (must be Top or Bottom)")
      else:
        has_zminmax = has_zmin and has_zmax
        if not has_thickness and not has_zminmax:
          errors.append(f"Dielectric '{label}' needs either Thickness or both Zmin and Zmax")
        if has_thickness:
          problem = _numeric_problem(thickness, known_variable_names)
          if problem:
            errors.append(f"Dielectric '{label}' has invalid Thickness='{thickness}' ({problem})")
        if has_zminmax:
          problem = _numeric_problem(zmin, known_variable_names)
          if problem:
            errors.append(f"Dielectric '{label}' has invalid Zmin='{zmin}' ({problem})")
          problem = _numeric_problem(zmax, known_variable_names)
          if problem:
            errors.append(f"Dielectric '{label}' has invalid Zmax='{zmax}' ({problem})")

      boundary = el.get("Boundary")
      if boundary is not None and boundary != "":
        problem = _int_problem(boundary, known_variable_names)
        if problem:
          errors.append(f"Dielectric '{label}' has invalid Boundary='{boundary}' ({problem})")

    # circular Dielectric -> Dielectric Reference check (Reference only ever targets another
    # Dielectric, so - unlike Layer's Reference - there's no ambiguous-namespace case here)
    dielectric_reference_target = {}
    for el in dielectric_elements:
      name = el.get("Name")
      reference = el.get("Reference")
      if name and reference and reference in dielectric_names:
        dielectric_reference_target[name] = reference

    resolved_dielectric_names = set(dielectric_names) - set(dielectric_reference_target.keys())
    remaining_dielectric_refs = dict(dielectric_reference_target)
    progress = True
    while progress and remaining_dielectric_refs:
      progress = False
      for name, target in list(remaining_dielectric_refs.items()):
        if target in resolved_dielectric_names:
          resolved_dielectric_names.add(name)
          del remaining_dielectric_refs[name]
          progress = True
    if remaining_dielectric_refs:
      errors.append(f"Circular Dielectric Reference detected among: {sorted(remaining_dielectric_refs.keys())}")

  layers_el = get_layers_element(root)
  layer_numbers = set()
  layer_names = []
  if layers_el is not None:
    layer_elements = layers_el.findall("Layer")

    # collect all layer names up front (order in <Layers> is not meaningful, so a
    # Layer's Reference may point to another Layer defined later in the file)
    for el in layer_elements:
      name = el.get("Name")
      if not name:
        continue
      if name in layer_names:
        errors.append(f"Duplicate layer Name '{name}'")
      else:
        layer_names.append(name)

    for el in layer_elements:
      name = el.get("Name")
      ltype = el.get("Type")
      material = el.get("Material")
      zmin = el.get("Zmin")
      zmax = el.get("Zmax")
      layernum = el.get("Layer")
      label = name or "<unnamed layer>"
      if layernum is not None and _is_int(layernum):
        layer_numbers.add(int(layernum))

      if not name:
        errors.append("Layer is missing required attribute 'Name'")
      if not ltype:
        errors.append(f"Layer '{label}' is missing required attribute 'Type'")
      elif ltype.upper() not in [t.upper() for t in VALID_LAYER_TYPES]:
        errors.append(f"Layer '{label}' has invalid Type '{ltype}' (must be one of {VALID_LAYER_TYPES})")

      if not material:
        errors.append(f"Layer '{label}' is missing required attribute 'Material'")
      elif material not in material_names:
        errors.append(f"Layer '{label}' references undefined Material '{material}'")

      if zmin is None or zmin == "":
        errors.append(f"Layer '{label}' is missing required attribute 'Zmin'")
      else:
        problem = _numeric_problem(zmin, known_variable_names)
        if problem:
          errors.append(f"Layer '{label}' has invalid Zmin='{zmin}' ({problem})")

      if zmax is None or zmax == "":
        errors.append(f"Layer '{label}' is missing required attribute 'Zmax'")
      else:
        problem = _numeric_problem(zmax, known_variable_names)
        if problem:
          errors.append(f"Layer '{label}' has invalid Zmax='{zmax}' ({problem})")

      if layernum is None or layernum == "":
        errors.append(f"Layer '{label}' is missing required attribute 'Layer'")
      else:
        problem = _int_problem(layernum, known_variable_names)
        if problem:
          errors.append(f"Layer '{label}' has invalid Layer='{layernum}' ({problem})")

      # sheet-thickness check only applies when Zmin/Zmax are both concrete literals - an
      # expression-based Zmin/Zmax can't be compared without resolving it (see
      # util_stackup_reader.py's metal_layer._finalize_type_flags() for the authoritative check)
      if ltype and zmin is not None and zmax is not None and _is_float(zmin) and _is_float(zmax):
        is_zero_thickness = float(zmin) == float(zmax)
        if ltype.upper() == "SHEET" and not is_zero_thickness:
          errors.append(f"Layer '{label}' has Type=\"sheet\" but Zmax != Zmin (sheet layers must have zero thickness)")

      reference = el.get("Reference")
      if reference:
        dielectric_match = reference in dielectric_names
        layer_match = reference in layer_names
        if dielectric_match and layer_match:
          errors.append(f"Layer '{label}' has Reference '{reference}' which is ambiguous - matches both a Dielectric and a Layer")
        elif not dielectric_match and not layer_match:
          errors.append(f"Layer '{label}' has Reference '{reference}' which matches no Dielectric or Layer")

        reference_edge = el.get("ReferenceEdge")
        if reference_edge is not None and reference_edge != "" and reference_edge.upper() not in ("TOP", "BOTTOM"):
          errors.append(f"Layer '{label}' has invalid ReferenceEdge '{reference_edge}' (must be Top or Bottom)")

    # circular Layer -> Layer Reference check (Dielectric targets are excluded: a Dielectric
    # can't reference a Layer, so it can never be part of a cycle)
    layer_reference_target = {}
    for el in layer_elements:
      name = el.get("Name")
      reference = el.get("Reference")
      if name and reference and reference in layer_names:
        layer_reference_target[name] = reference

    resolved_names = set(layer_names) - set(layer_reference_target.keys())
    remaining = dict(layer_reference_target)
    progress = True
    while progress and remaining:
      progress = False
      for name, target in list(remaining.items()):
        if target in resolved_names:
          resolved_names.add(name)
          del remaining[name]
          progress = True
    if remaining:
      errors.append(f"Circular Layer Reference detected among: {sorted(remaining.keys())}")

    # Reference-based positioning and <Substrate Offset> are mutually exclusive (see
    # XML_stackup_format.md) - Offset applying before/after reference resolution is ambiguous
    substrate_offset_el = get_substrate_offset_element(root)
    if substrate_offset_el is not None:
      offset_value = substrate_offset_el.get("Offset")
      # an expression-valued Offset can't be checked against zero without resolving it - treat
      # it as potentially nonzero (fail toward still catching a real conflict) rather than
      # silently skipping the check
      offset_maybe_nonzero = offset_value is not None and (
          _is_expression(offset_value) or (_is_float(offset_value) and float(offset_value) != 0))
      if offset_maybe_nonzero:
        referenced_layer_names = [el.get("Name") or "<unnamed layer>" for el in layer_elements if el.get("Reference")]
        if referenced_layer_names:
          errors.append(f"<Substrate Offset=\"{offset_value}\"> cannot be combined with Reference-based Layer "
                         f"positioning. Layers using Reference: {referenced_layer_names}")

  # DerivedLayers: these checks intentionally mirror util_stackup_reader.derived_layer's
  # requirements exactly (invalid Operation / wrong operand count for SIZE / fewer than
  # 2 operands otherwise / SIZE without a non-zero Oversize) - that reader class calls
  # exit(1) on any of them instead of raising, which would otherwise kill the whole GUI
  # process the moment something tries to parse this data (e.g. the live preview).
  derived_layers_el = get_derived_layers_element(root)
  derived_names = []
  if derived_layers_el is not None:
    all_derived_elements = derived_layers_el.findall("DerivedLayer")

    # a derived layer used as another derived layer's Operand is a pure
    # intermediate helper (e.g. a poly/implant/contact intersection stage) and
    # is never itself drawn, so it legitimately doesn't need a <Layer> entry -
    # only require one for a derived layer nothing else consumes
    referenced_as_operand = set()
    for el in all_derived_elements:
      for operand in el.findall("Operand"):
        operand_layer = operand.get("Layer")
        if operand_layer is not None and _is_int(operand_layer):
          referenced_as_operand.add(int(operand_layer))

    for el in all_derived_elements:
      name = el.get("Name")
      layernum = el.get("Layer")
      operation = el.get("Operation")
      oversize = el.get("Oversize")
      operands = [operand.get("Layer") for operand in el.findall("Operand")]
      label = name or "<unnamed derived layer>"

      if not name:
        errors.append("DerivedLayer is missing required attribute 'Name'")
      elif name in derived_names:
        errors.append(f"Duplicate derived layer Name '{name}'")
      else:
        derived_names.append(name)

      if not layernum:
        errors.append(f"DerivedLayer '{label}' is missing required attribute 'Layer'")
      elif _is_expression(layernum):
        problem = _expression_problem(layernum, known_variable_names)
        if problem:
          errors.append(f"DerivedLayer '{label}' has invalid Layer='{layernum}' ({problem})")
        # else: valid expression - can't resolve it to a concrete layer number at validate
        # time, so the cross-reference check below is skipped for this row
      elif not _is_int(layernum):
        errors.append(f"DerivedLayer '{label}' has non-integer Layer='{layernum}'")
      elif int(layernum) not in layer_numbers and int(layernum) not in referenced_as_operand:
        errors.append(f"DerivedLayer '{label}' target Layer={layernum} has no matching <Layer> "
                       f"entry (needed to give it a Z-position/material) and isn't used as "
                       f"another derived layer's operand either")

      op_upper = operation.upper() if operation else None
      if not operation:
        errors.append(f"DerivedLayer '{label}' is missing required attribute 'Operation'")
      elif op_upper not in VALID_DERIVED_OPERATIONS:
        errors.append(f"DerivedLayer '{label}' has invalid Operation '{operation}' "
                       f"(must be one of {VALID_DERIVED_OPERATIONS})")

      oversize_value = None
      oversize_is_valid_expression = False
      if oversize is not None and oversize != "":
        if _is_expression(oversize):
          problem = _expression_problem(oversize, known_variable_names)
          if problem:
            errors.append(f"DerivedLayer '{label}' has invalid Oversize='{oversize}' ({problem})")
          else:
            # can't tell if it resolves to zero at validate time - assume it's fine, same
            # spirit as the Substrate Offset check above
            oversize_is_valid_expression = True
        elif not _is_float(oversize):
          errors.append(f"DerivedLayer '{label}' has non-numeric Oversize='{oversize}'")
        else:
          oversize_value = float(oversize)

      for operand_layer in operands:
        if not operand_layer:
          errors.append(f"DerivedLayer '{label}' has a non-integer Operand Layer='{operand_layer}'")
        else:
          problem = _int_problem(operand_layer, known_variable_names)
          if problem:
            errors.append(f"DerivedLayer '{label}' has invalid Operand Layer='{operand_layer}' ({problem})")

      if op_upper == "SIZE":
        if len(operands) != 1:
          errors.append(f"DerivedLayer '{label}' has Operation=\"SIZE\", which needs "
                         f"exactly 1 operand, found {len(operands)}")
        if not oversize_value and not oversize_is_valid_expression:
          errors.append(f"DerivedLayer '{label}' has Operation=\"SIZE\", which needs "
                         f"a non-zero Oversize value")
      elif op_upper in ("AND", "OR", "XOR", "NOT"):
        if len(operands) < 2:
          errors.append(f"DerivedLayer '{label}' needs at least 2 Operand entries, "
                         f"found {len(operands)}")

  return errors
