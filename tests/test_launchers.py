"""The Windows .bat launchers, checked without Windows.

cmd.exe can't run here or in CI on Linux, so these tests pin down the batch
traps that break a real install, by reading the files:

- _env.bat is `call`ed by every launcher. A `setlocal` in it would undo the
  venv activation; labels/goto misbehave once the updater saves it with LF
  line endings.
- Inside a ( ) block cmd expands %VAR% once, when the block is read, so a
  variable set in the block reads stale in the same block. Unescaped
  parentheses in echo text end a block early.
- Stan's RTX 5080 runs a cu128 torch. Nothing may --upgrade or reinstall a
  torch that already imports.
- The desktop shortcut runs START minimized, so a `pause` on the normal launch
  path looks like a hang. Pauses belong to failures and first-time setup.
"""

import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_BATS = sorted(f for f in os.listdir(BASE) if f.lower().endswith(".bat"))

# Owner decision: every existing launcher keeps its name (installs and the
# desktop shortcut point at them, and the updater never deletes files).
KEPT_NAMES = [
    "1 - Calibrate.bat", "2 - Test Screenshot.bat",
    "3 - Dry Run (test, saves nothing).bat",
    "4 - Benchmark (will my PC handle it).bat", "Check Voice.bat",
    "Check YouTube.bat", "Create Desktop Shortcut.bat", "Diagnose Capture.bat",
    "Rebuild Last Session.bat", "START Kill Recorder (Window).bat",
    "START Kill Recorder.bat", "Update WITNESS.bat", "_env.bat",
]

# These two pick the venv Python themselves and fall back to a system one.
# Everything else that runs Python goes through _env.bat.
OWN_PYTHON_FALLBACK = {"Check YouTube.bat", "Rebuild Last Session.bat"}


def _read(name):
    with open(os.path.join(BASE, name), "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")


def _lines(name):
    return _read(name).split("\n")


def _structural(line):
    """The line with quoted text and ^-escaped characters removed: what is
    left is what cmd's block parser actually reacts to."""
    out, quoted, i = [], False, 0
    while i < len(line):
        c = line[i]
        if c == '"':
            quoted = not quoted
        elif quoted:
            pass
        elif c == "^":
            i += 1  # the escaped character is literal
        else:
            out.append(c)
        i += 1
    s = "".join(out)
    # A for-loop's "in (...)" list is not a block.
    return re.sub(r"(?i)\bin\s*\([^()]*\)\s*do\b", " in do ", s)


def _is_rem(line):
    s = line.strip().lower()
    return s == "rem" or s.startswith("rem ")


def _parse_blocks(name):
    """Return (problems, top_level) where top_level is a list of
    (opener_line_index, [line indexes of the whole block]) and problems lists
    every paren that cmd would misread."""
    problems, top, depth, current = [], [], 0, None
    for i, raw in enumerate(_lines(name)):
        if _is_rem(raw):
            if depth and re.search(r"[()]", raw):
                problems.append(f"{name}:{i + 1}: REM with a paren inside a block")
            if current is not None:
                current[1].append(i)
            continue
        s = _structural(raw).strip()
        rest = s
        if rest.startswith(")"):
            if depth == 0:
                problems.append(f"{name}:{i + 1}: ')' closes nothing")
            depth = max(0, depth - 1)
            rest = rest[1:].strip()
        opens = rest.endswith("(")
        body = rest[:-1] if opens else rest
        if "(" in body or ")" in body:
            problems.append(f"{name}:{i + 1}: unescaped paren in text: {raw.strip()}")
        if depth == 0 and opens and current is None:
            current = (i, [])
        if current is not None:
            current[1].append(i)
        if opens:
            depth += 1
        if depth == 0 and current is not None:
            top.append(current)
            current = None
    if depth:
        problems.append(f"{name}: {depth} block(s) never closed")
    return problems, top


def _enclosing_opener(name, idx):
    """The top-level block opener text for line idx, or '' at top level."""
    lines = _lines(name)
    for opener, members in _parse_blocks(name)[1]:
        if idx in members:
            return lines[opener]
    return ""


def _block_text(name, idx):
    lines = _lines(name)
    for opener, members in _parse_blocks(name)[1]:
        if idx in members:
            return "\n".join(lines[j] for j in members)
    return lines[idx]


def _code_lines(name):
    return [(i, ln) for i, ln in enumerate(_lines(name))
            if ln.strip() and not _is_rem(ln)]


# --------------------------------------------------------------------------
# Every launcher
# --------------------------------------------------------------------------

def test_launcher_names_are_kept_and_teach_a_game_exists():
    for name in KEPT_NAMES + ["Teach a Game.bat"]:
        assert os.path.exists(os.path.join(BASE, name)), name


