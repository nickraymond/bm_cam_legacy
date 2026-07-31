#!/usr/bin/env python3
# filename: sofar_send_command.py
# description: Sprint10 §7 — send a BM camera command via the Sofar Command API.
"""
Sprint10 — Mac-side sender: BM camera command -> Sofar cloud -> Spotter.

Wraps a v1 camera command (SPEC "Command message contract") in the
Spotter console line our Phase B bench proved end-to-end:

    bm pub <topic> {"id":N,"c":"roi","v":2} 1 1

and POSTs it to the Sofar Command API
(docs/sofar_command_api_reference.md):

    POST https://api.sofarocean.com/user-rest/devices/<spotterId>/command
    body {"telemetry": "cellular", "message": "<console line>"}

The cloud queues the command in the Spotter's cellular mailbox; the
Spotter executes it on its next successful cellular transmit, which
publishes onto the BM bus -> mote -> Pi UART (same inbound path as the
Phase B console injection).

Inputs
  --spotter-id SPOT-XXXXX     target Spotter (required)
  --id N --cmd roi --value 2  camera command (validated vs command_tables)
  --raw-message '...'         escape hatch: send an arbitrary console line
                              (LOUD warning; bypasses table validation)
  --clear-queue               flush the cellular mailbox before enqueuing
                              (add alone to only flush; see Sofar doc —
                              cannot selectively remove)
  --topic bmcam/cmd           BM topic (default matches bm_commands.topic)
  --dry-run                   print request, send nothing
  --force                     bypass the 60 s client-side rate-limit guard
  env SOFAR_API_TOKEN_BM_REEF API token (never on CLI, never printed)

Outputs
  - stdout: the exact console line, byte count, HTTP status + response
  - append-only send log runs/sofar_command_sends.jsonl (no token) —
    also drives the client-side rate-limit guard (Sofar: 1 successful
    request/min/Spotter; during cooldown ALL requests are rejected)
  - exit 0 only on HTTP 202 (or --dry-run)

Assumptions / limitations
  - telemetry is hard-locked to "cellular" for v1 (Nick 2026-07-27:
    satellite disabled on the account; cellular costs no credits and its
    mailbox has no expiry/limit).
  - Auth via ?token= query param — the proven api/sensor-data convention
    (Sprint09); the Sofar doc's cURL example was not captured. First
    real send verifies this.
  - 202 means ENQUEUED in the cloud mailbox, not delivered: delivery
    happens on the Spotter's next successful cellular transmit, and the
    device ack then takes 13-30 min to appear at api/sensor-data.

Example
  export SOFAR_API_TOKEN_BM_REEF=...   # Nick's shell; do not echo
  python3 tools/sofar_send_command.py --spotter-id SPOT-33507C \
      --id 700 --cmd ping --dry-run
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_tables as ct  # noqa: E402

API_BASE = "https://api.sofarocean.com"
TOKEN_ENV = "SOFAR_API_TOKEN_BM_REEF"
TELEMETRY = "cellular"  # v1 hard lock — see module docstring
MAX_MESSAGE_BYTES = 270  # Sofar limit, final newline included
RATE_LIMIT_S = 60  # Sofar: 1 successful request/min/Spotter
DEFAULT_TOPIC = "bmcam/cmd"  # must match bm_commands.topic on the Pi
SEND_LOG = os.path.join(REPO_ROOT, "runs", "sofar_command_sends.jsonl")

# bm pub <topic> <data> <type> <version> — type/version proven in Phase B
BM_PUB_TYPE = 1
BM_PUB_VERSION = 1


def build_command_json(cmd_id, cmd, value):
    """Compact command JSON, byte-identical to the Phase B bench payloads.

    Raises ValueError on anything the daemon would reject — fail here,
    not after spending a cloud send.
    """
    if not isinstance(cmd_id, int) or isinstance(cmd_id, bool):
        raise ValueError(f"command id must be an int, got {cmd_id!r}")
    if not 0 <= cmd_id <= 0xFFFFFFFF:
        raise ValueError(f"command id out of uint32 range: {cmd_id}")
    if not ct.is_command(cmd):
        raise ValueError(f"unknown command {cmd!r}; valid: {ct.COMMANDS}")
    if cmd == "ping":
        obj = {"id": cmd_id, "c": "ping"}
    else:
        if value is None:
            raise ValueError(f"{cmd!r} requires --value")
        if not ct.valid_value(cmd, value):
            raise ValueError(
                f"invalid value {value!r} for {cmd!r}; "
                f"valid indices: {sorted(ct.table_for(cmd))}"
            )
        obj = {"id": cmd_id, "c": cmd, "v": value}
    return json.dumps(obj, separators=(",", ":"))


def build_console_line(payload_json, topic=DEFAULT_TOPIC):
    """The Spotter console line the cloud mailbox will execute."""
    if " " in topic or "\t" in topic or "\n" in topic:
        raise ValueError(f"topic must have no whitespace: {topic!r}")
    return f"bm pub {topic} {payload_json} {BM_PUB_TYPE} {BM_PUB_VERSION}"


def validate_message(message):
    """Enforce Sofar message-format rules before spending a request.

    Returns the byte length WITH the final newline the server adds/counts.
    """
    if "\t" in message:
        raise ValueError("tabs are not allowed in command messages")
    for ch in message:
        if ch != "\n" and not (0x20 <= ord(ch) <= 0x7E):
            raise ValueError(f"non-printable/non-ascii char {ch!r} in message")
    n = len(message.encode("ascii"))
    if not message.endswith("\n"):
        n += 1  # server appends one before enforcing the limit
    if n > MAX_MESSAGE_BYTES:
        raise ValueError(f"message is {n} bytes; Sofar limit {MAX_MESSAGE_BYTES}")
    return n


def load_last_success_ts(log_path, spotter_id):
    """Newest successful-send timestamp for this Spotter, or None."""
    try:
        with open(log_path, "r", encoding="ascii") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None
    last = None
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # torn tail line; guard stays best-effort
        if rec.get("spotter_id") == spotter_id and rec.get("http_status") == 202:
            last = rec.get("ts", last)
    return last


def append_send_log(log_path, record):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="ascii") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _ssl_context():
    """Default trust store, with certifi as fallback when the interpreter
    has no CA bundle (stock python.org macOS installs)."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            print("[WARN] this Python has no CA certificates and certifi is "
                  "not installed — TLS will fail. Fix: run 'Install "
                  "Certificates.command' for your Python, or pip install "
                  "certifi.")
    return ctx


