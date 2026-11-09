#!/bin/sh
# Minimal S3 client over curl --aws-sigv4, sourced by backup.sh and restore.sh.
# Environment: S3_ENDPOINT (https://host:port), S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION (default us-east-1),
# S3_CA_FILE (optional PEM for a private CA). Keys never appear in a process list: they go through a curl config on stdin.
S3_REGION="${S3_REGION:-us-east-1}"
EMPTY_SHA=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

_s3() { # _s3 METHOD KEY_OR_QUERY PAYLOAD_SHA [curl args...]
  _m="$1"; _p="$2"; _h="$3"; shift 3
  _ca=""; [ -n "${S3_CA_FILE:-}" ] && _ca="--cacert $S3_CA_FILE"
  printf 'user = "%s:%s"\n' "$S3_ACCESS_KEY" "$S3_SECRET_KEY" | curl -sS --fail-with-body $_ca --config - --aws-sigv4 "aws:amz:$S3_REGION:s3" \
    -H "x-amz-content-sha256: $_h" -X "$_m" "$@" "$S3_ENDPOINT/$S3_BUCKET$_p"
}

s3_put() { # s3_put FILE KEY
  _sha="$(sha256sum "$1" | cut -d' ' -f1)"
  _s3 PUT "/$2" "$_sha" -T "$1" -o /dev/null
}
s3_get() { # s3_get KEY FILE
  _s3 GET "/$1" "$EMPTY_SHA" -o "$2"
}
s3_delete() { # s3_delete KEY
  _s3 DELETE "/$1" "$EMPTY_SHA" -o /dev/null
}
s3_ensure_bucket() { # create the bucket if it is not there; fails unless it exists afterwards
  _s3 PUT "" "$EMPTY_SHA" -o /dev/null 2>/dev/null || true   # 409 when it already exists
  _s3 GET "?list-type=2&max-keys=1" "$EMPTY_SHA" -o /dev/null
}
# the listings capture the answer first: a pipeline would report the status of its last command and hide a failed request
s3_keys() { # s3_keys PREFIX: every key under the prefix, one per line (first 1000: a backup set has a handful)
  _o="$(_s3 GET "?list-type=2&prefix=$1" "$EMPTY_SHA")" || return 1
  printf '%s' "$_o" | sed 's|<Key>|\n<Key>|g' | sed -n 's|^<Key>\([^<]*\)</Key>.*|\1|p' | sort -u
}
s3_dirs() { # s3_dirs PREFIX: the immediate "directories" under the prefix (names only, no slash)
  _o="$(_s3 GET "?list-type=2&delimiter=/&prefix=$1" "$EMPTY_SHA")" || return 1
  printf '%s' "$_o" | sed 's|<Prefix>|\n<Prefix>|g' | sed -n "s|^<Prefix>$1\([^/<]*\)/</Prefix>.*|\1|p" | sort -u
}
