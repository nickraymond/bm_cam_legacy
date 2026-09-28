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
  result_cache        {"<id>": {ok,h[,e,k,s,v],b,cb}}  last 256 answers (dedupe v2)
  high_water          {"remote": id, "service": id}
  pending_trigger_v9  {"id", "v", "kv"} or null (kv re-validated on load)
  v8_folded           hash of the v8 settings last folded into the overlay (G2)
  v8_fold_values      {path: value} that fold produced (re-fold precedence)
  v8                  the S2 v8 section, verbatim (+ shared pending_heals)
  (any other key)     kept verbatim

Writes: atomic_io (unique tmp, fsync, rename, fsync dir). A change is applied
in memory ONLY after its persist succeeded (D15): commit() snapshots, writes,
and restores the snapshot if the write raises. Journal lines are appended
after the persist (a journal failure is logged, never fatal).

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
        return copy.deepcopy({k: getattr(self, k) for k in _OWNED})

    def _restore(self, snap):
        for k, v in snap.items():
            setattr(self, k, v)

    def transaction(self, mutate):
        """Run mutate(self) in memory, persist, and undo the in-memory change if
        the persist raises (D15: no ok ack, and nothing changed). Re-raises."""
        snap = self._snapshot()
        try:
            result = mutate(self)
            self.save()
        except Exception:
            self._restore(snap)
            raise
        return result

    def journal(self, source, key, old, new, cid=None):
        try:
            import config_journal
            config_journal.append(config_journal.path_beside(self.path), source,
                                  key=key, old=old, new=new, cid=cid)
        except Exception as exc:          # the state IS saved; never fatal
            self._log(f"[CMD][WARN] config journal not written: {exc}")

    # --------------------------------------------------------------- overlay
    def commit(self, changes, cid=None, source="console"):
        """Apply [(path, value or remove())] to the overlay atomically, then
        journal one line per key that changed. -> [(path, old, new)]."""
        def mutate(st):
            done = []
            for path, value in changes:
                old = st.overlay.get(path, _REMOVE)
                if value is _REMOVE:
                    if path in st.overlay:
                        del st.overlay[path]
                        st.overlay_ids.pop(path, None)
                        done.append((path, old, None))
                    continue
                if old is not _REMOVE and old == value and type(old) is type(value):
                    continue
                st.overlay[path] = value
                if cid is not None:
                    st.overlay_ids[path] = cid
                done.append((path, None if old is _REMOVE else old, value))
            return done
        done = self.transaction(mutate)
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
        fold = overlay_from_v8(v8) if v8 else {}
        first = self.v8_folded is None

        def mutate(st):
            done = []
            for path, value in fold.items():
                if first:
                    win = path not in st.overlay
                else:
                    win = path not in st.v8_fold_values or st.v8_fold_values[path] != value
                if win and st.overlay.get(path, _REMOVE) != value:
                    done.append((path, st.overlay.get(path), value))
                    st.overlay[path] = value
                    st.overlay_ids.pop(path, None)
            st.v8_folded = cur_hash
            st.v8_fold_values = dict(fold)
            if isinstance(v8_trig, dict) and _is_int(v8_trig.get("id")) and \
                    v8_trig.get("value") in (1, 2, 3, 4) and st.pending_trigger_v9 is None:
                st.pending_trigger_v9 = {"id": v8_trig["id"], "v": v8_trig["value"], "kv": {}}
                done.append(("pending_trigger", None, st.pending_trigger_v9))
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
        """The armed one-shot in the runtime's {"id", "value", "kv"} shape."""
        t = self.pending_trigger_v9
        return None if t is None else {"id": t["id"], "value": t["v"], "kv": dict(t["kv"])}

    def arm_trigger(self, cid, v, kv=None):
        """trg v (v:0 cancels). Persisted by the caller's transaction."""
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
