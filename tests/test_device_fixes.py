"""Isolated device-fixes tests (S-BE-04 / S-BE-05 / S-OPT-02, Tasks 1-4).

Never import the real root plugin; never execute real setters, lifecycle,
switchers, installer or dispatcher code. Pattern: tests/test_backend_state.py
(AST-extract + fail-closed builtins + in-memory fs + scripted _run_cmd).

Covers:
- D1 bounded effective-read retry: transient unknown retried to success
  (both workers), persistent mismatch still fails, budget exhaustion notes
  attempts, rollback verification retried. The helper contract
  ``_poll_effective_backend(tries=3, delay=2.0) -> (value, services,
  attempts)`` is asserted via AST (signature/defaults) and at runtime
  (return arity, sleep budget, transient-unknown notes in errors[]).
- D2 convergence reconciliation: review-checklist greps over the
  refreshStatus body in src/index.tsx (no frontend unit harness). The
  converge branch deletes errors.wifi_backend AND sets backendSwitch to
  null; diverged/unknown/in-progress keeps both. Pre-existing sites
  (:303 terminal reconcile, :791 pre-start clear, :806 rejected start)
  are allow-listed, not violations.
- D3 per-file ASPM verify: unwritable-but-correct files are skip-notes,
  unwritable-and-wrong files fail named, missing paths stay skipped, and
  the helper ALWAYS returns the uniform 3-tuple (code, detail, notes).

Red-phase: the ``_poll_effective_backend`` fixture shim raises
AssertionError until the helper exists, and the ASPM/converge asserts are
explicit behavior asserts -- red means AssertionError, never
ImportError/NameError/AttributeError.
"""

import ast
import asyncio as _real_asyncio
import io
import posixpath as _posixpath
import re
import time as _real_time
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "main.py"
SRC_INDEX = Path(__file__).resolve().parents[1] / "src" / "index.tsx"

METHOD_NAMES = {
    "_read_desired_backend",
    "_read_effective_backend",
    "_poll_effective_backend",
    "_backend_switch_worker",
    "_generic_backend_switch_worker",
    "_ensure_backend_switch_state",
    "_friendly_backend_error",
    "_capture_slot",
    "_get_current_backend",
    "_get_backend_method",
    "_has_backend_tool",
    "_apply_pcie_aspm_fix",
}

MODULE_FUNCS = {
    "_load_settings",
    "_save_settings",
    "_load_snapshot",
    "_save_snapshot",
}

ASSIGN_NAMES = {
    "DEFAULT_SETTINGS",
    "SETTINGS_FILE",
    "SNAPSHOT_FILE",
    "GENERIC_BACKEND_CONF",
    "WIFI_BACKEND_CONF",
    "NM_DEFAULT_CONF",
    "BAZZITE_IWD_CONF",
    "BACKEND_HELPER",
    "DISPATCHER_PATH",
    "ENFORCED_FILE",
    "SYSCTL_PARAMS",
}

PCIE_DEVICE = "/sys/devices/pci0000:00/0000:00:14.3"
PCIE_LINK_FILES = [
    "l0s_aspm",
    "l1_aspm",
    "l1_1_aspm",
    "l1_2_aspm",
    "l1_1_pcipm",
    "l1_2_pcipm",
]
PCIE_POWER = "power/control"

SYSTEMCTL = "/usr/bin/systemctl"


def _ok(stdout=""):
    return {"success": True, "stdout": stdout, "stderr": "", "returncode": 0}


def _fail(stderr="simulated failure"):
    return {"success": False, "stdout": "", "stderr": stderr, "returncode": 1}


def _tree():
    return ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))


def _refresh_status_body():
    """Slice the refreshStatus callback out of src/index.tsx."""
    text = SRC_INDEX.read_text(encoding="utf-8")
    start = text.index("const refreshStatus = useCallback")
    end = text.index("const stopBackendPoll")
    assert start < end, "refreshStatus must precede stopBackendPoll"
    return text[start:end]


class _MemWriter(io.StringIO):
    """Commit-on-close in-memory write buffer for the stub filesystem."""

    def __init__(self, fixture, path):
        super().__init__()
        self._fixture = fixture
        self._path = path

    def close(self):
        if not self.closed:
            self._fixture.fs[self._path] = self.getvalue()
            self._fixture.writes.append(self._path)
        super().close()


