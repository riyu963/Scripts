# Scripts
Just random scripts to share

> [!WARNING]
> **All of the code in this repo was written by AI** (Claude), not by hand. It's been tested against real Sonarr and Radarr instances, but read what a script does, and run it without `--apply` first (a dry run), before letting it change your library.

# arr_rename.py

Renames Sonarr series and Radarr movie folders (and their files) that don't match the arr's naming format, through the arr itself, then checks no files went missing. Any problem stops the run.

Python 3.9+ (standard library only).

The arr must run on Linux or macOS (or in Docker). An arr running on Windows isn't supported: the script reads the arr's paths as Linux paths.

## Setup

Copy `.env.example` to `.env` and fill in `SONARR_URL` / `SONARR_API_KEY` and/or `RADARR_URL` / `RADARR_API_KEY`.

A `DISCORD_USER_ID` that isn't a number (copy it with Copy User ID in Discord) doesn't stop a run, in any of the scripts: the log has a warning, Discord a red post without a ping, and the run goes on without pinging anyone.

## Several Sonarrs or Radarrs

Add named sets to `.env`, such as `SONARR_ANIME_URL` / `SONARR_ANIME_API_KEY` / `SONARR_ANIME_IGNORE_TAG` or `RADARR_4K_URL` / `RADARR_4K_API_KEY`. A run checks the main one, then each named one, with its own log section and Discord post. One that can't start (it can't be reached, say) is reported and skipped, and the others are still checked; a problem once renaming has started stops the run.

## Batched runs

Rather than checking the whole library every run, each run can check a batch of it. Set the batch size with `SONARR_RENAME_COUNT` / `RADARR_RENAME_COUNT` (or `--count N`), and each run checks the next N series or movies, in title order, that haven't been checked yet in this pass through the library.

