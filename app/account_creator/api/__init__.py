from .accounts import router as accounts_router
from .aliases import router as aliases_router
from .profile import router as profile_router

__all__ = ["accounts_router", "aliases_router", "profile_router"]
