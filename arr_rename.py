#!/usr/bin/env python3
"""Find Sonarr series and Radarr movie folders whose names don't match the arr's naming format, and rename them.

"The arr" below means Sonarr or Radarr, whichever one the script is pointed at. It must run on
Linux or macOS (or in Docker): the script reads the arr's paths as Linux paths, so an arr running
on Windows isn't supported, and a run on one doesn't start.

What is checked
  For each series or movie, the script asks the arr what its folder should be called (the arr's
  /folder endpoint) and compares that with the folder's real name. Capitals count as a difference,
  unless --ignore-case is given, and so do special characters (anything other than letters, digits
  and spaces, outside the year and id tags), unless --ignore-punctuation is given. Each mismatch
  says what is different: the title, the year, an id tag ({tvdb-...}, {tmdb-...}, {imdb-...} or
  {tvmaze-...}) that doesn't match the arr's id, or only the capitals. The two options apply to
  file names in the same way: a file rename that only changes what they ignore is skipped.
  These checks only read from the arr.

How a folder is renamed (only with --apply or --interactive)
  1. The item is refreshed in the arr, and the script waits for that to finish. A failed refresh
     stops the run, unless the item has been deleted from the arr in the meantime: then it's
     skipped, and the Discord report lists it as GONE, with a ping.
  2. The item is checked again. If it no longer needs renaming, it's skipped. If the new name has
     changed, the new one is used.
  3. Once any refresh the arr is running has finished the folder is renamed the same way the arr's web
     page does it: the item's root folder is "changed" to the one it's already in, with "move
     files" on, so the arr moves the folder to <root folder>/<correct name>. A folder inside a
     subfolder of a root folder can't be renamed this way (the arr only takes a root folder, and
     would move it out of the subfolder), so it's listed as CAN'T RENAME; --files still renames
     its files.
  4. The script waits for the move to finish, then compares the files before and after, both on
     disk (as the arr sees them) and in the arr's records. If the records don't match the files,
     it asks the arr to refresh, and logs that.
  5. It waits 2 seconds before contacting the arr again.
  Any problem stops the whole run, including an arr command still running after an hour, so
  nothing else gets moved until you've looked at it. Only a read from the arr that fails because
  it can't be reached or has a server error (as while it restarts for an update) is tried once
  more, 10 seconds later; a request that changes something never is.

File names
  When a folder is renamed, its files are renamed too, to the names the arr's rename preview gives
  them, so they pick up the new title and year. This is done with the arr's RenameFiles command
  once the folder has been moved and checked, and the files are compared before and after in the
  same way.
  --files checks the files of every item, so files are renamed even when their folder is fine.
  --tba (Sonarr only) does the same, but only for files whose episode title was "TBA" and now has
  a real title, and only when that's the only change. TBA only counts as a word of its own, with a
  space before it and a space or the file extension after it, so "FOOTBALL" has none. Less clear
  cases, such as a new title with TBA in it ("This episode title is TBA"), are marked TBA? and,
  like irregular folders, only renamed with --interactive.

Irregular renames
  If the new name would lose something the current folder name has (its year, an id tag or the
  title), the arr is probably missing some information. The rename is marked IRREGULAR and only
  done with --interactive or --force

Options
  By default this is a dry run: nothing is changed. --apply does every rename, and --interactive
  asks you about each one. --apply --force also does the renames that would otherwise need
  --interactive and marks them FORCED. Folders that can't be renamed, 
  for example because their new name is already taken, are still skipped.
  Series and movies with the tag ignore tag are left alone.

Batched runs
  Rather than checking the whole library every time, each run can check a batch of it. Set the
  batch size with SONARR_RENAME_COUNT / RADARR_RENAME_COUNT (or --count N): each run then checks
  the next N series or movies, in title order, that haven't been checked yet in this pass through
  the library. With --apply or --interactive, each one it checks is tagged SONARR_RENAME_TAG /
  RADARR_RENAME_TAG (or --checked-tag; default rename-checked, created if the arr hasn't got it),
  so the next run moves on to the next batch. The tag can only have letters, digits and - (the arr
  won't make any other), and can't be the --ignore-tag; with such a tag, the run doesn't start.
  The run that checks the last of them takes the tag off everything, so the next run starts a new
  pass from the top.
  If a run stops partway, the ones it didn't finish stay untagged and the next run checks them
  again. Ones left for --interactive are tagged like the rest and come round again next pass.
  The summary and the Discord post show the progress, e.g. "Checked 50 series this run; total 100
  of 1,234 series checked, 1,134 left to check, 23 more runs at 50 a run". A dry run never tags.
  Leave the batch size empty, or set it to 0, to check everything every run.

Rename limit
  Rename at most N series or movies, with SONARR_RENAME_MAX / RADARR_RENAME_MAX (or --max-renames N): 
  once N have been renamed, the rest that need renaming are listed as LATER and left alone. No tag is needed: the ones
  renamed now match, so the next run finds nothing to do for them and goes on to the rest.
  Irregular folders and TBA? files left for --interactive, and those that can't be renamed or are
  ignored, don't count towards N and are never LATER: they're listed as usual every run. A dry run stops at the same
  point, so it shows what --apply would do. The summary and the Discord post say how far it got,
  e.g. "Rename limit 10 - 25 more to rename in 3 runs". 
  With batched runs too, those left for later aren't tagged, so they're checked again next run. Empty or 0: no limit.

Several Sonarrs or Radarrs
  Besides the main one (SONARR_URL, SONARR_API_KEY, SONARR_IGNORE_TAG, SONARR_RENAME_COUNT, - A main instance is optional and all can be named.
  SONARR_RENAME_TAG, SONARR_RENAME_MAX), you can set up named ones: SONARR_ANIME_URL, SONARR_ANIME_API_KEY and so on
  make one called "anime" (the same for RADARR_..._URL). A run checks every one in turn, main first, then by name, each with its
  own section in the log and its own Discord post; --instance anime (or main) checks just one.
  One that can't start (see below) is skipped, and the others are still checked. A problem once
  renaming has started stops the whole run, so the rest aren't checked either.

Log and Discord
  Everything the script prints, and your answers to its questions, also go into
  logs/arr_rename.log next to the script, with the time each run started and ended, and its
  command line (but not an API key or webhook URL given on it).
  If a Discord webhook is set (DISCORD_WEBHOOK_URL or --discord-webhook), each run posts a report:
  whether it was a dry run, and one entry per rename (done, or still to do) showing the old and
  new folder name and the file names under it. If the run can't start in a Sonarr or Radarr (it
  can't be reached, it refuses the API key, its URL or API key isn't set, the --ignore-tag doesn't
  exist, ...), the reason goes in the log and in a red Discord post, before anything is changed in
  it, and the run's exit code is 1.

Settings
  Each one is read from the environment, or else from a .env file next to the script, with one
  KEY=value per line (keep it private: chmod 600 .env). Command-line options win over both.
    SONARR_URL, SONARR_API_KEY    e.g. SONARR_URL=http://192.168.1.10:8989
    RADARR_URL, RADARR_API_KEY    e.g. RADARR_URL=http://192.168.1.10:7878
                                  (http:// can be left out: 192.168.1.10:8989)
    SONARR_IGNORE_TAG             optional: leave series with this tag alone (--ignore-tag)
    RADARR_IGNORE_TAG             optional: leave movies with this tag alone (--ignore-tag)
    SONARR_RENAME_COUNT, RADARR_RENAME_COUNT
                                  optional: the batch size for batched runs, how many each run
                                  checks (--count; see "Batched runs" above)
    SONARR_RENAME_TAG, RADARR_RENAME_TAG
                                  optional: the tag batched runs mark each one checked with
                                  (--checked-tag; default rename-checked; letters, digits and - only)
    SONARR_RENAME_MAX, RADARR_RENAME_MAX
                                  optional: the rename limit, the most each run renames
                                  (--max-renames; see "Rename limit" above)
    SONARR_<NAME>_URL, ..._API_KEY, ..._IGNORE_TAG, ..._RENAME_COUNT, ..._RENAME_TAG, ..._RENAME_MAX
                                  optional: another Sonarr, called NAME (the same for RADARR_<NAME>_...;
                                  see "Several Sonarrs or Radarrs" above)
    DISCORD_WEBHOOK_URL           optional: post each run's results to this Discord webhook
                                  (Server Settings > Integrations > Webhooks)
    DISCORD_USER_ID               optional: your Discord user id, pinged when a run can't start or
                                  stops, renames need an --interactive run or were forced, or
                                  something was deleted from the arr mid-run. Turn on
                                  Developer Mode (Settings > Advanced), then right-click your name >
                                  Copy User ID.

Examples
    ./arr_rename.py sonarr     # dry run; reads SONARR_URL and SONARR_API_KEY (or .env)
    ./arr_rename.py radarr -i  # ask about each rename; reads RADARR_URL and RADARR_API_KEY
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

THROTTLE = 2.0  # seconds to wait after a rename (and its checks) before contacting the arr again
SETTLE = 0.5    # seconds to wait between refreshing an item and renaming it
POLL = 1.0      # seconds between checks on whether an arr command has finished
TIMEOUT = 3600  # seconds to wait for an arr command before giving up; giving up stops the run
RETRY = 10      # seconds to wait before trying a failed read from the arr again (once)
THREADS = 4     # reads at a time while checking: as fast as 10 against a nearby arr, with less load on it
BUSY = ("queued", "started")
CHECKED_TAG = "rename-checked"  # what marks one checked in this pass through the library (see "Batched runs")
# The oldest versions whose API has the /folder endpoint that check() asks folder names from: older ones answer 404.
# (Sonarr's first release with it is 4.0.12.2823; its develop builds have it from 4.0.11.2688.)
MIN_VERSIONS = {"sonarr": (4, 0, 11, 2688), "radarr": (5, 22, 3, 9889)}
FOLDER_FILES = 2  # Discord: files listed under a renamed folder before "… and N more files"
FILES_ONLY = 13   # Discord: files listed (per list) when only an item's files are renamed (--files or --tba)
# The differences --ignore-case and --ignore-punctuation can skip: (how the log describes it, the option)
IGNORABLE = {"capitals": ("capitals", "--ignore-case"),
             "punctuation": ("special characters", "--ignore-punctuation")}

ID_TAG = re.compile(r"[\[{](tvdb|tmdb|tvmaze|imdb)(?:id)?[-=](tt\d+|\d*)[\]}]", re.IGNORECASE)
ID_FIELDS = {"tvdb": "tvdbId", "tmdb": "tmdbId", "tvmaze": "tvMazeId", "imdb": "imdbId"}  # where the arr keeps each id
YEAR = re.compile(r"\((\d{4})\)")
# A special character (removed by unpunctuated), or an id tag or year (kept as they are)
SPECIAL = re.compile(rf"({ID_TAG.pattern}|{YEAR.pattern})|[^\w\s]|_", re.IGNORECASE)
# A "(1)" or "Part 1" ending, which Sonarr leaves out when it puts several episode titles in one file name
MULTI_PART = re.compile(r":?\s?(?:\(\d+\)|(?:Part|Pt\.?)\s?\d+)$", re.IGNORECASE)
# "TBA" as a word of its own: a space before it, and a space or the file extension after it
TBA = re.compile(r"(?<= )TBA(?= |\.\w+$|$)")


class Kind(NamedTuple):
    items: str      # API name for the items: series / movie
    files: str      # API name for their files: episodefile / moviefile
    file_param: str  # query parameter that picks one item's files: seriesId / movieId
    ids_field: str  # field that lists item ids when moving or refreshing: seriesIds / movieIds
    move: str       # name of the arr command that moves folders
    refresh: str    # name of the arr command that refreshes and rescans one item
    file_id: str    # name of the file id field in the arr's rename preview


KINDS = {
    "sonarr": Kind("series", "episodefile", "seriesId", "seriesIds", "BulkMoveSeries", "RefreshSeries", "episodeFileId"),
    "radarr": Kind("movie", "moviefile", "movieId", "movieIds", "BulkMoveMovie", "RefreshMovie", "movieFileId"),
}


class Mismatch(NamedTuple):
    item: dict
    expected: str          # the folder name the arr says it should have
    problems: list[str]    # what's wrong with the folder name; empty if nothing (or only ignored differences)
    irregular: list[str]   # what the new name would lose from the current one; --apply never does these
    blocker: str | None    # why the folder can't be renamed, if it can't
    files: tuple = ()      # files to rename, as (file id, old name, new name), relative to the folder
    unsure: tuple = ()     # --tba only: file renames that may be more than a TBA title being filled in
    ignored: tuple = ()    # differences an --ignore-... option skipped: "capitals" and/or "punctuation"
    skipped: tuple = ()    # file renames an --ignore-... option skipped: (file id, old, new, those differences)
    # How many files are in the folder, if it's going to be renamed. It's the last field so it can be
    # left out when checking whether a refresh changed the plan.
    count: int = 0


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


def fold(s: str) -> str:
    """`s` in lower case, with its accents stored one way. An accented letter like "é" can be one
    character or a letter plus an accent mark (as Macs often store file names); both look the same."""
    return unicodedata.normalize("NFC", s).casefold()


def lowered(s: str) -> str:
    """Like fold, but only lowers capitals: fold also matches different spellings, such as "ß" with
    "ss" ("Weiße" and "Weisse"), which is safe for spotting clashes but isn't a capitals change."""
    return unicodedata.normalize("NFC", s).lower()


