from __future__ import annotations

import pytest

from app.models import Repo, User
from app.services.repos import repo_permissions


def _repo(*, owner_id: int, visibility: str) -> Repo:
    return Repo(
        id=1,
        owner_id=owner_id,
        name="repo",
        description="",
        visibility=visibility,
    )


def _user(*, user_id: int, role: str = "reader") -> User:
    return User(
        id=user_id,
        email=f"user{user_id}@example.com",
        password_hash="x",
        display_name=f"user{user_id}",
        role=role,
        is_active=True,
    )


@pytest.mark.parametrize(
    ("label", "visibility", "user_id", "role", "expected"),
    [
        # (can_edit, can_delete, can_manage_papers, can_star, can_chat)
        ("owner_public", "public", 1, "reader", (True, True, True, False, True)),
        ("owner_private", "private", 1, "reader", (True, True, True, False, True)),
        ("reader_public", "public", 2, "reader", (False, False, False, True, True)),
        ("admin_public", "public", 2, "admin", (False, False, False, True, True)),
        ("admin_private", "private", 2, "admin", (True, True, True, False, True)),
    ],
)
def test_repo_permissions_truth_table(
    label: str,
    visibility: str,
    user_id: int,
    role: str,
    expected: tuple[bool, bool, bool, bool, bool],
) -> None:
    repo = _repo(owner_id=1, visibility=visibility)
    user = _user(user_id=user_id, role=role)

    permissions = repo_permissions(repo, user)

    assert (
        permissions.can_edit,
        permissions.can_delete,
        permissions.can_manage_papers,
        permissions.can_star,
        permissions.can_chat,
    ) == expected, label


def test_repo_permissions_admin_owner_is_treated_as_owner() -> None:
    """管理员同时是创建者时按创建者处理：可管理、但不能 star 自己的仓库。"""
    repo = _repo(owner_id=5, visibility="public")
    admin = _user(user_id=5, role="admin")

    permissions = repo_permissions(repo, admin)

    assert permissions.can_edit is True
    assert permissions.can_delete is True
    assert permissions.can_manage_papers is True
    assert permissions.can_star is False
    assert permissions.can_chat is True


def test_repo_permissions_empty_repo_still_allows_chat() -> None:
    """can_chat 只表示权限；空仓库由对话接口返回 409 repo_empty。"""
    repo = _repo(owner_id=1, visibility="public")

    assert repo_permissions(repo, _user(user_id=2)).can_chat is True
