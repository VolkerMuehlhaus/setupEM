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

"""Preserve mode: edit an imported gds2palace model script in place.

ScriptModel reads an existing *.py model and records, for every setting the
GUI knows, the place in the source that defines it (a settings['key']
assignment, a top-level variable, or an argument of read_gds() /
read_substrate()), and every simulation_port / heatsource / constanttemp call.
Writing back replaces only the text of values that changed. Everything else -
comments, spacing, number spellings like 1e9, custom code - stays
byte-identical, because edits are applied as exact source spans taken from
the stdlib ast positions.

No Qt in here, so the module can be tested on its own.
"""

import ast
import difflib
import os

__all__ = ["ScriptModel", "Refused", "eval_simple_python_expression",
           "patch_script", "GUI_KEY_ALIASES", "LEGACY_SETTINGS"]


# ---------------------------------------------------------------------------
# expression evaluation (moved here from setup_common.py, re-exported there)
# ---------------------------------------------------------------------------

def eval_simple_python_expression(node, known_constants):
    # Evaluate a single AST expression node against a symbol table of already-known
    # module-level constants. This is intentionally NOT a general interpreter - it only
    # understands literals (delegated to ast.literal_eval for plain Constant nodes, the
    # same grammar callers used before this function existed, so anything that already
    # worked keeps working unchanged), bare Name lookups against known_constants, simple
    # arithmetic (BinOp/UnaryOp) combining those, and literal-ish List/Tuple/Set/Dict
    # containers whose elements may themselves reference known constants (e.g.
    # "[ftarget]"). Anything else (calls, attributes, subscripts, comprehensions, ...)
    # raises so the caller can fall back to its own "could not resolve this" handling.
    if isinstance(node, ast.Name):
        if node.id in known_constants:
            return known_constants[node.id]
        raise ValueError(f"unknown name '{node.id}'")

    if isinstance(node, ast.BinOp):
        left = eval_simple_python_expression(node.left, known_constants)
        right = eval_simple_python_expression(node.right, known_constants)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise ValueError(f"unsupported operator {type(node.op).__name__}")

    if isinstance(node, ast.UnaryOp):
        operand = eval_simple_python_expression(node.operand, known_constants)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"unsupported unary operator {type(node.op).__name__}")

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [eval_simple_python_expression(elt, known_constants) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(values)
        if isinstance(node, ast.Set):
            return set(values)
        return values

    if isinstance(node, ast.Dict):
        return {
            eval_simple_python_expression(k, known_constants): eval_simple_python_expression(v, known_constants)
            for k, v in zip(node.keys, node.values)
        }

    # plain literals: numbers, strings, etc. - same code path used before
    # Name/BinOp/UnaryOp/container support was added above
    return ast.literal_eval(node)


def _module_constants(tree):
    # same rules as setup_common.collect_module_level_constants(), from a parsed tree
    known = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue
        try:
            known[stmt.targets[0].id] = eval_simple_python_expression(stmt.value, known)
        except (ValueError, TypeError, ZeroDivisionError, SyntaxError):
            continue
    return known


def _stackup_call_name(node):
    """'name' for utilities.get_stackup_path("name") (or a bare get_stackup_path("name")),
    the way gds2palace_mcp writes the stackup file of its models; None otherwise."""
    if not isinstance(node, ast.Call) or node.keywords or len(node.args) != 1:
        return None
    func = node.func
    func_name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    arg = node.args[0]
    if func_name == "get_stackup_path" and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _bundled_stackup(name):
    """Full path of a stackup file that ships with gds2palace, or None if it doesn't
    ship that file (or is older than 0.9.1, without get_stackup_path())."""
    try:
        from gds2palace import utilities
        return utilities.get_stackup_path(name)
    except (ImportError, AttributeError, OSError):
        return None


# ---------------------------------------------------------------------------
# where the GUI's keys live in a script
# ---------------------------------------------------------------------------

# GUI key (saved_values) -> names a script may use for it, as settings['name']
# or as a top-level variable (same aliases as the .py import mapping)
GUI_KEY_ALIASES = {
    "GdsFile": ("GdsFile", "gds_filename"),
    "SubstrateFile": ("SubstrateFile", "XML_filename"),
}

# GUI key -> (the older setting gds2palace replaced it by, older value for each
# new value it can express). A script with the older setting keeps it when the
# new value can be written that way; otherwise its line is turned into the new
# setting - gds2palace refuses both together when they disagree.
LEGACY_SETTINGS = {
    "conductor_model": ("filled_metals", {"solve_inside": "True", "surface": "False"}),
}

# GUI key -> (function, positional index or None, keyword name) of the workflow
# call argument that actually consumes it. Where a script passes the value
# straight into the call (e.g. read_gds(..., purposelist=[0])), that argument
# is the place to edit.
CALL_ARGUMENTS = {
    "GdsFile": ("read_gds", 0, "filename"),
    "purpose": ("read_gds", 2, "purposelist"),
    "cellname": ("read_gds", None, "cellname"),
    "preprocess_gds": ("read_gds", None, "preprocess"),
    "merge_polygon_size": ("read_gds", None, "merge_polygon_size"),
    "SubstrateFile": ("read_substrate", 0, "XML_filename"),
    "variable_overrides": ("read_substrate", None, "variable_overrides"),
}

PORT_ARGS = ("portnumber", "voltage", "port_Z0", "source_layernum",
             "target_layername", "from_layername", "to_layername", "direction")
THERMAL_ARGS = {"heatsource": ("power", "source_layernum", "target_layername"),
                "constanttemp": ("temp", "source_layernum", "target_layername")}
THERMAL_ADD_METHOD = {"heatsource": "add_heatsource", "constanttemp": "add_consttemp"}

CREATE_CALLS = ("create_palace", "create_elmer", "create_elmer_thermal", "create_model")

_UNRESOLVED = object()


class Refused(Exception):
    """A change that can't be written into this script; the message says why."""


class Site:
    """The source text that defines one GUI key."""

    def __init__(self, key, node, value, writable, reason, kind, wrapper=None):
        self.key = key
        self.node = node            # ast expression node whose text is the value
        self.value = value          # evaluated value, or _UNRESOLVED
        self.writable = writable
        self.reason = reason        # why not writable ("" if writable)
        self.kind = kind            # "dict", "variable" or "argument"
        self.wrapper = wrapper      # ast.List around the use, e.g. purposelist=[settings['purpose']]

    @property
    def resolved(self):
        return self.value is not _UNRESOLVED


class CallSite:
    """One simulation_port / heatsource / constanttemp call."""

    def __init__(self, kind, stmt, call, args, static, reason):
        self.kind = kind            # "port", "heatsource" or "constanttemp"
        self.stmt = stmt            # enclosing statement (e.g. ports.add_port(...))
        self.call = call            # the simulation_setup.simulation_port(...) call node
        self.args = args            # keyword name -> evaluated value
        self.static = static        # module level, keywords only, all values resolvable
        self.reason = reason


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

class ScriptModel:

    def __init__(self, text, path=None):
        self.text = text
        self.path = path
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.tree = ast.parse(text)
        self._line_starts = [0]
        for line in text.splitlines(keepends=True):
            self._line_starts.append(self._line_starts[-1] + len(line))
        self._lines = text.splitlines(keepends=True)
        self._parents = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self._parents[child] = parent
        self.constants = _module_constants(self.tree)
        self._edits = []            # (start, end, replacement)
        self._collect_assignments()
        self.settings_dict = self._main_settings_dict()
        self.ports = self._collect_calls("port")
        self.thermal = self._collect_calls("heatsource") + self._collect_calls("constanttemp")
        self.thermal.sort(key=lambda c: (c.stmt.lineno, c.stmt.col_offset))
        self.create_call = self._find_create_call()

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8", newline="") as f:
            return cls(f.read(), path)

    # ---- positions -------------------------------------------------------

    def _offset(self, lineno, col_offset):
        # ast columns are UTF-8 byte offsets within the line
        line = self._lines[lineno - 1] if lineno - 1 < len(self._lines) else ""
        char_col = len(line.encode("utf-8")[:col_offset].decode("utf-8", errors="ignore"))
        return self._line_starts[lineno - 1] + char_col

    def span(self, node):
        return (self._offset(node.lineno, node.col_offset),
                self._offset(node.end_lineno, node.end_col_offset))

    def source(self, node):
        start, end = self.span(node)
        return self.text[start:end]

    def _statement(self, node):
        while node is not None and not isinstance(node, ast.stmt):
            node = self._parents.get(node)
        return node

    # ---- assignments -----------------------------------------------------

    def _collect_assignments(self):
        # every assignment target in the whole file (also inside loops and
        # functions), so a name assigned more than once is never treated as one
        # fixed, editable value
        self._var_assigns = {}      # name -> [Assign statements] (simple "name = ...")
        self._var_other = {}        # name -> count of other bindings (loop targets, aug-assign, ...)
        self._dict_assigns = {}     # (dictname, key) -> [Assign statements]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and len(node.targets) == 1:
                        self._var_assigns.setdefault(target.id, []).append(node)
                    elif isinstance(target, ast.Subscript) and len(node.targets) == 1:
                        key = self._subscript_key(target)
                        if key is not None:
                            self._dict_assigns.setdefault(key, []).append(node)
                    else:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name):
                                self._var_other[name.id] = self._var_other.get(name.id, 0) + 1
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                target = node.target
                if isinstance(target, ast.Name):
                    self._var_other[target.id] = self._var_other.get(target.id, 0) + 1
                elif isinstance(target, ast.Subscript):
                    key = self._subscript_key(target)
                    if key is not None:
                        self._dict_assigns.setdefault(key, []).append(node)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                for name in ast.walk(node.target):
                    if isinstance(name, ast.Name):
                        self._var_other[name.id] = self._var_other.get(name.id, 0) + 1
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        for name in ast.walk(item.optional_vars):
                            if isinstance(name, ast.Name):
                                self._var_other[name.id] = self._var_other.get(name.id, 0) + 1

    @staticmethod
    def _subscript_key(target):
        if not isinstance(target.value, ast.Name):
            return None
        index = target.slice
        if isinstance(index, ast.Index):        # Python < 3.9
            index = index.value
        if isinstance(index, ast.Constant) and isinstance(index.value, str):
            return (target.value.id, index.value)
        return None

    def _main_settings_dict(self):
        # the dict most settings are written to (normally "settings")
        counts = {}
        for (dictname, _key), stmts in self._dict_assigns.items():
            counts[dictname] = counts.get(dictname, 0) + len(stmts)
        if not counts:
            return None
        return max(sorted(counts), key=lambda name: counts[name])

    def _single_assignment(self, stmts, other_count, label):
        # one assignment statement, wherever it is: inside a loop (a sweep script's
        # loop body) every pass uses that same text, so editing it is as safe as at
        # module level; a value that depends on the loop is caught by the caller
        # (it uses other names)
        if len(stmts) != 1 or other_count:
            return None, "set in several places"
        stmt = stmts[0]
        if not isinstance(stmt, ast.Assign):
            return None, "changed with an operator"
        return stmt, ""

    def _follow(self, key, node, kind, wrapper=None, depth=0):
        # from an expression that provides a value, go to the place that defines it
        if depth > 5:
            return Site(key, node, _UNRESOLVED, False, "too many indirections", kind, wrapper)
        if isinstance(node, ast.List) and len(node.elts) == 1 and key == "purpose" and wrapper is None \
                and isinstance(node.elts[0], (ast.Name, ast.Subscript)):
            # purposelist=[settings['purpose']]: the value inside is a single purpose
            return self._follow(key, node.elts[0], kind, wrapper=node, depth=depth + 1)
        if isinstance(node, ast.Name) and node.id not in ("True", "False", "None"):
            stmts = self._var_assigns.get(node.id, [])
            if not stmts:
                # a loop variable (a sweep): read-only, shown with its first value
                values = self.loop_values(node.id)
                if values:
                    return Site(key, node, values[0], False, self._swept_reason({node.id: values}), kind, wrapper)
                # an imported name, ...
                return Site(key, node, _UNRESOLVED, False, f"uses {node.id}", kind, wrapper)
            other = self._var_other.get(node.id, 0)
            stmt, reason = self._single_assignment(stmts, other, f"'{node.id}'")
            if stmt is None:
                return self._read_only_last(key, stmts, other, f"{node.id} is {reason}", kind, node, wrapper, depth)
            return self._follow(key, stmt.value, "variable", wrapper, depth + 1)
        if isinstance(node, ast.Subscript):
            dkey = self._subscript_key(node)
            if dkey is not None:
                stmts = self._dict_assigns.get(dkey, [])
                if not stmts:
                    return Site(key, node, _UNRESOLVED, False, f"uses {dkey[0]}['{dkey[1]}']", kind, wrapper)
                stmt, reason = self._single_assignment(stmts, 0, f"{dkey[0]}['{dkey[1]}']")
                if stmt is None:
                    return self._read_only_last(key, stmts, 0, reason, kind, node, wrapper, depth)
                return self._follow(key, stmt.value, "dict", wrapper, depth + 1)
        stackup_name = _stackup_call_name(node)
        if stackup_name is not None:
            path = _bundled_stackup(stackup_name)
            if path is not None:
                return Site(key, node, path, True, "", kind, wrapper)
        # a value that refers to other names (e.g. fstop = 2*ftarget, or a sweep's
        # loop variable) would lose that link if overwritten - keep it read-only
        names = self._names_in(node)
        try:
            value = eval_simple_python_expression(node, self.constants)
        except (ValueError, TypeError, ZeroDivisionError, SyntaxError, KeyError):
            # depends on sweep loop variables, e.g. {'Temp_Celsius': Temp_Celsius}
            # or 2*cellsize: read-only, shown with the first loop pass's value
            loops = {n: self.loop_values(n) for n in names if n not in self.constants}
            if loops and all(loops.values()):
                try:
                    first = dict(self.constants, **{n: v[0] for n, v in loops.items()})
                    value = eval_simple_python_expression(node, first)
                    return Site(key, node, value, False, self._swept_reason(loops), kind, wrapper)
                except (ValueError, TypeError, ZeroDivisionError, SyntaxError, KeyError):
                    pass
            reason = f"uses {', '.join(names)}" if names else "computed by the script"
            return Site(key, node, _UNRESOLVED, False, reason, kind, wrapper)
        if names:
            return Site(key, node, value, False, f"uses {', '.join(names)}", kind, wrapper)
        return Site(key, node, value, True, "", kind, wrapper)

    def loop_values(self, name):
        """The values a for loop gives to name, when the loop is the only binding
        of it and iterates over plain values: a list / tuple, a list defined at
        module level, or range(...). None otherwise."""
        loops = [n for n in ast.walk(self.tree)
                 if isinstance(n, (ast.For, ast.AsyncFor)) and isinstance(n.target, ast.Name) and n.target.id == name]
        if len(loops) != 1 or self._var_assigns.get(name) or self._var_other.get(name, 0) != 1:
            return None
        iterable = loops[0].iter
        try:
            if isinstance(iterable, ast.Call) and getattr(iterable.func, "id", None) == "range" and not iterable.keywords:
                values = list(range(*[eval_simple_python_expression(a, self.constants) for a in iterable.args]))
            else:
                values = eval_simple_python_expression(iterable, self.constants)
        except (ValueError, TypeError, ZeroDivisionError, SyntaxError, KeyError):
            return None
        return list(values) if isinstance(values, (list, tuple)) and values else None

    @staticmethod
    def _swept_reason(loops):
        return "swept: " + "; ".join(f"{name} = {', '.join(str(v) for v in values)}"
                                      for name, values in loops.items())

    @staticmethod
    def _names_in(node):
        names = []
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id not in names:
                names.append(n.id)
        return names

    def _workflow_calls(self, function):
        calls = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == function:
                    calls.append(node)
        return calls

    def site(self, key):
        """The Site defining a GUI key, or None if the script doesn't set it."""
        if key in CALL_ARGUMENTS:
            function, position, keyword = CALL_ARGUMENTS[key]
            calls = self._workflow_calls(function)
            args = []
            for call in calls:
                arg = next((kw.value for kw in call.keywords if kw.arg == keyword), None)
                if arg is None and position is not None and len(call.args) > position \
                        and not any(isinstance(a, ast.Starred) for a in call.args[:position + 1]):
                    arg = call.args[position]
                if arg is not None:
                    args.append(arg)
            if len(args) == 1:
                return self._follow(key, args[0], "argument")
            if len(args) > 1:
                sites = [self._follow(key, a, "argument") for a in args]
                spans = {self.span(s.node) for s in sites}
                if len(spans) == 1:
                    return sites[0]
                return Site(key, args[0], _UNRESOLVED, False,
                            f"set in {len(args)} {function}() calls", "argument")
        for name in GUI_KEY_ALIASES.get(key, (key,)):
            if self.settings_dict and (self.settings_dict, name) in self._dict_assigns:
                stmts = self._dict_assigns[(self.settings_dict, name)]
                stmt, reason = self._single_assignment(stmts, 0, f"{self.settings_dict}['{name}']")
                if stmt is None:
                    target = stmts[0].targets[0] if isinstance(stmts[0], ast.Assign) else stmts[0].target
                    return self._read_only_last(key, stmts, 0, reason, "dict", target)
                return self._follow(key, stmt.value, "dict")
        for name in GUI_KEY_ALIASES.get(key, (key,)):
            if name in self._var_assigns or self._var_other.get(name):
                stmts = self._var_assigns.get(name, [])
                other = self._var_other.get(name, 0)
                stmt, reason = self._single_assignment(stmts, other, f"'{name}'")
                if stmt is None:
                    if not stmts:
                        # only a loop variable or similar
                        return Site(key, self.tree, _UNRESOLVED, False, f"uses {name}", "variable")
                    return self._read_only_last(key, stmts, other, reason, "variable", stmts[0].targets[0])
                return self._follow(key, stmt.value, "variable")
        return None

    def _read_only_last(self, key, stmts, other_bindings, reason, kind, node, wrapper=None, depth=0):
        """A setting assigned several times is read-only; its value for showing
        in the GUI is the last plain assignment in the file, the one the script
        ends up using (unless a loop or similar also binds it)."""
        assigns = [s for s in stmts if isinstance(s, ast.Assign)]
        if assigns and not other_bindings:
            last = max(assigns, key=lambda s: (s.lineno, s.col_offset))
            inner = self._follow(key, last.value, kind, wrapper, depth + 1)
            return Site(key, inner.node, inner.value, False, reason, kind, wrapper)
        return Site(key, node, _UNRESOLVED, False, reason, kind, wrapper)

    # ---- ports and thermal objects ---------------------------------------

    def _collect_calls(self, kind):
        function = "simulation_port" if kind == "port" else kind
        result = []
        for call in self._workflow_calls(function):
            stmt = self._statement(call)
            args = {}
            static, reason = True, ""
            if call.args:
                static, reason = False, "uses positional arguments"
            for kw in call.keywords:
                if kw.arg is None:
                    static, reason = False, "uses **kwargs"
                    continue
                try:
                    args[kw.arg] = eval_simple_python_expression(kw.value, self.constants)
                except (ValueError, TypeError, ZeroDivisionError, SyntaxError, KeyError):
                    names = self._names_in(kw.value)
                    static, reason = False, (f"uses {', '.join(names)}" if names else "computed by the script")
            # like settings: a call with plain values is editable also inside a loop
            if static and not self._owns_lines(stmt):
                static, reason = False, "shares its line with other code"
            result.append(CallSite(kind, stmt, call, args, static, reason))
        return result

    def _owns_lines(self, stmt):
        # the statement starts its first line and ends its last one (apart from
        # a trailing comment), so it can be removed / used as an anchor by lines
        start, end = self.span(stmt)
        line_start = self._line_starts[stmt.lineno - 1]
        if self.text[line_start:start].strip():
            return False
        rest = self._lines[stmt.end_lineno - 1][end - self._line_starts[stmt.end_lineno - 1]:]
        rest = rest.strip()
        return rest == "" or rest.startswith("#")

    def _find_create_call(self):
        # every create_*() call; a script that builds several models (e.g. thermal,
        # then EM) has no single one to edit
        self.create_calls = [c for name in CREATE_CALLS for c in self._workflow_calls(name)]
        return self.create_calls[0] if len(self.create_calls) == 1 else None

    @property
    def tool(self):
        """'palace', 'elmer', 'elmer_thermal', 'openems' or None."""
        if any(isinstance(n, (ast.Import, ast.ImportFrom)) and
               any(a.name.split(".")[0] in ("openEMS", "CSXCAD") for a in n.names) or
               isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in ("openEMS", "CSXCAD")
               for n in ast.walk(self.tree)):
            return "openems"
        if self.create_call is None:
            return None
        name = self._call_name(self.create_call)
        return {"create_palace": "palace", "create_elmer": "elmer",
                "create_elmer_thermal": "elmer_thermal"}.get(name)

    @staticmethod
    def _call_name(call):
        func = call.func
        return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)

    # ---- editing ---------------------------------------------------------

    def _add_edit(self, start, end, text):
        # insertions (start == end) may share a position, they keep their order
        for s, e, _t in self._edits:
            if start < e and s < end:
                raise Refused("two changes touch the same part of the script")
        self._edits.append((start, end, text))

    def set_value(self, key, script_text):
        """Write script_text (a Python expression) as the value of a GUI key.
        Inserts settings['key'] = ... when the script doesn't set it yet."""
        site = self.site(key)
        if site is None:
            if key in CALL_ARGUMENTS:
                # the workflow call exists but doesn't pass this argument: a new
                # settings[] entry would have no effect, so add the keyword there
                function, _position, keyword = CALL_ARGUMENTS[key]
                calls = self._workflow_calls(function)
                if len(calls) == 1:
                    self._add_keyword(calls[0], keyword, script_text)
                    return
                if calls:
                    raise Refused(f"{function}() is called {len(calls)} times")
            self._insert_setting(key, script_text)
            return
        if not site.writable:
            raise Refused(site.reason)
        start, end = self.span(site.node)
        self._add_edit(start, end, script_text)

    def replace_setting(self, old_key, new_key, script_text):
        """Replace settings['old_key'] = ... by settings['new_key'] = script_text."""
        site = self.site(old_key)
        if site is None:
            raise Refused(f"the script doesn't set {old_key}")
        if not site.writable:
            raise Refused(site.reason)
        stmts = self._dict_assigns.get((self.settings_dict, old_key), []) if self.settings_dict else []
        if len(stmts) != 1 or not isinstance(stmts[0], ast.Assign) or len(stmts[0].targets) != 1 \
                or not isinstance(stmts[0].targets[0], ast.Subscript):
            raise Refused(f"{old_key} is not a single {self.settings_dict or 'settings'}['{old_key}'] = ... line")
        start, end = self.span(stmts[0])
        self._add_edit(start, end, f"{self.settings_dict}['{new_key}'] = {script_text}")

    def unwrap_purpose(self):
        """Replace purposelist=[settings['purpose']] by purposelist=settings['purpose']."""
        site = self.site("purpose")
        if site is None or site.wrapper is None:
            return
        start, end = self.span(site.wrapper)
        inner = self.source(site.wrapper.elts[0])
        self._add_edit(start, end, inner)

    def _add_keyword(self, call, keyword, script_text):
        if any(kw.arg is None for kw in call.keywords) or any(isinstance(a, ast.Starred) for a in call.args):
            raise Refused("the call uses *args / **kwargs")
        last = max(list(call.args) + [kw.value for kw in call.keywords],
                   key=lambda n: (n.end_lineno, n.end_col_offset), default=None)
        if last is None:
            raise Refused("the call has no arguments to add to")
        _start, pos = self.span(last)
        self._add_edit(pos, pos, f", {keyword}={script_text}")

    def _insert_setting(self, key, script_text):
        if not self.settings_dict:
            raise Refused("the script has no settings dictionary to add it to")
        stmts = [s for (d, _k), group in self._dict_assigns.items() if d == self.settings_dict
                 for s in group if self._owns_lines(s)]
        if not stmts:
            raise Refused("the script has no settings dictionary to add it to")
        anchor = max(stmts, key=lambda s: s.end_lineno)
        self.insert_after(anchor, f"{self.settings_dict}['{key}'] = {script_text}")

    def set_call_arguments(self, callsite, values, keyword_order):
        """Write new keyword values into one port / thermal call.
        values: keyword -> script text. Spot-edits values when the same keywords
        are present, otherwise rewrites the call's argument list."""
        if not callsite.static:
            raise Refused(callsite.reason)
        present = [kw.arg for kw in callsite.call.keywords]
        if set(present) == set(values):
            for kw in callsite.call.keywords:
                new = values[kw.arg]
                if self.source(kw.value) != new:
                    start, end = self.span(kw.value)
                    self._add_edit(start, end, new)
            return
        args = ", ".join(f"{k}={values[k]}" for k in keyword_order if k in values)
        call_start, call_end = self.span(callsite.call)
        func_text = self.source(callsite.call.func)
        self._add_edit(call_start, call_end, f"{func_text}({args})")

    def remove_statement(self, callsite):
        if not callsite.static:
            raise Refused(callsite.reason)
        parent = self._parents.get(callsite.stmt)
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and callsite.stmt in block and len(block) == 1:
                raise Refused("it is the only statement in its block")
        start = self._line_starts[callsite.stmt.lineno - 1]
        end = self._line_starts[callsite.stmt.end_lineno]
        self._add_edit(start, end, "")

    def _indent_of(self, stmt):
        line_start = self._line_starts[stmt.lineno - 1]
        start, _ = self.span(stmt)
        return self.text[line_start:start]

    def insert_after(self, callsite_or_stmt, line_text):
        # the new line gets the indentation of the line it follows (e.g. a loop body)
        stmt = callsite_or_stmt.stmt if isinstance(callsite_or_stmt, CallSite) else callsite_or_stmt
        pos = self._line_starts[stmt.end_lineno]
        line = self._indent_of(stmt) + line_text + self.newline
        if not self.text[:pos].endswith(("\n", "\r")):
            line = self.newline + line
        self._add_edit(pos, pos, line)

    def rename_create_call(self, new_name):
        if self.create_call is None:
            raise Refused("the script doesn't call exactly one create_palace / create_elmer function")
        func = self.create_call.func
        if isinstance(func, ast.Attribute):
            start, end = self.span(func)
            value_text = self.source(func.value)
            self._add_edit(start, end, f"{value_text}.{new_name}")
        else:
            start, end = self.span(func)
            self._add_edit(start, end, new_name)

    @property
    def changed(self):
        return bool(self._edits)

    def result(self):
        text = self.text
        # apply from the end, so earlier positions stay valid; of several insertions
        # at one position the last one added is applied first, which keeps their order
        order = sorted(range(len(self._edits)),
                       key=lambda i: (self._edits[i][0], self._edits[i][1], i), reverse=True)
        for i in order:
            start, end, new = self._edits[i]
            text = text[:start] + new + text[end:]
        return text


