from .accounts import router as accounts_router
from .aliases import router as aliases_router
from .profile import router as profile_router
from .browser_profiles import router as browser_profiles_router

__all__ = ["accounts_router", "aliases_router", "profile_router", "browser_profiles_router"]