def id_value(v):
    """An id in a form that can be compared: a number, or for other ids (like IMDb's "tt...", or ""
    when missing) the text in lower case."""
    return int(v) if isinstance(v, str) and v.isdigit() else v.casefold() if isinstance(v, str) else v


def item_id(item: dict, k: str):
    """The `k` id ("tvdb", "imdb", ...) the arr has for `item`, or None if it has none."""
    return id_value(item.get(ID_FIELDS[k])) or None


def parse(folder: str):
    """Split a folder name into its title, year and id tags: (title, year, {"tvdb": id, ...}). The
    title is in lower case, for comparing."""
    ids = {k.lower(): id_value(v) for k, v in ID_TAG.findall(folder)}
    rest = ID_TAG.sub("", folder)
    years = list(YEAR.finditer(rest))
    year = None
    if years:
        year = int(years[-1].group(1))
        rest = rest[:years[-1].start()] + rest[years[-1].end():]
    return " ".join(lowered(rest).split()), year, ids


def unpunctuated(name: str) -> str:
    """`name` with its special characters (anything other than letters, digits and spaces) removed,
    except in its year and id tags, and runs of spaces made single: "What's Up! (2020)" becomes
    "Whats Up (2020)". An accent mark stored apart from its letter (see fold), like the one making
    "か" into "が", is part of the letter, so it stays: "がっこう" mustn't become "かっこう"."""
    def kept(m):  # a year or id tag, or an accent mark
        return m.group(1) or (m.group() if unicodedata.category(m.group()).startswith("M") else "")
    return " ".join(SPECIAL.sub(kept, name).split())


def passed_over(folder: str, expected: str) -> tuple[str, ...]:
    """Why problems() let `folder` through although it isn't exactly the arr's `expected` name:
    ("capitals",) if only capitals differ, ("punctuation",) if only special characters differ, or
    both. () if the names are identical."""
    if folder == expected:
        return ()
    if lowered(folder) == lowered(expected):
        return ("capitals",)
    if unpunctuated(folder) == unpunctuated(expected):
        return ("punctuation",)
    return ("punctuation", "capitals")


def problems(folder: str, expected: str, item: dict, ignore_case: bool = False,
             ignore_punctuation: bool = False) -> tuple[list[str], list[str]]:
    """Compare `folder` with the arr's `expected` name. Returns two lists: what's wrong with the
    current name, and what the new name would lose (which makes the rename irregular). Both are
    empty if the names match. With `ignore_case`, a difference only in capitals doesn't count, and
    with `ignore_punctuation`, neither does one only in special characters (see unpunctuated)."""
    a, b = (unpunctuated(folder), unpunctuated(expected)) if ignore_punctuation else (folder, expected)
    if a == b:
        return [], []
    if lowered(a) == lowered(b):  # the same apart from capitals and/or how accents are stored (see fold)
        out = ["accents encoded differently"] if a.lower() != b.lower() else []
        if not ignore_case and unicodedata.normalize("NFC", a) != unicodedata.normalize("NFC", b):
            out.append("capitals differ")
        return out, []
    title, year, ids = parse(folder)
    want_title, want_year, want_ids = parse(expected)
    out = []
    if title != want_title:
        out.append(f"title {title!r} should be {want_title!r}")
    if year is not None and want_year is not None and year != want_year:
        out.append(f"year {year} should be {want_year}")
    elif year is None and want_year is not None:
        out.append("no year")
    for k, v in ids.items():
        if item_id(item, k) and v != item_id(item, k):
            out.append(f"{k} id {v} should be {item_id(item, k)}" if v else f"empty {k} id")
    out += [f"no {k} id" for k in want_ids if k not in ids]
    irregular = []
    if title and not want_title:
        irregular.append("the new name has no title")
    if year is not None and want_year is None:
        irregular.append(f"the new name drops the year {year}")
    irregular += [f"the new name drops the {k} id {v}" for k, v in ids.items() if k not in want_ids]
    irregular += [f"the new name has an empty {k} id" for k, v in want_ids.items() if not v]
    return out or ["formatted differently"], irregular


def listing(arr: Arr, parent: str) -> set:
    """The names of the folders inside `parent`, as the arr sees them (none if `parent` doesn't exist)."""
    dirs = arr.get("/api/v3/filesystem", path=parent + "/").get("directories", [])
    return {d["name"] for d in dirs}


