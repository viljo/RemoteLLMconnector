"""Preprompt (system prompt) storage for the broker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger()


@dataclass
class Preprompt:
    """A stored system prompt configuration."""

    name: str
    content: str
    is_default: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        return {
            "name": self.name,
            "content": self.content,
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Preprompt:
        """Create Preprompt from dictionary."""
        return cls(
            name=data["name"],
            content=data["content"],
            is_default=data.get("is_default", False),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


class PrepromptStore:
    """YAML-based preprompt storage with in-memory caching."""

    def __init__(self, file_path: Path | str | None = None) -> None:
        """Initialize the preprompt store.

        Args:
            file_path: Path to the preprompts.yaml file. If None, operates in memory-only mode.
        """
        self.file_path = Path(file_path) if file_path else None
        self._preprompts: dict[str, Preprompt] = {}
        self._load()

    def _load(self) -> None:
        """Load preprompts from YAML file."""
        if self.file_path is None or not self.file_path.exists():
            log.info("No preprompts file found, starting with empty store")
            return

        try:
            with open(self.file_path) as f:
                data = yaml.safe_load(f)

            if data and "preprompts" in data:
                for preprompt_data in data["preprompts"]:
                    preprompt = Preprompt.from_dict(preprompt_data)
                    self._preprompts[preprompt.name] = preprompt

            log.info(
                "Loaded preprompts from file",
                count=len(self._preprompts),
                file=str(self.file_path),
            )
        except Exception as e:
            log.error("Failed to load preprompts file", error=str(e), file=str(self.file_path))

    def _save(self) -> None:
        """Save preprompts to YAML file."""
        if self.file_path is None:
            return

        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"preprompts": [p.to_dict() for p in self._preprompts.values()]}
            with open(self.file_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            log.debug("Saved preprompts to file", count=len(self._preprompts))
        except Exception as e:
            log.error("Failed to save preprompts file", error=str(e))

    def get_by_name(self, name: str) -> Preprompt | None:
        """Get preprompt by name."""
        return self._preprompts.get(name)

    def get_default(self) -> Preprompt | None:
        """Get the default preprompt."""
        for preprompt in self._preprompts.values():
            if preprompt.is_default:
                return preprompt
        return None

    def get_all(self) -> list[Preprompt]:
        """Get all preprompts."""
        return list(self._preprompts.values())

    def create_or_update(
        self,
        name: str,
        content: str,
        is_default: bool = False,
    ) -> Preprompt:
        """Create or update a preprompt.

        Args:
            name: Preprompt name/identifier
            content: The system prompt content
            is_default: Whether this is the default preprompt
        """
        existing = self._preprompts.get(name)

        if existing:
            existing.content = content
            existing.is_default = is_default
            existing.updated_at = datetime.utcnow()
            preprompt = existing
        else:
            preprompt = Preprompt(
                name=name,
                content=content,
                is_default=is_default,
            )
            self._preprompts[name] = preprompt

        # If this is set as default, unset others
        if is_default:
            for p in self._preprompts.values():
                if p.name != name:
                    p.is_default = False

        self._save()
        log.info("Saved preprompt", name=name, is_default=is_default)
        return preprompt

    def set_default(self, name: str) -> bool:
        """Set a preprompt as the default.

        Args:
            name: Name of the preprompt to set as default

        Returns:
            True if preprompt exists, False otherwise
        """
        preprompt = self._preprompts.get(name)
        if preprompt is None:
            return False

        # Unset all others
        for p in self._preprompts.values():
            p.is_default = p.name == name

        self._save()
        log.info("Set default preprompt", name=name)
        return True

    def delete(self, name: str) -> bool:
        """Delete a preprompt.

        Args:
            name: Name of the preprompt to delete

        Returns:
            True if preprompt existed, False otherwise
        """
        if name not in self._preprompts:
            return False

        del self._preprompts[name]
        self._save()
        log.info("Deleted preprompt", name=name)
        return True