# ---------------------------------------------------------------------------
# GUI values -> script text
# ---------------------------------------------------------------------------

GHZ_KEYS = ("fstart", "fstop", "fstep")
GHZ_LIST_KEYS = ("fpoint", "fdump")
PATH_KEYS = ("GdsFile", "SubstrateFile")


def _number(value):
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _ghz(value):
    # GUI GHz -> script Hz, spelled like the generated scripts: 100e9, 2.5e9
    return _number(float(value)) + "e9"


def _render_string(text, original_source):
    # keep the script's quote character where possible
    if original_source and original_source[0] in "\"'" and not original_source.startswith(("'''", '"""')):
        q = original_source[0]
        if q not in text and "\\" not in text:
            return f"{q}{text}{q}"
    return repr(text)


def _canonical_purpose(value):
    if isinstance(value, (list, tuple)):
        flat = []
        for item in value:
            flat.extend(_canonical_purpose(item))
        return flat
    return [value]


def render_value(model, key, value):
    """Script text for a GUI value, in the shape the script already uses."""
    site = model.site(key)
    original = model.source(site.node) if site is not None and site.resolved else ""
    if key in GHZ_KEYS:
        return _ghz(value)
    if key in GHZ_LIST_KEYS:
        values = value if isinstance(value, (list, tuple)) else [value]
        if site is not None and site.resolved and not isinstance(site.value, (list, tuple)) and len(values) == 1:
            return _ghz(values[0])
        return "[" + ", ".join(_ghz(v) for v in values) + "]"
    if key == "purpose":
        # a single number stays a single number where the script wraps it in a
        # list itself (purposelist=[settings['purpose']]); everything else is
        # written as a list, which read_gds() needs ("purpose in purposelist")
        purposes = _canonical_purpose(value)
        scalar_site = site is not None and site.resolved and not isinstance(site.value, (list, tuple))
        if scalar_site and site.wrapper is not None and len(purposes) == 1:
            return _number(purposes[0])
        return "[" + ", ".join(_number(p) for p in purposes) + "]"
    if key in PATH_KEYS and isinstance(value, str):
        if site is not None and _stackup_call_name(site.node) is not None:
            # another stackup that ships with gds2palace stays a get_stackup_path() call
            original = model.source(site.node.args[0])
            name = os.path.basename(value)
            bundled = _bundled_stackup(name)
            if bundled and os.path.normcase(os.path.abspath(bundled)) == os.path.normcase(os.path.abspath(value)):
                return f"{model.source(site.node.func)}({_render_string(name, original)})"
        path = value.replace("\\", "/")
        if model.path:
            script_dir = os.path.dirname(os.path.abspath(model.path))
            try:
                rel = os.path.relpath(path, script_dir).replace("\\", "/")
                if not rel.startswith(".."):
                    path = rel
            except ValueError:
                pass                # other drive on Windows
        return _render_string(path, original)
    if isinstance(value, str):
        return _render_string(value, original)
    if isinstance(value, (bool, int, float)):
        return _number(value)
    return repr(value)


