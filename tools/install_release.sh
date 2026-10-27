#!/usr/bin/env bash
# install_release.sh — install a pinned bm release on a unit, dependencies first, with no
# quiet fallback (SPEC_e4_noaa_release rev 13 M13-2 + rev 13.1 c + rev 13.3 N1–N3).
#
# Repo path: tools/install_release.sh
# Run ON the Pi over SSH, from the staged copy (the unit's checkout is still the old sha):
#   bash /tmp/release/install_release.sh --tag bmcam-rel-20261027 \
#        --manifest /tmp/release/release_manifest.json --debs /tmp/release/debs
# Staged beside it by the runbook's scp step: release_manifest.py (this repo's
# tools/release_manifest.py), release_manifest.json (releases/<tag>/), debs/ (the
# target's offline .deb set from the data root; never in git).
#
# Steps (any FAIL exits non-zero; every FAIL before step 4 leaves the runtime untouched):
#   1 record    OS, arch, Python, repo sha, software_sha, config file hashes (step 0).
#               An OS / arch / Python the manifest has no target for -> STOP, ask Nick (F13).
#   2 deps      each dependency: present and good -> left alone; missing -> installed from
#               the offline .deb set (sha256-checked, no network, no apt-get update; only
#               packages the unit lacks; an apt simulation first, any removal = FAIL).
#   3 check     cjxl >= the manifest's minimum, the runtime's Python imports (numpy, yaml,
#               PIL, serial), rpicam-still, a 1-image cjxl encode. Any miss -> FAIL listing
#               every miss, before any runtime change.
#   3a tree     the checkout is clean (a tree of only 0-byte tracked files from a power cut
#               is repaired, as RUNBOOK U3 / #188; anything else -> STOP, ask Nick).
#   4 install   detached checkout of the tag's sha (its manifest must equal the staged one),
#               rc_field_update.sh --skip-repo-sync --leave-disarmed --target-config <profile>,
#               v1 -> v2 migration (config_migrate_v1_v2.py), then the target camera_config.yaml
#               (the replaced one is kept beside it). Skipped when the unit already runs it.
#   5 post      sha = tag, software_sha, runtime file hashes, --print-config hash = pin with
#               0 overlay keys, commands.runtime supervisor (rev 12.4 method), clean tree,
#               no 0-byte runtime file, the step 3 checks again.
#   6 result    one line: [INSTALL] PASS … or [INSTALL] FAIL step N: ….
# Idempotent: a re-run after a PASS installs nothing and skips step 4.
# The unit is left DISARMED (rc_field_update.sh --leave-disarmed): re-arm with the crontab
# file the catch saved (RUNBOOK A1), never the one rc_field_update.sh prints.
#
# Exit codes: 0 PASS · 1 FAIL · 2 bad usage · 3 STOP, ask Nick (F13 OS, a dirty tree).
# Log: <log-dir>/install_release_<UTC>.log (default /home/pi/release_logs).
# Test hooks: BMCAM_PYTHON, BMCAM_OS_RELEASE, BMCAM_SUDO, BMCAM_FIELD_UPDATE, BMCAM_DPKG_TIMEOUT_S.
# Known limitations: arm64 + trixie only (rev 13.3 N1: bookworm's cjxl is 0.7.0); the
# print-config diff the deploy prints is accepted and logged, and the step 5 hash is the gate;
# rpicam-apps is checked, not installed (not in the offline set).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG=""
MANIFEST=""
DEBS=""
REPO="/home/pi/repos/bm_cam_legacy"
DST="/home/pi/BM_Devel_Pi"
PROFILE_V1="bmcam002"            # only feeds rc_field_update.sh stage 4 (RUNBOOK U4)
LOG_DIR="/home/pi/release_logs"
PY="${BMCAM_PYTHON:-/usr/bin/python3}"
OS_RELEASE="${BMCAM_OS_RELEASE:-/etc/os-release}"
SUDO="${BMCAM_SUDO-sudo -n}"
DPKG_TIMEOUT_S="${BMCAM_DPKG_TIMEOUT_S:-600}"

