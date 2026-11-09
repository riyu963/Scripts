#!/usr/bin/env python3
"""Keep Sonarr's and Radarr's blocklists down in size.

Each run reads the whole blocklist and removes the entries the rules pick, through the arr itself (its bulk
delete). Removing an entry lets the arr grab that release again, so nothing is removed unless a rule says so:
with no rule set for a Sonarr or Radarr, it's left alone, and the run says so.

The rules, any of them together (an entry any of them picks is removed; each Sonarr or Radarr can have its own,
see below):
  --older-than DAYS  entries blocklisted more than DAYS days ago (by then, indexers have mostly dropped the release)
  --keep N           only the newest N entries for each movie (Radarr), or each episode or pack (Sonarr: the same
                     episodes), the older ones removed
  --max N            at most N entries in all, after the other rules: the oldest removed first
  --orphans          entries for a movie or series no longer in the arr
  --orphan-indexer   entries from an indexer no longer in the arr; one with no indexer recorded is left alone; not
                     with --keep. Matched by name only (an entry keeps the indexer's name, not its id): renaming an
                     indexer makes every entry blocklisted under its old name an orphan, removed by the next run

Which entries the rules look at (each Sonarr or Radarr can have its own, see below):
  --indexer NAME         only entries from this indexer (several: any of them), by its name in the arr
  --ignore-indexer NAME  never entries from this indexer (several: none of them)
--keep and --orphan-indexer can't be used with either, nor with each other: keep keeps the newest of all a movie's
entries, whichever indexer each came from, and orphan indexer is for whichever indexers are gone.

Guards, always on: --orphans doesn't run while the arr says it has no movies or series at all, nor --orphan-indexer
while it says it has no indexers (as while it starts up, or Prowlarr syncs): either would take every entry for an
orphan.

Without --apply it's a dry run: it says what it would remove, and removes nothing. The report lists each entry
removed (or that would be), under the rule that picked it, grouped by movie or series: the Discord post with its
release, indexer, protocol, how long ago it was blocklisted and why (the arr's message); the log with its episodes,
quality and custom formats too. If removing them fails partway, those not removed are listed as such.

Instances are set up as for arr_rename.py: SONARR_URL and SONARR_API_KEY (RADARR_... for Radarr), plus named ones
such as SONARR_ANIME_URL. A run goes through every one of the arr it's given (sonarr or radarr, first) set up;
--instance picks one. Each one can have its own rules, <PREFIX>_BLOCKLIST_... (SONARR_ANIME_BLOCKLIST_KEEP=3), one
for each: OLDER_THAN, KEEP, MAX (numbers; 0 or empty: off), ORPHANS and ORPHAN_INDEXER (yes or no), and INDEXERS and IGNORE_INDEXERS
(comma-separated); one given on the command line is for every one. Settings are read from the environment, or else from a .env file next to the script.

Python 3.9+ (standard library only). Everything printed is also saved to logs/arr_blocklist_cleanup.log, next to
the script. With a Discord webhook (DISCORD_WEBHOOK_URL or --discord-webhook), each run posts its report there.

    ./arr_blocklist_cleanup.py radarr --older-than 90
    ./arr_blocklist_cleanup.py sonarr --older-than 90 --keep 5 --orphans --apply
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

PINGS = {"failed": "the run fails", "removed": "entries are removed"}
DEFAULT_PINGS = ("failed",)
RULES = {  # each rule: its setting (<PREFIX>_BLOCKLIST_<NAME>), and how the report heads the entries it picked
    "orphans": ("ORPHANS", "No longer in {arr}"),
    "orphan_indexer": ("ORPHAN_INDEXER", "Indexer no longer in {arr}"),
    "older_than": ("OLDER_THAN", "Older than {n} days"),
    "keep": ("KEEP", "Over keep threshold"),
    "max": ("MAX", "Over the max of {n}"),
}
NOT_REMOVED = "not_removed"  # how the report groups the entries a failed delete left in the blocklist
PAGE = 1000          # blocklist entries read a request
PAGES = 1000         # pages read, at most
CHUNK = 500          # entries removed a request (bulk delete)
EPISODE_CHUNK = 100  # episodes looked up a request
RETRY = 10           # seconds to wait before trying a failed read from the arr again (once)


class Arr:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def call(self, method: str, endpoint: str, body=None, params=None):
        url = self.url + endpoint + (f"?{urllib.parse.urlencode(params, doseq=True)}" if params else "")
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "X-Api-Key": self.api_key, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:  # add the arr's explanation: the status line alone doesn't say what's wrong
            with e:  # (and close the reply once it's read)
                reason = " ".join(e.read().decode(errors="replace").split())[:300]
            hint = ", check the API key" if e.code == 401 else ""  # the arr's 401 has no explanation of its own
            raise OSError(f"HTTP Error {e.code}: {e.reason}{hint} ({method} {endpoint})"
                          + (f": {reason}" if reason else "")) from e
        except OSError as e:  # couldn't connect, or the connection dropped: say to which server and request
            raise OSError(f"couldn't reach {self.url} ({method} {endpoint}): {getattr(e, 'reason', e)}") from e
        try:
            return json.loads(raw) if raw else None
        except ValueError as e:  # a web page, like a login page in front of the arr, or another app at that address
            start = " ".join(raw[:100].decode(errors="replace").split())
            raise OSError(f"the reply from {self.url} ({method} {endpoint}) isn't the arr's: it starts {start!r}. "
                          "Is the URL right, with no login page in front of the arr?") from e

    def get(self, endpoint: str, **params):
        """Read from the arr. If it can't be reached or has a server error (as while it restarts),
        try once more RETRY seconds later. Only reads are tried again: a request that changes
        something may have gone through even though it failed."""
        try:
            return self.call("GET", endpoint, params=params)
        except OSError as e:
            cause = e.__cause__
            if isinstance(cause, ValueError) or isinstance(cause, urllib.error.HTTPError) and cause.code < 500:
                raise  # the arr refused it (wrong API key, not found, ...) or isn't the arr: trying again won't help
            print(f"    {e}; trying once more in {RETRY}s")
            time.sleep(RETRY)
            return self.call("GET", endpoint, params=params)


def instances(app: str) -> list:
    """The `app` ("sonarr"/"radarr") instances set up in the environment: None for the main one
    (SONARR_URL), then the named ones (SONARR_<NAME>_URL) by name, in lower case."""
    pre = app.upper() + "_"
    named = {k[len(pre):-len("_URL")].lower() for k, v in os.environ.items()
             if k.startswith(pre) and k.endswith("_URL") and k[len(pre):-len("_URL")] and v}
    return ([None] if os.environ.get(pre + "URL") else []) + sorted(named)


def settings(app: str, name: str | None) -> tuple[str, str]:
    """The start of an instance's setting names ("SONARR" or "SONARR_ANIME"), and how the log and
    Discord name it ("Sonarr" or "Sonarr Anime")."""
    return (app.upper() + (f"_{name.upper()}" if name else ""),
            app.capitalize() + (f" {name.title()}" if name else ""))


def full_url(url: str) -> str:
    """`url` with http:// in front if it has no http:// or https://: "192.168.1.10:8989" is
    http://192.168.1.10:8989."""
    return url if "://" in url else "http://" + url


