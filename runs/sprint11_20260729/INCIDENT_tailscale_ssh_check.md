# Incident — Tailscale SSH re-auth check blocks all unit access (2026-07-29T19:00Z)

**Impact:** no SSH to either bmcam unit, so the Sprint11 runtime deploy could
not start. Cost: the 19:00Z wake window on both units. **No unit was left
mid-surgery** — both are still in their Sprint10 test config, armed and
cycling normally. Nothing needs undoing.

---

## What happened

The catch-awake watcher was armed at 18:43Z for the 19:00Z power-up. Both
units powered on at 19:00:00Z and joined the bus (confirmed on both consoles:
`Neighbor 53171fa3d81a8e6f added`, `Neighbor 49cfe4d7cceb2771 added`). The
watcher printed nothing and caught neither unit.

Two separate faults, stacked.

### Fault 1 — Tailscale SSH is in check mode; every connection blocks

`ssh -vv` gets all the way through key exchange and then stops at:

```
debug1: SSH2_MSG_SERVICE_ACCEPT received
# Tailscale SSH requires an additional check.
# To authenticate, visit: https://login.tailscale.com/a/<id>
```

The connection then sits open until something kills it, waiting for a human to
visit that URL in a browser. It is not a key problem:
`-o PreferredAuthentications=publickey` hits the same wall, because Tailscale
SSH intercepts port 22 on the tailnet regardless of auth method. The tailnet
itself is healthy — `tailscale ping bmcam000` answers in 9 ms via a direct
path, and `nc -z 100.119.14.92 22` succeeds.

This is a periodic re-auth policy that came due today; SSH to these units
worked earlier in the day.

**Only Nick can clear it** — visiting an authentication URL is not something
this session does on his behalf.

### Fault 2 — the watcher hid the message, and one hung probe starved the other unit

Two bugs made fault 1 invisible and doubled its cost.

1. **The `sshq()` helper filters `grep -viE "tailscale|authenticate"`.** That
   filter exists to drop the Tailscale login banner from clean runs. It also
   drops `# Tailscale SSH requires an additional check.` and
   `To authenticate, visit: ...` — the two lines that explained everything.
   Carried over verbatim from Phase E, where it was harmless.

2. **`-o ConnectTimeout` does not bound an ssh invocation.** It covers the TCP
   connect only. Here the socket opened fine and the *handshake* stalled, so
   the probe hung indefinitely. The watcher polls both units in one sequential
   loop, so a hung probe to bmcam000 meant **bmcam003 was never polled at all**
   — and bmcam003 was reachable for a full 8 minutes that cycle
   (`tailscale status` showed it "last seen 1m ago" at 19:10Z).

   A watcher that prints nothing looks exactly like "no unit is up yet". It was
   in fact wedged inside a single `ssh ... true`.

---

## Fixes applied

- `runs/sprint11_20260729/sshto.sh` — `ssh_to <secs> <host> <cmd>` with a real
  wall-clock bound (background the ssh, arm a killer, reap whichever wins;
  macOS has no `timeout(1)`). Returns 124 on timeout.
- The watcher's output filter now drops only the login *banner* and never
  swallows a line containing `check`, `visit`, or `authenticate` as an
  instruction to the operator.
- The watcher probes units independently and logs every probe outcome, so a
  wedged unit can no longer starve the other and silence is no longer
  ambiguous.

## Lesson worth keeping

> A log filter that hides a class of message will eventually hide the one
> message that mattered. Filter the banner you have seen, never the category.

Second-order: a poll loop over N hosts must bound each probe, or it is a poll
loop over 1 host chosen by whichever fails worst.
