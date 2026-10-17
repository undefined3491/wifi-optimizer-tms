"""Isolated ST0-007/008 truthful-results tests (red first, then green).

Never import the real root plugin; never execute real setters, lifecycle,
installer or dispatcher code. Pattern: tests/test_restoration.py
(AST-extract + fail-closed builtins + in-memory fs).

The fixture execs _hard_reconnect, the setters, _clear_owned_profiles,
optimize_safe, reapply_all, reset_settings and get_status from main.py
against scriptable stubs: _run_cmd with per-command scripted results plus a
run_calls log, scripted _get_wifi_interface / _get_active_connection_uuid,
in-memory settings/snapshot, and a time.sleep no-op.
"""

import ast
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

UUID_A = "UUID-A-1111"
UUID_B = "UUID-B-2222"
IFACE = "wlan0"
FIXED_TIME = 1234567890

SOURCE = Path(__file__).resolve().parents[1] / "main.py"
TEMPLATE = Path(__file__).resolve().parents[1] / "defaults" / "dispatcher.sh.tmpl"

METHOD_NAMES = {
    "get_status",
    "_compute_drift_flags",
    "_parse_dns_ips",
    "_is_dns_drifted",
    "set_power_save",
    "set_auto_fix",
    "set_bssid_lock",
    "set_band_preference",
    "set_dns",
    "set_ipv6",
    "set_buffer_tuning",
    "set_cake",
    "optimize_safe",
    "reapply_all",
    "reset_settings",
    "_hard_reconnect",
    "_clear_owned_profiles",
    "_resolve_uuid",
    "_require_wifi",
    "_clear_transferred_profile",
    "_unexpected_response",
    "_capture_slot",
    "_restore_slot",
    "_apply_driver_fixes",
    "_apply_pcie_aspm_fix",
    "_install_dispatcher",
    "_remove_dispatcher",
    "_get_cake_status",
    "_get_support_tier",
    "_detect_distro",
}

MODULE_FUNCS = {
    "_load_settings",
    "_save_settings",
    "_save_settings_with_timestamp",
    "_load_snapshot",
    "_save_snapshot",
}

ASSIGN_NAMES = {
    "DEFAULT_SETTINGS",
    "SYSCTL_PARAMS",
    "SYSCTL_DEFAULTS",
    "DNS_PROVIDERS",
    "DRIVER_PROFILES",
    "DISPATCHER_PATH",
    "NM_CONF_PATH",
    "MODPROBE_CONF_PATH",
    "GENERIC_BACKEND_CONF",
    "SETTINGS_FILE",
    "ENFORCED_FILE",
    "SNAPSHOT_FILE",
}

