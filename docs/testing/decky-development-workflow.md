# Decky development and debugging workflow

Checked: 2026-10-16 against official Decky documentation, template and Loader `v3.2.10` source (the owner's reported version). This records documented platform practice and project recommendations, not a formal industry standard or device-test result.

| Tool | Documented purpose | Use in this project |
| --- | --- | --- |
| CEF debugging | Inspect Steam's HTML/CSS/JavaScript and console | Frontend errors, layout, event/IPC behavior and lifecycle investigation |
| React DevTools | Inspect components, props/state and rendering performance | Focused React investigation when CEF alone is insufficient |
| Konsole / optional SSH | Execute Linux commands; SSH also supports remote file/log access | System/backend inventory and controlled device tests; SSH is a convenience, not a prerequisite |
| Decky ZIP / URL installer | Install packaged plugins through Decky | Standard distribution/test-install option to evaluate against the plugin's lifecycle/restoration defects |

## CEF on Decky 3.2.10

The official guide's remote setup uses **Allow Remote CEF Debugging**, a Chromium browser's `chrome://inspect` or `edge://inspect`, and network target `<DECK_IP>:8081`. Useful targets include `SharedJSContext` for JavaScript/console output and the QAM/Steam UI windows for layout. Local inspection on the Deck uses port `8080`. The setting calls Decky's port-forwarding service; it is separate from SSH.

The guide explicitly limits remote debugging to a trusted network and says to turn it off when finished. Do not expose it publicly or leave it enabled as an ordinary player requirement. Enabling a debug transport does not make plugin calls observational: this plugin's `get_status()` still mutates state (ST0-006).

## React DevTools on Decky 3.2.10

Use a standalone React DevTools instance on the development PC and its IP in Decky's React DevTools setting. The Loader fetches the tool script and proxies communication using port `8097`; a browser extension alone should not be assumed to attach to remote Steam CEF. The Loader reloads the relevant Steam UI context to inject the React hook before React initializes. Enable it for a deliberate debugging session, not during the initial configuration snapshot or a performance comparison.

## Backend, deployment and current inventory

The official plugin template contains SSH/rsync deployment tasks. They establish a supported development workflow; they do not require every tester to enable SSH or authorize copying their password-oriented snippets unchanged. Our existing VS Code deployment tasks likewise use SSH. Local builds/CI and device runtime checks remain separate. For a pinned Stage 0 candidate, the optional [read-only SSH evidence collector](collect-candidate-evidence.md) compares deployed file hashes and captures bounded service/journal observations; it supplements rather than replaces the inventory or owner-operated UI checks.

For the current Stage 0 inventory, use the [standalone collector in Konsole](collect-inventory.md); no CEF, React DevTools or SSH setup is needed. It captures selected Linux state that frontend inspection alone does not establish. Later remote backend work can use the same collector/commands over SSH, with deliberate command scope and privileged execution only where required. Python/plugin logs and system/service observations complement CEF console output.

For a future reviewed test candidate, distinguish rapid development deployment from testing an actual packaged install/update. Decky provides local ZIP and URL installation in Developer settings. In Loader 3.2.10, replacement can call `uninstall_plugin()` and the backend `_uninstall()` before extracting the new package. Our current uninstall/restoration findings therefore still apply; using the native installer does not prove a safe update or preserve all prior system state. Renaming a plugin can also change which existing installation Decky matches. Do not use ZIP reinstallation as a shortcut to obtain a pristine baseline.

The [recovery proposal](../superpowers/specs/2026-10-15-delivery-recovery-design.md) is specialized project work, not the default Decky development method. Before implementing that engine, compare retaining the inherited self-updater with relying on Decky's package lifecycle after correcting uninstall/ownership. Removing the current installer/updater or changing the agreed release channels needs an explicit design decision. This research changes no delivery policy, runtime, package identity, remote settings or device configuration.

## Primary sources

- [Official CEF guide source](https://github.com/SteamDeckHomebrew/wiki/blob/main/plugin-dev/cef-debugging.adoc).
- [React DevTools documentation](https://react.dev/learn/react-developer-tools).
- [Loader 3.2.10 Developer settings](https://github.com/SteamDeckHomebrew/decky-loader/blob/v3.2.10/frontend/src/components/settings/pages/developer/index.tsx) and [remote CEF toggle](https://github.com/SteamDeckHomebrew/decky-loader/blob/v3.2.10/frontend/src/components/settings/pages/general/RemoteDebugging.tsx).
- [Loader 3.2.10 debug-service and React DevTools implementation](https://github.com/SteamDeckHomebrew/decky-loader/blob/v3.2.10/backend/decky_loader/utilities.py#L265-L278), React proxy/injection starts at line 369.
- [Official template deployment tasks](https://github.com/SteamDeckHomebrew/decky-plugin-template/blob/main/.vscode/tasks.json#L76-L146).
- [Loader 3.2.10 package replacement and uninstall](https://github.com/SteamDeckHomebrew/decky-loader/blob/v3.2.10/backend/decky_loader/browser.py#L237-L249), including the uninstall callback at lines 125-149.
