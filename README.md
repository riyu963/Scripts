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
- Once the whole library has the tag, the next run removes it from everything and starts a new pass from the top.
- If a run stops partway, the ones it didn't finish stay untagged and are checked again next run. Ones left for `-i` are tagged like the rest and come round again next pass.
- The summary and the Discord post show the progress: `Checked 50 of 1,234 series; 1,184 left to check, 24 more runs at 50 a run`, or at the end of a pass `Checked 34 of 1,234 series; 100% checked next run starts over`.
- A dry run never tags. Leave the batch size empty, or set it to 0, to check everything every run.
- Named sets have their own: `SONARR_ANIME_RENAME_COUNT`, `SONARR_ANIME_RENAME_TAG`.

## Usage
```bash
./arr_rename.py sonarr            # dry run: shows what it would rename root folders only
./arr_rename.py sonarr --files    # dry run: folders and files
./arr_rename.py sonarr --tba      # dry run: folders and TBA episodes only
./arr_rename.py radarr --files    # dry run: folders and files
./arr_rename.py sonarr -i         # asks about each rename
./arr_rename.py radarr --apply    # root folders it can safely rename
```

Renames that would lose information (a year, an id tag, the title) are marked **IRREGULAR** and only done with `-i` or `--force`. Everything printed is also saved to `logs/arr_rename.log`.

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
| `--checked-tag TAG` | Batched runs: the tag that marks one checked (default: `SONARR_RENAME_TAG` / `RADARR_RENAME_TAG`, or `rename-checked`) |
| `--discord-webhook URL` | Post each run's report to Discord (default: `DISCORD_WEBHOOK_URL`) |
| `--discord-user ID` | Discord user id to ping when a run can't start or stops, something needs `-i` or was forced, or something was deleted from Sonarr/Radarr mid-run (default: `DISCORD_USER_ID`) |

`--apply` and `-i` can't be combined, and neither can `--files` and `--tba`.