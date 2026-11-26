#!/usr/bin/env bash
# Publish the component images that just passed the platform acceptance (deploy/compose/e2e.sh) to
# ghcr.io/idp-xyz/<image>:<component commit>, the commit releases/platform-bom.yaml pins. Each image is
# signed by digest with cosign (keyless: the workflow's GitHub OIDC identity, recorded in Rekor) and gets
# an SPDX SBOM (syft) attached as a signed attestation. A tag that already exists is left alone: an image
# published for a commit is never replaced by a rebuild. Writes published-images.json (reference, digest).
#   bash tools/publish_images.sh            (CI: needs docker login to ghcr.io, cosign, syft)
#   PUBLISH_SKIP="idp-embedding:dev"        leave these local images out (the T2 run, which has the embedding double and not the real service's image)
#   PUBLISH_ONLY="idp-embedding:dev"        publish only these (the T3 run, which is what proves the real embedding image); tests/ACCEPTANCE.md
set -euo pipefail
cd "$(dirname "$0")/.."
REGISTRY="${REGISTRY:-ghcr.io/idp-xyz}"
OUT="${OUT:-published-images.json}"
# local image built by e2e.sh -> component in the BOM -> published repository
IMAGES=("idp-cmi:dev idp-cmi idp-cmi" "idp-host:dev idp-host idp-host" "idp-workspace:dev idp-workspace idp-workspace" "eer-runtime:dev idp-eer eer-runtime" "idp-mcp:dev idp-mcp idp-mcp" "idp-sandbox:dev idp-sandbox idp-sandbox" "idp-web:dev idp-web idp-web" "idp-mcp-bridge:dev idp-mcp-bridge idp-mcp-bridge" "idp-browser:dev idp-browser idp-browser" "idp-browser-proxy:dev idp-browser idp-browser-proxy")
wanted() { [[ " ${PUBLISH_SKIP:-} " != *" $1 "* && ( -z "${PUBLISH_ONLY:-}" || " $PUBLISH_ONLY " == *" $1 "* ) ]]; }
commit_of() { python3 -I -c 'import sys,yaml; print(next(c["commit"] for c in yaml.safe_load(open("releases/platform-bom.yaml"))["components"] if c["name"]==sys.argv[1]))' "$1"; }
entries=()
# publish LOCAL-IMAGE REPOSITORY TAG: pushed (signed, SBOM attested) unless that tag is already there; sets ref and digest
publish() {
  local local="$1" repo="$2" tag="$3"
  ref="$REGISTRY/$repo:$tag"
  if docker buildx imagetools inspect "$ref" >/dev/null 2>&1; then
    digest="$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}')"
    echo "KEPT: $ref@$digest (published before)"
  else
    docker tag "$local" "$ref"
    # a new package may be denied for a moment after ghcr creates it: retry before giving up
    for attempt in 1 2 3 4; do docker push "$ref" && break; [[ $attempt == 4 ]] && exit 1; echo "push denied, retrying in $((attempt * 15))s"; sleep $((attempt * 15)); done
    digest="$(docker image inspect "$ref" --format '{{join .RepoDigests "\n"}}' | grep "^$REGISTRY/$repo@" | cut -d@ -f2)"
    cosign sign --yes "$REGISTRY/$repo@$digest" >/dev/null
    syft -q "$REGISTRY/$repo@$digest" -o spdx-json > "sbom-$repo.spdx.json"
    cosign attest --yes --type spdxjson --predicate "sbom-$repo.spdx.json" "$REGISTRY/$repo@$digest" >/dev/null
    echo "PUBLISHED: $ref@$digest (signed, SBOM attested)"
  fi
}
for row in "${IMAGES[@]}"; do
  read -r local component repo <<<"$row"
  wanted "$local" || continue
  commit="$(commit_of "$component")"
  [[ "$(git -C "$component" rev-parse HEAD)" == "$commit" ]] || { echo "FAIL: $component is not at the BOM's commit $commit" >&2; exit 1; }
  publish "$local" "$repo" "$commit"
  entries+=("{\"component\":\"$component\",\"commit\":\"$commit\",\"reference\":\"$ref\",\"digest\":\"$digest\"}")
done
# the images this repository builds itself (catalog/images.yaml, deploy/images/*): the tag is the git tree of their directory, which the BOM records
while read -r name path tree; do
  [[ -n "$name" ]] || continue
  wanted "$name:dev" || continue
  [[ "$(git rev-parse "HEAD:$path")" == "$tree" ]] || { echo "FAIL: $path is not at the BOM's tree $tree" >&2; exit 1; }
  publish "$name:dev" "$name" "$tree"
  entries+=("{\"component\":\"$name\",\"commit\":\"$tree\",\"reference\":\"$ref\",\"digest\":\"$digest\"}")
done < <(python3 -I -c 'import yaml; [print(i["name"], i["path"], i.get("tree", "")) for i in yaml.safe_load(open("releases/platform-bom.yaml")).get("images", [])]')
(IFS=,; echo "[${entries[*]}]") | python3 -m json.tool > "$OUT"
echo "OK: $OUT"