usage() { sed -n '2,40p' "$0"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --debs) DEBS="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --dst) DST="$2"; shift 2 ;;
    --profile-v1) PROFILE_V1="$2"; shift 2 ;;
    --log-dir) LOG_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[INSTALL][ERROR] unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$TAG" && -n "$MANIFEST" && -n "$DEBS" ]] \
  || { echo "[INSTALL][ERROR] --tag, --manifest and --debs are required" >&2; exit 2; }
MTOOL="$SCRIPT_DIR/release_manifest.py"
[[ -f "$MTOOL" ]] || { echo "[INSTALL][ERROR] $MTOOL missing (stage it beside this script)" >&2; exit 2; }
[[ -f "$MANIFEST" ]] || { echo "[INSTALL][ERROR] manifest not found: $MANIFEST" >&2; exit 2; }

TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$LOG_DIR" || { echo "[INSTALL][ERROR] cannot create $LOG_DIR" >&2; exit 2; }
LOG="$LOG_DIR/install_release_${TS}.log"
exec > >(tee -a "$LOG") 2>&1
WORK="$(mktemp -d "${TMPDIR:-/tmp}/install_release.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

STEP=0
log() { echo "[INSTALL] $*"; }
result_fail() { echo "[INSTALL] FAIL step $STEP: $*"; echo "[INSTALL] log: $LOG"; exit 1; }
result_stop() { echo "[INSTALL] STOP step $STEP (ask Nick): $*"; echo "[INSTALL] log: $LOG"; exit 3; }
m() { "$PY" "$MTOOL" "$@" --manifest "$MANIFEST"; }
mget() { "$PY" "$MTOOL" get --manifest "$MANIFEST" "$1"; }
sha256_of() { "$PY" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"; }
installed_version() {   # -> version if the package is installed, else empty
  dpkg-query -W -f='${db:Status-Status} ${Version}\n' "$1" 2>/dev/null | awk '$1=="installed"{print $2}'
}

log "release $TAG · manifest $MANIFEST · debs $DEBS · repo $REPO · runtime $DST"
log "log $LOG · $(date -u +%FT%TZ) · host $(hostname 2>/dev/null || echo unknown)"
[[ "$(mget tag)" == "$TAG" ]] || { STEP=1; result_fail "manifest is for tag $(mget tag), not $TAG"; }

# ---- 1. record (step 0) + OS gate (F13) -------------------------------------
STEP=1
log "=== step 1: record ==="
OS_LINE="$(. "$OS_RELEASE" 2>/dev/null; echo "ID=${ID:-?} VERSION_CODENAME=${VERSION_CODENAME:-?} VERSION_ID=${VERSION_ID:-?}")"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo unknown)"
PYV="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
OLD_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo none)"
SW_SHA="$(cat "$DST/software_sha.txt" 2>/dev/null || echo none)"
log "os: $OS_LINE arch=$ARCH python=$PYV ($PY)"
log "repo sha: $OLD_SHA · software_sha.txt: $SW_SHA"
for f in camera_schedule.yaml camera_config.yaml; do
  if [[ -f "$DST/$f" ]]; then log "$f sha256: $(sha256_of "$DST/$f")"; else log "$f: absent"; fi
done
TARGET_ID="$("$PY" "$MTOOL" target --manifest "$MANIFEST" --os-release "$OS_RELEASE" --arch "$ARCH" --python "$PYV")" \
  || result_stop "F13: this OS / arch / Python is not one the release supports (above); nothing changed"
log "target: $TARGET_ID"
if pgrep -f 'rc_progressive_jpeg.py|rc_run_capture_cycle.sh|main_pi_camera.py' >/dev/null 2>&1; then
  result_fail "a camera cycle is running; wait for it (or catch the next wake). Nothing changed"