class DeviceFixFixture:
    """AST-extracted backend/pcie methods over scriptable in-memory stubs."""

    def __init__(self):
        self.iface = "wlan0"
        self.active_uuid = "UUID-A-1111"
        self.distro = "generic"
        self.driver = "generic"
        self.helper_present = False
        self.helper_executable = False
        self.iwd_service = True
        self.conf = {}
        self.services = {
            "iwd": {"active": "inactive", "enabled": "disabled"},
            "wpa_supplicant": {"active": "inactive", "enabled": "disabled"},
        }
        self.cmd_scripts = {}
        self.cmd_failures = set()
        self.cmd_stderr = "simulated failure"
        self.run_calls = []
        self.sleep_calls = []
        self.makedirs_calls = []
        self.remove_calls = []
        self.replace_calls = []
        self.chmod_calls = []
        self.writes = []
        self.info_logs = []
        self.error_logs = []
        self.fs = {}
        self.readonly_paths = set()
        self.read_fail_paths = set()
        self.read_missing_paths = set()
        self.pcie_present = True
        self.pcie_device = PCIE_DEVICE
        self.device_info = {}

        tree = _tree()
        plugin_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Plugin"
        )
        found_methods = [
            node
            for node in plugin_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in METHOD_NAMES
        ]
        self.present_methods = {node.name for node in found_methods}

        top_assigns = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                top_assigns.append(node)
            elif isinstance(node, ast.Try):
                top_assigns.extend(
                    child for child in node.body if isinstance(child, ast.Assign)
                )
        found_assigns = [
            node
            for node in top_assigns
            if any(
                isinstance(target, ast.Name) and target.id in ASSIGN_NAMES
                for target in node.targets
            )
        ]
        found_funcs = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in MODULE_FUNCS
        ]
        assert {node.name for node in found_funcs} == MODULE_FUNCS

        fixture = self

        def _stub_open(path, mode="r", *args, **kwargs):
            p = str(path)
            if "r" in mode and "+" not in mode:
                if p in fixture.read_missing_paths:
                    raise FileNotFoundError(f"simulated disappearing sysfs knob: {p}")
                if p in fixture.read_fail_paths:
                    raise OSError(f"simulated read failure: {p}")
                if p in fixture.fs:
                    return io.StringIO(fixture.fs[p])
                raise FileNotFoundError(f"isolated fs missing: {p}")
            if "w" in mode or "a" in mode or "+" in mode:
                if p in fixture.readonly_paths:
                    raise PermissionError(f"[Errno 13] Permission denied: {p}")
                if (
                    p == fixture.GENERIC_BACKEND_CONF
                    or p in fixture.fs
                    or p.endswith(".tmp")
                ):
                    if "a" in mode and p in fixture.fs:
                        buf = _MemWriter(fixture, p)
                        buf.write(fixture.fs[p])
                        return buf
                    return _MemWriter(fixture, p)
                raise FileNotFoundError(f"isolated fs missing: {p}")
            raise ValueError(f"unsupported mode in isolated fixture: {mode}")

        def _isfile(path):
            p = str(path)
            if p in fixture.fs:
                return True
            if p == fixture.BACKEND_HELPER:
                return fixture.helper_present
            return False

        def _isdir(path):
            p = str(path)
            if not fixture.pcie_present:
                return False
            return p in (fixture.pcie_device, fixture.pcie_device + "/link")

        def _access(path, mode):
            return str(path) == fixture.BACKEND_HELPER and fixture.helper_executable

        def _realpath(path):
            p = str(path)
            if p == f"/sys/class/net/{fixture.iface}/device":
                return fixture.pcie_device
            return p

        os_stub = SimpleNamespace(
            path=SimpleNamespace(
                isfile=_isfile,
                isdir=_isdir,
                join=_posixpath.join,
                dirname=_posixpath.dirname,
                basename=_posixpath.basename,
                realpath=_realpath,
            ),
            makedirs=lambda path, exist_ok=False: fixture.makedirs_calls.append(
                str(path)
            ),
            remove=lambda path: fixture._stub_remove(str(path)),
            replace=lambda src, dst: fixture._stub_replace(str(src), str(dst)),
            chmod=lambda path, mode: fixture.chmod_calls.append((str(path), mode)),
            access=_access,
            geteuid=lambda: 0,
            environ={},
        )

        async def _noop_sleep(seconds):
            fixture.sleep_calls.append(seconds)

        asyncio_stub = SimpleNamespace(
            to_thread=_real_asyncio.to_thread,
            create_task=_real_asyncio.create_task,
            sleep=_noop_sleep,
            CancelledError=_real_asyncio.CancelledError,
        )

        time_stub = SimpleNamespace(
            sleep=lambda seconds: fixture.sleep_calls.append(seconds),
            time=_real_time.time,
        )

        namespace = {
            "__builtins__": {
                "dict": dict,
                "list": list,
                "str": str,
                "int": int,
                "bool": bool,
                "float": float,
                "len": len,
                "tuple": tuple,
                "set": set,
                "isinstance": isinstance,
                "range": range,
                "enumerate": enumerate,
                "getattr": getattr,
                "hasattr": hasattr,
                "open": _stub_open,
                "sorted": sorted,
                "min": min,
                "max": max,
                "any": any,
                "all": all,
                "sum": sum,
                "repr": repr,
                "zip": zip,
                "next": next,
                "iter": iter,
                "Exception": Exception,
                "ValueError": ValueError,
                "RuntimeError": RuntimeError,
                "AttributeError": AttributeError,
                "KeyError": KeyError,
                "TypeError": TypeError,
                "FileNotFoundError": FileNotFoundError,
                "PermissionError": PermissionError,
                "OSError": OSError,
                "StopIteration": StopIteration,
            },
            "decky": SimpleNamespace(
                logger=SimpleNamespace(
                    info=lambda *args, **kwargs: fixture.info_logs.append(
                        " ".join(str(a) for a in args)
                    ),
                    error=lambda *args, **kwargs: fixture.error_logs.append(
                        " ".join(str(a) for a in args)
                    ),
                ),
                DECKY_PLUGIN_VERSION="0.0.0-test",
                DECKY_PLUGIN_SETTINGS_DIR="/tmp/wifi-optimizer-test",
                DECKY_PLUGIN_DIR="/tmp/wifi-optimizer-test",
            ),
            "os": os_stub,
            "open": _stub_open,
            "json": __import__("json"),
            "time": time_stub,
            "asyncio": asyncio_stub,
        }
        exec(
            compile(
                ast.Module(
                    body=[*found_assigns, *found_funcs, *found_methods],
                    type_ignores=[],
                ),
                str(SOURCE),
                "exec",
            ),
            namespace,
        )
        self.ns = namespace
        self.SETTINGS_FILE = namespace["SETTINGS_FILE"]
        self.SNAPSHOT_FILE = namespace["SNAPSHOT_FILE"]
        self.GENERIC_BACKEND_CONF = namespace["GENERIC_BACKEND_CONF"]
        self.BACKEND_HELPER = namespace["BACKEND_HELPER"]
        self.WIFI_BACKEND_CONF = namespace["WIFI_BACKEND_CONF"]
        self.DEFAULT_SETTINGS = dict(namespace["DEFAULT_SETTINGS"])
        self.conf_paths = {
            "generic": self.GENERIC_BACKEND_CONF,
            "valve": self.WIFI_BACKEND_CONF,
        }

        self.plugin = type(
            "IsolatedDeviceFixPlugin",
            (),
            {name: namespace[name] for name in self.present_methods},
        )()
        # Red-phase shim: raise AssertionError (never ImportError/NameError)
        # until the D1 helper exists.
        if "_poll_effective_backend" not in self.present_methods:
            def _missing_poll(*args, **kwargs):
                raise AssertionError("_poll_effective_backend not implemented")

            self.plugin._poll_effective_backend = _missing_poll
        self.plugin._run_cmd = self._stub_run_cmd
        self.plugin._get_wifi_interface = lambda: self.iface
        self.plugin._get_active_connection_uuid = lambda: self.active_uuid
        self.plugin._get_support_tier = lambda: 2
        self.plugin._detect_distro = lambda: {"id": self.distro, "name": "Test"}

        async def _stub_device_info(*args, **kwargs):
            return dict(fixture.device_info)

        self.plugin.get_device_info = _stub_device_info
        self.plugin._ensure_backend_switch_state()
        self.seed_settings()

    # ---- stub filesystem helpers ----

    def _stub_remove(self, path):
        self.remove_calls.append(path)
        try:
            del self.fs[path]
        except KeyError:
            raise FileNotFoundError(f"isolated fs missing: {path}")

    def _stub_replace(self, src, dst):
        self.replace_calls.append((src, dst))
        self.fs[dst] = self.fs.pop(src)

    # ---- seed helpers ----

    def seed_settings(self, **overrides):
        merged = dict(self.DEFAULT_SETTINGS)
        merged.update(overrides)
        if "distro_id" not in overrides:
            merged["distro_id"] = self.distro
        if "driver" not in overrides:
            merged["driver"] = self.driver
        import json as _json

        self.fs[self.SETTINGS_FILE] = _json.dumps(merged)

    def seed_snapshot(self, snapshot):
        import json as _json

        self.fs[self.SNAPSHOT_FILE] = _json.dumps(snapshot)

    def set_conf(self, slot, backend):
        """Script one conf slot: backend 'iwd'/'wpa_supplicant'/None(missing)."""
        path = self.conf_paths[slot]
        if backend is None:
            self.fs.pop(path, None)
            self.conf.pop(slot, None)
            return
        content = (
            "[device]\nwifi.backend=iwd\nwifi.iwd.autoconnect=yes\n"
            if backend == "iwd"
            else "[device]\nwifi.backend=wpa_supplicant\n"
        )
        self.fs[path] = content
        self.conf[slot] = content

    def set_services(self, iwd_active, wpa_active,
                     iwd_enabled="enabled", wpa_enabled="enabled"):
        self.services["iwd"] = {"active": iwd_active, "enabled": iwd_enabled}
        self.services["wpa_supplicant"] = {
            "active": wpa_active, "enabled": wpa_enabled}

    def seed_pcie(self, contents, readonly=(), read_fail=(), read_missing=()):
        """Seed sysfs PCIe files and optional write/read failure behavior."""
        for name, content in contents.items():
            if "/" in name:
                path = f"{self.pcie_device}/{name}"
            else:
                path = f"{self.pcie_device}/link/{name}"
            self.fs[path] = content
        self.readonly_paths = {
            (f"{self.pcie_device}/{n}" if "/" in n
             else f"{self.pcie_device}/link/{n}")
            for n in readonly
        }
        self.read_fail_paths = {
            (f"{self.pcie_device}/{n}" if "/" in n
             else f"{self.pcie_device}/link/{n}")
            for n in read_fail
        }
        self.read_missing_paths = {
            (f"{self.pcie_device}/{n}" if "/" in n
             else f"{self.pcie_device}/link/{n}")
            for n in read_missing
        }

    def pcie_path(self, name):
        if "/" in name:
            return f"{self.pcie_device}/{name}"
        return f"{self.pcie_device}/link/{name}"

    # ---- _run_cmd stub ----

    def _stub_run_cmd(self, cmd, timeout=None, clean_env=True):
        key = tuple(cmd)
        self.run_calls.append(list(cmd))
        if key in self.cmd_scripts:
            scripted = self.cmd_scripts[key]
            if isinstance(scripted, list):
                # Prefix scripts fall through to the live service model once
                # exhausted so multi-attempt polls keep deterministic tails.
                if scripted:
                    return scripted.pop(0)
            else:
                return scripted
        if key in self.cmd_failures:
            return _fail(self.cmd_stderr)
        if len(cmd) >= 3 and cmd[0] == SYSTEMCTL and cmd[1] == "is-active":
            svc = cmd[2]
            state = self.services.get(svc, {}).get("active", "unknown")
            return _ok(state + "\n")
        if len(cmd) >= 3 and cmd[0] == SYSTEMCTL and cmd[1] == "is-enabled":
            svc = cmd[2]
            state = self.services.get(svc, {}).get("enabled", "unknown")
            return _ok(state + "\n")
        if len(cmd) >= 3 and cmd[0] == SYSTEMCTL and cmd[1] in (
                "stop", "disable", "enable", "start"):
            svc = cmd[2]
            if svc in self.services:
                if cmd[1] == "stop":
                    self.services[svc]["active"] = "inactive"
                elif cmd[1] == "start":
                    self.services[svc]["active"] = "active"
                elif cmd[1] == "enable":
                    self.services[svc]["enabled"] = "enabled"
                elif cmd[1] == "disable":
                    self.services[svc]["enabled"] = "disabled"
            return _ok("")
        return _ok("")

    def fail_command(self, *cmd, stderr="simulated failure"):
        self.cmd_failures.add(tuple(cmd))
        self.cmd_stderr = stderr

    def script_command(self, result, *cmd):
        self.cmd_scripts[tuple(cmd)] = result

    # ---- drivers ----

    async def run_generic(self, target):
        await self.plugin._generic_backend_switch_worker(target)
        return self.plugin._backend_switch["result"]

    async def run_steamos(self, target):
        self.seed_settings(distro_id="steamos")
        self.helper_present = True
        self.helper_executable = True
        await self.plugin._backend_switch_worker(target)
        return self.plugin._backend_switch["result"]