CUSTOM_SYSCTL = {  # foreign custom values, distinct per key
    "net.core.rmem_max": "70001",
    "net.core.wmem_max": "70002",
    "net.core.rmem_default": "70003",
    "net.core.wmem_default": "70004",
    "net.core.netdev_max_backlog": "70005",
    "net.core.netdev_budget": "70006",
    "net.core.netdev_budget_usecs": "70007",
    "net.ipv4.tcp_slow_start_after_idle": "70008",
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
SEED_QDISC = "qdisc cake 8001: root refcnt 2 unlimited diffserv4 nat ack-filter"

# Split so this file never contains the guarded literal consecutively.
PRIORITY_LITERAL = "autoconnect" + "-priority"


def _ok(stdout=""):
    return {"success": True, "stdout": stdout, "stderr": "", "returncode": 0}


def _fail(stderr="simulated failure"):
    return {"success": False, "stdout": "", "stderr": stderr, "returncode": 1}


class _MemWriter(io.StringIO):
    """Commit-on-close in-memory write buffer for the stub filesystem."""

    def __init__(self, fixture, path):
        super().__init__()
        self._fixture = fixture
        self._path = path

    def close(self):
        if not self.closed:
            self._fixture.fs[self._path] = self.getvalue()
            self._fixture.write_counts[self._path] = (
                self._fixture.write_counts.get(self._path, 0) + 1
            )
        super().close()


class TruthfulFixture:
    """AST-extracted plugin methods over scriptable in-memory stubs."""

    def __init__(self):
        self.iface = IFACE
        self.active_uuid = UUID_A
        self.link_bssid = "AA:BB:CC:DD:EE:FF"
        self.power_save_off = False
        self.sysctl = dict(CUSTOM_SYSCTL)
        self.txq = {IFACE: "3000"}
        self.qdisc_output = ""
        self.nm_profiles = {}
        # Failure knobs (all default to success).
        self.nmcli_fail_keys = set()
        self.nmcli_fail_pairs = set()
        self.nmcli_fail_uuid = None
        self.nmcli_stderr = "simulated nmcli failure"
        self.radio_off_ok = True
        self.radio_off_stderr = "simulated radio-off failure"
        self.radio_on_ok = True
        self.radio_on_stderr = "simulated radio-on failure"
        self.con_up_ok = True
        self.con_up_stderr = "simulated activation failure"
        # Successive `con show --active` answers for the reconnect
        # verification poll (None = no active wifi). When empty, the poll
        # observes self.active_uuid like every other reader.
        self.verify_sequence = []
        self.sysctl_fail_keys = set()
        self.sysctl_stderr = "simulated sysctl failure"
        self.txq_fail = False
        self.txq_stderr = "simulated txqueuelen failure"
        self.tc_del_stderr = None
        self.tc_replace_ok = True
        self.tc_replace_stderr = "simulated qdisc failure"
        self.modprobe_ok = True
        self.modprobe_stderr = "simulated modprobe failure"
        self.fail_paths = set()
        self.pcie_present = True
        self.pcie_device = PCIE_DEVICE
        self.backend_available = False
        self.backend_current = None
        self.device_info = {
            "model": "lcd",
            "driver": "rtw88",
            "device_family": "deck_lcd",
            "device_label": "Steam Deck LCD",
            "chip_label": "WiFi 5 (RTL8822CE)",
            "supports_6ghz": False,
        }
        self.fs = {}
        self.dirs = set()
        self.info_logs = []
        self.error_logs = []
        self.run_calls = []
        self.nmcli_calls = []
        self.sleep_calls = []
        self.remove_calls = []
        self.chmod_calls = []
        self.write_counts = {}
        # Generic per-command override, checked before the stateful
        # defaults: list of (predicate(cmd) -> bool, result | fn(cmd)).
        self.run_script = []
        self.template_text = TEMPLATE.read_text(encoding="utf-8")
        import posixpath as _posixpath

        self.posix_template = _posixpath.join(
            "/tmp/wifi-optimizer-test", "defaults", "dispatcher.sh.tmpl"
        )

        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        plugin = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Plugin"
        )
        # Lenient extraction (no hard presence assert): pre-change main.py
        # has every name here, and any future helper stays optional so this
        # file imports clean on any baseline.
        found_methods = [
            node
            for node in plugin.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in METHOD_NAMES
        ]
        self.present_methods = {node.name for node in found_methods}
        self.missing_methods = set(METHOD_NAMES) - self.present_methods

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

        fixture = self

        def _stub_open(path, mode="r", *args, **kwargs):
            p = str(path)
            if "r" in mode and "+" not in mode:
                if p == str(TEMPLATE) or p == fixture.posix_template:
                    return io.StringIO(fixture.template_text)
                if p in fixture.fs:
                    return io.StringIO(fixture.fs[p])
                raise FileNotFoundError(f"isolated fs missing: {p}")
            if "w" in mode or "a" in mode or "+" in mode:
                if p in fixture.fail_paths:
                    raise PermissionError(f"write blocked in isolated fixture: {p}")
                allowed_prefixes = (
                    "/tmp/wifi-optimizer-test",
                    "/etc/NetworkManager/",
                    "/etc/modprobe.d/",
                    "/sys/",
                )
                if p in fixture.fs or p.startswith(allowed_prefixes):
                    if "a" in mode and p in fixture.fs:
                        buf = _MemWriter(fixture, p)
                        buf.write(fixture.fs[p])
                        return buf
                    return _MemWriter(fixture, p)
                raise PermissionError(f"write blocked in isolated fixture: {p}")
            raise ValueError(f"unsupported mode in isolated fixture: {mode}")

        def _isfile(path):
            return str(path) in fixture.fs

        def _isdir(path):
            p = str(path)
            if p in fixture.dirs:
                return True
            if fixture.pcie_present and p in (
                fixture.pcie_device,
                fixture.pcie_device + "/link",
            ):
                return True
            return False

        def _realpath(path):
            p = str(path)
            if p == f"/sys/class/net/{fixture.iface}/device":
                return fixture.pcie_device
            return p

        def _makedirs(path, exist_ok=False):
            fixture.dirs.add(str(path))

        def _remove(path):
            fixture.remove_calls.append(str(path))
            try:
                del fixture.fs[str(path)]
            except KeyError:
                raise FileNotFoundError(f"isolated fs missing: {path}")

        def _replace(src, dst):
            fixture.fs[str(dst)] = fixture.fs.pop(str(src))

        def _chmod(path, mode):
            fixture.chmod_calls.append((str(path), mode))

        os_stub = SimpleNamespace(
            path=SimpleNamespace(
                isfile=_isfile,
                isdir=_isdir,
                join=__import__("posixpath").join,
                dirname=__import__("posixpath").dirname,
                basename=__import__("posixpath").basename,
                realpath=_realpath,
            ),
            makedirs=_makedirs,
            remove=_remove,
            replace=_replace,
            chmod=_chmod,
            access=lambda path, mode: False,
            listdir=lambda path: [],
            getmtime=lambda path: 0,
            environ={},
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
                "any": any,
                "all": all,
                "getattr": getattr,
                "hasattr": hasattr,
                "open": _stub_open,
                "Exception": Exception,
                "ValueError": ValueError,
                "RuntimeError": RuntimeError,
                "AttributeError": AttributeError,
                "KeyError": KeyError,
                "TypeError": TypeError,
                "FileNotFoundError": FileNotFoundError,
                "PermissionError": PermissionError,
                "OSError": OSError,
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
            "json": json,
            "time": SimpleNamespace(
                sleep=lambda seconds: fixture.sleep_calls.append(seconds),
                time=lambda: FIXED_TIME,
            ),
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
        self.DISPATCHER_PATH = namespace["DISPATCHER_PATH"]
        self.NM_CONF_PATH = namespace["NM_CONF_PATH"]
        self.MODPROBE_CONF_PATH = namespace["MODPROBE_CONF_PATH"]
        self.GENERIC_BACKEND_CONF = namespace["GENERIC_BACKEND_CONF"]
        self.SYSCTL_PARAMS = dict(namespace["SYSCTL_PARAMS"])
        self.DEFAULT_SETTINGS = dict(namespace["DEFAULT_SETTINGS"])

        self.plugin = type(
            "IsolatedTruthfulPlugin",
            (),
            {name: namespace[name] for name in self.present_methods},
        )()
        self.plugin._run_cmd = self._stub_run_cmd
        self.plugin._nmcli_modify = self._stub_nmcli_modify
        self.plugin._get_wifi_interface = lambda: self.iface
        self.plugin._get_active_connection_uuid = lambda: self.active_uuid
        self.plugin._has_backend_tool = lambda: self.backend_available
        self.plugin._get_current_backend = lambda: self.backend_current

        async def _stub_device_info(*args, **kwargs):
            return dict(fixture.device_info)

        self.plugin.get_device_info = _stub_device_info
        self.seed_settings()

    # ---- seed/read helpers ----

    def seed_settings(self, **overrides):
        merged = dict(self.DEFAULT_SETTINGS)
        merged.update(overrides)
        self.fs[self.SETTINGS_FILE] = json.dumps(merged)

    def get_settings(self):
        return self.ns["_load_settings"]()

    def settings_writes(self):
        return self.write_counts.get(self.SETTINGS_FILE, 0)

    def read_snapshot(self):
        raw = self.fs.get(self.SNAPSHOT_FILE)
        if raw is None:
            return None
        return json.loads(raw)

    def radio_calls(self):
        return [
            cmd
            for cmd in self.run_calls
            if cmd[:3] == ["/usr/bin/nmcli", "radio", "wifi"]
            or cmd[1:3] == ["con", "up"]
        ]

    def sysctl_read_calls(self):
        return [
            cmd
            for cmd in self.run_calls
            if cmd[:2] == ["/usr/bin/sysctl", "-n"]
        ]

    # ---- stubs ----

    def _stub_run_cmd(self, cmd, timeout=5, clean_env=True):
        self.run_calls.append(list(cmd))
        cmd = list(cmd)
        for pred, res in self.run_script:
            if pred(list(cmd)):
                return res(list(cmd)) if callable(res) else dict(res)
        if cmd[0] == "/usr/bin/nmcli":
            if cmd[1:4] == ["radio", "wifi", "off"]:
                return _ok("") if self.radio_off_ok else _fail(self.radio_off_stderr)
            if cmd[1:4] == ["radio", "wifi", "on"]:
                return _ok("") if self.radio_on_ok else _fail(self.radio_on_stderr)
            if cmd[1:3] == ["con", "up"]:
                return _ok("") if self.con_up_ok else _fail(self.con_up_stderr)
            if "-f" in cmd:
                field = cmd[cmd.index("-f") + 1]
                rest = cmd[cmd.index("-f") + 2 :]
                if rest[:2] == ["con", "show"] and "--active" in rest:
                    if self.verify_sequence:
                        entry = self.verify_sequence.pop(0)
                        if entry is None:
                            return _ok("")
                        return _ok(f"{entry}:802-11-wireless")
                    if self.active_uuid:
                        return _ok(f"{self.active_uuid}:802-11-wireless")
                    return _ok("")
                if rest[:2] == ["con", "show"] and "uuid" in rest:
                    uuid = rest[rest.index("uuid") + 1]
                    value = self.nm_profiles.get(uuid, {}).get(field, "")
                    if field == "802-11-wireless.bssid" and value:
                        value = value.replace(":", "\\:")
                    return _ok(f"{field}:{value}")
                if rest[:2] == ["dev", "show"]:
                    if field == "IP4.ADDRESS":
                        return _ok("IP4.ADDRESS[1]:192.168.1.100/24")
                    if field == "IP4.DNS":
                        return _ok("IP4.DNS[1]:9.9.9.9")
                    return _ok("")
            return _ok("")
        if cmd[0] == "/usr/bin/iw":
            sub = cmd[3:]
            if sub == ["get", "power_save"]:
                return _ok(
                    f"Power save: {'off' if self.power_save_off else 'on'}"
                )
            if sub[:2] == ["set", "power_save"]:
                self.power_save_off = sub[2] == "off"
                return _ok("")
            if sub == ["link"]:
                if self.link_bssid:
                    return _ok(
                        f"Connected to {self.link_bssid} (on {self.iface})\n"
                        "\tsignal: -50 dBm"
                    )
                return _ok("Not connected.")
            if sub == ["info"]:
                return _ok(
                    "Interface wlan0\n"
                    "\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz"
                )
            return _ok("")
        if cmd[0] == "/usr/bin/sysctl":
            if cmd[1] == "-n" and len(cmd) == 3:
                return _ok(self.sysctl.get(cmd[2], ""))
            if cmd[1] == "-w" and len(cmd) == 3 and "=" in cmd[2]:
                key, _, value = cmd[2].partition("=")
                if key in self.sysctl_fail_keys:
                    return _fail(self.sysctl_stderr)
                self.sysctl[key] = value
                return _ok(f"{key} = {value}")
            return _ok("")
        if cmd[0] == "/usr/bin/ip":
            if cmd[1:3] == ["link", "show"]:
                txq = self.txq.get(self.iface, "1000")
                return _ok(
                    f"2: {self.iface}: <BROADCAST,MULTICAST,UP,LOWER_UP> "
                    f"mtu 1500 qdisc noqueue state UP mode DORMANT group "
                    f"default qlen {txq}"
                )
            if cmd[1:3] == ["link", "set"]:
                if "txqueuelen" in cmd:
                    if self.txq_fail:
                        return _fail(self.txq_stderr)
                    self.txq[cmd[3]] = cmd[cmd.index("txqueuelen") + 1]
                return _ok("")
            return _ok("")
        if cmd[0] == "/usr/bin/tc":
            if cmd[1:3] == ["qdisc", "show"]:
                return _ok(self.qdisc_output)
            if cmd[1:3] == ["qdisc", "del"]:
                if self.tc_del_stderr is not None:
                    if (
                        "No such qdisc" in self.tc_del_stderr
                        or "No such file" in self.tc_del_stderr
                    ):
                        # Idempotent del: nothing installed, counts as success.
                        self.qdisc_output = ""
                        return _ok("")
                    return _fail(self.tc_del_stderr)
                self.qdisc_output = ""
                return _ok("")
            if cmd[1:3] == ["qdisc", "replace"]:
                if not self.tc_replace_ok:
                    return _fail(self.tc_replace_stderr)
                self.qdisc_output = SEED_QDISC
                return _ok("")
            return _ok("")
        if "modprobe" in cmd[0]:
            return _ok("") if self.modprobe_ok else _fail(self.modprobe_stderr)
        return _ok("")

    def _stub_nmcli_modify(self, uuid, key, value, timeout=5):
        self.nmcli_calls.append((uuid, key, value))
        fail_uuid = self.nmcli_fail_uuid
        uuid_match = True
        if fail_uuid is not None:
            if isinstance(fail_uuid, (set, list, tuple)):
                uuid_match = uuid in fail_uuid
            else:
                uuid_match = uuid == fail_uuid
        pair_match = (key, value) in self.nmcli_fail_pairs
        if (key in self.nmcli_fail_keys or pair_match) and uuid_match:
            return {
                "success": False,
                "stdout": "",
                "stderr": self.nmcli_stderr,
                "returncode": 1,
            }
        self.nm_profiles.setdefault(uuid, {})[key] = value
        return {"success": True, "stdout": "", "stderr": "", "returncode": 0}


class TruthfulResultsTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, **overrides):
        f = TruthfulFixture()
        f.seed_settings(**overrides)
        return f

    async def test_radio_on_fail_reports_dead_radio(self):
        f = self.fixture()
        f.radio_on_ok = False
        f.radio_on_stderr = "radio still off"
        result = await f.plugin.set_bssid_lock(True)
        # The profile mutation succeeded; only the reconnect is unverified.
        self.assertTrue(result["success"])
        self.assertFalse(result["reconnected"])
        reconnect = result.get("reconnect") or {}
        self.assertTrue(reconnect.get("attempted"))
        self.assertFalse(reconnect.get("verified"))
        self.assertTrue(reconnect.get("radio_off_without_on"))
        stages = reconnect.get("stages") or {}
        self.assertFalse((stages.get("radio_on") or {}).get("ok"))
        self.assertIn("radio", (stages.get("radio_on") or {}).get("stderr", ""))

    async def test_reconnect_unverified_never_true(self):
        f = self.fixture()
        # Radio cycles fine and activation is accepted, but no wifi comes
        # back: verified must stay False and reconnected must never be True.
        f.verify_sequence = [None, None]
        result = await f.plugin.set_bssid_lock(True)
        self.assertTrue(result["success"])
        self.assertFalse(result["reconnected"])
        reconnect = result.get("reconnect") or {}
        self.assertTrue(reconnect.get("attempted"))
        self.assertFalse(reconnect.get("verified"))
        self.assertEqual(reconnect.get("active_uuid_after"), None)
        # Two verification checks with one sleep(1) between them.
        self.assertEqual(f.sleep_calls, [1])

    async def test_offline_foreign_reconnect_skipped_detail(self):
        # Owned disable while offline: skipped, never attempted.
        f = self.fixture(driver="rtw88")
        f.active_uuid = None
        f.seed_settings(
            driver="rtw88",
            bssid_lock_enabled=True,
            bssid_lock_value="AA:BB:CC:DD:EE:FF",
            bssid_lock_connection_uuid=UUID_A,
        )
        f.nm_profiles[UUID_A] = {"802-11-wireless.bssid": "AA:BB:CC:DD:EE:FF"}
        result = await f.plugin.set_bssid_lock(False)
        self.assertTrue(result["success"])
        self.assertFalse(result["reconnected"])
        self.assertEqual(
            result.get("reconnect"), {"attempted": False, "skipped": "offline"}
        )
        self.assertEqual(f.radio_calls(), [])

        # Owned disable while a foreign profile is active: skipped.
        g = self.fixture(driver="rtw88")
        g.active_uuid = UUID_B
        g.seed_settings(
            driver="rtw88",
            bssid_lock_enabled=True,
            bssid_lock_value="AA:BB:CC:DD:EE:FF",
            bssid_lock_connection_uuid=UUID_A,
        )
        g.nm_profiles[UUID_A] = {"802-11-wireless.bssid": "AA:BB:CC:DD:EE:FF"}
        result = await g.plugin.set_bssid_lock(False)
        self.assertTrue(result["success"])
        self.assertFalse(result["reconnected"])
        self.assertEqual(
            result.get("reconnect"), {"attempted": False, "skipped": "foreign_owner"}
        )
        self.assertEqual(g.radio_calls(), [])

    async def test_aggregates_compute_reconnected_from_children(self):
        # One failed child fails the aggregate; reconnected comes from the
        # verified child, never from a hardcoded True.
        f = self.fixture()
        f.sysctl_fail_keys = {"net.core.wmem_max"}
        result = await f.plugin.optimize_safe()
        self.assertFalse(result["success"])
        self.assertEqual(result["applied"], 3)
        self.assertEqual(result["total"], 4)
        self.assertFalse(result["results"]["buffer_tuning"]["success"])
        self.assertTrue(result.get("reconnected"))
        self.assertTrue(result.get("did_reconnect"))

        # Only non-reconnecting children: nothing attempted, nothing verified.
        g = self.fixture(auto_fix_on_wake=True, buffer_tuning_enabled=True)
        result = await g.plugin.reapply_all()
        self.assertTrue(result["success"])
        self.assertFalse(result.get("did_reconnect"))
        self.assertFalse(result.get("reconnected", False))

    async def test_sysctl_fail_blocks_intent(self):
        f = self.fixture()
        f.sysctl_fail_keys = {"net.core.wmem_max"}
        writes_before = f.settings_writes()
        result = await f.plugin.set_buffer_tuning(True)
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "sysctl_failed")
        self.assertIn("net.core.wmem_max", result.get("detail", ""))
        # Intent flag unsaved; settings file untouched.
        self.assertFalse(f.get_settings()["buffer_tuning_enabled"])
        self.assertEqual(f.settings_writes(), writes_before)
        # Partial application is fine (idempotent): first key written,
        # failing key untouched.
        self.assertEqual(f.sysctl["net.core.rmem_max"], "16777216")
        self.assertEqual(f.sysctl["net.core.wmem_max"], "70002")
        # Retry converges once the write works.
        f.sysctl_fail_keys = set()
        retry = await f.plugin.set_buffer_tuning(True)
        self.assertTrue(retry["success"])
        self.assertEqual(f.sysctl, f.SYSCTL_PARAMS)

    async def test_txqueuelen_fail_blocks_intent(self):
        f = self.fixture()
        f.txq_fail = True
        writes_before = f.settings_writes()
        result = await f.plugin.set_buffer_tuning(True)
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "txqueuelen_failed")
        self.assertTrue(result.get("detail"))
        self.assertFalse(f.get_settings()["buffer_tuning_enabled"])
        self.assertEqual(f.settings_writes(), writes_before)
        # sysctl writes (which precede txqueuelen) still applied.
        self.assertEqual(f.sysctl, f.SYSCTL_PARAMS)
        f.txq_fail = False
        retry = await f.plugin.set_buffer_tuning(True)
        self.assertTrue(retry["success"])
        self.assertEqual(f.txq.get(IFACE), "2000")

    async def test_cake_del_idempotent_vs_real_fail(self):
        # Real `tc qdisc del` failure blocks the disable with a named error.
        f = self.fixture()
        self.assertTrue((await f.plugin.set_cake(True))["success"])
        f.tc_del_stderr = "RTNETLINK answers: Operation not permitted"
        result = await f.plugin.set_cake(False)
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "qdisc_failed")
        self.assertTrue(f.get_settings()["cake_enabled"])

        # Idempotent del (nothing installed) counts as success.
        g = self.fixture()
        self.assertTrue((await g.plugin.set_cake(True))["success"])
        g.tc_del_stderr = "RTNETLINK answers: No such file or directory"
        result = await g.plugin.set_cake(False)
        self.assertTrue(result["success"])
        self.assertFalse(g.get_settings()["cake_enabled"])

    async def test_dispatcher_fail_blocks_intent(self):
        f = self.fixture(auto_fix_on_wake=False)
        f.fail_paths = {f.DISPATCHER_PATH}
        writes_before = f.settings_writes()
        result = await f.plugin.set_auto_fix(True)
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "dispatcher_failed")
        self.assertTrue(result.get("detail"))
        self.assertFalse(f.get_settings()["auto_fix_on_wake"])
        self.assertEqual(f.settings_writes(), writes_before)
        f.fail_paths = set()
        self.assertTrue((await f.plugin.set_auto_fix(True))["success"])

    async def test_sysfs_pcie_modprobe_fail_blocks_intent(self):
        sysfs_path = "/sys/module/rtw88_core/parameters/disable_lps_deep"
        # sysfs write failure blocks power-save intent.
        f = self.fixture(power_save_disabled=False, driver="rtw88")
        f.fail_paths = {sysfs_path}
        writes_before = f.settings_writes()
        result = await f.plugin.set_power_save(True)
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "sysfs_failed")
        self.assertIn("disable_lps_deep", result.get("detail", ""))
        self.assertFalse(f.get_settings()["power_save_disabled"])
        self.assertEqual(f.settings_writes(), writes_before)

        # modprobe conf write failure reports its own code.
        g = self.fixture(power_save_disabled=False, driver="rtw88")
        g.fail_paths = {g.MODPROBE_CONF_PATH}
        result = await g.plugin.set_power_save(True)
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "modprobe_failed")
        self.assertFalse(g.get_settings()["power_save_disabled"])

        # PCIe ASPM write failure reports its own code.
        h = self.fixture(power_save_disabled=False, driver="rtw88")
        h.fail_paths = {PCIE_DEVICE + "/link/l0s_aspm"}
        h.fs[PCIE_DEVICE + "/link/l0s_aspm"] = "1"
        result = await h.plugin.set_power_save(True)
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "pcie_failed")
        self.assertFalse(h.get_settings()["power_save_disabled"])

    async def test_dns_rollback_on_second_key_fail(self):
        # Second-key failure rolls the first key back to its snapshot prior.
        f = self.fixture()
        f.nm_profiles[UUID_A] = {
            "ipv4.dns": "9.9.9.9",
            "ipv4.ignore-auto-dns": "no",
        }
        f.nmcli_fail_keys = {"ipv4.ignore-auto-dns"}
        result = await f.plugin.set_dns(True, "cloudflare")
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "dns_partial")
        self.assertEqual(f.nm_profiles[UUID_A]["ipv4.dns"], "9.9.9.9")
        self.assertFalse(f.get_settings()["dns_enabled"])
        self.assertEqual(f.radio_calls(), [])

        # Without a snapshot prior, the first key is cleared instead.
        g = self.fixture()
        g.nm_profiles[UUID_A] = {}
        g.nmcli_fail_keys = {"ipv4.ignore-auto-dns"}
        result = await g.plugin.set_dns(True, "cloudflare")
        self.assertFalse(result["success"])
        self.assertEqual(result.get("error"), "dns_partial")
        self.assertEqual(g.nm_profiles[UUID_A]["ipv4.dns"], "")
        self.assertFalse(g.get_settings()["dns_enabled"])

    async def test_band_preclear_checked(self):
        f = self.fixture()
        f.seed_settings(
            bssid_lock_enabled=True,
            bssid_lock_value="AA:BB:CC:DD:EE:FF",
            bssid_lock_connection_uuid=UUID_A,
        )
        # Only the pre-clear (bssid, "") fails; the band write and the
        # later relock write succeed.
        f.nmcli_fail_pairs = {("802-11-wireless.bssid", "")}
        result = await f.plugin.set_band_preference(True, "a")
        self.assertTrue(result["success"])
        errors = result.get("errors") or []
        self.assertTrue(any("preclear" in str(e) for e in errors))
        relock = result.get("relock") or {}
        self.assertTrue(relock.get("attempted"))
        self.assertTrue(relock.get("ok"))
        reconnect = result.get("reconnect") or {}
        self.assertTrue(reconnect.get("attempted"))
        self.assertTrue(reconnect.get("verified"))
        self.assertEqual(f.get_settings()["band_owner_uuid"], UUID_A)

    async def test_buffer_status_checks_all_keys(self):
        # A non-rmem_max key diverging must report drift.
        f = self.fixture(buffer_tuning_enabled=True)
        f.sysctl = dict(f.SYSCTL_PARAMS)
        f.sysctl["net.core.netdev_budget"] = "12345"
        status = await f.plugin.get_status()
        self.assertTrue(status["success"])
        self.assertFalse(status["live"].get("buffer_tuning_applied"))
        self.assertTrue(status["drift"].get("buffer_tuning"))
        # Every SYSCTL_PARAMS key is actually queried.
        queried = {cmd[2] for cmd in f.sysctl_read_calls()}
        self.assertEqual(queried, set(f.SYSCTL_PARAMS))

        # All keys matching reports applied with no drift.
        g = self.fixture(buffer_tuning_enabled=True)
        g.sysctl = dict(g.SYSCTL_PARAMS)
        status = await g.plugin.get_status()
        self.assertTrue(status["live"].get("buffer_tuning_applied"))
        self.assertNotIn("buffer_tuning", status["drift"])

    async def test_reset_errors_nonempty_means_success_false(self):
        # Fallback reset (no snapshot slots): errors non-empty, success False.
        f = self.fixture(
            bssid_lock_enabled=True,
            bssid_lock_connection_uuid=UUID_A,
            band_preference_enabled=True,
            band_owner_uuid=UUID_A,
            dns_enabled=True,
            dns_owner_uuid=UUID_B,
            ipv6_disabled=True,
            ipv6_owner_uuid=UUID_A,
        )
        f.nm_profiles[UUID_A] = {
            "802-11-wireless.bssid": "AA:BB:CC:DD:EE:FF",
            "802-11-wireless.band": "a",
            "ipv6.method": "disabled",
        }
        f.nm_profiles[UUID_B] = {
            "ipv4.dns": "1.1.1.1 1.0.0.1",
            "ipv4.ignore-auto-dns": "yes",
        }
        result = await f.plugin.reset_settings()
        self.assertTrue(result.get("errors"))
        self.assertFalse(result["success"])

        # Clean reset (every slot captured by prior enables): no errors,
        # success stays True.
        g = self.fixture(driver="rtw88")
        self.assertTrue((await g.plugin.set_power_save(True))["success"])
        self.assertTrue((await g.plugin.set_auto_fix(True))["success"])
        self.assertTrue((await g.plugin.set_buffer_tuning(True))["success"])
        self.assertTrue((await g.plugin.set_cake(True))["success"])
        self.assertTrue((await g.plugin.set_bssid_lock(True))["success"])
        self.assertTrue((await g.plugin.set_band_preference(True, "a"))["success"])
        self.assertTrue((await g.plugin.set_dns(True, "cloudflare"))["success"])
        self.assertTrue((await g.plugin.set_ipv6(True))["success"])
        result = await g.plugin.reset_settings()
        self.assertEqual(result.get("errors"), [])
        self.assertTrue(result["success"])

    async def test_reset_reports_unverified_reconnect(self):
        # Owned profile on the active UUID forces the conditional reconnect;
        # dead verification (no wifi back) must land in errors.
        f = self.fixture(bssid_lock_connection_uuid=UUID_A)
        f.nm_profiles[UUID_A] = {"802-11-wireless.bssid": "AA:BB:CC:DD:EE:FF"}
        f.verify_sequence = [None, None]
        result = await f.plugin.reset_settings()
        errors = result.get("errors") or []
        self.assertTrue(
            any(str(e).startswith("reconnect_failed") for e in errors),
            f"expected reconnect_failed in {errors}",
        )

    async def test_reset_silent_on_verified_reconnect(self):
        # Same setup with wifi coming back: verified, no reconnect entry.
        f = self.fixture(bssid_lock_connection_uuid=UUID_A)
        f.nm_profiles[UUID_A] = {"802-11-wireless.bssid": "AA:BB:CC:DD:EE:FF"}
        result = await f.plugin.reset_settings()
        errors = result.get("errors") or []
        self.assertFalse(
            any(str(e).startswith("reconnect_failed") for e in errors),
            f"unexpected reconnect_failed in {errors}",
        )

    def test_clear_owned_profiles_reconnect_reporting(self):
        # Direct helper check (shared by reset_settings and _uninstall).
        f = self.fixture()
        f.verify_sequence = [None, None]
        errors = []
        f.plugin._clear_owned_profiles({"bssid": UUID_A}, UUID_A, errors)
        self.assertTrue(
            any(str(e).startswith("reconnect_failed") for e in errors),
            f"expected reconnect_failed in {errors}",
        )
        g = self.fixture()
        g_errors = []
        g.plugin._clear_owned_profiles({"bssid": UUID_A}, UUID_A, g_errors)
        self.assertFalse(
            any(str(e).startswith("reconnect_failed") for e in g_errors),
            f"unexpected reconnect_failed in {g_errors}",
        )

    def test_priority_never_written_truthful(self):
        hits = []
        for lineno, line in enumerate(
            SOURCE.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.strip().startswith("#"):
                continue
            if PRIORITY_LITERAL in line:
                hits.append(f"{SOURCE.name}:{lineno}:{line.strip()}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
