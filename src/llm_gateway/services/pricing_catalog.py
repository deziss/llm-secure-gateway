"""Provider Pricing Catalog and Registry.

Standard benchmark token rates (USD per 1,000,000 tokens) for known cloud and
self-hosted LLM providers.
"""

from typing import Dict, Optional, List
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Standard pricing registry in USD per 1,000,000 (1M) tokens.
# To convert to per-1K: rate_per_1k = rate_per_1m / 1000.0
# To convert from per-1K: rate_per_1m = rate_per_1k * 1000.0
PROVIDER_PRICING_CATALOG: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {
    "openai": {
        "gpt-4o": {
            "input_cost_per_1m": 2.50,
            "output_cost_per_1m": 10.00,
            "cached_input_cost_per_1m": 1.25,
            "description": "OpenAI Flagship multimodal model",
        },
        "gpt-4o-mini": {
            "input_cost_per_1m": 0.15,
            "output_cost_per_1m": 0.60,
            "cached_input_cost_per_1m": 0.075,
            "description": "OpenAI Fast, affordable small model",
        },
        "gpt-4.1": {
            "input_cost_per_1m": 2.00,
            "output_cost_per_1m": 8.00,
            "cached_input_cost_per_1m": 1.00,
            "description": "OpenAI GPT-4.1 general purpose",
        },
        "gpt-4.1-mini": {
            "input_cost_per_1m": 0.40,
            "output_cost_per_1m": 1.60,
            "cached_input_cost_per_1m": 0.20,
            "description": "OpenAI GPT-4.1 Mini",
        },
        "gpt-4.1-nano": {
            "input_cost_per_1m": 0.10,
            "output_cost_per_1m": 0.40,
            "cached_input_cost_per_1m": 0.05,
            "description": "OpenAI Ultra-lightweight Nano model",
        },
        "gpt-4-turbo": {
            "input_cost_per_1m": 10.00,
            "output_cost_per_1m": 30.00,
            "cached_input_cost_per_1m": None,
            "description": "OpenAI GPT-4 Turbo legacy",
        },
        "gpt-3.5-turbo": {
            "input_cost_per_1m": 0.50,
            "output_cost_per_1m": 1.50,
            "cached_input_cost_per_1m": None,
            "description": "OpenAI GPT-3.5 Turbo legacy",
        },
        "o1": {
            "input_cost_per_1m": 15.00,
            "output_cost_per_1m": 60.00,
            "cached_input_cost_per_1m": 7.50,
            "description": "OpenAI Reasoning flagship model",
        },
        "o1-mini": {
            "input_cost_per_1m": 1.10,
            "output_cost_per_1m": 4.40,
            "cached_input_cost_per_1m": 0.55,
            "description": "OpenAI Fast reasoning model",
        },
        "o3": {
            "input_cost_per_1m": 10.00,
            "output_cost_per_1m": 40.00,
            "cached_input_cost_per_1m": 5.00,
            "description": "OpenAI O3 reasoning model",
        },
        "o3-mini": {
            "input_cost_per_1m": 1.10,
            "output_cost_per_1m": 4.40,
            "cached_input_cost_per_1m": 0.55,
            "description": "OpenAI O3 Mini reasoning model",
        },
        "o4-mini": {
            "input_cost_per_1m": 1.10,
            "output_cost_per_1m": 4.40,
            "cached_input_cost_per_1m": 0.55,
            "description": "OpenAI O4 Mini reasoning model",
        },
        "text-embedding-3-small": {
            "input_cost_per_1m": 0.02,
            "output_cost_per_1m": 0.00,
            "cached_input_cost_per_1m": None,
            "description": "OpenAI Text Embeddings Small",
        },
        "text-embedding-3-large": {
            "input_cost_per_1m": 0.13,
            "output_cost_per_1m": 0.00,
            "cached_input_cost_per_1m": None,
            "description": "OpenAI Text Embeddings Large",
        },
    },
    "anthropic": {
        "claude-3-5-sonnet-20241022": {
            "input_cost_per_1m": 3.00,
            "output_cost_per_1m": 15.00,
            "cached_input_cost_per_1m": 0.30,
            "description": "Anthropic Claude 3.5 Sonnet (v2)",
        },
        "claude-3-5-haiku-20241022": {
            "input_cost_per_1m": 0.80,
            "output_cost_per_1m": 4.00,
            "cached_input_cost_per_1m": 0.08,
            "description": "Anthropic Claude 3.5 Haiku",
        },
        "claude-3-opus-20240229": {
            "input_cost_per_1m": 15.00,
            "output_cost_per_1m": 75.00,
            "cached_input_cost_per_1m": 1.50,
            "description": "Anthropic Claude 3 Opus",
        },
        "claude-sonnet-4-20250514": {
            "input_cost_per_1m": 3.00,
            "output_cost_per_1m": 15.00,
            "cached_input_cost_per_1m": 0.30,
            "description": "Anthropic Claude Sonnet 4 preview",
        },
        "claude-3-haiku-20240307": {
            "input_cost_per_1m": 0.25,
            "output_cost_per_1m": 1.25,
            "cached_input_cost_per_1m": 0.03,
            "description": "Anthropic Claude 3 Haiku",
        },
    },
    "gemini": {
        "gemini-2.5-pro": {
            "input_cost_per_1m": 1.25,
            "output_cost_per_1m": 10.00,
            "cached_input_cost_per_1m": 0.3125,
            "description": "Google Gemini 2.5 Pro",
        },
        "gemini-2.5-flash": {
            "input_cost_per_1m": 0.15,
            "output_cost_per_1m": 0.60,
            "cached_input_cost_per_1m": 0.0375,
            "description": "Google Gemini 2.5 Flash",
        },
        "gemini-2.0-flash": {
            "input_cost_per_1m": 0.10,
            "output_cost_per_1m": 0.40,
            "cached_input_cost_per_1m": 0.025,
            "description": "Google Gemini 2.0 Flash",
        },
        "gemini-1.5-pro": {
            "input_cost_per_1m": 1.25,
            "output_cost_per_1m": 5.00,
            "cached_input_cost_per_1m": 0.3125,
            "description": "Google Gemini 1.5 Pro",
        },
        "gemini-1.5-flash": {
            "input_cost_per_1m": 0.075,
            "output_cost_per_1m": 0.30,
            "cached_input_cost_per_1m": 0.01875,
            "description": "Google Gemini 1.5 Flash",
        },
    },
    "google": {
        "gemini-2.5-pro": {
            "input_cost_per_1m": 1.25,
            "output_cost_per_1m": 10.00,
            "cached_input_cost_per_1m": 0.3125,
            "description": "Google Gemini 2.5 Pro",
        },
        "gemini-2.5-flash": {
            "input_cost_per_1m": 0.15,
            "output_cost_per_1m": 0.60,
            "cached_input_cost_per_1m": 0.0375,
            "description": "Google Gemini 2.5 Flash",
        },
        "gemini-2.0-flash": {
            "input_cost_per_1m": 0.10,
            "output_cost_per_1m": 0.40,
            "cached_input_cost_per_1m": 0.025,
            "description": "Google Gemini 2.0 Flash",
        },
    },
    "deepseek": {
        "deepseek-chat": {
            "input_cost_per_1m": 0.14,
            "output_cost_per_1m": 0.28,
            "cached_input_cost_per_1m": 0.014,
            "description": "DeepSeek-V3 general model",
        },
        "deepseek-reasoner": {
            "input_cost_per_1m": 0.55,
            "output_cost_per_1m": 2.19,
            "cached_input_cost_per_1m": 0.14,
            "description": "DeepSeek-R1 reasoning model",
        },
        "deepseek-coder": {
            "input_cost_per_1m": 0.14,
            "output_cost_per_1m": 0.28,
            "cached_input_cost_per_1m": 0.014,
            "description": "DeepSeek Coder model",
        },
    },
    "groq": {
        "llama-3.3-70b-versatile": {
            "input_cost_per_1m": 0.59,
            "output_cost_per_1m": 0.79,
            "cached_input_cost_per_1m": None,
            "description": "Groq LLaMA 3.3 70B",
        },
        "llama-3.1-8b-instant": {
            "input_cost_per_1m": 0.05,
            "output_cost_per_1m": 0.08,
            "cached_input_cost_per_1m": None,
            "description": "Groq LLaMA 3.1 8B Instant",
        },
        "mixtral-8x7b-32768": {
            "input_cost_per_1m": 0.24,
            "output_cost_per_1m": 0.24,
            "cached_input_cost_per_1m": None,
            "description": "Groq Mixtral 8x7B",
        },
    },
    "mistral": {
        "mistral-large-latest": {
            "input_cost_per_1m": 2.00,
            "output_cost_per_1m": 6.00,
            "cached_input_cost_per_1m": None,
            "description": "Mistral Large Latest",
        },
        "mistral-small-latest": {
            "input_cost_per_1m": 0.20,
            "output_cost_per_1m": 0.60,
            "cached_input_cost_per_1m": None,
            "description": "Mistral Small Latest",
        },
        "codestral-latest": {
            "input_cost_per_1m": 0.30,
            "output_cost_per_1m": 0.90,
            "cached_input_cost_per_1m": None,
            "description": "Mistral Codestral Latest",
        },
    },
    "ollama": {
        "*": {
            "input_cost_per_1m": 0.00,
            "output_cost_per_1m": 0.00,
            "cached_input_cost_per_1m": 0.00,
            "description": "Local Ollama host (self-hosted, free)",
        }
    },
    "vllm": {
        "*": {
            "input_cost_per_1m": 0.00,
            "output_cost_per_1m": 0.00,
            "cached_input_cost_per_1m": 0.00,
            "description": "Local vLLM host (self-hosted, free)",
        }
    },
    "llamacpp": {
        "*": {
            "input_cost_per_1m": 0.00,
            "output_cost_per_1m": 0.00,
            "cached_input_cost_per_1m": 0.00,
            "description": "Local llama.cpp host (self-hosted, free)",
        }
    },
}


