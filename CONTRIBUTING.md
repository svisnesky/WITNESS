# Contributing

Thanks for helping. Bug reports go through the issue form
(github.com/svisnesky/WITNESS/issues/new/choose); security problems go
privately, as described in [SECURITY.md](SECURITY.md).

## Run the tests

No GPU, OBS or Windows needed:

```
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

`requirements-dev.txt` is what the tests need and nothing else (it skips the
multi-GB OCR install). Two tests also want `ffmpeg` on your PATH. CI runs the
suite on Windows and Linux for every push and pull request, and it has to pass
before anything lands on `main`: every install updates itself from `main` on
its next launch.

## Rules that keep installs working

- **Code runs on Python 3.9 through 3.12.** No `match` statements and no
  `X | Y` type unions at runtime.
- **Guard Windows-only calls** (`winsound`, `ctypes.windll`, `pythonw`,
  `CREATE_NO_WINDOW`) so the tests still run on macOS and Linux.
- **A new `config.yaml` key needs a code default that keeps today's
  behaviour.** Updates never overwrite an existing `config.yaml`, so most
  installs will never have the key.
- **No new `http://` or `https://` literal in a `.py` file.** Every host the
  app can reach is listed in SECURITY.md, and `tests/test_security_claims.py`
  fails on one that isn't. Print a link as a bare host path, like
  `github.com/svisnesky/WITNESS/issues`. A new network call, even one made
  inside a library, means a new row in SECURITY.md.
- **Keep `.bat` files CRLF** (`.gitattributes` does this) and keep their
  names: the updater never deletes files, so a renamed `.bat` leaves the old
  copy behind on every install.

## Share a game profile

1. Run `Teach a Game.bat` (or `python main.py --teach`) and finish it,
   including the verify step at the end.
2. Play a session with the profile and check that kills are caught.
3. Open a pull request with only `games/<game>.yaml`, and say which
   resolution you play at. A profile holds detection, theme and feature-gate
   keys only: no paths, no `obs:` block, no upload settings.

## Never commit secrets

Don't commit your own `config.yaml` (it holds your OBS password),
`settings_override.yaml`, `client_secret.json`, `youtube_token.json`,
`elevenlabs_key.txt` or any `*.key` file. The credential files are gitignored,
and a test fails if one is ever tracked. Check `git diff --cached` before you
commit anyway.
