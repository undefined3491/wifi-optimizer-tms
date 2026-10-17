# clean_env call-site audit (S-BE-06 mechanism fix)

Date: 2026-10-17. Baseline: `5d48ded` (main); audited file `main.py` was
unchanged since baseline except the D1 default flip (`clean_env: bool = False` →
`True` at `main.py:271`) until the 2026-10-18 ASPM skip-note fix. That fix
changed no command call sites; references below reflect their current positions.

Method: AST walk of `main.py` — every `Call` node whose `func` is the
attribute `_run_cmd` (direct `self._run_cmd(...)` sites), plus every
`asyncio.to_thread(self._run_cmd, ...)` wrapper (flag passed positionally
as the 4th overall argument). Line numbers below are post docstring
generalization (docstring edit shifted post-277 lines by +2; verified by
`test_call_site_count_matches_audit`). No `clean_env=False` exists in either form
(keyword or positional, direct or wrapper); that absence is enforced by
`tests/test_clean_env.py::test_no_unjustified_false_opt_outs`.

Result: 61/61 direct sites inherit the safe default; 14/14 wrapper sites
carry an explicit positional `True` (kept deliberately — defense in depth
at the highest-risk sites AND required by
`scripts/check_delivery.py:388-395`, which asserts the literal for the
updater paths).

## Direct call sites (61)