def only_change(old: str, new: str) -> tuple[str, str]:
    """The part of `old` that was replaced to make `new`, and what replaced it, both widened to whole
    words: "S01E01 - TBA.mkv" -> "S01E01 - Pilot.mkv" gives ("TBA", "Pilot")."""
    p = 0
    while p < min(len(old), len(new)) and old[p] == new[p]:
        p += 1
    while p and old[p - 1].isalnum():
        p -= 1
    s = 0
    while s < min(len(old), len(new)) - p and old[-1 - s] == new[-1 - s]:
        s += 1
    while s and old[len(old) - s].isalnum():
        s -= 1
    return old[p:len(old) - s], new[p:len(new) - s]


def letters(s: str) -> str:
    """`s` with only its letters and digits, in lower case, for loose comparisons."""
    return re.sub(r"[\W_]", "", s).casefold()


def just_titles(text: str, titles) -> bool:
    """Whether `text` is nothing but the episode `titles` (in episode order), the way Sonarr writes
    them in a file name. Capitals and anything other than letters and digits are ignored, so
    "World's" matches "Worlds". `text` may be one title, or all of them for a file holding several
    episodes, either joined together or merged the way Sonarr merges them: without the "(1)" /
    "Part 1" endings, and each title once ("Pilot (1)" + "Pilot (2)" -> "Pilot"). If `text` ends in
    "...", it may be any of these cut short to fit."""
    titles = [t for t in titles if t and t != "TBA"]
    merged = dict.fromkeys(MULTI_PART.sub("", t).strip() for t in titles)
    forms = {letters(t) for t in titles} | {letters("".join(titles)), letters("".join(merged))}
    got = letters(text)
    if text.rstrip().endswith(("...", "…")):
        return bool(got) and any(f.startswith(got) for f in forms)
    return bool(got) and got in forms


def tba_kind(old: str, new: str, titles) -> str | None:
    """What kind of change renaming the file `old` to `new` is. `titles` are the file's episode titles.
    Only a "TBA" that's a word of its own counts (see TBA): "FOOTBALL" and "TBAx" have none.

    "tba"     It only fills in an episode title that used to be "TBA". Either putting "TBA" back in
              place of the titles in `new` gives exactly `old`, or the only change is the word
              "TBA" being replaced by the titles (perhaps tidied up, see just_titles). Also, none
              of the new titles may contain "TBA".
    "unsure"  It looks TBA-related but isn't clear-cut: a new title contains "TBA" (like "This
              episode title is TBA"), a "TBA" disappears some other way
              ("Mask. TBA Again" -> "Mask. Again"), more than the title changes ("TBA" -> "Pilot
              [WEBDL-1080p]"; naming format changes are for --files), or there are no titles to
              check against.
    None      It has nothing to do with TBA (`old` has no "TBA"), or only capitals and punctuation
              change ("TBA" -> "Tba", "episode title" -> "Episode Title!")."""
    old, new = posixpath.basename(old), posixpath.basename(new)
    if letters(old) == letters(new) or not TBA.search(old):
        return None
    if any(TBA.search(f" {t}") and t not in old for t in titles):  # " ": the title follows one in the file name
        return "unsure"
    known = sorted({t for t in titles if t and t != "TBA"}, key=len, reverse=True)
    # every title back to "TBA" in one go, longest first, so a short title can't match inside a longer one
    back = re.sub("|".join(map(re.escape, known)), "TBA", new) if known else new
    if back == old and back != new:
        return "tba"
    was, now = only_change(old, new)
    if was == "TBA" and not TBA.search(f" {now} ") and just_titles(now, titles):
        return "tba"
    return "unsure" if len(TBA.findall(old)) > len(TBA.findall(new)) else None


def tba_split(arr: Arr, series_id: int, renames) -> tuple[tuple, tuple]:
    """Sort the file `renames` ((file id, old, new) each) into two groups: TBA titles being filled
    in, and unsure ones (see tba_kind). Renames that have nothing to do with TBA are dropped. The
    episode titles are only read when an old name has a TBA in it: the arr's rename preview only
    lists files whose name would change, so a file still waiting for its title isn't in it, and
    most series have no TBA rename at all."""
    renames = tuple(r for r in renames if TBA.search(posixpath.basename(r[1])))
    if not renames:
        return (), ()
    titles: dict[int, list] = {}
    # in season and episode order, the order Sonarr uses for a file holding several episodes
    for ep in sorted(arr.get("/api/v3/episode", seriesId=series_id),
                     key=lambda e: (e.get("seasonNumber", 0), e.get("episodeNumber", 0))):
        if ep.get("episodeFileId"):
            titles.setdefault(ep["episodeFileId"], []).append(ep.get("title") or "")
    kinds = [tba_kind(old, new, titles.get(i, [])) for i, old, new in renames]
    return (tuple(r for r, k in zip(renames, kinds) if k == "tba"),
            tuple(r for r, k in zip(renames, kinds) if k == "unsure"))


def check(arr: Arr, kind: Kind, item: dict, files: str | None, taken: set, names: set,
          ignore_case: bool = False, ignore_punctuation: bool = False, roots: set | None = None) -> Mismatch | None:
    """What needs renaming for `item`: its folder, its files, or both. None if nothing does.

    `taken` holds the paths (in lower case) of every item in the arr, and `names` the folders next
    to this one, so a new name can't clash with them. `roots` are the arr's root folders: a folder
    not directly inside one can't be renamed in place (see rename). When the folder is renamed, every file in it
    that needs a new name is renamed too. When it isn't, `files` decides which files are checked:
    "all" checks every file, "tba" only those whose episode title was TBA and now has a real one
    (see tba_kind), and None none.

    With `ignore_case` / `ignore_punctuation`, a folder whose name only differs in capitals / special
    characters isn't renamed. It's still returned, with the reason in `ignored`, so the log can
    say it was skipped. File renames that only change those are skipped too, and listed in `skipped`
    (--tba never renames for them anyway, see tba_kind)."""
    parent, folder = posixpath.split(item["path"].rstrip("/"))
    expected = arr.get(f"/api/v3/{kind.items}/{item['id']}/folder")["folder"]
    found, irregular = problems(folder, expected, item, ignore_case, ignore_punctuation)
    blocker, ignored = None, ()
    if not found:
        ignored = passed_over(folder, expected)
    elif not expected.strip() or "/" in expected:
        blocker = f"the arr gives an unusable folder name ({expected!r})"
    elif roots and parent not in roots:  # the arr's editor refuses anything but a root folder
        blocker = f"it's inside {parent}, not directly in a root folder, and the arr can only rename it by moving it out"
    # (a rename that only changes capitals keeps the item's own path, which isn't a clash)
    elif fold(expected) != fold(folder) and fold(posixpath.join(parent, expected)) in taken:
        blocker = "another series/movie in the arr already uses that folder"
    elif fold(expected) in {fold(n) for n in names if n != folder}:  # ignoring capitals, to be safe
        blocker = "a folder with that name already exists"
    renames = unsure = ()
    if files or (found and not blocker):
        renames = tuple((r[kind.file_id], r["existingPath"], r["newPath"])
                        for r in arr.get("/api/v3/rename", **{kind.file_param: item["id"]}))
        if files == "tba" and not (found and not blocker) and renames:
            renames, unsure = tba_split(arr, item["id"], renames)
    skipped = tuple((i, old, new, passed_over(old, new)) for i, old, new in renames
                    if not problems(old, new, {}, ignore_case, ignore_punctuation)[0])
    renames = tuple(r for r in renames if r[0] not in {s[0] for s in skipped})
    if not (found or renames or unsure or ignored or skipped):
        return None
    count = len(on_disk(arr, item["path"].rstrip("/"))) if found and not blocker else 0
    return Mismatch(item, expected, found, irregular, blocker, renames, unsure, ignored, skipped, count)


def root_folders(arr: Arr) -> set:
    """The arr's root folders, without a trailing slash."""
    return {r["path"].rstrip("/") or "/" for r in arr.get("/api/v3/rootfolder")}


def plan(arr: Arr, kind: Kind, files: str | None = None, ignore_tag: int | None = None, ignore_case: bool = False,
         ignore_punctuation: bool = False, roots: set | None = None, pick=None):
    """Check every item in the arr. Returns four things: what needs renaming (a list of Mismatch),
    the items whose folder isn't on disk, the items skipped because they have the tag `ignore_tag`
    (a tag id), and the paths of every item in lower case. `roots` are the arr's root folders (see
    check). With `pick`, only the items pick(the rest) returns are checked (see "Batched runs"). The arr is
    read THREADS requests at a time; nothing is changed here, and everything comes back in the items' order."""
    items = sorted(arr.get(f"/api/v3/{kind.items}"), key=lambda i: fold(i.get("sortTitle") or i["title"]))
    windows = next((i["path"] for i in items if "\\" in i["path"] or re.match(r"[A-Za-z]:", i["path"])), None)
    if windows:  # every folder would look missing, as its path isn't split up right
        raise CouldntStart(f"the arr uses Windows paths (like {windows!r}), which aren't supported")
    taken = {fold(i["path"].rstrip("/")) for i in items}
    ignored = [item for item in items if ignore_tag is not None and ignore_tag in item.get("tags", [])]
    rest = [item for item in items if item not in ignored]
    # (item, its parent folder, its folder name)
    todo = [(item, *posixpath.split(item["path"].rstrip("/"))) for item in (pick(rest) if pick else rest)]
    with ThreadPoolExecutor(THREADS) as pool:
        parents = list(dict.fromkeys(parent for _, parent, _ in todo))
        listings = dict(zip(parents, pool.map(lambda parent: listing(arr, parent), parents)))
        # exact match: a folder with other capitals isn't the arr's
        not_on_disk = [item for item, parent, folder in todo if folder not in listings[parent]]
        present = [(item, parent) for item, parent, folder in todo if folder in listings[parent]]
        checked = pool.map(lambda t: check(arr, kind, t[0], files, taken, listings[t[1]], ignore_case,
                                           ignore_punctuation, roots), present)
        mismatches = [m for m in checked if m]
    return mismatches, not_on_disk, ignored, taken


