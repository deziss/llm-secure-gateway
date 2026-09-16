"""Cross-provider fallback chain and model alias resolution.

Resolves virtual model aliases to real backend+model pairs, and provides
cross-provider failover when a target returns 429/5xx.
"""

import logging
from typing import Optional, Tuple, List, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..models import ModelAlias, FallbackChain

logger = logging.getLogger(__name__)


async def resolve_alias(
    session: AsyncSession, model_name: str
) -> Tuple[Optional[str], str, Optional[int]]:
    """Resolve a model alias to (backend_name, real_model, fallback_chain_id).

    Returns (None, model_name, None) if no alias is found (pass-through).
    """
    result = await session.execute(
        select(ModelAlias).where(ModelAlias.alias == model_name)
    )
    alias = result.scalars().first()
    if alias and hasattr(alias, 'backend_name') and isinstance(alias.backend_name, str):
        logger.info(
            "Alias '%s' resolved -> backend='%s' model='%s' chain_id=%s",
            model_name,
            alias.backend_name,
            alias.model_name,
            alias.fallback_chain_id,
        )
        return alias.backend_name, alias.model_name, alias.fallback_chain_id
    return None, model_name, None


async def get_fallback_chain(
    session: AsyncSession, chain_id: int
) -> Optional[FallbackChain]:
    """Load a fallback chain by ID."""
    result = await session.execute(
        select(FallbackChain).where(FallbackChain.id == chain_id)
    )
    return result.scalars().first()


async def get_chain_targets(
    session: AsyncSession, chain_id: int
) -> List[Dict]:
    """Return the ordered list of targets for a fallback chain."""
    chain = await get_fallback_chain(session, chain_id)
    if chain and chain.targets:
        return chain.targets
    return []


async def list_aliases(session: AsyncSession) -> List[ModelAlias]:
    """Return all model aliases."""
    result = await session.execute(select(ModelAlias))
    return list(result.scalars().all())


async def list_chains(session: AsyncSession) -> List[FallbackChain]:
    """Return all fallback chains."""
    result = await session.execute(select(FallbackChain))
    return list(result.scalars().all())
