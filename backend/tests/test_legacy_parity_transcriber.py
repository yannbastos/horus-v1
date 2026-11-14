"""Paridade com o desktop C# legado (HOR-010): comportamento já existente no backend, antes sem teste.

Origem: tests/Horus.Tests/YouTubeDownloadTests.cs. Sem rede, sem yt-dlp real, sem Whisper.
"""
import sys
from types import SimpleNamespace

import pytest

from horus_backend import transcriber
from horus_backend.transcriber import FILE_MARKER, LANGUAGES, MODELS, is_youtube_url


@pytest.mark.parametrize("url,valid", [
    ("https://www.youtube.com/watch?v=BaW_jenozKc", True),
    ("https://youtu.be/BaW_jenozKc", True),
    ("https://m.youtube.com/shorts/BaW_jenozKc", True),
    ("https://music.youtube.com/watch?v=BaW_jenozKc", True),
    ("https://www.youtube.com/live/BaW_jenozKc", True),
    ("https://www.youtube.com/embed/BaW_jenozKc", True),
    ("https://youtube.com.evil.org/watch?v=BaW_jenozKc", False),
    ("https://notyoutube.com/watch?v=BaW_jenozKc", False),
    ("https://youtu.be.evil.org/BaW_jenozKc", False),
    ("file:///C:/test", False),
    ("--exec calc", False),
    ("", False),
    ("https://youtube.com/playlist?list=test", False),
    ("https://www.youtube.com/watch?list=PL123", False),
    ("https://youtu.be/curto", False),
    ("https://www.youtube.com/shorts/curto", False),
    ("https://www.youtube.com:8443/watch?v=BaW_jenozKc", False),
    ("https://user:pass@youtu.be/BaW_jenozKc", False),
])
def test_only_a_single_https_youtube_video_is_accepted(url, valid):
    assert is_youtube_url(url) is valid


def test_download_arguments_are_safe_and_report_the_final_path(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(transcriber, "find_tool", lambda name: None)
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(main=lambda args: captured.append(args)))
    url = "https://youtu.be/BaW_jenozKc"

    assert transcriber.download(url, tmp_path / "novo") == 0

    args = captured[0]
    assert (tmp_path / "novo").is_dir()
    for flag in ("--ignore-config", "--no-playlist", "--no-overwrites", "--windows-filenames"):
        assert flag in args
    assert args[args.index("--merge-output-format") + 1] == "mp4"
    assert args[args.index("--print") + 1] == f"after_move:{FILE_MARKER}%(filepath)s"
    assert args[args.index("-P") + 1] == str(tmp_path / "novo")
    assert args[-2:] == ["--", url]


def test_invalid_url_never_reaches_the_downloader(monkeypatch, tmp_path):
    called = []
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(main=lambda args: called.append(args)))
    for bad in ("--exec calc", "file:///C:/test", "https://youtube.com.evil.org/watch?v=BaW_jenozKc"):
        with pytest.raises(ValueError):
            transcriber.download(bad, tmp_path)
    assert called == []


def test_model_and_language_options_match_the_legacy_desktop():
    assert MODELS == ("tiny", "base", "small", "medium", "large-v3-turbo")
    assert LANGUAGES == ("auto", "pt", "en", "es")
