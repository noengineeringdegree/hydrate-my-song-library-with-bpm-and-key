# hydrate-my-song-library-with-bpm-and-key

**BPM and key data provided by [GetSongBPM](https://getsongbpm.com).**

Adds BPM, musical key and Camelot notation to a Spotify playlist export, then builds harmonically compatible DJ practice crates.

## Scripts

- `hydrate_bpm_key.py`: looks up BPM and key for each track through the [GetSongBPM API](https://getsongbpm.com/api). Rate-limited (~1 request/sec with jitter, backoff on 429/5xx, daily cap) and resumable.
- `build_crate.py`: builds an ordered crate where each track mixes into the next (BPM within ±6%, compatible Camelot keys).

## Requirements

- Python 3.9+ (standard library only, no packages to install)
- A free GetSongBPM API key, set as the environment variable `GETSONGBPM_API_KEY`

## Expected folder layout

```
<project>/
├── Catalogue/catalog_master.csv     (input)
├── agents downloads/                (outputs: catalog_enriched.csv, crates/)
└── development/<this repo>/         (scripts)
```

The scripts find `<project>` by walking up to the folder that contains `Catalogue/`. To override, set `DJ_PROJECT_ROOT`.

## Usage

```
python hydrate_bpm_key.py --limit 50
python build_crate.py --seed "Artist - Title" --genre house
```

## Credits

Song tempo and key data: [GetSongBPM.com](https://getsongbpm.com)
