from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class CreationJob:
    id: int
    account_id: int
    platform: str
    step: str
    status: str
    error: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "CreationJob":
        return cls(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            platform=row["platform"],
            step=row["step"] or "",
            status=row["status"] or "WAITING",
            error=row["error"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