class CouldntStart(Exception):
    """Why the run can't start in a Sonarr or Radarr (a rule set to something that isn't one, ...)."""


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def when(text: str | None) -> datetime.datetime | None:
    """A time from the arr ("2021-09-15T10:00:00Z", maybe with up to 7 digits of fractions of a second, which Python
    before 3.11 can't read past 6), in UTC, or None."""
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], str(text or "").replace("Z", "+00:00"), count=1)
    try:
        t = datetime.datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return t if t is None or t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def episodes_text(eps: list[dict]) -> str:
    """Episodes as "S01E01–E08", "S01E03", or "S01–S02 (14 episodes)"."""
    eps = sorted(eps, key=lambda e: (e.get("seasonNumber", 0), e.get("episodeNumber", 0)))
    if not eps:
        return ""
    seasons = sorted({e.get("seasonNumber", 0) for e in eps})
    if len(seasons) > 1:
        return f"S{seasons[0]:02d}–S{seasons[-1]:02d} ({plural(len(eps), 'episode')})"
    first, last = eps[0].get("episodeNumber", 0), eps[-1].get("episodeNumber", 0)
    return f"S{seasons[0]:02d}E{first:02d}" + (f"–E{last:02d}" if last != first else "")


def own_rules(args, prefix: str) -> dict:
    """The rules for one Sonarr or Radarr: each from the command line if given there, else <PREFIX>_BLOCKLIST_<NAME>.
    Numbers: 0 or unset is off. Raises CouldntStart for a setting that isn't a number (or yes/no)."""
    rules = {}
    for key, (setting, _) in RULES.items():
        given = getattr(args, key)
        if given is not None and given is not False:
            rules[key] = given
            continue
        var = f"{prefix}_BLOCKLIST_{setting}"
        text = os.environ.get(var, "").strip()
        if key in ("orphans", "orphan_indexer"):
            if text.lower() not in ("", "yes", "no", "true", "false", "1", "0"):
                raise CouldntStart(f"{var} is {text!r}: set it to yes or no")
            rules[key] = text.lower() in ("yes", "true", "1")
        elif text:
            if not text.isdigit():
                raise CouldntStart(f"{var} is {text!r}: set it to a number (0 or empty: off)")
            rules[key] = int(text)
        else:
            rules[key] = 0
    return rules


