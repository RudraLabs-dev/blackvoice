"""Test isolation for anything that touches a real per-user file.

:meth:`blackvoice.config.Config.save` and ``.load`` default to the module
global ``CONFIG_FILE`` - the real ``~/.config/blackvoice/config.json`` -
whenever a caller does not hand them an explicit path. That default is
correct for the real app (``control_socket.apply_config`` is meant to persist
a setting the user just changed), but it means any test that reaches
``apply_config`` without first overriding ``CONFIG_FILE`` writes straight into
the developer's real config instead of a throwaway one.

That is exactly what happened: `test_set_then_get_config_round_trips`
exercises the real "set_config" control-socket op, which calls
``apply_config`` -> ``config.save()``, and only the *socket* path was
isolated to ``tmp_path`` - the config file save was not - so running the
suite overwrote a real machine's live config, including its
``control.socket_path``, with test data.

Patching ``CONFIG_FILE`` here rather than in each test is what actually
closes this: ``Config.save``/``Config.load`` look it up from
``blackvoice.config``'s own module namespace at call time, so every call
anywhere in the codebase is covered, including ones - like
``apply_config`` - the test author never has to remember to isolate.

``TIMERS_FILE`` gets the same treatment, pre-emptively rather than after a
second incident: UtilsSkill now persists timers/reminders to it so they
survive a restart (see skills/utils.py), read and written from
``blackvoice.skills.utils``'s own imported name - a plain ``from ..config
import TIMERS_FILE`` binds it into that module's namespace at import time, so
patching ``blackvoice.config.TIMERS_FILE`` alone would not reach it, the same
gap that let the CONFIG_FILE incident happen. Existing tests already
monkeypatch ``blackvoice.skills.utils.NOTES_FILE`` per-test for the same
reason (see test_skills.py) - patched here too as a global backstop, so a
test that forgets to still cannot reach a real machine's file.
"""

from __future__ import annotations

import pytest

import blackvoice.config as _config
import blackvoice.skills.utils as _utils


@pytest.fixture(autouse=True)
def _isolated_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(_config, "CONFIG_FILE", tmp_path / "config.json")

    timers_file = tmp_path / "timers.json"
    monkeypatch.setattr(_config, "TIMERS_FILE", timers_file)
    monkeypatch.setattr(_utils, "TIMERS_FILE", timers_file)

    notes_file = tmp_path / "notes.md"
    monkeypatch.setattr(_config, "NOTES_FILE", notes_file)
    monkeypatch.setattr(_utils, "NOTES_FILE", notes_file)
