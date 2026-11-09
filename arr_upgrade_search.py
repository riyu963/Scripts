#!/usr/bin/env python3
"""Search Sonarr and Radarr, a few at a time, for upgrades they missed.

Each run searches the next --count series (Sonarr: a series search) or movies (Radarr) that have files,
are monitored (--monitored says otherwise) and pass the limits, one at a time with a pause between, and
tags each one searched (--search-tag, made if it isn't there). The run that searches the last one the
limits allow takes the tag off them all, as arr_rename.py does, so the next run starts over and the whole
library is searched in turn, however slowly; ones added since are just part of the next turn.

How many it searches a run has to be set, for each Sonarr or Radarr (<PREFIX>_UPGRADE_COUNT, 1 to 500) or for every
one (--count): without it, that one isn't searched, rather than its whole library being searched at once and the
indexers hammered.

The limits, any of them together (each Sonarr or Radarr can have its own, see below):
  --tag NAME           only those with this tag (several: any of them)
  --ignore-tag TAG     leave those with this tag alone
  --quality-profile N  only those with this quality profile, by name (several: any of them)
  --age DAYS           only those released DAYS days ago or more (with 5 on the 20th: on the 15th or before; a
                       movie's release is the earliest of in cinemas, digitally and on disc, a series' its first
                       episode; one with no date is left out; 0: off)

After each search, what the arr grabbed for it is read from its history: each grab with the quality
and custom format score of the file it would replace, and of what was grabbed. The run's report (and
its Discord post) says how many were searched, how many had something grabbed, and each grab, old
against new.

Instances are set up as for arr_rename.py: SONARR_URL and SONARR_API_KEY (RADARR_... for Radarr), plus
named ones such as SONARR_ANIME_URL. A run searches in every one of the arr it's given (sonarr or radarr, first)
set up; --instance picks one. Each one can have its own settings, <PREFIX>_UPGRADE_... (SONARR_ANIME_UPGRADE_COUNT=5), one for each
option: COUNT, SEARCH_TAG, TAGS, QUALITY_PROFILES (the two comma-separated), IGNORE_TAG, AGE, MONITORED,
MODE and MAX_ADD (--max-add); an option given on the command line is for every one. Settings are read from the environment, or else from a .env file next to the script.

What it searches (--mode): every one that's out (all, the default: one with files, a movie Radarr counts as
available, a series whose first episode has aired), only those below their quality profile's
cutoff (cutoff: the arr's Wanted > Cutoff Unmet), or only missing ones (missing: Wanted > Missing). For Sonarr,
cutoff and missing search just the episodes listed there, not the whole series, as Sonarr's own Wanted search
does: a season search for each season with several of them listed, and an episode search for the rest.

--max-add N: once N of a run's searches have grabbed something (an upgrade, or a missing one), the run stops there,
and the rest of its batch isn't searched or tagged, so the next runs search them. 0 (the default): no limit. A dry
run can't know what a search would grab, so it lists the whole batch.

Guards, always on:
  - the ones with a download in the arr's queue (downloading, or waiting or stuck importing) under 72 hours old
    are left alone, so it never grabs a second copy (the next one takes its place in the batch; one is listed as
    held back only if the run got to it: not one after where max add, or a search still going, stopped it)
  - the run is skipped while the arr already has 3 or more searches queued
  - only the ones whose search finished are tagged: one still going after 10 minutes stops the run, so the rest is
    searched next time, and the next run tags it (and says what it grabbed) once it's finished, skipping the run
    while it isn't
  - one whose search the arr says failed 3 runs in a row is tagged anyway, so the turn can end (searched again next
    turn); one the arr couldn't be reached for isn't counted

Each grab is remembered (in a JSON file of each Sonarr's or Radarr's own, in --state), and each run after says which imported since, at what
quality and custom format score, and which failed; one still not imported after 7 days is let go.

Without --apply it's a dry run: it says what it would search, and searches and tags nothing.

Python 3.9+ (standard library only). Everything printed is also saved to logs/arr_upgrade_search.log, next to
the script. With a Discord webhook (DISCORD_WEBHOOK_URL or --discord-webhook), each run posts its report there.

    ./arr_upgrade_search.py sonarr --count 10 --apply
    ./arr_upgrade_search.py radarr --quality-profile "HD-1080p" --age 30 --apply
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
from collections import Counter

PINGS = {"failed": "the run fails", "grabbed": "an upgrade is grabbed", "imported": "an upgrade it grabbed imports",
         "import_failed": "one it grabbed doesn't import, or is stuck in the queue",
         "rotated": "it's searched them all and starts over"}
DEFAULT_PINGS = ("failed",)
SEARCH_TAG = "upgrade-searched"  # what marks one searched in this turn through the library
RENAME_TAG = "rename-checked"  # arr_rename.py's checked tag (its CHECKED_TAG), which the search tag can't be
DELAY = 5.0          # seconds between searches, to go easy on the indexers
MAX_COUNT = 500
LIMITS = ("tags", "quality_profiles", "monitored")
MONITORED = {"yes": "only monitored", "any": "monitored or not", "no": "only unmonitored"}  # --monitored
MODES = {"all": "every one that's out", "cutoff": "only those below their cutoff", "missing": "only missing ones"}  # --mode
SKIP_QUEUED = 72     # hours: one with a download in the queue younger than this is left alone (see the docstring)
MAX_BUSY = 3         # the run is skipped while the arr has this many searches queued or going on
SEARCH_WAIT = 10     # minutes a search is waited for; then the run stops there, and the next one finishes it
SEARCHES = ("SeriesSearch", "SeasonSearch", "EpisodeSearch", "MissingEpisodeSearch", "CutoffUnmetEpisodeSearch",
            "MoviesSearch", "MissingMoviesSearch", "CutoffUnmetMoviesSearch")  # the commands that are searches (MAX_BUSY)
BUSY = ("queued", "started")
POLL = 2.0           # seconds between checks on a search
SEARCHING = "_SEARCHING"  # --state's key (after the prefix) for the search a run stopped at, still going
FAILS = "_FAILS"     # --state's key (after the prefix) for how many runs in a row each one's search has failed
GIVE_UP_FAILS = 3    # runs in a row a search can fail (the arr saying so) before it's tagged anyway, so the turn can end
GIVE_UP = 6          # hours that search is waited for by the next runs; then it's taken as done
FOLLOW_DAYS = 7      # days a grab is followed for its import
STUCK = {"importBlocked": "blocked from importing", "importFailed": "import failed", "failedPending": "failed, waiting to be removed"}
PAGES = 100          # pages of the queue or a wanted list read, at most (500 each)
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")  # --state's default: where each one's state file is
RETRY = 10           # seconds to wait before trying a failed read from the arr again (once)


class Arr:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def call(self, method: str, endpoint: str, body=None, params=None):
        url = self.url + endpoint + (f"?{urllib.parse.urlencode(params)}" if params else "")
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

    def tags(self) -> dict[str, int]:
        """The arr's tags, as {label in lower case: id}."""
        return {t["label"].lower(): t["id"] for t in self.get("/api/v3/tag")}

    def quality_profiles(self) -> dict[str, int]:
        """The arr's quality profiles, as {name in lower case: id}."""
        return {p["name"].lower(): p["id"] for p in self.get("/api/v3/qualityprofile")}


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
    """Why the run can't start in a Sonarr or Radarr (a limit it doesn't have, ...). Found before anything is searched."""


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def day(text: str | None) -> datetime.date | None:
    """A date from the arr ("2021-09-15T00:00:00Z") or a limit ("2021-09-15"), or None."""
    try:
        return datetime.date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def when(text: str | None) -> datetime.datetime | None:
    """A time from the arr ("2021-09-15T10:00:00Z", maybe with fractions of a second), or None."""
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def released(item: dict, app: str) -> datetime.date | None:
    """When a movie came out (the earliest of in cinemas, digitally and on disc; else 1 January of its year),
    or a series' first episode aired (else 1 January of its year)."""
    dates = [d for d in (day(item.get(k)) for k in (("inCinemas", "digitalRelease", "physicalRelease") if app == "radarr"
                                                     else ("firstAired",))) if d]
    if dates:
        return min(dates)
    return datetime.date(item["year"], 1, 1) if item.get("year") else None


