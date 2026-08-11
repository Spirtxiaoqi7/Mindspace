"""Frozen HTTP operation contract for the API route split."""

from __future__ import annotations

import warnings

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from mindspace_graph.api import create_app
from mindspace_graph.settings import AppSettings

EXPECTED_OPERATIONS = [('/api/v1/activities', 'get', 'list_activities_api_v1_activities_get'),
 ('/api/v1/activities/{activity_id}/sessions', 'post', 'start_activity_api_v1_activities__activity_id__sessions_post'),
 ('/api/v1/activity-sessions/{activity_session_id}',
  'get',
  'get_activity_session_api_v1_activity_sessions__activity_session_id__get'),
 ('/api/v1/activity-sessions/{activity_session_id}/actions',
  'post',
  'apply_activity_action_api_v1_activity_sessions__activity_session_id__actions_post'),
 ('/api/v1/art/catalog', 'get', 'art_catalog_api_v1_art_catalog_get'),
 ('/api/v1/art/packs', 'get', 'art_packs_api_v1_art_packs_get'),
 ('/api/v1/art/packs/{pack_id}/install', 'post', 'install_art_pack_api_v1_art_packs__pack_id__install_post'),
 ('/api/v1/art/packs/{pack_id}/pause', 'post', 'pause_art_pack_api_v1_art_packs__pack_id__pause_post'),
 ('/api/v1/art/packs/{pack_id}/resume', 'post', 'resume_art_pack_api_v1_art_packs__pack_id__resume_post'),
 ('/api/v1/audio/asr', 'post', 'transcribe_api_v1_audio_asr_post'),
 ('/api/v1/audio/asr/corrections', 'get', 'get_asr_correction_history_api_v1_audio_asr_corrections_get'),
 ('/api/v1/audio/asr/corrections', 'post', 'add_asr_correction_api_v1_audio_asr_corrections_post'),
 ('/api/v1/audio/asr/vocabulary', 'get', 'get_asr_vocabulary_api_v1_audio_asr_vocabulary_get'),
 ('/api/v1/audio/asr/vocabulary', 'put', 'put_asr_vocabulary_api_v1_audio_asr_vocabulary_put'),
 ('/api/v1/audio/asr/vocabulary/test', 'post', 'test_asr_vocabulary_api_v1_audio_asr_vocabulary_test_post'),
 ('/api/v1/audio/diagnostics', 'get', 'audio_diagnostics_api_v1_audio_diagnostics_get'),
 ('/api/v1/audio/status', 'get', 'audio_status_api_v1_audio_status_get'),
 ('/api/v1/audio/tts', 'post', 'synthesize_api_v1_audio_tts_post'),
 ('/api/v1/audio/tts/qwen3/voices', 'get', 'list_qwen3_tts_voices_api_v1_audio_tts_qwen3_voices_get'),
 ('/api/v1/audio/tts/reference', 'delete', 'clear_tts_reference_api_v1_audio_tts_reference_delete'),
 ('/api/v1/audio/tts/reference', 'post', 'upload_tts_reference_api_v1_audio_tts_reference_post'),
 ('/api/v1/audio/tts/reference/transcribe',
  'post',
  'transcribe_tts_reference_api_v1_audio_tts_reference_transcribe_post'),
 ('/api/v1/audio/tts/stream', 'post', 'stream_synthesize_api_v1_audio_tts_stream_post'),
 ('/api/v1/audio/tts/voice/select', 'post', 'select_tts_voice_api_v1_audio_tts_voice_select_post'),
 ('/api/v1/audio/tts/voices', 'get', 'list_tts_voices_api_v1_audio_tts_voices_get'),
 ('/api/v1/avatar/config', 'get', 'avatar_config_api_v1_avatar_config_get'),
 ('/api/v1/avatar/config', 'put', 'put_avatar_config_api_v1_avatar_config_put'),
 ('/api/v1/avatar/upload/{role}', 'post', 'upload_avatar_api_v1_avatar_upload__role__post'),
 ('/api/v1/character-drafts', 'delete', 'legacy_character_drafts_collection_delete'),
 ('/api/v1/character-drafts', 'get', 'legacy_character_drafts_collection_get'),
 ('/api/v1/character-drafts', 'patch', 'legacy_character_drafts_collection_patch'),
 ('/api/v1/character-drafts', 'post', 'legacy_character_drafts_collection_post'),
 ('/api/v1/character-drafts', 'put', 'legacy_character_drafts_collection_put'),
 ('/api/v1/character-drafts/{legacy_path}',
  'delete',
  'legacy_character_drafts_path_delete'),
 ('/api/v1/character-drafts/{legacy_path}',
  'get',
  'legacy_character_drafts_path_get'),
 ('/api/v1/character-drafts/{legacy_path}',
  'patch',
  'legacy_character_drafts_path_patch'),
 ('/api/v1/character-drafts/{legacy_path}',
  'post',
  'legacy_character_drafts_path_post'),
 ('/api/v1/character-drafts/{legacy_path}',
  'put',
  'legacy_character_drafts_path_put'),
 ('/api/v1/characters', 'get', 'list_characters_api_v1_characters_get'),
 ('/api/v1/characters', 'post', 'create_character_api_v1_characters_post'),
 ('/api/v1/characters/fate-options', 'get', 'legacy_fate_options_get'),
 ('/api/v1/characters/fate-options', 'post', 'legacy_fate_options_post'),
 ('/api/v1/characters/import', 'post', 'import_character_api_v1_characters_import_post'),
 ('/api/v1/characters/options', 'get', 'legacy_character_options_api_v1_characters_options_get'),
 ('/api/v1/characters/{character_id}', 'get', 'get_character_api_v1_characters__character_id__get'),
 ('/api/v1/characters/{character_id}', 'put', 'update_character_api_v1_characters__character_id__put'),
 ('/api/v1/characters/{character_id}/activity-sessions',
  'get',
  'list_activity_sessions_api_v1_characters__character_id__activity_sessions_get'),
 ('/api/v1/characters/{character_id}/archive',
  'post',
  'archive_character_api_v1_characters__character_id__archive_post'),
 ('/api/v1/characters/{character_id}/card', 'get', 'get_character_card_api_v1_characters__character_id__card_get'),
 ('/api/v1/characters/{character_id}/chapters/summary',
  'get',
  'shared_chapter_summary_api_v1_characters__character_id__chapters_summary_get'),
 ('/api/v1/characters/{character_id}/clone', 'post', 'clone_character_api_v1_characters__character_id__clone_post'),
 ('/api/v1/characters/{character_id}/export', 'get', 'export_character_api_v1_characters__character_id__export_get'),
 ('/api/v1/characters/{character_id}/history', 'get', 'character_history_api_v1_characters__character_id__history_get'),
 ('/api/v1/characters/{character_id}/journal', 'get', 'list_journal_api_v1_characters__character_id__journal_get'),
 ('/api/v1/characters/{character_id}/journal', 'post', 'create_journal_api_v1_characters__character_id__journal_post'),
 ('/api/v1/characters/{character_id}/journal/generate',
  'post',
  'generate_journal_api_v1_characters__character_id__journal_generate_post'),
 ('/api/v1/characters/{character_id}/journal/{entry_id}',
  'delete',
  'delete_journal_api_v1_characters__character_id__journal__entry_id__delete'),
 ('/api/v1/characters/{character_id}/journal/{entry_id}',
  'put',
  'update_journal_api_v1_characters__character_id__journal__entry_id__put'),
 ('/api/v1/characters/{character_id}/moments', 'get', 'list_moments_api_v1_characters__character_id__moments_get'),
 ('/api/v1/characters/{character_id}/moments', 'post', 'create_moment_api_v1_characters__character_id__moments_post'),
 ('/api/v1/characters/{character_id}/moments/{moment_id}',
  'put',
  'update_moment_api_v1_characters__character_id__moments__moment_id__put'),
 ('/api/v1/characters/{character_id}/restore',
  'post',
  'restore_character_api_v1_characters__character_id__restore_post'),
 ('/api/v1/chat', 'post', 'chat_api_v1_chat_post'),
 ('/api/v1/chat/chunks', 'get', 'list_chat_chunks_api_v1_chat_chunks_get'),
 ('/api/v1/chat/stream', 'post', 'chat_stream_api_v1_chat_stream_post'),
 ('/api/v1/config', 'get', 'public_config_api_v1_config_get'),
 ('/api/v1/data/clear', 'post', 'clear_data_api_v1_data_clear_post'),
 ('/api/v1/destiny/avatars', 'post', 'upload_destiny_avatar_api_v1_destiny_avatars_post'),
 ('/api/v1/destiny/avatars/{filename}', 'delete', 'discard_destiny_avatar_api_v1_destiny_avatars__filename__delete'),
 ('/api/v1/destiny/definition', 'get', 'destiny_definition_api_v1_destiny_definition_get'),
 ('/api/v1/destiny/journeys', 'post', 'create_destiny_journey_api_v1_destiny_journeys_post'),
 ('/api/v1/destiny/journeys/{journey_id}', 'get', 'get_destiny_journey_api_v1_destiny_journeys__journey_id__get'),
 ('/api/v1/destiny/journeys/{journey_id}/archetypes',
  'post',
  'generate_destiny_archetypes_api_v1_destiny_journeys__journey_id__archetypes_post'),
 ('/api/v1/destiny/journeys/{journey_id}/cards',
  'post',
  'generate_destiny_cards_api_v1_destiny_journeys__journey_id__cards_post'),
 ('/api/v1/destiny/journeys/{journey_id}/cards/{archetype_id}',
  'post',
  'legacy_generate_destiny_cards_api_v1_destiny_journeys__journey_id__cards__archetype_id__post'),
 ('/api/v1/destiny/journeys/{journey_id}/commit',
  'post',
  'commit_destiny_journey_api_v1_destiny_journeys__journey_id__commit_post'),
 ('/api/v1/destiny/journeys/{journey_id}/rewind/archetypes',
  'post',
  'rewind_destiny_archetypes_api_v1_destiny_journeys__journey_id__rewind_archetypes_post'),
 ('/api/v1/destiny/journeys/{journey_id}/selections',
  'delete',
  'clear_destiny_selections_api_v1_destiny_journeys__journey_id__selections_delete'),
 ('/api/v1/destiny/journeys/{journey_id}/selections/{slot_id}',
  'delete',
  'unselect_destiny_card_api_v1_destiny_journeys__journey_id__selections__slot_id__delete'),
 ('/api/v1/destiny/journeys/{journey_id}/selections/{slot_id}',
  'put',
  'select_destiny_card_api_v1_destiny_journeys__journey_id__selections__slot_id__put'),
 ('/api/v1/destiny/journeys/{journey_id}/synthesize',
  'post',
  'synthesize_destiny_journey_api_v1_destiny_journeys__journey_id__synthesize_post'),
 ('/api/v1/diagnostics', 'get', 'diagnostics_api_v1_diagnostics_get'),
 ('/api/v1/health', 'get', 'health_api_v1_health_get'),
 ('/api/v1/interrupt', 'post', 'interrupt_api_v1_interrupt_post'),
 ('/api/v1/knowledge', 'get', 'list_knowledge_api_v1_knowledge_get'),
 ('/api/v1/knowledge', 'post', 'add_knowledge_api_v1_knowledge_post'),
 ('/api/v1/knowledge/stats', 'get', 'knowledge_stats_api_v1_knowledge_stats_get'),
 ('/api/v1/knowledge/upload', 'post', 'upload_knowledge_api_v1_knowledge_upload_post'),
 ('/api/v1/knowledge/{chunk_id}', 'delete', 'delete_knowledge_api_v1_knowledge__chunk_id__delete'),
 ('/api/v1/memory/entities', 'get', 'list_entities_api_v1_memory_entities_get'),
 ('/api/v1/memory/entities', 'post', 'create_entity_api_v1_memory_entities_post'),
 ('/api/v1/memory/entities/merge', 'post', 'merge_entities_api_v1_memory_entities_merge_post'),
 ('/api/v1/memory/entities/{entity_id}/aliases',
  'post',
  'add_entity_alias_api_v1_memory_entities__entity_id__aliases_post'),
 ('/api/v1/memory/events', 'get', 'event_memories_api_v1_memory_events_get'),
 ('/api/v1/memory/events', 'post', 'create_event_memory_api_v1_memory_events_post'),
 ('/api/v1/memory/events/{event_id}',
  'delete',
  'delete_event_memory_api_v1_memory_events__event_id__delete'),
 ('/api/v1/memory/events/{event_id}',
  'put',
  'update_event_memory_api_v1_memory_events__event_id__put'),
 ('/api/v1/memory/events/{event_id}/complete',
  'post',
  'complete_event_memory_api_v1_memory_events__event_id__complete_post'),
 ('/api/v1/memory/items', 'get', 'memory_items_api_v1_memory_items_get'),
 ('/api/v1/memory/items/{memory_key}', 'delete', 'delete_memory_item_api_v1_memory_items__memory_key__delete'),
 ('/api/v1/memory/items/{memory_key}', 'put', 'update_memory_item_api_v1_memory_items__memory_key__put'),
 ('/api/v1/memory/rebuild', 'post', 'rebuild_memory_api_v1_memory_rebuild_post'),
 ('/api/v1/memory/registry', 'get', 'memory_registry_api_v1_memory_registry_get'),
 ('/api/v1/memory/restore', 'post', 'restore_memory_item_api_v1_memory_restore_post'),
 ('/api/v1/memory/structured', 'get', 'structured_memory_api_v1_memory_structured_get'),
 ('/api/v1/models/available', 'get', 'get_available_models_api_v1_models_available_get'),
 ('/api/v1/profiles/{name}', 'get', 'get_profile_api_v1_profiles__name__get'),
 ('/api/v1/profiles/{name}', 'put', 'put_profile_api_v1_profiles__name__put'),
 ('/api/v1/profiles/{name}/card', 'get', 'profile_card_api_v1_profiles__name__card_get'),
 ('/api/v1/profiles/{name}/history', 'get', 'profile_history_api_v1_profiles__name__history_get'),
 ('/api/v1/profiles/{name}/restore', 'post', 'restore_profile_api_v1_profiles__name__restore_post'),
 ('/api/v1/runs/{run_id}', 'get', 'get_run_status_api_v1_runs__run_id__get'),
 ('/api/v1/runs/{run_id}/cancel', 'post', 'cancel_run_api_v1_runs__run_id__cancel_post'),
 ('/api/v1/runs/{run_id}/prompt-inspection', 'get', 'prompt_inspection_api_v1_runs__run_id__prompt_inspection_get'),
 ('/api/v1/runs/{run_id}/stream', 'get', 'resume_chat_stream_api_v1_runs__run_id__stream_get'),
 ('/api/v1/scenes', 'get', 'list_scenes_api_v1_scenes_get'),
 ('/api/v1/scenes/custom', 'post', 'upload_custom_scene_api_v1_scenes_custom_post'),
 ('/api/v1/sessions', 'get', 'list_sessions_api_v1_sessions_get'),
 ('/api/v1/sessions', 'post', 'create_session_api_v1_sessions_post'),
 ('/api/v1/sessions/{session_id}', 'delete', 'delete_session_api_v1_sessions__session_id__delete'),
 ('/api/v1/sessions/{session_id}', 'get', 'get_session_api_v1_sessions__session_id__get'),
 ('/api/v1/sessions/{session_id}/clear', 'post', 'clear_session_api_v1_sessions__session_id__clear_post'),
 ('/api/v1/sessions/{session_id}/context-diagnostics',
  'get',
  'context_diagnostics_api_v1_sessions__session_id__context_diagnostics_get'),
 ('/api/v1/sessions/{session_id}/messages/{message_id}',
  'delete',
  'delete_message_api_v1_sessions__session_id__messages__message_id__delete'),
 ('/api/v1/sessions/{session_id}/rounds/{round_num}',
  'delete',
  'delete_round_api_v1_sessions__session_id__rounds__round_num__delete'),
 ('/api/v1/sessions/{session_id}/scene', 'get', 'get_session_scene_api_v1_sessions__session_id__scene_get'),
 ('/api/v1/sessions/{session_id}/scene', 'put', 'set_session_scene_api_v1_sessions__session_id__scene_put'),
 ('/api/v1/settings', 'get', 'get_settings_api_v1_settings_get'),
 ('/api/v1/settings', 'patch', 'put_settings_api_v1_settings_patch'),
 ('/api/v1/settings', 'put', 'put_settings_api_v1_settings_put'),
 ('/api/v1/settings/test', 'post', 'test_settings_api_v1_settings_test_post')]


