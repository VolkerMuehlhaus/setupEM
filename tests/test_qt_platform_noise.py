# Qt's platform messages of a child process (snp2le's window on Wayland) stay out of setupEM's log; real errors don't.
#
# Run from the repo root:   python -m pytest tests

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from setupEM.setup_common import is_qt_platform_noise  # noqa: E402


def test_wayland_text_input_messages_are_noise():
    line = ("virtual void QtWaylandClient::QWaylandTextInputv3::disableSurface(wl_surface*) Trying to disable "
            "0x1a197430 but 0x0 is focused")
    assert is_qt_platform_noise(line)
    assert is_qt_platform_noise("This plugin does not support propagateSizeHints()")


def test_real_errors_stay():
    assert not is_qt_platform_noise("Traceback (most recent call last):")
    assert not is_qt_platform_noise("ERROR: port wall xmin has no port")
