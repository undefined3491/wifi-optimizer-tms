#!/usr/bin/env python3
"""Read-only SSH evidence for the pinned Stage 0 candidate; no remote writes."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

CANDIDATE = "9a2cb963dd5289d88897832805206543098227c4"
PLUGIN_DIR = "/home/deck/homebrew/plugins/WiFi Optimizer"
FILES = (
    "plugin.json", "package.json", "main.py", "decky.pyi",
    "dist/index.js", "dist/index.js.map", "defaults/dispatcher.sh.tmpl",
)
UNITS = (
    "plugin_loader.service", "NetworkManager.service",
    "wpa_supplicant.service", "iwd.service",
)
BINARIES = {
    "systemctl": ("/usr/bin/systemctl", "--version"),
    "nmcli": ("/usr/bin/nmcli", "--version"),
    "iw": ("/usr/bin/iw", "--version"),
}
JOURNAL = (
    "/usr/bin/journalctl", "--no-pager", "--output=cat",
    "--unit=plugin_loader.service", "--since=-30min", "--lines=500",
)
SAFE_HOST = re.compile(r"^deck@[A-Za-z0-9][A-Za-z0-9.-]*$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9 ._()+,:/-]{1,160}$")
ACTIVE_STATES = {
    "active", "inactive", "failed", "activating", "deactivating", "maintenance",
}


def ssh_error(stderr: str) -> str:
    text = stderr.lower()
    for state, marker in (
        ("host_key_verification_failed", "host key verification failed"),
        ("authentication_failed", "permission denied"),
        ("timed_out", "timed out"),
        ("host_unresolved", "could not resolve hostname"),
        ("identity_unavailable", "identity file"),
    ):
        if marker in text:
            return state
    return "ssh_or_remote_command_failed"


def ssh_query(config: dict, remote_argv: tuple[str, ...] | list[str]) -> dict:
    command = " ".join(shlex.quote(part) for part in remote_argv)
    argv = [
        config["ssh"], "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={config['timeout']}",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ProxyCommand=none", "-o", "ProxyJump=none",
        "-o", "PermitLocalCommand=no",
        "-o", f"UserKnownHostsFile={config['known_hosts']}",
        "-i", config["identity"], config["target"], command,
    ]
    try:
        proc = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=config["timeout"] + 5,
            check=False, shell=False,
        )
    except FileNotFoundError:
        return {"state": "ssh_client_missing"}
    except subprocess.TimeoutExpired:
        return {"state": "timed_out"}
    except OSError:
        return {"state": "local_process_error"}
    output = proc.stdout
    if len(output.encode("utf-8")) > 262144:
        return {"state": "output_limit"}
    return {
        "state": "ok" if proc.returncode == 0 else ssh_error(proc.stderr),
        "exit_code": proc.returncode,
        "stdout": output,
        "stderr": proc.stderr,
    }


def candidate_hash(repo: Path, relative: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"{CANDIDATE}:{relative}"], cwd=repo,
        stdin=subprocess.DEVNULL, capture_output=True, shell=False, check=False,
    )
    if proc.returncode:
        raise RuntimeError("candidate object unavailable")
    return hashlib.sha256(proc.stdout).hexdigest()


def collect_files(config: dict, expected: dict[str, str]) -> dict:
    paths = [f"{PLUGIN_DIR}/{name}" for name in FILES]
    query = ssh_query(config, ["/usr/bin/sha256sum", "--", *paths])
    found = {}
    for line in query.get("stdout", "").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            continue
        path = parts[1].lstrip("*")
        prefix = PLUGIN_DIR + "/"
        if path.startswith(prefix):
            found[path[len(prefix):]] = parts[0].lower()
    rows = []
    for name in FILES:
        actual, wanted = found.get(name), expected.get(name)
        state = "match" if actual and actual == wanted else (
            "mismatch" if actual else "unreadable"
        )
        rows.append({
            "path": name, "expected_sha256": wanted,
            "device_sha256": actual, "state": state,
        })
    return {"query_state": query["state"], "files": rows}


def collect_services(config: dict) -> dict:
    result = {}
    for unit in UNITS:
        query = ssh_query(config, [
            "/usr/bin/systemctl", "--no-pager", "show",
            "--property=ActiveState", "--value", unit,
        ])
        value = query.get("stdout", "").strip()
        result[unit] = value if query["state"] == "ok" and value in ACTIVE_STATES else {
            "state": query["state"], "exit_code": query.get("exit_code"),
        }
    return result


def collect_binaries(config: dict) -> dict:
    result = {}
    for name, command in BINARIES.items():
        query = ssh_query(config, command)
        lines = [line.strip() for line in query.get("stdout", "").splitlines() if line.strip()]
        first = lines[0] if lines else ""
        result[name] = (
            {"state": "ok", "version": first[:160]}
            if query["state"] == "ok" and SAFE_VERSION.fullmatch(first)
            else {"state": query["state"]}
        )
    return result


def collect_openssl(config: dict) -> dict:
    query = ssh_query(config, JOURNAL)
    result = {"window": "last 30 minutes, up to 500 plugin_loader entries"}
    if query["state"] != "ok":
        result["state"] = query["state"]
        return result
    output = query.get("stdout", "")
    diagnostic = (output + query.get("stderr", "")).lower()
    if any(marker in diagnostic for marker in (
        "insufficient permissions", "no journal files were opened",
        "not seeing messages from other users", "permission denied",
    )):
        result["state"] = "permission_limited"
        return result
    entries = [
        line for line in output.splitlines()
        if line.strip() and not line.strip().startswith("-- No entries --")
    ]
    if not entries:
        result.update({
            "state": "searched_empty_window",
            "accessible_entry_count": 0,
            "matching_line_count": 0,
            "raw_lines_saved": False,
        })
        return result
    categories, count = set(), 0
    for line in entries:
        lowered = line.lower()
        matched = []
        if "libcrypto" in lowered:
            matched.append("libcrypto")
        if re.search(r"openssl_[0-9]", lowered):
            matched.append("openssl_symbol_version")
        if matched:
            count += 1
            categories.update(matched)
    result.update({
        "state": "searched",
        "accessible_entry_count": len(entries),
        "matching_line_count": count,
        "match_categories": sorted(categories),
        "raw_lines_saved": False,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", required=True, help="SSH identity selector/file")
    parser.add_argument("--known-hosts", required=True, help="verified known_hosts file")
    parser.add_argument("--target", default="deck@steamdeck")
    parser.add_argument("--ssh", default="ssh")
    parser.add_argument("--timeout", type=int, default=30, choices=range(5, 61))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not SAFE_HOST.fullmatch(args.target):
        parser.error("--target must be deck@ followed by a DNS name or IPv4 address")
    identity, known_hosts = Path(args.identity), Path(args.known_hosts)
    if not identity.is_file():
        parser.error("--identity file does not exist")
    if not known_hosts.is_file():
        parser.error("--known-hosts file does not exist")

    repo = Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            stdin=subprocess.DEVNULL, capture_output=True,
            text=True, shell=False, check=False,
        )
        if head.returncode:
            raise RuntimeError("collector source commit unavailable")
        collector_commit = head.stdout.strip()
        collector_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        rev = subprocess.run(
            ["git", "rev-parse", "--verify", f"{CANDIDATE}^{{commit}}"],
            cwd=repo, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, shell=False, check=False,
        )
        if rev.returncode or rev.stdout.strip() != CANDIDATE:
            raise RuntimeError("pinned candidate commit unavailable")
        expected = {name: candidate_hash(repo, name) for name in FILES}
    except (OSError, RuntimeError):
        print("Pinned candidate objects are unavailable in this checkout.", file=sys.stderr)
        return 2

    config = {
        "ssh": args.ssh, "timeout": args.timeout,
        "identity": str(identity.resolve()),
        "known_hosts": str(known_hosts.resolve()), "target": args.target,
    }
    files = collect_files(config, expected)
    services = collect_services(config)
    binaries = collect_binaries(config)
    openssl = collect_openssl(config)
    report = {
        "schema_version": 1,
        "collected_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate_commit": CANDIDATE,
        "collector": {
            "git_commit": collector_commit,
            "script_sha256": collector_sha256,
        },
        "device": {
            "plugin_dir_alias": "homebrew/plugins/WiFi Optimizer",
            "ssh_target_recorded": False,
            "sudo_used": False,
            "remote_writes_performed": False,
        },
        "candidate_files": files,
        "services": services,
        "system_binaries": binaries,
        "plugin_loader_openssl_scan": openssl,
        "manual_ui_observations_still_required": [
            "S-BE-04 persistent mismatch (do not force an unsafe mismatch)",
            "S-BE-05 banner self-clear after a converged backend switch",
            "S-OPT-02 UI result and ASPM skip-note after owner-approved apply",
            "S-OPT-01, S-OPT-03, and S-RST-01 smoke results",
        ],
        "limitations": [
            "Host observations do not prove which code is resident in plugin_loader.",
            "An inaccessible journal window does not mean there were no OpenSSL errors.",
            "This supplemental report does not update or close matrix rows.",
            "Queries run sequentially; this is not an atomic snapshot.",
            "Inspect software hashes and service state before sharing the report.",
        ],
    }
    output = args.output or (
        repo / "out" / "device" / "candidate-9a2cb96" /
        f"readonly-evidence-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S%fZ}.json"
    )
    output = output.expanduser().resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        output.chmod(0o600)
    except OSError:
        print("Could not save the local evidence report.", file=sys.stderr)
        return 2

    matches = all(row["state"] == "match" for row in files["files"])
    print(f"Saved local read-only report: {output}")
    print(f"Candidate files: {'all match' if matches else 'review required'}")
    print(f"plugin_loader: {services.get('plugin_loader.service')}")
    print(f"OpenSSL journal scan: {openssl.get('state')}, matches={openssl.get('matching_line_count', 'unknown')}")
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
