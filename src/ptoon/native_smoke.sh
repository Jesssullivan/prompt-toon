#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: native_smoke.sh PTOON OUTPUT_JSON" >&2
  exit 64
fi

binary=$1
output=$2
if [ ! -f "$binary" ] || [ ! -x "$binary" ] || [ -L "$binary" ]; then
  echo "native smoke requires one regular executable ptoon binary" >&2
  exit 65
fi
if [ -e "$output" ]; then
  echo "native smoke output already exists: $output" >&2
  exit 66
fi

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
# shellcheck source=src/ptoon/native_smoke_expected.sh
. "$script_dir/native_smoke_expected.sh"

tmpdir=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/ptoon-native-smoke.XXXXXX")
cleanup() {
  status=$?
  trap - 0 1 2 15
  /bin/rm -rf "$tmpdir"
  exit "$status"
}
trap cleanup 0 1 2 15

sha256_file() {
  file=$1
  set -- $(/usr/bin/shasum -a 256 "$file")
  if [ "$#" -lt 1 ]; then
    echo "could not hash $file" >&2
    exit 67
  fi
  printf '%s' "$1"
}

require_hash() {
  file=$1
  expected=$2
  description=$3
  actual=$(sha256_file "$file")
  if [ "$actual" != "$expected" ]; then
    echo "$description hash mismatch: expected $expected, got $actual" >&2
    exit 68
  fi
}

export HWLOC_SYNTHETIC="core:2 pu:1"
export CHPL_RT_NUM_THREADS_PER_LOCALE=2
export QT_NUM_SHEPHERDS=1
export QT_NUM_WORKERS_PER_SHEPHERD=2

"$binary" caps >"$tmpdir/caps" 2>"$tmpdir/caps.stderr"
if [ -s "$tmpdir/caps.stderr" ]; then
  echo "native smoke caps wrote stderr" >&2
  /bin/cat "$tmpdir/caps.stderr" >&2
  exit 69
fi
printf '%s' '{"engine":"chapel","utf8proc":true,"unicode_version":"15.1.0","patterns":9,"serve_protocol":1,"features":["normalize","redact","defang","redact-batch","condense-batch","condense","serve"]}' >"$tmpdir/caps.expected"
/usr/bin/cmp -s "$tmpdir/caps" "$tmpdir/caps.expected" || {
  echo "native smoke caps bytes differ from the complete expected contract" >&2
  exit 70
}
require_hash "$tmpdir/caps" "$CAPS_SHA256" "caps"

printf '\357\274\241\n' | "$binary" normalize >"$tmpdir/normalize"
printf 'A\n' >"$tmpdir/normalize.expected"
/usr/bin/cmp -s "$tmpdir/normalize" "$tmpdir/normalize.expected" || {
  echo "native smoke Unicode normalization mismatch" >&2
  exit 72
}
require_hash "$tmpdir/normalize" "$NORMALIZE_SHA256" "normalize"

redaction_canary=ghp_ABCDEFGHIJKLMNOP123456
printf '%s\n' "$redaction_canary" | "$binary" redact >"$tmpdir/redact"
if /usr/bin/grep -F "$redaction_canary" "$tmpdir/redact" >/dev/null 2>&1; then
  echo "native smoke redaction canary survived" >&2
  exit 73
fi
require_hash "$tmpdir/redact" "$REDACTION_SHA256" "redaction"

printf '5\nsmoke12\nGENERATED_AT3\n0.221\nuntrusted_tool_output2\n{}1\n8\nsmoke.md21\nuntrusted_tool_output26\nMUST preserve provenance.\n' >"$tmpdir/payload"
payload_size=$(/usr/bin/wc -c <"$tmpdir/payload")
if [ "$payload_size" != "120" ]; then
  echo "native smoke payload framing drifted: $payload_size bytes" >&2
  exit 74
fi

"$binary" condense 2000000 60000 2 <"$tmpdir/payload" >"$tmpdir/one-shot"
require_hash "$tmpdir/one-shot" "$ONE_SHOT_SHA256" "one-shot condensation"
one_shot_size=$(/usr/bin/wc -c <"$tmpdir/one-shot")

{
  printf '5\nsmoke6\nstream2000000\n60000\n2\n%s\n' "$payload_size"
  /bin/cat "$tmpdir/payload"
} >"$tmpdir/request"
{
  printf '5\nsmoke6\nstream2\nok%s\n' "$one_shot_size"
  /bin/cat "$tmpdir/one-shot"
} >"$tmpdir/resident.expected"

"$binary" serve 1 2 4 4194304 4096 2000000 60000 2 \
  <"$tmpdir/request" >"$tmpdir/resident" 2>"$tmpdir/resident.stderr"
if [ -s "$tmpdir/resident.stderr" ]; then
  echo "native smoke resident service wrote stderr" >&2
  /bin/cat "$tmpdir/resident.stderr" >&2
  exit 75
fi
/usr/bin/cmp -s "$tmpdir/resident" "$tmpdir/resident.expected" || {
  echo "native smoke resident framing differs from one-shot output" >&2
  exit 76
}
require_hash \
  "$tmpdir/resident" \
  "$RESIDENT_ROUND_TRIP_SHA256" \
  "resident round trip"

artifact_sha256=$(sha256_file "$binary")
printf '{"artifact_sha256":"sha256:%s","caps_engine":"chapel","caps_serve_protocol":1,"caps_sha256":"%s","kind":"prompt-toon-darwin-native-smoke","normalize_sha256":"%s","one_shot_sha256":"%s","redaction_canary_absent":true,"redaction_sha256":"%s","resident_round_trip_sha256":"%s","schema_version":1}\n' \
  "$artifact_sha256" \
  "$CAPS_SHA256" \
  "$NORMALIZE_SHA256" \
  "$ONE_SHOT_SHA256" \
  "$REDACTION_SHA256" \
  "$RESIDENT_ROUND_TRIP_SHA256" >"$output"
