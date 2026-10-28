# Scripts
Just random scripts to share
# arr_rename.py - All vibes don't blame me

Renames Sonarr series and Radarr movie folders (and their files) that don't match the arr's naming format, through the arr itself, then checks no files went missing. Any problem stops the run.

Python 3.9+ (standard library only).

The arr must run on Linux or macOS (or in Docker). An arr running on Windows isn't supported: the script reads the arr's paths as Linux paths.

## Setup

Copy `.env.example` to `.env` and fill in `SONARR_URL` / `SONARR_API_KEY` and/or `RADARR_URL` / `RADARR_API_KEY`.

## Several Sonarrs or Radarrs

Add named sets to `.env`, such as `SONARR_ANIME_URL` / `SONARR_ANIME_API_KEY` / `SONARR_ANIME_IGNORE_TAG` or `RADARR_4K_URL` / `RADARR_4K_API_KEY`. A run checks the main one, then each named one, with its own log section and Discord post. One that can't start (it can't be reached, say) is reported and skipped, and the others are still checked; a problem once renaming has started stops the run.

## Batched runs

Rather than checking the whole library every run, each run can check a batch of it. Set the batch size with `SONARR_RENAME_COUNT` / `RADARR_RENAME_COUNT` (or `--count N`), and each run checks the next N series or movies, in title order, that haven't been checked yet in this pass through the library.

- With `--apply` or `-i`, each one checked is tagged `SONARR_RENAME_TAG` / `RADARR_RENAME_TAG` (or `--checked-tag`; default `rename-checked`, created if it isn't there), so the next run moves on to the next batch.
- The tag can only have letters, digits and `-` (the arr won't make any other), and can't be the ignore tag. With such a tag the run doesn't start, and says why.
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
| `--ignore-tag TAG` | Leave items with this tag alone (default: `SONARR_IGNORE_TAG` / `RADARR_IGNORE_TAG`) |
| `--count N` | Batched runs: check the next N not checked yet in this pass, tagging each one (default: `SONARR_RENAME_COUNT` / `RADARR_RENAME_COUNT`; 0 or unset: every one) |
| `--max-renames N` | Rename limit: rename at most N each run, leaving the rest for the next runs (default: `SONARR_RENAME_MAX` / `RADARR_RENAME_MAX`; 0 or unset: no limit) |
| `--checked-tag TAG` | Batched runs: the tag that marks one checked, letters, digits and `-` only (default: `SONARR_RENAME_TAG` / `RADARR_RENAME_TAG`, or `rename-checked`) |
| `--discord-webhook URL` | Post each run's report to Discord (default: `DISCORD_WEBHOOK_URL`) |
| `--discord-user ID` | Discord user id to ping when a run can't start or stops, something needs `-i` or was forced, or something was deleted from Sonarr/Radarr mid-run (default: `DISCORD_USER_ID`) |

`--apply` and `-i` can't be combined, and neither can `--files` and `--tba`.