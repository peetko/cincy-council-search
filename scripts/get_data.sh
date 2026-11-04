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
  # Before the first nightly update saves its data: use the website package the latest data
  # update built (a "github-pages" artifact from an "Update council data" run), not the live
  # site, which web caches can serve out of date for a few minutes after a publish.
  pages=$(python3 - "$repo" <<'PY'
import json, os, sys, urllib.request
repo = sys.argv[1]
def api(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}",
                                 headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"})
    return json.load(urllib.request.urlopen(req, timeout=60))
arts = [a for a in api("actions/artifacts?name=github-pages&per_page=100")["artifacts"] if not a["expired"]]
arts.sort(key=lambda a: a["created_at"], reverse=True)
for a in arts:
    run = api(f"actions/runs/{a['workflow_run']['id']}")
    if run["path"].endswith("update.yml") and run["conclusion"] == "success":
        print(a["id"]); break
PY
)
  if [ -n "$pages" ]; then
    echo "No saved data yet; using the website package from the latest data update (artifact $pages)."
    curl -fsSL -H "Authorization: Bearer $GH_TOKEN" -o /tmp/pages.zip "https://api.github.com/repos/$repo/actions/artifacts/$pages/zip"
    unzip -q -o /tmp/pages.zip -d /tmp/pages
    tar -xf /tmp/pages/artifact.tar -C site ./data
    rm -rf /tmp/pages /tmp/pages.zip
  else
    echo "No saved data yet; copying what the live site is serving."
    python scripts/fetch_data.py --live "$site"
  fi
fi
du -sh site/data
