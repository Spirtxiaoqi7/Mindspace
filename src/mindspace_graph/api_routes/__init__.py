"""Domain route registration for the Mindspace HTTP API."""

from .audio_scenes import register_routes as register_audio_scene_routes
from .characters_cards import register_routes as register_character_routes
from .chat_runs import register_routes as register_chat_routes
from .destiny_routes import register_routes as register_destiny_routes
from .legacy_routes import register_routes as register_legacy_routes
from .memory_knowledge import register_routes as register_memory_routes
from .system_settings import register_routes as register_system_routes

__all__ = [
    "register_audio_scene_routes",
    "register_character_routes",
    "register_chat_routes",
    "register_destiny_routes",
    "register_legacy_routes",
    "register_memory_routes",
    "register_system_routes",
]
