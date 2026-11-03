"""A small, polite client for the public Legistar Web API.

The API speaks OData: you add ?$filter=…&$top=…&$skip=… to a URL and get JSON
back. It returns at most 1,000 rows per request, so `get_all` pages through.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from common import SETTINGS

# LEGISTAR_API_BASE lets the tests point this at a fake server.
API_BASE = os.environ.get("LEGISTAR_API_BASE", "https://webapi.legistar.com/v1").rstrip("/")
CLIENT = SETTINGS["legistar_client"]
PAUSE = float(os.environ.get("LEGISTAR_PAUSE", "0.15"))  # seconds between requests

request_count = 0


def get(path, params=None):
    global request_count
    url = f"{API_BASE}/{CLIENT}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="$'(),:")
    delay = 2.0
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "juncta-lucent (github.com)"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                request_count += 1
                time.sleep(PAUSE)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"Legistar API error {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"Could not reach Legistar API: {url}") from e


def get_all(path, params=None, page_size=1000):
    params = dict(params or {})
    skip = 0
    while True:
        page = get(path, {**params, "$top": page_size, "$skip": skip})
        yield from page
        if len(page) < page_size:
            return
        skip += page_size


def odata_date(d):
    """'2020-07-01' -> datetime'2020-07-01T00:00:00' (the API's date syntax)."""
    if len(d) == 10:
        d += "T00:00:00"
    return f"datetime'{d[:19]}'"


def events(filter_expr):
    return get_all("events", {"$filter": filter_expr, "$orderby": "EventDate"})


def event_items(event_id):
    return get(f"events/{event_id}/eventitems", {"AgendaNote": 1, "MinutesNote": 1, "Attachments": 0})


def votes(item_id):
    return get(f"eventitems/{item_id}/votes")
