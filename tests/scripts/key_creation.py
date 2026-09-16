import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import sys
import os

# Add src to path
sys.path.append("/app/src")

from llm_gateway.models import APIKeyCreate
from llm_gateway.services import AuthService

async def main():
    database_url = os.getenv("DATABASE_URL")
    print(f"Connecting to {database_url}")
    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        service = AuthService()
        try:
            print("Attempting to create API key for owner 'proj1-vapi'...")
            # We assume 'proj1-vapi' exists from previous tests or default data
            data = APIKeyCreate(owner="proj1-vapi", scopes=["chat"])
            raw_key, api_key = await service.create_api_key(session, data)
            
            # Simulate router commit
            await session.commit()
            print(f"Success! Raw key: {raw_key}")
            print(f"Prefix: {api_key.prefix}")
        except Exception as e:
            await session.rollback()
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
