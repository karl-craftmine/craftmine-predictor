"""Low-level smoke test for the Flashscore feed host + x-fsign header.

The feed lives at global.flashscore.ninja (NOT d.flashscore.com, which always
returns "0"), and requires the x-fsign header or it 401s.
"""

from curl_cffi import requests

from footy.flashscore import NINJA, FSIGN

session = requests.Session(impersonate="chrome")
session.headers.update({"x-fsign": FSIGN})

# Today's soccer feed: a quick "is the pipeline reachable?" check.
endpoints = [
    NINJA + "f_1_0_3_en_1",       # today's matches
    NINJA + "f_1_-1_3_en_1",      # yesterday's results
]

for url in endpoints:
    print(f"Testing: {url}")
    try:
        response = session.get(url, timeout=15)
        body = response.text
        ok = body and body != "0"
        print(f"  Status: {response.status_code}  length: {len(body)}  "
              f"{'OK (data)' if ok else 'EMPTY/REJECTED'}")
        print(f"  First 120 chars: {body[:120]!r}")
    except Exception as e:
        print(f"  Error: {e}")
    print("---")