# ---------------------------------------------------------------------------
# one-call API for the GUI
# ---------------------------------------------------------------------------

def _port_values(port):
    # GUI port dict -> keyword -> script text
    values = {
        "portnumber": _number(int(port["portnumber"])),
        "voltage": _number(port.get("voltage", 1)),
        "port_Z0": _number(port.get("port_Z0", 50)),
        "source_layernum": _number(int(port["source_layernum"])),
    }
    if str(port.get("direction", "")).upper() == "Z":
        values["from_layername"] = repr(port.get("from_layername", ""))
        values["to_layername"] = repr(port.get("to_layername", ""))
    else:
        values["target_layername"] = repr(port.get("target_layername", ""))
    values["direction"] = repr(port.get("direction", ""))
    return values


def _thermal_values(obj):
    kind = obj["type"]
    first = THERMAL_ARGS[kind][0]
    return {first: _number(obj[first]),
            "source_layernum": _number(int(obj["source_layernum"])),
            "target_layername": repr(obj.get("target_layername", ""))}


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return float(a) == float(b)
    return a == b


def _keep_spelling(model, callsite, values):
    # keep a keyword's original text when its value didn't change (e.g. 50 vs 50.0,
    # "z" vs 'z', or 2*Z0), so only real changes are written
    for kw in callsite.call.keywords:
        if kw.arg in values and kw.arg in callsite.args:
            try:
                new_value = ast.literal_eval(values[kw.arg])
            except (ValueError, SyntaxError):
                continue
            old_value = callsite.args[kw.arg]
            if kw.arg == "direction" and isinstance(new_value, str) and isinstance(old_value, str):
                # the GUI shows directions in upper case, the workflow ignores case
                new_value, old_value = new_value.upper(), old_value.upper()
            if _same(new_value, old_value):
                values[kw.arg] = model.source(kw.value)
    return values


