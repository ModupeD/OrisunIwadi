"""Who may see what. Roles are ordered: each role can do everything the roles below it can.

  viewer      dataset list, summaries, grouped counts (no row-level data)
  researcher  + row-level data
  admin       + quarantine and run log
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    RESEARCHER = "researcher"
    ADMIN = "admin"

    @property
    def level(self) -> int:
        return list(Role).index(self)

    def can_act_as(self, needed: Role) -> bool:
        return self.level >= needed.level


@dataclass(frozen=True)
class User:
    username: str
    role: Role
