"""Test isolation for anything that touches "the config file".

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
"""

from __future__ import annotations

import pytest

import blackvoice.config as _config


@pytest.fixture(autouse=True)
def _isolated_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(_config, "CONFIG_FILE", tmp_path / "config.json")
