from collections.abc import MutableMapping
from typing import Any

import streamlit as st

from app.auth.google_oauth import (
    InvalidGoogleIdentity,
    OAuthConfigurationError,
    configure_google_oauth,
    validate_google_claims,
)
from app.auth.judge import (
    JudgeAccessError,
    judge_demo_enabled,
    resume_judge_workspace,
    start_judge_workspace,
)
from app.database.repository import ProfileRepository, profile_repository

AUTH_SESSION_KEYS = (
    "authenticated",
    "auth_mode",
    "current_user_id",
    "user_email",
    "user_name",
    "is_demo",
)
USER_WORKSPACE_KEYS = (
    "workflow_result",
    "workflow_thread_id",
    "selected_user_id",
    "active_conversation_id",
    "conversation_selector",
    "agent_last_result",
    "memory_recovery_checked_user_id",
)
JUDGE_LOGIN_KEYS = (
    "judge_login_open",
    "judge_recovery_code_once",
)
JUDGE_WIDGET_PREFIXES = ("judge_start_", "judge_resume_")
USER_WIDGET_PREFIXES = (
    "edit_",
    "confirm_",
    "cancel_",
    "missing_fields_",
    "onboarding_type_",
    "document_",
    "rollback_",
    "accept_memory_",
    "reject_memory_",
    "accept_profile_change_",
    "reject_profile_change_",
    "apply_profile_draft_",
    "keep_existing_memory_",
    "keep_both_memory_",
)


def clear_auth_state(
    state: MutableMapping[str, Any], *, preserve_judge_login: bool = False
) -> None:
    """Remove active identity and user-scoped UI state from one session."""

    for key in list(state):
        if (
            key in AUTH_SESSION_KEYS
            or key in USER_WORKSPACE_KEYS
            or (key in JUDGE_LOGIN_KEYS and not preserve_judge_login)
            or (
                key.startswith(JUDGE_WIDGET_PREFIXES)
                and not preserve_judge_login
            )
            or key.startswith(USER_WIDGET_PREFIXES)
        ):
            state.pop(key, None)


def set_active_identity(
    state: MutableMapping[str, Any], user: dict[str, Any], mode: str
) -> None:
    """Cache a database identity for UI display; SQL remains authoritative."""

    if mode not in {"google", "judge"}:
        raise ValueError("Authentication mode must be google or judge.")
    if mode == "judge" and user.get("is_demo") is not True:
        raise ValueError("Judge mode requires an isolated demo user.")
    if mode == "google" and user.get("is_demo") is True:
        raise ValueError("Google authentication cannot assume the demo identity.")

    state["authenticated"] = True
    state["auth_mode"] = mode
    state["current_user_id"] = user["user_id"]
    state["user_email"] = user.get("email")
    state["user_name"] = user["name"]
    state["is_demo"] = mode == "judge"


def _render_account_sidebar(
    user: dict[str, Any],
    mode: str,
    repository: ProfileRepository,
) -> None:
    with st.sidebar:
        if mode == "judge":
            st.warning("Demo workspace — uses synthetic data")
        elif user.get("profile_image"):
            st.image(user["profile_image"], width=48)

        st.write(user["name"])
        if user.get("email"):
            st.caption(user["email"])
        else:
            st.caption("Anonymous judge workspace")

        if st.button("Logout", icon=":material/logout:"):
            active_conversation_id = st.session_state.get("active_conversation_id")
            if active_conversation_id:
                try:
                    from app.services.conversation_memory import trigger_conversation_boundary

                    trigger_conversation_boundary(
                        user["user_id"], str(active_conversation_id),
                        process_now=False, repository=repository,
                    )
                except ValueError:
                    pass
            clear_auth_state(st.session_state)
            if mode == "google":
                st.logout()
            else:
                st.rerun()


