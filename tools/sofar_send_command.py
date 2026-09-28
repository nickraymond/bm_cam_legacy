#!/usr/bin/env python3
# filename: sofar_send_command.py
# description: Sprint10 §7 / Sprint26 S4 c.3 — send a BM camera command (commands v9) via the Sofar Command API.
"""
Mac-side sender: BM camera command (v9) -> Sofar cloud -> Spotter.

Wraps a commands-v9 JSON (DESIGN_supervisor.md §6.1–6.2) in the Spotter
console line our Phase B bench proved end-to-end:

    bm pub <topic> {"id":1000101,"c":"set","kv":{"power.halt.enabled":false}} 1 1

and POSTs it to the Sofar Command API
(docs/sofar_command_api_reference.md):

    POST https://api.sofarocean.com/user-rest/devices/<spotterId>/command
    body {"telemetry": "cellular", "message": "<console line>"}

The cloud queues the command in the Spotter's cellular mailbox; the
Spotter executes it on its next successful cellular transmit, which
publishes onto the BM bus -> mote -> Pi UART.

Every command is validated BEFORE a cloud send is spent: the unit's own
strict decoder (BM_Devel_Pi/command_wire.decode: strict JSON, verb shape,
charset, lists <= 4, JSON <= 248 B incl. sig), the id range, the 270 B
console line, and (for set / trg kv) the registry: known key or short
name, not a locked key, value valid for the key (config_registry).

Inputs
  --spotter-id SPOT-XXXXX     target Spotter (required)
  --id N                      command id (required for a command). Must be in
                              the remote range 1 000 000 - 99 999 999 (cellular
                              ack, high-water: use a NEW, higher id each time)
                              or the service range 100 000 000 - 199 999 999
                              (needs a sig: tools/bm_service_sign.py). Other
                              ranges are refused unless --allow-any-range.
  one command (exactly one of):
    --json '<object>'         any v9 command (rsd, wap, get with "to", "b", a
                              signed service command, ...); "id" is added from
                              --id when absent and must equal --id when present
    --set key=value           repeatable; value is JSON-typed (false, 8, 1.5,
                              [1504,846,1600,900], "text"); a bare word that is
                              not JSON is a string (10:00, America/New_York)
    --get key|group           repeatable (<= 4); `journal` = the unit's journal
    --reset key|group         repeatable (<= 4)   | --reset-all
    --cfm ID                  confirm a staged/guarded change of command ID
    --trg V [--kv key=value]  0 cancel, 1 capture+save, 2 capture + output per
                              mode, 3/4 reference image; --kv = one-shot values
    --hld MIN                 stay awake MIN minutes (0 releases)
    --ping | --help-cmd       liveness ack | the unit prints help on the console
  --raw-message '...'         escape hatch: send an arbitrary console line
                              (LOUD warning; bypasses every check but Sofar's)
  --clear-queue               flush the cellular mailbox before enqueuing
                              (add alone to only flush; see Sofar doc —
                              cannot selectively remove)
  --topic bmcam/cmd           BM topic (default matches commands.topic)
  --dry-run                   print the request, send nothing (no token needed)
  --force                     bypass the 60 s client-side rate-limit guard
  --token-env NAME            env var holding the API token (default
                              SOFAR_API_TOKEN_BM_REEF). SPOT-33361C (bmcam001/
                              002) is on SOFAR_API_TOKEN_AOML. The backend heal
                              endpoint fills this in (Sprint25 S2b).
  env <token-env>             API token (never on CLI, never printed)

Outputs
  - stdout: the exact console line, JSON + line byte counts, HTTP status +
    response
  - append-only send log runs/sofar_command_sends.jsonl (no token) —
    also drives the client-side rate-limit guard (Sofar: 1 successful
    request/min/Spotter; during cooldown ALL requests are rejected)
  - exit 0 only on HTTP 202 (or --dry-run); 2 = refused before sending

Assumptions / limitations
  - telemetry is hard-locked to "cellular" (Nick 2026-07-27: satellite
    disabled on the account; cellular costs no credits and its mailbox has
    no expiry/limit).
  - Auth via ?token= query param — the proven api/sensor-data convention.
  - 202 means ENQUEUED in the cloud mailbox, not delivered: delivery
    happens on the Spotter's next successful cellular transmit, and the
    device ack ({"id","ok","h"[,"e","k","s","d","v"]}) then takes 13-30 min
    to appear at api/sensor-data (tools/sofar_poll_acks.py).
  - The whole-config rules (cross-key, environment) run on the unit only;
    a locally valid set can still come back ok:0 e:"xk".
  - Only a unit on the supervisor runtime with a migrated (v2) state file
    speaks v9; a legacy unit answers these with its v8 rejection.

Example
  export SOFAR_API_TOKEN_BM_REEF=...   # Nick's shell; do not echo
  python3 tools/sofar_send_command.py --spotter-id SPOT-33507C \
      --id 1000101 --set power.halt.enabled=false --dry-run
  python3 tools/sofar_send_command.py --spotter-id SPOT-33507C \
      --id 1000102 --get mode --get schedule.window
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

import command_wire as W  # noqa: E402
import config_registry as R  # noqa: E402

API_BASE = "https://api.sofarocean.com"
TOKEN_ENV = "SOFAR_API_TOKEN_BM_REEF"
TELEMETRY = "cellular"  # v1 hard lock — see module docstring
MAX_MESSAGE_BYTES = W.MAX_CONSOLE_LINE_BYTES  # 270, Sofar limit, final newline included
MAX_JSON_BYTES = W.MAX_JSON_BYTES             # 248, the unit's strict-decode limit (sig included)
SENDER_RANGES = ("remote", "service")         # command_wire.RANGES a Mac sender may use
RATE_LIMIT_S = 60  # Sofar: 1 successful request/min/Spotter
DEFAULT_TOPIC = "bmcam/cmd"  # must match bm_commands.topic on the Pi
SEND_LOG = os.path.join(REPO_ROOT, "runs", "sofar_command_sends.jsonl")

# bm pub <topic> <data> <type> <version> — type/version proven in Phase B
BM_PUB_TYPE = 1
BM_PUB_VERSION = 1


def check_id_range(cmd_id, allow_any_range=False):
    """-> the command_wire range name of cmd_id. Raises ValueError for a
    non-int, an id outside every range, or (without allow_any_range) an id
    outside the remote/service ranges: console/heal/conductor ids get no
    cellular ack, so a cloud send with one is almost always a mistake."""
    if not isinstance(cmd_id, int) or isinstance(cmd_id, bool):
        raise ValueError(f"command id must be an int, got {cmd_id!r}")
    rng = W.id_range(cmd_id) if 0 <= cmd_id <= W.MAX_ID else None
    if rng is None:
        raise ValueError(f"command id {cmd_id} is outside every v9 id range")
    if rng not in SENDER_RANGES and not allow_any_range:
        raise ValueError(
            f"command id {cmd_id} is in the {rng} range; a cloud sender uses "
            f"remote 1000000-99999999 (or signed service 100000000-199999999). "
            f"--allow-any-range to override.")
    return rng


def _parse_rsd(data):
    """rsd shape check, only imported when an rsd is sent (command_messages
    carries the v8 tables; nothing else here needs them)."""
    from command_messages import parse_rsd
    return parse_rsd(data)


def parse_value(text):
    """CLI value -> JSON-typed value: strict JSON when it parses (false, 8,
    1.5, null, [1,2,3,4], "quoted"), else the bare text as a string."""
    try:
        return W.strict_loads('{"v":' + text + '}')["v"]
    except (W.Unackable, ValueError):
        return text


def parse_kv_arg(text):
    """'key=value' -> (key, JSON-typed value). Raises ValueError."""
    if "=" not in text:
        raise ValueError(f"expected key=value, got {text!r}")
    key, value = text.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"empty key in {text!r}")
    return key, parse_value(value.strip())


def check_kv_keys(kv, one_shot=False, signed=False):
    """Registry sanity for a set / trg kv, before a cloud send is spent.
    Full paths or short names (G5); `m` is the media's message cap. Refuses
    unknown keys, locked keys, service keys (sign those with
    bm_service_sign.py and send with --json), keys a one-shot may not
    override, and values the registry rejects. signed: a sig-carrying
    service-range command (service keys allowed). Raises ValueError."""
    for name, value in kv.items():
        if name in R.MEDIA_SHORT:
            paths = sorted(set(R.MEDIA_SHORT[name].values()))
        else:
            path = R.resolve_short(name, None)
            if path is None:
                raise ValueError(f"{name!r} is not a setting or short name "
                                 f"(config_registry)")
            paths = [path]
        for path in paths:
            key = R.BY_PATH[path]
            if key.guard == R.LOCKED:
                raise ValueError(f"{path} is locked (deploy only), e:lock")
            if key.guard == R.SERVICE and not signed:
                raise ValueError(f"{path} is a service key: sign it with "
                                 f"tools/bm_service_sign.py and send --json")
            if one_shot and path not in R.ONE_SHOT:
                raise ValueError(f"{path} cannot be a trg one-shot override")
            why = R.check_value(key, value)
            if why:
                raise ValueError(f"{path}={value!r}: {why}")


def guard_notes(kv):
    """One line per guarded key in a set kv (§6.3): what the operator must
    do next. Pure text; an unknown key is skipped (check_kv_keys refuses it)."""
    notes = []
    for name, value in (kv or {}).items():
        path = R.resolve_short(name, None)
        key = R.BY_PATH.get(path) if path else None
        if key is None or key.guard not in (R.GUARDED_STAGE, R.GUARDED_REVERT):
            continue
        if key.guard_when is not None and value not in key.guard_when:
            continue
        if key.guard == R.GUARDED_STAGE:
            notes.append(f"{path}={value!r} is STAGED until a cfm: the ack says "
                         f"s:1; send --cfm <this id> to apply it")
        else:
            notes.append(f"{path}={value!r} applies at once but REVERTS unless "
                         f"confirmed: send --cfm <this id> once it proves good")
    return notes


def build_v9_command(cmd_id, verb, fields=None):
    """{"id", "c", **fields} in wire order (id and c first)."""
    obj = {"id": cmd_id, "c": verb}
    obj.update(fields or {})
    return obj


def validate_command(obj, allow_any_range=False, registry_check=True):
    """A v9 command (dict or JSON text) -> the compact JSON text the unit
    will decode. Raises ValueError on anything the unit would refuse by
    shape — fail here, not after spending a cloud send:
      - id range (check_id_range), JSON <= 248 B (sig included);
      - command_wire.decode (the unit's strict parser) accepts it;
      - registry_check: set / trg kv keys and values (check_kv_keys).
    """
    if isinstance(obj, (bytes, str)):
        try:
            obj = W.strict_loads(obj)
        except W.Unackable as exc:
            raise ValueError(f"not a v9 command: {exc}")
    if not isinstance(obj, dict):
        raise ValueError("a command is a JSON object")
    check_id_range(obj.get("id"), allow_any_range)
    try:
        text = W.encode_command(obj)
    except ValueError as exc:
        raise ValueError(f"not strict JSON: {exc}")
    n = len(text.encode("ascii", "replace"))
    if n > MAX_JSON_BYTES:
        raise ValueError(f"command JSON is {n} B; the unit refuses > "
                         f"{MAX_JSON_BYTES} B (sig included)")
    try:
        cmd = W.decode(text, parse_rsd=_parse_rsd)
    except W.Unackable as exc:
        raise ValueError(f"the unit would drop it (no ack): {exc}")
    except W.Rejected as rej:
        raise ValueError(f"the unit would refuse it: e={rej.code}"
                         + (f" k={rej.key}" if rej.key else "")
                         + (f" ({rej.why})" if rej.why else ""))
    if registry_check and cmd.verb in ("set", "trg") and "kv" in cmd.fields:
        check_kv_keys(cmd.fields["kv"], one_shot=cmd.verb == "trg",
                      signed=cmd.range == "service" and "sig" in cmd.fields)
    return text


def build_command_json(cmd_id, verb, fields=None, allow_any_range=False):
    """Validated compact v9 JSON for id + verb + fields (see
    validate_command). Raises ValueError."""
    return validate_command(build_v9_command(cmd_id, verb, fields),
                            allow_any_range=allow_any_range)


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


def command_from_args(args):
    """argparse namespace -> (verb, fields) or (None, None) when no command
    flag was given. --json is handled by the caller. Raises ValueError."""
    chosen = [name for name, on in (
        ("--set", args.set), ("--get", args.get), ("--reset", args.reset),
        ("--reset-all", args.reset_all), ("--cfm", args.cfm is not None),
        ("--trg", args.trg is not None), ("--hld", args.hld is not None),
        ("--ping", args.ping), ("--help-cmd", args.help_cmd)) if on]
    if len(chosen) > 1:
        raise ValueError(f"one command at a time: {' '.join(chosen)}")
    if args.kv and args.trg is None:
        raise ValueError("--kv belongs to --trg (use --set for a lasting change)")
    if not chosen:
        return None, None
    if args.set:
        kv = {}
        for item in args.set:
            key, value = parse_kv_arg(item)
            if key in kv:
                raise ValueError(f"{key} set twice")
            kv[key] = value
        return "set", {"kv": kv}
    if args.get:
        return "get", {"k": list(args.get)}
    if args.reset:
        return "reset", {"k": list(args.reset)}
    if args.reset_all:
        return "reset", {"all": 1}
    if args.cfm is not None:
        return "cfm", {"ref": args.cfm}
    if args.trg is not None:
        fields = {"v": args.trg}
        if args.kv:
            kv = {}
            for item in args.kv:
                key, value = parse_kv_arg(item)
                if key in kv:
                    raise ValueError(f"{key} given twice in --kv")   # S4c review NIT 5
                kv[key] = value
            fields["kv"] = kv
        return "trg", fields
    if args.hld is not None:
        return "hld", {"v": args.hld}
    if args.ping:
        return "ping", {}
    return "help", {}


def json_from_arg(text, cmd_id):
    """--json text -> a command dict carrying --id. Raises ValueError."""
    try:
        obj = W.strict_loads(text)
    except W.Unackable as exc:
        raise ValueError(f"--json: {exc}")
    if "id" not in obj:
        obj = {"id": cmd_id, **obj}
    elif obj["id"] != cmd_id:
        raise ValueError(f"--json id {obj['id']!r} != --id {cmd_id}")
    return obj


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spotter-id", required=True)
    ap.add_argument("--id", type=int, dest="cmd_id",
                    help="command id: remote range 1000000-99999999 (fresh, "
                         "above every id sent before) or signed service range")
    ap.add_argument("--allow-any-range", action="store_true",
                    help="accept a console/heal/conductor-range id (no "
                         "cellular ack for those)")
    ap.add_argument("--json", default=None,
                    help="any v9 command object (validated by command_wire)")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="set a key (full path or short name); repeatable")
    ap.add_argument("--get", action="append", metavar="KEY",
                    help="get a key or group (or `journal`); repeatable")
    ap.add_argument("--reset", action="append", metavar="KEY",
                    help="reset a key or group to the YAML value; repeatable")
    ap.add_argument("--reset-all", action="store_true",
                    help="reset every command-set key")
    ap.add_argument("--cfm", type=int, metavar="ID",
                    help="confirm the staged/guarded change of command ID")
    ap.add_argument("--trg", type=int, metavar="V",
                    help="0 cancel, 1 capture+save, 2 capture+output per "
                         "mode, 3/4 reference image")
    ap.add_argument("--kv", action="append", metavar="KEY=VALUE",
                    help="one-shot override for --trg; repeatable")
    ap.add_argument("--hld", type=int, metavar="MIN",
                    help="stay awake MIN minutes (0 releases)")
    ap.add_argument("--ping", action="store_true", help="liveness ack")
    ap.add_argument("--help-cmd", action="store_true",
                    help="the unit prints its v9 help on the Spotter console")
    ap.add_argument("--raw-message", default=None,
                    help="send this console line verbatim (bypasses checks)")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--clear-queue", action="store_true",
                    help="clear the cellular mailbox before enqueuing")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="bypass the client-side 60 s rate-limit guard")
    ap.add_argument("--token-env", default=TOKEN_ENV,
                    help=f"env var NAME holding the Sofar API token (default {TOKEN_ENV})")
    ap.add_argument("--send-log", default=SEND_LOG, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    # -- build the message ------------------------------------------------
    try:
        verb, fields = command_from_args(args)
    except ValueError as exc:
        ap.error(str(exc))
    has_command = verb is not None or args.json is not None
    if args.json is not None and verb is not None:
        ap.error("--json and a command flag are mutually exclusive")
    if args.raw_message is not None and has_command:
        ap.error("--raw-message and a command are mutually exclusive")
    message = None
    command_json = None
    if args.raw_message is not None:
        print("[WARN] --raw-message bypasses every v9 check; the Spotter "
              "will execute this line verbatim.")
        message = args.raw_message
    elif has_command:
        if args.cmd_id is None:
            ap.error("a command needs --id (remote range 1000000-99999999)")
        try:
            obj = (json_from_arg(args.json, args.cmd_id) if args.json is not None
                   else build_v9_command(args.cmd_id, verb, fields))
            command_json = validate_command(obj, args.allow_any_range)
            message = build_console_line(command_json, args.topic)
            validate_message(message)
        except ValueError as exc:
            print(f"[REFUSED] {exc}")
            return 2
        rng = W.id_range(args.cmd_id)
        if rng not in SENDER_RANGES:
            print(f"[WARN] id {args.cmd_id} is in the {rng} range: the unit "
                  f"answers on the console only (no cellular ack).")
        if verb == "set":
            for note in guard_notes(fields.get("kv")):
                print(f"[GUARD] {note}")
        if rng == "service" and '"sig"' not in command_json:
            print("[WARN] service-range id without sig: the unit will answer "
                  "e:auth (sign with tools/bm_service_sign.py).")
    elif not args.clear_queue:
        ap.error("need a command (--json/--set/--get/--reset/--reset-all/"
                 "--cfm/--trg/--hld/--ping/--help-cmd), --raw-message, or "
                 "--clear-queue")

    body = {"telemetry": TELEMETRY}
    if message is not None:
        try:
            nbytes = validate_message(message)
        except ValueError as exc:
            print(f"[REFUSED] {exc}")
            return 2
        body["message"] = message
        print(f"message : {message!r}")
        if command_json is not None:
            print(f"json    : {len(command_json)} of {MAX_JSON_BYTES} B")
        print(f"bytes   : {nbytes} of {MAX_MESSAGE_BYTES} (incl. final newline)")
    if args.clear_queue:
        body["clear_command_queue"] = True
        print("[WARN] clear_command_queue=True: ALL pending cellular-mailbox "
              "commands for this Spotter are erased first.")
    print(f"spotter : {args.spotter_id}")
    print(f"POST    : {API_BASE}/user-rest/devices/{args.spotter_id}/command "
          f"(telemetry={TELEMETRY}, token=<{args.token_env}>)")

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

    token = os.environ.get(args.token_env)
    if not token:
        print(f"[ERROR] set {args.token_env} in the environment (never on the CLI).")
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
