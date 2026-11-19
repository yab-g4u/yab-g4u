#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub GraphQL API.

Needs GH_TOKEN (the workflow passes secrets.GH_STATS_TOKEN, else github.token).
Schema written (render_cards.py also accepts a raw contributionCalendar dump):
  {"login", "generated", "total",
   "days":  [{"date": "YYYY-MM-DD", "count": n}, ...],
   "repos": [{"name", "description", "stars", "forks", "language", "url"}, ...]}
"""
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

LOGIN = os.environ.get("PROFILE_LOGIN", "yab-g4u")
OUT = Path(__file__).resolve().parent.parent / "data" / "contributions.json"
QUERY = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from,to:$to){
      contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}
    }
    repositories(first:100,ownerAffiliations:OWNER,privacy:PUBLIC,orderBy:{field:PUSHED_AT,direction:DESC}){
      nodes{name description url stargazerCount forkCount primaryLanguage{name}}
    }
  }
}"""

now = dt.datetime.now(dt.timezone.utc)
body = json.dumps({"query": QUERY, "variables": {
    "login": LOGIN,
    "from": (now - dt.timedelta(days=364)).strftime("%Y-%m-%dT00:00:00Z"),
    "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
}}).encode()
req = urllib.request.Request("https://api.github.com/graphql", data=body, headers={
    "Authorization": f"bearer {os.environ['GH_TOKEN']}", "Content-Type": "application/json"})
res = json.load(urllib.request.urlopen(req, timeout=30))
if "errors" in res:
    raise SystemExit(json.dumps(res["errors"], indent=2))
u = res["data"]["user"]
cal = u["contributionsCollection"]["contributionCalendar"]
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps({
    "login": LOGIN,
    "generated": now.isoformat(timespec="seconds"),
    "total": cal["totalContributions"],
    "days": [{"date": d["date"], "count": d["contributionCount"]}
             for w in cal["weeks"] for d in w["contributionDays"]],
    "repos": [{"name": r["name"], "description": r["description"], "url": r["url"],
               "stars": r["stargazerCount"], "forks": r["forkCount"],
               "language": (r["primaryLanguage"] or {}).get("name")} for r in u["repositories"]["nodes"]],
}, indent=1))
print(f"wrote {OUT} ({cal['totalContributions']} contributions)")