def _render_login_page(
    repository: ProfileRepository,
    google_client_id: str | None,
    google_error: str | None,
) -> None:
    st.title("Welcome to CareerTrace AI")
    st.write("Sign in to access your private career profile and documents.")
    if st.button(
        "Continue with Google",
        type="primary",
        icon=":material/login:",
        disabled=google_client_id is None,
    ):
        st.login()
    if google_error:
        st.caption(f"Google sign-in is unavailable: {google_error}")

    if judge_demo_enabled():
        if st.button("Try Judge Demo", icon=":material/science:"):
            st.session_state["judge_login_open"] = True
        if st.session_state.get("judge_login_open"):
            st.markdown("## Judge Demo")
            with st.form("start_judge_workspace"):
                access_code = st.text_input(
                    "Demo Access Code",
                    type="password",
                    key="judge_start_access_code",
                )
                if st.form_submit_button("Start New Workspace"):
                    try:
                        demo_user, recovery_code = start_judge_workspace(
                            access_code, repository
                        )
                        clear_auth_state(st.session_state)
                        set_active_identity(st.session_state, demo_user, "judge")
                        st.session_state["judge_recovery_code_once"] = recovery_code
                        st.rerun()
                    except JudgeAccessError as error:
                        st.error(str(error))
            with st.expander("Resume Existing Workspace"):
                with st.form("resume_judge_workspace"):
                    access_code = st.text_input(
                        "Demo Access Code",
                        type="password",
                        key="judge_resume_access_code",
                    )
                    recovery_code = st.text_input(
                        "Recovery Code",
                        type="password",
                        key="judge_resume_recovery_code",
                    )
                    if st.form_submit_button("Resume Existing Workspace"):
                        try:
                            demo_user = resume_judge_workspace(
                                access_code, recovery_code, repository
                            )
                            clear_auth_state(st.session_state)
                            set_active_identity(st.session_state, demo_user, "judge")
                            st.rerun()
                        except JudgeAccessError as error:
                            st.error(str(error))


def _render_recovery_code_once() -> None:
    recovery_code = st.session_state.get("judge_recovery_code_once")
    if not recovery_code:
        return
    with st.container(border=True):
        st.subheader("Your recovery code")
        st.code(recovery_code, language=None)
        st.warning(
            "Save this code now. You will need it to return to this workspace "
            "after logout. CareerTrace does not store the plaintext code."
        )
        st.caption("Use the copy control in the code box, then store it securely.")
        if st.button("I saved my recovery code"):
            st.session_state.pop("judge_recovery_code_once", None)
            st.rerun()


def require_authenticated_user(
    repository: ProfileRepository = profile_repository,
) -> dict[str, Any] | None:
    """Resolve Google or fixed judge identity without accepting URL user IDs."""

    google_client_id: str | None = None
    google_error: str | None = None
    try:
        google_client_id = configure_google_oauth()
    except OAuthConfigurationError as error:
        google_error = str(error)

    google_logged_in = bool(
        google_client_id and getattr(st.user, "is_logged_in", False)
    )
    if google_logged_in:
        try:
            identity = validate_google_claims(dict(st.user), google_client_id)
            user = repository.get_or_create_google_user(**identity)
            if user.get("is_demo"):
                raise InvalidGoogleIdentity(
                    "A Google identity cannot map to the judge demo account."
                )
        except (InvalidGoogleIdentity, ValueError) as error:
            clear_auth_state(st.session_state)
            st.error(f"Google sign-in could not be validated: {error}")
            if st.button("Sign out", icon=":material/logout:"):
                st.logout()
            return None

        set_active_identity(st.session_state, user, "google")
        _recover_pending_once(user, repository)
        _render_account_sidebar(user, "google", repository)
        return user

    if st.session_state.get("auth_mode") == "judge":
        try:
            user = repository.get_demo_user(
                str(st.session_state.get("current_user_id") or "")
            )
        except ValueError:
            clear_auth_state(st.session_state)
            _render_login_page(repository, google_client_id, google_error)
            return None
        set_active_identity(st.session_state, user, "judge")
        _recover_pending_once(user, repository)
        _render_recovery_code_once()
        _render_account_sidebar(user, "judge", repository)
        return user

    clear_auth_state(st.session_state, preserve_judge_login=True)
    _render_login_page(repository, google_client_id, google_error)
    return None


def _recover_pending_once(
    user: dict[str, Any], repository: ProfileRepository
) -> None:
    if st.session_state.get("memory_recovery_checked_user_id") == user["user_id"]:
        return
    st.session_state["memory_recovery_checked_user_id"] = user["user_id"]
    try:
        from app.services.conversation_memory import (
            recover_pending_conversation_extractions,
        )

        recover_pending_conversation_extractions(user["user_id"], repository)
    except Exception:
        # The watermark remains pending; login is never denied by extraction work.
        st.sidebar.warning("Conversation memory review will retry automatically.")
