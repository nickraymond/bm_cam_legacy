#!/usr/bin/env python3
# filename: command_v9.py
# description: Sprint26 S4 W8a — the commands v9 dispatcher: strict decode, dedupe v2, high-water, CAS, set/reset/trg/rsd/wap/ping/help on the V9State.
"""
Commands v9 on the supervisor path of a migrated unit (DESIGN §6.1–6.2;
PLAN_S4.md G1, G5, G6, G8, G9, G13; b.2c).

One payload in -> one answer out (ack + console line through V9Replies), on the
MAIN thread at a decision point or listen pass (never the burst path: b.7).

Order per command (review A13 / consensus R16):
  strict decode -> id range -> result cache (a duplicate returns the ORIGINAL
  answer + d:1) -> high-water (`old`) -> verb -> validate the WHOLE resulting
  config -> persist (V9State.transaction: nothing changes if the write fails,
  D15) -> journal -> answer.
A rejected id never advances the high-water. Every answer (also a rejection)
is cached, so a re-send gets the same answer.

Duplicate cellular copy (G9): the console always answers; the cellular `d:1`
copy goes at most once per id per process, and never within 10 min of the
original answer in the same process (the mote replays each command ~60 s
later; the console forwards a `bm pub` 4-5x).

Verbs here (b.2c): ping, help, set, reset, trg (v only), rsd, wap. `get`
(b.3), `cfm` + guarded/service keys (b.5), `trg kv` and `hld` (b.6) answer
e:"cmd" / e:"lock" / e:"key" until their commit lands.

Inputs:  the daemon (acks, console, heal screening, wap), the V9State, the
         YAML base values, env facts, a monotonic clock.
Outputs: handle(payload) -> event dict {"action", "id", ...} for the summary.

Known limitations: the help text is the compact registry list (c.4 generates
the full command reference from the same data).
"""

import time

import command_guards as G
import command_wire as W
import config_registry as R
import config_v2
import config_validate
import supervisor_config

DUP_CELLULAR_QUIET_S = 600.0      # G9: no cellular d:1 copy within 10 min of the answer
# R1 ack re-send (Nick 2026-10-03): a cellular ack sent during the Spotter's
# mailbox sync is often dropped ("Unable to submit message to cell-only queue")
# and nothing re-sends it. The next boots re-send the cached answer as the
# existing d:1 duplicate ack, bounded: answers from the last RESEND_BOOTS boots,
# at most RESEND_MAX of them (newest first), once per id per process (G9).
RESEND_BOOTS = 2
RESEND_MAX = 6
GET_MAX_CF_PARTS = 3              # §6.1: a cellular get is capped at 3 <CF> parts (e:"big")
JOURNAL_LINES = 5                 # `get journal`: the last N lines, console only
WAP_VALUES = (0, 1, 2)            # command_tables.WAP_TABLE (wap is unchanged, §6.1)


def _fmt(value):
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_fmt(v) for v in value)
    return str(value)


def render_help():
    """The compact v9 help: verbs, short names, settable groups (console only)."""
    lines = ["v9 commands: bm pub bmcam/cmd <json> 1 1   (json <= 248 B)",
             'ping   {"id":N,"c":"ping"}   help {"id":N,"c":"help"}',
             'get    {"id":N,"c":"get","k":["<key, short or group>",...]}  "journal" = last changes',
             'set    {"id":N,"c":"set","kv":{"<key or short>":value,...}}  all-or-none',
             'reset  {"id":N,"c":"reset","k":["<key or group>"]}  or  {"all":1}',
             'cfm    {"id":N,"c":"cfm","ref":<set id>}  confirms a guarded set',
             'trg    {"id":N,"c":"trg","v":0-4}  0 cancel, 1 capture+save, '
             '2 capture + output per mode, 3/4 reference',
             'hld    {"id":N,"c":"hld","v":<min>}  stay awake (0 releases)',
             'rsd    heals (unchanged)   wap {"v":0-2} WiFi (unchanged)',
             "short: " + " ".join(f"{s}={p}" for s, p in sorted(R.SHORT_NAMES.items()))
             + " m=message cap of the media",
             "ids: 1-99999 console | 1e5- heals | 1e6-99999999 remote (+cellular ack) | "
             "1e8- signed service | 2e9- conductor"]
    return lines


