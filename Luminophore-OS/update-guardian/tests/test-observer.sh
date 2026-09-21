#!/bin/sh
set -eu

root=$(mktemp -d)
trap 'chmod -R u+w "$root" 2>/dev/null || :; rm -rf -- "$root"' EXIT HUP INT TERM
base=$(dirname "$0")/..
observer=${1:-$base/libexec/luminophore-update-observe}
follower=$base/desktop-follow.py
hook=$base/hooks/95-luminophore-update-observer.hook
db=$root/db
queue=$root/queue
manifest=$root/capabilities.json
release_state=$root/state.json
mkdir -p "$db/glibc-2.42-1" "$db/quickhack-1.0-1" "$queue"
printf '%%NAME%%\nglibc\n\n%%VERSION%%\n2.42-1\n' > "$db/glibc-2.42-1/desc"
printf '%%NAME%%\nquickhack\n\n%%VERSION%%\n1.0-1\n' > "$db/quickhack-1.0-1/desc"
printf '%s\n' '{"schema":"luminophore-desktop-capabilities/v1","packages":{"required":["glibc"],"private":[],"optional":{}},"capabilities":[]}' > "$manifest"
printf '%s\n' '{"selected":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' > "$release_state"

run_observer() {
    start=$(date +%s)
    printf '%s\n' "$1" | LUMINOPHORE_GUARDIAN_FOLLOWER="$follower" \
        LUMINOPHORE_GUARDIAN_DBPATH="$db" LUMINOPHORE_GUARDIAN_QUEUE="$2" \
        LUMINOPHORE_CAPABILITY_MANIFEST="$manifest" LUMINOPHORE_RELEASE_STATE="$release_state" \
        LUMINOPHORE_GUARDIAN_MAX_PENDING="${3:-16}" "$observer"
    [ "$?" -eq 0 ]
    [ "$(($(date +%s) - start))" -lt 2 ]
}

run_observer quickhack "$queue"
[ "$(find "$queue" -maxdepth 1 -name 'transaction-*.json' | wc -l)" -eq 0 ]

run_observer glibc "$queue"
record=$(find "$queue" -maxdepth 1 -name 'transaction-*.json')
[ -f "$record" ]
[ "$(stat -c %a "$record")" = 640 ]
grep -q '"package_set_digest"' "$record"
grep -q '"state":"observed"' "$record"
run_observer glibc "$queue"
[ "$(find "$queue" -maxdepth 1 -name 'transaction-*.json' | wc -l)" -eq 1 ]

printf '%s\n' glibc | LUMINOPHORE_GUARDIAN_FOLLOWER="$root/missing" "$observer"
run_observer glibc "$root/missing-queue"
full=$root/full
mkdir "$full"
: > "$full/transaction-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
run_observer glibc "$full" 1
[ "$(find "$full" -maxdepth 1 -name 'transaction-*.json' | wc -l)" -eq 1 ]

grep -q '^When = PostTransaction$' "$hook"
! grep -q 'AbortOnFail\|PreTransaction' "$hook"

printf 'observer tests: PASS\n'
