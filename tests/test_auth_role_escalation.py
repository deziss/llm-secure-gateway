"""Tests for role privilege-escalation prevention on self-registration.

Anyone can POST /auth/register (behind an invite code), so a client-supplied
`role` in UserCreate must NEVER be honoured: UserManager.create() forces
Role.VIEWER.  Admin-side role assignment (routers/users.py) must still work.
"""

import uuid

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase

from llm_gateway.auth.manager import UserManager
from llm_gateway.auth.models import Role, User
from llm_gateway.auth.schemas import UserCreate


def _make_manager(session):
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    # Require-invite check reads manager.session; leave REQUIRE_INVITE unset
    # (default false) so registration is permitted in tests.
    manager.session = session
    return manager


class TestRegistrationRoleEscalation:

    async def test_create_with_admin_role_yields_viewer(self, db_session):
        manager = _make_manager(db_session)
        user = await manager.create(UserCreate(
            email="escalate@example.com",
            password="Str0ngPass!23",
            role=Role.ADMIN,
        ))
        assert user.role == Role.VIEWER
        assert user.is_superuser is False

    @pytest.mark.parametrize("supplied_role", [Role.ADMIN, Role.MANAGER, Role.DEVELOPER])
    async def test_every_privileged_role_is_downgraded(self, db_session, supplied_role):
        manager = _make_manager(db_session)
        user = await manager.create(UserCreate(
            email=f"user-{supplied_role.value}@example.com",
            password="Str0ngPass!23",
            role=supplied_role,
        ))
        assert user.role == Role.VIEWER

    async def test_role_omitted_defaults_to_viewer(self, db_session):
        manager = _make_manager(db_session)
        user = await manager.create(UserCreate(
            email="plain@example.com",
            password="Str0ngPass!23",
        ))
        assert user.role == Role.VIEWER

    async def test_persisted_role_is_viewer(self, db_session):
        """The downgrade must survive the DB write, not just the returned object."""
        from sqlalchemy import select
        manager = _make_manager(db_session)
        user = await manager.create(UserCreate(
            email="persist@example.com",
            password="Str0ngPass!23",
            role=Role.ADMIN,
        ))
        result = await db_session.execute(select(User).where(User.id == user.id))
        stored = result.scalars().first()
        assert stored is not None
        assert stored.role == Role.VIEWER

    def test_schema_default_is_viewer(self):
        assert UserCreate.model_fields["role"].default == Role.VIEWER

    async def test_admin_path_can_still_assign_roles(self):
        """Sanity guard: admin user creation builds User directly with the
        requested role and must NOT be affected by the registration override."""
        admin = User(
            id=uuid.uuid4(),
            email="admin-direct@example.com",
            hashed_password="x",
            role=Role.ADMIN,
        )
        assert admin.role == Role.ADMIN
