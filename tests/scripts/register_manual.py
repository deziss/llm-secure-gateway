import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import sys
import os

# Add src to path
sys.path.append("/app/src")

from llm_gateway.models import Owner, OwnerType
from llm_gateway.services import OwnerService

async def main():
    database_url = os.getenv("DATABASE_URL")
    print(f"Connecting to {database_url}")
    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        service = OwnerService()
        try:
            print("Attempting to create owner...")
            owner = await service.create_owner(
                session,
                id="test-register-id-manual",
                name="Test Register Manual",
                email="test@example.com",
                type=OwnerType.USER,
                description="Test description",
                max_keys=3,
                is_active=True,
                block_endpoints=True
            )
            await session.commit()
            print(f"Success: {owner.id}")
        except Exception as e:
            await session.rollback()
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
