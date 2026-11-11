import pytest

from spotdl.utils.arguments import parse_arguments


def test_parse_arguments():
    with pytest.raises(SystemExit):
        vars(parse_arguments())


def test_parse_use_official_api(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "spotdl",
            "download",
            "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b",
            "--use-official-api",
        ],
    )

    arguments = parse_arguments()

    assert arguments.use_official_api is True


def test_parse_easy_without_query(monkeypatch):
    monkeypatch.setattr("sys.argv", ["spotdl", "easy"])

    arguments = parse_arguments()

    assert arguments.operation == "easy"


def test_help_mentions_resume(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["spotdl", "-h"])

    with pytest.raises(SystemExit):
        parse_arguments()

    assert "resume" in capsys.readouterr().out.lower()
