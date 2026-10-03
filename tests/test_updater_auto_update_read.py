"""updater._auto_update_enabled reads auto_update the way main.load_config
does, so an opt-out the app honours is never ignored by the updater.

The bug: config.yaml saved as ANSI (Notepad, a typed accented name) failed the
updater's strict UTF-8 read, and any exception counted as "updates on". The
launch check, the splash check and the dashboard's Check for update then
pulled code from GitHub onto a PC whose owner had turned updates off."""

import locale

import updater


def _write(tmp_path, name, data: bytes):
    (tmp_path / name).write_bytes(data)


def test_an_ansi_saved_config_still_turns_updates_off(tmp_path, monkeypatch):
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a: "cp1252")
    _write(tmp_path, "config.yaml",
           'announcer_player_name: "Jos\xe9"\nauto_update: false\n'.encode("cp1252"))
    assert updater._auto_update_enabled(str(tmp_path)) is False


def test_the_locale_fallback_never_raises(tmp_path, monkeypatch):
    # A locale that cannot decode the bytes either: replaced, not an exception.
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a: "ascii")
    _write(tmp_path, "config.yaml", b'name: "\xff\xfe"\nauto_update: false\n')
    assert updater._auto_update_enabled(str(tmp_path)) is False


def test_a_bom_config_turns_updates_off(tmp_path):
    _write(tmp_path, "config.yaml", b"\xef\xbb\xbfauto_update: false\n")
    assert updater._auto_update_enabled(str(tmp_path)) is False


def test_a_missing_config_or_key_means_on(tmp_path):
    assert updater._auto_update_enabled(str(tmp_path)) is True
    _write(tmp_path, "config.yaml", b"gamertag: \"\"\n")
    assert updater._auto_update_enabled(str(tmp_path)) is True
    _write(tmp_path, "config.yaml", b"")
    assert updater._auto_update_enabled(str(tmp_path)) is True


def test_settings_override_wins_like_load_config(tmp_path):
    _write(tmp_path, "config.yaml", b"auto_update: true\n")
    _write(tmp_path, "settings_override.yaml", b"auto_update: false\n")
    assert updater._auto_update_enabled(str(tmp_path)) is False
    _write(tmp_path, "config.yaml", b"auto_update: false\n")
    _write(tmp_path, "settings_override.yaml", b"auto_update: true\n")
    assert updater._auto_update_enabled(str(tmp_path)) is True
    # An override without the key leaves config.yaml's value.
    _write(tmp_path, "settings_override.yaml", b"announcer_volume: 0.5\n")
    assert updater._auto_update_enabled(str(tmp_path)) is False


def test_a_config_that_will_not_parse_keeps_its_false(tmp_path):
    _write(tmp_path, "config.yaml", b"auto_update: false\nbroken: [unclosed\n")
    assert updater._auto_update_enabled(str(tmp_path)) is False
    _write(tmp_path, "config.yaml", b"auto_update: true\nbroken: [unclosed\n")
    assert updater._auto_update_enabled(str(tmp_path)) is True


def test_quoted_and_yaml_spellings_of_off(tmp_path):
    for text in (b'auto_update: "false"\n', b"auto_update: no\n", b"auto_update: off\n",
                 b"auto_update:\n"):
        _write(tmp_path, "config.yaml", text)
        assert updater._auto_update_enabled(str(tmp_path)) is False, text


def test_check_and_update_honours_an_ansi_opt_out(tmp_path, monkeypatch):
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a: "cp1252")
    _write(tmp_path, "config.yaml", 'n: "Jos\xe9"\nauto_update: false\n'.encode("cp1252"))

    def no_network(*a, **k):
        raise AssertionError("must not contact GitHub")
    monkeypatch.setattr(updater, "_get", no_network)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is False and msg == updater.AUTO_UPDATE_OFF_MSG