def post_command(spotter_id, token, body, timeout_s=30):
    """POST to the Command API. Returns (http_status, parsed_or_raw_body).
    Network-level failure returns (None, <error string>)."""
    url = (
        f"{API_BASE}/user-rest/devices/{urllib.parse.quote(spotter_id)}/command"
        + "?" + urllib.parse.urlencode({"token": token})
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("ascii"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s,
                                    context=_ssl_context()) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return None, f"network error (nothing enqueued): {e}"
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spotter-id", required=True)
    ap.add_argument("--id", type=int, dest="cmd_id",
                    help="unique command id (daemon dedupes by this)")
    ap.add_argument("--cmd", choices=ct.COMMANDS)
    ap.add_argument("--value", type=int, default=None)
    ap.add_argument("--raw-message", default=None,
                    help="send this console line verbatim (bypasses tables)")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--clear-queue", action="store_true",
                    help="clear the cellular mailbox before enqueuing")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="bypass the client-side 60 s rate-limit guard")
    ap.add_argument("--send-log", default=SEND_LOG, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    # -- build the message ------------------------------------------------
    if args.raw_message is not None and args.cmd is not None:
        ap.error("--raw-message and --cmd are mutually exclusive")
    message = None
    if args.raw_message is not None:
        print("[WARN] --raw-message bypasses command_tables validation; "
              "the Spotter will execute this line verbatim.")
        message = args.raw_message
    elif args.cmd is not None:
        if args.cmd_id is None:
            ap.error("--cmd requires --id")
        payload = build_command_json(args.cmd_id, args.cmd, args.value)
        message = build_console_line(payload, args.topic)
    elif not args.clear_queue:
        ap.error("need --cmd, --raw-message, or --clear-queue")

    body = {"telemetry": TELEMETRY}
    if message is not None:
        nbytes = validate_message(message)
        body["message"] = message
        print(f"message : {message!r}")
        print(f"bytes   : {nbytes} of {MAX_MESSAGE_BYTES} (incl. final newline)")
    if args.clear_queue:
        body["clear_command_queue"] = True
        print("[WARN] clear_command_queue=True: ALL pending cellular-mailbox "
              "commands for this Spotter are erased first.")
    print(f"spotter : {args.spotter_id}")
    print(f"POST    : {API_BASE}/user-rest/devices/{args.spotter_id}/command "
          f"(telemetry={TELEMETRY}, token=<{TOKEN_ENV}>)")

    if args.dry_run:
        print("[dry-run] nothing sent.")
        return 0

    # -- client-side rate-limit guard ------------------------------------
    last = load_last_success_ts(args.send_log, args.spotter_id)
    now = time.time()
    if last is not None and now - last < RATE_LIMIT_S and not args.force:
        wait = int(RATE_LIMIT_S - (now - last)) + 1
        print(f"[RATE-LIMIT] last successful send to {args.spotter_id} was "
              f"{int(now - last)} s ago; Sofar rejects ALL requests for 60 s "
              f"after a success. Wait ~{wait} s or use --force.")
        return 3

    token = os.environ.get(TOKEN_ENV)
    if not token:
        print(f"[ERROR] set {TOKEN_ENV} in the environment (never on the CLI).")
        return 2

    status, resp = post_command(args.spotter_id, token, body)
    print(f"HTTP    : {status}")
    print(f"response: {json.dumps(resp) if isinstance(resp, dict) else resp}")

    append_send_log(args.send_log, {
        "ts": now,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "spotter_id": args.spotter_id,
        "telemetry": TELEMETRY,
        "message": message,
        "clear_command_queue": bool(args.clear_queue),
        "http_status": status,
        "response": resp,
    })

    if status == 202:
        print("[OK] enqueued in the cloud mailbox — delivery happens on the "
              "Spotter's next successful cellular transmit.")
        return 0
    print("[FAIL] command NOT enqueued (see response above).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