- With `--apply` or `-i`, each one checked is tagged `SONARR_RENAME_TAG` / `RADARR_RENAME_TAG` (or `--checked-tag`; default `rename-checked`, made if it isn't there, in a dry run too), so the next run moves on to the next batch.
- The tag can only have letters, digits and `-` (the arr won't make any other), and can't be the ignore tag, or either of `arr_upgrade_search.py`'s tags (`SONARR_UPGRADE_SEARCH_TAG`, default `upgrade-searched`, and `SONARR_UPGRADE_IGNORE_TAG`). With such a tag the run doesn't start, and says why.
- The run that checks the last of them removes the tag from everything, so the next run starts a new pass from the top. Ones added since are just part of that pass, rather than being all each run checks.
- If a run stops partway, the ones it didn't finish stay untagged and are checked again next run. Ones left for `-i` are tagged like the rest and come round again next pass.
- The summary and the Discord post show the progress: `Checked 50 series this run; total 100 of 1,234 series checked, 1,134 left to check, 23 more runs at 50 a run`, or at the end of a pass `Checked 34 series this run; total 1,234 of 1,234 series checked, next run starts over`.
- A dry run never tags. Leave the batch size empty, or set it to 0, to check everything every run.
- Named sets have their own: `SONARR_ANIME_RENAME_COUNT`, `SONARR_ANIME_RENAME_TAG`.

## Rename limit

Rather than renaming everything that needs it in one go, each run can check the whole library but rename at most N series or movies. Set the limit with `SONARR_RENAME_MAX` / `RADARR_RENAME_MAX` (or `--max-renames N`). Handy when a lot need renaming and you'd rather the arr (and whatever watches the folders, like Plex) took them a few at a time.

- Once N have been renamed, the rest that need renaming are listed as `LATER` and left alone; the arr isn't even asked to refresh them.
- No tag is needed. The ones renamed now match, so the next run finds nothing to do for them and goes on to the rest, until none are left.
- Irregular folders and `TBA?` files left for `-i`, and those that can't be renamed or are ignored, don't count towards N and are never `LATER`: they're listed as usual every run, so they can't hold up the others.
- A dry run stops at the same point, so it shows exactly what `--apply` would do.
- The summary and the Discord post end with how far it got. The counts above it are what this run renamed, as in any run, then the limit and what's left for the next runs (only renames they'll do, not ones waiting for `-i`):

  ```
  1 Movie folder with 1 file
  1 Movie with file renames - 1 file total
  1 can't rename
  Rename limit 1 - 5 more to rename in 5 runs
  ```

  Once it has caught up, the last line is `Rename limit 1 - none left to rename`.
- With batched runs too, each batch is checked as usual, but those left for later aren't tagged, so they're checked again next run.
- Leave it empty, or set it to 0, for no limit. Named sets have their own: `SONARR_ANIME_RENAME_MAX`.

## Usage
```bash
./arr_rename.py sonarr            # dry run: shows what it would rename root folders only
./arr_rename.py sonarr --files    # dry run: folders and files
./arr_rename.py sonarr --tba      # dry run: folders and TBA episodes only
./arr_rename.py radarr --files    # dry run: folders and files
./arr_rename.py sonarr -i         # asks about each rename
./arr_rename.py radarr --apply    # root folders it can safely rename
./arr_rename.py radarr --apply --files --max-renames 10   # at most 10 movies this run, the rest next runs
```

Renames that would lose information (a year, an id tag, the title) are marked **IRREGULAR** and only done with `-i` or `--force`. Everything printed is also saved to `logs/arr_rename.log`, with the command line (but not an API key or webhook URL given on it).

## Options

| Option | Description |
| --- | --- |
| `sonarr` / `radarr` | Which arr to check (required, first) |
| `--instance NAME` | Only check this one when several are set up (`main` for the plain `SONARR_URL` one); default is every one, in turn |
| `--url URL` | Arr address (default: `SONARR_URL` / `RADARR_URL`) |
| `--api-key KEY` | Arr API key (default: `SONARR_API_KEY` / `RADARR_API_KEY`) |
| `--apply` | Do every rename except irregular ones (default is a dry run) |
| `-i`, `--interactive` | Ask about each rename: yes / no / all / quit |
| `--force` | With `--apply`, also do irregular and `TBA?` renames |
| `--files` | Also rename files of items whose folder is fine |
| `--tba` | Sonarr only: like `--files`, but only files whose "TBA" episode title now has a real one |
| `--ignore-case` | Skip folders and files that only differ in capitals |
| `--ignore-punctuation` | Skip folders and files that only differ in special characters |
| `--ignore-tag TAG` | Leave items with this tag alone (default: `SONARR_IGNORE_TAG` / `RADARR_IGNORE_TAG`). A run (a dry run too) makes it if the arr hasn't got it; it can only have letters, digits and `-` |
| `--count N` | Batched runs: check the next N not checked yet in this pass, tagging each one (default: `SONARR_RENAME_COUNT` / `RADARR_RENAME_COUNT`; 0 or unset: every one) |
| `--max-renames N` | Rename limit: rename at most N each run, leaving the rest for the next runs (default: `SONARR_RENAME_MAX` / `RADARR_RENAME_MAX`; 0 or unset: no limit) |
| `--checked-tag TAG` | Batched runs: the tag that marks one checked, letters, digits and `-` only (default: `SONARR_RENAME_TAG` / `RADARR_RENAME_TAG`, or `rename-checked`) |
| `--discord-webhook URL` | Post each run's report to Discord (default: `DISCORD_WEBHOOK_URL`) |
| `--discord-user ID` | Discord user id to ping when a run can't start or stops, something needs `-i` or was forced, or something was deleted from Sonarr/Radarr mid-run (default: `DISCORD_USER_ID`) |

`--apply` and `-i` can't be combined, and neither can `--files` and `--tba`.

# arr_upgrade_search.py

Searches Sonarr and Radarr for upgrades they missed, runs in batches until the whole library has been searched then starts over.

Python 3.9+ (standard library only). It uses the same `.env` as `arr_rename.py`.

## How it works

How many it searches a run has to be set: `SONARR_UPGRADE_COUNT` / `RADARR_UPGRADE_COUNT` (`SONARR_ANIME_UPGRADE_COUNT` for a named one), or `--count N` for every one. A Sonarr or Radarr without one (or with `0`) isn't searched: it's taken as not wanted there, so the log says it's skipped, with no Discord post or ping.

- Each run searches the next `SONARR_UPGRADE_COUNT` / `RADARR_UPGRADE_COUNT` (or `--count N`; 1 to 500) monitored series or movies that are out, in title order. Sonarr gets a series search, Radarr a movie search.
- Each one searched is tagged `upgrade-searched` (`--search-tag`; made if it isn't there, in a dry run too). The run that searches the last film/series takes the tag off them all, so the next run starts over from the top. Failed searches aren't tagged and left for the next run; one the arr says failed 3 runs in a row is tagged anyway, so the turn can end (it's searched again next turn). One the arr can't be reached for isn't counted.
- After each search it reads what the arr grabbed from its history, and shows each grab old against new: the file (or season pack) it replaces, with its quality and custom format score, then what was grabbed.
- Each grab is remembered in a file of each Sonarr's or Radarr's own, `logs/arr_upgrade_search_SONARR.json` (`..._SONARR_ANIME.json` for a named one, `..._RADARR.json`; `--state DIR` for another folder), so runs of different ones at the same time can't overwrite each other's. The next runs say whether each grab imported (and at what quality and score) or failed. One still not imported after 7 days is let go. One whose download is stuck in the arr's queue (blocked from importing, failed to import, or with a warning) is listed under `Stuck in the queue`, with the arr's own reason, each run until it imports or is let go.
- Without `--apply` it's a dry run: it says what it would search, and searches and tags nothing.

## What it searches

| Option | Description |
| --- | --- |
| `--mode all` | Every one that's out, with files or not (the default): a movie Radarr counts as available, a series whose first episode has aired |
| `--mode cutoff` | Only those below their quality profile's cutoff (the arr's Wanted > Cutoff Unmet) |
| `--mode missing` | Only missing ones (Wanted > Missing) |
| `--tag NAME` | Only those with this tag (repeat it for any of several) |
| `--ignore-tag TAG` | Leave those with this tag alone (default: `SONARR_UPGRADE_IGNORE_TAG` / `RADARR_UPGRADE_IGNORE_TAG`). A tag the arr hasn't got is made, in a dry run too; one that's also the search tag stops the run, and says why |
| `--search-tag TAG` | The tag that marks one searched (default: `SONARR_UPGRADE_SEARCH_TAG` / `RADARR_UPGRADE_SEARCH_TAG`, or `upgrade-searched`). It can only have letters, digits and `-` (the arr won't make any other tag), and can't be one of the `--tag` limits, the ignore tag or either of `arr_rename.py`'s tags (`SONARR_RENAME_TAG`, default `rename-checked`, and `SONARR_IGNORE_TAG`); with one of them the run doesn't start, and says why |
| `--quality-profile NAME` | Only those with this quality profile |
| `--age DAYS` | Only those released DAYS days ago or more: with 5 on 20 November, those released on 15 November or before (a movie: the earliest of in cinemas, digital and disc; a series: its first episode). One with no release date is left out. Each run's summary says how many it left out: `Skipped: 1 No release date - 3 Released after 2026-11-01`. 0: off |
| `--monitored yes/any/no` | Only monitored ones (the default), either, or only unmonitored ones |

For Sonarr, `cutoff` and `missing` search just the episodes listed there, not the whole series, as Sonarr's own Wanted search does.

## Max add

Rather than grabbing a whole batch's worth in one go, a run can stop once N of its searches have grabbed something (an upgrade, or a missing one). Set it with `SONARR_UPGRADE_MAX_ADD` / `RADARR_UPGRADE_MAX_ADD` (or `--max-add N`). Handy when a lot could be upgraded and you'd rather the downloads come over time.

- With a batch of 20 and a max add of 3: if the 5th search is the third to grab something, the run stops there. The other 15 aren't searched or tagged, so the next runs search them.
- A series counts once, however many releases its search grabbed.
- The summary and the Discord post end with `Max add 3 reached: stopped with 15 movies of the batch left, searched next time`.
- A dry run can't know what a search would grab, so it lists the whole batch.
- Leave it empty, or set it to 0, for no limit. Named sets have their own: `SONARR_ANIME_UPGRADE_MAX_ADD`.

## Guards

Always on:

- One with a download in the arr's queue (downloading, or waiting or stuck importing) under 72 hours old is left alone, so it never grabs a second copy. The next one takes its place in the batch; the report lists it under `Held back` only if the run got to it, so not one after where max add (or a search still going) stopped the run.
- The run is skipped while the arr already has 3 or more searches queued.
- Only ones whose search finished are tagged. A search still going after 10 minutes stops the run there; the next run tags it once it's finished (or lets it go, if it's been deleted from the arr since).
- A search the arr says failed 3 runs in a row is tagged anyway, so one that always fails can't keep the turn from ending.

## Several Sonarrs or Radarrs

Set up as for `arr_rename.py` (`SONARR_ANIME_URL`, ...). A run searches every one of the arr it's given (`sonarr` or `radarr`, first) in turn; `--instance NAME` picks one (`main` for the plain `SONARR_URL` one); a name that isn't set up stops the run (exit 2), and says which are, in all three scripts.

Each one can have its own settings in `.env`, `<PREFIX>_UPGRADE_<SETTING>`: `SONARR_UPGRADE_COUNT=5`, `SONARR_ANIME_UPGRADE_MODE=cutoff`. The settings are `COUNT`, `SEARCH_TAG`, `TAGS`, `QUALITY_PROFILES` (these two comma-separated, each name with or without quotes: `"SQP-1 WEB (1080p)", "SQP-2 WEB (1080p)"`), `IGNORE_TAG`, `AGE`, `MONITORED`, `MODE` and `MAX_ADD`. An option given on the command line wins, for every one. A setting that can't be read stops that one, and says why.

## Usage
```bash
./arr_upgrade_search.py sonarr --count 10          # dry run: what it would search
./arr_upgrade_search.py sonarr --count 10 --apply  # search the next 10 series
./arr_upgrade_search.py radarr --mode cutoff --apply   # as many as RADARR_UPGRADE_COUNT says
./arr_upgrade_search.py radarr --count 20 --max-add 3 --apply   # stop once 3 have grabbed something
./arr_upgrade_search.py radarr --quality-profile "HD-1080p" --age 30 --apply   # only ones out 30 days or more
```

Everything printed is also saved to `logs/arr_upgrade_search.log`.

## Discord

With `DISCORD_WEBHOOK_URL` (or `--discord-webhook`), each run posts its report. The summary says how far through the library it is `Searched 5 movies this run; total 5 of 16 movies searched, 11 left to search, 3 more runs at 5 a run`, or at the end of a turn `... next run starts over`. Then come the counts, `5 Searched; 2 Grabbed` (and `; 1 Failed`; which ones is in the log), then the limits, then `Imported: 1 of 3 grabs from previous run` (of the earlier grabs still waiting to import, how many have since; which ones is in the log), the lists (failed, held back, and earlier grabs that didn't import), then a block for each grab (earlier grabs that have imported since are only counted; the log says what each one was):

````
**Alien (1979)**
Release Name: Alien.1979.2160p.UHD.BluRay.REMUX.HDR-FGT
```diff
- WEBDL-1080p
- CF: HDR10, DD+ 5.1
- Score: 100
+ Remux-2160p
+ CF: DV HDR10, TrueHD Atmos, Remux Tier 01
+ Score: 2050
```
````

In red the quality, custom formats and custom format score of what it replaces, in green what was grabbed. A side with no custom formats has no `CF:` line. It pings `DISCORD_USER_ID` (or `--discord-user`) about what `--discord-ping` names, comma-separated: `failed` (the default), `grabbed`, `imported` (an earlier grab imported), `import_failed` (an earlier grab didn't import, or is stuck in the queue) and `rotated` (it's searched them all and starts over), or `none`.

# arr_blocklist_cleanup.py

Keeps Sonarr's and Radarr's blocklists down in size, removing the entries the rules pick through the arr itself.

Python 3.9+ (standard library only). It uses the same `.env` as `arr_rename.py`.

Removing an entry lets the arr grab that release again, so nothing is removed unless a rule says so: a Sonarr or Radarr with no rules set is left alone, and the run says so.

## Rules

Any of them together; an entry any of them picks is removed. Each can be set for every one on the command line, or for each Sonarr or Radarr as `<PREFIX>_BLOCKLIST_<RULE>` (`SONARR_BLOCKLIST_OLDER_THAN=90`, `SONARR_ANIME_BLOCKLIST_KEEP=3`). Numbers: 0 or empty is off.

| Option | Setting | Removes |
| --- | --- | --- |
| `--older-than DAYS` | `..._BLOCKLIST_OLDER_THAN` | Entries blocklisted more than DAYS days ago. By then indexers have mostly dropped the release, so it's the safest rule |
| `--keep N` | `..._BLOCKLIST_KEEP` | All but the newest N entries for each movie (Radarr), or each episode or pack (Sonarr: entries for the same episodes) |
| `--max N` | `..._BLOCKLIST_MAX` | The oldest entries, until at most N are left in all (counted after the other rules) |
| `--orphans` | `..._BLOCKLIST_ORPHANS=yes` | Entries for a movie or series no longer in the arr |

Without `--apply` it's a dry run: it says what it would remove, and removes nothing.

Guards, always on:

- `--orphans` doesn't run while the arr says it has no movies or series at all, and `--orphan-indexer` doesn't run while it says it has no indexers (as while it starts up, or Prowlarr syncs): either would take every entry for an orphan. The run stops and says why.
- If removing entries fails partway, those it couldn't remove are listed as `Not removed (the delete failed)`, in the log and the Discord post, and the run ends with an error.

## Advanced options

For the indexers entries came from. They're left out of `.env.example`; set them on the command line, or add them to your `.env` yourself (below). Indexers are matched by their name in the arr, any case; one synced from Prowlarr has ` (Prowlarr)` on the end.

| Option | Setting | Does |
| --- | --- | --- |
| `--orphan-indexer` | `..._BLOCKLIST_ORPHAN_INDEXER=yes` | A rule: removes entries from an indexer no longer in the arr. By name, so a renamed indexer counts as gone; an entry with no indexer recorded is left alone |
| `--indexer NAME` | `..._BLOCKLIST_INDEXERS` | Only looks at entries from this indexer (repeat it, or comma-separate the setting, for any of several) |
| `--ignore-indexer NAME` | `..._BLOCKLIST_IGNORE_INDEXERS` | Never looks at entries from this indexer (repeat it, or comma-separate the setting, for several) |

> [!NOTE]
> `--orphan-indexer` matches indexers **by name only**: a blocklist entry records the name of the indexer it came from, not its id. So **renaming an indexer** in the arr (or in Prowlarr, which renames it in the arr) makes every entry blocklisted under its old name look like it's from an indexer that's gone, and the next run removes them all. Run a dry run first after renaming an indexer, to see what it would remove.

- `--keep` can't be used with any of them: it keeps each movie's (or episode's) newest entries from every indexer. `--orphan-indexer` can't be used with `--indexer` or `--ignore-indexer` either: it's for whichever indexers are gone. With one of these the run stops, and says why.
- With `--indexer` or `--ignore-indexer`, `--max` counts only the entries looked at.
- A name no entry has is warned about, with the names the blocklist does have.

In `.env`, for example:

```bash
# Radarr: entries older than 30 days, only from HD-Torrents and FileList
RADARR_BLOCKLIST_OLDER_THAN=30
RADARR_BLOCKLIST_INDEXERS=HD-Torrents,FileList
# Sonarr: entries older than 90 days from any indexer but NZBgeek (Prowlarr)
SONARR_BLOCKLIST_OLDER_THAN=90
SONARR_BLOCKLIST_IGNORE_INDEXERS=NZBgeek (Prowlarr)
# Sonarr Anime: entries from indexers it no longer has
SONARR_ANIME_BLOCKLIST_ORPHAN_INDEXER=yes
```

## Usage
```bash
./arr_blocklist_cleanup.py radarr --older-than 90                  # dry run: what it would remove
./arr_blocklist_cleanup.py radarr --older-than 90 --apply
./arr_blocklist_cleanup.py sonarr --older-than 90 --keep 5 --orphans --apply
./arr_blocklist_cleanup.py sonarr --instance anime --max 500 --apply
./arr_blocklist_cleanup.py radarr --older-than 30 --indexer "HD-Torrents" --apply   # only HD-Torrents' entries
```

Everything printed is also saved to `logs/arr_blocklist_cleanup.log`.

## Discord

With `DISCORD_WEBHOOK_URL` (or `--discord-webhook`), each run posts its report: `Removed 4 of 7 entries · 3 left`, the limits (`Limits: older than 90 days, keep 2 per movie`), then a block for each movie or series with entries removed (or that would be), each under the rule that picked it:

````
**Alien (1979)**
```diff
Older than 90 days:
- Alien.1979.1080p.WEB-DL-GRP
  Indexer · torrent
  200 days ago: Manually marked as failed
Over keep threshold:
- Alien.1979.2160p.WEB-DL-GRP
  Indexer · torrent
  5 days ago: Download failed
```
````

In red the release removed, then the indexer it came from and torrent or usenet, then how long ago it was blocklisted and why (the arr's message). The log also has each one's episodes (Sonarr), quality and custom formats. An entry for a movie or series no longer in the arr is headed `Not in Radarr (id 99)`. Every movie or series gets its block, over as many messages as it takes; one with more removals than fit in its block says `… and 13 more` (the log lists them all). It pings `DISCORD_USER_ID` (or `--discord-user`) about what `--discord-ping` names, comma-separated: `failed` (the default) and `removed` (entries were removed), or `none`.
