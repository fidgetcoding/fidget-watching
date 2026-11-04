"""Maintenance regressions. All media/network side effects use temporary fixtures or mocks."""
import json
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import channel, download, frames, library, resolve, scenes, sections, setup, transcribe, watch


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    root = tmp_path / 'library'
    monkeypatch.setattr(library, 'LIBRARY_ROOT', root)
    video = tmp_path / 'input.mp4'
    video.write_bytes(b'video')
    source_state = {'source_hash': 'original'}
    monkeypatch.setattr(resolve, 'resolve_source', lambda source, focus_range: {
        'title': 'Test', 'duration_s': 100.0, 'source': str(video), 'is_url': False,
        'source_hash': source_state['source_hash'],
        'focus_range_str': '' if focus_range is None else f'{focus_range[0]}-{focus_range[1]}',
    })
    detect = Mock(return_value=[scenes.Scene(0, 1, 'detected'), scenes.Scene(30, .8, 'detected'), scenes.Scene(60, .7, 'detected')])
    monkeypatch.setattr(scenes, 'detect_scenes', detect)
    monkeypatch.setattr(frames, 'video_aspect', lambda _: .75)
    def extract(video, cuts, *, out_dir, width_px):
        out_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for i, cut in enumerate(cuts, 1):
            name = frames.format_filename(i, cut.t)
            (out_dir / name).write_bytes(b'jpeg')
            result.append({'index': i, 't': cut.t, 'kind': cut.kind, 'path': name})
        return result
    extract_mock = Mock(side_effect=extract)
    monkeypatch.setattr(frames, 'extract_frames', extract_mock)
    args = SimpleNamespace(start=None, end=None, max_frames=None, resolution=512, scene_threshold=.3,
        max_gap=45., no_whisper=True, whisper=None, no_sections=True, section_minutes=None)
    def run():
        return watch._run_one_video(str(video), args)
    return SimpleNamespace(run=run, args=args, detect=detect, extract=extract_mock, root=root, video=video, source_state=source_state)


def test_missing_cached_video_recovers(pipeline):
    first = pipeline.run()
    work = Path(first['library_dir'])
    for path in (work / 'source').iterdir():
        path.unlink()
    assert pipeline.run()['frames']


@pytest.mark.parametrize('bad', ['{', '{}', 'null', '[{"text": "missing timestamps"}]'])
def test_malformed_transcript_cache_recovers(pipeline, bad):
    work = Path(pipeline.run()['library_dir'])
    (work / 'transcript.json').write_text(bad)
    pipeline.run()
    assert json.loads((work / 'transcript.json').read_text()) == []


def test_scene_budget_can_increase_on_rerun(pipeline):
    pipeline.args.max_frames = 1
    assert len(pipeline.run()['frames']) == 1
    pipeline.args.max_frames = 10
    assert len(pipeline.run()['frames']) == 3
    assert pipeline.detect.call_count == 1


def test_scene_threshold_change_redetects(pipeline):
    pipeline.run()
    pipeline.args.scene_threshold = .8
    pipeline.run()
    assert pipeline.detect.call_count == 2


def test_identical_rerun_reuses_images(pipeline):
    pipeline.run()
    pipeline.run()
    assert pipeline.extract.call_count == 1


def test_missing_image_regenerates_frames(pipeline):
    result = pipeline.run()
    (Path(result['library_dir']) / result['frames'][0]['path']).unlink()
    pipeline.run()
    assert pipeline.extract.call_count == 2


def test_failed_frame_regeneration_preserves_previous_images(pipeline):
    result = pipeline.run()
    work = Path(result['library_dir'])
    before = {p.name: p.read_bytes() for p in (work / 'frames').iterdir()}
    pipeline.args.resolution = 1024
    pipeline.extract.side_effect = RuntimeError('synthetic extraction failure')
    with pytest.raises(RuntimeError, match='synthetic'):
        pipeline.run()
    assert {p.name: p.read_bytes() for p in (work / 'frames').iterdir()} == before