- `main.py:317` → `/usr/bin/nmcli` (`dev status`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:329` → `/usr/bin/nmcli` (`con show --active`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:403` → `/usr/bin/systemctl` (`is-active`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:412` → `/usr/bin/systemctl` (`is-enabled`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:524` → `/usr/bin/sysctl` (`-n`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:533` → `/usr/bin/ip` (`link show`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:556` → `/usr/bin/tc` (`qdisc show`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:567` → `/usr/bin/iw` (`get power_save`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:653` → `/usr/bin/systemctl` (service op) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:670` → `/usr/bin/nmcli` (`con show uuid`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:725` → `/usr/bin/sysctl` (`-w`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:739` → `/usr/bin/ip` (`link set txqueuelen`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:764` → `/usr/bin/tc` (`qdisc del`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:777` → `/usr/bin/tc` (`qdisc replace`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:793` → `/usr/bin/iw` (`set power_save`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:909` → `/usr/bin/nmcli` (`radio wifi off`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:910` → `/usr/bin/nmcli` (`radio wifi on`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:913` → `/usr/bin/nmcli` (`con up uuid`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:940` → `/usr/bin/nmcli` (`con show --active` poll) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1303` → `/usr/bin/sysctl` (`-w` uninstall) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1324` → `/usr/bin/tc` (`qdisc del` uninstall) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1344` → `/usr/bin/ip` (`link set txqueuelen 1000` uninstall) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1536` → `/usr/bin/nmcli` (`con mod`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1665` → `/usr/bin/iw` (`dev`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1666` → `/usr/bin/iw` (`reg get`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1667` → `/usr/bin/uname` (`-r`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1823` → `/usr/bin/iw` (`get power_save`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1830` → `/usr/bin/nmcli` (`802-11-wireless.bssid`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1851` → `/usr/bin/nmcli` (`IP4.DNS`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1857` → `/usr/bin/nmcli` (`ipv6.method`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1875` → `/usr/bin/nmcli` (`802-11-wireless.band`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1895` → `/usr/bin/sysctl` (`-n`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:1997` → `/usr/bin/iw` (`get power_save`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2004` → `/usr/bin/iw` (`dev link`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2024` → `/usr/bin/iw` (`dev info`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2050` → `/usr/bin/nmcli` (`802-11-wireless.bssid`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2073` → `/usr/bin/nmcli` (`IP4.ADDRESS`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2084` → `/usr/bin/nmcli` (`IP4.DNS`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2091` → `/usr/bin/nmcli` (`ipv6.method`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2109` → `/usr/bin/nmcli` (`802-11-wireless.band`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2131` → `/usr/bin/sysctl` (`-n`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2209` → `/usr/bin/iw` (`set power_save`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2291` → `/usr/bin/iw` (`set power_save on`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2479` → `/usr/bin/iw` (`dev link`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:2715` → `/usr/bin/iw` (`dev link`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3186` → `/usr/bin/sysctl` (`-w`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3222` → `/usr/bin/sysctl` (`-w`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3247` → `/usr/bin/ip` (`link set txqueuelen`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3260` → `/usr/bin/ip` (`link set txqueuelen 256`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3283` → `/usr/bin/ip` (`link set txqueuelen 1000`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3316` → `/usr/bin/tc` (`qdisc show`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3352` → `/usr/bin/modprobe` (fallback `/usr/sbin/modprobe`, `sch_cake`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3353` → `/usr/bin/tc` (`qdisc replace ... cake`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3366` → `/usr/bin/ip` (`link set txqueuelen 256`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3389` → `/usr/bin/tc` (`qdisc del`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3413` → `/usr/bin/ip` (`link set txqueuelen 2000`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3435` → `/usr/bin/ip` (`link set txqueuelen 1000`) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3762` → `/usr/bin/sysctl` (`-w` reset) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3784` → `/usr/bin/tc` (`qdisc del` reset) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3804` → `/usr/bin/ip` (`link set txqueuelen 1000` reset) → inherit safe default (D1); absolute-path system binary, no bundled-env need.
- `main.py:3867` → `/usr/bin/iw` (`set power_save off` reset) → inherit safe default (D1); absolute-path system binary, no bundled-env need.

## `to_thread` wrapper sites (14, all explicit positional `True` — kept)

| Site | Command | Disposition |
| ---- | ------- | ----------- |
| main.py:3968 (`check_for_update`) | `/usr/bin/curl` release check | KEEP explicit `True` (D2 addendum; `check_delivery.py:388-395` requires the literal). |
| main.py:4228 (`_update_worker`) | `curl_cmd` archive download | KEEP explicit `True` (D2 addendum; `check_delivery.py:388-395` requires the literal). |
| main.py:4351 (`_update_worker`) | `/usr/bin/systemctl restart plugin_loader` | KEEP explicit `True`; same poisoning class as S-BE-06 (restart path, latent). |
| main.py:4435 (`_backend_switch_worker`) | `BACKEND_HELPER write_config target` | KEEP explicit `True`; helper is a system-side executable. |
| main.py:4463 (`_backend_switch_worker`) | `BACKEND_HELPER write_config rb_prev` | KEEP explicit `True`; helper is a system-side executable. |
| main.py:4469 (`_backend_switch_worker`) | `BACKEND_HELPER restart_units target` | KEEP explicit `True`; helper is a system-side executable. |
| main.py:4485 (`_backend_switch_worker`) | `rb_cmd` loop (`systemctl enable/start rb_prev`, `restart NetworkManager`) | KEEP explicit `True`; absolute-path system binaries. |
| main.py:4532 (`_backend_switch_worker`) | `BACKEND_HELPER restart_units other` | KEEP explicit `True`; helper is a system-side executable. |
| main.py:4627 (`_backend_switch_worker` repair) | `BACKEND_HELPER write_config rb_prev` | KEEP explicit `True`; helper is a system-side executable. |
| main.py:4633 (`_backend_switch_worker` repair) | `BACKEND_HELPER restart_units target` | KEEP explicit `True`; helper is a system-side executable. |
| main.py:4649 (`_backend_switch_worker` repair) | `rb_cmd` loop (`systemctl enable/start rb_prev`, `restart NetworkManager`) | KEEP explicit `True`; absolute-path system binaries. |
| main.py:4786 (`_generic_backend_switch_worker`) | `cmd` loop (`systemctl stop/disable other`, `enable/start target`) | KEEP explicit `True`; absolute-path system binaries. |
| main.py:4796 (`_generic_backend_switch_worker`) | `/usr/bin/systemctl restart NetworkManager` | KEEP explicit `True`; absolute-path system binary. |
| main.py:4902 (`_generic_backend_switch_worker` rollback) | `step_cmd` loop (`systemctl enable/start prev`, `restart NetworkManager`) | KEEP explicit `True`; absolute-path system binaries. |

## Tally

Direct: nmcli 17, iw 13, ip 10, sysctl 8, tc 8, systemctl 3, uname 1,
modprobe 1 = 61/61 inherit the safe default. Wrappers: curl 2, systemctl
(incl. loop vars) 6, BACKEND_HELPER 6 = 14/14 explicit `True` kept.
`clean_env=False`: 0 in both keyword and positional forms.
