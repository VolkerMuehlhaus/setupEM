"""Join the results of a split EM region into one .sNp of the whole region.

klayout_em_export's Export All writes a split region as <export>.simcfg (with a "pieces" section) and one model per
piece in <export>_pieces/. Each piece is simulated on its own; its ports on a cut to another piece are port wall
ports, calibrated with the reference plane on the cut (gds2palace port walls). So the two ports of a cut, one in
each piece, meet at the same plane, and their de-embedded S-parameters connect directly: this module connects them
(scikit-rf Circuit) and writes the joined result where a single model of the whole region would have it:

    <sim_path>/palace_model/<export>_data/output/<export>/<export>_deembedded.sNp   (and _dc_deembedded when all
                                                                                   pieces have a DC result)

The ports of the joined result are the outer ports, numbered as in <export>.simcfg; the header names each one's
piece. Not in the joined result: coupling between the pieces other than through the lines that cross the cuts.

    python -m setupEM.join_pieces <export>.simcfg
"""
import json
import os
import re
import sys

import numpy as np


class JoinError(Exception):
    pass


def read_set(set_path):
    """(set data, its "pieces" section)."""
    with open(set_path, encoding="utf-8") as f:
        data = json.load(f)
    section = data.get("pieces")
    if not isinstance(section, dict) or not section.get("pieces"):
        raise JoinError(f"{os.path.basename(set_path)} is not a split region (no pieces section)")
    return data, section


def _model_place(simcfg_path, data, default_name):
    """(sim_path, model basename) of a model's .simcfg: as setupEM keeps them, else next to the file."""
    values = data.get("saved_values", {}) if isinstance(data, dict) else {}
    sim_path = values.get("sim_path") or os.path.dirname(os.path.abspath(simcfg_path))
    name = values.get("model_basename") or default_name
    return sim_path, name


def result_path(sim_path, name, ports, kind="_deembedded"):
    return os.path.join(sim_path, "palace_model", f"{name}_data", "output", name, f"{name}{kind}.s{ports}p")


def piece_paths(set_path, section):
    """{piece name: (simcfg path, sim_path, model basename, port count)}."""
    base = os.path.dirname(os.path.abspath(set_path))
    out = {}
    for entry in section["pieces"]:
        simcfg = os.path.join(base, entry["simcfg"])
        try:
            with open(simcfg, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        sim_path, name = _model_place(simcfg, data, entry["name"])
        out[entry["name"]] = (simcfg, sim_path, name, len(entry.get("ports", {})))
    return out


def joined_path(set_path, data, ports, kind="_deembedded"):
    sim_path, name = _model_place(set_path, data, os.path.splitext(os.path.basename(set_path))[0])
    return result_path(sim_path, name, ports, kind)


def _calibrated_ports(path):
    """{port number: header text} of the ports whose header line says they were calibrated at a port wall
    (combine_extend_snp.py)."""
    found = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("!"):
                if line.strip() and not line.startswith("#"):
                    break
                continue
            m = re.match(r"!\s*port\s+(\d+):\s*(calibrated at port wall.*)", line, re.IGNORECASE)
            if m:
                found[int(m.group(1))] = m.group(2).strip()
    return found


def join(set_path, kind="_deembedded", write=True):
    """Join the pieces' results of one kind ('_deembedded' or '_dc_deembedded'). Returns (path written or None,
    the joined skrf Network, messages). Raises JoinError when a piece's result is missing, a port on a cut is not
    calibrated, or the frequency grids differ."""
    import skrf as rf
    from skrf.circuit import Circuit
    data, section = read_set(set_path)
    places = piece_paths(set_path, section)
    networks, messages = {}, []
    for name, (simcfg, sim_path, model, ports) in places.items():
        path = result_path(sim_path, model, ports, kind)
        if not os.path.isfile(path):
            raise JoinError(f"{name}: no result {os.path.basename(path)} (run {name} first)")
        net = rf.Network(path)
        net.name = name
        networks[name] = (net, path)
    for link in section.get("links", []):
        for name, port in link["ports"]:
            if name not in networks:
                raise JoinError(f"the cut port {port} of {name} has no piece result")
            calibrated = _calibrated_ports(networks[name][1])
            if int(port) not in calibrated:
                raise JoinError(f"{name} port {port} on a cut is not calibrated at a port wall (its result was made "
                                "without the port wall calibration): run it again with the current gds2palace")
            text = calibrated[int(port)]
            if "reference plane at the wall" not in text and "reference plane at the layout edge" not in text:
                raise JoinError(f"{name} port {port} on a cut has its reference plane moved ({text}): the two pieces "
                                "of a cut meet only at the cut; set the reference plane shift of that port wall to 0")
    first = next(iter(networks.values()))[0]
    for name, (net, _) in networks.items():
        if len(net.f) != len(first.f) or not np.allclose(net.f, first.f, rtol=1e-9, atol=1e-3):
            raise JoinError(f"the frequencies of {name} differ from the other pieces: give all pieces the same "
                            "frequency settings")
    external = sorted(section.get("external", []), key=lambda e: e["port"])
    if not external:
        raise JoinError("the split region has no outer ports")
    connections = []
    for e in external:
        port = Circuit.Port(first.frequency, name=f"P{e['port']}", z0=50)
        connections.append([(port, 0), (networks[e["piece"]][0], int(e["piece_port"]) - 1)])
    for link in section.get("links", []):
        (a, pa), (b, pb) = link["ports"]
        connections.append([(networks[a][0], int(pa) - 1), (networks[b][0], int(pb) - 1)])
    # every port of every piece must appear exactly once
    used = {}
    for conn in connections:
        for net, index in conn:
            if net.name in networks:
                used.setdefault(net.name, set()).add(index)
    for name, (net, _) in networks.items():
        missing = set(range(net.nports)) - used.get(name, set())
        if missing:
            raise JoinError(f"{name} ports {', '.join(str(i + 1) for i in sorted(missing))} are neither outer ports "
                            "nor on a cut: export the split region again")
    joined = Circuit(connections).network
    out = None
    if write:
        out = joined_path(set_path, data, joined.nports, kind)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        header = [f"! joined by setupEM from the pieces {', '.join(networks)} ({section.get('folder', '')}): "
                  "each piece simulated on its own, the ports on the cuts calibrated (port walls) and connected"]
        for e in external:
            header.append(f"!port {e['port']}: {e['piece']} port {e['piece_port']} (layout port {e['layout_port']})")
        _write_touchstone(joined, out, header)
        messages.append(f"joined {len(networks)} pieces into {out}")
    return out, joined, messages


def _write_touchstone(network, path, header):
    stem = os.path.splitext(path)[0]
    network.write_touchstone(stem, form="ri")
    written = stem + f".s{network.nports}p"
    with open(written, encoding="utf-8") as f:
        text = f.read()
    with open(written, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(header) + "\n" + text)
    if os.path.normcase(written) != os.path.normcase(path):
        os.replace(written, path)


def join_all(set_path):
    """Join the de-embedded results, and the DC-extended ones when every piece has them. Returns messages."""
    out, _, messages = join(set_path, "_deembedded")
    try:
        join(set_path, "_dc_deembedded")
        messages.append("and the DC-extended results")
    except JoinError:
        pass
    return messages


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    try:
        for message in join_all(argv[0]):
            print(message)
    except JoinError as e:
        print(f"not joined: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
