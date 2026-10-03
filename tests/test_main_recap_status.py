"""The recap tells the truth about what got built.

Before: _end_session always finished with status 'ready' and an empty note,
which the dashboard draws as "Everything built" — after failed stages, after
a night with no ffmpeg, after zero kills, after a dry run. And a recap that
kept failing was re-rendered at every launch, forever.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import stats  # noqa: E402

# every stage off except the one a test turns on; nothing opens or uploads
QUIET = {"make_card": False, "witness_report": False, "make_montage": False,
         "play_of_the_night": False, "make_shorts": False,
         "make_session_reel": False, "tidy_session_folder": False}


class _Web:
    def __init__(self):
        self.recaps = []

    def set_recap(self, **kw):
        self.recaps.append(kw)

    @property
    def final(self):
        status = note = None
        for kw in self.recaps:
            status = kw.get("status", status)
            note = kw.get("note", note)
        return status, note


class _Obs:
    def __init__(self, rec):
        self.rec = rec

    def get_record_directory(self):
        return self.rec


def _session(tmp_path, name="s1", clips=("001_down_20-07-34.mkv",)):
    sdir = tmp_path / "Marathon Sessions" / name
    sdir.mkdir(parents=True)
    for c in clips:
        (sdir / c).write_bytes(b"x")
    return sdir


def _end(monkeypatch, tmp_path, cfg, tags=("down",), kills=1, dry_run=False,
         health_note=""):
    monkeypatch.setattr(stats, "record_session", lambda base, session: "recap.html")
    web = _Web()
    main._end_session(dict(cfg), list(tags), start_monotonic=0.0,
                      start_wall="20:00", dry_run=dry_run,
                      obs=_Obs(str(tmp_path)), session_id="s1", kills=kills,
                      web=web, health_note=health_note)
    return web


def test_a_failing_stage_is_an_error_with_the_reason(monkeypatch, tmp_path):
    _session(tmp_path)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    monkeypatch.setattr(main, "_build_session_reel_and_upload",
                        lambda *a, **k: False)
    web = _end(monkeypatch, tmp_path, dict(QUIET, make_session_reel=True))
    status, note = web.final
    assert status == "error"
    assert note.startswith("1 stage(s) failed:") and "session reel" in note


def test_a_clean_build_is_ready_with_no_note(monkeypatch, tmp_path):
    sdir = _session(tmp_path)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    monkeypatch.setattr(main, "_build_session_reel_and_upload",
                        lambda *a, **k: True)
    web = _end(monkeypatch, tmp_path, dict(QUIET, make_session_reel=True))
    assert web.final == ("ready", "")
    assert (sdir / ".recap_done").exists()


def test_no_kills_and_dry_run_say_so(monkeypatch, tmp_path):
    web = _end(monkeypatch, tmp_path, QUIET, tags=(), kills=0)
    assert web.final == ("ready", "No kills this session - nothing to build")
    web = _end(monkeypatch, tmp_path, QUIET, kills=3, dry_run=True)
    assert web.final == ("ready", "Dry run - nothing was saved")
    # neither creates a session folder or a marker
    assert not (tmp_path / "Marathon Sessions").exists()


def test_the_canary_verdict_reaches_the_final_recap(monkeypatch, tmp_path):
    msg = "DETECTION LOOKS BROKEN - the game credited 6 kill(s) and WITNESS detected 0"
    web = _end(monkeypatch, tmp_path, QUIET, tags=(), kills=0, health_note=msg)
    status, note = web.final
    assert status == "ready" and note.startswith(msg)
    assert "No kills this session" in note


def test_a_failed_montage_with_clips_on_disk_counts_as_a_failure(monkeypatch, tmp_path):
    import montage
    sdir = _session(tmp_path)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    monkeypatch.setattr(montage, "build_montage", lambda d, ff: None)
    failures = main._build_session_artifacts(dict(QUIET, make_montage=True), str(sdir),
                                             ["down"])
    assert failures and failures[0].startswith("montage")
    assert (sdir / ".recap_failed").exists()


def test_a_montage_with_nothing_to_stitch_is_not_a_failure(monkeypatch, tmp_path):
    import montage
    sdir = _session(tmp_path, clips=())
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    monkeypatch.setattr(montage, "build_montage", lambda d, ff: None)
    assert main._build_session_artifacts(dict(QUIET, make_montage=True), str(sdir),
                                         []) == []


def test_rebuilding_a_tidied_session_is_not_a_montage_failure(monkeypatch, tmp_path):
    # tidy_session runs after every recap (failed ones too) and moves the clips
    # into clips/. build_montage lists only the top level, so a resume or a
    # 'Rebuild reels' of that session finds nothing to stitch. Counting clips/
    # as "clips on disk" failed every such rebuild and wrote .recap_failed,
    # which the attempt cap then made permanent. Real build_montage, no stub.
    sdir = tmp_path / "Marathon Sessions" / "s1"
    (sdir / "clips").mkdir(parents=True)
    for c in ("001_down_20-05-00.mkv", "002_down+finisher_20-09-00.mkv"):
        (sdir / "clips" / c).write_bytes(b"x")
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    assert main._session_clips_from_dir(str(sdir))      # the clips ARE there
    assert main._build_session_artifacts(dict(QUIET, make_montage=True), str(sdir),
                                         ["down", "down", "finisher"]) == []
    assert not (sdir / ".recap_failed").exists()
    assert (sdir / ".recap_done").exists()


# --- auto-resume is capped -------------------------------------------------------

def test_a_failed_recap_is_resumed_once_then_left_to_rebuild(monkeypatch, tmp_path):
    sdir = _session(tmp_path)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    monkeypatch.setattr(main, "_build_session_reel_and_upload",
                        lambda *a, **k: False)
    cfg = dict(QUIET, make_session_reel=True)
    main._build_session_artifacts(cfg, str(sdir), ["down"])     # end of session
    assert (sdir / ".recap_failed").read_text().splitlines()[0].endswith("attempts=1")
    assert main.find_unfinished_session(str(tmp_path)) == str(sdir)   # still offered
    main._build_session_artifacts(cfg, str(sdir), ["down"])     # the launch resume
    assert "attempts=2" in (sdir / ".recap_failed").read_text().splitlines()[0]
    assert main.find_unfinished_session(str(tmp_path)) == ""    # not a third time
    # the reasons are still where rebuild_session reads them (lines 2+)
    assert (sdir / ".recap_failed").read_text().splitlines()[1] == "session reel"


def test_an_old_failure_file_counts_as_one_attempt(tmp_path):
    sdir = _session(tmp_path)
    (sdir / ".recap_failed").write_text("2026-07-29 23:00:00\nsession reel\n")
    assert main._recap_attempts(str(sdir)) == 1
    assert main.find_unfinished_session(str(tmp_path)) == str(sdir)


def test_a_successful_resume_clears_the_stale_failure(monkeypatch, tmp_path):
    sdir = _session(tmp_path)
    (sdir / ".recap_failed").write_text("2026-07-29 23:00:00 attempts=1\nshorts: x\n")
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    assert main._build_session_artifacts(dict(QUIET), str(sdir), ["down"]) == []
    assert (sdir / ".recap_done").exists()
    assert not (sdir / ".recap_failed").exists()


def test_resume_reports_an_error_instead_of_ready(monkeypatch, tmp_path):
    _session(tmp_path)
    monkeypatch.setattr(main, "cached_record_dir", lambda: str(tmp_path))
    monkeypatch.setattr(main, "load_config",
                        lambda path=None: dict(QUIET, make_session_reel=True))
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    monkeypatch.setattr(main, "_build_session_reel_and_upload",
                        lambda *a, **k: False)
    web = _Web()
    assert main.resume_unfinished_recap(web=web) is True
    status, note = web.final
    assert status == "error" and "session reel" in note


# --- no ffmpeg ---------------------------------------------------------------------

def test_no_ffmpeg_skips_the_renders_marks_skipped_and_still_tidies(monkeypatch, tmp_path):
    sdir = _session(tmp_path)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: False)
    called = []
    monkeypatch.setattr(main, "_build_session_reel_and_upload",
                        lambda *a, **k: called.append(1) or False)
    cfg = dict(QUIET, make_session_reel=True, tidy_session_folder=True)
    web = _Web()
    failures = main._build_session_artifacts(cfg, str(sdir), ["down"], rc=web.set_recap)
    assert failures == [] and called == []
    assert (sdir / ".recap_skipped").exists()
    assert not (sdir / ".recap_done").exists() and not (sdir / ".recap_failed").exists()
    assert (sdir / "clips" / "001_down_20-07-34.mkv").exists()      # tidied
    assert main.find_unfinished_session(str(tmp_path)) == ""        # not resumed
    assert main._recap_verdict(str(sdir), failures) == ("ready", main.FFMPEG_SKIP_NOTE)


def test_with_every_render_off_missing_ffmpeg_does_not_matter(monkeypatch, tmp_path):
    sdir = _session(tmp_path)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: False)
    assert main._build_session_artifacts(dict(QUIET), str(sdir), ["down"]) == []
    assert (sdir / ".recap_done").exists()
    assert not (sdir / ".recap_skipped").exists()


def test_a_rebuild_without_ffmpeg_reports_an_error(monkeypatch, tmp_path):
    sdir = _session(tmp_path)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: False)
    web = _Web()
    main.rebuild_session(dict(QUIET, make_session_reel=True), str(sdir),
                         rc=web.set_recap)
    status, note = web.final
    assert status == "error" and "ffmpeg not found" in note
