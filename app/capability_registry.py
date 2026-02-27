"""Capability registry loaded from CAPABILITIES.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.config import BASE_DIR, logger
from app.product_scope import ProductScope

REGISTRY_FILE: Path = BASE_DIR / "app" / "CAPABILITIES.yaml"


class JsonSchema(BaseModel):
    """Simple JSON schema representation for auditability."""

    type: str = "object"
    required: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class CapabilityDefinition(BaseModel):
    """Single capability definition entry."""

    id: str
    phase: int = Field(ge=1)
    provider: str
    summary: str
    input_schema: JsonSchema
    output_schema: JsonSchema
    fallback_to: list[str] = Field(default_factory=list)


class CapabilityRegistryFile(BaseModel):
    """Top-level file model."""

    version: int
    updated_at: str
    capabilities: list[CapabilityDefinition]


class CapabilityRegistry:
    """Runtime capability registry with scope filtering and fallback chains."""

    def __init__(
        self,
        path: Path = REGISTRY_FILE,
        product_scope: ProductScope | None = None,
    ) -> None:
        self.path = path
        self.product_scope = product_scope
        self.version: int = 0
        self.updated_at: str = ""
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self.reload()

    def reload(self) -> None:
        """Reload capability registry from disk and validate shape."""
        if not self.path.exists():
            logger.warning(f"CAPABILITIES registry no encontrado: {self.path}")
            self._capabilities = {}
            return

        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if "updated_at" in data:
            data["updated_at"] = str(data["updated_at"])
        try:
            parsed = CapabilityRegistryFile.model_validate(data)
        except ValidationError as exc:
            logger.error(f"CAPABILITIES.yaml invalido: {exc}")
            self._capabilities = {}
            return

        ids_seen: set[str] = set()
        capabilities: dict[str, CapabilityDefinition] = {}
        for capability in parsed.capabilities:
            if capability.id in ids_seen:
                logger.warning(f"Capability duplicada ignorada: {capability.id}")
                continue
            ids_seen.add(capability.id)
            capabilities[capability.id] = capability

        self.version = parsed.version
        self.updated_at = parsed.updated_at
        self._capabilities = capabilities
        logger.info(f"CapabilityRegistry cargado con {len(self._capabilities)} capacidades.")

    def get(self, capability_id: str) -> CapabilityDefinition | None:
        """Gets a capability by id."""
        capability = self._capabilities.get(capability_id)
        if not capability:
            return None
        if self.product_scope and not self.product_scope.is_allowed(capability_id):
            return None
        return capability

    def all_ids(self) -> list[str]:
        """Returns allowed capability ids respecting product scope."""
        if not self.product_scope:
            return list(self._capabilities.keys())
        return [
            capability_id
            for capability_id in self._capabilities
            if self.product_scope.is_allowed(capability_id)
        ]

    def resolve_chain(self, primary_capability: str) -> list[str]:
        """Builds primary + fallback chain, filtered by scope and registry presence."""
        first = self.get(primary_capability)
        if not first:
            return []

        chain: list[str] = [primary_capability]
        seen: set[str] = {primary_capability}
        for fallback_id in first.fallback_to:
            if fallback_id in seen:
                continue
            if self.get(fallback_id):
                chain.append(fallback_id)
                seen.add(fallback_id)
        return chain

    def ensure_scope_consistency(self) -> tuple[set[str], set[str]]:
        """Returns (in_scope_not_in_registry, in_registry_not_in_scope)."""
        if not self.product_scope:
            return set(), set()

        registry_ids = set(self._capabilities.keys())
        scope_ids = set(self.product_scope.capabilities)
        return scope_ids - registry_ids, registry_ids - scope_ids
