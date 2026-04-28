"""Integration tests for new sources and arranger features."""

import asyncio
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from dj_set_curator.sources import (
    PlaylistSource,
    StyleSongSource,
    CrossArtistSource,
    GenreSearchSource,
)
from dj_set_curator.arranger import SongStructureAnalyzer
from dj_set_curator.models import Song, ScoredSong


class TestPlaylistSource:
    """Tests for PlaylistSource."""

    @pytest.fixture
    def mcp(self):
        m = MagicMock()
        m.search_playlist = AsyncMock()
        m.get_playlist_songs = AsyncMock()
        return m

    @pytest.mark.asyncio
    async def test_playlist_source_searches_and_extracts(self, mcp):
        """PlaylistSource should search playlists and extract tracks."""
        mcp.search_playlist.return_value = [
            {"id": "1001", "name": "Best of Artist", "track_count": 20},
        ]
        mcp.get_playlist_songs.return_value = [
            {"id": "2001", "name": "Track A", "artist": "DJ Z"},
            {"id": "2002", "name": "Track B", "artist": "DJ Y"},
        ]

        anchor = {"id": "1", "name": "Anchor", "artist": "Artist"}
        src = PlaylistSource(mcp)
        songs = await src.collect(anchor)

        assert len(songs) == 2
        assert songs[0].name == "Track A"
        assert songs[1].name == "Track B"

    @pytest.mark.asyncio
    async def test_playlist_source_handles_empty_search(self, mcp):
        """Should handle empty playlist search gracefully."""
        mcp.search_playlist.return_value = []

        anchor = {"id": "1", "name": "Anchor", "artist": "Artist"}
        src = PlaylistSource(mcp)
        songs = await src.collect(anchor)

        assert songs == []


class TestStyleSongSource:
    """Tests for StyleSongSource."""

    @pytest.fixture
    def mcp(self):
        m = MagicMock()
        m.get_song_wiki = AsyncMock(return_value={"genres": ["House"]})
        m.get_style_songs = AsyncMock()
        return m

    @pytest.fixture
    def hierarchy(self):
        h = MagicMock()
        h.is_loaded.return_value = True
        node = MagicMock()
        node.tag_id = 100
        h.find.side_effect = lambda x: node if "house" in str(x).lower() else None
        return h

    @pytest.mark.asyncio
    async def test_style_source_fetches_by_tag(self, mcp, hierarchy):
        """StyleSongSource should fetch songs by style tag."""
        mcp.get_style_songs.return_value = [
            {"id": "3001", "name": "Deep House Jam", "artist": "DJ A"},
            {"id": "3002", "name": "Tech House", "artist": "DJ B"},
        ]

        anchor = {"id": "1", "name": "Anchor", "artist": "Artist", "genre_tags": ["House"]}
        src = StyleSongSource(mcp, hierarchy=hierarchy)
        songs = await src.collect(anchor)

        assert len(songs) == 2
        mcp.get_style_songs.assert_called_once()

    @pytest.mark.asyncio
    async def test_style_source_skips_if_no_hierarchy(self, mcp):
        """Should skip if hierarchy is not loaded."""
        anchor = {"id": "1", "name": "Anchor", "artist": "Artist", "genre_tags": ["House"]}
        src = StyleSongSource(mcp, hierarchy=None)
        songs = await src.collect(anchor)

        assert songs == []
        mcp.get_style_songs.assert_not_called()


