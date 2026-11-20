"""Live Palace status line: port and frequency progress parsed from Palace 0.18 and 0.19 logs.

The log lines are copied from real runs of the pcb_lowpass model (adaptive sweep, 119 frequencies)
with palace_0181_x86_64_v3.sif and palace_0190_x86_64_v3.sif.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def tab():
    QApplication.instance() or QApplication([])
    from setupEM.setupEM import CreateModelTab

    class Label:
        text_ = ""

        def setText(self, text):
            self.text_ = text

        def text(self):
            return self.text_

    class Log:
        def appendPlainText(self, text):
            pass

    class Window:
        APP_NAME = "setupEM"
        PalaceMode = True

    # only the parser and the status line, without building the tab's widgets
    tab = CreateModelTab.__new__(CreateModelTab)
    tab.status_line, tab.log_area, tab.MainWindow = Label(), Log(), Window()
    return tab


def feed(tab, lines):
    tab._init_status_state()
    for line in lines:
        tab._parse_palace_status_line(line)
    return tab.status_line.text()


SETUP_TWO = ["Running with 16 MPI processes",
             "Excitation 1/2 with index 1 has contributions from:",
             "Excitation 2/2 with index 2 has contributions from:",
             "Adding excitation index 1 (1/2):",
             "Greedy iteration 1 (n = 4): ω* = 5.902e+00 GHz (7.064e+00), error = 3.901e-01, memory = 0/2",
             "Adding excitation index 2 (2/2):"]


def test_palace_018_prom_online_phase(tab):
    text = feed(tab, SETUP_TWO + ["Sweeping excitation index 2 (2/2):",
                                  "It 3/119: ω/2π = 2.000e-01 GHz (total elapsed time = 8.19e+00 s)"])
    assert "Port: 2/2" in text and "Freq: 3/119 (PROM eval)" in text


def test_palace_019_prom_online_phase_names_the_excitation(tab):
    text = feed(tab, SETUP_TWO + ["It 60/119, excitation 1: ω/2π = 3.050e+00 GHz (total elapsed time = 7.62e+00 s)"])
    assert "Port: 1/2" in text and "Freq: 60/119 (PROM eval)" in text
    tab._parse_palace_status_line("It 60/119, excitation 2: ω/2π = 3.050e+00 GHz (total elapsed time = 7.62e+00 s)")
    assert "Port: 2/2" in tab.status_line.text()


def test_palace_019_sweeping_line_without_position(tab):
    """0.19 with field output: "Sweeping excitation index k:" in the online phase, no (k/N)"""
    text = feed(tab, SETUP_TWO + ["Sweeping excitation index 2:"])
    assert "Port: 2/2" in text


@pytest.mark.parametrize("it_line", [
    "It 7/119: ω/2π = 4.000e-01 GHz (total elapsed time = 5.93e+00 s)",              # 0.18
    "It 7/119, excitation 1: ω/2π = 4.000e-01 GHz (total elapsed time = 5.90e+00 s)",  # 0.19
])
def test_one_excitation_shows_port_1_of_1(tab, it_line):
    """Palace prints no "excitation index" line for a single excitation"""
    text = feed(tab, ["Excitation with index 1 has contributions from:", it_line])
    assert "Port: 1/1" in text and "Freq: 7/119 (PROM eval)" in text


def test_uniform_sweep_is_unchanged(tab):
    text = feed(tab, ["Sweeping excitation index 2 (2/8):",
                      "It 1/1: ω/2π = 9.300e+01 GHz (total elapsed time = 1.52e+01 s, solve 9/64)"])
    assert "Port: 2/8" in text and "Freq: 1/1 (solve 9/64)" in text


def test_amr_counts_refinements_up_to_the_end(tab):
    """AMR lines of a MaxIts=2 run, the same in Palace 0.18.1 and 0.19.0: the final summary keeps 2/2"""
    tab._init_status_state()
    tab._status_amr_max, tab._status_amr_cur = 2, 0       # as _reset_status_for_run() sets them
    seen = []
    for line in ["It 3/3: ω/2π = 5.000e+00 GHz (total elapsed time = 2.06e+00 s, solve 3/6)",
                 "Adaptive mesh refinement (AMR) iteration 1:",
                 "Proceeding with solve/estimate iteration 2...",
                 "Adaptive mesh refinement (AMR) iteration 2:",
                 "Proceeding with solve/estimate iteration 3...",
                 "Completed 2 iterations of adaptive mesh refinement (AMR):"]:
        tab._parse_palace_status_line(line)
        seen.append(tab.status_line.text().split("AMR iteration: ")[1])
    assert seen == ["0/2", "0/2", "1/2", "1/2", "2/2", "2/2"]


def test_amr_stopped_by_tolerance(tab):
    """AMR ends before MaxIts when the error indicator is below the tolerance"""
    tab._init_status_state()
    tab._status_amr_max, tab._status_amr_cur = 5, 0
    for line in ["Adaptive mesh refinement (AMR) iteration 1:",
                 "Proceeding with solve/estimate iteration 2...",
                 "Completed 1 iteration of adaptive mesh refinement (AMR):"]:
        tab._parse_palace_status_line(line)
    assert "AMR iteration: 1/5" in tab.status_line.text()