def _prefix_from(model, callsite, fallback):
    # "simulation_ports.add_port(simulation_setup.simulation_port(" from an existing call
    if callsite is None:
        return fallback
    stmt_start, _ = model.span(callsite.stmt)
    call_start, _ = model.span(callsite.call)
    return model.text[stmt_start:call_start] + model.source(callsite.call.func) + "("


def _suffix_from(model, callsite):
    _, stmt_end = model.span(callsite.stmt)
    _, call_end = model.span(callsite.call)
    return model.text[call_end:stmt_end]


def patch_script(model, baseline, current, ignore_keys=(), baseline_ports=None, current_ports=None,
                 baseline_thermal=None, current_thermal=None, tool_change=None, raw_values=None):
    """Apply the GUI changes (current vs. baseline) to the script model.

    baseline / current: saved_values dicts (GUI units). Only keys whose value
    differs are written. raw_values: key -> script expression text to write
    as is (e.g. settings['fdump'] = [settings['fstop']] for Elmer's checkbox).
    Returns (written_keys, refused) where refused is a list of (what, reason).
    """
    written, refused = [], []
    keys = [k for k in current if k not in ignore_keys]
    for key in keys:
        if key in baseline and _same(baseline[key], current[key]):
            continue
        if key not in baseline and current[key] in (None, "", [], {}):
            continue
        try:
            text = render_value(model, key, current[key])
            legacy_key, legacy_values = LEGACY_SETTINGS.get(key, (None, {}))
            if legacy_key and model.site(key) is None and model.site(legacy_key) is not None:
                if current[key] in legacy_values:
                    model.set_value(legacy_key, legacy_values[current[key]])
                    written.append(legacy_key)
                else:
                    model.replace_setting(legacy_key, key, text)
                    written.append(key)
                continue
            site = model.site(key)
            if key == "purpose" and site is not None and site.wrapper is not None and text.startswith("["):
                model.unwrap_purpose()
            model.set_value(key, text)
            written.append(key)
        except Refused as e:
            refused.append((key, str(e)))
    for key, text in (raw_values or {}).items():
        try:
            model.set_value(key, text)
            written.append(key)
        except Refused as e:
            refused.append((key, str(e)))

    if baseline_ports is not None and current_ports is not None:
        _patch_ports(model, baseline_ports, current_ports, written, refused)
    if baseline_thermal is not None and current_thermal is not None:
        _patch_thermal(model, baseline_thermal, current_thermal, written, refused)

    if tool_change:
        try:
            model.rename_create_call(tool_change)
            written.append(tool_change)
        except Refused as e:
            refused.append(("simulator", str(e)))
    return written, refused