def test_every_bat_parses_into_balanced_blocks():
    problems = []
    for name in ALL_BATS:
        problems += _parse_blocks(name)[0]
    assert not problems, "\n".join(problems)


def _stale_reads(name):
    """Reads of %VAR% after a line of the same block set VAR."""
    bad = []
    lines = _lines(name)
    _, top = _parse_blocks(name)
    for _, members in top:
        code = [(j, lines[j]) for j in members if not _is_rem(lines[j])]
        for s, set_line in code:
            for var in re.findall(r'(?i)\bset\s+"?(\w+)=', set_line):
                read = re.compile(r"%" + re.escape(var) + r"[%:]", re.I)
                later = [r for r, ln in code if r > s and read.search(ln)]
                if later:
                    bad.append(f"{name}:{later[0] + 1}: reads %{var}% set at "
                               f"line {s + 1} of the same block")
    return bad


def test_no_block_reads_a_variable_it_sets():
    """%VAR% inside a block is the value from BEFORE the block ran."""
    bad = []
    for name in ALL_BATS:
        bad += _stale_reads(name)
    assert not bad, bad


def test_the_block_lint_catches_the_traps(tmp_path, monkeypatch):
    """The two checks above must actually fire on the mistakes they guard."""
    (tmp_path / "bad.bat").write_text(
        'if defined X (\n    set "A=1"\n    echo %A%\n    echo oops (no caret)\n)\n'
        'echo fine ^(escaped^) "and (quoted)"\n',
        encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "BASE", str(tmp_path))
    problems, top = _parse_blocks("bad.bat")
    assert len(problems) == 1 and "unescaped paren" in problems[0]
    assert top == [(0, [0, 1, 2, 3, 4])]
    assert _stale_reads("bad.bat") == ["bad.bat:3: reads %A% set at line 2 of the same block"]


def test_no_execution_policy_bypass():
    """-Command runs no script file, so the flag did nothing but look alarming."""
    for name in ALL_BATS:
        assert "executionpolicy" not in _read(name).lower(), name


def test_launchers_that_run_python_go_through_env_bat():
    """A launcher that skips _env.bat flashes shut when setup never finished,
    and never installs the components an update added."""
    for name in ALL_BATS:
        if name in OWN_PYTHON_FALLBACK or name == "_env.bat":
            continue
        code = _code_lines(name)
        runs = [i for i, ln in code
                if re.search(r"(?i)(^|[\s\"\\])pythonw?(\.exe)?\"?\s", ln + " ")]
        if not runs:
            continue
        calls = [i for i, ln in code if ln.strip() == 'call "%~dp0_env.bat"']
        assert calls and calls[0] < runs[0], f"{name}: python before _env.bat"
        after = [ln for i, ln in code if i > calls[0]]
        assert after[0].strip() == "if errorlevel 1 exit /b 1", name
        assert ".venv\\Scripts\\python " not in _read(name), name


def test_scripts_that_pause_themselves_are_not_double_paused():
    """diagnose.py and voice_check.py wait for Enter on their own; the .bat
    only pauses when Python could not run the script at all."""
    for name, script in (("Diagnose Capture.bat", "diagnose.py"),
                         ("Check Voice.bat", "voice_check.py")):
        code = [ln.strip() for _, ln in _code_lines(name)]
        assert code[-2] == f"python {script}", name
        assert code[-1] == "if errorlevel 1 pause", name


def test_inline_python_snippets_compile():
    found = 0
    for name in ALL_BATS:
        for m in re.finditer(r'-c "([^"]*)"', _read(name)):
            compile(m.group(1).replace("%%", "%"), f"{name} -c", "exec")
            found += 1
    assert found >= 7


# --------------------------------------------------------------------------
# _env.bat
# --------------------------------------------------------------------------

def test_env_bat_has_no_labels_goto_or_setlocal():
    for i, ln in _code_lines("_env.bat"):
        low = ln.strip().lower()
        assert not low.startswith(":"), f"label at line {i + 1}"
        assert not re.search(r"\bgoto\b|\bcall\s+:", low), f"jump at line {i + 1}"
        assert "setlocal" not in low and "endlocal" not in low, (
            "setlocal would undo the venv activation for the caller")
    assert _code_lines("_env.bat")[-1][1].strip() == "exit /b 0"


def test_env_bat_never_upgrades_or_reinstalls_packages():
    for i, ln in _code_lines("_env.bat"):
        low = ln.lower()
        if "pip" not in low:
            continue
        assert "--force-reinstall" not in low and " -u " not in low + " ", ln
        if "--upgrade" in low:
            assert re.search(r"pip install --upgrade pip\b", low), ln
            assert "-r " not in low and "torch" not in low, ln


