"""The iOS app shows a refusal instead of a fake success.

webserver answers /clip and /addkill with HTTP 200 {"ok":false,"error":"not
running"} when no session runs (200 because the app's post() treats any non-2xx
as "the PC didn't respond"). WitnessClient.post ignored the body, so the iPad
toasted "Kill added" / "Clip requested" for presses the PC threw away. Swift
cannot run here; the app was built with xcodebuild for the simulator, and these
checks pin the source to the server's contract."""
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "ios", "WITNESS", "WITNESS")


def _swift(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


def test_the_server_still_answers_an_idle_press_with_ok_false():
    with open(os.path.join(BASE, "webserver.py"), encoding="utf-8") as f:
        src = f.read()
    assert b'{"ok":false,"error":"not running"}'.decode() in src


def test_post_decodes_ok_false_into_a_refusal():
    client = _swift("WitnessClient.swift")
    post = client[client.index("func post("):client.index("func info(")]
    assert "let (data, resp)" in post
    assert "PostReply.self, from: data" in post
    assert re.search(r"reply\.ok == false\s*\{\s*throw ClientError\.refused", post)


def test_perform_shows_the_refusal_not_the_success_toast():
    model = _swift("AppModel.swift")
    perform = model[model.index("func perform("):model.index("private func show(")]
    refused = perform.index("catch WitnessClient.ClientError.refused")
    assert refused > perform.index("show(action.done)")
    assert "press START first" in perform