def quality(f: dict | None) -> str:
    return (((f or {}).get("quality") or {}).get("quality") or {}).get("name") or "?"


def score(f: dict | None) -> int | None:
    s = (f or {}).get("customFormatScore")
    return s if isinstance(s, int) else None


def formats(files: list[dict]) -> list[str]:
    """The custom formats `files` (or grabs) matched, by name, each once, in the order the arr gives them."""
    out = []
    for f in files:
        for cf in f.get("customFormats") or []:
            name = cf.get("name") if isinstance(cf, dict) else None
            if name and name not in out:
                out.append(name)
    return out


def describe(files: list[dict]) -> str:
    """The quality and custom format score of `files` (what a grab would replace): "WEBDL-1080p · CF 100", or
    with several, each quality with how many ("WEBDL-1080p ×6, HDTV-720p ×2 · CF 0–50"); "no file" for none."""
    if not files:
        return "no file"
    kinds = Counter(quality(f) for f in files)
    names = ", ".join(q if len(files) == 1 else f"{q} ×{n}" for q, n in kinds.most_common())
    scores = sorted({s for s in (score(f) for f in files) if s is not None})
    cf = "" if not scores else f" · CF {scores[0]}" if len(scores) == 1 else f" · CF {scores[0]}–{scores[-1]}"
    return names + cf


def file_name(f: dict) -> str:
    return os.path.basename(f.get("relativePath") or f.get("path") or "") or "?"


def was(files: list[dict], releases: dict[int, str]) -> str:
    """What a grab replaces, by name: with several files all from one release (a season pack), that release; with
    one, its file's name (and the release it came from, if that's known and isn't the file's name); with several
    not from one release, the first one's name and how many more. `releases`: each file's (by id) release name, if known."""
    if not files:
        return ""
    names = [releases.get(f.get("id")) for f in files]
    if len(files) > 1 and names[0] and len(set(names)) == 1:
        return f"{names[0]} (a season pack: {len(files)} files)"
    if len(files) == 1:
        name, release = file_name(files[0]), names[0]
        return name + (f" (from {release})" if release and os.path.splitext(name)[0] != release else "")
    first = sorted(files, key=file_name)[0]  # (not one pack: just the first's name, and how many more)
    return f"{file_name(first)} (and {len(files) - 1} more file{'s' * (len(files) > 2)})"


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