def own_indexers(args, prefix: str) -> tuple[list[str], list[str]]:
    """The indexers one Sonarr's or Radarr's entries are limited to, and those left alone: each from the command line
    if given there, else <PREFIX>_BLOCKLIST_INDEXERS / _IGNORE_INDEXERS (comma-separated)."""
    def names(given, setting):
        if given:
            return [n.strip() for n in given if n.strip()]
        return [n.strip() for n in os.environ.get(f"{prefix}_BLOCKLIST_{setting}", "").split(",") if n.strip()]
    return names(args.indexer, "INDEXERS"), names(args.ignore_indexer, "IGNORE_INDEXERS")


def rules_text(rules: dict, noun: str, only=(), ignore=()) -> str:
    on = ([f"older than {plural(rules['older_than'], 'day')}"] if rules["older_than"] else []) + (
        [f"keep {rules['keep']:,} per {noun}"] if rules["keep"] else []) + (
        [f"max {rules['max']:,}"] if rules["max"] else []) + (["orphans"] if rules["orphans"] else []) + (
        ["orphan indexers"] if rules["orphan_indexer"] else []) + (
        [("indexer " if len(only) == 1 else "indexers ") + ", ".join(only)] if only else []) + (
        ["not " + ", ".join(ignore)] if ignore else [])
    return "Limits: " + (", ".join(on) if on else "none")


def pick(entries: list[dict], rules: dict, library: set[int], app: str, today: datetime.datetime,
         indexers: set[str] = frozenset()) -> dict[str, list[dict]]:
    """Which entries the rules remove, by the rule that picked each one first (orphans, orphan indexers, older than,
    keep, then max, which counts what the others leave). `library`: the ids of the movies or series the arr has;
    `indexers`: the names of the indexers it has, in lower case."""
    owner = "movieId" if app == "radarr" else "seriesId"
    picked: dict[str, list[dict]] = {key: [] for key in RULES}
    gone: set[int] = set()

    def take(key: str, e: dict) -> None:
        picked[key].append(e)
        gone.add(e["id"])

    newest_first = sorted(entries, key=lambda e: when(e.get("date")) or today, reverse=True)
    if rules["orphans"]:
        for e in newest_first:
            if e.get(owner) not in library:
                take("orphans", e)
    if rules["orphan_indexer"]:
        for e in newest_first:  # (one with no indexer recorded has no name to check: left alone)
            name = str(e.get("indexer") or "").strip().lower()
            if e["id"] not in gone and name and name not in indexers:
                take("orphan_indexer", e)
    if rules["older_than"]:
        cutoff = today - datetime.timedelta(days=rules["older_than"])
        for e in newest_first:
            if e["id"] not in gone and (when(e.get("date")) or today) < cutoff:
                take("older_than", e)
    if rules["keep"]:
        seen: dict[tuple, int] = {}
        for e in newest_first:  # (the newest of each are kept)
            if e["id"] in gone:
                continue
            item = (e.get(owner),) if app == "radarr" else (e.get(owner), tuple(sorted(e.get("episodeIds") or [])))
            seen[item] = seen.get(item, 0) + 1
            if seen[item] > rules["keep"]:
                take("keep", e)
    if rules["max"]:
        left = [e for e in newest_first if e["id"] not in gone]
        for e in left[rules["max"]:]:
            take("max", e)
    return picked


def owner_name(e: dict, names: dict[int, str], app: str) -> str:
    """The movie or series an entry is for, as the report heads it: "Alien (1979)", or "Not in Radarr (id 99)"."""
    owner = e.get("movieId" if app == "radarr" else "seriesId")
    return names.get(owner) or f"Not in {app.capitalize()} (id {owner})"


def details(e: dict, episodes: dict[int, dict], today: datetime.datetime) -> tuple[str, str, str]:
    """An entry's details: in full for the log ("S01E01 · WEBDL-1080p · CF: DV, HDR10 · Indexer · torrent"), only
    where it came from for Discord ("Indexer · torrent"), and "130 days ago: Manually marked as failed" (the arr's
    message)."""
    eps = episodes_text([episodes[i] for i in e.get("episodeIds") or [] if i in episodes])
    quality = ((e.get("quality") or {}).get("quality") or {}).get("name")
    cfs = [cf.get("name") for cf in e.get("customFormats") or [] if isinstance(cf, dict) and cf.get("name")]
    source = [d for d in (e.get("indexer"), str(e.get("protocol") or "")) if d and d != "unknown"]
    what = [d for d in (eps, quality, "CF: " + ", ".join(cfs) if cfs else "") if d] + source
    at = when(e.get("date"))
    message = " ".join(str(e.get("message") or "").split())
    return " · ".join(what), " · ".join(source), (f"{plural((today - at).days, 'day')} ago" if at else "when: unknown") + (
        f": {message[:150]}" if message else "")


