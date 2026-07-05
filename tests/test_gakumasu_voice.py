from pathlib import Path

from gakumasu_voice import (
    AndroidClient,
    collect_character_voice_lines,
    parse_stream_count,
    parse_stream_name,
    resolve_bank_path,
    split_remote_paths,
)


def test_collects_krnh_voice_even_when_display_name_is_not_rinha():
    script = "\n".join(
        [
            "[message text=正体を隠した台詞 name=？？？ clip=\\{\"_startTime\":1.0,\"_duration\":2.0\\}]",
            "[actorlayoutgroup clip=\\{\"_startTime\":1.1,\"_duration\":0.0\\}]",
            "[voice voice=sud_vo_adv_demo_001_krnh-001 channel=1 clip=\\{\"_startTime\":1.2,\"_duration\":1.7\\}]",
        ]
    )

    lines = collect_character_voice_lines(script, "script.bin", "krnh")

    assert len(lines) == 1
    assert lines[0].text == "正体を隠した台詞"
    assert lines[0].speaker == "？？？"
    assert lines[0].voice_cue == "sud_vo_adv_demo_001_krnh-001"
    assert lines[0].bank_name == "sud_vo_adv_demo_001"


def test_ignores_name_rinha_when_voice_is_someone_else():
    script = "\n".join(
        [
            "[message text=表示名だけ燐羽 name=燐羽 clip=\\{\"_startTime\":10.0,\"_duration\":2.0\\}]",
            "[voice voice=sud_vo_adv_demo_002_hski-001 actorId=hski clip=\\{\"_startTime\":10.1,\"_duration\":1.5\\}]",
        ]
    )

    lines = collect_character_voice_lines(script, "script.bin", "krnh")

    assert lines == []


def test_matches_voice_to_message_by_clip_time_not_adjacent_line_only():
    script = "\n".join(
        [
            "[message text=時間で対応する台詞 name=燐羽 clip=\\{\"_startTime\":20.0,\"_duration\":4.0\\}]",
            "[fade layer=Content clip=\\{\"_startTime\":20.0,\"_duration\":0.5\\}]",
            "[camera clip=\\{\"_startTime\":20.2,\"_duration\":0.0\\}]",
            "[voice voice=sud_vo_adv_demo_003_krnh-007 channel=1 clip=\\{\"_startTime\":20.3,\"_duration\":3.0\\}]",
        ]
    )

    lines = collect_character_voice_lines(script, "script.bin", "krnh")

    assert len(lines) == 1
    assert lines[0].line_no == 1
    assert lines[0].voice_line_no == 4
    assert lines[0].text == "時間で対応する台詞"


def test_parse_vgmstream_metadata_count_and_stream_name():
    metadata = "\n".join(
        [
            "metadata for bank.acb",
            "stream count: 37",
            "stream index: 13",
            "stream name: sud_vo_adv_dear_hski_017_krnh-001",
        ]
    )

    assert parse_stream_count(metadata) == 37
    assert parse_stream_name(metadata) == "sud_vo_adv_dear_hski_017_krnh-001"


def test_split_remote_paths_accepts_commas_and_semicolons():
    assert split_remote_paths("/octo/3,/octo/4; /octo/9 ") == ["/octo/3", "/octo/4", "/octo/9"]


def test_grep_text_files_searches_each_root_with_size_limit():
    class FakeClient:
        grep_text_files = AndroidClient.grep_text_files

        def __init__(self):
            self.calls = []

        def root_shell(self, *args, check=True):
            self.calls.append((args, check))
            return "/octo/3/a\nignored\n/octo/3/a\n/octo/4/b\n"

    client = FakeClient()

    paths = client.grep_text_files("krnh-", ["/octo/3", "/octo/4"], 500)

    assert paths == ["/octo/3/a", "/octo/4/b"]
    assert client.calls == [
        (
            (
                "find",
                "/octo/3",
                "-type",
                "f",
                "-size",
                "-500k",
                "-exec",
                "grep",
                "-a",
                "-l",
                "krnh-",
                "{}",
                "\\;",
            ),
            False,
        ),
        (
            (
                "find",
                "/octo/4",
                "-type",
                "f",
                "-size",
                "-500k",
                "-exec",
                "grep",
                "-a",
                "-l",
                "krnh-",
                "{}",
                "\\;",
            ),
            False,
        ),
    ]


def test_output_slug_keeps_one_file_per_voice_line(tmp_path: Path):
    script = "\n".join(
        [
            "[message text=あなた、メッキなんてない方が素敵よ。 name=燐羽 clip=\\{\"_startTime\":51.7236738788,\"_duration\":4.3429927879\\}]",
            "[voice voice=sud_vo_adv_dear_hski_017_krnh-001 channel=1 clip=\\{\"_startTime\":51.8236738788,\"_duration\":4.042\\}]",
        ]
    )

    line = collect_character_voice_lines(script, "R9443_4a929a.txt", "krnh")[0]

    assert line.output_slug == "sud_vo_adv_dear_hski_017_krnh-001"


def test_resolve_bank_path_ignores_stale_known_map(monkeypatch, tmp_path: Path):
    class FakeClient:
        def __init__(self):
            self.pulled = []

        def remote_file_exists(self, remote_path: str) -> bool:
            return remote_path == "/fresh/acb"

        def grep_files(self, pattern: str, root: str):
            assert pattern == "sud_vo_adv_demo_004"
            assert root == "/octo"
            return ["/fresh/acb"]

        def pull_private_file(self, remote_path: str, local_path: Path) -> None:
            self.pulled.append(remote_path)
            local_path.write_bytes(b"fake")

    def fake_metadata(vgmstream: str, acb_path: Path, subsong=None) -> str:
        return "stream name: sud_vo_adv_demo_004_krnh-001\n"

    monkeypatch.setattr("gakumasu_voice.vgmstream_metadata", fake_metadata)

    known = {"sud_vo_adv_demo_004": "/stale/acb"}
    remote = resolve_bank_path(FakeClient(), "vgm", "sud_vo_adv_demo_004", "/octo", tmp_path, known)

    assert remote == "/fresh/acb"
    assert known["sud_vo_adv_demo_004"] == "/fresh/acb"