class Library:
    """One Sonarr's series or Radarr's movies, and the arr's names for its tags and quality profiles."""

    def __init__(self, arr: Arr, app: str):
        self.arr, self.app = arr, app
        self.noun, self.nouns = ("series", "series") if app == "sonarr" else ("movie", "movies")
        self.tags = arr.tags()
        self.profiles = arr.quality_profiles()
        self.items = arr.get("/api/v3/series" if app == "sonarr" else "/api/v3/movie")
        self.too_new = self.no_date = 0  # (how many the age limit left out: see eligible)
        self._queue = None  # (see queue)
        self.held_downloads: dict[int, set] = {}  # (each one queued gave, its downloads in the queue)

    def pages(self, path: str, **params) -> list[dict]:
        """Every record of a paged list (the queue, a wanted list), 500 at a time, up to PAGES pages."""
        out = []
        for page in range(1, PAGES + 1):
            got = self.arr.get(path, page=page, pageSize=500, **params) or {}
            out += got.get("records") or []
            if len(got.get("records") or []) < 500:
                break
        return out

    def wanted(self, mode: str, monitored: str) -> dict[int, list[dict]] | None:
        """For --mode cutoff or missing, the series (with their episodes listed there) or movies on the arr's Wanted
        list: {id: [{"id", "seasonNumber"} of each episode]} (a movie's: []); None for all."""
        if mode == "all":
            return None
        out: dict[int, list[dict]] = {}
        for value in {"yes": ["true"], "no": ["false"], "any": ["true", "false"]}[monitored]:
            for r in self.pages(f"/api/v3/wanted/{'cutoff' if mode == 'cutoff' else 'missing'}", monitored=value):
                if self.app == "sonarr" and r.get("seriesId") is not None:
                    out.setdefault(r["seriesId"], []).append({"id": r["id"], "seasonNumber": r.get("seasonNumber")})
                elif self.app == "radarr":
                    out.setdefault(r["id"], [])
        return out

    def queue(self) -> list[dict]:
        """The arr's download queue, read once a run."""
        if self._queue is None:
            self._queue = self.pages("/api/v3/queue")
        return self._queue

    def stuck(self, pending: list[dict]) -> list[dict]:
        """Those of earlier runs' grabs (`pending`, each as search gave it) whose download is stuck in the arr's queue
        (blocked from importing, failed to import, or with a warning or error), each with "reason": the arr's own messages."""
        out = []
        for g in pending:
            mine = [r for r in self.queue() if g.get("download") and r.get("downloadId") == g["download"]]
            bad = [r for r in mine if r.get("trackedDownloadStatus") in ("warning", "error")
                   or r.get("trackedDownloadState") in ("importBlocked", "importFailed", "failedPending")]
            if not bad:
                continue
            reasons = []  # (a season pack is in the queue once for each episode: each message once)
            for r in bad:
                said = [m for sm in r.get("statusMessages") or [] for m in sm.get("messages") or [] if m]
                for text in said or [r.get("errorMessage") or STUCK.get(r.get("trackedDownloadState"), "stuck in the queue")]:
                    if text not in reasons:
                        reasons.append(text)
            out.append({**g, "reason": "; ".join(reasons)})
        return out

    def queued(self, hours: float) -> dict[int, str]:
        """The series and movies with a download in the arr's queue under `hours` old (downloading, or waiting or stuck
        importing), each with why: {id: "waiting to import, 5h"}."""
        key, now, out = ("seriesId" if self.app == "sonarr" else "movieId"), datetime.datetime.now(datetime.timezone.utc), {}
        for r in self.queue():
            added = when(r.get("added"))
            age = (now - added).total_seconds() / 3600 if added else 0
            if r.get(key) is None or age > hours:
                continue
            state = r.get("trackedDownloadState") or r.get("status") or ""
            what = ("waiting to import" if state == "importPending" else "importing" if state == "importing"
                    else "stuck importing" if state in ("importBlocked", "importFailed", "failedPending") else "downloading")
            out.setdefault(r[key], f"{what}, {age:.0f}h")
            self.held_downloads.setdefault(r[key], set()).add(r.get("downloadId") or "")
        return out

    def busy(self) -> int:
        """How many searches the arr has queued or going on."""
        return sum(c.get("name") in SEARCHES and c.get("status") in BUSY for c in self.arr.get("/api/v3/command") or [])

    def wait(self, commands: list[int], limit: float) -> str:
        """Wait for the commands `commands`, up to `limit` seconds: "completed" once all have, else how one ended
        ("failed", ...), or "timeout" while one's still going. One the arr no longer has (long finished) counts as completed."""
        start = note = time.monotonic()
        ended: dict[int, str] = {}
        while True:
            for c in commands:
                if c in ended:
                    continue
                try:
                    status = (self.arr.get(f"/api/v3/command/{c}") or {}).get("status") or "completed"
                except OSError as e:
                    if getattr(e.__cause__, "code", None) != 404:
                        raise
                    status = "completed"
                if status not in BUSY:
                    ended[c] = status
            if all(c in ended for c in commands):
                return next((s for s in ended.values() if s != "completed"), "completed")
            if time.monotonic() - start >= limit:
                return "timeout"
            time.sleep(POLL)
            if time.monotonic() - note >= 30:
                note = time.monotonic()
                print(f"        still searching ({int(note - start)}s)")

    def has_files(self, item: dict) -> bool:
        return bool(item.get("hasFile")) if self.app == "radarr" else (item.get("statistics") or {}).get("episodeFileCount", 0) > 0

    def out(self, item: dict) -> bool:
        """Whether `item` is out, so a search can find it: it has files, or it's a movie Radarr counts as available
        (its minimum availability reached), or a series whose first episode has aired."""
        if self.has_files(item):
            return True
        if self.app == "radarr":
            if "isAvailable" in item:
                return bool(item["isAvailable"])
            dates = [d for d in (day(item.get(k)) for k in ("inCinemas", "digitalRelease", "physicalRelease")) if d]
            return bool(dates) and min(dates) <= datetime.date.today()
        first = day(item.get("firstAired"))
        return first is not None and first <= datetime.date.today()

    def tag_id(self, name: str, make: bool = False) -> int | None:
        """The id of tag `name` (made, with `make`, if the arr hasn't got it)."""
        if name.lower() not in self.tags and make:
            self.tags[name.lower()] = self.arr.call("POST", "/api/v3/tag", {"label": name.lower()})["id"]
        return self.tags.get(name.lower())

    def retag(self, ids: list[int], tag: int, how: str) -> None:
        """Add (`how` "add") or take off ("remove") tag `tag` on the series or movies `ids`."""
        if ids:
            key, path = ("seriesIds", "/api/v3/series/editor") if self.app == "sonarr" else ("movieIds", "/api/v3/movie/editor")
            self.arr.call("PUT", path, {key: ids, "tags": [tag], "applyTags": how})

    def eligible(self, limits: dict, wanted: dict | None = None) -> list[dict]:
        """The ones that are out (see out) the limits allow (only monitored ones, unless "monitored" says otherwise), by title; with
        `wanted` (see wanted), only those on it (for missing ones, whether they've files or not). Raises CouldntStart
        for a tag or quality profile the arr hasn't got."""
        def ids(names, table, what):
            out = set()
            for n in names:
                if n.lower() not in table:
                    raise CouldntStart(f"there's no {what} {n!r} (it has {', '.join(sorted(table)) or 'none'})")
                out.add(table[n.lower()])
            return out
        want = ids(limits.get("tags") or [], self.tags, "tag")
        ignore = limits.get("ignore_tag") or ""
        if ignore and ignore.lower() not in self.tags:  # (as arr_rename.py says it)
            raise CouldntStart(f"{self.app.capitalize()} has no tag {ignore!r}, so nothing would be ignored. "
                               f"Its tags: {', '.join(sorted(self.tags)) or 'none'}")
        unwanted = {self.tags[ignore.lower()]} if ignore else set()
        profiles = ids(limits.get("quality_profiles") or [], self.profiles, "quality profile")
        age = int(limits.get("age") or 0)
        latest = datetime.date.today() - datetime.timedelta(days=age)  # (age 5 on the 20th: released the 15th or before)
        monitored = limits.get("monitored") or "yes"
        out, self.too_new, self.no_date = [], 0, 0  # (see the age limit below)
        for item in self.items:
            tags = set(item.get("tags") or [])
            when = released(item, self.app)
            if monitored != "any" and bool(item.get("monitored")) != (monitored == "yes"):
                continue
            if wanted is not None and item["id"] not in wanted:
                continue
            if not (self.out(item) or limits.get("mode") == "missing") or want and not tags & want or tags & unwanted:
                continue
            if profiles and item.get("qualityProfileId") not in profiles:
                continue
            if age and (when is None or when > latest):  # (one with no date can't be known to be old enough)
                self.too_new += when is not None  # (counted only here, so these passed every other limit)
                self.no_date += when is None
                continue
            out.append(item)
        return sorted(out, key=lambda i: (i.get("sortTitle") or i.get("title") or "").lower())

    def name(self, item: dict) -> str:
        title, year = str(item.get("title")), item.get("year")
        return f"{title} ({year})" if year and not title.endswith(f"({year})") else title  # ("Show (2020)" has it already)

    def releases(self, item: dict, files: list[dict], episodes: dict[int, dict] | None = None) -> dict[int, str]:
        """The release each of `files` (by id) came from: its scene name, or else the release the arr's history
        says it imported for it (the latest import, for its episodes or its movie, up to when the file was added).
        `episodes`: a Sonarr series' episodes, by id."""
        out = {f["id"]: f["sceneName"] for f in files if f.get("sceneName")}
        if len(out) == len(files):
            return out
        try:
            if self.app == "sonarr":
                imports = self.arr.get("/api/v3/history/series", seriesId=item["id"], eventType="downloadFolderImported")
            else:
                imports = self.arr.get("/api/v3/history/movie", movieId=item["id"], eventType="downloadFolderImported")
        except OSError as e:  # only the names are missing: say so, and go on
            print(f"    couldn't read what the old files came from: {e}")
            return out
        imports = [h for h in imports if h.get("eventType") == "downloadFolderImported" and h.get("sourceTitle")]
        by_file = {}
        for e in (episodes or {}).values():
            by_file.setdefault(e.get("episodeFileId"), set()).add(e["id"])
        for f in files:
            if f["id"] in out:
                continue
            added = when(f.get("dateAdded"))  # (the import is logged a moment after the file's added: some leeway)
            mine = [h for h in imports if (h.get("episodeId") in by_file.get(f["id"], ()) if self.app == "sonarr" else True)
                    and (added is None or when(h.get("date")) and when(h.get("date")) <= added + datetime.timedelta(minutes=5))]
            if mine:
                out[f["id"]] = max(mine, key=lambda h: h.get("date") or "")["sourceTitle"]
        return out

    def snapshot(self, item: dict) -> dict:
        """What `item` has before it's searched, to say later what a grab replaces: its files, and a Sonarr series'
        episodes (each with just what's needed)."""
        def slim(f):
            return {k: f[k] for k in ("id", "relativePath", "path", "quality", "customFormats", "customFormatScore", "sceneName",
                                      "dateAdded") if k in f}
        if self.app == "radarr":
            return {"files": [slim(f) for f in self.arr.get("/api/v3/moviefile", movieId=item["id"]) or []]}
        return {"files": [slim(f) for f in self.arr.get("/api/v3/episodefile", seriesId=item["id"]) or []],
                "episodes": [{k: e.get(k) for k in ("id", "seasonNumber", "episodeNumber", "episodeFileId")}
                             for e in self.arr.get("/api/v3/episode", seriesId=item["id"]) or []]}

    def commands(self, item: dict, episodes: list[dict] | None = None) -> list[dict]:
        """The searches for `item`: a movie's, a whole series', or for a Sonarr series' `episodes` (from wanted), as
        Sonarr's own Wanted search does it: a season search for each season with several of them, and one search
        for the rest (an episode search is an indexer search for each episode)."""
        if self.app == "radarr":
            return [{"name": "MoviesSearch", "movieIds": [item["id"]]}]
        if not episodes:
            return [{"name": "SeriesSearch", "seriesId": item["id"]}]
        seasons: dict[int, list[int]] = {}
        for e in episodes:
            seasons.setdefault(e.get("seasonNumber") or 0, []).append(e["id"])
        singles = [ids[0] for ids in seasons.values() if len(ids) == 1]
        return ([{"name": "SeasonSearch", "seriesId": item["id"], "seasonNumber": n} for n, ids in sorted(seasons.items()) if len(ids) > 1]
                + ([{"name": "EpisodeSearch", "episodeIds": singles}] if singles else []))

    def search(self, item: dict, episodes: list[dict] | None = None, limit: float = SEARCH_WAIT * 60) -> tuple[str, list[dict], dict]:
        """Search for `item` (see commands), wait up to `limit` seconds for the searches to finish, and return how
        they ended (see wait), what the arr grabbed for it by then (see grabs), and what a later run needs to finish
        it if they're still going: {"id", "title", "year", "commands", "since", "before", "seen" (the releases
        reported already), "t"}."""
        before = self.snapshot(item)  # (before the search: a grab can import while it's still searching)
        local = datetime.datetime.now(datetime.timezone.utc)
        replies = [self.arr.call("POST", "/api/v3/command", body) for body in self.commands(item, episodes)]
        started = [r["id"] for r in replies]
        # (from when the arr says it queued the search, by its own clock, as its history is: this machine's may differ)
        queued = [t for t in (when(r.get("queued")) for r in replies) if t and t.tzinfo]
        since = (min(queued) if queued else local).replace(microsecond=0) - datetime.timedelta(seconds=2)
        status = self.wait(started, limit)
        if status not in ("completed", "timeout"):
            print(f"    the arr's search ended {status} (see System > Tasks in the arr)")
        stamp = since.isoformat().replace("+00:00", "Z")
        grabs = self.grabs(item["id"], stamp, before)
        return status, grabs, {"id": item["id"], "title": item.get("title"), "year": item.get("year"), "commands": started,
                               "since": stamp, "before": before, "seen": [g["release"] for g in grabs], "t": time.time()}

    def grabs(self, ident: int, since: str, before: dict, seen=()) -> list[dict]:
        """What the arr grabbed for series or movie `ident` since `since` (but not the releases `seen`), each against
        what it replaces in `before` (see snapshot): {"release", "before", "after", "old" (what it replaces, by name:
        see was), "quality", "score", "episodes", "download" (its download id, to follow it)}."""
        key = "seriesId" if self.app == "sonarr" else "movieId"
        grabs = [h for h in self.arr.get("/api/v3/history/since", date=since, eventType="grabbed")
                 if h.get("eventType") == "grabbed" and h.get(key) == ident and (h.get("sourceTitle") or "?") not in seen]
        if not grabs:
            return []
        item = {"id": ident}
        if self.app == "radarr":
            files = before["files"]
            old = was(files, self.releases(item, files))
            return [{"release": g.get("sourceTitle") or "?", "before": describe(files), "after": describe([g]), "old": old,
                     "before_cfs": formats(files), "after_cfs": formats([g]),
                     "quality": quality(g), "score": score(g), "episodes": "", "download": g.get("downloadId") or ""} for g in grabs]
        # a season pack is one grab for each of its episodes: one grab, each episode's file before it
        eps = {e["id"]: e for e in before["episodes"]}
        files = {f["id"]: f for f in before["files"]}
        names = self.releases(item, list(files.values()), eps)
        by_release: dict[str, list[dict]] = {}
        for g in grabs:
            by_release.setdefault(g.get("sourceTitle") or "?", []).append(g)
        out = []
        for release, gs in by_release.items():
            mine = [eps[g["episodeId"]] for g in gs if g.get("episodeId") in eps]
            before_files = [files[e["episodeFileId"]] for e in mine if e.get("episodeFileId") in files]
            out.append({"release": release, "before": describe(before_files), "after": describe([gs[0]]), "old": was(before_files, names),
                        "before_cfs": formats(before_files), "after_cfs": formats(gs[:1]),
                        "quality": quality(gs[0]), "score": score(gs[0]), "episodes": episodes_text(mine),
                        "download": gs[0].get("downloadId") or ""})
        return out

    def follow(self, pending: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        """What became of grabs from earlier runs (`pending`, each as search gave it, with its "t"): (imported, each
        with "imported" (its quality and score as it came in); not imported, each with "why"; still pending)."""
        if not pending:
            return [], [], []
        since = datetime.datetime.fromtimestamp(min(p["t"] for p in pending) - 60, datetime.timezone.utc)
        date = since.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        found: dict[str, dict[str, list[dict]]] = {}
        for event in ("downloadFolderImported", "downloadFailed"):
            for h in self.arr.get("/api/v3/history/since", date=date, eventType=event) or []:
                if h.get("eventType") == event and h.get("downloadId"):
                    found.setdefault(event, {}).setdefault(h["downloadId"], []).append(h)
        imported, not_imported, still = [], [], []
        for p in pending:
            done, failed = found.get("downloadFolderImported", {}).get(p["download"]), found.get("downloadFailed", {}).get(p["download"])
            if done:
                imported.append({**p, "imported": describe(done[:1])})
            elif failed:
                not_imported.append({**p, "why": ((failed[0].get("data") or {}).get("message") or "the download failed")[:200]})
            elif time.time() - p["t"] > FOLLOW_DAYS * 86400:
                not_imported.append({**p, "why": f"not imported after {FOLLOW_DAYS} days"})
            else:
                still.append(p)
        return imported, not_imported, still


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def own_state(folder: str, prefix: str) -> tuple[str, dict]:
    """One Sonarr's or Radarr's state file in `folder` (its own, so runs of different ones at once can't overwrite each
    other's), and what's in it."""
    path = os.path.join(folder, f"arr_upgrade_search_{prefix}.json")
    return path, load_state(path)


def save_state(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def quality_and_score(text: str) -> tuple[str, str]:
    """A describe() text split in two: ("WEBDL-1080p", "100") from "WEBDL-1080p · CF 100"; the score "" if there's none."""
    name, _, cf = str(text).partition(" · CF ")
    return name, cf


CF_WIDTH = 50  # characters on one CF line of a grab's block, "- CF: " included: Discord wraps a desktop embed's code
               # block at about 54, which would break the line in two, the second half without its colour


def cf_lines(sign: str, names: list[str], cut: int = 0) -> list[str]:
    """`names` as a block's "CF:" lines, `sign` ("-" or "+") at the start of each so all are red or green, each at most
    CF_WIDTH characters (a name too long for one is on a line of its own); the last `cut` of them left out, as "… and N
    more"."""
    shown = names[:len(names) - cut] if cut else names
    words = [n + "," for n in shown[:-1]] + shown[-1:] + ([f"… and {cut} more"] if cut else [])
    out, now = [], ""
    for w in words:
        if now and len("- CF: ") + len(now) + 1 + len(w) > CF_WIDTH:
            out.append(now)
            now = w
        else:
            now = f"{now} {w}" if now else w
    out.append(now)
    return [f"{sign} CF: {out[0]}"] + [f"{sign}     {more}" for more in out[1:]]


def grab_field(g: dict) -> str:
    """A grab's Discord field: the release grabbed, then a block of old against new, in red the quality, custom formats
    (see cf_lines) and custom format score of what it replaces, in green what was grabbed. Within a field's 1024
    characters: if it won't fit, the longer side's last custom formats are left out until it does."""
    def line(text: str) -> str:  # (a ``` inside the block would end it; 130 keeps a long name from taking the field)
        text = str(text).replace("```", "`\u200b`\u200b`")
        return text if len(text) <= 130 else text[:129] + "…"
    sides = [("-", g["before"], [line(n) for n in g.get("before_cfs") or []]),
             ("+", g["after"], [line(n) for n in g.get("after_cfs") or []])]
    cuts = [0, 0]
    while True:
        body = []
        for (sign, side, cfs), cut in zip(sides, cuts):
            name, score = quality_and_score(side)
            body += ([f"{sign} {line(name)}"] + (cf_lines(sign, cfs, cut) if cfs else [])
                     + ([f"{sign} Score: {line(score)}"] if score else []))
        lines = "\n".join(body)
        out = f"Release Name: {discord_escape(line(g['release']))}\n```diff\n{lines}\n```"
        n = max((0, 1), key=lambda i: len(sides[i][2]) - cuts[i])  # (the side with the most still shown)
        if len(out) <= 1024 or len(sides[n][2]) - cuts[n] <= 1:
            return out[:1024]
        cuts[n] += 1


OWN = {  # each Sonarr's or Radarr's own settings: <PREFIX>_<NAME>, for the limit `key`, read as `kind`
    "UPGRADE_COUNT": ("count", "count"), "UPGRADE_SEARCH_TAG": ("tag", "text"), "UPGRADE_TAGS": ("tags", "list"),
    "UPGRADE_IGNORE_TAG": ("ignore_tag", "text"), "UPGRADE_QUALITY_PROFILES": ("quality_profiles", "list"),
    "UPGRADE_AGE": ("age", "whole"), "UPGRADE_MONITORED": ("monitored", MONITORED), "UPGRADE_MODE": ("mode", MODES),
    "UPGRADE_MAX_ADD": ("max_add", "whole")}


def own_settings(prefix: str) -> dict:
    """One Sonarr's or Radarr's own settings, <PREFIX>_UPGRADE_... (see OWN); an empty one isn't set. Raises
    CouldntStart for one that can't be read."""
    out = {}
    for name, (key, kind) in OWN.items():
        var = f"{prefix}_{name}"
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        if kind == "list":
            # (each name may have its own quotes, "A (1080p)", "B": the .env reader only takes off the outer two)
            out[key] = [v.strip().strip("\"'").strip() for v in raw.split(",") if v.strip().strip("\"'").strip()]
        elif kind == "text":
            out[key] = raw
        elif isinstance(kind, dict):
            if raw.lower() not in kind:
                raise CouldntStart(f"{var} must be one of {', '.join(kind)}, not {raw!r}")
            out[key] = raw.lower()
        else:
            try:
                value = int(raw)
            except ValueError:
                value = -1
            if value < 0 or kind == "count" and not 1 <= value <= MAX_COUNT:
                raise CouldntStart(f"{var} must be " + (f"a whole number from 1 to {MAX_COUNT}" if kind == "count" else
                                   "a whole number (0 or more)") + f", not {raw!r}")
            out[key] = value
    return out


def limits_for(args, prefix: str) -> dict:
    """One Sonarr's or Radarr's limits, count and tag: its own settings (see own_settings), each overridden by what
    the command line gives. Raises CouldntStart for a setting that can't be read."""
    own = own_settings(prefix)
    given = {"tags": args.tag, "quality_profiles": args.quality_profile, "monitored": args.monitored}
    out = {k: given[k] if given[k] else own.get(k) for k in LIMITS}
    out["age"] = args.age if args.age is not None else own.get("age") or 0  # (a given 0 turns it off)
    out["max_add"] = args.max_add if args.max_add is not None else own.get("max_add") or 0  # (a given 0: no limit)
    out["mode"] = args.mode or own.get("mode") or "all"
    out["count"] = args.count if args.count is not None else own.get("count")
    if not out["count"]:  # (no default: searching a whole library at once would hammer the indexers)
        raise CouldntStart(f"how many to search each run isn't set: set {prefix}_UPGRADE_COUNT (1 to {MAX_COUNT}), "
                           "or give --count")
    out["tag"] = args.search_tag or own.get("tag") or SEARCH_TAG
    if not re.fullmatch(r"[a-z0-9-]+", out["tag"].lower()):  # (or the arr refuses to make it: checked before anything's searched)
        raise CouldntStart(f"the search tag {out['tag']!r} (--search-tag or {prefix}_UPGRADE_SEARCH_TAG) can only have "
                           "letters, digits and -, as the arr won't make any other tag")
    if out["tag"].lower() in (t.lower() for t in out.get("tags") or []):
        raise CouldntStart(f"the search tag {out['tag']!r} can't also be one of the tags searched (--tag or {prefix}_UPGRADE_TAGS), "
                           "or each turn would only search the ones searched last time")
    out["ignore_tag"] = (args.ignore_tag or own.get("ignore_tag") or "").strip()
    if out["ignore_tag"].lower() == out["tag"].lower():  # (as arr_rename.py refuses it)
        raise CouldntStart(f"the search tag {out['tag']!r} can't also be the ignore tag (--ignore-tag or {prefix}_UPGRADE_IGNORE_TAG), "
                           "or each one a run searches would be left alone from then on")
    # (nor one of arr_rename.py's, on the same arr: a run of either would add or take off the other's tag)
    rename_tag = (os.environ.get(f"{prefix}_RENAME_TAG") or "").strip() or RENAME_TAG
    rename_ignore = (os.environ.get(f"{prefix}_IGNORE_TAG") or "").strip()
    if out["tag"].lower() == rename_tag.lower():
        raise CouldntStart(f"the search tag {out['tag']!r} can't also be arr_rename.py's checked tag "
                           f"({prefix}_RENAME_TAG, default {RENAME_TAG}), or each script would take the other's off")
    if rename_ignore and out["tag"].lower() == rename_ignore.lower():
        raise CouldntStart(f"the search tag {out['tag']!r} can't also be arr_rename.py's ignore tag ({prefix}_IGNORE_TAG), "
                           "or arr_rename.py would leave alone each one a run searches")
    return out


def limits_text(limits: dict) -> str:
    parts = []
    for key, label in (("tags", "tagged"), ("quality_profiles", "profile")):
        if limits.get(key):
            parts.append(f"{label} {' or '.join(limits[key])}")
    if limits.get("ignore_tag"):
        parts.append(f"ignoring those tagged {limits['ignore_tag']!r}")
    if limits.get("age"):
        parts.append(f"released {plural(int(limits['age']), 'day')} ago or more")  # (the Skipped line gives the date)
    if (limits.get("monitored") or "yes") != "yes":
        parts.append(MONITORED[limits["monitored"]])
    if limits.get("max_add"):
        parts.append(f"Max add {int(limits['max_add']):,}")
    if limits.get("mode", "all") != "all":
        parts.insert(0, MODES[limits["mode"]])
    return "; ".join(parts) or "no limits"


def run(args, app: str, name: str | None) -> int:
    """Search the next ones in one Sonarr or Radarr. Returns the exit code."""
    prefix, label = settings(app, name)
    mode = "apply" if args.apply else "dry run"
    title = f"{label} upgrade search: {mode}"

    def failed(reason: str) -> int:
        print(f"ERROR: {label}: {reason}", file=sys.stderr)
        discord_report(args, title, f"**The search failed:** {discord_escape(reason)}", [], RED, [("failed", "the search failed")])
        return 1

    url, key = os.environ.get(prefix + "_URL"), os.environ.get(prefix + "_API_KEY", "")
    if not (url and key):
        return failed(f"no URL or API key: set {prefix}_URL and {prefix}_API_KEY (e.g. in .env)")
    try:
        limits = limits_for(args, prefix)
    except CouldntStart as e:
        return failed(str(e))
    count = max(1, min(MAX_COUNT, int(limits["count"])))
    print(f"{label}: {limits_text(limits)}; {plural(count, 'search', 'searches')} a run")
    try:
        lib = Library(Arr(full_url(url), key), app)
    except OSError as e:
        return failed(str(e))

    # what became of the grabs of earlier runs (a dry run only looks)
    state_path, state = own_state(args.state, prefix)
    pending = state.get(prefix, [])
    try:
        imported, not_imported, still = lib.follow(pending)
    except OSError as e:
        print(f"    couldn't read whether earlier grabs imported: {e}")
        imported, not_imported, still = [], [], pending
    for g in imported:
        print(f"    IMPORTED {withyear(g)}" + (f" ({g['episodes']})" if g.get("episodes") else "")
              + f": {g['release']}: {g['before']} → {g['imported']}")  # (only here: the post just counts them)
    for g in not_imported:
        print(f"    NOT IMPORTED {withyear(g)}: {g['release']}: {g['why']}")
    try:
        stuck = lib.stuck(still)  # (still followed: each run says so, with the arr's reason, until it imports or is let go)
    except OSError as e:
        print(f"    couldn't read the arr's queue: {e}")
        stuck = []
    for g in stuck:
        print(f"    STUCK {withyear(g)}: {g['release']}: {g['reason']}")
    if args.apply and (imported or not_imported):
        state[prefix] = still
        save_state(state_path, state)
    # of the earlier runs' grabs still waiting to import (last run's, and any older ones not imported yet), how many have
    followed = f"Imported: {len(imported):,} of {plural(len(pending), 'grab')} from previous run" if pending else ""
    if followed:
        print("    " + followed)

    def post(summary: str, lists: list, blocks: list, reasons: list, color: int) -> None:
        # the lists first (this run's, then what didn't import since the last run), then a block for each of this run's
        # grabs; what imported since the last run is only counted, below the limits (the log says what each one was)
        fields = lists + bullet_fields(f"Didn't import ({len(not_imported):,})",
                                       [f"{withyear(g)}: {g['release']} ({g['why']})" for g in not_imported])
        fields += bullet_fields(f"Stuck in the queue ({len(stuck):,})",  # (as plain text, with the arr's reason)
                                [f"{withyear(g)}: {g['release']}: {g['reason']}" for g in stuck])
        fields += blocks
        reasons = reasons + ([("imported", f"{plural(len(imported), 'upgrade')} imported")] if imported else []) + (
            [("import_failed", f"{plural(len(not_imported), 'grab')} didn't import")] if not_imported else []) + (
            [("import_failed", f"{plural(len(stuck), 'grab')} stuck in the queue")] if stuck else [])
        discord_report(args, title, discord_escape(summary) + f"\nLimits: {discord_escape(limits_text(limits))}"
                       + (f"\n{followed}" if followed else ""), fields, color, reasons)

    searched, grabbed, problems, stopped, gave_up = [], [], [], None, []
    fails = state.get(prefix + FAILS) or {}  # {id (as text): failed runs in a row}

    def save_fails() -> None:
        if fails:
            state[prefix + FAILS] = fails
        else:
            state.pop(prefix + FAILS, None)
        save_state(state_path, state)

    def failed_search(ident: int, what: str, status: str) -> str:
        """Count a search the arr says failed; after GIVE_UP_FAILS runs in a row, tag it anyway (searched for this turn), so
        one that always fails can't keep the turn from ending. Returns what the Failed list says."""
        fails[str(ident)] = fails.get(str(ident), 0) + 1
        why = f"{what}: the search ended {status}"
        if fails[str(ident)] >= GIVE_UP_FAILS:
            try:
                lib.retag([ident], lib.tag_id(limits["tag"], make=True), "add")
                for i in lib.items:  # (so it isn't picked again below)
                    if i["id"] == ident:
                        i["tags"] = [*(i.get("tags") or []), lib.tag_id(limits["tag"])]
                del fails[str(ident)]
                gave_up.append(ident)
                why += f" ({GIVE_UP_FAILS} runs in a row: tagged anyway, so the turn can end; it's searched again next turn)"
            except OSError as e:
                why += f" (couldn't tag it after {GIVE_UP_FAILS} failed runs: {e})"
        else:
            why += f" ({fails[str(ident)]} of {GIVE_UP_FAILS} runs in a row)"
        save_fails()
        return why

    def took(item: dict, what: str, grabs: list[dict]) -> None:
        """Report and remember what a search grabbed (each grab followed by the next runs, to say whether it imported)."""
        for g in grabs:
            print(f"        GRABBED {g['release']}" + (f" ({g['episodes']})" if g["episodes"] else "") +
                  f": {g['before']} → {g['after']}" + (f"\n            in place of {g['old']}" if g["old"] else ""))
            if g.get("download"):
                state.setdefault(prefix, []).append({**g, "t": time.time(), "title": item.get("title"), "year": item.get("year")})
                save_state(state_path, state)
        if grabs:
            grabbed.append((what, grabs))

    # a search the last run stopped at, still going then: once it's finished (or given up on), tagged, and what it grabbed since
    going = state.get(prefix + SEARCHING)
    finished = None
    if going:
        what = withyear(going)
        try:
            status = lib.wait(going["commands"], 0)
        except OSError as e:
            return failed(str(e))
        if status == "timeout" and time.time() - going["t"] < GIVE_UP * 3600:
            summary = f"Skipped: the last run's search of {what} is still going; the next run searches once it's finished"
            print(summary)
            post(summary, [], [], [], BLUE)
            return 0
        print(f"The last run's search of {what} " + (f"is still going after {GIVE_UP} hours: taken as done" if status == "timeout"
                                                       else f"has ended ({status})"))
        if args.apply and not any(i["id"] == going["id"] for i in lib.items):  # (deleted since: nothing left to tag)
            print(f"    {what} is no longer in {app.capitalize()}: its search is let go")
            del state[prefix + SEARCHING]
            save_state(state_path, state)
        elif args.apply:
            try:
                grabs = lib.grabs(going["id"], going["since"], going["before"], going["seen"])
                if status in ("completed", "timeout"):
                    lib.retag([going["id"]], lib.tag_id(limits["tag"], make=True), "add")
                    for i in lib.items:  # (so it isn't picked again below)
                        if i["id"] == going["id"]:
                            i["tags"] = [*(i.get("tags") or []), lib.tag_id(limits["tag"])]
            except OSError as e:
                return failed(str(e))
            del state[prefix + SEARCHING]
            save_state(state_path, state)
            if status in ("completed", "timeout"):
                finished = what
                searched.append(what)
                if fails.pop(str(going["id"]), None) is not None:
                    save_fails()
                took(going, what, grabs)
            else:
                problems.append(failed_search(going["id"], what, status))
    try:
        backlog = lib.busy()
    except OSError as e:
        return failed(str(e))
    if backlog >= MAX_BUSY:  # it'd only queue ours behind them: next time
        summary = f"Skipped: {app.capitalize()} already has {plural(backlog, 'search', 'searches')} queued or going on; the next run searches"
        print(summary)
        post(summary, [], [], [], BLUE)
        return 0
    try:
        wanted = lib.wanted(limits["mode"], limits.get("monitored") or "yes")
        pool = lib.eligible(limits, wanted)
        held = lib.queued(SKIP_QUEUED)
    except (OSError, CouldntStart) as e:
        return failed(str(e))
    tag = lib.tag_id(limits["tag"])
    untagged = [i for i in pool if tag not in (i.get("tags") or [])]
    rotated = bool(pool) and not untagged
    if rotated:  # every one searched: take the tag off them and start over
        print(f"Every one of the {plural(len(pool), lib.noun, lib.nouns)} has been searched: starting over")
        if args.apply:
            try:
                lib.retag([i["id"] for i in pool], tag, "remove")
            except OSError as e:
                return failed(f"couldn't take the {limits['tag']} tag off: {e}")
        untagged = pool
    batch, picked = [], []  # (in title order: the next `count` to search, with those held back on the way among them)
    for i in untagged:
        if len(picked) == count:
            break
        batch.append(i)
        if i["id"] not in held:
            picked.append(i)
    print(f"{plural(len(pool), lib.noun, lib.nouns)} within the limits, {len(untagged):,} not searched yet in this turn"
          + f"; {'searching' if args.apply else 'would search'} {len(picked):,}")
    waiting = []  # (those held back that the run got to: one after where it stopped wasn't to be searched this run)
    if args.apply and picked:
        try:
            tag = lib.tag_id(limits["tag"], make=True)
        except OSError as e:
            return failed(f"couldn't make the {limits['tag']} tag: {e}")
    most = int(limits["max_add"])
    capped = None  # with --max-add: how many of the batch were left unsearched once it was reached
    n = 0  # (searches started this run)
    for item in batch:
        what = lib.name(item)
        if args.apply and most and len(grabbed) >= most:  # enough grabbed: the rest of the batch is for the next runs
            capped = len(picked) - n
            print(f"    max add {most} reached: the other {plural(capped, lib.noun, lib.nouns)} of this batch are searched next time")
            break
        if item["id"] in held:  # (left for a run after its download's done; the next one takes its place in the batch)
            waiting.append(item)
            print(f"    HELD BACK {what}: {held[item['id']]}")
            continue
        n += 1
        if not args.apply:
            print(f"    WOULD SEARCH {what}")
            searched.append(what)
            continue
        if n > 1:
            time.sleep(args.delay)
        print(f"    SEARCH {what}")
        try:
            status, grabs, unfinished = lib.search(item, (wanted or {}).get(item["id"]) or None, SEARCH_WAIT * 60)
            if status != "completed":  # only one whose search finished is tagged
                if status == "timeout":  # the arr's falling behind: the rest waits for the next run, which finishes this one
                    stopped = what
                    print(f"    still searching after {SEARCH_WAIT} min: tagged once it's finished, and the run stops here")
                    state[prefix + SEARCHING] = unfinished
                    save_state(state_path, state)
                else:
                    why = failed_search(item["id"], what, status)
                    print(f"    FAILED: {why}")
                    problems.append(why)
                    continue
            else:
                lib.retag([item["id"]], tag, "add")  # (searched, whatever it found)
                if fails.pop(str(item["id"]), None) is not None:
                    save_fails()
        except OSError as e:
            print(f"    FAILED: {e}")
            problems.append(f"{what}: {e}")
            if len(problems) >= 3 and not searched:  # the arr isn't answering: stop rather than keep trying
                break
            continue
        took(item, what, grabs)  # (what a search still going had grabbed by then, too)
        if stopped:
            break
        searched.append(what)
    remaining = max(0, len(untagged) - len(searched) - len([i for i in untagged if i["id"] in gave_up])
                    + (finished is not None))  # (the last run's one was tagged before untagged was counted)
    untag_failed = ""
    if args.apply and pool and not remaining and not stopped:
        # the run that searches the last of them takes the tag off them all, as arr_rename.py does, so the next run starts
        # a new turn from the top, and ones added since are just part of it, rather than all each run searches
        try:
            lib.retag([i["id"] for i in pool], lib.tag_id(limits["tag"], make=True), "remove")
            rotated = True
            print(f"Searched the last of them: the {limits['tag']} tag is off them all, so the next run starts over")
        except OSError as e:
            untag_failed = f"Couldn't take the {limits['tag']} tag off them all ({e}); the next run does"
            print(untag_failed)
    did, done, total, nouns = "Searched" if args.apply else "Would search", len(searched), len(pool), lib.nouns
    runs = -(-remaining // count)
    if not pool:  # how far through the turn it is, and how many more runs that takes, as arr_rename.py says it
        progress = f"{did} no {nouns}: none within the limits"
    elif remaining:
        progress = (f"{did} {plural(done, lib.noun, nouns)} this run; total {total - remaining:,} of {total:,} {nouns} searched, "
                    f"{remaining:,} left to search, {plural(runs, 'more run')} at {count:,} a run")
    elif done == total:  # the whole turn in one run
        progress = f"{did} all {plural(total, lib.noun, nouns)} this run; next run starts over"
    else:
        progress = f"{did} {plural(done, lib.noun, nouns)} this run; total {total:,} of {total:,} {nouns} searched, next run starts over"
    counts = [f"{len(searched):,} Searched", f"{len(grabbed):,} Grabbed"] + ([f"{len(problems):,} Failed"] if problems else [])
    summary = (progress + ("\n" + "; ".join(counts) if args.apply else "")  # (which ones: in the log)
               + (f"\n{untag_failed}" if untag_failed else "")
               + ("\nSkipped: " + " - ".join(([f"{lib.no_date:,} No release date"] if lib.no_date else []) + (
                   [f"{lib.too_new:,} Released after {datetime.date.today() - datetime.timedelta(days=int(limits['age']))}"]
                   if lib.too_new else [])) if lib.no_date or lib.too_new else "")  # (what the age limit left out)
               + (f"\nFinished the last run's search of {finished}" if finished else "")
               + (f"\nStopped at {stopped}: still searching after {SEARCH_WAIT} min; it's tagged once it's finished, "
                  "and the rest is searched next time" if stopped else "")
               + (f"\nMax add {most} reached: stopped with {plural(capped, lib.noun, lib.nouns)} of the batch left, searched next time"
                  if capped else ""))
    print("\n" + summary)
    blocks = [(what + (f" · {g['episodes']}" if g["episodes"] else ""), grab_field(g)) for what, grabs in grabbed for g in grabs]
    lists = bullet_fields(f"Failed ({len(problems):,})", problems)  # (what was searched is only counted: the log lists each one)
    stuck_downloads = {g.get("download") for g in stuck}  # (one held back only by a download stuck in the queue is in that
    shown = [i for i in waiting if not lib.held_downloads.get(i["id"], {""}) <= stuck_downloads]  # list, with the arr's reason)
    lists += bullet_fields(f"Held back: a download's in the queue ({len(shown):,})", [f"{lib.name(i)}: {held[i['id']]}" for i in shown])
    reasons = ([("failed", f"{plural(len(problems), 'search', 'searches')} failed")] if problems else []) + (
        [("grabbed", f"{plural(len(grabbed), 'upgrade')} grabbed")] if grabbed else []) + (
        [("rotated", "every one was searched: starting over")] if rotated else [])
    color = RED if problems else GREEN if grabbed or imported else BLUE
    post(summary, lists, blocks, reasons, color)
    return 1 if problems else 0


def withyear(g: dict) -> str:
    title, year = str(g.get("title")), g.get("year")
    return f"{title} ({year})" if year and not title.endswith(f"({year})") else title


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    apps = ap.add_subparsers(dest="app", required=True, metavar="{radarr,sonarr}", help="which arr to search")
    for name in ("radarr", "sonarr"):  # (as arr_rename.py: the arr first, then its options)
        things = "series" if name == "sonarr" else "movies"
        p, var = apps.add_parser(name, help=f"search the {things} in {name.capitalize()}"), name.upper()
        p.add_argument("--instance", metavar="NAME",
                       help=f"only search this {name.capitalize()}: NAME from ${var}_<NAME>_URL, or main for ${var}_URL "
                            f"(default: every one set up, in turn)")
        p.add_argument("--apply", action="store_true", help="search and tag (without it: say what it would search)")
        p.add_argument("--count", type=int, metavar="N",
                       help=f"how many {things} to search each run, 1 to {MAX_COUNT} (default: ${var}_UPGRADE_COUNT, or "
                            f"${var}_<NAME>_UPGRADE_COUNT; one of them is needed)")
        p.add_argument("--search-tag", metavar="TAG",
                       help=f"the tag that marks one searched (default: ${var}_UPGRADE_SEARCH_TAG, or {SEARCH_TAG})")
        p.add_argument("--tag", action="append", metavar="NAME", help=f"only {things} with this tag (again for any of several)")
        p.add_argument("--ignore-tag", metavar="TAG", help=f"leave {things} with this tag alone, in every instance "
                                                            f"(default: ${var}_UPGRADE_IGNORE_TAG)")
        p.add_argument("--quality-profile", action="append", metavar="NAME", help=f"only {things} with this quality profile")
        p.add_argument("--age", type=int, metavar="DAYS",
                       help=f"only {things} " + ("whose first episode aired" if name == "sonarr" else "released")
                            + " DAYS days ago or more (5 on the 20th: on the 15th or before; 0: off)")
        p.add_argument("--monitored", choices=MONITORED, help="yes: only monitored ones (the default), any: monitored or "
                                                              "not, no: only unmonitored ones")
        p.add_argument("--mode", choices=MODES, help="all: every one that's out (the default), cutoff: only those below "
                                                     "their quality profile's cutoff, missing: only missing ones")
        p.add_argument("--max-add", type=int, metavar="N",
                       help=f"stop the run once N searches have grabbed something, leaving the rest of the batch untagged "
                            f"for the next runs (default: ${var}_UPGRADE_MAX_ADD; 0 or unset: no limit)")
        p.add_argument("--state", default=STATE_DIR, metavar="DIR", help="the folder of each one's state file, "
                       f"arr_upgrade_search_{var}.json (or ..._{var}_<NAME>.json): its grabs, to say on later runs whether they "
                       "imported, and a search still going (default: logs, next to the script)")
        p.add_argument("--delay", type=float, default=DELAY, metavar="S", help=f"seconds between searches (default {DELAY:g})")
        p.add_argument("--discord-webhook", default=os.environ.get("DISCORD_WEBHOOK_URL"), metavar="URL",
                       help="post the results to this Discord webhook (default: $DISCORD_WEBHOOK_URL)")
        p.add_argument("--discord-user", default=os.environ.get("DISCORD_USER_ID"), metavar="ID",
                       help="Discord user id to ping (default: $DISCORD_USER_ID)")
        p.add_argument("--discord-ping", metavar="ON", default="",
                       help=f"what to ping about, comma-separated: {', '.join(PINGS)}, or none (default: {','.join(DEFAULT_PINGS)})")
    args, unknown = ap.parse_known_args(argv)
    if unknown:  # argparse's own message would show them as typed, and it goes in the log: a mistyped --webhok URL too
        ap.error(f"unrecognized arguments: {masked_command(unknown)}")
    if args.count is not None and not 1 <= args.count <= MAX_COUNT:
        ap.error(f"--count must be from 1 to {MAX_COUNT}")
    if args.age is not None and args.age < 0:
        ap.error("--age must be 0 or more days")
    if args.max_add is not None and args.max_add < 0:
        ap.error("--max-add must be 0 or more")
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


# ----- Settings from a .env file next to the script -----
# Each line is KEY=value. Blank lines and lines starting with # are ignored, an `export ` prefix is allowed, and a value
# may be wrapped in single or double quotes. Variables already set in the environment win over the file.

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

FIELDS_PER_POST = 12  # fields of a list (searched, failed, ...) posted, at most; then "… and N more"

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

def bullet_fields(name: str, lines: list[str]) -> list[tuple[str, str]]:
    """`lines` as fields named `name` (then "(continued)"), escaped, one per line, each within a field's 1024
    characters, at most FIELDS_PER_POST of them."""
    fields, value = [], ""
    for n, line in enumerate(lines):
        line = "• " + discord_escape(line)[:300]
        if len(value) + len(line) + 1 > 1024 - len("\n… and 9999 more"):
            if len(fields) == FIELDS_PER_POST - 1:
                fields.append((name if not fields else "(continued)", value + f"\n… and {len(lines) - n} more"))
                return fields
            fields.append((name if not fields else "(continued)", value))
            value = ""
        value += ("\n" if value else "") + line
    if value:
        fields.append((name if not fields else "(continued)", value))
    return fields


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