def _patch_ports(model, baseline, current, written, refused):
    by_number = {}
    for cs in model.ports:
        number = cs.args.get("portnumber")
        if cs.static and number is not None:
            by_number.setdefault(int(number), []).append(cs)
    old = {int(p["portnumber"]): p for p in baseline}
    new = {int(p["portnumber"]): p for p in current}
    anchor = max((cs for cs in model.ports if cs.static), key=lambda c: c.stmt.end_lineno, default=None)
    for number in sorted(set(old) | set(new)):
        sites = by_number.get(number, [])
        if number in old and number in new:
            if _port_values(old[number]) == _port_values(new[number]):
                continue
            if len(sites) != 1:
                refused.append((f"port {number}", "not found as one plain simulation_port() call in the script"))
                continue
            values = _keep_spelling(model, sites[0], _port_values(new[number]))
            try:
                model.set_call_arguments(sites[0], values, PORT_ARGS)
                written.append(f"port {number}")
            except Refused as e:
                refused.append((f"port {number}", str(e)))
        elif number in old:
            if len(sites) != 1:
                refused.append((f"port {number}", "not found as one plain simulation_port() call in the script"))
                continue
            try:
                model.remove_statement(sites[0])
                written.append(f"port {number}")
            except Refused as e:
                refused.append((f"port {number}", str(e)))
        else:
            if anchor is None:
                refused.append((f"port {number}", "the script has no port definition to add it after"))
                continue
            values = _port_values(new[number])
            args = ", ".join(f"{k}={values[k]}" for k in PORT_ARGS if k in values)
            line = _prefix_from(model, anchor, "") + args + ")" + _suffix_from(model, anchor)
            try:
                model.insert_after(anchor, line)
                written.append(f"port {number}")
            except Refused as e:
                refused.append((f"port {number}", str(e)))


