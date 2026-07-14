from pathlib import Path

from gakumasu_voice import (
    AndroidClient,
    BankCacheStats,
    BankPathCache,
    collect_limited_character_voice_lines,
    collect_character_voice_lines,
    ensure_local_acb,
    find_character_preset,
    paired_item_paths,
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


def test_collects_fktn_voice_for_kotone():
    script = "\n".join(
        [
            "[message text=お金がなくても、夢はあります！ name=ことね clip=\\{\"_startTime\":1.0,\"_duration\":2.0\\}]",
            "[voice voice=sud_vo_adv_demo_005_fktn-001 channel=1 clip=\\{\"_startTime\":1.1,\"_duration\":1.6\\}]",
        ]
    )

    lines = collect_character_voice_lines(script, "script.bin", "fktn")

    assert len(lines) == 1
    assert lines[0].speaker == "ことね"
    assert lines[0].voice_cue == "sud_vo_adv_demo_005_fktn-001"
    assert lines[0].bank_name == "sud_vo_adv_demo_005"


def test_character_preset_lookup_accepts_slug_code_and_full_name():
    assert find_character_preset("kotone").code == "fktn"
    assert find_character_preset("fktn").slug == "kotone"
    assert find_character_preset("藤田ことね").character_name == "ことね"


def test_paired_item_paths_keep_subtitle_and_voice_adjacent(tmp_path: Path):
    script = "\n".join(
        [
            "[message text=sample name=ことね clip=\\{\"_startTime\":1.0,\"_duration\":2.0\\}]",
            "[voice voice=sud_vo_adv_demo_005_fktn-001 channel=1 clip=\\{\"_startTime\":1.1,\"_duration\":1.6\\}]",
        ]
    )
    line = collect_character_voice_lines(script, "script.bin", "fktn")[0]

    subtitle, wav, metadata = paired_item_paths(tmp_path, 12, line)

    assert subtitle.name == "0012_sud_vo_adv_demo_005_fktn-001_01_subtitle.txt"
    assert wav.name == "0012_sud_vo_adv_demo_005_fktn-001_02_voice.wav"
    assert metadata.name == "0012_sud_vo_adv_demo_005_fktn-001_03_metadata.json"
    assert sorted([metadata.name, wav.name, subtitle.name]) == [subtitle.name, wav.name, metadata.name]


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


def test_limited_collection_stops_after_requested_voice_line_count(tmp_path: Path):
    def script_for(cues: list[str]) -> str:
        rows = []
        for index, cue in enumerate(cues, 1):
            start = float(index)
            rows.append(
                f"[message text=sample{index} name=ことね clip=\\{{\"_startTime\":{start},\"_duration\":1.0\\}}]"
            )
            rows.append(
                f"[voice voice={cue} channel=1 clip=\\{{\"_startTime\":{start + 0.1},\"_duration\":0.8\\}}]"
            )
        return "\n".join(rows)

    class FakeClient:
        def __init__(self):
            self.scripts = {
                "/octo/3/a": script_for(["sud_vo_adv_demo_010_fktn-001"]),
                "/octo/3/b": script_for(["sud_vo_adv_demo_011_fktn-001", "sud_vo_adv_demo_011_fktn-002"]),
                "/octo/3/c": script_for(["sud_vo_adv_demo_012_fktn-001"]),
            }
            self.contains_checked = []

        def iter_text_files(self, roots, max_kb):
            assert list(roots) == ["/octo/3"]
            assert max_kb == 500
            for path in self.scripts:
                yield path

        def text_file_contains(self, remote_path: str, pattern: str) -> bool:
            self.contains_checked.append(remote_path)
            assert pattern == "fktn-"
            return pattern in self.scripts[remote_path]

        def cat_text(self, remote_path: str) -> str:
            return self.scripts[remote_path]

    client = FakeClient()

    script_texts, lines, script_paths, scanned_count, stopped_early = collect_limited_character_voice_lines(
        client=client,
        script_roots=["/octo/3"],
        script_max_kb=500,
        character_code="fktn",
        line_limit=2,
        scripts_dir=tmp_path,
        progress=False,
    )

    assert len(script_texts) == 2
    assert [line.voice_cue for line in lines] == [
        "sud_vo_adv_demo_010_fktn-001",
        "sud_vo_adv_demo_011_fktn-001",
    ]
    assert script_paths == ["/octo/3/a", "/octo/3/b"]
    assert scanned_count == 2
    assert stopped_early is True
    assert client.contains_checked == ["/octo/3/a", "/octo/3/b"]


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


def test_resolve_bank_path_uses_valid_bank_cache_without_grep(tmp_path: Path):
    class FakeClient:
        def remote_file_exists(self, remote_path: str) -> bool:
            return remote_path == "/cached/acb"

        def grep_files(self, pattern: str, root: str):
            raise AssertionError("cache hit should not grep the octo root")

    cache = BankPathCache(tmp_path / "bank_cache.json")
    cache.update_remote_path("sud_vo_adv_demo_020", "/cached/acb", "/octo", "grep")
    stats = BankCacheStats()

    remote = resolve_bank_path(
        FakeClient(),
        "vgm",
        "sud_vo_adv_demo_020",
        "/octo",
        tmp_path,
        {},
        bank_cache=cache,
        stats=stats,
    )

    assert remote == "/cached/acb"
    assert stats.remote_cache_hits == 1
    assert stats.grep_lookups == 0


def test_resolve_bank_path_invalidates_stale_bank_cache_and_updates_from_grep(monkeypatch, tmp_path: Path):
    class FakeClient:
        def __init__(self):
            self.checked = []

        def remote_file_exists(self, remote_path: str) -> bool:
            self.checked.append(remote_path)
            return remote_path == "/fresh/acb"

        def grep_files(self, pattern: str, root: str):
            assert pattern == "sud_vo_adv_demo_021"
            assert root == "/octo"
            return ["/fresh/acb"]

        def pull_private_file(self, remote_path: str, local_path: Path) -> None:
            local_path.write_bytes(b"fake")

    def fake_metadata(vgmstream: str, acb_path: Path, subsong=None) -> str:
        return "stream name: sud_vo_adv_demo_021_fktn-001\n"

    monkeypatch.setattr("gakumasu_voice.vgmstream_metadata", fake_metadata)
    cache = BankPathCache(tmp_path / "bank_cache.json")
    cache.update_remote_path("sud_vo_adv_demo_021", "/stale/acb", "/octo", "grep")
    stats = BankCacheStats()
    client = FakeClient()

    remote = resolve_bank_path(
        client,
        "vgm",
        "sud_vo_adv_demo_021",
        "/octo",
        tmp_path,
        {},
        bank_cache=cache,
        stats=stats,
    )

    assert remote == "/fresh/acb"
    assert client.checked == ["/stale/acb"]
    assert cache.get_remote_path("sud_vo_adv_demo_021", "/octo") == "/fresh/acb"
    assert stats.stale_remote_entries == 1
    assert stats.grep_lookups == 1
    assert stats.updated_remote_entries == 1


def test_ensure_local_acb_uses_shared_cache_without_pull(tmp_path: Path):
    class FakeClient:
        def pull_private_file(self, remote_path: str, local_path: Path) -> None:
            raise AssertionError("shared ACB cache hit should not pull")

    shared_dir = tmp_path / "shared"
    run_dir = tmp_path / "run"
    shared_dir.mkdir()
    (shared_dir / "sud_vo_adv_demo_022.acb").write_bytes(b"cached acb")
    stats = BankCacheStats()

    local_acb = ensure_local_acb(
        client=FakeClient(),
        remote_acb="/remote/acb",
        bank_name="sud_vo_adv_demo_022",
        run_acb_dir=run_dir,
        shared_acb_dir=shared_dir,
        stats=stats,
    )

    assert local_acb == run_dir / "sud_vo_adv_demo_022.acb"
    assert local_acb.read_bytes() == b"cached acb"
    assert stats.local_acb_hits == 1
    assert stats.local_acb_copies == 1
    assert stats.local_acb_pulls == 0


def test_ensure_local_acb_repulls_when_shared_cache_remote_path_changed(tmp_path: Path):
    class FakeClient:
        def __init__(self):
            self.pulled = []

        def pull_private_file(self, remote_path: str, local_path: Path) -> None:
            self.pulled.append(remote_path)
            local_path.write_bytes(b"fresh acb")

    shared_dir = tmp_path / "shared"
    run_dir = tmp_path / "run"
    shared_dir.mkdir()
    shared_acb = shared_dir / "sud_vo_adv_demo_023.acb"
    shared_acb.write_bytes(b"old acb")
    (shared_dir / "sud_vo_adv_demo_023.acb.json").write_text('{"remote_acb":"/old/acb"}', encoding="utf-8")
    client = FakeClient()
    stats = BankCacheStats()

    local_acb = ensure_local_acb(
        client=client,
        remote_acb="/new/acb",
        bank_name="sud_vo_adv_demo_023",
        run_acb_dir=run_dir,
        shared_acb_dir=shared_dir,
        stats=stats,
    )

    assert client.pulled == ["/new/acb"]
    assert local_acb.read_bytes() == b"fresh acb"
    assert shared_acb.read_bytes() == b"fresh acb"
    assert stats.local_acb_pulls == 1
    assert stats.local_acb_copies == 1