@pytest.mark.parametrize('flag,value', [('--max-gap', '0'), ('--max-gap', '-1'), ('--max-gap', 'nan'),
    ('--max-gap', 'inf'), ('--resolution', '0'), ('--max-frames', '0'), ('--limit', '-1'),
    ('--scene-threshold', 'nan'), ('--scene-threshold', '1.1'), ('--section-minutes', '0'),
    ('--start', 'nan'), ('--start', '-1'), ('--start', '1:90'), ('--end', '0')])
def test_invalid_cli_values_rejected_before_work(flag, value, monkeypatch, tmp_path):
    monkeypatch.setattr(watch, '_run_one_video', Mock(side_effect=AssertionError('pipeline started')))
    with pytest.raises(SystemExit) as e:
        watch.main(['input.mp4', flag, value, '--out-dir', str(tmp_path / 'lib')])
    assert e.value.code == 2
    assert not (tmp_path / 'lib').exists()


def test_focus_beyond_duration_fails_before_extraction(pipeline):
    pipeline.args.start = '120'
    with pytest.raises(ValueError, match='duration'):
        pipeline.run()
    pipeline.extract.assert_not_called()


@pytest.mark.parametrize('timestamp', [0., 3.])
def test_ffmpeg_success_without_image_is_failure(tmp_path, monkeypatch, timestamp):
    monkeypatch.setattr(frames.subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 0, b'', b'')))
    with pytest.raises(RuntimeError, match='frame'):
        frames.extract_frames(tmp_path / 'video', [scenes.Scene(timestamp, 1, 'detected')], out_dir=tmp_path/'frames')


def test_download_rejects_partial_sidecars(tmp_path, monkeypatch):
    (tmp_path / 'video.mp4.part').write_bytes(b'partial')
    (tmp_path / 'video.info.json').write_text('{}')
    monkeypatch.setattr(download.subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 0, '', '')))
    with pytest.raises(RuntimeError, match='no .*video'):
        download.download_video('https://example.test/video', tmp_path)


def test_local_source_change_changes_cache_hash(tmp_path, monkeypatch):
    video = tmp_path/'video.mp4'
    video.write_bytes(b'first')
    monkeypatch.setattr(resolve.subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 0, '10', '')))
    first = resolve.resolve_source(str(video), None)
    video.write_bytes(b'different contents')
    assert resolve.resolve_source(str(video), None)['source_hash'] != first['source_hash']


def test_long_titles_fit_one_path_component():
    slug = library.slug_for({'title': 'a' * 400, 'source': 'local'})
    assert len(slug.encode()) <= 255


def test_channel_handles_nested_null_and_duplicate_entries():
    entry = {'id': 'a', 'url': 'https://example.test/a'}
    info = {'_type': 'playlist', 'entries': [None, {'_type': 'playlist', 'entries': [None, entry, entry]}, {'id': 'b', 'url': 'https://example.test/b'}]}
    assert [v['id'] for v in channel.enumerate_videos(info)] == ['a', 'b']


def test_single_entry_playlist_is_a_playlist():
    assert channel.is_channel_or_playlist({'_type': 'playlist', 'entries': [{'id': 'a'}]})


def test_vtt_fractional_seconds_and_entities():
    cue = transcribe.parse_vtt('WEBVTT\n\n00:00.1 --> 00:01.25\nA &amp; B &lt; C\n')[0]
    assert cue == {'t_start': .1, 't_end': 1.25, 'text': 'A & B < C'}


def test_repeated_words_after_pause_are_not_removed():
    cues = [{'t_start': 0, 't_end': 1, 'text': 'yes'}, {'t_start': 60, 't_end': 61, 'text': 'yes'}]
    assert transcribe.dedupe_cues(cues) == cues


