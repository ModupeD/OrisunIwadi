"""Signed, short-lived access tokens (JWT, HS256)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from ...domain.access import Role, User

ISSUER = "orisun-iwadi"
MIN_SECRET_LENGTH = 32


class InvalidToken(Exception):
    pass


class JwtTokens:
    def __init__(self, secret: str, ttl: timedelta = timedelta(minutes=30)) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"JWT secret must be at least {MIN_SECRET_LENGTH} characters")
        self.secret = secret
        self.ttl = ttl

    def issue(self, user: User) -> str:
        now = datetime.now(UTC)
        return jwt.encode({"sub": user.username, "role": user.role.value, "iss": ISSUER,
                           "iat": now, "exp": now + self.ttl}, self.secret, algorithm="HS256")

    def decode(self, token: str) -> User:
        try:
            # algorithms is pinned, so a token can't pick "none" or another algorithm.
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=ISSUER,
                                options={"require": ["sub", "role", "exp", "iss"]})
            return User(username=claims["sub"], role=Role(claims["role"]))
        except (jwt.PyJWTError, ValueError) as e:
            raise InvalidToken(str(e)) from e