def test_openapi_operation_set_is_stable_after_route_split(tmp_path):
    app = create_app(
        AppSettings(
            runtime_dir=tmp_path / "runtime",
            llm_mode="demo",
            tts_provider="browser",
            asr_provider="browser",
            role_audit_enabled=False,
            context_compaction_enabled=False,
        )
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()
    assert caught == []
    actual = sorted(
        (path, method, operation.get("operationId", ""))
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if method in {"get", "post", "put", "patch", "delete", "options", "head"}
    )
    assert actual == EXPECTED_OPERATIONS
    operation_ids = [operation_id for _path, _method, operation_id in actual]
    assert len(operation_ids) == len(set(operation_ids))


def test_static_mount_contract_is_stable_after_route_split(tmp_path):
    app = create_app(
        AppSettings(
            runtime_dir=tmp_path / "runtime",
            llm_mode="demo",
            tts_provider="browser",
            asr_provider="browser",
            role_audit_enabled=False,
            context_compaction_enabled=False,
        )
    )

    mounts = {
        (route.path, route.name)
        for route in app.routes
        if route.__class__.__name__ == "Mount"
    }
    assert mounts == {
        ("/assets", "assets"),
        ("/api/v1/avatar/files", "avatars"),
        ("/api/v1/character/files", "character-files"),
        ("/api/v1/art/files", "art-pack-files"),
        ("/api/v1/scene/files", "scene-files"),
    }


def test_multi_method_legacy_tombstones_keep_shared_410_contract(tmp_path):
    app = create_app(
        AppSettings(
            runtime_dir=tmp_path / "runtime",
            llm_mode="demo",
            tts_provider="browser",
            asr_provider="browser",
            role_audit_enabled=False,
            context_compaction_enabled=False,
        )
    )
    client = TestClient(app)
    cases = {
        "/api/v1/characters/fate-options": "旧版命格选项接口已废弃，请使用 V7 命格旅程接口",
        "/api/v1/character-drafts": "旧角色档案、蓝图和系统提示词创建链已废弃，请使用 V7 命格生成 V2 角色卡",
        "/api/v1/character-drafts/nested/path": (
            "旧角色档案、蓝图和系统提示词创建链已废弃，请使用 V7 命格生成 V2 角色卡"
        ),
    }
    methods = {
        "/api/v1/characters/fate-options": ("GET", "POST"),
        "/api/v1/character-drafts": ("GET", "POST", "PUT", "PATCH", "DELETE"),
        "/api/v1/character-drafts/nested/path": ("GET", "POST", "PUT", "PATCH", "DELETE"),
    }

    for path, detail in cases.items():
        for method in methods[path]:
            response = client.request(method, path)
            assert response.status_code == 410
            assert response.json() == {"detail": detail}
            assert response.headers["content-type"] == "application/json"

    grouped_endpoints = {
        "fate": {
            id(route.endpoint)
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == "/api/v1/characters/fate-options"
        },
        "drafts": {
            id(route.endpoint)
            for route in app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api/v1/character-drafts")
        },
    }
    assert len(grouped_endpoints["fate"]) == 1
    assert len(grouped_endpoints["drafts"]) == 1
