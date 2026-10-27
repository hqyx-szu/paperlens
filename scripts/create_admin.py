"""创建或提升第一个管理员。

用法：
    uv run python -m scripts.create_admin --email admin@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy import select

from app.core.roles import UserRole
from app.core.security import hash_password
from app.db import get_session_factory, reset_engine
from app.models import User


async def run(email: str, password: str, display_name: str | None) -> None:
    email = email.lower()
    async with get_session_factory()() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                password_hash=hash_password(password),
                display_name=display_name or email.split("@", 1)[0][:64],
                role=UserRole.admin.value,
                is_active=True,
            )
            db.add(user)
            action = "created"
        else:
            user.role = UserRole.admin.value
            user.is_active = True
            user.password_hash = hash_password(password)
            if display_name:
                user.display_name = display_name
            action = "updated"
        await db.commit()
        print(f"admin {email} {action}")
    await reset_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="创建或提升管理员账号")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name")
    parser.add_argument("--password", help="不传则交互输入，避免留在命令历史里")
    args = parser.parse_args()

    if args.display_name is not None and not (1 <= len(args.display_name.strip()) <= 64):
        parser.error("--display-name 长度必须为 1–64")

    password = args.password or getpass.getpass("Password: ")
    if not (8 <= len(password) <= 128):
        parser.error("密码长度必须为 8–128")

    asyncio.run(run(args.email, password, args.display_name))


if __name__ == "__main__":
    main()