def test_cuda_torch_installs_only_when_torch_does_not_import():
    lines = _lines("_env.bat")
    torch_line = [i for i, ln in enumerate(lines) if "--index-url" in ln]
    assert len(torch_line) == 1
    assert "cu128" in lines[torch_line[0]]
    assert _enclosing_opener("_env.bat", torch_line[0]).strip() == \
        "if defined WITNESS_WANTTORCH ("
    setters = [i for i, ln in enumerate(lines)
               if 'set "WITNESS_WANTTORCH=1"' in ln]
    assert len(setters) == 1
    block = _block_text("_env.bat", setters[0])
    assert 'python -c "import torch"' in block
    assert "if errorlevel 1 set \"WITNESS_WANTTORCH=1\"" in block
    # A failed GPU download skips requirements, so easyocr can't pull CPU torch.
    req = [i for i, ln in enumerate(lines) if "pip install -r requirements.txt" in ln]
    assert len(req) == 1
    assert "if not defined WITNESS_TORCHFAIL" in _enclosing_opener("_env.bat", req[0])


def test_system_python_is_only_probed_on_first_install():
    """Once .venv exists, a newer system Python must never block START."""
    lines = _lines("_env.bat")
    fresh = [ln for ln in lines if 'set "WITNESS_FRESH=1"' in ln]
    assert fresh == ['if not exist ".venv\\Scripts\\python.exe" set "WITNESS_FRESH=1"']
    for i, ln in _code_lines("_env.bat"):
        if re.search(r"\bpy -|-m venv|%WITNESS_PY%|-c \"import sys\" |OneDrive|"
                     r"PYTHON_MANAGER", ln):
            gate = ln + _enclosing_opener("_env.bat", i)
            assert "if defined WITNESS_FRESH" in gate, f"line {i + 1}: {ln.strip()}"
    assert "where python" not in _read("_env.bat").lower()


def test_python_probe_prefers_the_py_launcher_and_skips_the_store_stub():
    text = _read("_env.bat")
    assert "for %%V in (3.12 3.11 3.10)" in text
    assert text.index("py -%%V") < text.index('python -c "import sys"')
    # The probe tests the exit code, so the Store alias (exit 9009) is never used.
    assert 'if not errorlevel 1 set "WITNESS_PY=python"' in text
    assert "python.org/downloads/release/python-31210/" in text
    assert "Add python.exe to PATH" in text


def test_onedrive_warning_is_first_install_only():
    lines = _lines("_env.bat")
    idx = [i for i, ln in enumerate(lines) if "\\OneDrive" in ln]
    assert len(idx) == 1
    assert lines[idx[0]].startswith("if defined WITNESS_FRESH if ")
    assert "C:\\Games\\" in _block_text("_env.bat", idx[0])


def test_pause_only_on_failure_or_first_install():
    lines = _lines("_env.bat")
    for i, ln in _code_lines("_env.bat"):
        if not re.search(r"(?i)\bpause\b", ln):
            continue
        opener = _enclosing_opener("_env.bat", i)
        block = _block_text("_env.bat", i)
        ok = ("WITNESS_FRESH" in ln or "if defined WITNESS_FRESH" in opener
              or "exit /b 1" in block)
        assert ok, f"pause on the normal launch path at line {i + 1}"
    assert len(lines) > 50


def test_stamp_is_written_only_after_a_clean_install():
    text = _read("_env.bat")
    lines = _lines("_env.bat")
    assert 'fc /b requirements.txt ".venv\\witness-installed.txt"' in text
    copy = [i for i, ln in enumerate(lines) if ln.strip().startswith("copy ")]
    assert len(copy) == 1
    assert "witness-installed.txt" in lines[copy[0]]
    opener = _enclosing_opener("_env.bat", copy[0])
    assert "if not defined WITNESS_PARTIAL" in opener
    # The import check that blocks the launch comes first.
    imp = text.index("if defined WITNESS_IMPORTFAIL (")
    assert imp < text.index('copy /y requirements.txt')
    assert text.index('set "WITNESS_PARTIAL=1"') < text.index('copy /y requirements.txt')


def test_failed_pip_with_working_imports_still_launches():
    """Offline launch of an installed WITNESS must open: only the import check
    may stop it, and every pip failure just sets a flag."""
    lines = _lines("_env.bat")
    for i, ln in _code_lines("_env.bat"):
        if "exit /b 1" not in ln:
            continue
        opener = _enclosing_opener("_env.bat", i)
        assert ("WITNESS_FRESH" in opener or "WITNESS_IMPORTFAIL" in opener
                or "activate.bat" in opener), f"line {i + 1} blocks: {opener}"
    for i, ln in enumerate(lines):
        if re.search(r"pip install (torch|-r)", ln):
            assert lines[i + 1].strip().startswith("if errorlevel 1 set "), ln