class PollHelperContractTests(unittest.TestCase):
    """D1 helper signature/defaults (AST; red until implemented)."""

    def test_poll_signature_defaults(self):
        tree = _tree()
        plugin_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Plugin"
        )
        found = [
            node
            for node in plugin_node.body
            if isinstance(node, ast.FunctionDef) and node.name == "_poll_effective_backend"
        ]
        assert found, "_poll_effective_backend helper missing in main.py"
        node = found[0]
        assert not node.args.vararg and not node.args.kwarg, (
            "helper takes exactly (self, tries, delay)"
        )
        names = [a.arg for a in node.args.args]
        assert names == ["self", "tries", "delay"], (
            f"helper args must be (self, tries, delay), got {names}"
        )
        defaults = [ast.literal_eval(d) for d in node.args.defaults]
        assert defaults == [3, 2.0], (
            f"helper defaults must be (3, 2.0), got {defaults}"
        )


class EffectiveRetryTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        return DeviceFixFixture()

    async def test_transient_unknown_retried_to_success(self):
        f = self.fixture()
        f.set_conf("generic", "wpa_supplicant")
        f.set_services(iwd_active="inactive", wpa_active="inactive")
        f.seed_snapshot({
            "backend": {"backend": "wpa_supplicant", "services": {}},
            "file_backend_conf": {"exists": True, "content": "x"},
        })
        f.script_command(
            [_ok(""), _ok("active\n")], SYSTEMCTL, "is-active", "iwd")
        result = await f.run_generic("iwd")
        assert result["success"] is True, (
            f"transient unknown must be retried to success: {result!r}")
        assert result.get("backend") == "iwd"
        assert "transient-unknown:1" in (result.get("errors") or []), (
            f"interim unknown must land in errors[] as note: {result!r}")

    async def test_transient_unknown_retried_to_success_steamos(self):
        f = self.fixture()
        f.set_conf("valve", "wpa_supplicant")
        f.set_services(iwd_active="inactive", wpa_active="inactive")
        f.seed_snapshot({
            "backend": {"backend": "wpa_supplicant", "services": {}},
            "file_backend_conf": {"exists": True, "content": "x"},
        })
        f.script_command(_ok("ok"), f.BACKEND_HELPER, "write_config", "iwd")
        f.script_command(
            _ok("units restarted"), f.BACKEND_HELPER, "restart_units",
            "wpa_supplicant")
        f.script_command(
            [_ok(""), _ok("active\n")], SYSTEMCTL, "is-active", "iwd")
        result = await f.run_steamos("iwd")
        assert result["success"] is True, (
            f"steamos transient unknown must be retried: {result!r}")
        assert result.get("backend") == "iwd"
        assert "transient-unknown:1" in (result.get("errors") or []), (
            f"steamos interim unknown must land in errors[]: {result!r}")

    async def test_persistent_mismatch_still_fails(self):
        f = self.fixture()
        f.set_conf("generic", "wpa_supplicant")
        f.set_services(iwd_active="inactive", wpa_active="active")
        f.seed_snapshot({
            "backend": {"backend": "wpa_supplicant", "services": {}},
            "file_backend_conf": {"exists": True, "content": "x"},
        })
        # Scripted ground truth overrides the worker's own service
        # mutations: effective stays wpa no matter what the worker does.
        f.script_command(
            _ok("inactive\n"), SYSTEMCTL, "is-active", "iwd")
        f.script_command(
            _ok("active\n"), SYSTEMCTL, "is-active", "wpa_supplicant")
        result = await f.run_generic("iwd")
        assert result["success"] is False
        errors = result.get("errors") or []
        assert "effective_mismatch" in errors, (
            f"persistent mismatch must keep effective_mismatch: {result!r}")
        assert not [e for e in errors if e.startswith("transient-unknown")], (
            f"determinate mismatch must not carry retry notes: {result!r}")

    async def test_budget_exhaustion_notes_attempts(self):
        f = self.fixture()
        f.script_command(
            [_ok(""), _ok(""), _ok("")], SYSTEMCTL, "is-active", "iwd")
        f.script_command(
            [_ok(""), _ok(""), _ok("")],
            SYSTEMCTL, "is-active", "wpa_supplicant")
        value, services, attempts = f.plugin._poll_effective_backend()
        assert isinstance(attempts, int), (
            f"helper must return (value, services, attempts): {(value, services, attempts)!r}")
        assert value is None, f"no services scripted active: got {value!r}"
        assert attempts == 3, f"budget must be exhausted at 3 tries: got {attempts}"
        assert f.sleep_calls == [2.0, 2.0], (
            f"budget sleeps must be [2.0, 2.0]: got {f.sleep_calls!r}")
        # Worker surface: exhaustion declares mismatch (never a verdict
        # from the first sample) with the attempts count as a note.
        g = self.fixture()
        g.set_conf("generic", "wpa_supplicant")
        g.set_services(iwd_active="inactive", wpa_active="inactive")
        g.seed_snapshot({
            "backend": {"backend": "wpa_supplicant", "services": {}},
            "file_backend_conf": {"exists": True, "content": "x"},
        })
        g.script_command(
            [_ok(""), _ok(""), _ok("")], SYSTEMCTL, "is-active", "iwd")
        g.script_command(
            [_ok(""), _ok(""), _ok("")],
            SYSTEMCTL, "is-active", "wpa_supplicant")
        result = await g.run_generic("iwd")
        assert result["success"] is False
        assert "effective_mismatch" in (result.get("errors") or []), (
            f"exhausted budget must declare mismatch: {result!r}")
        assert "transient-unknown:3" in (result.get("errors") or []), (
            f"exhaustion count must land in errors[]: {result!r}")

    async def test_rollback_verify_retried(self):
        f = self.fixture()
        f.set_conf("generic", "iwd")
        f.set_services(iwd_active="inactive", wpa_active="active")
        f.seed_snapshot({
            "backend": {"backend": "wpa_supplicant", "services": {}},
            "file_backend_conf": {"exists": False, "content": None},
        })
        f.plugin._backend_switch["repair_of"] = "iwd"
        f.fail_command(SYSTEMCTL, "start", "iwd",
                       stderr="Job for iwd.service failed")
        # Final verify sees one transient unknown, then settles on the
        # (wrong) wpa backend; the rollback verify settles on wpa too.
        f.script_command([_ok("")], SYSTEMCTL, "is-active", "iwd")
        f.script_command(
            [_ok("inactive\n")], SYSTEMCTL, "is-active", "wpa_supplicant")
        result = await f.run_generic("iwd")
        assert result["success"] is False
        assert result.get("rolled_back") is True, (
            f"retried rollback verify must roll back: {result!r}")
        assert result.get("backend") == "wpa_supplicant"
        assert "transient-unknown:1" in (result.get("errors") or []), (
            f"rollback retry note must land in errors[]: {result!r}")