class TestGenreSearchSource:
    """Tests for GenreSearchSource style tree + BPM fallback."""

    @pytest.fixture
    def mcp(self):
        m = MagicMock()
        m.search_song = AsyncMock()
        m.get_style_songs = AsyncMock()
        m.get_song_wiki = AsyncMock()
        return m

    @pytest.fixture
    def hierarchy(self):
        h = MagicMock()
        h.is_loaded.return_value = True
        node = MagicMock()
        node.tag_id = 200
        h.find.side_effect = lambda x: node if "techno" in str(x).lower() else None
        return h

    @pytest.mark.asyncio
    async def test_genre_source_uses_style_tree_when_loaded(self, mcp, hierarchy):
        """Should use style tree when hierarchy is loaded and genre tags exist."""
        mcp.get_style_songs.return_value = [
            {"id": "4001", "name": "Techno Rave", "artist": "DJ X"},
        ]

        anchor = {"id": "1", "name": "Anchor", "artist": "Artist", "genre_tags": ["Techno"]}
        src = GenreSearchSource(mcp, hierarchy=hierarchy)
        songs = await src.collect(anchor)

        assert len(songs) == 1
        assert songs[0].name == "Techno Rave"
        mcp.get_style_songs.assert_called()
        mcp.search_song.assert_not_called()

    @pytest.mark.asyncio
    async def test_genre_source_falls_back_to_bpm_when_no_hierarchy(self, mcp):
        """Should fallback to BPM mapping when hierarchy is not loaded."""
        from dj_set_curator.models import Song
        mcp.search_song.return_value = [
            Song(id="5001", name="128 BPM Track", artist="DJ Y"),
        ]

        anchor = {"id": "1", "name": "Anchor", "artist": "Artist", "bpm": 128}
        src = GenreSearchSource(mcp, hierarchy=None)
        songs = await src.collect(anchor)

        assert len(songs) == 1
        assert songs[0].name == "128 BPM Track"
        mcp.search_song.assert_called()

    @pytest.mark.asyncio
    async def test_genre_source_skips_without_bpm_and_hierarchy(self, mcp):
        """Should skip if anchor has no BPM and no hierarchy."""
        anchor = {"id": "1", "name": "Anchor", "artist": "Artist"}
        src = GenreSearchSource(mcp, hierarchy=None)
        songs = await src.collect(anchor)

        assert songs == []
        mcp.search_song.assert_not_called()


class TestSequentialSelectorDynamicCurve:
    """Tests for SequentialSelector with dynamic curves."""

    def test_dynamic_curve_based_on_anchor_energies(self):
        """When anchor_energies provided, curve should be centered on anchor mean."""
        from dj_set_curator.transition import SequentialSelector, TransitionScorer
        scorer = TransitionScorer(bpm_tolerance=5.0)
        selector = SequentialSelector(
            scorer, arrange_mode="warm-up", anchor_energies=[55.0, 60.0, 58.0]
        )
        curve = selector._get_target_energies(10)
        assert min(curve) >= 10
        assert max(curve) <= 95
        assert all(10 <= e <= 95 for e in curve)
        # 均值应该接近锚点均值 57.7
        assert abs(np.mean(curve) - 57.7) < 10

    def test_fixed_curve_without_anchor_energies(self):
        """Without anchor_energies, should use fixed curve."""
        from dj_set_curator.transition import SequentialSelector, TransitionScorer
        scorer = TransitionScorer(bpm_tolerance=5.0)
        selector = SequentialSelector(scorer, arrange_mode="flat")
        curve = selector._get_target_energies(10)
        assert all(e == 50.0 for e in curve)


class TestSongStructureAnalyzer:
    """Tests for SongStructureAnalyzer."""

    @pytest.fixture
    def mcp(self):
        return MagicMock()

    @patch("dj_set_curator.arranger.librosa.load")
    @patch("dj_set_curator.arranger.librosa.get_duration")
    @patch("dj_set_curator.arranger.librosa.onset.onset_strength")
    def test_structure_detection(self, mock_onset, mock_duration, mock_load, mcp):
        """Should detect intro and breakdown from mock audio."""
        sr = 22050
        y = np.zeros(sr * 30)
        # Intro: 0-8s 低能量
        y[:sr*8] = np.random.normal(0, 0.01, sr*8)
        # Main: 8-20s 高能量
        y[sr*8:sr*20] = np.random.normal(0, 0.5, sr*12)
        # Breakdown: 20-24s 低能量
        y[sr*20:sr*24] = np.random.normal(0, 0.02, sr*4)
        # Outro: 24-30s 高能量
        y[sr*24:] = np.random.normal(0, 0.5, sr*6)

        mock_load.return_value = (y, sr)
        mock_duration.return_value = 30.0

        # Mock onset strength
        onset = np.zeros(len(y))
        onset[sr*8:sr*20] = 0.5
        onset[sr*20:sr*24] = 0.02
        onset[sr*24:] = 0.5
        mock_onset.return_value = onset

        analyzer = SongStructureAnalyzer(mcp)
        result = analyzer._analyze_structure_sync("/fake/path.mp3")

        assert result is not None
        assert result["duration"] == 30.0
        assert result["intro_sec"] > 0
        assert result["has_breakdown"] is True

    def test_structure_handles_failure(self, mcp):
        """Should return None on analysis failure."""
        analyzer = SongStructureAnalyzer(mcp)
        result = analyzer._analyze_structure_sync("/nonexistent/file.mp3")
        assert result is None
