"""Render the community's public GitHub activity and a named WakaTime account."""

import argparse
import base64
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request


ORG = "CWNU-Open-Source-Community"
TZ = timezone(timedelta(hours=8))
BLUE, GRAY, ICON = "#0366d6", "#777", "#959da5"
GREENS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]


def request(url, token=None, payload=None):
    headers = {"User-Agent": "CWNU-Community-Metrics", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if payload is None else json.dumps(payload).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=60) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        detail = ""
        if urllib.parse.urlsplit(url).hostname == "api.wakatime.com":
            try:
                result = json.loads(error.read())
                message = result.get("error") or result.get("errors") or result.get("message")
                detail = ": " + json.dumps(message, ensure_ascii=True)[:500]
                if token:
                    detail = detail.replace(token, "[redacted]")
            except (ValueError, AttributeError):
                pass
        raise RuntimeError(f"HTTP {error.code}: {urllib.parse.urlsplit(url).path}{detail}") from None


class GitHub:
    def __init__(self, token):
        self.token = token

    def rest(self, path):
        return request(f"https://api.github.com/{path}", self.token)[1]

    def pages(self, path):
        result = []
        separator = "&" if "?" in path else "?"
        page = 1
        while True:
            batch = self.rest(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise RuntimeError(f"Expected a list from {path}")
            result.extend(batch)
            if len(batch) < 100:
                return result
            page += 1

    def graphql(self, query, **variables):
        result = request("https://api.github.com/graphql", self.token, {"query": query, "variables": variables})[1]
        if result.get("errors"):
            raise RuntimeError("GitHub GraphQL: " + "; ".join(item["message"] for item in result["errors"]))
        return result["data"]


def repository_activity(api, repo, since):
    owner, name = repo["full_name"].split("/")
    metadata = api.graphql("""
      query($owner:String!,$name:String!) {
        repository(owner:$owner,name:$name) {
          isPrivate releases { totalCount } watchers { totalCount }
          defaultBranchRef { target { ... on Commit { history { totalCount } } } }
        }
      }
    """, owner=owner, name=name)["repository"]
    if metadata["isPrivate"]:
        raise RuntimeError("Only public repositories may be included")
    branch = metadata["defaultBranchRef"]
    total_commits = branch["target"]["history"]["totalCount"] if branch else 0
    days = Counter()
    if branch:
        cursor = None
        while True:
            connection = api.graphql("""
              query($owner:String!,$name:String!,$since:GitTimestamp!,$cursor:String) {
                repository(owner:$owner,name:$name) { defaultBranchRef { target { ... on Commit {
                  history(first:100,since:$since,after:$cursor) {
                    nodes { committedDate } pageInfo { hasNextPage endCursor }
                  }
                } } } }
              }
            """, owner=owner, name=name, since=since, cursor=cursor)["repository"]["defaultBranchRef"]["target"]["history"]
            for item in connection["nodes"]:
                day = datetime.fromisoformat(item["committedDate"].replace("Z", "+00:00")).astimezone(TZ).date()
                days[day.isoformat()] += 1
            if not connection["pageInfo"]["hasNextPage"]:
                break
            cursor = connection["pageInfo"]["endCursor"]
    counters = {"commits": total_commits, "issues": 0, "prs": 0, "reviewed": 0, "comments": 0}
    for field in ("issues", "pullRequests"):
        cursor = None
        while True:
            reviews = "reviews(states:[APPROVED,CHANGES_REQUESTED,COMMENTED,DISMISSED]) { totalCount }" if field == "pullRequests" else ""
            connection = api.graphql("""
              query($owner:String!,$name:String!,$cursor:String) {
                repository(owner:$owner,name:$name) {
                  FIELD(first:100,after:$cursor) {
                    totalCount nodes { comments { totalCount } REVIEWS }
                    pageInfo { hasNextPage endCursor }
                  }
                }
              }
            """.replace("FIELD", field).replace("REVIEWS", reviews), owner=owner, name=name, cursor=cursor)["repository"][field]
            counters["prs" if field == "pullRequests" else "issues"] = connection["totalCount"]
            counters["comments"] += sum(item["comments"]["totalCount"] for item in connection["nodes"])
            counters["reviewed"] += sum(item.get("reviews", {}).get("totalCount", 0) > 0 for item in connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                break
            cursor = connection["pageInfo"]["endCursor"]
    return {"name": name, "days": dict(days), "activity": counters, **metadata}


def external_contributions(api, members):
    totals = defaultdict(lambda: {"commits": 0, "prs": 0})
    for member in members:
        collection = api.graphql("""
          query($login:String!) { user(login:$login) { contributionsCollection {
            commitContributionsByRepository(maxRepositories:100) {
              repository { isPrivate owner { __typename login } } contributions { totalCount }
            }
            pullRequestContributionsByRepository(maxRepositories:100) {
              repository { isPrivate owner { __typename login } } contributions { totalCount }
            }
          } } }
        """, login=member["login"])["user"]["contributionsCollection"]
        for field, metric in (("commitContributionsByRepository", "commits"), ("pullRequestContributionsByRepository", "prs")):
            for item in collection[field]:
                repo = item["repository"]
                owner = repo["owner"]
                if not repo["isPrivate"] and owner["__typename"] == "Organization" and owner["login"].lower() != ORG.lower():
                    totals[owner["login"]][metric] += item["contributions"]["totalCount"]
    return sorted(({"name": key, **value} for key, value in totals.items()), key=lambda item: item["commits"] + item["prs"], reverse=True)[:8]


def avatar(url):
    # Avatar requests never receive a GitHub or WakaTime credential.
    if urllib.parse.urlsplit(url).hostname != "avatars.githubusercontent.com":
        raise RuntimeError("Unexpected avatar host")
    separator = "&" if "?" in url else "?"
    with urllib.request.urlopen(f"{url}{separator}s=64", timeout=30) as response:
        content_type = response.headers.get_content_type()
        return f"data:{content_type};base64," + base64.b64encode(response.read()).decode()


def wakatime(token):
    if not token:
        return {"state": "not-connected", "label": "Qiyuanqiii"}
    status, result = request("https://api.wakatime.com/api/v1/users/current/stats/last_year", token)
    if status == 202:
        return {"state": "processing", "label": "Qiyuanqiii"}
    stats = result["data"]
    return {
        "state": "ready", "label": "Qiyuanqiii",
        "total_seconds": stats["total_seconds_including_other_language"],
        "daily_average": stats["daily_average_including_other_language"],
        "languages": stats.get("languages", []), "editors": stats.get("editors", []),
        "operating_systems": stats.get("operating_systems", []),
    }


def collect(api, waka_token, now=None):
    now = now or datetime.now(TZ)
    personal_wakatime = wakatime(waka_token)
    start = now.date() - timedelta(days=181)
    since = datetime.combine(start, datetime.min.time(), TZ).isoformat()
    org = api.rest(f"users/{ORG}")
    repositories = [repo for repo in api.pages(f"orgs/{ORG}/repos?type=public") if not repo["fork"]]
    members = api.pages(f"orgs/{ORG}/public_members")
    followers = api.pages(f"users/{ORG}/followers")
    following = api.pages(f"users/{ORG}/following")
    starred = api.pages(f"users/{ORG}/starred")
    social = api.graphql("""
      query($login:String!) { organization(login:$login) {
        packages { totalCount }
        sponsorshipsAsMaintainer { totalCount } sponsorshipsAsSponsor { totalCount }
      } }
    """, login=ORG)["organization"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        activity = list(pool.map(lambda repo: repository_activity(api, repo, since), repositories))
        pictures = list(pool.map(avatar, [org["avatar_url"]] + [item["avatar_url"] for item in (followers[:12] + following[:12])]))
    days, counts = Counter(), Counter()
    for item in activity:
        days.update(item["days"])
        counts.update(item["activity"])
    notable = external_contributions(api, members)
    for item in notable:
        item["avatar"] = avatar(api.rest(f'users/{item["name"]}')["avatar_url"])
    licenses = Counter(repo["license"]["spdx_id"] for repo in repositories if repo.get("license") and repo["license"]["spdx_id"] != "NOASSERTION")
    return {
        "generated_at": now.isoformat(), "start": start.isoformat(), "org": org,
        "avatar": pictures[0], "followers": followers, "following": following,
        "people_avatars": pictures[1:], "public_members": len(members), "social": social,
        "starred": len(starred), "repositories": len(repositories),
        "stars": sum(repo["stargazers_count"] for repo in repositories),
        "forks": sum(repo["forks_count"] for repo in repositories),
        "watchers": sum(item["watchers"]["totalCount"] for item in activity),
        "releases": sum(item["releases"]["totalCount"] for item in activity),
        "size_kb": sum(repo["size"] for repo in repositories),
        "license": licenses.most_common(1)[0][0] if licenses else None,
        "activity": dict(counts), "days": dict(days),
        "active_repositories": sum(bool(item["days"]) for item in activity),
        "notable": notable,
        "wakatime": personal_wakatime,
    }


def streaks(days, today, start):
    values = [days.get((start + timedelta(days=i)).isoformat(), 0) for i in range((today - start).days + 1)]
    best = run = 0
    for count in values:
        run = run + 1 if count else 0
        best = max(best, run)
    cursor = today if values[-1] else today - timedelta(days=1)
    current = 0
    while cursor >= start and days.get(cursor.isoformat(), 0):
        current += 1
        cursor -= timedelta(days=1)
    return current, best, max(values, default=0), sum(values) / len(values)


def render(data):
    parts = []
    def add(value):
        parts.append(value)
    def text(x, y, value, color=GRAY, size=14, weight="normal", extra=""):
        add(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}" {extra}>{escape(str(value))}</text>')
    def icon(x, y, kind="circle"):
        shapes = {
            "people": '<circle cx="5" cy="4" r="2.4"/><path d="M1 13v-2a4 4 0 0 1 8 0v2m1-11a2.5 2.5 0 0 1 0 5m1 2c2 0 3 2 3 4"/>',
            "repo": '<path d="M2 1h11v12H3a2 2 0 0 1 0-4h10M2 1v10m3 1v3l2-1 2 1v-3"/>',
            "commit": '<circle cx="8" cy="8" r="3"/><path d="M1 8h4m6 0h4"/>',
            "pr": '<circle cx="4" cy="3" r="1.8"/><circle cx="4" cy="13" r="1.8"/><circle cx="12" cy="13" r="1.8"/><path d="M4 5v6m8 0V6c0-2-2-3-4-3m0 0 2-2M8 3l2 2"/>',
            "comment": '<path d="M1 2h13v9H7l-4 3v-3H1zM4 5h7M4 8h5"/>',
            "calendar": '<rect x="1" y="3" width="13" height="11" rx="1"/><path d="M1 6h13M4 1v4m7-4v4"/>',
            "chart": '<path d="M1 1v13h14M3 10l4-4 3 2 5-6"/>',
            "star": '<path d="m8 1 2 4.5 5 .5-3.8 3.5 1.1 5L8 12l-4.3 2.5 1.1-5L1 6l5-.5z"/>',
            "heart": '<path d="M8 14 2 8C-2 3 4-1 8 4c4-5 10-1 6 4z"/>',
            "eye": '<path d="M1 8s3-5 7-5 7 5 7 5-3 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/>',
            "clock": '<circle cx="8" cy="8" r="6"/><path d="M8 4v4l3 2"/>',
            "box": '<path d="m1 4 7-3 7 3v8l-7 3-7-3zM1 4l7 3 7-3M8 7v8"/>',
            "code": '<path d="m5 4-4 4 4 4m6-8 4 4-4 4M9 2 7 14"/>',
        }
        shape = shapes.get(kind, '<circle cx="8" cy="8" r="6"/>')
        add(f'<g transform="translate({x},{y-13})" fill="none" stroke="{ICON}" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">{shape}</g>')
    def field(x, y, value, kind="circle", color=GRAY):
        icon(x, y, kind)
        text(x + 24, y, value, color)
    def heading(y, title, kind):
        field(12, y, title, kind, BLUE)
    def image(x, y, size, value, title=""):
        key = f"avatar-{x}-{y}"
        add(f'<clipPath id="{key}"><circle cx="{x+size/2}" cy="{y+size/2}" r="{size/2}"/></clipPath>')
        add(f'<image x="{x}" y="{y}" width="{size}" height="{size}" href="{escape(value, quote=True)}" clip-path="url(#{key})"><title>{escape(title)}</title></image>')

    now = datetime.fromisoformat(data["generated_at"])
    today, start = now.date(), date.fromisoformat(data["start"])
    days = data["days"]
    image(12, 13, 22, data["avatar"])
    text(40, 31, data["org"].get("name") or ORG, BLUE, 18, "bold")
    created = datetime.fromisoformat(data["org"]["created_at"].replace("Z", "+00:00"))
    age = max(0, (today - created.astimezone(TZ).date()).days)
    age_text = f"{age // 365} years" if age >= 730 else "1 year" if age >= 365 else f"{age // 30} months" if age >= 60 else "1 month" if age >= 30 else f"{age} days"
    field(12, 54, f"Joined GitHub {age_text} ago", "clock")
    field(12, 74, f'Followed by {len(data["followers"])} users', "people")
    ceiling = max((days.get((today - timedelta(days=i)).isoformat(), 0) for i in range(14)), default=1) or 1
    for i in range(14):
        count = days.get((today - timedelta(days=13-i)).isoformat(), 0)
        color = GREENS[min(4, math.ceil(count / ceiling * 4))]
        add(f'<rect x="{254+i*14}" y="45" width="11" height="11" rx="1.5" fill="{color}"/>')
    field(254, 74, f'Active in {data["active_repositories"]} repositories', "repo")

    heading(106, "Activity", "chart")
    field(254, 106, "Community stats", "people", BLUE)
    activity = data["activity"]
    left = [(f'{activity["commits"]:,} Commits', "commit"), (f'{activity["reviewed"]:,} Pull requests reviewed', "comment"), (f'{activity["prs"]:,} Pull requests opened', "pr"), (f'{activity["issues"]:,} Issues opened', "circle"), (f'{activity["comments"]:,} Issue comments', "comment")]
    right = [(f'{data["public_members"]} public members', "people"), (f'Following {len(data["following"])} users', "people"), (f'Sponsoring {data["social"]["sponsorshipsAsSponsor"]["totalCount"]} accounts', "heart"), (f'Starred {data["starred"]} repositories', "star"), ("Watching —", "eye")]
    for i, ((label, kind), (other, other_kind)) in enumerate(zip(left, right)):
        field(12, 127+i*20, label, kind)
        field(254, 127+i*20, other, other_kind)

    heading(239, f'{data["repositories"]} Repositories', "repo")
    license_text = f'Prefers {data["license"]} license' if data["license"] else "No license preference"
    size_mb = data["size_kb"] / 1024
    size_text = f"{size_mb / 1024:.2f} GB used" if size_mb >= 1024 else f"{size_mb:.0f} MB used"
    for i, (label, kind) in enumerate([(license_text, "circle"), (f'{data["releases"]} Releases', "box"), (f'{data["social"]["packages"]["totalCount"]} Packages', "box"), (size_text, "box")]):
        field(12, 260+i*20, label, kind)
    for i, (key, label, kind) in enumerate([("sponsors", "Sponsors", "heart"), ("stars", "Stargazers", "star"), ("forks", "Forkers", "pr"), ("watchers", "Watchers", "eye")]):
        value = data["social"]["sponsorshipsAsMaintainer"]["totalCount"] if key == "sponsors" else data[key]
        field(254, 260+i*20, f"{value} {label}", kind)

    heading(352, "Notable contributions", "repo")
    text(36, 370, "Public members · past year · commits / PRs", size=10)
    x, y = 12, 382
    for item in data["notable"]:
        label = f'{item["name"]}  {item["commits"]} · {item["prs"]}'
        width = min(452, 35 + len(label) * 6.1)
        if x + width > 467:
            x, y = 12, y + 26
        add(f'<rect x="{x}" y="{y}" width="{width:.1f}" height="22" rx="4" fill="#f6f8fa" stroke="#d0d7de"/>')
        if item.get("avatar"):
            image(x+5, y+4, 14, item["avatar"], item["name"])
        text(x + 25, y + 15, label, size=11)
        x += width + 5
    if not data["notable"]:
        text(36, y+15, "No public external contributions recorded", size=12)
    y += 53
    heading(y, "Contributions calendar", "calendar")
    text(36, y+18, "Community repositories · default branches · past 182 days", size=10)
    current, best, maximum, average = streaks(days, today, start)
    grid_start = start - timedelta(days=(start.weekday()+1) % 7)
    for i in range((today-grid_start).days+1):
        day = grid_start + timedelta(days=i)
        if day < start:
            continue
        count = days.get(day.isoformat(), 0)
        week, weekday = divmod(i, 7)
        px, py = 42 + (week+weekday)*6.2, y+52 + (week-weekday)*3.2
        height = min(26, math.log2(count+1)*3.8)
        color = GREENS[min(4, math.ceil(count / max(maximum, 1) * 4))]
        def shade(factor):
            return "#" + "".join(f"{int(int(color[j:j+2], 16)*factor):02x}" for j in (1, 3, 5))
        add(f'<g><title>{day}: {count} commits</title>')
        if height:
            add(f'<path d="M{px-6},{py+3-height}v{height}l6,3v{-height}Z" fill="{shade(.75)}"/>')
            add(f'<path d="M{px},{py+6-height}v{height}l6,-3v{-height}Z" fill="{shade(.58)}"/>')
        add(f'<path d="m{px},{py-height} 6,3 -6,3 -6,-3Z" fill="{color}"/></g>')
    for offset, label, kind, color in [(40, "Commits streaks", "calendar", BLUE), (60, f"Current streak {current} days", "clock", GRAY), (80, f"Best streak {best} days", "chart", GRAY), (111, "Commits per day", "commit", BLUE), (131, f"Highest in a day at {maximum}", "chart", GRAY), (151, f"Average per day at ~{average:.2f}", "chart", GRAY)]:
        field(254, y+offset, label, kind, color)
    y += 190
    for group, label in (("followers", "followers"), ("following", "followed")):
        people = data[group]
        heading(y, f"{len(people)} {label}", "people")
        image_offset = 0 if group == "followers" else min(12, len(data["followers"]))
        for i, person in enumerate(people[:12]):
            image(12+i*37, y+9, 28, data["people_avatars"][image_offset+i], person["login"])
        y += 65 if people else 38

    waka = data["wakatime"]
    heading(y, f'WakaTime · {waka["label"]} (past year)', "clock")
    if waka["state"] == "ready":
        def top(field_name):
            items = sorted(waka[field_name], key=lambda item: item["total_seconds"], reverse=True)
            return items[0]["name"] if items else "No data yet"
        field(12, y+23, f'~{waka["total_seconds"]/3600:,.1f} coding hours recorded', "clock")
        field(254, y+23, f'~{waka["daily_average"]/3600:.1f} hours per day', "chart")
        field(12, y+44, f'Mostly coding in {top("languages")}', "code")
        field(254, y+44, f'Coding with {top("editors")}', "code")
        field(254, y+65, f'Using {top("operating_systems")}', "box")
        y += 98
        text(240, y, "Language activity", BLUE, 14, extra='text-anchor="middle"')
        for item in sorted(waka["languages"], key=lambda item: item["total_seconds"], reverse=True)[:5]:
            y += 18
            text(167, y, item["name"], size=11, extra='text-anchor="end"')
            width = max(0, min(100, item["percent"])) * 1.65
            add(f'<rect x="175" y="{y-10}" width="{width}" height="9" rx="4.5" fill="#6cc16c"/>')
            text(181+width, y-1, f'{item["percent"]:.0f}%', size=9)
    else:
        state = "Statistics are being calculated" if waka["state"] == "processing" else "Waiting for WakaTime connection"
        field(12, y+25, state, "clock")
        y += 44
    y += 36
    text(468, y, "GitHub: public community repositories / public member contributions", size=9, extra='text-anchor="end"')
    text(468, y+15, "WakaTime: Qiyuanqiii's personal coding activity", size=9, extra='text-anchor="end"')
    text(468, y+30, f'Last updated {now:%d %b %Y, %H:%M} (UTC+8)', size=9, extra='text-anchor="end"')
    height = y + 43
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="{height}" viewBox="0 0 480 {height}" role="img" aria-label="CWNU community activity"><rect width="480" height="{height}" fill="#fff"/><style>text{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif}}</style>' + "".join(parts) + "</svg>"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, help="Render a saved collection without making API requests")
    parser.add_argument("--save-data", type=Path)
    parser.add_argument("--output", type=Path, default=Path("profile/community-metrics.svg"))
    args = parser.parse_args()
    if args.data:
        data = json.loads(args.data.read_text(encoding="utf-8"))
    else:
        token = os.environ.get("GH_TOKEN") or subprocess.check_output(["gh", "auth", "token"], text=True).strip()
        data = collect(GitHub(token), os.environ.get("WAKATIME_TOKEN"))
    if args.save_data:
        args.save_data.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(data), encoding="utf-8")
    print(f'Generated {args.output}: {data["repositories"]} repositories, {len(data["followers"])} followers, WakaTime {data["wakatime"]["state"]}')


if __name__ == "__main__":
    main()
