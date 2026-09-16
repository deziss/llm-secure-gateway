import logging
import typer
import asyncio
from .database import engine
from .auth.models import User
from .auth.schemas import UserCreate
from .auth.manager import UserManager
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

app = typer.Typer()

async def create_superuser_async(email: str, password: str) -> None:
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user_manager = UserManager(user_db)
        
        try:
            user = await user_manager.create(
                UserCreate(
                    email=email,
                    password=password,
                    is_superuser=True,
                    is_active=True,
                    is_verified=True
                )
            )
            logger.info("Superuser created: %s", user.email)
        except Exception as e:
            logger.error("Error creating superuser: %s", e)

@app.command()
def create_superuser(
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True)
) -> None:
    """
    Create a new superuser with full admin privileges.
    """
    asyncio.run(create_superuser_async(email, password))

if __name__ == "__main__":
    app()