def block(groups: list[tuple[str, list[tuple[str, str, str, str]]]]) -> str:
    """A ```diff``` code block of one movie's or series' removals: each rule's label, then each entry it picked, its
    release in red ("- "), then where it came from and when and why it was blocklisted; as many as fit in a field's 1024 characters."""
    def line(s):  # (a ``` inside the block would end it early)
        return s.replace("```", "`\u200b`\u200b`")[:300]
    total = sum(len(entries) for _, entries in groups)
    body, shown = "", 0
    room = 1024 - len("```diff\n\n```") - len("\n… and 9999 more")
    for label, entries in groups:
        head = ("\n" if body else "") + label + ":"
        for n, (release, _, what, why) in enumerate(entries):
            entry = (head if not n else "") + f"\n- {line(release)}" + (f"\n  {line(what)}" if what else "") + f"\n  {line(why)}"
            if len(body) + len(entry) > room:
                return f"```diff\n{body}\n… and {total - shown:,} more\n```"
            body += entry
            shown += 1
    return f"```diff\n{body}\n```"


def run(args, app: str, name: str | None) -> int:
    """Clean up one Sonarr's or Radarr's blocklist. Returns the exit code."""
    prefix, label = settings(app, name)
    mode = "apply" if args.apply else "dry run"
    title = f"{label} blocklist cleanup: {mode}"

    def failed(reason: str) -> int:
        print(f"ERROR: {label}: {reason}", file=sys.stderr)
        discord_report(args, title, f"**The cleanup failed:** {discord_escape(reason)}", [], RED,
                       [("failed", "the cleanup failed")])
        return 1

    url, key = os.environ.get(prefix + "_URL"), os.environ.get(prefix + "_API_KEY", "")
    if not (url and key):
        return failed(f"no URL or API key: set {prefix}_URL and {prefix}_API_KEY (e.g. in .env)")
    item = "movie" if app == "radarr" else "episode or pack"
    try:
        rules = own_rules(args, prefix)
        only, ignore = own_indexers(args, prefix)
        if rules["orphan_indexer"] and rules["keep"]:
            raise CouldntStart("orphan indexer can't be used with keep: keep counts each "
                               f"{item}'s entries from every indexer, gone ones too. Drop one of them")
        if rules["orphan_indexer"] and (only or ignore):
            raise CouldntStart("orphan indexer can't be used with an indexer limit: it's for the indexers no longer in "
                               f"{app.capitalize()}, whichever they are. Drop --orphan-indexer (or its setting), or the indexer limit")
        if rules["keep"] and (only or ignore):
            # (keep is about all of a movie's entries: counting only some indexers' would keep more than it says)
            raise CouldntStart(f"keep can't be used with an indexer limit: it keeps each {item}'s newest entries from "
                               "every indexer. Drop --keep (or its setting), or the indexer limit")
    except CouldntStart as e:
        return failed(str(e))
    limits = rules_text(rules, item, only, ignore)
    print(f"{label}: {limits}")
    if not any(rules.values()):
        print(f"No rules set for {label}, so nothing is removed: give --older-than, --keep, --max, --orphans or --orphan-indexer, "
              f"or set {prefix}_BLOCKLIST_OLDER_THAN (and so on)")
        return 0
    arr = Arr(full_url(url), key)
    try:
        entries = []
        for page in range(1, PAGES + 1):
            got = arr.get("/api/v3/blocklist", page=page, pageSize=PAGE, sortKey="date", sortDirection="descending")
            entries += got.get("records") or []
            if len(entries) >= (got.get("totalRecords") or 0) or not got.get("records"):
                break
        # (one blocklisted while the pages are read shifts them, so an entry can be on two)
        entries = list({e["id"]: e for e in entries}.values())
        items = arr.get("/api/v3/movie" if app == "radarr" else "/api/v3/series")
        indexers = {str(i.get("name") or "").strip().lower() for i in arr.get("/api/v3/indexer")} if rules["orphan_indexer"] else set()
    except OSError as e:
        return failed(str(e))
    things = "movies" if app == "radarr" else "series"
    if rules["orphans"] and entries and not items:
        return failed(f"{app.capitalize()} says it has no {things} at all, so every entry would be an orphan: not running "
                      "--orphans until it lists them (is it still starting up?)")
    if rules["orphan_indexer"] and entries and not indexers:
        return failed(f"{app.capitalize()} says it has no indexers at all, so every entry would be from an orphan indexer: "
                      "not running --orphan-indexer until it lists them (is it starting up, or Prowlarr syncing?)")
    names = {i["id"]: f"{i.get('title')} ({i['year']})" if i.get("year") else str(i.get("title")) for i in items}
    today = datetime.datetime.now(datetime.timezone.utc)
    looked_at = entries
    if only or ignore:  # (by name, whatever the case)
        known = sorted({str(e.get("indexer") or "") for e in entries} - {""}, key=str.lower)
        unknown = [n for n in only + ignore if n.lower() not in {k.lower() for k in known}]
        if unknown:
            print(f"    WARNING: no entry is from {', '.join(unknown)}; the indexers in the blocklist are: "
                  + (", ".join(known) or "none"))
        wanted, unwanted = {n.lower() for n in only}, {n.lower() for n in ignore}
        looked_at = [e for e in entries if (not wanted or str(e.get("indexer") or "").lower() in wanted)
                     and str(e.get("indexer") or "").lower() not in unwanted]
    picked = pick(looked_at, rules, set(names), app, today, indexers)
    removing = [e for key in RULES for e in picked[key]]

    episodes: dict[int, dict] = {}  # (Sonarr: the episodes of the entries removed, to name them)
    if app == "sonarr" and removing:
        ids = sorted({i for e in removing for i in e.get("episodeIds") or []})
        try:
            for n in range(0, len(ids), EPISODE_CHUNK):
                for ep in arr.get("/api/v3/episode", episodeIds=ids[n:n + EPISODE_CHUNK]) or []:
                    episodes[ep["id"]] = ep
        except OSError as e:
            print(f"    couldn't read the episodes' numbers: {e}")

    removed, problem, kept = 0, "", set()  # kept: the ids a failed delete left in the blocklist
    if args.apply:
        ids = [e["id"] for e in removing]
        for n in range(0, len(ids), CHUNK):
            try:
                arr.call("DELETE", "/api/v3/blocklist/bulk", body={"ids": ids[n:n + CHUNK]})
            except OSError as e:
                kept = set(ids[n:])
                problem = f"Couldn't remove the rest ({len(kept):,}): {e}"
                print(problem, file=sys.stderr)
                break
            removed += len(ids[n:n + CHUNK])

    heads = {key: RULES[key][1].format(n=rules[key], arr=app.capitalize(), item=item) for key in RULES}
    heads[NOT_REMOVED] = "Not removed (the delete failed)"
    # each movie or series with what's removed from it, by rule: {name: {rule: [(release, details, when and why)]}}
    by_owner: dict[str, dict[str, list]] = {}
    for key in RULES:
        for e in picked[key]:
            by_owner.setdefault(owner_name(e, names, app), {}).setdefault(NOT_REMOVED if e["id"] in kept else key, []).append(
                (e.get("sourceTitle") or "?", *details(e, episodes, today)))
    for owner in sorted(by_owner, key=str.lower):
        print(f"    {owner}")
        for key, found in by_owner[owner].items():
            word = ("NOT REMOVED (the delete failed)" if key == NOT_REMOVED else
                    f"{'REMOVE' if args.apply else 'WOULD REMOVE'} ({heads[key][0].lower() + heads[key][1:]})")
            for release, what, _, why in found:
                print(f"        {word} {release} ({what}{' · ' if what else ''}{why})")

    total, count = len(entries), removed if args.apply else len(removing)
    did = "Removed" if args.apply else "Would remove"
    summary = (f"{did} {count:,} of {plural(total, 'entry', 'entries')} · {total - count:,} left" if total
               else "The blocklist is empty") + (f"\n{problem}" if problem else "")
    print("\n" + summary)
    # (every movie or series, however many: the post is split over as many messages as it takes)
    fields = [(discord_escape(owner), block([(heads[key], by_owner[owner][key]) for key in [*RULES, NOT_REMOVED]
                                             if key in by_owner[owner]]))
              for owner in sorted(by_owner, key=str.lower)]
    reasons = ([("failed", "removing entries failed")] if problem else []) + (
        [("removed", f"{plural(count, 'blocklist entry', 'blocklist entries')} removed")] if args.apply and count else [])
    color = RED if problem else GREEN if count else BLUE
    discord_report(args, title, discord_escape(summary) + f"\n{discord_escape(limits)}", fields, color, reasons)
    return 1 if problem else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    apps = ap.add_subparsers(dest="app", required=True, metavar="{radarr,sonarr}", help="which arr's blocklist")
    for name in ("radarr", "sonarr"):  # (as arr_rename.py: the arr first, then its options)
        item = "movie" if name == "radarr" else "episode or pack"
        p, var = apps.add_parser(name, help=f"clean up {name.capitalize()}'s blocklist"), name.upper()
        p.add_argument("--instance", metavar="NAME",
                       help=f"only this {name.capitalize()}: NAME from ${var}_<NAME>_URL, or main for ${var}_URL "
                            f"(default: every one set up, in turn)")
        p.add_argument("--apply", action="store_true", help="remove the entries (without it: say what it would remove)")
        p.add_argument("--older-than", type=int, metavar="DAYS",
                       help=f"remove entries blocklisted more than DAYS days ago (default: ${var}_BLOCKLIST_OLDER_THAN; 0: off)")
        p.add_argument("--keep", type=int, metavar="N",
                       help=f"keep only the newest N entries for each {item} (default: ${var}_BLOCKLIST_KEEP; 0: off); not with "
                            "--orphan-indexer or an indexer limit")
        p.add_argument("--max", type=int, metavar="N",
                       help=f"keep at most N entries in all, the oldest removed first (default: ${var}_BLOCKLIST_MAX; 0: off)")
        p.add_argument("--orphans", action="store_true", default=None,
                       help=f"remove entries for a {'movie' if name == 'radarr' else 'series'} no longer in "
                            f"{name.capitalize()} (default: ${var}_BLOCKLIST_ORPHANS=yes)")
        p.add_argument("--orphan-indexer", action="store_true", default=None,
                       help=f"remove entries from an indexer no longer in {name.capitalize()}, matched by name: renaming an indexer "
                            f"makes its older entries orphans (default: ${var}_BLOCKLIST_ORPHAN_INDEXER=yes); not with --keep "
                            "or an indexer limit")
        p.add_argument("--indexer", action="append", metavar="NAME",
                       help=f"only entries from this indexer, by its name in {name.capitalize()} (again for any of several; "
                            f"default: ${var}_BLOCKLIST_INDEXERS, comma-separated); not with --keep or --orphan-indexer")
        p.add_argument("--ignore-indexer", action="append", metavar="NAME",
                       help=f"never entries from this indexer (again for several; default: ${var}_BLOCKLIST_IGNORE_INDEXERS, "
                            "comma-separated); not with --keep or --orphan-indexer")
        p.add_argument("--discord-webhook", default=os.environ.get("DISCORD_WEBHOOK_URL"), metavar="URL",
                       help="post the results to this Discord webhook (default: $DISCORD_WEBHOOK_URL)")
        p.add_argument("--discord-user", default=os.environ.get("DISCORD_USER_ID"), metavar="ID",
                       help="Discord user id to ping (default: $DISCORD_USER_ID)")
        p.add_argument("--discord-ping", metavar="ON", default="",
                       help=f"what to ping about, comma-separated: {', '.join(PINGS)}, or none (default: {','.join(DEFAULT_PINGS)})")
    args, unknown = ap.parse_known_args(argv)
    if unknown:  # argparse's own message would show them as typed, and it goes in the log: a mistyped --webhok URL too
        ap.error(f"unrecognized arguments: {masked_command(unknown)}")
    for option in ("older_than", "keep", "max"):
        if getattr(args, option) is not None and getattr(args, option) < 0:
            ap.error(f"--{option.replace('_', '-')} must be 0 or more")
    if args.discord_user and not args.discord_user.isdigit():
        ap.error(f"the Discord user id must be a number (Copy User ID in Discord), not {args.discord_user!r}")
    try:
        args.discord_ping = pings(args.discord_ping, PINGS, DEFAULT_PINGS)
    except ValueError as e:
        ap.error(str(e))
    names = instances(args.app)
    if args.instance:
        names = [None if args.instance.lower() == "main" else args.instance.lower()]
    if not names:
        print(f"No {args.app.capitalize()} set up: set {args.app.upper()}_URL and {args.app.upper()}_API_KEY "
              f"(or {args.app.upper()}_<NAME>_...), e.g. in .env", file=sys.stderr)
        return 1
    code = 0
    for n, name in enumerate(names):
        if len(names) > 1:
            print(("\n" if n else "") + f"----- {settings(args.app, name)[1]} -----")
        code = max(code, run(args, args.app, name))
    return code


ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env(path: str = ENV_FILE) -> None:
    """Set each KEY=value in `path` as an environment variable, unless it's already set. No file is fine."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = (part.strip() for part in line.partition("="))
        if not (sep and key):
            sys.exit(f"{path}:{n}: expected KEY=value")
        quoted = re.fullmatch(r"""(["'])(.*?)\1\s*(?:#.*)?""", value)
        if quoted:  # kept as it is, # and all; a comment may follow the closing quote
            value = quoted.group(2)
        elif value.startswith(("'", '"')):
            sys.exit(f"{path}:{n}: a quoted value needs its closing {value[0]}, with nothing but a # comment after it")
        else:  # a # at the start or after a space or tab starts a comment
            value = re.sub(r"(?:^|\s+)#.*", "", value)
        os.environ.setdefault(key, value)


# ----- Log: a copy of everything the script prints, in logs/ next to the script, to check on runs later -----
# Each run is added to the end of logs/<script name>.log, between a line saying when it started, with which arguments
# and from which git branch and commit, and a line saying when and how it ended. Answers typed at its questions are
# logged after them, and a crash's traceback is logged too. Once the log reaches 5 MB, a run starts a new one; the old
# ones are kept as <script name>.log.1 (the newest) to .log.3, and older ones are deleted.

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_MAX_SIZE = 5 * 1024 * 1024  # bytes; a bigger log is moved to .log.1 and a new one started
LOG_KEEP = 3  # old logs kept: .log.1 to .log.3
SECRET = ("key", "webhook")  # options whose values are left out of the log: any with these in their name,
SECRET_OPTIONS = ("--api-key", "--discord-webhook")  # and these however shortened (argparse takes --api for --api-key)


class Tee:
    """Writes to `stream` and to `log`. It has no fileno(), so input() writes its prompt through it."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, s: str) -> int:
        self.stream.write(s)
        self.log.write(s)
        return len(s)

    def flush(self) -> None:
        self.stream.flush()
        self.log.flush()


class Answers:
    """Reads from `stream` and logs each line read, so input()'s answers end up after its prompts."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def readline(self, *args) -> str:
        line = self.stream.readline(*args)
        self.log.write(line)
        return line


def masked_command(argv) -> str:
    """`argv` as a command line, with the values of options like --api-key replaced by ***."""
    out, hide = [], False
    for arg in argv:
        name, eq, _ = arg.partition("=")
        secret = name.startswith("--") and (any(s in name for s in SECRET)
                                            or len(name) > 2 and any(o.startswith(name) for o in SECRET_OPTIONS))
        out.append("***" if hide else f"{name}=***" if secret and eq else shlex.quote(arg))
        hide = secret and not eq  # the value is the next argument
    return " ".join(out)


def rotate_log(path: str) -> None:
    """Move `path` to `path`.1, .1 to .2 and so on up to .LOG_KEEP, and delete any older ones."""
    prefix = os.path.basename(path) + "."
    for name in os.listdir(os.path.dirname(path)):
        n = name[len(prefix):]
        if name.startswith(prefix) and n.isdigit() and int(n) >= LOG_KEEP:
            os.remove(os.path.join(os.path.dirname(path), name))
    for n in range(LOG_KEEP - 1, 0, -1):
        if os.path.exists(f"{path}.{n}"):
            os.replace(f"{path}.{n}", f"{path}.{n + 1}")
    os.replace(path, path + ".1")


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextlib.contextmanager
def tee(script: str, argv=None):
    """Log what's printed (and typed) inside the `with` to logs/<`script`'s name>.log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, os.path.splitext(os.path.basename(script))[0] + ".log")
    if os.path.exists(path) and os.path.getsize(path) >= LOG_MAX_SIZE:
        rotate_log(path)
    argv = sys.argv[1:] if argv is None else argv
    start = time.monotonic()
    with open(path, "a", encoding="utf-8", errors="backslashreplace") as log:
        log.write(f"===== {now()} · {script_footer(script)} · {masked_command(argv)}\n")
        log.flush()
        streams = sys.stdout, sys.stderr, sys.stdin
        sys.stdout, sys.stderr, sys.stdin = Tee(sys.stdout, log), Tee(sys.stderr, log), Answers(sys.stdin, log)
        end = "exit 0"
        try:
            yield path
        except SystemExit as e:
            if isinstance(e.code, str):  # sys.exit("message"): Python prints it once we're done
                log.write(e.code + "\n")
            end = f"exit {e.code if isinstance(e.code, int) else 0 if e.code is None else 1}"
            raise
        except KeyboardInterrupt:
            end = "interrupted"
            raise
        except BaseException:
            log.write(traceback.format_exc())
            end = "crashed"
            raise
        finally:
            sys.stdout, sys.stderr, sys.stdin = streams
            log.write(f"===== {now()} · {end} after {int(time.monotonic() - start)}s\n\n")


# ----- Discord: post a report to a channel as embeds, through a plain webhook -----
# (Server Settings > Integrations > Webhooks.) A report is a title, a description, a list of fields (each a name and a
# value, such as a series title and a ```diff``` code block) and a footer naming the script and the git commit it ran
# from. Fields are packed into as few embeds as fit Discord's limits, one embed per message, and mentions are switched
# off so a name like "@everyone" can't ping anybody.

EMBED_FIELDS = 25   # fields per embed
EMBED_CHARS = 5800  # Discord allows 6000 characters per embed; this leaves some room
POST_GAP = 1.0      # seconds between messages, to stay under the webhook rate limit
USER_AGENT = "vibes-scripts/1.0"  # Discord refuses urllib's default

BLUE, GREEN, RED = 0x3498DB, 0x2ECC71, 0xE74C3C


def discord_escape(text: str) -> str:
    """Text shown as-is in Discord markdown."""
    text = re.sub(r"([\\*_~`|])", r"\\\1", text)
    return "\\" + text if text[:1] in "#>-" and text else text


def script_footer(script: str) -> str:
    """`script`'s name and the git branch and commit it's running from, e.g. "x.py · dev@5dd3027"
    ("-dirty" if it has local changes). Just the name when git can't tell."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=os.path.dirname(os.path.abspath(script)), capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip()
    name = os.path.basename(script)
    try:
        return f"{name} · {git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('describe', '--always', '--dirty')}"
    except (OSError, subprocess.SubprocessError):
        return name


def discord_embeds(title: str, description: str, fields, color: int, footer: str = "") -> list[dict]:
    """Embeds holding `fields` ((name, value) pairs), as few as fit, each with `footer`. Later ones
    are titled "(continued)"."""
    text = footer[:2048]  # counts towards each embed's characters
    out = [{"title": title[:256], "description": description[:4096], "color": color, "fields": []}]
    size = len(out[0]["title"]) + len(out[0]["description"])
    for name, value in fields:
        field = {"name": name[:256], "value": value[:1024], "inline": False}
        n = len(field["name"]) + len(field["value"])
        if len(out[-1]["fields"]) == EMBED_FIELDS or size + n > EMBED_CHARS - len(text):
            out.append({"title": f"{title} (continued)"[:256], "color": color, "fields": []})
            size = len(out[-1]["title"])
        out[-1]["fields"].append(field)
        size += n
    if text:
        for embed in out:
            embed["footer"] = {"text": text}
    return out


def discord_post(url: str, payload: dict, ping: str = "") -> None:
    """POST `payload` to the webhook. Only the user id `ping` (if any) may be pinged."""
    body = json.dumps({**payload, "allowed_mentions": {"parse": [], "users": [ping] if ping else []}}).encode()
    for _ in range(5):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            urllib.request.urlopen(req, timeout=30).close()
            return
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            try:
                wait = float(json.loads(e.read()).get("retry_after", 1))
            except (ValueError, TypeError, AttributeError):  # not Discord's JSON, e.g. a Cloudflare page
                wait = 1
            time.sleep(wait)
    raise OSError("Discord kept rate-limiting the webhook")


def discord_send(url: str, title: str, description: str, fields, color: int = BLUE, footer: str = "",
                 ping: str = "", why: str = "") -> None:
    """Post the report to the webhook at `url`, one embed per message. With `ping` (a Discord user
    id), the first message also mentions that user, followed by `why`; embeds alone can't ping."""
    for n, embed in enumerate(discord_embeds(title, description, fields, color, footer)):
        payload = {"embeds": [embed]}
        if n:
            time.sleep(POST_GAP)
        elif ping:
            payload["content"] = f"<@{ping}> {why}".strip()
        discord_post(url, payload, ping if not n else "")

def pings(value, choices, default) -> set[str]:
    """What to ping about, from a --discord-ping value: names from `choices`, comma-separated, or "none".
    Empty means `default`. Raises ValueError for a name it doesn't know."""
    names = {n.strip() for n in (value or "").split(",") if n.strip()}
    if not names:
        return set(default)
    if names == {"none"}:
        return set()
    unknown = names - set(choices)
    if unknown:
        raise ValueError(f"can't ping on {', '.join(sorted(unknown))}: choose from {', '.join(choices)}, or none")
    return names


def discord_report(args, title: str, description: str, fields, color: int, reasons=()) -> None:
    """Post a run's report to --discord-webhook (if there is one), pinging --discord-user about those of `reasons`
    ((PINGS name, why) each) that --discord-ping names. One that fails is only warned about: the output says it all."""
    if not args.discord_webhook:
        return
    why = "; ".join(text for name, text in reasons if name in args.discord_ping)
    try:
        discord_send(args.discord_webhook, title, description, fields, color, script_footer(__file__),
                     args.discord_user if why and args.discord_user else "", why)
    except Exception as e:
        print(f"WARNING: couldn't post to Discord: {e if isinstance(e, OSError) else repr(e)}", file=sys.stderr)


if __name__ == "__main__":
    load_env()
    sys.stdout.reconfigure(errors="backslashreplace", line_buffering=True)
    with tee(__file__):
        sys.exit(main())
