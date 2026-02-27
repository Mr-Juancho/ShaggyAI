"""Product scope loader and guardrails."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import BASE_DIR, logger

SCOPE_FILE: Path = BASE_DIR / "PRODUCT_SCOPE.md"
_CAPABILITY_RE = re.compile(r"`([a-z0-9_]+)`")


@dataclass
class ProductScope:
    """Loads and validates allowed product capabilities from PRODUCT_SCOPE.md."""

    path: Path = SCOPE_FILE
    capabilities: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        """Reload scope file from disk."""
        if not self.path.exists():
            logger.warning(f"PRODUCT_SCOPE no encontrado: {self.path}")
            self.capabilities = set()
            return

        text = self.path.read_text(encoding="utf-8", errors="ignore")
        found = {match.group(1).strip() for match in _CAPABILITY_RE.finditer(text)}
        self.capabilities = {cap for cap in found if cap}
        logger.info(f"ProductScope cargado con {len(self.capabilities)} capacidades.")

    def is_allowed(self, capability_id: str) -> bool:
        """Returns True when a capability belongs to product scope."""
        return capability_id in self.capabilities

    def filter_allowed(self, capability_ids: list[str]) -> list[str]:
        """Filters a list preserving order and deduplicating."""
        filtered: list[str] = []
        seen: set[str] = set()
        for capability_id in capability_ids:
            if capability_id in seen:
                continue
            seen.add(capability_id)
            if capability_id in self.capabilities:
                filtered.append(capability_id)
        return filtered
