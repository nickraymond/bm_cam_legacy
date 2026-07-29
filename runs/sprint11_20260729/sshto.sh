#!/usr/bin/env bash
# filename: sshto.sh
# description: ssh with a HARD wall-clock timeout. Sourced by the Sprint11 bench scripts.
#
# WHY THIS EXISTS (bench incident, 2026-07-29T19:00Z — cost one 30-min cycle)
# `ssh -o ConnectTimeout=4` does NOT bound an ssh invocation. ConnectTimeout
# covers the TCP connect only; once the socket is open, a stalled handshake
# hangs forever. A halting Pi does exactly that: it accepts the connection and
# then dies mid-auth.
#
# The Sprint11 catch-awake watcher polls two units in one loop. A hung probe to
# bmcam000 blocked the loop entirely, so bmcam003 — which was reachable for a
# full 8 minutes that cycle — was never polled at all. Nothing in the log said
# so; the watcher just sat there printing nothing, which looks identical to
# "no unit is up yet".
#
# macOS has no `timeout(1)` (and bash 3.2 has no associative arrays), so the
# bound has to be built by hand: run ssh in the background, arm a killer, and
# reap whichever finishes first.
#
# Usage:  source sshto.sh
#         ssh_to <seconds> <host> <remote command>     # exit 124 on timeout
#         scp_to <seconds> <local> <remote>

ssh_to() {
  local secs="$1" host="$2"; shift 2
  ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no \
      -o ServerAliveInterval=5 -o ServerAliveCountMax=2 \
      -n "pi@$host" "$@" &
  local pid=$!
  ( sleep "$secs"; kill -9 "$pid" 2>/dev/null ) &
  local killer=$!
  wait "$pid" 2>/dev/null
  local rc=$?
  kill "$killer" 2>/dev/null
  wait "$killer" 2>/dev/null
  # 137 = SIGKILL from the killer; report it as the conventional timeout code.
  [ "$rc" -eq 137 ] && return 124
  return "$rc"
}

scp_to() {
  local secs="$1" src="$2" dst="$3"
  scp -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no \
      -o ServerAliveInterval=5 -o ServerAliveCountMax=2 "$src" "$dst" &
  local pid=$!
  ( sleep "$secs"; kill -9 "$pid" 2>/dev/null ) &
  local killer=$!
  wait "$pid" 2>/dev/null
  local rc=$?
  kill "$killer" 2>/dev/null
  wait "$killer" 2>/dev/null
  [ "$rc" -eq 137 ] && return 124
  return "$rc"
}