class Dispatcher:
    def __init__(self, daemon, state, base, env=None, clock=time.monotonic,
                 service_key=None, log=print, base_source=None):
        self.daemon = daemon
        self.state = state
        self.base = base
        self.base_source = base_source or {}   # {path: "yaml"|"default"} from the loader
        self.env = env
        self.clock = clock
        self.service_key = service_key
        self._log = log
        self.restart_requested = []   # next-boot keys changed (stay_on exits 72, G10g)
        self.boot = None              # rc_supervisor.Boot (b.6c): note_command, request_hold
        self._answered_at = {}     # id -> clock() when first answered in THIS process
        self._dup_cell = {}        # id -> clock() of the last cellular d:1 copy

    # ------------------------------------------------------------ helpers
    def _state_dict(self, overlay=None):
        s = self.state
        return {"schema": "bm_command_state_v2", "overlay": dict(s.overlay if overlay is None
                                                                 else overlay),
                "overlay_ids": dict(s.overlay_ids), "v8": s.v8, "v8_folded": s.v8_folded,
                "v8_fold_values": s.v8_fold_values}

    def effective(self, overlay=None):
        values = dict(self.base)
        values.update(config_v2.state_overlay(self._state_dict(overlay)))
        return values

    def current_hash(self):
        return supervisor_config.resolve(self.base, self._state_dict(), env=self.env).hash

    def _answer(self, cid, ok, error=None, key=None, text="", staged=False, granted=None,
                duplicate=False, cellular=True):
        replies = self.daemon.v9
        ack, lines = replies.reply(cid, ok, error, {}, duplicate=duplicate, key=key,
                                   text=text, staged=staged, granted=granted)
        if ack is not None and cellular:
            self.daemon._acks.append(ack)
        self.daemon._console.extend(lines)

    def _persist(self, cid, rng, answer, mutate=None, source=None):
        """One transaction: the verb's change + the cached answer + the
        high-water. Journal after the persist. Raises on a write failure.
        The journal's old/new are the EFFECTIVE values (YAML + overlay), the
        ones the console answer shows (S5 F4: it logged the overlay's own
        value, `none -> 03:00` for a key the YAML had at 00:00)."""
        done = []
        hw = []
        before = self.effective() if mutate is not None else None

        def m(st):
            if mutate is not None:
                done.extend(mutate(st) or [])
            st.remember(cid, answer)
            if answer.get("ok") and rng in W.HIGH_WATER_RANGES:
                old = st.high_water.get(rng)
                st.advance_high_water(rng, cid)
                if st.high_water.get(rng) != old:
                    hw.append((old, st.high_water[rng]))
        self.state.transaction(m)
        after = self.effective() if done else None
        for path, old, new in done:
            if isinstance(path, str) and "." in path:
                if path in before:
                    old, new = before.get(path), after.get(path)
                self.state.journal(source or rng or "unknown", path, old, new, cid)
        for old, new in hw:
            # S4b review #5: the journal survives a lost state file; this line
            # lets the high-water be re-seeded (replay stays blocked).
            self.state.journal("hw", f"high_water.{rng}", old, new, cid)
        return done

    def _reject(self, cid, rng, code, key=None, why=""):
        answer = {"ok": 0, "e": code, "k": key}
        try:
            self._persist(cid, rng, answer)
        except Exception as exc:
            self._log(f"[CMD][WARN] rejection of id={cid} not cached: {exc}")
        self._answered_at.setdefault(cid, self.clock())
        self._answer(cid, False, code, key=key, text=why)
        return {"action": "rejected", "id": cid, "e": code}

    # -------------------------------------------------------------- entry
    def handle(self, payload):
        if self.boot is not None:
            self.boot.note_command()      # keep-alive: any command received (§4)
        try:
            cmd = W.decode(payload, parse_rsd=_parse_rsd)
        except W.Unackable as exc:
            self.daemon.stats["unackable"] += 1
            self.daemon._console.append(W.console_line(
                self.daemon.v9.host, f"DROPPED (no ack): {exc}"))
            print(f"[CMD] dropped unackable payload: {exc} bytes={bytes(payload)[:40]!r}")
            return {"action": "dropped"}
        except W.Rejected as rej:
            self.daemon.stats["rejected"] += 1
            return self._rejected_before_verb(rej)

        cid, rng = cmd.id, cmd.range
        cached = self.state.cached(cid)
        if cached is not None:
            return self._duplicate(cid, cached)
        if rng == "service" and not ("sig" in cmd.fields
                                     and W.verify_sig(cmd.raw, self.service_key)):
            # S4b review #3: an unsigned service-range id must never move the
            # service high-water (that would close the range for good).
            self.daemon.stats["rejected"] += 1
            # Review N1: not cached, so a forged unsigned id cannot block the real
            # signed command that will carry it (the high-water never moved).
            self._answer(cid, False, "auth", key="sig",
                         text="service-range ids need a valid service signature")
            return {"action": "rejected", "id": cid, "e": "auth"}
        if rng in W.HIGH_WATER_RANGES and self.state.is_old(rng, cid):
            self.daemon.stats["rejected"] += 1
            return self._reject(cid, rng, "old", why="below this sender's newest id")
        handler = getattr(self, "_verb_" + cmd.verb, None)
        self._answered_ok = None
        try:
            event = handler(cmd)
        except Exception as exc:          # a persist failure (D15) or a bug: no ok ack
            if self._answered_ok == cid:
                # the ok was persisted AND answered; a later step failed (review #11)
                self._log(f"[CMD][WARN] id={cid} {cmd.verb}: after the answer: "
                          f"{type(exc).__name__}: {exc}")
                return {"action": "applied", "id": cid}
            self.daemon.stats["rejected"] += 1
            print(f"[CMD][ERROR] id={cid} {cmd.verb} failed: {type(exc).__name__}: {exc}")
            self._answer(cid, False, "err", text=f"{cmd.verb}: not saved ({exc})")
            return {"action": "persist_failed", "id": cid}
        if event.get("action") == "applied":
            self.daemon.stats["applied"] += 1
        return event

    def _rejected_before_verb(self, rej):
        rng = W.id_range(rej.id)
        cached = self.state.cached(rej.id)
        if cached is not None:
            return self._duplicate(rej.id, cached)
        return self._reject(rej.id, rng, rej.code, key=rej.key, why=rej.why)

    def _duplicate(self, cid, cached):
        self.daemon.stats["duplicates"] += 1
        now = self.clock()
        first = self._answered_at.get(cid)
        last = self._dup_cell.get(cid)
        if first is not None:       # answered in THIS process: once, after 10 min
            cellular = now - first >= DUP_CELLULAR_QUIET_S and last is None
        else:                       # answered in an earlier process: once
            cellular = last is None
        if cellular:
            self._dup_cell[cid] = now
        ok, text, granted = self._dup_fields(cached, first)
        self._answer(cid, ok, cached.get("e"), key=cached.get("k"), text=text,
                     staged=cached.get("s"), granted=granted, duplicate=True,
                     cellular=cellular)
        if cellular and ok and cached.get("g"):
            # §6.2: a duplicate get re-sends its <CF> (at most once per 10 min, G9).
            try:
                names = cached["g"]
                fake = W.Command(id=cid, verb="get", range=W.id_range(cid))
                self._queue_cf(fake, self._get_items(fake, names))
            except W.Rejected:
                pass
        print(f"[CMD] duplicate id={cid}: original answer "
              f"({'cellular + ' if cellular else ''}console)")
        return {"action": "duplicate", "id": cid}

    @staticmethod
    def _dup_fields(cached, first):
        """(ok, text, granted) of a duplicate answer from its cached original."""
        ok = bool(cached.get("ok"))
        text, granted = cached.get("t", ""), cached.get("v")
        if cached.get("hld") and first is None:
            # S4b review #9: a hold is never persisted; one granted in an earlier
            # boot is not active now (the re-send must not claim it is).
            text, granted = "hold from an earlier boot: NOT active (send a new hld)", 0
        return ok, text, granted

    def recent_answers(self, boots=RESEND_BOOTS, max_n=RESEND_MAX):
        """R1 ack re-send: the d:1 duplicate acks (the form the backend already
        reads) of the cellular-range answers cached in the last `boots` boots
        BEFORE this one (result_cache `b`), newest `max_n`, oldest first. Each
        counts as this process's one cellular d:1 copy of that id (G9), so a
        mote replay later in this process does not send it again; ids already
        answered or copied in this process are skipped. Console lines are not
        repeated (the console is not lossy). -> [(id, ack JSON)]"""
        cur = getattr(self.state, "boot_counter", None)
        cache = getattr(self.state, "result_cache", None)
        if not isinstance(cur, int) or not isinstance(cache, dict) or max_n <= 0:
            return []
        picks = []
        for key, cached in cache.items():          # insertion order: oldest first
            try:
                cid = int(key)
            except (TypeError, ValueError):
                continue
            b = cached.get("b") if isinstance(cached, dict) else None
            if not isinstance(b, int) or not cur - boots <= b < cur:
                continue
            if W.id_range(cid) not in W.CELLULAR_RANGES:
                continue
            if cid in self._dup_cell or cid in self._answered_at:
                continue
            picks.append(cid)
        out = []
        for cid in picks[-max_n:]:
            cached = cache[str(cid)]
            ok, text, granted = self._dup_fields(cached, None)
            ack, _lines = self.daemon.v9.reply(cid, ok, cached.get("e"), {}, duplicate=True,
                                               key=cached.get("k"), text=text,
                                               staged=cached.get("s"), granted=granted)
            if ack is None:
                continue
            self._dup_cell[cid] = self.clock()
            out.append((cid, ack))
        return out

    def _applied(self, cmd, text, mutate=None, source=None, answer_extra=None):
        answer = {"ok": 1, "t": text[:120]}
        answer.update(answer_extra or {})
        done = self._persist(cmd.id, cmd.range, answer, mutate=mutate, source=source)
        self._answered_at.setdefault(cmd.id, self.clock())
        self._answer(cmd.id, True, text=text, staged=answer.get("s"), granted=answer.get("v"))
        self._answered_ok = cmd.id
        print(f"[CMD] applied id={cmd.id} {cmd.verb}: {text}")
        return {"action": "applied", "id": cmd.id, "changed": done}

    # -------------------------------------------------------------- verbs
    def _verb_ping(self, cmd):
        return self._applied(cmd, "ping")

    def _verb_help(self, cmd):
        event = self._applied(cmd, "help (reference follows)")
        self.daemon._console.extend(render_help())
        return event

    # ------------------------------------------------------------ get / <CF>
    def _source(self, path):
        if path in self.state.overlay:
            cid = self.state.overlay_ids.get(path)
            return ("cmd", cid) if cid is not None else "v8"
        return self.base_source.get(path, "yaml")

    def _get_items(self, cmd, names):
        media = self.effective().get("mode.media")
        values = self.effective()
        paths = []
        for name in names:
            path = R.resolve_short(name, media)
            group = [k.path for k in R.keys_in(name)] if path is None else [path]
            if not group:
                raise W.Rejected(cmd.id, "key", name, f"{name!r} is not a setting or group")
            paths += [p for p in group if p not in paths]
        return [(p, values.get(p), self._source(p)) for p in paths]

    def _queue_cf(self, cmd, items, head=()):
        """<CF> parts for a cellular sender, through the ack pacer (one
        pacer for every small uplink message)."""
        if W.id_range(cmd.id) not in W.CELLULAR_RANGES:
            return 0
        parts = W.build_cf(self.current_hash(), items, head=head)
        self.daemon._acks.extend(parts)
        return len(parts)

    def _verb_get(self, cmd):
        names = cmd.fields["k"]
        if names == ["journal"]:
            return self._get_journal(cmd)
        try:
            items = self._get_items(cmd, names)
        except W.Rejected as rej:
            return self._reject(cmd.id, cmd.range, rej.code, rej.key, rej.why)
        console_only = cmd.fields.get("to") == "con"
        cellular = W.id_range(cmd.id) in W.CELLULAR_RANGES and not console_only
        if cellular and len(W.build_cf("00000000", items)) > GET_MAX_CF_PARTS:
            return self._reject(cmd.id, cmd.range, "big", None,
                                f"{len(items)} keys need more than {GET_MAX_CF_PARTS} <CF> "
                                'parts; ask for fewer, or add "to":"con"')
        event = self._applied(cmd, f"get {', '.join(names)}: {len(items)} key(s)",
                              answer_extra={"g": list(names)})
        self._console_items(items)
        if cellular:
            self._queue_cf(cmd, items)
        return event

    def _console_items(self, items):
        for path, value, source in items:
            src = source if isinstance(source, str) else f"cmd {source[1]}"
            self.daemon._console.append(f"  {path} = {_fmt(value)} ({src})")

    def _get_journal(self, cmd):
        import config_journal
        entries = [e for e in config_journal.read(config_journal.path_beside(self.state.path))
                   if e.get("src") != "hw"][-JOURNAL_LINES:]      # review N2: settings only
        event = self._applied(cmd, f"get journal: last {len(entries)} change(s)")
        for e in entries:
            self.daemon._console.append(
                f"  {e.get('t')} {e.get('src')} id={e.get('id')} {e.get('key')}: "
                f"{_fmt(e.get('old'))} -> {_fmt(e.get('new'))}")
        return event

    def _verb_cfm(self, cmd):
        """§6.3: confirms a guarded set. The confirm can only arrive over the
        new path, so it proves the path works."""
        ref = cmd.fields["ref"]
        pending = {p: r for p, r in self.state.guarded.items() if r.get("ref") == ref}
        if not pending:
            return self._reject(cmd.id, cmd.range, "ref", None,
                                f"nothing is waiting for a cfm of id {ref}")
        staged = [p for p, r in pending.items() if r.get("cls") == "stage"]
        if staged:
            new_overlay = dict(self.state.overlay)
            new_overlay.update((p, pending[p]["new"]) for p in staged)
            try:
                self._validate(cmd, new_overlay, staged)        # review #6: as it is NOW
            except W.Rejected as rej:
                return self._reject(cmd.id, cmd.range, rej.code, rej.key, rej.why)
        # S5 F3: a staged value is applied now but governs from the next
        # decision point (next action) or the next boot, like `set` says.
        text = f"cfm {ref}: " + ", ".join(
            (f"{p} applied ({'next boot' if R.BY_PATH[p].apply == R.NEXT_BOOT else 'next action'})"
             if p in staged else f"{p} confirmed") for p in sorted(pending))
        event = self._applied(cmd, text, mutate=lambda st: G.confirm(st, ref))
        self._change_summary(cmd, event)
        return event

    def report_errors(self, errors):
        """K7 / DESIGN §5.1: the boot's config problems as <CF v=1 h=.. err=<kind>
        k=<key>> lines, cellular whatever the id (nobody asked; the backend must
        see why the unit runs what it runs). errors: [(kind, key, why)] with
        kind "overlay" (a remote value not run), "base" (the YAML fails an S4
        rule), "level" (a config fallback: lkg / v1_migrated)."""
        if not errors:
            return 0
        h = self.current_hash()
        for kind, key, why in errors:
            head = [("err", kind)] + ([("k", key)] if key else [])
            self.daemon._acks.extend(W.build_cf(h, [], head=head))
            self.daemon._console.append(W.console_line(
                self.daemon.v9.host, f"CONFIG {kind.upper()}: {key or ''} {why}".strip()))
        return len(errors)

    def flush_notes(self):
        """G10f: every pending <CF reverted=..> goes cellular (a revert has no
        remote id to choose a lane by), then the notes are cleared."""
        notes = G.take_notes(self.state)
        if not notes:
            return 0
        h = self.current_hash()
        for n in notes:
            self.daemon._acks.extend(W.build_cf(h, [], head=[
                ("reverted", n.get("reverted")), ("lim", n.get("lim")), ("ref", n.get("ref"))]))
            self.daemon._console.append(W.console_line(
                self.daemon.v9.host, f"REVERTED {n.get('reverted')} (limit {n.get('lim')}, no "
                                     f"cfm for id {n.get('ref')}) cfg={h}"))
        try:
            self.state.transaction(lambda st: st.extra.pop("notes", None))
        except Exception as exc:
            self._log(f"[CMD][WARN] revert notes not cleared (sent again next time): {exc}")
        return len(notes)

    def _verb_hld(self, cmd):
        """§4: hold the unit awake v minutes (0 releases); not persisted; the
        ack's `v` is the minutes actually granted (hold_max_min, and on
        per_boot the budget unless power.bus_always_on)."""
        v = cmd.fields["v"]
        if self.boot is not None:
            granted = self.boot.request_hold(v)
        else:
            granted = min(v, int(self.effective().get("commands.hold_max_min", 120)))
        if v == 0:
            text = "hold released"
        else:
            text = f"hold awake {granted} min"
            if granted < v:
                text += f" (asked {v}: clamped by hold_max_min / the bus-power budget)"
        return self._applied(cmd, text, answer_extra={"v": granted, "hld": 1})

    def _paths_for(self, cmd, names, media):
        """Resolve names (full path, short name, or for reset a group prefix)."""
        out = []
        for name in names:
            path = R.resolve_short(name, media)
            if path is None:
                raise W.Rejected(cmd.id, "key", name, f"{name!r} is not a setting")
            out.append((name, path))
        return out

    def _guard(self, cmd, key, value):
        """-> guard class ("revert" | "stage" | None), or raises Rejected.
        LOCKED: never by command. SERVICE: a service-range id AND a valid
        signature with the unit's key (§6.3), then guarded_revert."""
        if key.guard == R.LOCKED:
            raise W.Rejected(cmd.id, "lock", key.path, f"{key.path} is locked (deploy only)")
        signed = False
        if key.guard == R.SERVICE:
            signed = (cmd.range == "service" and "sig" in cmd.fields
                      and W.verify_sig(cmd.raw, self.service_key))
            if not signed:
                raise W.Rejected(cmd.id, "auth", key.path,
                                 f"{key.path} needs a signed service-range command")
        return G.guard_class(key, value, service_signed=signed)

    def _check_cas(self, cmd):
        want = cmd.fields.get("b")
        if want is not None:
            have = self.current_hash()
            if have != want:
                raise W.Rejected(cmd.id, "cas", None, f"config is {have}, not {want}")

    def _validate(self, cmd, new_overlay, paths):
        """Whole-config validation of the would-be effective config: any rule
        that names one of `paths` rejects the command."""
        values = self.effective(new_overlay)
        env = self.env
        zones = [values.get(p) for p in paths if R.BY_PATH.get(p) and R.BY_PATH[p].type == R.TZ]
        if env is not None and zones:
            # A zone being SET is probed now (env holds the configured one only).
            env = dict(env, timezones_ok=set(env.get("timezones_ok") or ())
                       | config_validate.probe_env(zones)["timezones_ok"])
        for v in config_validate.validate(values, "effective", env=env):
            hit = [p for p in v.paths if p in paths]
            if hit:
                raise W.Rejected(cmd.id, v.code, hit[0], v.message)

    def _verb_set(self, cmd):
        try:
            self._check_cas(cmd)
            kv = cmd.fields["kv"]
            med = next((kv[n] for n in kv if R.resolve_short(n, None) == "mode.media"), None)
            media = med or self.effective().get("mode.media")      # review #7
            changes, paths = [], []
            for name, path in self._paths_for(cmd, cmd.fields["kv"], media):
                value = cmd.fields["kv"][name]
                key = R.BY_PATH[path]
                # A zone is judged through env when the supervisor probed one
                # (G14); without env, the registry's own zoneinfo check.
                why = None if (key.type == R.TZ and self.env is not None) \
                    else R.check_value(key, value)
                if why:
                    raise W.Rejected(cmd.id, "val", path, why)
                if path == "mode.media" and value == "video_logger":
                    # S4b review R2-3: the recorder runs the v8 path until R1; a
                    # remote switch there would leave v9 (and its guards) for good.
                    raise W.Rejected(cmd.id, "lock", path, "video_logger is set by deploy only")
                cls = self._guard(cmd, key, value)
                if path in paths:
                    raise W.Rejected(cmd.id, "key", path, "set twice in one command")
                changes.append((path, value, cls))
                paths.append(path)
            new_overlay = dict(self.state.overlay)
            new_overlay.update((p, v) for p, v, _c in changes)   # staged values validated too
            self._validate(cmd, new_overlay, paths)
        except W.Rejected as rej:
            return self._reject(cmd.id, cmd.range, rej.code, rej.key, rej.why)
        before = self.effective()
        applied = [(p, v) for p, v, c in changes if c != "stage"]
        staged = [(p, v) for p, v, c in changes if c == "stage"]
        guarded = [(p, v, c) for p, v, c in changes if c is not None]
        parts = [f"{p}: {_fmt(before.get(p))} -> {_fmt(v)}" for p, v in applied
                 if before.get(p) != v]
        parts += [f"{p}: STAGED {_fmt(v)} (awaiting cfm {cmd.id})" for p, v in staged]
        parts += [f"{p} guarded: cfm {cmd.id} within "
                  f"{'3 boots or 2 h' if G.limit_for(p) == 'boot3_2h' else '2 sends or 3 boots'}"
                  for p, v, c in guarded if c == "revert"]
        text = ", ".join(parts) or "no change (already in effect)"
        when = "next boot" if any(R.BY_PATH[p].apply == R.NEXT_BOOT for p, _v in applied) \
            else "next action"

        def mutate(st):
            for p, v, c in changes:
                if c is None:
                    # S4b review #6: an unguarded value replaces a staged or
                    # probationary one (a later cfm of the old id must not apply it).
                    st.guarded.pop(p, None)
            for p, v, c in guarded:
                st.guarded[p] = G.new_record(st, p, cmd.id, c, v)
            return st.apply_overlay(applied, cmd.id)
        event = self._applied(cmd, f"{text} ({when})", mutate=mutate,
                              answer_extra={"s": 1} if staged else None)
        self._change_summary(cmd, event)
        return event

    def _verb_reset(self, cmd):
        import command_state_v9 as S
        try:
            self._check_cas(cmd)
            if cmd.fields.get("all") == 1:
                paths = sorted(set(self.state.overlay) | set(self.state.guarded))
            else:
                paths = []
                media = self.effective().get("mode.media")
                for name in cmd.fields["k"]:
                    path = R.resolve_short(name, media)
                    group = [k.path for k in R.keys_in(name)]
                    if path is None and not group:
                        raise W.Rejected(cmd.id, "key", name, f"{name!r} is not a setting")
                    for p in ([path] if path else group):
                        if (p in self.state.overlay or p in self.state.guarded) \
                                and p not in paths:
                            paths.append(p)
            for p in paths:
                key = R.BY_PATH.get(p)
                if key is not None and key.guard == R.SERVICE and not (
                        cmd.range == "service" and "sig" in cmd.fields
                        and W.verify_sig(cmd.raw, self.service_key)):
                    raise W.Rejected(cmd.id, "auth", p, f"{p} needs a valid service signature")
            new_overlay = {k: v for k, v in self.state.overlay.items() if k not in paths}
            self._validate(cmd, new_overlay, paths)
        except W.Rejected as rej:
            return self._reject(cmd.id, cmd.range, rej.code, rej.key, rej.why)
        text = ("reset " + ", ".join(paths) + " -> YAML") if paths else "reset: nothing to reset"
        changes = [(p, S.remove()) for p in paths]

        def mutate(st):
            for p in paths:                     # back to the YAML: nothing left to guard
                st.guarded.pop(p, None)
            return st.apply_overlay(changes, cmd.id)
        event = self._applied(cmd, text, mutate=mutate)
        self._change_summary(cmd, event)
        return event

    def _change_summary(self, cmd, event):
        """R5 / DESIGN §6.2: a <CF> of the keys that changed, after the ack,
        for a cellular sender (the backend's hash -> snapshot record). Also
        notes next-boot keys for the stay_on config restart (G10g). Never
        raises: the ok is already persisted and answered."""
        try:
            self._change_summary_inner(cmd, event)
        except Exception as exc:
            self._log(f"[CMD][WARN] id={cmd.id}: change summary not sent ({exc})")

    def _change_summary_inner(self, cmd, event):
        changed = [path for path, _old, _new in event.get("changed", [])]
        for path in changed:
            key = R.BY_PATH.get(path)
            if key is not None and key.apply == R.NEXT_BOOT and path not in self.restart_requested:
                self.restart_requested.append(path)
        if changed:
            values = self.effective()
            self._queue_cf(cmd, [(p, values.get(p), self._source(p)) for p in changed])

    def one_shot(self, names_values, v=2):
        """Resolve + validate a trg kv against the CURRENT effective config
        (§9, G6): names -> full paths with the ACTION's media (a `med` in the
        kv decides it), each key in R.ONE_SHOT, registry types, then the
        whole per-action copy. -> ({path: value}, media). Raises Rejected
        (id 0: the caller re-labels). Used at `trg` time AND at action time."""
        effective = self.effective()
        med_name = next((n for n in names_values if R.resolve_short(n, None) == "mode.media"),
                        None)
        media = names_values[med_name] if med_name else effective.get("mode.media")
        out = {}
        for name, value in names_values.items():
            path = R.resolve_short(name, media)
            if path is None or path not in R.ONE_SHOT:
                raise W.Rejected(0, "key", name, f"{name!r} cannot be set for one action")
            if path == "still.crop" and media != "still":
                raise W.Rejected(0, "key", name, "r (still.crop) on a video action: the "
                                 "recording geometry is used")
            why = R.check_value(R.BY_PATH[path], value)
            if why:
                raise W.Rejected(0, "val", path, why)
            if path in out:
                raise W.Rejected(0, "key", path, "set twice in one command")
            out[path] = value
        if out.get("mode.media") == "video_logger":
            raise W.Rejected(0, "val", "mode.media", "a one-shot action is a still or a clip")
        values = dict(effective)
        values.update(out)
        for viol in config_validate.validate(values, "effective", env=self.env):
            hit = [p for p in viol.paths if p in out]
            if hit:
                raise W.Rejected(0, viol.code, hit[0], viol.message)
        return out, media

    def _verb_trg(self, cmd):
        v = cmd.fields["v"]
        kv = {}
        if cmd.fields.get("kv"):
            try:
                kv, media = self.one_shot(cmd.fields["kv"], v)
            except W.Rejected as rej:
                return self._reject(cmd.id, cmd.range, rej.code, rej.key, rej.why)
        labels = {0: "trg 0: pending trigger cancelled", 1: "trg 1 armed: capture, save only",
                  2: "trg 2 armed: capture + output per mode",
                  3: "trg 3 armed: stored reef reference", 4: "trg 4 armed: stored reference card"}
        text = labels[v] + ("" if v == 0 else " (next decision point)")
        if kv:
            # G7: the resolved one-shot values (and a d != 5 flag) on the console;
            # the ack stays slim; START carries tg/r/m/d (W8b).
            shown = ", ".join(f"{p}={_fmt(val)}" for p, val in kv.items())
            d = kv.get("video.send.duration_s")
            flag = f" (d={_fmt(d)}! only 5 s is ladder-validated)" if d is not None and d != 5 else ""
            text += f"; one action only: {shown}{flag}"
        return self._applied(cmd, text, mutate=lambda st: st.arm_trigger(cmd.id, v, kv))

    def _verb_rsd(self, cmd):
        n0 = len(self.daemon.heal_events)
        try:
            return self._rsd(cmd)
        except Exception:
            # review #10: nothing was stored, so <HL> must not report it either
            del self.daemon.heal_events[n0:]
            raise

    def _rsd(self, cmd):
        value, ok = self.daemon._screen_heals(cmd.id, cmd.rsd)
        if value.get("x"):
            text = "rsd: every pending heal cancelled"
        else:
            text = f"rsd: {len(value.get('h', []))} heal(s) queued (<HL> reports each)"
        if not ok:
            answer = {"ok": 0, "e": "rsd", "t": "rsd: every heal refused (<HL> reports each)"}
            self._persist(cmd.id, cmd.range, answer,
                          mutate=lambda st: st.record_heals(cmd.id, value))
            self._answered_at.setdefault(cmd.id, self.clock())
            self._answer(cmd.id, False, "rsd", text=answer["t"])
            return {"action": "rejected", "id": cmd.id, "e": "rsd"}
        return self._applied(cmd, text, mutate=lambda st: st.record_heals(cmd.id, value))

    def _verb_wap(self, cmd):
        v = cmd.fields["v"]
        if v not in WAP_VALUES:
            return self._reject(cmd.id, cmd.range, "val", "v", "wap v is 0, 1 or 2")
        event = self._applied(cmd, f"wap {v}: network change dispatched")
        fn = self.daemon.wap_action_fn
        if fn is None:
            print("[CMD][WARN] wap acked but no action wired; network unchanged")
        else:
            try:
                fn(v)
            except Exception as exc:
                print(f"[CMD][WARN] wap action failed: {exc}")
        return event


def check_persisted_kv(kv, v):
    """The load-time check of a persisted trg kv (V9State trigger_validator):
    full paths only, each in R.ONE_SHOT, registry types. The whole-config check
    runs again at action time (Boot._apply_one_shot). -> reason or None."""
    for path, value in (kv or {}).items():
        if path not in R.ONE_SHOT:
            return f"{path!r} is not a one-shot key"
        why = R.check_value(R.BY_PATH[path], value)
        if why:
            return f"{path}: {why}"
    return None


def _parse_rsd(data):
    from command_messages import parse_rsd
    return parse_rsd(data)


def load_service_key(path=W.SERVICE_KEY_PATH):
    """The unit's service key (§6.3), or None (then every service change is
    refused e:"auth"). Read once per process."""
    try:
        with open(path, "r", encoding="ascii") as fh:
            return W.parse_service_key(fh.read())
    except (OSError, UnicodeDecodeError):
        return None
