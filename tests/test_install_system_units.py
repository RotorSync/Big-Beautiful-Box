"""deploy/install-system-units.sh: self-updating boxes get the watchdog unit and
journald cap exactly like install.sh, idempotently, and never fail an update."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "install-system-units.sh"
UNIT = "etc/systemd/system/rotorsync_watchdog.service"
JCONF = "etc/systemd/journald.conf.d/bbb.conf"


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy(REPO / "rotorsync_watchdog.service", repo / "rotorsync_watchdog.service")
    shutil.copy(REPO / "deploy" / "journald-bbb.conf", repo / "deploy" / "journald-bbb.conf")
    root = tmp_path / "root"
    root.mkdir()
    log = tmp_path / "systemctl.log"
    fake = tmp_path / "fake_systemctl"
    fake.write_text(f'#!/bin/bash\necho "$*" >> {log}\nexit ${{FAKE_RC:-0}}\n')
    fake.chmod(0o755)
    return {"repo": repo, "root": root, "log": log, "fake": fake}


def run(env, **extra):
    e = dict(os.environ, BBB_REPO=str(env["repo"]), BBB_ROOT=str(env["root"]),
             BBB_SYSTEMCTL=str(env["fake"]), **extra)
    return subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30)


def calls(env):
    return env["log"].read_text().splitlines() if env["log"].exists() else []


def test_script_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_fresh_box_installs_like_install_sh(env):
    r = run(env)
    assert r.returncode == 0, r.stderr
    assert (env["root"] / UNIT).read_bytes() == (env["repo"] / "rotorsync_watchdog.service").read_bytes()
    assert (env["root"] / JCONF).read_bytes() == (env["repo"] / "deploy/journald-bbb.conf").read_bytes()
    assert oct((env["root"] / UNIT).stat().st_mode & 0o777) == "0o644"
    c = calls(env)
    assert c == ["daemon-reload", "enable rotorsync_watchdog.service", "restart systemd-journald"]
    # never restarts the watchdog itself (callers restart it last)
    assert not any("restart rotorsync_watchdog" in x for x in c)


def test_second_run_is_noop_except_enable(env):
    run(env)
    env["log"].unlink()
    mtime = (env["root"] / UNIT).stat().st_mtime_ns
    r = run(env)
    assert r.returncode == 0
    assert calls(env) == ["enable rotorsync_watchdog.service"]
    assert (env["root"] / UNIT).stat().st_mtime_ns == mtime
    assert not list((env["root"] / "etc/systemd/system").glob(".bbb-unit.*"))


def test_changed_unit_reloads_without_journald_restart(env):
    run(env)
    env["log"].unlink()
    with open(env["repo"] / "rotorsync_watchdog.service", "a") as f:
        f.write("\n# changed\n")
    run(env)
    assert calls(env) == ["daemon-reload", "enable rotorsync_watchdog.service"]
    assert (env["root"] / UNIT).read_text().endswith("# changed\n")


def test_changed_journald_only_restarts_journald(env):
    run(env)
    env["log"].unlink()
    with open(env["repo"] / "deploy/journald-bbb.conf", "a") as f:
        f.write("# changed\n")
    run(env)
    assert calls(env) == ["enable rotorsync_watchdog.service", "restart systemd-journald"]


def test_missing_sources_still_exit_zero(env):
    (env["repo"] / "rotorsync_watchdog.service").unlink()
    (env["repo"] / "deploy/journald-bbb.conf").unlink()
    r = run(env)
    assert r.returncode == 0
    assert calls(env) == []
    assert "WARN" in r.stderr


def test_systemctl_failure_never_fails_update(env):
    r = run(env, FAKE_RC="1")
    assert r.returncode == 0
    assert "WARN" in r.stderr
    assert (env["root"] / UNIT).exists()


def test_unwritable_destination_exit_zero(env):
    if os.geteuid() == 0:
        pytest.skip("root ignores permissions")
    (env["root"] / "etc").mkdir()
    (env["root"] / "etc").chmod(0o555)
    try:
        r = run(env)
    finally:
        (env["root"] / "etc").chmod(0o755)
    assert r.returncode == 0
    assert "WARN" in r.stderr


def test_dashboard_deploy_cmd_calls_script_fail_soft():
    src = (REPO / "dashboard.py").read_text()
    line = "bash /home/pi/Big-Beautiful-Box/deploy/install-system-units.sh || true; "
    assert line in src
    # runs before the final daemon-reload / service enables
    assert src.index(line) < src.index('"systemctl enable --now rotorlink.service || true; "')


def test_streamed_update_paths_carry_unit_and_call_helper(monkeypatch, tmp_path):
    import rotorsync_bumble as rb
    from src import box_update as bu
    assert "rotorsync_watchdog.service" in rb.MAINTENANCE_RUNTIME_PATHS
    assert "rotorsync_watchdog.service" in bu.DEFAULT_RUNTIME_PATHS
    assert "deploy" in rb.MAINTENANCE_RUNTIME_PATHS and "deploy" in bu.DEFAULT_RUNTIME_PATHS

    # helpers swallow every failure
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "install-system-units.sh").write_text("exit 0\n")

    def boom(*a, **k):
        raise OSError("no bash")
    monkeypatch.setattr(subprocess, "run", boom)
    rb._install_system_units(tmp_path)
    bu.install_system_units(tmp_path)
    # missing script is a no-op
    rb._install_system_units(tmp_path / "nope")
    bu.install_system_units(tmp_path / "nope")