fi

# ---- 2 / 3. dependencies: check, install what is missing, check again -------
CHECK_MISSES=()
check_deps() {          # fills CHECK_MISSES and NEED_PKGS; prints one line per dependency
  CHECK_MISSES=(); NEED_PKGS=()
  local mod pkg tool line ok ver
  while IFS=$'\t' read -r mod pkg; do
    if "$PY" -c "import $mod" >/dev/null 2>&1; then log "  python import $mod: OK"
    else log "  python import $mod: MISSING"; CHECK_MISSES+=("python import $mod ($pkg)"); NEED_PKGS+=("$pkg"); fi
  done < <("$PY" -c "import json,sys; [print(k+'\t'+v) for k,v in json.load(open(sys.argv[1]))['checks']['python_imports'].items()]" "$MANIFEST")
  if command -v cjxl >/dev/null 2>&1; then
    line="$(cjxl --version 2>/dev/null | head -1)"
    if ver="$("$PY" "$MTOOL" cjxl-ok --manifest "$MANIFEST" "$line")"; then log "  cjxl $ver: OK (>= $(mget checks.cjxl_min))"
    else log "  cjxl $ver: BELOW $(mget checks.cjxl_min)"; CHECK_MISSES+=("cjxl $ver < $(mget checks.cjxl_min) (libjxl-tools)"); NEED_PKGS+=("libjxl-tools"); fi
  else log "  cjxl: MISSING"; CHECK_MISSES+=("cjxl (libjxl-tools)"); NEED_PKGS+=("libjxl-tools"); fi
  if command -v rpicam-still >/dev/null 2>&1; then log "  rpicam-still: OK"
  else log "  rpicam-still: MISSING (rpicam-apps, not in the offline set)"; CHECK_MISSES+=("rpicam-still (rpicam-apps)"); fi
}
self_test() {           # one tiny image through cjxl (rev 13 M13-2 step 3)
  "$PY" -c "
import sys
pixels = bytes(v for y in range(16) for x in range(16) for v in (x * 16, y * 16, 128))
open(sys.argv[1], 'wb').write(b'P6 16 16 255\n' + pixels)" "$WORK/selftest.ppm" 2>/dev/null \
    && cjxl "$WORK/selftest.ppm" "$WORK/selftest.jxl" -d 1 -e 1 >"$WORK/selftest.out" 2>&1 \
    && [[ -s "$WORK/selftest.jxl" ]]
}

STEP=2
log "=== step 2: dependencies (check-or-install, offline) ==="
check_deps
NEED_UNIQ=()          # bash 3.2-safe (the Mac test host): no mapfile, no associative arrays
while read -r p; do [[ -n "$p" ]] && NEED_UNIQ+=("$p"); done \
  < <(printf '%s\n' "${NEED_PKGS[@]+"${NEED_PKGS[@]}"}" | awk 'NF && !s[$0]++')
