from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class SocialAccount:
    id: int
    account_id: int
    platform: str
    username: str
    profile_url: str
    status: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "SocialAccount":
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            platform=row["platform"],
            username=row["username"] or "",
            profile_url=row["profile_url"] or "",
            status=row["status"] or "NOT_CREATED",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
