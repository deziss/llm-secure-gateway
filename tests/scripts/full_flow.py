"""Full end-to-end test: owner registration + API key creation."""
import asyncio
import os
import sys

sys.path.append("/app/src")

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from llm_gateway.models import Owner, OwnerType, APIKeyCreate
from llm_gateway.services import OwnerService, AuthService


async def main():
    database_url = os.getenv("DATABASE_URL")
    print(f"DB: {database_url}")
    engine = create_async_engine(database_url)
    S = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with S() as session:
        # 1. Owner registration
        osvc = OwnerService()
        try:
            owner = await osvc.create_owner(
                session,
                id="e2e-test-owner",
                name="E2E Test Owner",
                email="e2e@test.com",
                type=OwnerType.USER,
                description="End-to-end verification",
                max_keys=2,
                is_active=True,
            )
            await session.commit()
            print(f"[PASS] Owner created: id={owner.id} desc={owner.description} max_keys={owner.max_keys}")
        except ValueError:
            print("[SKIP] Owner already exists (re-run)")

        # 2. API key creation
        asvc = AuthService()
        raw, key = await asvc.create_api_key(
            session, APIKeyCreate(owner="e2e-test-owner", scopes=["chat"])
        )
        await session.commit()
        print(f"[PASS] API key created: prefix={key.prefix}")

        # 3. Second key
        raw2, key2 = await asvc.create_api_key(
            session, APIKeyCreate(owner="e2e-test-owner", scopes=["chat"])
        )
        await session.commit()
        print(f"[PASS] 2nd API key created: prefix={key2.prefix}")

        print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
