from __future__ import annotations

from mindspace_graph.adapters.file_storage import JsonProfileRepository
from mindspace_graph.asr_vocabulary import ASRVocabularyStore


def test_profile_json_is_compiled_without_prompt_or_llm(tmp_path) -> None:
    profiles = JsonProfileRepository(tmp_path / "profiles")
    ai = profiles.load_document("ai_profile")
    ai["identity"]["name"] = "长离"
    ai["continuity"]["long_term_goals"] = ["陪用户完成 Mindspace 项目"]
    profiles.save_document("ai_profile", ai)
    runtime = profiles.load_document("runtime_state")
    runtime["session_state"]["active_entities"] = ["八重神子"]
    profiles.save_document("runtime_state", runtime)

    store = ASRVocabularyStore(tmp_path / "asr" / "vocabulary.json", profiles)
    snapshot = store.snapshot()

    by_term = {item["term"]: item for item in snapshot["entries"]}
    assert by_term["长离"]["priority"] == "high"
    assert by_term["八重神子"]["priority"] == "high"
    assert by_term["陪用户完成 Mindspace 项目"]["priority"] == "low"
    assert snapshot["profile_revisions"]["ai_profile"] == 1
    assert "长离" in snapshot["decoder_hotwords"]


def test_manual_correction_is_atomic_and_immediately_testable(tmp_path) -> None:
    profiles = JsonProfileRepository(tmp_path / "profiles")
    store = ASRVocabularyStore(tmp_path / "asr" / "vocabulary.json", profiles)

    updated = store.replace_manual(
        [
            {
                "term": "长离",
                "aliases": ["长利", "常离"],
                "priority": "critical",
                "enabled": True,
            }
        ]
    )
    result = store.test_text("我想使用长利的声音")

    assert updated["manual_revision"] == 1
    assert result["corrected_text"] == "我想使用长离的声音"
    assert result["matches"][0]["from"] == "长利"
    assert (tmp_path / "asr" / "vocabulary.json").is_file()


def test_record_correction_reuses_existing_target(tmp_path) -> None:
    profiles = JsonProfileRepository(tmp_path / "profiles")
    store = ASRVocabularyStore(tmp_path / "asr" / "vocabulary.json", profiles)

    store.record_correction("长利", "长离")
    snapshot = store.record_correction("常离", "长离")
    manual = [item for item in snapshot["entries"] if item["source"] == "manual"]

    assert len(manual) == 1
    assert set(manual[0]["aliases"]) == {"长利", "常离"}
    assert manual[0]["priority"] == "critical"


def test_asr_observation_is_bounded_metadata_outside_profiles(tmp_path) -> None:
    profiles = JsonProfileRepository(tmp_path / "profiles")
    store = ASRVocabularyStore(tmp_path / "asr" / "vocabulary.json", profiles)

    store.record_observation(
        {
            "raw_text": "长利你好",
            "text": "长离你好",
            "correction_matches": [
                {"from": "长利", "to": "长离", "source": "explicit"}
            ],
            "vocabulary_revision": "v1",
        },
        event="asr.final",
    )

    history = store.correction_history(limit=10)
    assert history[0]["raw_text"] == "长利你好"
    assert history[0]["corrected_text"] == "长离你好"
    assert history[0]["matches"][0]["to"] == "长离"
    assert not (profiles.root / "correction-history.jsonl").exists()
