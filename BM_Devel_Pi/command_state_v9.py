#!/usr/bin/env python3
# filename: command_state_v9.py
# description: Sprint26 S4 a.4 — the ONE owner of bm_command_state_v2.json on the v9 path: overlay, result cache, high-water, boot counter, guarded records, trigger, heals.
"""
Command state for commands v9 (DESIGN §6.2 "State file"; PLAN_S4.md a.4, G1,
G2, G9, G10; review B1: one writer per file).

On the supervisor path of a migrated unit this object owns the WHOLE state
file. The v8 CommandState is never constructed there, so nothing can write
back a stale copy of these fields (the B1 blocker). The `v8` section is
passed through verbatim (legacy reads it on a rollback), except its
`pending_heals` (shared: a heal is a heal under either runtime) and a
`pending_trigger` moved once into `pending_trigger_v9` (a one-shot must not
fire twice).

File (schema bm_command_state_v2):
  boot_counter        int, +1 per counted boot (b.5), persisted before the UART
  overlay             {path: value}        remote/local changes over the YAML
  overlay_ids         {path: command id}   who set each overlay key (<CF> @c<id>)
  guarded             {path: record}       guarded_revert / guarded_stage (b.5)
  result_cache        {"<id>": {ok,h[,e,k,s,v],b}}  last 256 answers (dedupe v2); b = boot
  high_water          {"remote": id, "service": id}
  pending_trigger_v9  {"id", "v", "kv"} or null (kv re-validated on load)
  v8_folded           hash of the v8 settings last folded into the overlay (G2)
  v8_fold_values      {path: value} that fold produced (re-fold precedence)
  v8                  the S2 v8 section, verbatim (+ shared pending_heals)
  (any other key)     kept verbatim

Writes: atomic_io (unique tmp, fsync, rename, fsync dir). A change is applied
in memory ONLY after its persist succeeded (D15): every mutation runs inside
transaction(), which snapshots, writes, and restores the snapshot if the write
raises. The in-memory mutators (remember, advance_high_water, arm_trigger,
record_heals) refuse to run outside a transaction (review S4a #2). Journal
lines are appended after the persist (a journal failure is logged, never
fatal).

A LOST file (exists but unreadable / foreign schema) must not reopen replay
(review S4a #3): the high-water marks are re-seeded from the config journal
(highest id per range), and the service range is closed (mark at its top)
until a field update, because a captured signed line must never re-apply.

Example:
  s = V9State("/tmp/bm_command_state_v2.json")
  s.commit([("mode.run", "stay_on")], cid=1000001, source="remote")

Known limitations: not safe for two processes (one supervisor per boot, and
dev_mode.sh takes the cycle's flock, b.7). The guard COUNTING rules live in
the supervisor (b.5); this file only stores the records.
"""

import copy
import hashlib
import json
import os

import atomic_io

SCHEMA = "bm_command_state_v2"
RESULT_CACHE_MAX = 256
PENDING_HEALS_MAX = 8        # command_state.PENDING_HEALS_MAX (SPEC_resend_heal §5)
HEAL_WAKES = 3
_REMOVE = object()           # commit() marker: drop the key from the overlay

_OWNED = ("boot_counter", "overlay", "overlay_ids", "guarded", "result_cache",
          "high_water", "pending_trigger_v9", "v8_folded", "v8_fold_values", "v8")


def remove():
    """The value to pass to commit() to drop a key from the overlay (reset)."""
    return _REMOVE


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _valid_heal(item):
    # Same rule as command_state._valid_heal (imported lazily there to avoid
    # pulling the v8 tables into the v9 path).
    import re
    if not isinstance(item, dict):
        return None
    key, ns, hid, wakes = (item.get(k) for k in ("key", "n", "id", "wakes_left"))
    if not isinstance(key, str) or not re.match(r"^[0-9a-z]{6}\Z", key):
        return None
    if not isinstance(ns, list) or not ns or any(not _is_int(n) or n < 0 for n in ns):
        return None
    if not _is_int(hid) or not _is_int(wakes) or not 1 <= wakes <= HEAL_WAKES:
        return None
    return {"key": key, "n": sorted(set(ns)), "id": hid, "wakes_left": wakes}


