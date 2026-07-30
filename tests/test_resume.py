"""Resuming an interrupted end-of-session recap build."""
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _mk_session(root, name, clips=(), session_reel=False):
    sdir = os.path.join(root, "Marathon Sessions", name)
    os.makedirs(sdir, exist_ok=True)
    for c in clips:
        open(os.path.join(sdir, c), "wb").write(b"x")
    if session_reel:
        open(os.path.join(sdir, "session_reel.mp4"), "wb").write(b"x")
    return sdir


def test_finds_interrupted_session(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-23_19-00-00", ["001_down_19-10-00.mkv"], session_reel=True)
    cut = _mk_session(root, "2026-07-24_20-00-00", ["001_down_20-10-00.mkv",
                                                    "002_kill_20-20-00.mkv"])
    assert main.find_unfinished_session(root) == cut


def test_complete_session_means_nothing_to_do(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-24_20-00-00", ["001_down_20-10-00.mkv"], session_reel=True)
    assert main.find_unfinished_session(root) == ""


def test_no_clips_no_resume(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-24_20-00-00", [])          # exfil PNGs only, say
    assert main.find_unfinished_session(root) == ""
    assert main.find_unfinished_session("") == ""


def test_older_gaps_ignored(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-20_20-00-00", ["001_down_20-10-00.mkv"])  # old, unfinished
    _mk_session(root, "2026-07-24_20-00-00", ["001_down_20-10-00.mkv"], session_reel=True)
    assert main.find_unfinished_session(root) == ""       # newest is complete -> done


def test_tidied_session_is_still_resumable(tmp_path):
    """Regression from the tidy commit: find_unfinished_session scanned only the
    TOP LEVEL for clips, but tidy moves them into clips/ — so a session that was
    interrupted AND tidied became invisible and could never be resumed."""
    import tidy
    root = tmp_path / "Marathon Sessions" / "2026-07-29_20-00-15"
    root.mkdir(parents=True)
    (root / "001_down_20-07-34.mkv").write_bytes(b"x")
    (root / "exfil_20-15-19.png").write_bytes(b"x")
    tidy.tidy_session(str(root))
    assert (root / "clips" / "001_down_20-07-34.mkv").exists()
    # No .recap_done and no session_reel.mp4 -> must be offered for resume.
    assert main.find_unfinished_session(str(tmp_path)) == str(root)


def test_a_failed_recap_is_not_marked_done(tmp_path):
    """The 2026-07-29 failure mode: every reel raised, each except printed, and
    .recap_done was written anyway — so the session looked complete forever."""
    root = tmp_path / "Marathon Sessions" / "sess"
    root.mkdir(parents=True)
    (root / "001_down_20-07-34.mkv").write_bytes(b"x")
    cfg = {"make_montage": False, "play_of_the_night": False,
           "make_shorts": False, "make_session_reel": True,
           "tidy_session_folder": False, "witness_report": False}

    # Force the session-reel stage to fail the way it did that night.
    orig = main._build_session_reel_and_upload
    main._build_session_reel_and_upload = lambda *a, **k: False
    try:
        main._build_session_artifacts(cfg, str(root), ["down"], rc=None)
    finally:
        main._build_session_reel_and_upload = orig

    assert not (root / ".recap_done").exists(), "a failed recap must not be 'done'"
    assert (root / ".recap_failed").exists()
    assert "session reel" in (root / ".recap_failed").read_text()
    # And that means it is offered for resume/rebuild.
    assert main.find_unfinished_session(str(tmp_path)) == str(root)


def test_rebuild_reports_each_stage_and_refuses_a_second_run(tmp_path):
    """Stan asked "does it say the status of the rebuild anywhere?" — it must,
    per stage. A multi-minute render showing one unchanging message is
    indistinguishable from a hang.

    Also covers two bugs found writing this: set_recap takes KEYWORDS (the first
    version passed a dict and would have raised TypeError on the first click),
    and rebuild_session passed rc=None so no stage note ever reached the UI."""
    import time
    import webserver

    sess = tmp_path / "Marathon Sessions" / "s1"
    sess.mkdir(parents=True)
    (sess / "001_down_20-07-34.mkv").write_bytes(b"x")

    st = webserver.LiveState()
    st._cfg = {}
    orig_cached = main.cached_record_dir
    orig_build = main._build_session_artifacts
    main.cached_record_dir = lambda: str(tmp_path)

    notes = []

    def slow(cfg, sdir, tags, rc=None, report_speech=""):
        for note in ("building session montage…", "cutting Play of the Night…"):
            if rc:
                rc(status="building", note=note)
            notes.append(note)
            time.sleep(0.15)
        (pathlib.Path(sdir) / ".recap_done").write_text("x")

    main._build_session_artifacts = slow
    try:
        assert st.request_rebuild("s1")["ok"] is True
        time.sleep(0.05)
        # A second click while one is running must be refused, not queued.
        second = st.request_rebuild("s1")
        assert second["ok"] is False
        assert "already running" in second["error"]
        for _ in range(60):
            if st.recap.get("status") in ("ready", "error"):
                break
            time.sleep(0.05)
        assert st.recap.get("status") == "ready", st.recap
        assert notes == ["building session montage…", "cutting Play of the Night…"]
    finally:
        main.cached_record_dir = orig_cached
        main._build_session_artifacts = orig_build


def test_rebuild_reports_a_missing_session_instead_of_going_quiet(tmp_path):
    import webserver
    st = webserver.LiveState()
    st._cfg = {}
    orig = main.cached_record_dir
    main.cached_record_dir = lambda: str(tmp_path)
    try:
        main.rebuild_session({}, "nope-does-not-exist", rc=st.set_recap)
        assert st.recap.get("status") == "error", st.recap
        assert st.recap.get("note")
    finally:
        main.cached_record_dir = orig
