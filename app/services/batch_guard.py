"""Conservative handling of explicit platform upload limits in a batch.

Never retry, switch accounts, or change network identity to work around an
explicit platform refusal. Stop sending further videos to the affected target
for the remainder of this batch; leave its unposted files in the inbox.
"""
from __future__ import annotations


FINISHED_STATES = frozenset({"done", "already", "error", "blocked"})


def platform_limit_reason(target: str, error: str) -> str | None:
    """Only classify clearly reported upload/action restrictions, not 404/5xx."""
    message = (error or "").casefold()

    if target.startswith("youtube:") and any(phrase in message for phrase in (
        "the user has exceeded the number of videos they may upload",
        "uploadlimitexceeded",
        "daily upload limit",
    )):
        return "YouTube сообщил об ограничении загрузок этого аккаунта"

    if target.startswith("instagram:") and any(phrase in message for phrase in (
        "user is performing too many actions",
        "action blocked",
        "rate limit reached",
        "application request limit reached",
    )):
        return "Instagram сообщил об ограничении действий этого аккаунта"

    return None


def batch_counts(items: list[dict]) -> dict[str, int]:
    """Successes and errors are mutually exclusive, unlike old 'completed'."""
    statuses = [str(item.get("status", "queued")) for item in items]
    published = statuses.count("done")
    already = statuses.count("already")
    errors = statuses.count("error")
    blocked = statuses.count("blocked")
    return {
        "successful_videos": published,
        "already_videos": already,
        "failed_videos": errors,
        "blocked_videos": blocked,
        # Keep the existing progress-field contract. It counts attempts handled,
        # not verified publications; the UI uses successful_videos instead.
        "completed_videos": published + already + errors + blocked,
    }
