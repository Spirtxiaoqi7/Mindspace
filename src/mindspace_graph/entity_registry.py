"""Deterministic entity identity and explicitly curated aliases."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from mindspace_graph.product_database import ProductDatabase


def normalize_entity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    text = re.sub(r"[\s\-_·•]+", "", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)
    return text


class EntityRegistry:
    """Resolve only exact normalized values or administrator-approved aliases."""

    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    @staticmethod
    def _identifier(scope: str, entity_type: str, normalized: str) -> str:
        digest = hashlib.sha256(f"{scope}\0{entity_type}\0{normalized}".encode()).hexdigest()[:24]
        return f"ent_{digest}"

    def resolve(
        self,
        value: Any,
        *,
        scope: str,
        entity_type: str,
        create: bool = True,
    ) -> str | None:
        normalized = normalize_entity(value)
        if not normalized:
            return None
        with self.database.connection() as db:
            row = db.execute(
                "SELECT entity_id FROM entity_aliases WHERE scope=? AND entity_type=? "
                "AND alias_normalized=?",
                (scope, entity_type, normalized),
            ).fetchone()
            if row is not None:
                entity_id = str(row["entity_id"])
                merged = db.execute(
                    "SELECT merged_into FROM entities WHERE entity_id=?", (entity_id,)
                ).fetchone()
                return str(merged["merged_into"] or entity_id) if merged else entity_id
            if not create:
                return None
            entity_id = self._identifier(scope, entity_type, normalized)
            db.execute(
                "INSERT OR IGNORE INTO entities(entity_id, scope, entity_type, canonical_value, "
                "canonical_normalized) VALUES(?, ?, ?, ?, ?)",
                (entity_id, scope, entity_type, str(value), normalized),
            )
            db.execute(
                "INSERT OR IGNORE INTO entity_aliases(scope, entity_type, alias_normalized, "
                "alias_value, entity_id, source) VALUES(?, ?, ?, ?, ?, 'canonical')",
                (scope, entity_type, normalized, str(value), entity_id),
            )
            return entity_id

    def add_alias(
        self,
        entity_id: str,
        alias: str,
        *,
        source: str = "user",
    ) -> dict[str, Any]:
        normalized = normalize_entity(alias)
        if not normalized:
            raise ValueError("alias must not be blank")
        with self.database.connection() as db:
            entity = db.execute(
                "SELECT * FROM entities WHERE entity_id=? AND status='active'", (entity_id,)
            ).fetchone()
            if entity is None:
                raise KeyError("active entity not found")
            conflict = db.execute(
                "SELECT entity_id FROM entity_aliases WHERE scope=? AND entity_type=? "
                "AND alias_normalized=?",
                (entity["scope"], entity["entity_type"], normalized),
            ).fetchone()
            if conflict is not None and str(conflict["entity_id"]) != entity_id:
                raise ValueError("alias already belongs to another entity")
            db.execute(
                "INSERT OR REPLACE INTO entity_aliases(scope, entity_type, alias_normalized, "
                "alias_value, entity_id, source) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    entity["scope"],
                    entity["entity_type"],
                    normalized,
                    alias,
                    entity_id,
                    source,
                ),
            )
        return {"entity_id": entity_id, "alias": alias, "normalized": normalized}

    def merge(self, source_entity_id: str, target_entity_id: str) -> None:
        if source_entity_id == target_entity_id:
            return
        with self.database.connection() as db:
            source = db.execute(
                "SELECT * FROM entities WHERE entity_id=?", (source_entity_id,)
            ).fetchone()
            target = db.execute(
                "SELECT * FROM entities WHERE entity_id=?", (target_entity_id,)
            ).fetchone()
            if source is None or target is None:
                raise KeyError("entity not found")
            if (source["scope"], source["entity_type"]) != (target["scope"], target["entity_type"]):
                raise ValueError("entities from different scope or type cannot be merged")
            db.execute(
                "DELETE FROM entity_aliases WHERE entity_id=? AND alias_normalized IN "
                "(SELECT alias_normalized FROM entity_aliases WHERE entity_id=?)",
                (source_entity_id, target_entity_id),
            )
            db.execute(
                "UPDATE entity_aliases SET entity_id=? WHERE entity_id=?",
                (target_entity_id, source_entity_id),
            )
            db.execute(
                "UPDATE entities SET status='merged', merged_into=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE entity_id=?",
                (target_entity_id, source_entity_id),
            )

    def list(self, *, scope: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM entities WHERE status='active'"
        parameters: tuple[Any, ...] = ()
        if scope:
            sql += " AND scope=?"
            parameters = (scope,)
        sql += " ORDER BY scope, entity_type, canonical_value"
        with self.database.connection() as db:
            entities = db.execute(sql, parameters).fetchall()
            output = []
            for entity in entities:
                aliases = db.execute(
                    "SELECT alias_value, source FROM entity_aliases WHERE entity_id=? "
                    "ORDER BY alias_value",
                    (entity["entity_id"],),
                ).fetchall()
                output.append(
                    {
                        "entity_id": entity["entity_id"],
                        "scope": entity["scope"],
                        "entity_type": entity["entity_type"],
                        "canonical_value": entity["canonical_value"],
                        "aliases": [dict(row) for row in aliases],
                    }
                )
        return output
