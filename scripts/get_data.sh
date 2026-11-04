#!/usr/bin/env bash
# Put the site's latest data in site/data: the copy saved by the most recent data update
# (a GitHub "artifact" named site-data), or, if there isn't one, what the live site is serving.
#   scripts/get_data.sh OWNER/REPO SITE_URL      (needs GH_TOKEN)
set -euo pipefail
repo="$1"; site="$2"
mkdir -p site/data
id=$(curl -fsSL -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
       "https://api.github.com/repos/$repo/actions/artifacts?name=site-data&per_page=100" \
     | python3 -c "import json,sys; a=[x for x in json.load(sys.stdin)['artifacts'] if not x['expired']]; a.sort(key=lambda x: x['created_at'], reverse=True); print(a[0]['id'] if a else '')")
if [ -n "$id" ]; then
  echo "Using the data saved by the latest update (artifact $id)."
  curl -fsSL -H "Authorization: Bearer $GH_TOKEN" -o /tmp/site-data.zip "https://api.github.com/repos/$repo/actions/artifacts/$id/zip"
  unzip -q -o /tmp/site-data.zip -d site/data
  rm -f /tmp/site-data.zip
else
  echo "No saved data yet; copying what the live site is serving."
  python scripts/fetch_data.py --live "$site"
fi
du -sh site/data
