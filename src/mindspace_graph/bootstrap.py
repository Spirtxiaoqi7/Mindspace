"""Application composition root and concrete adapter assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mindspace_graph.adapters.profile_repository import JsonProfileRepository
from mindspace_graph.adapters.session_repository import JsonSessionRepository
from mindspace_graph.adapters.in_memory import DeterministicLanguageModel, RegexRolePolicy
from mindspace_graph.adapters.json_audit import JsonlAudit
from mindspace_graph.adapters.local_retriever import LocalKnowledgeRetriever
from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.application.conversation import ConversationService
from mindspace_graph.art_catalog import ArtCatalogService
from mindspace_graph.static_paths import BUILTIN_ART_MANIFEST
from mindspace_graph.asr_vocabulary import ASRVocabularyStore
from mindspace_graph.cancellation import CancellationRegistry
from mindspace_graph.capabilities import ReadOnlyCapabilityService
from mindspace_graph.characters import CharacterRepository
from mindspace_graph.compaction import ContextCompactionService
from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.emotion_disabled import DisabledEmotionCoordinator
from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.event_memory import EventMemoryStore
from mindspace_graph.memory_service import StructuredMemoryService
from mindspace_graph.models import ApiConfig
from mindspace_graph.ports import Dependencies, LanguageModelFactoryPort, LanguageModelPort
from mindspace_graph.product_config import ProductConfigStore
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.prompt_inspection import PromptInspectionStore
from mindspace_graph.role_audit import RoleAuditService
from mindspace_graph.settings import AppSettings
from mindspace_graph.shared_chapters import SharedChapterService


@dataclass(slots=True)
class ProductContainer:
    settings: AppSettings
    cancellation: CancellationRegistry
    profiles: JsonProfileRepository
    sessions: JsonSessionRepository
    knowledge: LocalKnowledgeRetriever
    memory: StructuredMemoryStore
    memory_service: StructuredMemoryService
    event_memory: EventMemoryStore
    audit: JsonlAudit
    config: ProductConfigStore
    conversation: ConversationService
    context: ContextLedger
    compaction: ContextCompactionService
    database: ProductDatabase
    role_audit: RoleAuditService
    entities: EntityRegistry
    asr_vocabulary: ASRVocabularyStore
    capabilities: ReadOnlyCapabilityService
    emotion: DisabledEmotionCoordinator
    prompt_inspector: PromptInspectionStore
    characters: CharacterRepository
    chapters: SharedChapterService
    art_catalog: ArtCatalogService


@dataclass(slots=True)
class _ConfiguredLanguageModelFactory(LanguageModelFactoryPort):
    settings: AppSettings

    def create(self) -> LanguageModelPort:
        if self.settings.llm_mode == "openai":
            return OpenAICompatibleLanguageModel()
        return DeterministicLanguageModel()


def build_container(settings: AppSettings | None = None) -> ProductContainer:
    settings = settings or AppSettings.from_env()
    settings.ensure_directories()
    config = ProductConfigStore(settings.runtime_dir / "config" / "settings.json", settings)
    cancellation = CancellationRegistry()
    database = ProductDatabase(settings.runtime_dir / "data" / "context" / "context.db")
    event_memory = EventMemoryStore(database)
    database.begin_projection_repair()
    prompt_inspector = PromptInspectionStore(database)
    entities = EntityRegistry(database)
    profiles = JsonProfileRepository(settings.runtime_dir / "data" / "profiles", database=database)
    asr_vocabulary = ASRVocabularyStore(
        settings.runtime_dir / "data" / "asr" / "vocabulary.json",
        profiles,
    )
    sessions = JsonSessionRepository(settings.runtime_dir / "data" / "sessions", database=database)
    characters = CharacterRepository(
        settings.runtime_dir / "data" / "characters",
        database=database,
        profiles=profiles,
        sessions=sessions,
        avatar_config_path=settings.runtime_dir / "data" / "avatars" / "config.json",
    )
    profiles.bind_characters(characters)
    context = ContextLedger(settings.runtime_dir / "data" / "context" / "context.db", database=database)
    context.configure_hard_limit(
        context_window=settings.llm_context_window,
        hard_ratio=settings.context_compaction_hard_ratio,
        reserved_tokens=settings.context_compaction_max_tokens,
    )
    memory = StructuredMemoryStore(
        settings.runtime_dir / "data" / "structured-memory.json",
        database=database,
        entity_registry=entities,
    )
    memory.bind_legacy_character(str(characters.default()["character_id"]))
    memory_service = StructuredMemoryService(profiles, memory, database=database, entity_registry=entities)
    memory.migrate_entity_identities()
    knowledge = LocalKnowledgeRetriever(
        settings.runtime_dir / "data" / "knowledge.json",
        sessions=sessions,
        embedding_model_path=(settings.model_root / "shibing624" / "text2vec-base-chinese"),
        memory_store=memory,
        reranker_model_path=(
            settings.model_root / "BAAI" / "bge-reranker-base"
            if (settings.model_root / "BAAI" / "bge-reranker-base").exists()
            else None
        ),
    )
    audit = JsonlAudit(settings.runtime_dir / "logs" / "events.jsonl")
    capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: config.snapshot(redact=False),
        runtime_dir=settings.runtime_dir,
        audit=audit,
    )
    emotion = DisabledEmotionCoordinator()
    language_model_factory = _ConfiguredLanguageModelFactory(settings)
    llm = language_model_factory.create()
    chapters = SharedChapterService(
        database,
        characters=characters,
        sessions=sessions,
        audit=audit,
        llm_provider=lambda: dependencies.llm,
        api_provider=lambda: ApiConfig(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=0.65,
            max_tokens=1_000,
        ),
    )
    art_catalog = ArtCatalogService(
        BUILTIN_ART_MANIFEST,
        settings.runtime_dir / "data" / "assets" / "packs",
    )
    dependencies = Dependencies(
        retriever=knowledge,
        profiles=profiles,
        sessions=sessions,
        llm=llm,
        role_policy=RegexRolePolicy(),
        audit=audit,
        language_model_factory=language_model_factory,
        cancellation=cancellation,
        memory=memory,
        event_memory=event_memory,
        context=context,
        database=database,
        role_audit_enabled=settings.role_audit_enabled,
        entities=entities,
        capabilities=capabilities,
        emotion=emotion,
        prompt_inspector=prompt_inspector,
        characters=characters,
        activities=chapters,
        tts_provider=lambda: settings.tts_provider,
    )
    conversation = ConversationService(settings, dependencies, cancellation)
    return ProductContainer(
        settings=settings,
        cancellation=cancellation,
        profiles=profiles,
        sessions=sessions,
        knowledge=knowledge,
        memory=memory,
        memory_service=memory_service,
        event_memory=event_memory,
        audit=audit,
        config=config,
        conversation=conversation,
        context=context,
        compaction=conversation.compaction,
        database=database,
        role_audit=conversation.role_audit,
        entities=entities,
        asr_vocabulary=asr_vocabulary,
        capabilities=capabilities,
        emotion=emotion,
        prompt_inspector=prompt_inspector,
        characters=characters,
        chapters=chapters,
        art_catalog=art_catalog,
    )

__all__ = ["ProductContainer", "build_container"]