class ConvergeReconcileTests(unittest.TestCase):
    """D2 refreshStatus converge branch (grep asserts, no unit harness)."""

    def test_converged_clears_both_errors(self):
        body = _refresh_status_body()
        assert "backend_desired" in body, (
            "refreshStatus must compare live.backend_desired")
        assert "backend_effective" in body, (
            "refreshStatus must compare live.backend_effective")
        assert re.search(r"delete\s+\w+\.wifi_backend", body), (
            "converge branch must delete errors.wifi_backend")
        assert "setBackendSwitch(null)" in body, (
            "converge branch must set backendSwitch to null")

    def test_diverged_keeps_errors(self):
        body = _refresh_status_body()
        assert re.search(r'!==?\s*"unknown"', body), (
            "unknown effective must not converge (keeps both errors)")

    def test_in_progress_keeps_errors(self):
        body = _refresh_status_body()
        assert "backendSwitchRef" in body, (
            "converge branch must consult the switch state via ref")
        assert "in_progress" in body, (
            "in-progress switch must keep both errors")


class PcieVerifyTests(unittest.TestCase):
    """D3 per-file ASPM verify (uniform 3-tuple + skip-notes)."""

    def check_result(self, result):
        assert isinstance(result, tuple) and len(result) == 3, (
            f"_apply_pcie_aspm_fix must return (code, detail, notes), "
            f"got {result!r}")
        return result

    def desired_contents(self, enable):
        link_val = "0" if enable else "1"
        power_val = "on" if enable else "auto"
        contents = {name: link_val for name in PCIE_LINK_FILES}
        contents[PCIE_POWER] = power_val
        return contents

    def test_unwritable_but_correct_skipped_with_note(self):
        f = DeviceFixFixture()
        f.seed_pcie(self.desired_contents(True), readonly=("l0s_aspm",))
        code, detail, notes = self.check_result(
            f.plugin._apply_pcie_aspm_fix(True))
        assert code is None and detail is None, (
            f"unsupported-but-unchanged must not fail: {(code, detail)!r}")
        assert f"aspm-skip:{f.pcie_path('l0s_aspm')}" in notes, (
            f"skip must be noted: {notes!r}")

    def test_unwritable_and_wrong_fails_named(self):
        f = DeviceFixFixture()
        contents = self.desired_contents(True)
        contents["l0s_aspm"] = "1"
        f.seed_pcie(contents, readonly=("l0s_aspm",))
        code, detail, notes = self.check_result(
            f.plugin._apply_pcie_aspm_fix(True))
        assert code == "pcie_failed", f"wrong content must fail: got {code!r}"
        assert "l0s_aspm" in (detail or ""), (
            f"failure must name the file: {detail!r}")
        assert f"aspm-skip:{f.pcie_path('l0s_aspm')}" not in notes

    def test_missing_path_is_skipped_with_note(self):
        f = DeviceFixFixture()
        contents = {
            name: ("0" if name != PCIE_POWER else "on")
            for name in PCIE_LINK_FILES + [PCIE_POWER]
            if name != "l1_2_pcipm"
        }
        seed = {name: "1" for name in PCIE_LINK_FILES if name != "l1_2_pcipm"}
        seed[PCIE_POWER] = "auto"
        f.seed_pcie(seed)
        code, detail, notes = self.check_result(
            f.plugin._apply_pcie_aspm_fix(True))
        assert (code, detail) == (None, None), (
            f"missing path must not fail: {(code, detail)!r}")
        assert notes == [f"aspm-skip:{f.pcie_path('l1_2_pcipm')}"], (
            f"missing path must be noted as skipped: {notes!r}")
        assert f.pcie_path("l1_2_pcipm") not in f.fs, (
            "missing path must stay absent: helper must not create it")
        for name, want in contents.items():
            assert f.fs[os_path(f, name)].strip() == want, (
                f"{name} must converge to {want!r}")

    def test_path_missing_during_verification_is_skipped_with_note(self):
        f = DeviceFixFixture()
        f.seed_pcie(self.desired_contents(True), read_missing=("l0s_aspm",))
        code, detail, notes = self.check_result(
            f.plugin._apply_pcie_aspm_fix(True))
        assert (code, detail) == (None, None), (
            f"disappearing sysfs knob must not fail: {(code, detail)!r}")
        assert notes == [f"aspm-skip:{f.pcie_path('l0s_aspm')}"], (
            f"missing verification path must be noted as skipped: {notes!r}")

    def test_other_verification_read_error_still_fails_named(self):
        f = DeviceFixFixture()
        f.seed_pcie(self.desired_contents(True), read_fail=("l0s_aspm",))
        code, detail, notes = self.check_result(
            f.plugin._apply_pcie_aspm_fix(True))
        assert code == "pcie_failed", (
            f"non-ENOENT read error must still fail: {(code, detail)!r}")
        assert "l0s_aspm" in (detail or ""), (
            f"failure must name the file: {detail!r}")


def os_path(fixture, name):
    return fixture.pcie_path(name)


if __name__ == "__main__":
    unittest.main()