def test_section_cuts_stay_ordered_with_long_cue():
    cuts = sections.plan_cuts(300, 3000, n_frames=5, transcript=[{'t_start': 0, 't_end': 3000, 'text':'long'}], scenes=[], target_s=600)
    assert cuts[0] == 300 and cuts[-1] == 3000
    assert all(a < b for a,b in zip(cuts, cuts[1:]))


def test_equal_length_transcript_change_invalidates_notes(tmp_path):
    def plan(text):
        return sections.build_plan([0, 1200], frames=[], transcript=[{'t_start': 1, 't_end': 2, 'text': text}], tokens_per_frame=200)
    sections.write_plan(tmp_path, plan('alpha'))
    notes = tmp_path/'sections/01.notes.md'
    notes.write_text('old notes')
    sections.write_plan(tmp_path, plan('bravo'))
    assert not notes.exists()


def test_setup_false_is_first_run(monkeypatch):
    monkeypatch.setattr(setup, '_read_env', lambda: {'SETUP_COMPLETE':'false'})
    monkeypatch.setattr(setup, '_which', lambda _: None)
    monkeypatch.setattr(setup, '_resolve_local_model', lambda _: None)
    assert setup.status_for()['first_run']


def test_install_maps_ffprobe_to_ffmpeg(monkeypatch):
    monkeypatch.setattr(setup.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(setup, '_which', lambda b: '/bin/'+b if b in ('brew','yt-dlp') else None)
    install = Mock()
    monkeypatch.setattr(setup.subprocess, 'run', install)
    setup._print_install_instructions()
    assert install.call_args.args[0] == ['brew', 'install', 'ffmpeg']


def test_entry_lock_excludes_a_second_process_and_releases(tmp_path):
    import sys
    from scripts.storage import entry_lock
    work = tmp_path/'entry'
    code = 'from pathlib import Path; from scripts.storage import entry_lock; import sys\nwith entry_lock(Path(sys.argv[1])): pass\n'
    def child():
        return subprocess.run([sys.executable, '-c', code, str(work)], capture_output=True, text=True, timeout=15)
    with entry_lock(work):
        busy = child()
        assert busy.returncode != 0 and 'another watch run' in busy.stderr
    assert child().returncode == 0


def test_entry_lock_releases_after_exception(tmp_path):
    from scripts.storage import entry_lock
    with pytest.raises(ValueError):
        with entry_lock(tmp_path):
            raise ValueError('test')
    with entry_lock(tmp_path):
        pass


def test_atomic_write_failure_keeps_existing_file(tmp_path, monkeypatch):
    from scripts import storage
    path = tmp_path/'meta.json'
    path.write_text('{"old": true}')
    monkeypatch.setattr(storage.os, 'replace', Mock(side_effect=OSError('disk error')))
    with pytest.raises(OSError):
        storage.write_json(path, {'new': True})
    assert path.read_text() == '{"old": true}'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('bad', ['{', 'null', '[]'])
def test_bad_meta_is_cache_miss(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(library, 'LIBRARY_ROOT', tmp_path)
    work = tmp_path/'entry'
    work.mkdir()
    (work/'meta.json').write_text(bad)
    assert library.cache_lookup('entry', 'hash') is None


def test_channel_probe_passes_limit_to_downloader(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{}', ''))
    monkeypatch.setattr(channel.subprocess, 'run', run)
    channel.probe('https://example.test/channel', limit=7)
    cmd = run.call_args.args[0]
    assert cmd[cmd.index('--playlist-end') + 1] == '7'
    assert run.call_args.kwargs['timeout'] == 120


@pytest.mark.parametrize('value', [0, -1, math.nan, math.inf])
def test_coverage_floor_rejects_nonpositive_or_nonfinite_gaps(value):
    with pytest.raises(ValueError):
        scenes.apply_coverage_floor([scenes.Scene(0, 1, 'detected')], 100, value)


def test_scene_scan_failure_is_not_cached_as_a_static_video(monkeypatch, tmp_path):
    monkeypatch.setattr(scenes.subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 1, '', 'decode failed')))
    with pytest.raises(RuntimeError, match='scene scan failed'):
        scenes.detect_scenes(tmp_path/'video')


@pytest.mark.parametrize('header', ['-2', 'nan', 'inf', 'garbage'])
def test_retry_after_is_finite_and_nonnegative(header):
    from urllib.error import HTTPError
    from scripts import whisper
    wait = whisper._retry_wait(HTTPError('https://example.test', 429, 'busy', {'Retry-After':header}, None), 1)
    assert math.isfinite(wait) and 0 <= wait <= whisper.MAX_RETRY_WAIT_S


def test_upload_budget_leaves_room_under_decimal_25mb():
    from scripts import whisper
    assert whisper.MAX_UPLOAD_BYTES + 10000 < 25_000_000


@pytest.mark.parametrize('payload', ['[]', '{"transcription":[{"text":"bad"}]}', '{"transcription":[{"text":"bad", "offsets":{"from": 0, "to": -1}}]}'])
def test_bad_local_whisper_output_is_a_backend_error(tmp_path, monkeypatch, payload):
    from scripts import whisper
    model = tmp_path/'model.bin'
    model.write_bytes(b'model')
    def fake_run(cmd, **kwargs):
        if '-of' in cmd:
            Path(cmd[cmd.index('-of')+1] + '.json').write_text(payload)
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(whisper.subprocess, 'run', fake_run)
    with pytest.raises(whisper.WhisperError, match='JSON parse failed'):
        whisper.transcribe_local(tmp_path/'audio', model_path=model)


def test_library_reuses_entry_across_dates_and_titles(tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'LIBRARY_ROOT', tmp_path)
    meta = {'title':'First title', 'source':'https://example.test/a', 'focus_range_str':'', 'watched_at':'2026-01-01'}
    entry = library.entry_for(meta)
    entry.mkdir()
    (entry/'meta.json').write_text(json.dumps(meta))
    assert library.entry_for({**meta, 'watched_at':'2026-02-01', 'title':'Updated title'}) == entry


def test_library_hash_collision_does_not_overwrite_other_source(tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'LIBRARY_ROOT', tmp_path)
    monkeypatch.setattr(library, 'slug_for', lambda _: 'date-title-abcd')
    entry = tmp_path/'date-title-abcd'
    entry.mkdir()
    (entry/'meta.json').write_text(json.dumps({'source':'https://example.test/old'}))
    assert library.entry_for({'source':'https://example.test/new'}) != entry


@pytest.mark.parametrize('header', ["{'Cookie': 'fixture-private-value'}", '{"Set-Cookie": "fixture-private-value"}', 'Authorization: Bearer fixture-private-value'])
def test_quoted_cookie_and_authorization_headers_are_scrubbed(header):
    from scripts import access
    assert 'fixture-private-value' not in access.scrub('[debug] Headers: ' + header)


def test_channel_failure_does_not_retry_as_a_single_video(monkeypatch, tmp_path):
    monkeypatch.setattr(channel, 'probe', lambda *args, **kwargs: {'_type': 'playlist', 'entries': []})
    monkeypatch.setattr(watch, '_run_channel', Mock(side_effect=RuntimeError('index write failed')))
    single = Mock(side_effect=AssertionError('retried as single'))
    monkeypatch.setattr(watch, '_run_one_video', single)
    assert watch.main(['https://example.test/channel', '--out-dir', str(tmp_path/'lib')]) == 1
    single.assert_not_called()


def test_changed_video_invalidates_section_notes_even_with_same_transcript(pipeline):
    pipeline.args.no_sections = False
    pipeline.args.section_minutes = .5
    work = Path(pipeline.run()['library_dir'])
    notes = work/'sections/01.notes.md'
    notes.write_text('old slide explanation')
    pipeline.source_state['source_hash'] = 'new-video-with-different-slides'
    pipeline.run()
    assert not notes.exists()
