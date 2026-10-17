# Read-only candidate evidence over SSH

This optional Windows-side collector reduces repeated manual SSH commands for
a pinned device candidate. For current Stage 0 revalidation it is pinned to
commit 9a2cb963dd5289d88897832805206543098227c4.

It runs an allowlist of read-only queries as the deck user. It never uses
sudo, writes to the Deck, calls plugin IPC, changes WiFi state, restarts a
service, or stores raw journal lines. Host key checking is strict and
IdentitiesOnly=yes is always enabled. Output is a local JSON report in ignored
out/device/candidate-9a2cb96/; it is not added to the validation matrix.

## Run from PowerShell

Use the verified SSH identity selector and known-hosts file:

    python .\scripts\collect_candidate_readonly_evidence.py --identity "$env:TEMP\steamdeck-agent-7.pub" --known-hosts "$env:TEMP\steamdeck-knownhosts-verified"

The default target is deck@steamdeck. If the host alias is unavailable, pass a
reachable Deck hostname or IPv4 address with --target deck@<host>. The identity
and known-hosts files are used only by local OpenSSH; their contents and paths
are not saved. The script does not prompt for or read a password.

Use --output <path> to choose another local destination. Inspect a report
before sharing it.

## Captured fields and limits

- SHA-256 for the seven files copied by candidate deploy; each is compared
  with the Git object from the pinned candidate.
- Collector Git commit and SHA-256, so later reports identify the exact tool.
- Read-only active states for plugin_loader, NetworkManager, wpa_supplicant
  and iwd.
- Version lines from systemctl, nmcli and iw.
- Count and category of libcrypto / OPENSSL_<version> matches in up to 500
  accessible plugin_loader journal entries from the last 30 minutes. Matching
  log text is inspected in memory and is not saved.
- An explicit list of remaining UI observations that still need the owner.

Matching file hashes support deploy identity. They do not prove which code is
resident in the loader process. An inaccessible or permission-limited journal
query is reported as unavailable, not as zero errors. An empty accessible
window means no matching lines were found within that window. The collector
uses sequential SSH queries, so its report is not an atomic snapshot. This
supplemental report does not close a Stage 0 row.

This tool captures machine-readable host evidence; screenshots or owner
observations remain the source for UI messages, banners, toggle results,
reconnect behavior and Reset behavior. Follow the device recovery runbook for
any device mutation.
