from pathlib import Path

from scripts import channel as channel_mod


def test_is_channel_single_video_returns_false():
    info = {"_type": "video", "id": "abc123", "title": "one video"}
    assert channel_mod.is_channel_or_playlist(info) is False


def test_is_channel_playlist_with_two_entries_true():
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "a", "title": "A"},
            {"id": "b", "title": "B"},
        ],
    }
    assert channel_mod.is_channel_or_playlist(info) is True


def test_is_channel_playlist_with_one_entry_true():
    info = {"_type": "playlist", "entries": [{"id": "a", "title": "A"}]}
    assert channel_mod.is_channel_or_playlist(info) is True


def test_enumerate_videos_flat_playlist():
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "abc", "title": "A", "duration": 60},
            {"id": "def", "title": "B", "duration": 120, "url": "https://youtu.be/def"},
        ],
    }
    out = channel_mod.enumerate_videos(info)
    assert len(out) == 2
    assert out[0]["id"] == "abc"
    assert out[0]["url"] == "https://youtu.be/abc"  # synthesized from id
    assert out[0]["title"] == "A"
    assert out[0]["duration"] == 60.0
    assert out[1]["url"] == "https://youtu.be/def"  # honored when provided


def test_enumerate_videos_channel_tabs_flattens_one_level():
    info = {
        "_type": "playlist",
        "entries": [
            {"_type": "playlist", "entries": [
                {"id": "v1", "title": "V1"},
                {"id": "v2", "title": "V2"},
            ]},
            {"id": "v3", "title": "V3"},  # mixed: a direct entry alongside a tab
        ],
    }
    out = channel_mod.enumerate_videos(info)
    assert [v["id"] for v in out] == ["v1", "v2", "v3"]


def test_enumerate_videos_respects_limit():
    info = {
        "_type": "playlist",
        "entries": [{"id": f"v{i}", "title": f"V{i}"} for i in range(20)],
    }
    out = channel_mod.enumerate_videos(info, limit=5)
    assert len(out) == 5
    assert out[-1]["id"] == "v4"


def test_enumerate_videos_skips_entries_without_id():
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "good", "title": "Good"},
            {"title": "No id"},
            {"id": "also_good", "title": "Also Good"},
        ],
    }
    out = channel_mod.enumerate_videos(info)
    assert [v["id"] for v in out] == ["good", "also_good"]


def test_channel_slug_is_stable_and_includes_hash():
    info = {"title": "Some Channel!"}
    slug = channel_mod.channel_slug(info, "https://www.youtube.com/@some")
    assert slug.startswith("channel-some-channel-")
    # Same inputs => same slug
    assert slug == channel_mod.channel_slug(info, "https://www.youtube.com/@some")
    # Different URL => different hash suffix
    assert slug != channel_mod.channel_slug(info, "https://www.youtube.com/@other")


def test_write_index_renders_video_table_and_failures(tmp_path: Path):
    results = [
        {
            "status": "ok",
            "title": "Good Video",
            "duration_s": 305.0,
            "library_dir": str(tmp_path / "lib" / "good-video"),
            "transcript_kind": "captions",
            "frames": [{}] * 42,
        },
        {
            "status": "ok",
            "title": "Pipe | In Title",
            "duration_s": 60.0,
            "library_dir": str(tmp_path / "lib" / "pipe-vid"),
            "transcript_kind": "whisper",
            "frames": [{}] * 7,
        },
        {
            "status": "error",
            "title": "Broken Video",
            "url": "https://youtu.be/broken",
            "error": "RuntimeError: yt-dlp 403",
        },
    ]
    path = channel_mod.write_index(
        tmp_path / "channel",
        channel_url="https://www.youtube.com/@test",
        channel_title="Test Channel",
        results=results,
        watched_at="2026-05-11",
    )
    body = path.read_text()
    assert "# Test Channel" in body
    assert "Videos:** 2/3" in body
    assert "Good Video" in body
    assert "5:05" in body  # 305s
    assert "Pipe \\| In Title" in body  # pipe escaped for markdown table
    assert "## Failed" in body
    assert "Broken Video" in body
    assert "yt-dlp 403" in body
    assert "Cross-channel synthesis" in body
