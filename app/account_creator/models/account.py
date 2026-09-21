from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class Account:
    id: int
    account_number: int
    email: str
    email_alias_id: str
    display_name: str
    username: str
    bio: str
    avatar_path: str
    browser_profile_path: str
    status: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Account":
        return cls(
            id=int(row["id"]),
            account_number=int(row["account_number"]),
            email=row["email"] or "",
            email_alias_id=row["email_alias_id"] or "",
            display_name=row["display_name"] or "",
            username=row["username"] or "",
            bio=row["bio"] or "",
            avatar_path=row["avatar_path"] or "",
            browser_profile_path=row["browser_profile_path"] or "",
            status=row["status"] or "CREATED",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