def lookup_catalog_rate(model: str, provider: str) -> Optional[Dict]:
    """Look up default benchmark pricing from catalog."""
    p_clean = (provider or "").lower().strip()
    # Normalize aliases
    if p_clean == "google":
        p_clean = "gemini"

    catalog = PROVIDER_PRICING_CATALOG.get(p_clean, {})
    if model in catalog:
        return catalog[model]

    # Check wildcard for local providers
    if "*" in catalog:
        return catalog["*"]

    # Fallback to searching all providers for exact model match
    for prov, models in PROVIDER_PRICING_CATALOG.items():
        if model in models:
            return models[model]

    return None


async def auto_seed_pricing_for_provider(session: AsyncSession, provider: str) -> List:
    """Automatically detect provider and seed its catalog pricing into ModelPricing.
    
    Triggered when an owner registers a provider key or an admin configures a backend.
    Preserves manual user overrides (source='manual').
    """
    from ..models import ModelPricing

    p_clean = (provider or "").lower().strip()
    if p_clean == "google":
        p_clean = "gemini"

    catalog = PROVIDER_PRICING_CATALOG.get(p_clean)
    if not catalog:
        logger.info("No standard pricing catalog found for provider: %s", provider)
        return []

    # Query existing entries for this provider
    res = await session.execute(
        select(ModelPricing).where(ModelPricing.provider == p_clean)
    )
    existing_map = {row.model: row for row in res.scalars().all()}

    seeded = []
    for model_name, rate_data in catalog.items():
        if model_name in existing_map:
            existing = existing_map[model_name]
            # If it's a catalog entry (not manual override), keep it fresh
            if existing.source == "provider_catalog":
                existing.input_cost_per_1m = rate_data["input_cost_per_1m"]
                existing.output_cost_per_1m = rate_data["output_cost_per_1m"]
                existing.cached_input_cost_per_1m = rate_data.get("cached_input_cost_per_1m")
                existing.description = rate_data.get("description")
                session.add(existing)
                seeded.append(existing)
            # If manual, leave untouched!
            continue

        new_entry = ModelPricing(
            model=model_name,
            provider=p_clean,
            input_cost_per_1m=rate_data["input_cost_per_1m"],
            output_cost_per_1m=rate_data["output_cost_per_1m"],
            cached_input_cost_per_1m=rate_data.get("cached_input_cost_per_1m"),
            source="provider_catalog",
            description=rate_data.get("description"),
        )
        session.add(new_entry)
        seeded.append(new_entry)

    if seeded:
        await session.commit()
        logger.info("Auto-seeded %d model rates for provider: %s", len(seeded), p_clean)

    return seeded
