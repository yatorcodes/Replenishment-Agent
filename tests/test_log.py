"""Stage 4: audit log timeline CLI."""

from app.agent import run_agent
from app.log import format_timeline, load_timeline, main


def _factory_text_only():
    from tests.test_agent import ScriptedGemini, _factory

    return _factory([("text", "Nothing to order today.")])


def test_load_timeline(db_path):
    result = run_agent(client_factory=_factory_text_only())
    rows = load_timeline(result["run_id"])
    assert rows
    assert rows[0]["step"] == "run_start"
    assert rows[-1]["step"] == "run_complete"


def test_format_timeline_readable(db_path):
    result = run_agent(client_factory=_factory_text_only())
    text = format_timeline(load_timeline(result["run_id"]))
    assert result["run_id"] in text
    assert "run_start" in text
    assert "run_complete" in text


def test_cli_log(db_path, capsys):
    result = run_agent(client_factory=_factory_text_only())
    main([result["run_id"]])
    out = capsys.readouterr().out
    assert "Audit timeline" in out
    assert result["run_id"] in out


def test_unknown_run_id(db_path, capsys):
    with __import__("pytest").raises(SystemExit):
        main(["does-not-exist"])
    out = capsys.readouterr().out
    assert "No audit entries" in out