if [[ ${#NEED_UNIQ[@]} -eq 0 ]]; then
  log "every dependency present: nothing installed"
else
  log "missing: ${NEED_UNIQ[*]}; installing from the offline set ($DEBS)"
  ROWS="$("$PY" "$MTOOL" debs --manifest "$MANIFEST" --target-id "$TARGET_ID" --dir "$DEBS")" \
    || result_fail "F12: the offline .deb set does not match the manifest (above); nothing installed"
  SEL=()
  OURS=""
  while IFS=$'\t' read -r pkg ver path; do
    OURS="$OURS$pkg"$'\n'
    have="$(installed_version "$pkg")"
    if [[ -z "$have" ]]; then SEL+=("$path"); continue; fi
    for want in "${NEED_UNIQ[@]}"; do
      [[ "$pkg" == "$want" ]] || continue
      # installed but its check failed: reinstall ours, never a downgrade
      if dpkg --compare-versions "$have" gt "$ver"; then
        result_fail "$pkg $have is installed and newer than the release's $ver, but its check failed; nothing installed"
      fi
      SEL+=("$path")
    done
  done <<<"$ROWS"
  for want in "${NEED_UNIQ[@]}"; do
    printf '%s' "$OURS" | grep -qxF "$want" \
      || result_fail "$want is needed but not in the offline set for $TARGET_ID; nothing installed"
  done
  APT_OPTS=(--no-download -o Dir::Etc::SourceList=/dev/null -o Dir::Etc::SourceParts=/dev/null
            -o APT::Get::List-Cleanup=0)
  log "apt simulation (${#SEL[@]} debs, no network)"
  SIM="$(apt-get install -s "${APT_OPTS[@]}" "${SEL[@]}" 2>&1)"; SIM_RC=$?
  printf '%s\n' "$SIM" | sed 's/^/  | /'
  [[ $SIM_RC -eq 0 ]] || result_fail "F12: apt simulation failed (rc $SIM_RC, above); nothing installed"
  if printf '%s\n' "$SIM" | grep -q '^Remv '; then
    result_fail "the install would REMOVE packages (Remv lines above); nothing installed"
  fi
  TO=(); command -v timeout >/dev/null 2>&1 && TO=(timeout "$DPKG_TIMEOUT_S")
  log "installing (${TO[*]:-no time box: timeout not found})"
  ${TO[@]+"${TO[@]}"} $SUDO apt-get install -y "${APT_OPTS[@]}" "${SEL[@]}"; RC=$?
  sync
  [[ $RC -eq 0 ]] || result_fail "F12: apt-get install failed (rc $RC); runtime untouched. Fix (sudo dpkg --configure -a) and re-run"
  log "installed ${#SEL[@]} package(s)"
fi

STEP=3
log "=== step 3: dependency check ==="
check_deps
if [[ ${#CHECK_MISSES[@]} -eq 0 ]]; then
  if self_test; then log "  cjxl self-test encode: OK ($(wc -c <"$WORK/selftest.jxl") B)"
  else CHECK_MISSES+=("cjxl self-test encode ($(tail -1 "$WORK/selftest.out" 2>/dev/null))"); fi
fi
if [[ ${#CHECK_MISSES[@]} -gt 0 ]]; then
  for miss in "${CHECK_MISSES[@]}"; do echo "[INSTALL][MISSING] $miss"; done
  result_fail "${#CHECK_MISSES[@]} dependency check(s) failed (listed above); runtime untouched"
fi

# ---- 3a. clean tree (rev 12.8 must do 3a; rev 13.3 N3) ------------------------
clean_tree() {          # 0 clean / repaired, 3 = a real local change (STOP)
  local dirty
  dirty="$(git -C "$REPO" status --porcelain 2>/dev/null)"
  [[ -z "$dirty" ]] && { log "tree clean"; return 0; }
  printf '%s\n' "$dirty" | sed 's/^/  | /'
  local keep
  keep="$(printf '%s\n' "$dirty" | while read -r st f; do
            if [[ "$st" == M && ! -s "$REPO/$f" ]]; then :; else echo "$st $f"; fi; done)"
  [[ -n "$keep" ]] && return 3
  log "every dirty file is a 0-byte tracked file (power cut, F11): repairing"
  git -C "$REPO" fsck --no-dangling >/dev/null && git -C "$REPO" checkout -- . && sync || return 3
  [[ -z "$(git -C "$REPO" status --porcelain)" ]] || return 3
  log "tree repaired and clean"
}
STEP=3
log "=== step 3a: clean tree before the update ==="
clean_tree || result_stop "the checkout has local changes that are not power-cut damage (above); nothing changed"

# ---- 4. install the release ---------------------------------------------------
STEP=4
log "=== step 4: install $TAG ==="
PROFILE_REL="$(mget profile.file)"
PROFILE_SHA="$(mget profile.sha256)"
PIN="$(mget profile.config_hash)"
MANIFEST_SHA="$(sha256_of "$MANIFEST")"
TAG_SHA="$(git -C "$REPO" rev-parse -q --verify "refs/tags/$TAG^{commit}" 2>/dev/null || true)"
if [[ -z "$TAG_SHA" ]]; then
  log "fetching tags from origin"
  git -C "$REPO" fetch origin "refs/tags/$TAG:refs/tags/$TAG" || result_fail "git fetch of $TAG failed (network?); nothing changed"
  TAG_SHA="$(git -C "$REPO" rev-parse -q --verify "refs/tags/$TAG^{commit}" 2>/dev/null || true)"
fi
[[ -n "$TAG_SHA" ]] || result_fail "tag $TAG not found in $REPO; nothing changed"
log "tag $TAG -> $TAG_SHA"
git -C "$REPO" show "$TAG_SHA:releases/$TAG/release_manifest.json" >"$WORK/tag_manifest.json" 2>/dev/null \
  || result_fail "the tag has no releases/$TAG/release_manifest.json; nothing changed"
[[ "$(sha256_of "$WORK/tag_manifest.json")" == "$MANIFEST_SHA" ]] \
  || result_fail "the staged manifest differs from the tag's copy (sha256); nothing changed"
log "staged manifest = the tag's copy (sha256 ${MANIFEST_SHA:0:12})"

already() {
  [[ "$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" == "$TAG_SHA" ]] \
    && [[ "$(cat "$DST/software_sha.txt" 2>/dev/null)" == "${TAG_SHA:0:12}" ]] \
    && [[ -f "$DST/camera_config.yaml" && "$(sha256_of "$DST/camera_config.yaml")" == "$PROFILE_SHA" ]] \
    && "$PY" "$MTOOL" runtime --manifest "$MANIFEST" --dst "$DST" >/dev/null 2>&1
}
if already; then
  log "the unit already runs $TAG (sha, software_sha, runtime files, profile): step 4 skipped"
else
  log "checkout --detach $TAG_SHA (was $OLD_SHA)"
  git -C "$REPO" checkout -q --detach "$TAG_SHA" && sync || result_fail "checkout failed; runtime untouched. Back: git -C $REPO checkout --detach $OLD_SHA"
  clean_tree || result_stop "the tree is not clean after the checkout (above)"
  FIELD_UPDATE="${BMCAM_FIELD_UPDATE:-$REPO/tools/rc_field_update.sh}"
  log "rc_field_update.sh (--skip-repo-sync --leave-disarmed --target-config $PROFILE_REL)"
  bash "$FIELD_UPDATE" --repo "$REPO" --skip-repo-sync --profile "$PROFILE_V1" --leave-disarmed \
       --accept-print-config-diff --target-config "$REPO/$PROFILE_REL" \
    || result_fail "rc_field_update.sh failed (above: its runtime backup + restore command); rollback per RUNBOOK R"
  sync
  if [[ ! -f "$DST/camera_config.yaml" ]]; then
    log "v1 -> v2 migration (dry run, then --write)"
    "$PY" "$REPO/tools/config_migrate_v1_v2.py" --app "$DST" || result_fail "migration dry run refused (above); rollback per RUNBOOK R"
    "$PY" "$REPO/tools/config_migrate_v1_v2.py" --app "$DST" --write || result_fail "migration --write failed (above); rollback per RUNBOOK R"
  fi
  if [[ "$(sha256_of "$DST/camera_config.yaml")" != "$PROFILE_SHA" ]]; then
    cp -p "$DST/camera_config.yaml" "$DST/camera_config.yaml.before_${TAG}_${TS}"
    cp "$REPO/$PROFILE_REL" "$DST/camera_config.yaml" && sync \
      || result_fail "installing the target camera_config.yaml failed; rollback per RUNBOOK R"
    log "target profile installed (previous: camera_config.yaml.before_${TAG}_${TS})"
  fi
fi

# ---- 5. post-checks -------------------------------------------------------------
STEP=5
log "=== step 5: post-checks ==="
POST=()
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$TAG_SHA" ]] && log "  sha = tag: OK" || POST+=("repo HEAD is not $TAG_SHA")
[[ "$(cat "$DST/software_sha.txt" 2>/dev/null)" == "${TAG_SHA:0:12}" ]] && log "  software_sha.txt: OK" \
  || POST+=("software_sha.txt is $(cat "$DST/software_sha.txt" 2>/dev/null), not ${TAG_SHA:0:12}")
"$PY" "$MTOOL" runtime --manifest "$MANIFEST" --dst "$DST" || POST+=("runtime files differ from the manifest")
[[ "$(sha256_of "$DST/camera_config.yaml")" == "$PROFILE_SHA" ]] && log "  camera_config.yaml sha256: OK" \
  || POST+=("camera_config.yaml is not the release profile")
PC="$(cd "$DST" && "$PY" rc_progressive_jpeg.py --print-config --config-path "$DST/camera_schedule.yaml" 2>&1)"
printf '%s\n' "$PC" | grep -E 'hash=|schedule:|crop_xywh|\[CFG\]\[ERR\]' | sed 's/^/  | /'
printf '%s\n' "$PC" | grep -q "hash=$PIN" && printf '%s\n' "$PC" | grep -q "overlay=0 key" \
  && log "  --print-config hash=$PIN, 0 overlay keys: OK" || POST+=("--print-config is not hash=$PIN with 0 overlay keys")
printf '%s\n' "$PC" | grep -q '\[CFG\]\[ERR\]' && POST+=("--print-config printed [CFG][ERR] lines")
RT="$(cd "$DST" && "$PY" -c "import config_v2 as C; c = C.load_config('camera_config.yaml'); print(c.base['commands.runtime'], C.config_hash(c.base))" 2>&1)"
[[ "$RT" == "supervisor $PIN" ]] && log "  [RUNTIME] supervisor (camera_config.yaml base, rev 12.4): OK" \
  || POST+=("runtime/hash from camera_config.yaml is '$RT', not 'supervisor $PIN'")
[[ -z "$(git -C "$REPO" status --porcelain)" ]] && log "  clean tree: OK" || POST+=("the checkout is dirty")
ZERO="$(find "$DST" -path "$DST/images" -prune -o -type f -size 0 -print 2>/dev/null | head -5)"
[[ -z "$ZERO" ]] && log "  no 0-byte runtime file: OK" || POST+=("0-byte files in $DST: $ZERO")
check_deps
POST+=(${CHECK_MISSES[@]+"${CHECK_MISSES[@]}"})
self_test && log "  cjxl self-test encode: OK" || POST+=("cjxl self-test encode")
if [[ ${#POST[@]} -gt 0 ]]; then
  for miss in "${POST[@]}"; do echo "[INSTALL][POST] $miss"; done
  result_fail "${#POST[@]} post-check(s) failed (listed above); rollback per RUNBOOK R"
fi

# ---- 6. result --------------------------------------------------------------------
STEP=6
CJXL_V="$("$PY" "$MTOOL" cjxl-ok --manifest "$MANIFEST" "$(cjxl --version 2>/dev/null | head -1)")"
NUMPY_V="$("$PY" -c 'import numpy; print(numpy.__version__)' 2>/dev/null)"
log "unit left DISARMED: re-arm with the crontab file the catch saved (RUNBOOK A1)"
echo "[INSTALL] PASS tag=$TAG sha=${TAG_SHA:0:12} cfg=$PIN runtime=supervisor target=$TARGET_ID cjxl=$CJXL_V numpy=$NUMPY_V log=$LOG"
exit 0