def plan_fold(overlay, overlay_ids, v8, v8_folded, v8_fold_values, overlay_from_v8):
    """The G2 fold rules as one pure function (V9State.fold_v8 applies it;
    config_v2.state_overlay uses it to compute the same overlay without
    writing, so the two can never disagree).
    -> (overlay after, this fold's values, [(path, old, new)] changes).

    First fold (v8_folded None): v8 values fill keys the overlay lacks (the
    overlay wins, = S2's "v8 then overlay"). Re-fold: only v8 keys whose value
    CHANGED since the last fold win; a key the fold no longer produces (hlt 0,
    twn 0, tmz 0 carry no override) goes back to the YAML unless a v9 command
    set it since (it has an overlay id; review S4a #1). Unchanged v8: no-op."""
    overlay = dict(overlay or {})
    v8 = v8 or {}
    if v8_folded is not None and v8_folded == v8_settings_hash(v8):
        return overlay, dict(v8_fold_values or {}), []
    fold = overlay_from_v8(v8) if v8 else {}
    first = v8_folded is None
    last = v8_fold_values or {}
    done = []
    for path, value in fold.items():
        win = path not in overlay if first else (path not in last or last[path] != value)
        if win and overlay.get(path, _REMOVE) != value:
            done.append((path, overlay.get(path), value))
            overlay[path] = value
    if not first:
        for path, old_value in last.items():
            if path not in fold and path in overlay and overlay[path] == old_value \
                    and path not in (overlay_ids or {}):
                done.append((path, overlay.pop(path), None))
    return overlay, fold, done


def v8_settings_hash(v8):
    """Fingerprint of the v8 settings a fold reads (settings + touched)."""
    body = {"settings": (v8 or {}).get("settings"), "touched": (v8 or {}).get("touched")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:8]


