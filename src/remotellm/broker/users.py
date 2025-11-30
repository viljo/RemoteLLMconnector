"""User model and storage for the broker web portal."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml

if TYPE_CHECKING:
    pass

log = structlog.get_logger()


class UserRole(str, Enum):
    """User role for access control."""

    ADMIN = "admin"
    USER = "user"


@dataclass
class User:
    """A registered user with GitLab OAuth credentials."""

    gitlab_username: str
    gitlab_id: int
    api_key: str
    role: UserRole = UserRole.USER
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used: datetime | None = None
    blocked: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        return {
            "gitlab_username": self.gitlab_username,
            "gitlab_id": self.gitlab_id,
            "api_key": self.api_key,
            "role": self.role.value,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "blocked": self.blocked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> User:
        """Create User from dictionary."""
        return cls(
            gitlab_username=data["gitlab_username"],
            gitlab_id=data["gitlab_id"],
            api_key=data["api_key"],
            role=UserRole(data.get("role", "user")),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_used=datetime.fromisoformat(data["last_used"]) if data.get("last_used") else None,
            blocked=data.get("blocked", False),
        )


class UserStore:
    """YAML-based user storage with in-memory caching."""

    def __init__(self, file_path: Path | str | None = None) -> None:
        """Initialize the user store.

        Args:
            file_path: Path to the users.yaml file. If None, operates in memory-only mode.
        """
        self.file_path = Path(file_path) if file_path else None
        self._users: dict[str, User] = {}  # Keyed by gitlab_username
        self._api_key_index: dict[str, User] = {}  # Keyed by api_key
        self._load()

    def _load(self) -> None:
        """Load users from YAML file."""
        if self.file_path is None or not self.file_path.exists():
            log.info("No users file found, starting with empty user store")
            return

        try:
            with open(self.file_path) as f:
                data = yaml.safe_load(f)

            if data and "users" in data:
                for user_data in data["users"]:
                    user = User.from_dict(user_data)
                    self._users[user.gitlab_username] = user
                    self._api_key_index[user.api_key] = user

            log.info("Loaded users from file", count=len(self._users), file=str(self.file_path))
        except Exception as e:
            log.error("Failed to load users file", error=str(e), file=str(self.file_path))

    def _save(self) -> None:
        """Save users to YAML file."""
        if self.file_path is None:
            return

        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"users": [user.to_dict() for user in self._users.values()]}
            with open(self.file_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            log.debug("Saved users to file", count=len(self._users))
        except Exception as e:
            log.error("Failed to save users file", error=str(e))

    def get_by_username(self, gitlab_username: str) -> User | None:
        """Get user by GitLab username."""
        return self._users.get(gitlab_username)

    def get_by_api_key(self, api_key: str) -> User | None:
        """Get user by API key."""
        return self._api_key_index.get(api_key)

    def get_all(self) -> list[User]:
        """Get all users."""
        return list(self._users.values())

    def create_user(
        self,
        gitlab_username: str,
        gitlab_id: int,
        role: UserRole | None = None,
    ) -> User:
        """Create a new user with a generated API key.

        If no users exist, the first user becomes admin.
        """
        # First user becomes admin
        if role is None:
            role = UserRole.ADMIN if len(self._users) == 0 else UserRole.USER

        api_key = f"sk-{secrets.token_hex(16)}"
        user = User(
            gitlab_username=gitlab_username,
            gitlab_id=gitlab_id,
            api_key=api_key,
            role=role,
        )

        self._users[gitlab_username] = user
        self._api_key_index[api_key] = user
        self._save()

        log.info(
            "Created new user",
            username=gitlab_username,
            role=role.value,
            is_first_user=(role == UserRole.ADMIN),
        )
        return user

    def update_last_used(self, user: User) -> None:
        """Update the last_used timestamp for a user."""
        user.last_used = datetime.utcnow()
        self._save()

    def set_blocked(self, gitlab_username: str, blocked: bool) -> bool:
        """Block or unblock a user. Returns True if user exists."""
        user = self._users.get(gitlab_username)
        if user is None:
            return False

        user.blocked = blocked
        self._save()
        log.info("User blocked status changed", username=gitlab_username, blocked=blocked)
        return True

    def set_role(self, gitlab_username: str, role: UserRole) -> bool:
        """Change a user's role. Returns True if user exists."""
        user = self._users.get(gitlab_username)
        if user is None:
            return False

        user.role = role
        self._save()
        log.info("User role changed", username=gitlab_username, role=role.value)
        return True

    def regenerate_api_key(self, gitlab_username: str) -> str | None:
        """Regenerate API key for a user. Returns new key or None if user not found."""
        user = self._users.get(gitlab_username)
        if user is None:
            return None

        # Remove old key from index
        del self._api_key_index[user.api_key]

        # Generate new key
        user.api_key = f"sk-{secrets.token_hex(16)}"
        self._api_key_index[user.api_key] = user
        self._save()

        log.info("Regenerated API key for user", username=gitlab_username)
        return user.api_key

    def delete_user(self, gitlab_username: str) -> bool:
        """Delete a user. Returns True if user existed."""
        user = self._users.get(gitlab_username)
        if user is None:
            return False

        del self._users[gitlab_username]
        del self._api_key_index[user.api_key]
        self._save()

        log.info("Deleted user", username=gitlab_username)
        return True

    def validate_api_key(self, api_key: str) -> User | None:
        """Validate an API key and return the user if valid and not blocked."""
        user = self._api_key_index.get(api_key)
        if user is None or user.blocked:
            return None
        return user
