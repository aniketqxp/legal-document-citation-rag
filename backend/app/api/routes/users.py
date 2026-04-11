from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.crud.user import update_user
from app.models.user import UserPublic, UserUpdateMe

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic)
async def read_current_user(current_user: CurrentUser) -> UserPublic:
    """Return the currently authenticated user's profile."""
    return UserPublic.model_validate(current_user)


@router.patch("/me", response_model=UserPublic)
async def update_current_user(
    session: SessionDep,
    current_user: CurrentUser,
    user_update: UserUpdateMe,
) -> UserPublic:
    """Update the current user's own profile (name only).

    Users cannot change their own email, password, or superuser status
    through this endpoint — those require dedicated endpoints.
    """
    updated = await update_user(session, db_user=current_user, user_in=user_update)
    return UserPublic.model_validate(updated)