def retag(arr: Arr, kind: Kind, ids, tag: int, how: str) -> None:
    """Add (`how` "add") or take off ("remove") tag `tag` on the series or movies `ids`."""
    if ids:
        arr.call("PUT", f"/api/v3/{kind.items}/editor", {kind.ids_field: list(ids), "tags": [tag], "applyTags": how})


def refresh(arr: Arr, kind: Kind, item_id: int) -> None:
    """Ask the arr to refresh the item (reload its information and rescan its files) and wait until
    that's done. Raises OSError if the refresh doesn't complete, since the arr's information may
    then be out of date."""
    cmd = arr.call("POST", "/api/v3/command", {"name": kind.refresh, kind.ids_field: [item_id]})
    status = wait_for(arr, command_id=cmd["id"])
    if status != "completed":
        raise OSError(f"the arr's refresh ended as {status!r}, not 'completed' (see the arr's logs)")


class Gone(Exception):
    """The item has been deleted from the arr since it was checked."""


def gone(arr: Arr, kind: Kind, item_id: int) -> bool:
    """Whether the item has been deleted from the arr: the arr answers "not found" for it."""
    try:
        arr.get(f"/api/v3/{kind.items}/{item_id}")
    except OSError as e:
        return isinstance(e.__cause__, urllib.error.HTTPError) and e.__cause__.code == 404
    return False


def recheck(arr: Arr, kind: Kind, m: Mismatch, files: str | None, taken: set, ignore_case: bool = False,
            ignore_punctuation: bool = False, roots: set | None = None) -> tuple[bool, Mismatch | None]:
    """Refresh `m`'s item in the arr and check it again. Returns whether its folder is still on
    disk, and what needs renaming now (None if nothing does). Raises Gone if the item has been
    deleted from the arr (its refresh then fails)."""
    item_id = m.item["id"]
    try:
        refresh(arr, kind, item_id)
        item = arr.get(f"/api/v3/{kind.items}/{item_id}")
    except OSError:
        if gone(arr, kind, item_id):
            raise Gone() from None
        raise
    parent, folder = posixpath.split(item["path"].rstrip("/"))
    names = listing(arr, parent)  # read again: a rename earlier in this run may have used a name
    if folder not in names:
        return False, None
    return True, check(arr, kind, item, files, taken, names, ignore_case, ignore_punctuation, roots)


def wait_for(arr: Arr, name: str | None = None, command_id: int | None = None) -> str | None:
    """Wait until the arr command `command_id` has finished, or, given `name` instead, until no
    command with that name is queued or running. Returns how `command_id` ended ("completed",
    "failed", ...). Raises OSError if it still isn't done after TIMEOUT seconds, so a stuck command
    stops the run instead of keeping it waiting forever."""
    start = note = time.monotonic()
    while True:
        status = None
        if command_id is not None:
            cmd = arr.get(f"/api/v3/command/{command_id}")
            status, name = cmd["status"], cmd.get("name")
            busy = status in BUSY
        else:
            busy = any(c.get("name") == name and c.get("status") in BUSY for c in arr.get("/api/v3/command"))
        if not busy:
            return status
        if time.monotonic() - start >= TIMEOUT:
            raise OSError(f"the arr's {name} command still wasn't done after {TIMEOUT // 60} minutes "
                          "(see System > Tasks in the arr)")
        time.sleep(POLL)
        if time.monotonic() - note >= 30:
            note = time.monotonic()
            print(f"    still waiting for the arr ({int(note - start)}s)")


def tracked(arr: Arr, kind: Kind, item_id: int) -> dict:
    """The files the arr has on record for the item, as {file id: path relative to its folder}."""
    return {f["id"]: f["relativePath"] for f in arr.get(f"/api/v3/{kind.files}", **{kind.file_param: item_id})}


def on_disk(arr: Arr, path: str) -> set:
    """The video files under `path`, as the arr sees them on disk, relative to `path`."""
    return {f["relativePath"] for f in arr.get("/api/v3/filesystem/mediafiles", path=path)}


def listed(paths) -> str:
    return "".join(f"\n        {p}" for p in sorted(paths))  # all of them: fixing things by hand needs the full list


def rename(arr: Arr, kind: Kind, m: Mismatch) -> tuple[bool, str]:
    """Rename one item's folder through the arr, then check no files went missing. Returns (ok,
    label): (False, label) on any problem, and (True, "RESCAN NEEDED") if the arr's records needed
    a refresh to match the files again."""
    item_id = m.item["id"]
    old = m.item["path"].rstrip("/")
    parent = posixpath.dirname(old)
    # A refresh running during the move (like the arr's scheduled one) can save the old path back,
    # and one still running would change the arr's records after they're read below
    wait_for(arr, kind.refresh)
    db_before = tracked(arr, kind, item_id)
    disk_before = on_disk(arr, old)
    missing_before = set(db_before.values()) - disk_before

    arr.call("PUT", f"/api/v3/{kind.items}/editor",
             {kind.ids_field: [item_id], "rootFolderPath": parent, "moveFiles": True})
    wait_for(arr, kind.move)

    new = arr.get(f"/api/v3/{kind.items}/{item_id}")["path"].rstrip("/")
    if new == old:
        print(f"    FAILED: the arr kept the old folder, so the move didn't happen (see the arr's logs). "
              f"{len(on_disk(arr, old))} of {len(disk_before)} files are still there.")
        return False, "FAILED"
    disk_after = on_disk(arr, new)
    left_behind = on_disk(arr, old)
    if fold(new) == fold(old) and left_behind == disk_after:  # a capitals change on a disk that ignores capitals:
        left_behind = set()  # the old name finds the new folder, so nothing was left behind
    if left_behind or disk_after != disk_before:
        print(f"    FAILED: files didn't all arrive in {new}. Not refreshing, because a refresh would drop "
              "the missing ones from the arr. Move them by hand, then refresh it in the arr.")
        if left_behind:
            print(f"    LEFT BEHIND in {old}:{listed(left_behind)}")
        if disk_before - disk_after - left_behind:
            print(f"    MISSING:{listed(disk_before - disk_after - left_behind)}")
        return False, "FAILED"

    db_after = tracked(arr, kind, item_id)
    label = ""
    if db_after != db_before:
        label = "RESCAN NEEDED"
        print(f"    NEEDS REFRESH: the arr had {len(db_before)} files on record before the move and "
              f"{len(db_after)} after; refreshing {m.item['title']!r}")
        refresh(arr, kind, item_id)
        db_after = tracked(arr, kind, item_id)
        print(f"    REFRESHED: the arr now has {len(db_after)} files on record")
    lost = (set(db_before.values()) - missing_before) - set(db_after.values())
    if lost:
        print(f"    FAILED: the arr lost track of {len(lost)} files:{listed(lost)}")
        return False, "FILES MISSING"
    print(f"    done: now {posixpath.basename(new)} ({len(disk_after)} files, all accounted for)")
    return True, label