def _patch_thermal(model, baseline, current, written, refused):
    # the GUI lists heat sources first, then constant temperatures (import order);
    # pair the script's calls with the baseline the same way
    ordered = [c for c in model.thermal if c.kind == "heatsource"] + \
              [c for c in model.thermal if c.kind == "constanttemp"]
    if len(ordered) != len(baseline) or not all(c.static for c in ordered):
        if baseline != current:
            refused.append(("thermal objects", "the script's heat sources / constant temperatures "
                                               "are not all plain, single-line definitions"))
        return

    def sig(obj):
        return (obj["type"],) + tuple(sorted(_thermal_values(obj).items()))

    matcher = difflib.SequenceMatcher(a=[sig(o) for o in baseline], b=[sig(o) for o in current], autojunk=False)
    anchor = max(model.thermal, key=lambda c: c.stmt.end_lineno, default=None)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            continue
        pairs = min(a1 - a0, b1 - b0) if op == "replace" else 0
        for i in range(pairs):
            cs, obj = ordered[a0 + i], current[b0 + i]
            label = f"thermal object {b0 + i + 1}"
            try:
                if cs.kind == obj["type"]:
                    values = _keep_spelling(model, cs, _thermal_values(obj))
                    model.set_call_arguments(cs, values, THERMAL_ARGS[cs.kind])
                else:
                    model.remove_statement(cs)
                    model.insert_after(cs, _thermal_line(model, cs, obj))
                written.append(label)
            except Refused as e:
                refused.append((label, str(e)))
        for i in range(a0 + pairs, a1):
            try:
                model.remove_statement(ordered[i])
                written.append(f"thermal object {i + 1} (removed)")
            except Refused as e:
                refused.append((f"thermal object {i + 1}", str(e)))
        for i in range(b0 + pairs, b1):
            if anchor is None:
                refused.append((f"thermal object {i + 1}", "the script has no thermal definition to add it after"))
                continue
            try:
                model.insert_after(anchor, _thermal_line(model, anchor, current[i]))
                written.append(f"thermal object {i + 1}")
            except Refused as e:
                refused.append((f"thermal object {i + 1}", str(e)))


def _thermal_line(model, template, obj):
    # receiver.add_heatsource(module.heatsource(...)) modelled on an existing line
    kind = obj["type"]
    values = _thermal_values(obj)
    args = ", ".join(f"{k}={values[k]}" for k in THERMAL_ARGS[kind])
    stmt_start, _ = model.span(template.stmt)
    call_start, _ = model.span(template.call)
    prefix = model.text[stmt_start:call_start]
    prefix = prefix.replace(THERMAL_ADD_METHOD[template.kind], THERMAL_ADD_METHOD[kind])
    func = template.call.func
    if isinstance(func, ast.Attribute):
        func_text = f"{model.source(func.value)}.{kind}"
    else:
        func_text = kind
    return prefix + func_text + "(" + args + ")" + _suffix_from(model, template)
