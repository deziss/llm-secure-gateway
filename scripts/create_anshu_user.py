import asyncio
import uuid
from sqlmodel import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from llm_gateway.auth.models import User, Role
from llm_gateway.auth.manager import UserManager
from llm_gateway.auth.db import SQLAlchemyUserDatabase
from llm_gateway.models import LLMBackend, BackendType, Owner, OwnerType, OwnerPermission, APIKey, APIKeyCreate
from llm_gateway.services.auth_service import AuthService

async def main():
    engine = create_async_engine('postgresql+asyncpg://gateway:password@postgres:5432/gateway_db')
    async with AsyncSession(engine, expire_on_commit=False) as session:
        email = 'kushawahaanshu8858@gmail.com'
        password = 'Anshu#8858'

        user_db = SQLAlchemyUserDatabase(session, User)
        mgr = UserManager(user_db)
        hashed_password = mgr.password_helper.hash(password)

        # 1. User
        res = await session.execute(select(User).where(User.email == email))
        user = res.scalars().first()
        if not user:
            user = User(
                id=uuid.uuid4(),
                email=email,
                hashed_password=hashed_password,
                is_active=True,
                is_superuser=True,
                is_verified=True,
                role=Role.ADMIN
            )
            session.add(user)
            await session.commit()
            print(f"[SUCCESS] User created: {user.email} (ID: {user.id})")
        else:
            user.hashed_password = hashed_password
            user.is_active = True
            user.is_superuser = True
            user.is_verified = True
            user.role = Role.ADMIN
            session.add(user)
            await session.commit()
            print(f"[SUCCESS] User updated: {user.email} (ID: {user.id})")

        user_id_str = str(user.id)

        # 2. vLLM Backend
        backend_name = 'vllm-42'
        vllm_models = [
            'llama-3.1-8b',
            'qwen-2.5-14b',
            'llama-3.3-70b',
            'llama-4-scout',
            'llava-1.6-34b',
            'nemotron-3-nano-30b-a3b',
            'bge-m3',
            'qwen-3-30b-a3b'
        ]
        b_res = await session.execute(select(LLMBackend).where(LLMBackend.name == backend_name))
        backend = b_res.scalars().first()
        if not backend:
            backend = LLMBackend(
                name=backend_name,
                base_url='http://10.10.110.42:13313',
                backend_type=BackendType.VLLM,
                models=vllm_models,
                allowed_endpoints=['/v1/chat/completions', '/v1/completions', '/v1/embeddings', '/v1/models']
            )
            session.add(backend)
            await session.commit()
            print(f"[SUCCESS] Backend {backend_name} created with models: {vllm_models}")
        else:
            backend.base_url = 'http://10.10.110.42:13313'
            backend.backend_type = BackendType.VLLM
            backend.models = vllm_models
            backend.allowed_endpoints = ['/v1/chat/completions', '/v1/completions', '/v1/embeddings', '/v1/models']
            session.add(backend)
            await session.commit()
            print(f"[SUCCESS] Backend {backend_name} updated with {len(vllm_models)} models")

        # 3. Owner
        owner_id = 'anshu'
        o_res = await session.execute(select(Owner).where(Owner.id == owner_id))
        owner = o_res.scalars().first()
        if not owner:
            owner = Owner(
                id=owner_id,
                type=OwnerType.USER,
                name='Anshu Kushwaha',
                email=email,
                user_id=user_id_str,
                is_active=True
            )
            session.add(owner)
            await session.commit()
            print(f"[SUCCESS] Owner {owner_id} created")
        else:
            owner.name = 'Anshu Kushwaha'
            owner.email = email
            owner.user_id = user_id_str
            owner.is_active = True
            session.add(owner)
            await session.commit()
            print(f"[SUCCESS] Owner {owner_id} updated")

        # 4. Owner Permission for vllm-42
        p_res = await session.execute(
            select(OwnerPermission).where(
                OwnerPermission.owner_id == owner_id,
                OwnerPermission.backend_name == backend_name
            )
        )
        perm = p_res.scalars().first()
        if not perm:
            perm = OwnerPermission(
                owner_id=owner_id,
                backend_name=backend_name,
                allowed_models=['*'],
                allowed_endpoints=['*']
            )
            session.add(perm)
            await session.commit()
            print(f"[SUCCESS] Permission granted: {owner_id} -> {backend_name}")
        else:
            perm.allowed_models = ['*']
            perm.allowed_endpoints = ['*']
            session.add(perm)
            await session.commit()
            print(f"[SUCCESS] Permission refreshed: {owner_id} -> {backend_name}")

        # Also ensure default-owner has permission to vllm-42
        p_default = await session.execute(
            select(OwnerPermission).where(
                OwnerPermission.owner_id == 'default-owner',
                OwnerPermission.backend_name == backend_name
            )
        )
        perm_def = p_default.scalars().first()
        if not perm_def:
            perm_def = OwnerPermission(
                owner_id='default-owner',
                backend_name=backend_name,
                allowed_models=['*'],
                allowed_endpoints=['*']
            )
            session.add(perm_def)
            await session.commit()

        # 5. API Key for owner anshu
        auth_service = AuthService()
        k_res = await session.execute(select(APIKey).where(APIKey.owner_id == owner_id, APIKey.is_active == True))
        existing_keys = k_res.scalars().all()
        if not existing_keys:
            raw_key, new_key = await auth_service.create_api_key(
                session,
                APIKeyCreate(owner=owner_id, scopes=['*'])
            )
            await session.commit()
            print(f"\n[KEY GENERATED] API Key for {owner_id}: {raw_key}\n")
        else:
            raw_key, new_key = await auth_service.create_api_key(
                session,
                APIKeyCreate(owner=owner_id, scopes=['*'])
            )
            await session.commit()
            print(f"\n[KEY GENERATED] New API Key for {owner_id}: {raw_key}\n")

if __name__ == '__main__':
    asyncio.run(main())