def rename_files(arr: Arr, kind: Kind, item_id: int, only: set | None = None) -> tuple[bool, str, tuple]:
    """Rename an item's files with the arr's RenameFiles command (given `only`, just the files with
    those ids), then check nothing went missing. Returns (ok, label, renamed), where `renamed` is
    (file id, old, new) for each file renamed. Any problem gives (False, label, ()). The label is
    "RESCAN NEEDED" if the arr's records needed a refresh to match the files."""
    path = arr.get(f"/api/v3/{kind.items}/{item_id}")["path"].rstrip("/")
    renames = {r[kind.file_id]: (r["existingPath"], r["newPath"])
               for r in arr.get("/api/v3/rename", **{kind.file_param: item_id})
               if only is None or r[kind.file_id] in only}
    if not renames:
        print("    files: nothing to rename any more")
        return True, "", ()
    wait_for(arr, kind.refresh)  # a refresh running during the renames can undo them (see rename)
    db_before = tracked(arr, kind, item_id)
    disk_before = on_disk(arr, path)
    olds = {old for old, _ in renames.values()}

    cmd = arr.call("POST", "/api/v3/command", {"name": "RenameFiles", kind.file_param: item_id, "files": list(renames)})
    wait_for(arr, command_id=cmd["id"])

    disk_after = on_disk(arr, path)
    missing = [old for old, new in renames.values() if old not in disk_after and new not in disk_after]
    missing += sorted((disk_before - olds) - disk_after)
    not_renamed = [old for old, new in renames.values() if old in disk_after and new not in disk_after]
    if missing:
        print(f"    FAILED: {len(missing)} files are gone from {path}. Not refreshing, because a refresh would "
              f"drop them from the arr:{listed(missing)}")
        return False, "FILES MISSING", ()
    if not_renamed:
        print(f"    FAILED: {len(not_renamed)} files weren't renamed (see the arr's logs):{listed(not_renamed)}")
        return False, "FAILED", ()

    want = {i: renames[i][1] if i in renames else rel for i, rel in db_before.items()}
    db_after = tracked(arr, kind, item_id)
    label = ""
    if db_after != want:
        label = "RESCAN NEEDED"
        print("    NEEDS REFRESH: the arr's records don't match the renamed files; refreshing")
        refresh(arr, kind, item_id)
        db_after = tracked(arr, kind, item_id)
        print(f"    REFRESHED: the arr now has {len(db_after)} files on record")
    lost = {rel for rel in want.values() if rel in disk_after} - set(db_after.values())
    if lost:
        print(f"    FAILED: the arr lost track of {len(lost)} files:{listed(lost)}")
        return False, "FILES MISSING", ()
    print(f"    done: renamed {len(renames)} files, all accounted for")
    return True, label, tuple((i, old, new) for i, (old, new) in renames.items())


def tally(noun: str, nouns: str, folders: int, moved: int, items: int, files: int) -> str:
    """The two summary lines, e.g. "1 Series folder with 12 files" and "2 Series with file renames -
    8 files total". An item whose folder is renamed counts only on the first, its own file renames too."""
    return (f"{folders} {noun} folder{'' if folders == 1 else 's'} with {moved} file{'' if moved == 1 else 's'}\n"
            f"{items} {noun if items == 1 else nouns} with file renames - {files} file{'' if files == 1 else 's'} total")


def ask(prompt: str, choices: str = "ynaq") -> str:
    """Return one of `choices` (default y/n/a/q). Enter means no; a closed stdin or Ctrl-C means quit."""
    while True:
        try:
            answer = input(prompt).strip().lower()[:1] or "n"
        except (EOFError, KeyboardInterrupt):
            print()
            return "q"
        if answer in choices:
            return answer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    apps = ap.add_subparsers(dest="app", required=True, metavar="{radarr,sonarr}", help="which arr to check")
    for name in sorted(KINDS):
        things = "series" if name == "sonarr" else "movies"
        p, var = apps.add_parser(name, help=f"check the {things} in {name.capitalize()}"), name.upper()
        p.add_argument("--instance", metavar="NAME",
                       help=f"only check this {name.capitalize()}: NAME from ${var}_<NAME>_URL, or main for ${var}_URL "
                            f"(default: every one set up, in turn)")
        p.add_argument("--url", help=f"{name.capitalize()} URL (http:// can be left out: 192.168.1.10:8989), for "
                                     f"one instance (default: ${var}_URL, or ${var}_<NAME>_URL with --instance)")
        p.add_argument("--api-key", help=f"its API key (default: ${var}_API_KEY, or ${var}_<NAME>_API_KEY)")
        mode = p.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true",
                          help="do every rename, except irregular ones, which need -i (default is a dry run)")
        mode.add_argument("-i", "--interactive", action="store_true",
                          help="ask about each rename (y/n/all/quit) and do the ones you accept")
        files = p.add_mutually_exclusive_group()
        files.add_argument("--files", action="store_true",
                           help="check the file names of everything, not only of renamed folders, and rename them")
        if name == "sonarr":  # --tba is Sonarr only: movies don't have episode titles
            files.add_argument("--tba", action="store_true",
                               help="like --files, but only rename files whose episode title was TBA and now has one")
        p.set_defaults(tba=False)
        p.add_argument("--ignore-case", action="store_true",
                       help="don't rename folders or files whose name only differs from the arr's in capitals")
        p.add_argument("--ignore-punctuation", action="store_true",
                       help="don't rename folders or files whose name only differs from the arr's in special "
                            "characters, like ! ' : (anything but letters, digits and spaces, outside the year and "
                            "id tags)")
        p.add_argument("--force", action="store_true",
                       help="with --apply, also do the renames that need -i (irregular folders, TBA? files)")
        p.add_argument("--ignore-tag", metavar="TAG", help=f"leave {things} with this tag alone, in every instance "
                                                            f"(default: ${var}_IGNORE_TAG, or ${var}_<NAME>_IGNORE_TAG)")
        p.add_argument("--count", type=int, metavar="N",
                       help=f"batched runs: check the next N {things} not checked yet in this pass, tagging each one "
                            f"(default: ${var}_RENAME_COUNT, or ${var}_<NAME>_RENAME_COUNT; 0 or unset: every one)")
        p.add_argument("--max-renames", type=int, metavar="N",
                       help=f"rename at most N {things} each run, leaving the rest for the next runs "
                            f"(default: ${var}_RENAME_MAX, or ${var}_<NAME>_RENAME_MAX; 0 or unset: no limit)")
        p.add_argument("--checked-tag", metavar="TAG",
                       help=f"batched runs: the tag that marks one checked, letters, digits and - only (default: "
                            f"${var}_RENAME_TAG, or ${var}_<NAME>_RENAME_TAG, or {CHECKED_TAG})")
        p.add_argument("--discord-webhook", default=os.environ.get("DISCORD_WEBHOOK_URL"), metavar="URL",
                       help="post the results to this Discord webhook (default: $DISCORD_WEBHOOK_URL)")
        p.add_argument("--discord-user", default=os.environ.get("DISCORD_USER_ID"), metavar="ID",
                       help="Discord user id to ping when a run can't start or stops, renames need an --interactive "
                            "run or were forced, or something was deleted from the arr mid-run "
                            "(default: $DISCORD_USER_ID)")
    args, unknown = ap.parse_known_args(argv)
    if unknown:  # argparse's own message would show them as typed, and it goes in the log: a mistyped --apikey KEY too
        ap.error(f"unrecognized arguments: {masked_command(unknown)}")
    if args.force and not args.apply:
        ap.error("--force only works with --apply")
    if args.count is not None and args.count < 0:
        ap.error("--count can't be less than 0")
    if args.max_renames is not None and args.max_renames < 0:
        ap.error("--max-renames can't be less than 0")
    if args.discord_user and not args.discord_user.isdigit():
        ap.error(f"the Discord user id must be a number (Copy User ID in Discord), not {args.discord_user!r}")
    modes = ["folders"] + (["files"] if args.files else []) + (["tba"] if args.tba else [])
    modes += ["ignore case"] if args.ignore_case else []
    modes += ["ignore punctuation"] if args.ignore_punctuation else []
    modes += ["force"] if args.force else []
    modes.append("apply" if args.apply else "interactive" if args.interactive else "dry run")
    modes = ", ".join(modes)

    configured = instances(args.app)
    if args.instance:
        name = None if args.instance.lower() == "main" else args.instance.lower()
        if not (args.url or name in configured):
            ap.error(f"no {args.app.capitalize()} called {args.instance!r}: set {settings(args.app, name)[0]}_URL "
                     f"(set up: {', '.join(n or 'main' for n in configured) or 'none'})")
        names = [name]
    elif args.url or args.api_key or not configured:
        names = [None]  # --url / --api-key are for one instance: the main one, unless --instance says otherwise
    else:
        names = configured
    code, failed = 0, []  # failed: the ones that couldn't start
    for n, name in enumerate(names):
        label = settings(args.app, name)[1]
        if len(names) > 1:
            print(("\n" if n else "") + f"----- {label} -----")
        try:
            code = run(args, name, modes)
        except CouldntStart as e:  # nothing was changed in it, so the others can still be checked
            couldnt_start(args, label, modes, str(e))
            failed.append(label)
            continue
        if code:
            if names[n + 1:]:
                print(f"Stopping, so these aren't checked either: {', '.join(settings(args.app, m)[1] for m in names[n + 1:])}")
            break
    if failed and len(names) > 1:  # (with just one, why is just above)
        print(f"\nCouldn't start: {', '.join(failed)} (see above)")
    return 1 if code or failed else 0


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
    """Why the run can't start in a Sonarr or Radarr. It's found out before anything is changed in it."""


def couldnt_start(args, label: str, modes: str, reason: str) -> None:
    """Report that the run couldn't start in the Sonarr or Radarr `label`: log the reason, and post
    it to Discord with a ping (otherwise a scheduled run nobody watches would fail unnoticed)."""
    print(f"Couldn't start the run: {reason}", file=sys.stderr)
    if args.discord_webhook:
        try:
            discord_send(args.discord_webhook, f"{label} Renaminatorr: {modes}",
                         f"**Couldn't start the run:** {discord_escape(reason)}", [], RED,
                         script_footer(__file__), args.discord_user or "", "the run couldn't start")
        except Exception as e:  # a Discord problem mustn't stop the run going on to the next one
            print(f"WARNING: couldn't post to Discord: {e if isinstance(e, OSError) else repr(e)}", file=sys.stderr)


