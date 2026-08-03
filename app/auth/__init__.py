"""Authentication boundary for CareerTrace."""

from app.auth.session import (
    clear_auth_state,
    require_authenticated_user,
    set_active_identity,
)

__all__ = ["clear_auth_state", "require_authenticated_user", "set_active_identity"]