def test_import_check_matches_requirements():
    """If a package leaves requirements.txt but stays in this list, every new
    install refuses to start."""
    line = [ln for ln in _lines("_env.bat")
            if ln.strip().startswith('python -c "import torch, ')]
    assert len(line) == 1
    mods = re.search(r'-c "import ([^"]+)"', line[0]).group(1)
    mods = [m.strip() for m in mods.split(",")]
    assert "webview" not in mods  # gui.py installs pywebview by itself
    dist = {"torch": "easyocr", "easyocr": "easyocr", "cv2": "opencv-python",
            "mss": "mss", "rapidfuzz": "rapidfuzz", "obsws_python": "obsws-python",
            "yaml": "PyYAML", "PIL": "Pillow"}
    req = _read("requirements.txt").lower()
    for m in mods:
        assert m in dist, f"add {m} to the map in this test"
        assert re.search(r"(?m)^" + re.escape(dist[m].lower()) + r"\b", req), m


def test_python_version_check_accepts_only_64bit_310_to_312():
    snippet = re.search(r'-c "(import sys; raise SystemExit[^"]+)"',
                        _read("_env.bat")).group(1)
    rc = subprocess.run([sys.executable, "-c", snippet]).returncode
    good = (3, 10) <= sys.version_info[:2] <= (3, 12) and sys.maxsize > 2 ** 32
    assert rc == (0 if good else 1)


def test_python_version_line_prints_version_and_bitness():
    snippet = re.search(r'-c "(import sys, platform; [^"]+)"',
                        _read("_env.bat")).group(1)
    out = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                         text=True).stdout.strip()
    assert re.match(r"^\d+\.\d+\.\d+\S*\s+\d+bit$", out), out


# --------------------------------------------------------------------------
# requirements.txt
# --------------------------------------------------------------------------

def test_requirements_add_imageio_ffmpeg_and_never_pin_torch():
    reqs = [ln.split("#")[0].strip() for ln in _lines("requirements.txt")]
    reqs = [r for r in reqs if r]
    assert any(re.match(r"imageio-ffmpeg>=0\.5$", r) for r in reqs)
    for r in reqs:
        assert not re.match(r"(?i)torch(vision)?\b", r), r
        assert not r.startswith("-"), f"no pip options in requirements: {r}"


# --------------------------------------------------------------------------
# Individual launchers
# --------------------------------------------------------------------------

def test_update_bat_forces_the_update_and_works_with_an_old_updater(tmp_path):
    """auto_update: false must not block the manual update, and the .bat must
    not crash on an updater.py that has no force parameter yet."""
    text = _read("Update WITNESS.bat")
    code = re.search(r'python -c "([^"]+)"', text).group(1)
    assert "force" in code
    for src, want in (
            ("def check_and_update(base_dir):\n    return False, 'old ' + base_dir\n",
             "old ."),
            ("def check_and_update(base_dir, force=False):\n"
             "    return True, 'forced' if force else 'not forced'\n", "forced")):
        (tmp_path / "updater.py").write_text(src, encoding="utf-8")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == want
    # Anything the update added gets installed while the window is open.
    code_lines = [ln.strip() for _, ln in _code_lines("Update WITNESS.bat")]
    py = code_lines.index('python -c "' + code + '"')
    assert 'call "%~dp0_env.bat"' in code_lines[py + 1:]
    assert code_lines[-1] == "pause"


def test_update_bat_survives_being_rewritten_while_it_runs():
    """The 1bd0911 Update WITNESS.bat gets overwritten by the update it runs.
    cmd then resumes the NEW file at the OLD byte offset (217 if the old copy
    had LF endings, 223 if CRLF; the old updater writes LF). Both must land on
    a line start so the new flow runs cleanly instead of a half-line."""
    new_lf = _read("Update WITNESS.bat").encode("ascii")
    for off in (217, 223):
        assert new_lf[off - 1:off] == b"\n", off
    assert new_lf[217:223] == b"echo.\n"
    assert new_lf[223:].startswith(b'call "%~dp0_env.bat"\n')


def test_calibrate_bat_describes_the_real_capture_and_passes_args():
    text = _read("1 - Calibrate.bat")
    assert "Virtual Camera" not in text
    assert "capture_source from config.yaml" in text
    assert "python calibrate.py %*" in text


def test_teach_a_game_runs_the_console_wizard():
    text = _read("Teach a Game.bat")
    assert "python main.py --teach" in text
    with open(os.path.join(BASE, "main.py"), encoding="utf-8") as f:
        assert '"--teach"' in f.read()
    assert [ln.strip() for _, ln in _code_lines("Teach a Game.bat")][-1] == "pause"


def test_desktop_shortcut_still_targets_the_window_launcher():
    text = _read("Create Desktop Shortcut.bat")
    assert "START Kill Recorder (Window).bat" in text
    assert "powershell -NoProfile -Command" in text
    assert "witness.ico" in text and "skull" not in text.lower()
