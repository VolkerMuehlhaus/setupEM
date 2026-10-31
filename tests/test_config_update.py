# Tests for updating a script edited in place from a *.simcfg of the same layout (src/setupEM/config_update.py).
#
# Run from the repo root:   python -m pytest tests

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from setupEM.config_update import (layout_differences, model_scripts, newer_configs,  # noqa: E402
                                   port_differences)


def via(n, layer, frm="Metal1", to="TopMetal2", direction="Z", z0=50.0, voltage=1.0):
    return {"portnumber": n, "source_layernum": layer, "from_layername": frm, "to_layername": to,
            "target_layername": None, "direction": direction, "port_Z0": z0, "voltage": voltage}


def test_same_ports_no_differences():
    ports = [via(1, 201), via(2, 202)]
    assert port_differences(ports, [dict(p) for p in ports]) == []


def test_z0_and_excitation_are_not_compared():
    assert port_differences([via(1, 201)], [via(1, 201, z0=25.0, voltage=0.0)]) == []


def test_renumbered_new_and_removed_ports():
    old = [via(1, 201), via(2, 202), via(3, 203)]
    new = [via(1, 201), via(2, 203), via(3, 204, "Metal2", "Metal5", "-Z"), via(4, 205)]
    diff = port_differences(old, new)
    assert diff[0] == "port 2: was via Metal1 -> TopMetal2 (Z) on layer 202, now via Metal1 -> TopMetal2 (Z) on layer 203"
    assert diff[1].startswith("port 3: was") and "(-Z)" in diff[1]
    assert diff[2].startswith("port 4: new")
    assert port_differences(new, old)[-1] == "port 4: removed"


def test_direction_case_and_in_plane_ports():
    in_plane = {"portnumber": 1, "source_layernum": "201", "direction": "x", "target_layername": "Metal5",
                "from_layername": "", "to_layername": ""}
    assert port_differences([in_plane], [dict(in_plane, direction="X", source_layernum=201)]) == []


def test_layout_differences(tmp_path):
    gds = tmp_path / "chip_em.gds"
    old = {"GdsFile": str(gds), "SubstrateFile": "/x/SG13G2.xml", "cellname": "TOP", "purpose": [0]}
    assert layout_differences(old, {"GdsFile": "chip_em.gds", "purpose": ["0"]}, str(tmp_path)) == []
    diff = layout_differences(old, {"GdsFile": "other_em.gds", "cellname": "CORE"}, str(tmp_path))
    assert diff == ["GDSII file: chip_em.gds -> other_em.gds", "cell: TOP -> CORE"]


def _touch(path, text, age):
    path.write_text(text)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def test_newer_configs_and_scripts(tmp_path):
    gds = tmp_path / "chip_em.gds"
    gds.write_bytes(b"")
    config = {"application": "setupEM", "saved_values": {"GdsFile": "chip_em.gds"}, "ports": [via(1, 201)]}
    script_text = f"settings = {{}}\nsettings['GdsFile'] = {str(gds)!r}\n"
    _touch(tmp_path / "chip_em.py", script_text, 100)
    _touch(tmp_path / "chip_em.simcfg", json.dumps(config), 10)
    _touch(tmp_path / "other.simcfg", json.dumps(dict(config, saved_values={"GdsFile": "other.gds"})), 5)
    _touch(tmp_path / "thermal.simcfg", json.dumps(dict(config, application="setupThermal")), 5)

    found = newer_configs(str(tmp_path / "chip_em.py"), str(gds), [str(tmp_path), str(tmp_path)])
    assert [os.path.basename(p) for p, _ in found] == ["chip_em.simcfg"]

    # model scripts for the GDS, whatever their age against the config (the layout tool writes the config again on
    # every export), newest first
    _touch(tmp_path / "relative.py", "settings = {}\nsettings['GdsFile'] = 'chip_em.gds'\n", 2)
    _touch(tmp_path / "unrelated.py", "x = 'chip_em_old.gds'\n", 1)
    scripts = model_scripts(str(gds), [str(tmp_path), str(tmp_path)])
    assert [os.path.basename(p) for p in scripts] == ["relative.py", "chip_em.py"]

    # the script saved after the config: the config is no longer newer
    _touch(tmp_path / "chip_em.py", script_text, 1)
    assert newer_configs(str(tmp_path / "chip_em.py"), str(gds), [str(tmp_path)]) == []
