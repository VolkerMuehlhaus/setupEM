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


# ---------- linked scripts and the settings compared ----------

from setupEM.config_update import (linked_scripts, model_settings_snapshot, settings_differences,  # noqa: E402
                                   _same_setting)


def test_linked_scripts(tmp_path):
    (tmp_path / "chip_em.py").write_text("")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "amp.py").write_text("")
    config = str(tmp_path / "export.simcfg")
    assert linked_scripts(config, {}) == []
    assert linked_scripts(config, {"written_with": "chip_em.py", "model_script": "models/amp.py"}) == [
        (str(tmp_path / "chip_em.py"), True), (os.path.normpath(str(tmp_path / "models" / "amp.py")), False)]
    assert linked_scripts(config, {"model_script": "missing.py"}) == []
    assert linked_scripts(config, {"written_with": "models/amp.py"}) == [], "written_with: same folder only"
    assert linked_scripts(config, {"written_with": "chip_em.py", "model_script": "chip_em.py"}) == [
        (str(tmp_path / "chip_em.py"), True)]


def test_model_settings_snapshot(tmp_path):
    script = tmp_path / "amp.py"
    script.write_text("")
    snapshot = tmp_path / "amp.simcfg"
    data = {"application": "setupEM", "written_with": "amp.py", "saved_values": {"fstop": 7.0}}
    snapshot.write_text(json.dumps(data))
    os.utime(script, (1000, 1000))
    os.utime(snapshot, (1000, 1000))
    assert model_settings_snapshot(str(script)) == data
    os.utime(script, (2000, 2000))
    assert model_settings_snapshot(str(script)) is None, "the script was changed after"
    os.utime(snapshot, (3000, 3000))
    assert model_settings_snapshot(str(script), ".tsimcfg", "setupThermal") is None
    snapshot.write_text(json.dumps(dict(data, written_with="other.py")))
    os.utime(snapshot, (3000, 3000))
    assert model_settings_snapshot(str(script)) is None, "not written with this script"


def test_same_setting():
    assert _same_setting(100.0, 100) and _same_setting(1e-6, 1.0000000000001e-6)
    assert not _same_setting(100.0, 100.1)
    assert _same_setting(50, [50.0, 50.0, 50.0, 50.0]) and _same_setting([50, 50], 50)
    assert not _same_setting(50, [50.0, 40.0, 50.0, 50.0])
    assert _same_setting(["PEC", "ABC"], ("PEC", "ABC")) and not _same_setting(["PEC"], ["ABC"])
    assert _same_setting({"Metal1": 1.0}, {"Metal1": 1}) and not _same_setting({"a": 1}, {"b": 1})
    assert _same_setting("x", "x") and not _same_setting(None, 0)


def test_settings_differences():
    script = {"elmer_mode": False, "saved_values": {
        "fstop": 100.0, "margin": [50.0] * 4, "GdsFile": "a.gds", "solver_maxits": 400, "order": 2,
        "boundary": ["ABC"] * 6}}
    config = {"saved_values": {
        "fstop": 100.0, "margin": 50, "GdsFile": "b.gds", "solver_maxits": 999, "unit": 1e-6, "amr_tol": 0.01}}
    assert settings_differences(script, config) == [], \
        "layout keys, preference settings and settings only one file has are not compared"
    config["saved_values"].update(order=3, boundary=["ABC"] * 4 + ["PORTWALL", "ABC"])
    config["elmer_mode"] = True
    assert settings_differences(script, config) == [
        "solver: Palace in the script, Elmer in the config",
        "boundary: ['ABC', 'ABC', 'ABC', 'ABC', 'ABC', 'ABC'] in the script, "
        "['ABC', 'ABC', 'ABC', 'ABC', 'PORTWALL', 'ABC'] in the config",
        "order: 2 in the script, 3 in the config"]