class V9State:
    def __init__(self, path, trigger_validator=None, log=print):
        """trigger_validator(kv, v) -> None or a reason: re-validates a persisted
        one-shot kv against today's registry (G6). log: one-line sink."""
        self.path = path
        self.is_v2 = True
        self._log = log
        self.boot_counter = 0
        self.overlay = {}
        self.overlay_ids = {}
        self.guarded = {}
        self.result_cache = {}
        self.high_water = {}
        self.pending_trigger_v9 = None
        self.v8_folded = None
        self.v8_fold_values = {}
        self.v8 = {}
        self.extra = {}
        self.load_info = {"source": "defaults", "error": None, "dropped": []}
        self._keep_aside = False     # an unreadable file is copied aside before the first save
        self._in_txn = False
        self._load(trigger_validator)

    # ------------------------------------------------------------------ load
    def _load(self, trigger_validator):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            if data.get("schema") != SCHEMA:
                raise ValueError(f"schema {data.get('schema')!r} is not {SCHEMA}")
        except Exception as exc:
            self.load_info["error"] = str(exc)
            self._keep_aside = True
            self._log(f"[CMD][WARN] v9 state {self.path} unreadable ({exc}); starting empty "
                      f"(the file is kept as {self.path}.unreadable at the first save)")
            self._seed_lost_high_water()
            return
        self.load_info["source"] = "file"
        self.extra = {k: v for k, v in data.items() if k not in _OWNED and k != "schema"}

        def take(name, kind, default):
            value = data.get(name, default)
            if not isinstance(value, kind) or isinstance(value, bool):
                if name in data and value is not None:
                    self.load_info["dropped"].append(name)
                    self._log(f"[CMD][WARN] v9 state {name}={value!r} malformed; reset")
                return copy.deepcopy(default)
            return value

        self.boot_counter = take("boot_counter", int, 0)
        self.overlay = take("overlay", dict, {})
        self.overlay_ids = take("overlay_ids", dict, {})
        self.guarded = take("guarded", dict, {})
        self.result_cache = take("result_cache", dict, {})
        self.high_water = {k: v for k, v in take("high_water", dict, {}).items() if _is_int(v)}
        self.v8_folded = data.get("v8_folded") if isinstance(data.get("v8_folded"), str) else None
        self.v8_fold_values = take("v8_fold_values", dict, {})
        self.v8 = take("v8", dict, {})
        heals = self.v8.get("pending_heals")
        if isinstance(heals, list):
            good = [h for h in (_valid_heal(x) for x in heals) if h]
            if len(good) != len(heals):
                self._log("[CMD][WARN] v9 state: invalid pending heal(s) dropped")
            self.v8["pending_heals"] = good[:PENDING_HEALS_MAX]

        trig = data.get("pending_trigger_v9")
        if trig is not None:
            why = self._trigger_problem(trig, trigger_validator)
            if why:
                self.load_info["dropped"].append("pending_trigger_v9")
                self._log(f"[CMD][WARN] pending trigger {trig!r} dropped: {why}")
            else:
                self.pending_trigger_v9 = {"id": trig["id"], "v": trig["v"],
                                           "kv": dict(trig.get("kv") or {})}

    def _seed_lost_high_water(self):
        """Review S4a #3: after a lost file, old remote commands still sit in
        Sofar's never-expiring mailbox. Seed each high-water range from the
        journal's highest id, and close the service range outright."""
        import command_wire
        try:
            import config_journal
            entries = config_journal.read(config_journal.path_beside(self.path))
        except Exception:
            entries = []
        for e in entries:
            cid = e.get("id")
            if _is_int(cid):
                rng = command_wire.id_range(cid)
                if rng in command_wire.HIGH_WATER_RANGES and cid > self.high_water.get(rng, -1):
                    self.high_water[rng] = cid
        top = next(r[2] for r in command_wire.RANGES if r[0] == "service")
        self.high_water["service"] = top
        self._log(f"[CMD][WARN] v9 state lost: high-water re-seeded from the journal "
                  f"{self.high_water}; the service range is CLOSED until a field update")

    def _require_txn(self, what):
        if not self._in_txn:
            raise RuntimeError(f"V9State.{what} changes memory: call it inside transaction() "
                               "(D15)")

    @staticmethod
    def _trigger_problem(trig, validator):
        if not isinstance(trig, dict):
            return "not an object"
        if not _is_int(trig.get("id")) or not _is_int(trig.get("v")) or \
                trig["v"] not in (1, 2, 3, 4):
            return "bad id or v"
        kv = trig.get("kv") or {}
        if not isinstance(kv, dict):
            return "kv is not an object"
        if validator is not None:
            return validator(kv, trig["v"])
        return None

    # ------------------------------------------------------------------ save
    def _payload(self):
        body = dict(self.extra)
        body.update(schema=SCHEMA, boot_counter=self.boot_counter, overlay=self.overlay,
                    overlay_ids=self.overlay_ids, guarded=self.guarded,
                    result_cache=self.result_cache, high_water=self.high_water,
                    pending_trigger_v9=self.pending_trigger_v9, v8_folded=self.v8_folded,
                    v8_fold_values=self.v8_fold_values, v8=self.v8)
        return body

    def save(self):
        """Atomic write of the whole file. Raises on I/O failure."""
        if self._keep_aside and os.path.exists(self.path):
            import shutil
            shutil.copyfile(self.path, self.path + ".unreadable")
            self._keep_aside = False
        atomic_io.write_text(self.path, json.dumps(self._payload(), separators=(",", ":")))

    def _snapshot(self):
        return copy.deepcopy({k: getattr(self, k) for k in _OWNED + ("extra",)})

    def _restore(self, snap):
        for k, v in snap.items():
            setattr(self, k, v)

    def transaction(self, mutate):
        """Run mutate(self) in memory, persist, and undo the in-memory change if
        the persist raises (D15: no ok ack, and nothing changed). Re-raises."""
        if self._in_txn:                       # nested: the outer one persists
            return mutate(self)
        snap = self._snapshot()
        self._in_txn = True
        try:
            result = mutate(self)
            self.save()
        except Exception:
            self._restore(snap)
            raise
        finally:
            self._in_txn = False
        return result

    def journal(self, source, key, old, new, cid=None):
        try:
            import config_journal
            config_journal.append(config_journal.path_beside(self.path), source,
                                  key=key, old=old, new=new, cid=cid)
        except Exception as exc:          # the state IS saved; never fatal
            self._log(f"[CMD][WARN] config journal not written: {exc}")

    # --------------------------------------------------------------- overlay
    def apply_overlay(self, changes, cid=None):
        """Apply [(path, value or remove())] to the overlay IN MEMORY, inside
        the caller's transaction(). -> [(path, old, new)] that changed (the
        caller journals them after the persist)."""
        self._require_txn("apply_overlay")
        done = []
        for path, value in changes:
            old = self.overlay.get(path, _REMOVE)
            if value is _REMOVE:
                if path in self.overlay:
                    del self.overlay[path]
                    self.overlay_ids.pop(path, None)
                    done.append((path, old, None))
                continue
            if old is not _REMOVE and old == value and type(old) is type(value):
                continue
            self.overlay[path] = value
            if cid is not None:
                self.overlay_ids[path] = cid
            else:                              # local GUI / revert: not that id's value now
                self.overlay_ids.pop(path, None)
            done.append((path, None if old is _REMOVE else old, value))
        return done

    def commit(self, changes, cid=None, source="console"):
        """Apply [(path, value or remove())] to the overlay atomically, then
        journal one line per key that changed. -> [(path, old, new)]."""
        done = self.transaction(lambda st: st.apply_overlay(changes, cid))
        for path, old, new in done:
            self.journal(source, path, old, new, cid)
        return done

    # ------------------------------------------------------------ v8 fold (G2)
    def fold_v8(self, overlay_from_v8):
        """Copy the v8 settings into the overlay (G2). First fold: keys already
        in the overlay win. Re-fold (the v8 section changed since, e.g. a legacy
        rollback edited it): the CHANGED v8 keys win. An armed v8 trigger moves
        to pending_trigger_v9 once. -> [(path, old, new)] (journaled
        `migrate_v8`), or [] when nothing to do."""
        v8 = self.v8 or {}
        cur_hash = v8_settings_hash(v8)
        v8_trig = v8.get("pending_trigger")
        if self.v8_folded == cur_hash and not v8_trig:
            return []

        def mutate(st):
            new_overlay, fold, done = plan_fold(st.overlay, st.overlay_ids, v8, st.v8_folded,
                                                st.v8_fold_values, overlay_from_v8)
            st.overlay = new_overlay
            for path, _old, _new in done:
                st.overlay_ids.pop(path, None)
            st.v8_folded = cur_hash
            st.v8_fold_values = dict(fold)
            if isinstance(v8_trig, dict) and _is_int(v8_trig.get("id")) and \
                    v8_trig.get("value") in (1, 2, 3, 4):
                if st.pending_trigger_v9 is None:
                    st.pending_trigger_v9 = {"id": v8_trig["id"], "v": v8_trig["value"],
                                             "kv": {}}
                    done.append(("pending_trigger", None, st.pending_trigger_v9))
                else:
                    self._log(f"[CMD][WARN] v8 pending trigger {v8_trig} dropped: a v9 "
                              f"trigger {st.pending_trigger_v9} is already armed")
                    done.append(("pending_trigger", v8_trig, None))
            if v8_trig is not None:
                st.v8 = dict(st.v8, pending_trigger=None)
            return done
        done = self.transaction(mutate)
        for path, old, new in done:
            self.journal("migrate_v8", path, old, new)
        return done

    # --------------------------------------------------- dedupe v2 (G9, R16)
    def cached(self, cid):
        """The original answer to this id, or None."""
        return self.result_cache.get(str(cid))

    def remember(self, cid, answer):
        """Store the original answer (in memory; persisted by the caller's
        next transaction/save). Oldest evicted past RESULT_CACHE_MAX."""
        self._require_txn("remember")
        entry = {k: v for k, v in answer.items() if v is not None}
        entry["b"] = self.boot_counter
        self.result_cache.pop(str(cid), None)
        self.result_cache[str(cid)] = entry
        while len(self.result_cache) > RESULT_CACHE_MAX:
            self.result_cache.pop(next(iter(self.result_cache)))

    def is_old(self, rng, cid):
        """Below-or-at this range's high-water and not cached (R16 order: the
        caller checks cached() first)."""
        hw = self.high_water.get(rng)
        return hw is not None and cid <= hw

    def advance_high_water(self, rng, cid):
        self._require_txn("advance_high_water")
        if cid > self.high_water.get(rng, -1):
            self.high_water[rng] = cid

    # ---------------------------------------------------------- boot counter
    def count_boot(self):
        """+1 and persist (b.5 calls it before the UART opens). -> new value."""
        return self.transaction(lambda st: setattr(st, "boot_counter",
                                                   st.boot_counter + 1) or st.boot_counter)

    # ---------------------------------------------------------------- trigger
    @property
    def pending_trigger(self):
        """The armed one-shot in the runtime's {"id", "value", "kv"} shape. The
        SAME object until the trigger changes: the supervisor compares it by
        identity (rc_supervisor W10 w10_stuck, stay_on stuck_trg)."""
        t = self.pending_trigger_v9
        if t is None:
            return None
        if getattr(self, "_trig_src", None) is not t:
            self._trig_src = t
            self._trig_view = {"id": t["id"], "value": t["v"], "kv": dict(t["kv"])}
        return self._trig_view

    def arm_trigger(self, cid, v, kv=None):
        """trg v (v:0 cancels). Persisted by the caller's transaction."""
        self._require_txn("arm_trigger")
        self.pending_trigger_v9 = None if v == 0 else {"id": cid, "v": v, "kv": dict(kv or {})}

    def consume_trigger(self):
        """Take the pending one-shot, clearing it FIRST (persisted before the
        caller acts; a failed persist keeps it armed and returns None)."""
        trig = self.pending_trigger
        if trig is None:
            return None
        try:
            self.transaction(lambda st: setattr(st, "pending_trigger_v9", None))
        except Exception as exc:
            self._log(f"[CMD][ERROR] could not persist trigger consume: {exc}; "
                      "trigger NOT serviced this boot")
            return None
        return trig

    # ------------------------------------------------------------------ heals
    @property
    def pending_heals(self):
        return list(self.v8.get("pending_heals") or [])

    def set_pending_heals(self, heals):
        """rc_heal's end-of-wake update. Persists; raises on I/O failure."""
        good = [h for h in (_valid_heal(x) for x in heals) if h][:PENDING_HEALS_MAX]
        self.transaction(lambda st: setattr(st, "v8", dict(st.v8, pending_heals=good)))

    def record_heals(self, cid, value):
        """rsd (in memory; persisted by the caller's transaction): {"x":1}
        cancels all; {"h": [[key, ns], ...]} = the ACCEPTED heals, newest
        first, one per key, at most PENDING_HEALS_MAX (command_state rules)."""
        self._require_txn("record_heals")
        value = value or {}
        if value.get("x"):
            self.v8 = dict(self.v8, pending_heals=[])
            return
        new = [{"key": k, "n": sorted(set(ns)), "id": cid, "wakes_left": HEAL_WAKES}
               for k, ns in value.get("h", [])]
        keys = {h["key"] for h in new}
        merged = new + [h for h in self.pending_heals if h["key"] not in keys]
        for evicted in merged[PENDING_HEALS_MAX:]:
            self._log(f"[CMD][WARN] pending heal list full; oldest heal "
                      f"key={evicted['key']} id={evicted['id']} evicted")
        self.v8 = dict(self.v8, pending_heals=merged[:PENDING_HEALS_MAX])
