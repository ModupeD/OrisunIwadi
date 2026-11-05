"""Use case: check a username and password. Hashing and storage are ports, so this stays pure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.access import Role, User


@dataclass(frozen=True)
class StoredUser:
    username: str
    password_hash: str
    role: Role
    active: bool = True


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, password_hash: str) -> bool: ...


class UserStore(Protocol):
    def get(self, username: str) -> StoredUser | None: ...


class Authenticate:
    def __init__(self, users: UserStore, hasher: PasswordHasher) -> None:
        self.users = users
        self.hasher = hasher
        # Checked when the user doesn't exist, so a wrong username takes as long as a wrong
        # password and response times don't reveal which usernames exist.
        self._dummy_hash = hasher.hash("not-a-real-password")

    def __call__(self, username: str, password: str) -> User | None:
        stored = self.users.get(username)
        ok = self.hasher.verify(password, stored.password_hash if stored else self._dummy_hash)
        if stored is None or not ok or not stored.active:
            return None
        return User(username=stored.username, role=stored.role)
