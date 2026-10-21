"""Fork self-test: the systemd ``TimeoutStopUSec`` probe must ask the scope this process
actually runs in, and must never read systemd's built-in default for a unit that is absent
from the queried scope (``rc=0`` + ``TimeoutStopUSec=1min 30s`` + ``LoadState=not-found``).

A system-scope gateway (our case) otherwise reads the *user* scope's 90s default instead of
its real 3min 30s and logs a phantom ``Stale systemd unit detected`` mismatch.

Fork-only file, deliberately NOT appended to ``tests/gateway/test_shutdown_forensics.py``:
upstream moved/deleted the class this used to extend (2026-10-14), so a hunk appended to that
file can never re-apply cleanly and would block every fork update.
"""

from __future__ import annotations

from gateway import shutdown_forensics as sf


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
