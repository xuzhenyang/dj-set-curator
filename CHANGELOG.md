# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-04-27

### Added

- `Song` dataclass in `models.py` with `from_dict()` / `to_dict()` helpers.
- New extracted modules from the monolithic `curator.py`:
  - `deduplicator.py` — ID/name/anchor deduplication
  - `energy_heuristics.py` — BPM + song-name keyword heuristics
  - `expansion.py` — cascade expander (secondary-anchor search)
  - `playlist_naming.py` — auto playlist naming (`[DJ Curator] {mode} | {artists}`)
- `get_audio_url()` method in `mcp_client.py` for precise energy analysis.
- `flush_cache()` method in `AudioAnalyzer` for batch cache persistence.

### Changed

- **Architecture**: `curator.py` refactored from 422 lines to ~280 lines (pure orchestrator).
- `AnchorSong` now inherits from `Song` instead of duplicating all fields.
- All modules migrated from plain `dict` to `Song` / `ScoredSong` objects.
- Anchor resolution (`anchor.py`) now runs in parallel via `asyncio.gather`.
- `BatchAudioAnalyzer` now flushes cache once after the batch instead of after every song.
- `EnergyAnalyzer` instance is reused across anchor analysis and post-selection analysis in `curator.py`.

### Fixed

- **Critical**: Synchronous `urllib.request.urlretrieve` blocking the async event loop — now wrapped with `asyncio.to_thread` in both `audio_analyzer.py` and `arranger.py`.
- **Critical**: MCP client resource leak — if `ClientSession.__aenter__()` fails, the `stdio_client` context is now properly cleaned up.
- **Major**: Diversity score was hard-coded to `100.0` and never applied — now dynamically computed based on already-scored candidates.
- **Major**: Fragile error detection in `_call_tool()` that matched `"失败"` / `"错误"` as substrings in normal song names — now uses specific error indicators and length guard.
- **Minor**: Module-level `logging.basicConfig()` polluted log config for library consumers — moved to `cli.main()`.
- **Minor**: `config.py` skipped explicit `--server cloud-music-mcp` due to `cli_value != "cloud-music-mcp"` check — now checks `cli_value is not None`.
- **Minor**: `import re` inside `key_transition_score()` moved to module top in `transition.py`.

## [0.2.1] - 2026-04-25

### Added

- `--dry-run` flag for preview mode (show candidates & predicted selection without creating playlist).
- Status machine in `DJSetCurator` (`_status` dict with stage / progress / message).
- `config.py` supporting `~/.dj-set-curator/config.yaml` and env var `DJ_CURATOR_MCP_SERVER`.
- 120-second soft timeout for audio analysis (skips remaining songs if exceeded).
- MCP client retry with exponential backoff (2 attempts).
- `--server` CLI option to override MCP server command.
- Auto playlist naming: `[DJ Curator] {mode} | {artists}` (Scheme E).
- Independent QR login script at `scripts/login.py`.

### Fixed

- **Critical**: `asyncio.gather` + `anyio` deadlock — switched from concurrent source collection to serial per-source collection with `asyncio.wait_for(..., timeout=30.0)`.
- NetEase API rejects emoji in playlist names — removed emoji from auto-generated names.
- UnboundLocalError for `time` import inside `build_playlist`.

### Changed

- Removed 25-song analysis limit — all candidates are now analyzed with batch=10 concurrency.

## [0.2.0] - 2026-04-24

### Added

- Transition-based DJ Set builder (v2.0):
  - `TransitionScorer` with BPM / Key / Energy / Artist-penalty dimensions.
  - `SequentialSelector` greedy sequence builder with 5 energy-curve modes (`flat`, `warm-up`, `peak-mid`, `rollercoaster`, `climax-end`).
- Multi-source candidate collection (7 sources):
  - `SimilarSource`, `ArtistTopSource`, `AlbumSource`, `DailyRecSource`, `TagSearchSource`, `GenreSearchSource`, `CrossArtistSource`.
- Audio analysis pipeline:
  - `AudioAnalyzer` with librosa (`beat_track` + `chroma_cqt`) for BPM/Key.
  - `EnergyAnalyzer` with librosa RMS for precise energy.
  - Cache system (`analysis_cache.json` + `audio_segments/`).
- Pre-filtering with `SongFilter` (Camelot Wheel + BPM scoring + dynamic weights).
- Cascade expansion when candidate pool is insufficient.
- Anchor songs automatically placed at the start of the final playlist.

## [0.1.0] - 2026-04-20

### Added

- Initial release.
- CLI with `typer` and `rich`.
- Basic anchor song resolution (by ID or "Artist - Song" search).
- Similar-song-based playlist creation via MCP Server.
- Simple deduplication and diversity controls.