def run(args, name: str | None, modes: str) -> int:
    """Check, and with --apply or -i rename, one instance of the arr: the main one, or `name`.
    Returns the exit code: 1 if a problem stopped it. Raises CouldntStart if the run can't start."""
    files_mode = "all" if args.files else "tba" if args.tba else None
    prefix, arr_name = settings(args.app, name)
    suffix = f" {name.title()}" if name else ""  # after the app's own name, in the Discord title
    url = args.url or os.environ.get(f"{prefix}_URL")
    api_key = args.api_key or os.environ.get(f"{prefix}_API_KEY")
    if not (url and api_key):
        raise CouldntStart(f"no {arr_name} URL or API key: set {prefix}_URL and {prefix}_API_KEY (e.g. in .env), "
                           "or pass --url and --api-key")
    url = full_url(url)
    count = args.count
    if count is None:
        raw = (os.environ.get(f"{prefix}_RENAME_COUNT") or "").strip()
        if raw and not raw.isdigit():
            raise CouldntStart(f"{prefix}_RENAME_COUNT must be a whole number (0 or more), not {raw!r}")
        count = int(raw or 0)
    most = args.max_renames
    if most is None:
        raw = (os.environ.get(f"{prefix}_RENAME_MAX") or "").strip()
        if raw and not raw.isdigit():
            raise CouldntStart(f"{prefix}_RENAME_MAX must be a whole number (0 or more), not {raw!r}")
        most = int(raw or 0)
    checked_tag = (args.checked_tag or os.environ.get(f"{prefix}_RENAME_TAG") or "").strip().lower() or CHECKED_TAG
    tag_name = args.ignore_tag or os.environ.get(f"{prefix}_IGNORE_TAG")
    if count and not re.fullmatch(r"[a-z0-9-]+", checked_tag):  # or the arr refuses to make it, and no run ever moves on
        raise CouldntStart(f"the checked tag {checked_tag!r} (--checked-tag or {prefix}_RENAME_TAG) can only have "
                           "letters, digits and -, as the arr won't make any other tag")
    if count and tag_name and tag_name.strip().lower() == checked_tag:
        raise CouldntStart(f"the checked tag {checked_tag!r} can't also be the ignore tag (--ignore-tag or "
                           f"{prefix}_IGNORE_TAG), or each one a run checks would be left alone from then on")
    arr = Arr(url, api_key)
    things = "series" if args.app == "sonarr" else "movies"
    changing = args.apply or args.interactive
    rounds = {}  # batched: "pool" (those it could check), "untagged" (not checked yet this pass), "picked", "rotated"
    try:
        try:
            system = arr.get("/api/v3/system/status")
        except OSError as e:
            if isinstance(e.__cause__, urllib.error.HTTPError) and e.__cause__.code == 404:
                raise CouldntStart(f"{url} doesn't look like {arr_name}: it has no /api/v3/system/status ({e})") from e
            raise
        app, version = system.get("appName", ""), str(system.get("version") or "")
        if app.lower() != args.app:
            raise CouldntStart(f"{url} is {f'{app}, ' if app else ''}not {arr_name}")
        have, need = tuple(map(int, re.findall(r"\d+", version)[:4])), MIN_VERSIONS[args.app]
        if have and have < need:  # (one whose version can't be read is let through)
            raise CouldntStart(f"{app} {version} is too old: update it to {'.'.join(map(str, need))} or newer. Older "
                               "ones can't say what a folder should be called (their API has no /folder endpoint)")
        kind = KINDS[args.app]
        print(f"Reading {things} from {app}")
        ignore_tag = None
        tags = {t["label"].lower(): t["id"] for t in arr.get("/api/v3/tag")} if tag_name or count else {}
        if tag_name:
            if tag_name.lower() not in tags:
                raise CouldntStart(f"{app} has no tag {tag_name!r}, so nothing would be ignored. "
                                   f"Its tags: {', '.join(sorted(tags)) or 'none'}")
            ignore_tag = tags[tag_name.lower()]
            print(f"Ignoring {things} tagged {tag_name!r}")
        roots = root_folders(arr)

        def pick(pool):
            """The next `count` of `pool` not checked yet in this pass (from the top, once they all have been)."""
            untagged = [i for i in pool if tags.get(checked_tag) not in i.get("tags", [])]
            rounds.update(pool=pool, rotated=bool(pool) and not untagged)
            rounds["untagged"] = pool if rounds["rotated"] else untagged
            rounds["picked"] = rounds["untagged"][:count]
            return rounds["picked"]
        mismatches, not_on_disk, ignored, taken = plan(arr, kind, files_mode, ignore_tag, args.ignore_case,
                                                       args.ignore_punctuation, roots, pick if count else None)
        if rounds.get("rotated"):  # every one checked: take the tag off them and start over
            print(f"Every one of the {things} has been checked: {'starting' if changing else 'a run that changes things starts'} over")
            if changing:
                retag(arr, kind, [i["id"] for i in rounds["pool"]], tags[checked_tag], "remove")
        if rounds:
            last = len(rounds["picked"]) == len(rounds["untagged"])
            print(f"{len(rounds['pool']):,} {things}, {len(rounds['untagged']):,} still to check this pass: "
                  f"checking {len(rounds['picked']):,} " + ("(the last of this pass: the tag then comes off them all)"
                                                           if last else f"(tagged {checked_tag!r} once checked)"))
    except CouldntStart:
        raise
    except Exception as e:  # e.g. the arr can't be reached, refuses the API key, or sends a reply we can't read
        if not isinstance(e, OSError):
            traceback.print_exc()  # not a connection problem, so maybe a bug: put the details in the log
        raise CouldntStart(str(e) if isinstance(e, OSError) else repr(e)) from e

    # The Discord entries, as (title, diff): renames left for --interactive, and renames done (in a dry run, to do)
    not_renamed, renames = [], []

    def change(m: Mismatch, folder: bool, note: str = "", took_unsure: bool = False, held: bool = False,
               gone: bool = False) -> None:
        """Add `m` to the Discord report. What gets renamed goes in `renames`, with `note` after the
        title (like " - FAILED"). What's left for --interactive goes in `not_renamed`: the whole
        item if `held` (an irregular folder that wasn't renamed), and its TBA? files unless
        `took_unsure` (they were renamed). An irregular folder's entry says what the new name
        loses, both when it's held and when --force renamed it anyway. If `gone` (deleted from the
        arr mid-run), `not_renamed` gets what was planned for it, none of which was done."""
        title = discord_escape(m.item["title"])
        old = posixpath.basename(m.item["path"].rstrip("/"))
        label = "TBA files:" if args.tba and not folder else "Files:"  # a renamed folder's files aren't only TBA ones
        limit = FOLDER_FILES if folder else FILES_ONLY
        pairs = lambda files: [(o, n) for _, o, n in files]  # noqa: E731
        if gone:
            not_renamed.append((f"{title} - GONE", discord_diff(old, m.expected if folder else old,
                                                               [(label, pairs(m.files + m.unsure))],
                                                               f"Deleted from {app} mid-run", limit)))
            return
        loses = discord_escape("; ".join(m.irregular)) if folder and m.irregular else ""  # why it's irregular
        if not held and (folder or m.files or took_unsure):
            groups = [(label, pairs(m.files) + pairs(m.unsure if took_unsure and not args.force else ())),
                      ("Forced:", pairs(m.unsure) if took_unsure and args.force else [])]
            renames.append((title + note, discord_diff(old, m.expected if folder else old, groups,
                                                       loses if args.force else "", limit)))
        unsure = () if took_unsure else m.unsure
        if held or unsure:
            why = ", ".join((["IRREGULAR"] if held else []) + (["TBA?"] if unsure else []))
            groups = [(label, pairs(m.files) if held else []), ("Interaction needed:", pairs(unsure))]
            not_renamed.append((f"{title} - {why}", discord_diff(old, m.expected if held else old, groups, loses,
                                                                 limit)))

    def show(m: Mismatch) -> None:
        item = m.item
        ids = ", ".join(f"{k} {item_id(item, k)}" for k in ID_FIELDS if item_id(item, k))
        print(f"    ARR    {item['title']!r} ({item.get('year') or 'no year'}), {ids or 'no ids'}")
        if m.problems:
            print(f"    -> {m.expected}")
            print(f"    {'; '.join(m.problems)}")
        if m.ignored:
            what, options = zip(*(IGNORABLE[k] for k in m.ignored))
            print(f"    IGNORED ({', '.join(options)}): only {' and '.join(what)} differ from {m.expected}")
        if m.blocker:
            print(f"    CAN'T RENAME: {m.blocker}")
        elif m.irregular:
            print(f"    IRREGULAR: {'; '.join(m.irregular)}")
        if m.problems and not m.blocker and not m.files and not m.skipped:  # renamed, but none of its files need to be
            print("    files: already named correctly")
        for word, files in (("FILE", m.files), ("TBA?", m.unsure)):  # every file, however many, so the log has them all
            for _, old, new in files:
                print(f"    {word} {old}\n      -> {new}")
        for _, old, new, why in m.skipped:
            what, options = zip(*(IGNORABLE[k] for k in why))
            print(f"    IGNORED FILE ({', '.join(options)}): only {' and '.join(what)} differ\n"
                  f"      {old}\n      -> {new}")
        if m.unsure:
            print(f"    TBA?: {len(m.unsure)} may not be a TBA title being filled in, so they need -i")

    def to_do(m: Mismatch) -> bool:
        """Whether `m` needs anything done: its folder or files renamed. (It may only be listed, as
        ignored or as a folder that can't be renamed.)"""
        return bool(m.problems) and not m.blocker or bool(m.files or m.unsure)

    def renames_some(m: Mismatch) -> bool:
        """Whether this run renames any of `m`, rather than only holding it for --interactive (a dry run:
        whether --apply would)."""
        if args.interactive or args.force:
            return to_do(m)
        folder = bool(m.problems) and not m.blocker
        return bool(folder and not m.irregular or not folder and m.files)  # an irregular folder's files wait with it

    renamed = declined = needs_i = not_reviewed = up_to_date = forced = deleted = 0
    # Counts for the summary, of what was renamed (done) and what waits for --interactive (need):
    # folders, the files in those folders, items with file renames, and files renamed
    done, need = [0, 0, 0, 0], [0, 0, 0, 0]
    stopped = rename_all = False
    unchecked = set()  # with a count: the ids not to tag, as the run stopped at them or before them, or they're gone
    later = set()  # with --max-renames: the ids of those left for the next runs, as this one renamed its most
    would = 0  # a dry run: how many --apply would rename
    last_done = None  # when the previous rename and its checks finished (see THROTTLE)
    for i, m in enumerate(mismatches, 1):
        if most and renames_some(m) and (renamed if changing else would) >= most:
            if not later:
                print(f"Reached the rename limit of {most:,} (--max-renames): the rest are left for the next runs")
            print(f"[{i}/{len(mismatches)}] LATER  {m.item['path'].rstrip('/')}")
            later.add(m.item["id"])
            unchecked.add(m.item["id"])
            continue
        print(f"[{i}/{len(mismatches)}] FOLDER {m.item['path'].rstrip('/')}")
        show(m)
        folder = bool(m.problems) and not m.blocker  # True if the folder itself will be renamed
        if not (folder or m.files or m.unsure):
            continue
        if not changing:
            change(m, folder, held=folder and bool(m.irregular))
            would += renames_some(m)
            continue
        if last_done is not None:  # give the arr THROTTLE seconds after the previous rename, before the refresh below
            time.sleep(max(0.0, THROTTLE - (time.monotonic() - last_done)))
            last_done = None
        print("    refreshing the arr's metadata to check it still needs renaming")
        try:
            present, fresh = recheck(arr, kind, m, files_mode, taken, args.ignore_case, args.ignore_punctuation, roots)
        except Gone:
            print(f"    GONE: deleted from {app} mid-run: skipped")
            deleted += 1
            unchecked.add(m.item["id"])
            change(m, folder, gone=True)
            continue
        except Exception as e:  # any error, not just connection ones: stop the run, but still send the report
            print(f"    FAILED: couldn't refresh it: {e if isinstance(e, OSError) else repr(e)}")
            change(m, folder, " - FAILED")
            stopped = True
            not_reviewed = sum(map(to_do, mismatches[i:]))
            unchecked |= {x.item["id"] for x in mismatches[i - 1:]}
            print("Stopping, so nothing else is moved until you've looked at this.")
            break
        if not present:
            print("    NOT ON DISK after the refresh: skipped")
            not_on_disk.append(m.item)
            continue
        if fresh is None:
            print("    UP TO DATE after the refresh: nothing to rename any more")
            up_to_date += 1
            continue
        if fresh[1:-1] != m[1:-1]:  # compare the plans, not the file count: a newly added file isn't a change
            print("    CHANGED after the refresh, now:")
            show(fresh)
        m = mismatches[i - 1] = fresh
        item = m.item
        folder = bool(m.problems) and not m.blocker
        if not (folder or m.files or m.unsure):
            continue
        irregular_folder = folder and bool(m.irregular)
        if irregular_folder and args.force:
            print("    FORCED: renaming the irregular folder anyway (--force)")
        elif irregular_folder and not args.interactive:
            print("    NOT RENAMED: irregular; review it with --interactive")
            needs_i += 1
            need[0] += 1  # its files are counted with the folder, not as file renames
            need[1] += m.count
            change(m, folder, held=True)  # listed, so the Discord ping shows what needs looking at
            continue
        took_unsure = False
        if args.interactive:
            choice = "y"
            if (folder or m.files) and (irregular_folder or not rename_all):
                if irregular_folder:
                    choice = ask("This is irregular (check the arr's metadata). Rename anyway? [y]es / [N]o / [q]uit: ", "ynq")
                else:
                    choice = ask("Rename? [y]es / [N]o / [a]ll remaining / [q]uit: ")
                    rename_all = choice == "a"
                    if rename_all:
                        choice = "y"  # "all" still asks about each item's TBA? files, below
            if choice == "y" and m.unsure:  # TBA? files are always asked about, even after "all"
                choice = ask(f"{len(m.unsure)} TBA? file(s) may not be a TBA title being filled in. "
                             "Rename them too? [y]es / [N]o / [q]uit: ", "ynq")
                took_unsure = choice == "y"
                if choice == "n" and (folder or m.files):
                    choice = "y"  # no to the TBA? files, but still rename the rest
            if choice == "q":
                not_reviewed = sum(map(to_do, mismatches[i - 1:]))  # this one too
                unchecked |= {x.item["id"] for x in mismatches[i - 1:]}
                break
            if choice == "n":
                declined += 1
                continue
        elif m.unsure and args.force:
            print(f"    FORCED: renaming the {len(m.unsure)} TBA? file(s) anyway (--force)")
            took_unsure = True
        elif m.unsure:
            print(f"    NOT RENAMED: {len(m.unsure)} TBA? file(s); review them with --interactive")
            needs_i += 1
            need[2] += 1
            need[3] += len(m.unsure)
            if not (folder or m.files):
                change(m, folder)
                continue
        labels = []
        time.sleep(SETTLE)  # give the arr a moment after the refresh
        try:
            ok = True
            if folder:
                ok, label = rename(arr, kind, m)
                labels.append(label)
                if ok:  # mark the new path as taken, so no later item can be renamed onto it
                    taken.add(fold(posixpath.join(posixpath.dirname(item["path"].rstrip("/")), m.expected)))
                    done[0] += 1
                    done[1] += m.count
            todo = m.files + (m.unsure if took_unsure else ())
            if ok and todo:
                # only the planned ones: the arr's preview also has those --tba or --ignore-... left out
                ok, label, files_done = rename_files(arr, kind, item["id"], {f[0] for f in todo})
                labels.append(label)
                if ok:  # from here on, m lists the files actually renamed, which may be fewer than planned
                    unsure_ids = {f[0] for f in m.unsure}
                    m = m._replace(files=tuple(f for f in files_done if f[0] not in unsure_ids),
                                   unsure=tuple(f for f in files_done if f[0] in unsure_ids) if took_unsure else m.unsure)
                    took_unsure = took_unsure and bool(m.unsure)
                    if files_done and not folder:  # a renamed folder's files are counted with it
                        done[2] += 1
                        done[3] += len(files_done)
        except Exception as e:  # any error stops the run, as above
            print(f"    FAILED: {e if isinstance(e, OSError) else repr(e)}")
            ok = False
            labels.append("FAILED")
        last_done = time.monotonic()
        if args.force and (irregular_folder or took_unsure):
            labels.insert(0, "FORCED")
            forced += 1
        # nothing was left to rename: its files were renamed some other way in the meantime
        if ok and not (folder or m.files or took_unsure):
            up_to_date += 1
            change(m, folder)  # only adds its TBA? files, if any are left for --interactive
            continue
        label = ", ".join(dict.fromkeys(label for label in labels if label))
        change(m, folder, f" - {label}" if label else "", took_unsure)
        if not ok:
            stopped = True
            not_reviewed = sum(map(to_do, mismatches[i:]))
            unchecked |= {x.item["id"] for x in mismatches[i - 1:]}
            print("Stopping, so nothing else is moved until you've looked at this.")
            break
        renamed += 1

    tagged = [i["id"] for i in rounds.get("picked", []) if i["id"] not in unchecked]  # (a dry run: what it would tag)
    tag_failed = ""
    # Checked the last of the pass: take the tag off them all now rather than next run, or ones added in between would be
    # all the next run checks, and the pass would never end
    finished = bool(rounds.get("pool")) and len(tagged) == len(rounds["untagged"])
    if changing and finished:
        if checked_tag in tags:
            try:
                retag(arr, kind, [i["id"] for i in rounds["pool"]], tags[checked_tag], "remove")
            except Exception as e:  # the renames have happened either way: the next run checks these again, then tries again
                tag_failed = f"couldn't take the tag {checked_tag!r} off them to start a new pass, so the next run tries again: " \
                             f"{e if isinstance(e, OSError) else repr(e)}"
                print(f"WARNING: {tag_failed}", file=sys.stderr)
    elif changing and tagged:
        try:
            if checked_tag not in tags:
                tags[checked_tag] = arr.call("POST", "/api/v3/tag", {"label": checked_tag})["id"]
            retag(arr, kind, tagged, tags[checked_tag], "add")
        except Exception as e:  # the renames have happened either way: those not tagged are just checked again
            tag_failed = f"couldn't tag them {checked_tag!r}, so the next run checks them again: " \
                         f"{e if isinstance(e, OSError) else repr(e)}"
            print(f"WARNING: {tag_failed}", file=sys.stderr)
            tagged = []

    now = [m for m in mismatches if m.item["id"] not in later]  # what this run does (a dry run: would do)
    if not changing:  # dry run: count what --apply would rename, and what it would leave for --interactive
        for m in now:
            folder = bool(m.problems) and not m.blocker
            counts = need if folder and m.irregular else done  # an irregular folder's files wait with it
            counts[0] += folder
            counts[1] += m.count
            if m.files and not folder:  # a renamed folder's files are counted with it
                counts[2] += 1
                counts[3] += len(m.files)
            if m.unsure:
                need[2] += 1
                need[3] += len(m.unsure)
    summary = []
    if changing:
        if declined:
            summary.append(f"{declined} declined")
        if forced:
            summary.append(f"{forced} forced")
        if up_to_date:
            summary.append(f"{up_to_date} up to date after refresh")
        if deleted:
            summary.append(f"{deleted} deleted from {app} mid-run")
        if not_reviewed:
            summary.append(f"{not_reviewed} not reviewed")
    summary.append(f"{sum(bool(m.blocker) for m in mismatches)} can't rename")
    for label, skipped in (("NOT ON DISK (skipped)", not_on_disk), (f"IGNORED (tagged {tag_name!r})", ignored)):
        if skipped:
            print(f"{label}:" + "".join(f"\n    {i['title']} ({i.get('year') or 'no year'})  {i['path']}"
                                        for i in skipped))
    for k in ("punctuation", "capitals"):  # what --ignore-punctuation / --ignore-case left alone, in the same order
        what, option = IGNORABLE[k]
        folders = [m for m in mismatches if k in m.ignored]
        if folders:
            print(f"IGNORED ({option}: only {what} differ):" + "".join(
                f"\n    {m.item['title']} ({m.item.get('year') or 'no year'})  {m.item['path']}\n      -> {m.expected}"
                for m in folders))
        files = [(m.item["path"].rstrip("/"), old, new) for m in mismatches for _, old, new, why in m.skipped if k in why]
        if files:
            print(f"IGNORED FILES ({option}: only {what} differ):" + "".join(
                f"\n    {path}/{old}\n      -> {new}" for path, old, new in files))
    if not_on_disk:
        summary.append(f"{len(not_on_disk)} not on disk")
    noun, nouns = ("Series", "Series") if args.app == "sonarr" else ("Movie", "Movies")
    lines = [tally(noun, nouns, *done)]
    if any(need):
        lines += ["**Needs intervention not renamed**", tally(noun, nouns, *need)]
    lines.append(", ".join(summary) + (", 1 failed" if stopped else ""))
    why_ignored = {"Tagged": len(ignored), **{k.capitalize(): sum(k in m.ignored for m in mismatches)
                                              for k in ("punctuation", "capitals")}}
    if any(why_ignored.values()):
        lines.append("Ignored: " + ", ".join(f"{n} {why}" for why, n in why_ignored.items() if n))
    if rounds:  # how far through the pass it is, and how many more runs that takes at this batch size
        left = len(rounds["untagged"]) - len(tagged)
        runs = -(-left // count)
        picked, pool, did = len(rounds["picked"]), len(rounds["pool"]), "Would check" if not changing else "Checked"
        noun = nouns.lower()
        if left:
            lines.append(f"{did} {picked:,} {noun} this run; total {pool - left:,} of {pool:,} {noun} checked, "
                         f"{left:,} left to check, {runs:,} more run{'s' if runs != 1 else ''} at {count:,} a run")
        elif picked == pool:  # the whole pass in one run
            lines.append(f"{did} all {pool:,} {noun} this run; next run starts over")
        else:
            lines.append(f"{did} {picked:,} {noun} this run; total {pool:,} of {pool:,} {noun} checked, next run starts over")
        if tag_failed:
            lines.append(discord_escape(tag_failed[0].upper() + tag_failed[1:]))
    if most:  # e.g. "Rename limit 10 - 25 more to rename in 3 runs" (the tally above counts what it renamed)
        runs = -(-len(later) // most)
        lines.append(f"Rename limit {most:,} - " + (f"{len(later):,} more to rename in {runs:,} run{'s' if runs != 1 else ''}"
                                                    if later else "none left to rename"))
    files_ignored = {k.capitalize(): sum(k in s[3] for m in mismatches for s in m.skipped)
                     for k in ("punctuation", "capitals")}
    if any(files_ignored.values()):
        lines.append("Ignored files: " + ", ".join(f"{n} {why}" for why, n in files_ignored.items() if n))
    summary = "\n".join(lines)
    print("\n" + summary)
    if not changing and (renames or not_renamed):
        print("Dry run: nothing changed. Re-run with --apply (or --interactive).")
    if args.discord_webhook:
        color = RED if stopped else GREEN if renamed else BLUE
        # renames that only an --interactive run will do: irregular folders and TBA? files
        need_i = needs_i if changing else sum(bool(m.irregular and not m.blocker or m.unsure) for m in now)
        # reasons to ping you: a problem that stopped the run, renames waiting for --interactive, renames
        # --force did anyway, and items deleted from the arr mid-run
        reasons = ["the run stopped"] if stopped else []
        if need_i:
            reasons.append(f"{need_i} rename{'s' if need_i > 1 else ''} need{'s' if need_i == 1 else ''} an --interactive run")
        if forced:
            reasons.append(f"{forced} rename{'s were' if forced > 1 else ' was'} forced")
        if deleted:
            reasons.append(f"{deleted} {(noun if deleted == 1 else nouns).lower()} {'was' if deleted == 1 else 'were'} "
                           f"deleted from {app} mid-run")
        ping = args.discord_user if reasons else ""
        why = "; ".join(reasons)
        # what's left for --interactive first, then what was renamed, each under a bold heading
        # (a field with a blank name)
        fields = []
        for header, entries in ((f"Not renamed {nouns.lower()}", not_renamed),
                                (f"Renamed {nouns.lower()}" if changing else f"{nouns} to rename", renames)):
            if entries:
                fields += [("\u200b", f"**{header}**")] + entries
        try:
            discord_send(args.discord_webhook, f"{app}{suffix} Renaminatorr: {modes}", summary, fields, color,
                         script_footer(__file__), ping, why)
        except Exception as e:  # the renames have happened either way, so just warn
            print(f"WARNING: couldn't post to Discord: {e if isinstance(e, OSError) else repr(e)}", file=sys.stderr)
    return 1 if stopped else 0


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


def discord_diff(old: str, new: str, groups=(), note: str = "", limit: int | None = None) -> str:
    """A ```diff``` code block showing `old` replaced by `new` (left out if they're the same), then
    for each (label, files) in `groups` that has files, the label and each (old, new) file, as many
    as fit in a field's 1024 characters, and at most `limit` per label. A `note` (markdown) goes on
    a line above the block."""
    def line(s):  # a ``` inside the block would end it early; 400 keeps the folder lines under 1024
        return s.replace("```", "`\u200b`\u200b`")[:400]
    note = (note if len(note) <= 150 else note[:149] + "…") + "\n" if note else ""  # 150 still fits with the above
    body = "" if old == new else f"- {line(old)}\n+ {line(new)}"
    room = 1024 - len(note) - len("```diff\n\n```") - len("\n… and 9999 more files")
    for label, files in groups:
        if not files:
            continue
        body += ("\n" if body else "") + label
        for n, (o, nw) in enumerate(files):
            pair = f"\n- {line(o)}\n+ {line(nw)}"
            if n == limit or len(body) + len(pair) > room:
                body += f"\n… and {len(files) - n} more files"
                break
            body += pair
    return f"{note}```diff\n{body}\n```"


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


if __name__ == "__main__":
    load_env()
    # line by line even into a pipe (cron, docker logs), so it stays in order with what goes to stderr
    sys.stdout.reconfigure(errors="backslashreplace", line_buffering=True)
    with tee(__file__):
        sys.exit(main())
