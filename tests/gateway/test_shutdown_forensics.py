"""Tests for gateway.shutdown_forensics — fast snapshot + async diag spawn."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest

from gateway import shutdown_forensics as sf


# ---------------------------------------------------------------------------
# _signal_name
# ---------------------------------------------------------------------------

class TestSignalName:

    def test_unknown_int_returns_signal_num_token(self):
        # Pick an integer extremely unlikely to ever be a real signal alias
        assert sf._signal_name(9999) == "signal#9999"


# ---------------------------------------------------------------------------
# snapshot_shutdown_context
# ---------------------------------------------------------------------------

class TestSnapshotShutdownContext:

    def test_handles_none_signal(self):
        ctx = sf.snapshot_shutdown_context(None)
        assert ctx["signal"] == "UNKNOWN"
        assert ctx["signal_num"] is None

    def test_includes_timestamps(self):
        before = time.time()
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        after = time.time()
        assert before <= ctx["ts"] <= after
        assert isinstance(ctx["ts_monotonic"], float)


    def test_under_systemd_false_without_invocation_id_and_normal_ppid(
        self, monkeypatch
    ):
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        # We can't actually change ppid; skip if we happen to be reaped
        # by init (e.g. running under tini).
        if os.getppid() == 1:
            pytest.skip("test process is reaped by init")
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        assert ctx["under_systemd"] is False


    def test_detects_takeover_marker_for_self(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        marker = tmp_path / ".gateway-takeover.json"
        marker.write_text(
            f'{{"target_pid": {os.getpid()}, "replacer_pid": 99999}}',
            encoding="utf-8",
        )
        ctx = sf.snapshot_shutdown_context(signal.SIGTERM)
        assert "takeover_marker" in ctx
        assert ctx["takeover_marker_for_self"] is True


# ---------------------------------------------------------------------------
# format_context_for_log / context_as_json
# ---------------------------------------------------------------------------

class TestFormatters:


    def test_context_as_json_handles_unserialisable_values(self):
        ctx = {"signal": "SIGTERM", "weird": object()}
        payload = sf.context_as_json(ctx)
        # default=str means objects get repr'd, JSON stays valid
        decoded = json.loads(payload)
        assert decoded["signal"] == "SIGTERM"
        assert "weird" in decoded


# ---------------------------------------------------------------------------
# spawn_async_diagnostic
# ---------------------------------------------------------------------------

class TestSpawnAsyncDiagnostic:
    # The diagnostic wraps its script in GNU coreutils ``timeout`` and the script
    # body is Linux-only (``ps auxf --sort``, ``/proc/loadavg``, ``dmesg``,
    # ``pstree``). On hosts without ``timeout`` (macOS) Popen raises and the
    # producer returns None by design (fail-soft), so the spawn can only be
    # observed on Linux.
    @pytest.mark.linux_only
    def test_spawns_subprocess_and_writes_output(self, tmp_path):
        log_path = tmp_path / "diag.log"
        pid = sf.spawn_async_diagnostic(log_path, "SIGTERM", timeout_seconds=3.0)
        assert pid is not None and pid > 0

        # Wait briefly for the subprocess to write — bounded by its own timeout.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if log_path.exists() and log_path.stat().st_size > 0:
                # Wait a touch longer for the script to finish writing
                time.sleep(0.2)
                break
            time.sleep(0.1)

        # Reap the subprocess so it doesn't show up as a zombie.
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass

        assert log_path.exists()
        contents = log_path.read_text(encoding="utf-8", errors="replace")
        assert "shutdown diagnostic" in contents
        assert "SIGTERM" in contents


# ---------------------------------------------------------------------------
# parse_systemd_duration_to_us
# ---------------------------------------------------------------------------

class TestParseSystemdDuration:
    def test_seconds(self):
        assert sf.parse_systemd_duration_to_us("90s") == 90 * 1_000_000

    def test_minutes(self):
        assert sf.parse_systemd_duration_to_us("3min") == 180 * 1_000_000


# ---------------------------------------------------------------------------
# check_systemd_timing_alignment
# ---------------------------------------------------------------------------

class TestCheckSystemdTimingAlignment:

    def test_returns_none_when_unit_undeterminable(self, monkeypatch):
        monkeypatch.setenv("INVOCATION_ID", "abc")
        # /proc/self/cgroup likely doesn't end in .service for the test runner
        result = sf.check_systemd_timing_alignment(180.0)
        # Either None (we couldn't find a unit) or a dict with mismatch info
        # for whatever unit pytest IS in.  Both are valid; we just ensure
        # the function doesn't raise.
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# systemd probe scope — a unit missing from a scope still answers rc=0 with
# systemd's built-in DEFAULTS, which must never be read as that unit's value.
# ---------------------------------------------------------------------------

class _Completed:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.returncode = stdout, returncode


# systemd's own default for a unit that does not exist in the queried scope
NOT_FOUND_DEFAULT = "TimeoutStopUSec=1min 30s\nLoadState=not-found\n"
LIVE_UNIT_3MIN30 = "TimeoutStopUSec=3min 30s\nLoadState=loaded\n"


def _fake_cgroup(monkeypatch, text: str) -> None:
    real_open = open

    def _open(path, *args, **kwargs):
        if str(path) == "/proc/self/cgroup":
            import io
            return io.StringIO(text)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _open)


def _fake_systemctl(monkeypatch, *, user: str, system: str, rc: int = 0):
    """Records the probe order; answers per scope."""
    calls: list = []

    def _run(cmd, **_kwargs):
        calls.append(list(cmd))
        return _Completed(user if "--user" in cmd else system, rc)

    monkeypatch.setattr(sf.subprocess, "run", _run)
    return calls


class TestSystemdProbeScope:

    def test_live_scope_is_probed_first(self, monkeypatch):
        system_calls = _fake_systemctl(monkeypatch, user=NOT_FOUND_DEFAULT, system=LIVE_UNIT_3MIN30)
        assert sf._systemd_timeout_stop_us("hermes-gateway.service", prefer_user=False) == 210_000_000
        assert "--user" not in system_calls[0]

        user_calls = _fake_systemctl(monkeypatch, user=LIVE_UNIT_3MIN30, system=NOT_FOUND_DEFAULT)
        assert sf._systemd_timeout_stop_us("hermes-gateway.service", prefer_user=True) == 210_000_000
        assert "--user" in user_calls[0]

    def test_alignment_ignores_not_found_default_from_the_other_scope(self, monkeypatch):
        monkeypatch.setenv("INVOCATION_ID", "boot-1")
        _fake_cgroup(monkeypatch, "0::/system.slice/hermes-gateway.service\n")
        _fake_systemctl(monkeypatch, user=NOT_FOUND_DEFAULT, system=LIVE_UNIT_3MIN30)

        result = sf.check_systemd_timing_alignment(180.0, 30.0)

        assert result is not None
        # Reads the live unit, not the user scope's 90s default
        assert result["timeout_stop_sec"] == 210.0
        assert result["mismatch"] is False  # 210 >= 180 + 30

    def test_not_found_in_every_scope_is_undeterminable(self, monkeypatch):
        _fake_systemctl(monkeypatch, user=NOT_FOUND_DEFAULT, system=NOT_FOUND_DEFAULT)
        assert sf._systemd_timeout_stop_us("hermes-gateway.service", prefer_user=False) is None

    def test_nonzero_returncode_is_skipped(self, monkeypatch):
        _fake_systemctl(monkeypatch, user=NOT_FOUND_DEFAULT, system=LIVE_UNIT_3MIN30, rc=1)
        assert sf._systemd_timeout_stop_us("hermes-gateway.service", prefer_user=False) is None
