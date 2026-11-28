#!/usr/bin/env bash
set -euo pipefail

if [[ ! "${RELEASE_SHA:-}" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'Set RELEASE_SHA to the tested full commit SHA' >&2
  exit 1
fi

image_prefix="${IMAGE_PREFIX:-semantic-bi-dev}"
if [[ "$image_prefix" != semantic-bi-dev && "$image_prefix" != semantic-bi-prod ]]; then
  echo 'IMAGE_PREFIX must identify dev or prod' >&2
  exit 1
fi

images=()
for target in gateway auth analytics ontology agent mcp; do
  image="$image_prefix/$target:$RELEASE_SHA"
  docker build --target "$target" --tag "$image" .
  images+=("$image")
done
docker save "${images[@]}" | gzip -1 > images.tar.gz
