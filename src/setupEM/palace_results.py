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

"""Parse AWS Palace solver output (palace.json / error-indicators.csv) into a
human-readable summary for the setupEM "Create Model" log. No Qt dependency,
so this can be exercised standalone against real Palace output directories.
"""

import os
import json
import csv
import re
import math
import cmath

_ITERATION_RE = re.compile(r'^iteration(\d+)$')


def find_output_dir(run_path, model_basename):
    # config.json's Problem.Output is a path relative to config.json's own directory
    config_path = os.path.join(run_path, 'config.json')
    output_rel = None
    if os.path.isfile(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            output_rel = config.get('Problem', {}).get('Output')
        except (OSError, json.JSONDecodeError):
            output_rel = None
    if not output_rel:
        output_rel = 'output/' + model_basename
    return os.path.normpath(os.path.join(run_path, output_rel))


def _read_palace_json(dir_path):
    path = os.path.join(dir_path, 'palace.json')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return {
        'duration_s': data.get('ElapsedTime', {}).get('Durations', {}).get('Total'),
        'peak_ram_mb': data.get('PeakMemoryMegabytes', {}).get('Total'),
        'dof': data.get('Problem', {}).get('DegreesOfFreedom'),
        'mesh_elements': data.get('Problem', {}).get('MeshElements'),
    }


def _read_error_indicators(dir_path):
    path = os.path.join(dir_path, 'error-indicators.csv')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8', newline='') as f:
            rows = [row for row in csv.reader(f, skipinitialspace=True) if row]
    except OSError:
        return None
    if len(rows) < 2:
        return None
    headers = [h.strip() for h in rows[0]]
    values = [v.strip() for v in rows[1]]
    fields = dict(zip(headers, values))
    try:
        return {
            'norm': float(fields['Norm']),
            'maximum': float(fields['Maximum']),
        }
    except (KeyError, ValueError):
        return None


def _parse_palace_port_s_csv(path):
    """Parse a Palace port-S.csv file into (freq_list, S_dB_list, S_arg_list,
    num_ports), where S_dB_list/S_arg_list are one dict-per-frequency mapping
    'i j' port-pair strings to their |S| (dB) / phase (deg) string values.
    """
    freq = []
    S_dB = []
    S_arg = []
    params = []
    num_ports = 0

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            aline = line.rstrip()
            aline = aline.replace(",", "")
            aline = aline.replace("(dB)", "")
            aline = aline.replace("(deg.)", "")

            if 'Hz)' in aline:
                # header line: items are like |S[1][1]| arg(S[1][1])
                items = aline.split()
                for item in items:
                    if '|' in item:
                        Sxx = item.replace('|S', '').replace('|', '')
                        Sxx = Sxx.replace('][', ' ').replace('[', '').replace(']', '')
                        params.append(Sxx)
                        a, b = (int(x) for x in Sxx.split())
                        num_ports = max(num_ports, a, b)
                continue

            items = aline.split()
            if not items:
                continue
            dB = {}
            arg = {}
            for param in params:
                dB_index = 2 * params.index(param) + 1
                arg_index = dB_index + 1
                dB[param] = items[dB_index]
                arg[param] = items[arg_index]
            freq.append(items[0])
            S_dB.append(dB)
            S_arg.append(arg)

    return freq, S_dB, S_arg, num_ports


def _read_port_s_data(dir_path):
    """Parse dir_path/port-S.csv (if present), or None if the file is
    missing, unfinished, or malformed (e.g. a still-running pass).
    """
    path = os.path.join(dir_path, 'port-S.csv')
    if not os.path.isfile(path):
        return None
    try:
        freq, S_dB, S_arg, num_ports = _parse_palace_port_s_csv(path)
    except (OSError, ValueError, IndexError):
        return None
    if not freq:
        return None
    return freq, S_dB, S_arg, num_ports


def _max_delta_s(prev_data, curr_data):
    """Max |S_curr - S_prev| across all ports and frequencies common to both
    passes, mirroring HFSS's per-pass Max Delta S. Returns None if either
    pass has no port-S data, or the two use different port counts.
    """
    if prev_data is None or curr_data is None:
        return None
    freq_p, dB_p, arg_p, ports_p = prev_data
    freq_c, dB_c, arg_c, ports_c = curr_data
    if ports_p != ports_c:
        return None

    max_delta = 0.0
    found_any = False
    for idx in range(min(len(freq_p), len(freq_c))):
        for key in set(dB_p[idx]) & set(dB_c[idx]):
            try:
                Sp = cmath.rect(10 ** (float(dB_p[idx][key]) / 20.0), math.radians(float(arg_p[idx][key])))
                Sc = cmath.rect(10 ** (float(dB_c[idx][key]) / 20.0), math.radians(float(arg_c[idx][key])))
            except ValueError:
                continue
            found_any = True
            max_delta = max(max_delta, abs(Sc - Sp))
    return max_delta if found_any else None


def _collect_amr_rows(output_dir, iteration_dirs):
    """One row per AMR iteration subfolder, plus the root output_dir as
    'Final': (label, palace_json_dict_or_None, error_indicators_dict_or_None,
    max_delta_s_vs_previous_row_or_None).
    """
    dirs_in_order = list(iteration_dirs) + [output_dir]
    labels = [os.path.basename(d) for d in iteration_dirs] + ["Final"]

    rows = []
    prev_port_s = None
    for label, d in zip(labels, dirs_in_order):
        summary = _read_palace_json(d)
        errors = _read_error_indicators(d)
        port_s = _read_port_s_data(d)
        delta_s = _max_delta_s(prev_port_s, port_s)
        rows.append((label, summary, errors, delta_s))
        prev_port_s = port_s
    return rows


def _list_iteration_dirs(output_dir):
    if not os.path.isdir(output_dir):
        return []
    found = []
    for name in os.listdir(output_dir):
        match = _ITERATION_RE.match(name)
        full_path = os.path.join(output_dir, name)
        if match and os.path.isdir(full_path):
            found.append((int(match.group(1)), full_path))
    found.sort(key=lambda item: item[0])
    return [full_path for _, full_path in found]


def _format_duration(seconds):
    if seconds is None:
        return 'n/a'
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def _format_ram(mb):
    if mb is None:
        return 'n/a'
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def _format_int(n):
    return 'n/a' if n is None else f"{n:,}"


def _format_sci(x):
    return 'n/a' if x is None else f"{x:.3e}"


def _format_delta(x):
    return 'n/a' if x is None else f"{x:.4f}"


def _save_convergence_summary(run_path, summary_text):
    """Save the AMR summary table alongside the run's config.json /
    port_information.json, so it's available on disk without re-running the
    simulation or digging through the setupEM log.
    """
    try:
        with open(os.path.join(run_path, "mesh_convergence_summary.txt"), "w", encoding="utf-8") as f:
            f.write(summary_text + "\n")
    except OSError:
        pass


def build_results_summary(run_path, model_basename):
    """Return a formatted multi-line summary of Palace results found under run_path,
    or an explanatory message if nothing is there yet.
    """
    output_dir = find_output_dir(run_path, model_basename)
    if not os.path.isdir(output_dir):
        return (f"No Palace results found yet (expected output directory: {output_dir}) "
                 "-- has the simulation finished?")

    iteration_dirs = _list_iteration_dirs(output_dir)

    if not iteration_dirs:
        summary = _read_palace_json(output_dir)
        errors = _read_error_indicators(output_dir)
        if summary is None:
            return (f"No palace.json found yet in {output_dir} "
                     "-- has the simulation finished?")
        lines = [
            f"=== Simulation results: {model_basename} ===",
            f"Degrees of freedom : {_format_int(summary['dof'])}",
            f"Mesh elements      : {_format_int(summary['mesh_elements'])}",
            f"Simulation time    : {_format_duration(summary['duration_s'])}",
            f"Peak RAM           : {_format_ram(summary['peak_ram_mb'])}",
        ]
        if errors:
            lines.append(
                f"Error indicator    : Norm={_format_sci(errors['norm'])}  "
                f"Max={_format_sci(errors['maximum'])}"
            )
        lines.append("=" * 40)
        return "\n".join(lines)

    # Adaptive mesh refinement: one row per iteration subfolder, plus the root as "Final"
    rows = _collect_amr_rows(output_dir, iteration_dirs)

    headers = ["Iteration", "DOF", "Mesh elems", "Error Norm", "Error Max",
               "Max dS", "Time", "Peak RAM"]
    table_rows = []
    for label, summary, errors, delta_s in rows:
        summary = summary or {}
        errors = errors or {}
        table_rows.append([
            label,
            _format_int(summary.get('dof')),
            _format_int(summary.get('mesh_elements')),
            _format_sci(errors.get('norm')),
            _format_sci(errors.get('maximum')),
            _format_delta(delta_s),
            _format_duration(summary.get('duration_s')),
            _format_ram(summary.get('peak_ram_mb')),
        ])

    widths = [max(len(headers[i]), *(len(r[i]) for r in table_rows)) for i in range(len(headers))]

    def fmt_row(cells):
        return " | ".join(
            cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
            for i, cell in enumerate(cells)
        )

    lines = [f"=== Simulation results: {model_basename} (adaptive mesh refinement) ==="]
    header_line = fmt_row(headers)
    lines.append(header_line)
    lines.append("-+-".join("-" * w for w in widths))
    for row in table_rows:
        lines.append(fmt_row(row))
    lines.append("=" * len(header_line))
    summary_text = "\n".join(lines)
    _save_convergence_summary(run_path, summary_text)
    return summary_text
