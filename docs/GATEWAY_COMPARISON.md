# DEZISS Gateway vs. the LLM Gateway Market

How the DEZISS Secure LLM Gateway compares with the gateways teams shortlist most
(Respan, Bifrost, LiteLLM, Agent Router, Vercel AI Gateway, Cloudflare AI
Gateway, OpenRouter, Portkey, TrueFoundry, Kong AI Gateway). Figures for other
vendors are from the vendors' own published pages as of Nov 2026 and change
often — verify against their pricing pages before deciding.

## At a glance

| Gateway | Best for | Token price | Fee or plan cost | Volume discounts | Self-host | Built in beyond routing |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DEZISS Secure LLM Gateway** | **Self-hosted teams needing governance + GPU-aware routing on their own hardware** | **Your provider rates (zero markup)** | **Free OSS (AGPL-3.0)** | **Your provider contracts** | **Yes, first-class (Docker/K8s, air-gap friendly)** | **Alerting, agent-run tracing, GPU/node telemetry, budgets, RBAC, audit, policy profiles** |
| Respan | Lower token costs, traced | Up to 55% off list | 0% fee; plans from $0 | Grow with annual commitment | Gateway yes; platform on Enterprise | Tracing, evals, guardrails |
| Bifrost | Fast self-hosted Go gateway | Your provider rates | Free OSS; Enterprise custom | Your provider contracts | Yes (Apache 2.0) | Semantic caching, budgets |
| LiteLLM | Self-hosted Python proxy | Your provider rates | Free OSS; Enterprise custom | Your provider contracts | Yes | Spend tracking, budgets |
| Agent Router | Envoy and Kubernetes teams | Your provider rates | Free OSS; vendors sell hosted | Your provider contracts | Yes (Apache 2.0) | MCP gateway, token limits |
| Vercel AI Gateway | Hosted, no token markup | Provider list price | No markup; paid add-ons | Custom, on committed spend | No | Budgets, ZDR routing |
| Cloudflare AI Gateway | Teams already on Cloudflare | Provider list price | Core free; 5% on credits | None published | No | Caching, rate limiting |
| OpenRouter | Large hosted catalog | Provider list price | 5.5% fee (8% Business) | Fee only (Enterprise) | No | Provider fallback |
| Portkey | Prisma AIRS customers | Your provider rates | Free; Pro $49/mo | On gateway usage, not tokens | OSS gateway; VPC Enterprise | Guardrails, governance |
| TrueFoundry | Private deployment with serving | Pass-through | Free; Pro $25/user/mo | None published | VPC, air-gapped (Enterprise) | Model serving, MCP |
| Kong AI Gateway | Existing Kong users | Your provider rates | $25/mo + $100 per LLM | On control planes, not tokens | Hybrid or managed | API management plugins |

## Speed (where vendors publish)

Side-by-side numbers from the respan-gateway README (their own harness —
treat as indicative; throughput/rps figures are routing-plane only and exclude
per-request auth, policy, and audit planes):

| Gateway | Requests per second | Median added latency | Memory |
| :--- | ---: | ---: | ---: |
| Respan Gateway (open source) | 56,734 | 0.07 ms | 31 MB |
| Bifrost | 18,182 | 0.16 ms | 488 MB |
| Portkey | 4,187 | 0.17 ms | 350 MB |
| LiteLLM (1 worker) | 268 | 4.34 ms | 418 MB |

DEZISS end-to-end overhead on real API paths with the full governance plane
(auth, CSRF policy, rate limiting, budget checks, audit) enabled, measured v0.19.0
(pooled keep-alive HTTP clients, warm caches, gateway in Docker alongside
Postgres + Redis):

| Route | p50 | p95 |
| :--- | ---: | ---: |
| `/health` (near bare ASGI+uvloop) | 3.4 ms | 5.1 ms |
| `/v1/models` (cookie auth + config + rate limit) | 8.3 ms | ~10 ms |

Notes for an honest reading:

- Go-native routers (Respan, Bifrost) win raw fan-out; the DEZISS design targets
  the **sub-10 ms p95 added-to-TTFT** bar, which is what actually matters for
  multi-step agentic workflows over slower LLM backends (50–2000 ms+ per call).
- v0.19.0 removed the two biggest Python-side overheads: the whole middleware
  stack is pure ASGI (no `BaseHTTPMiddleware` body buffering, streaming forwarded
  frame-by-frame), the loop is uvloop, and Redis rate limiting / prompt caching
  are fully async (`redis.asyncio`). LiteLLM-class proxy overhead (multi-ms per
  request in Python) is the number being beaten, not the Go routers' rps records.

## Where DEZISS is ahead

| Capability | DEZISS | Typical market position |
| :--- | :--- | :--- |
| **Alert & incident engine** | Declarative rules on any gateway/GPU/node metric, incident dedupe, ack workflow, webhook (HMAC) + email, UI Alert Center | Missing (TrueFoundry), Prometheus-out only (Kong), external APM (most) |
| **Agent-run correlation** | Automatic run/step stitching from headers or body signals, step waterfalls, SDK (`x-deziss-run-id`) | Rare; most need OTel plumbing you write yourself |
| **GPU / node intelligence** | KV-cache, queue depth, VRAM, temperature, XID errors via node agents; queue-aware routing chooses the *least-busy GPU* | Unique in this list — gateways here are model-plane only |
| **Governance plane** | RBAC, structured denial codes, per-tenant policy profiles (`/admin/policy-profiles`), compliance report endpoint, retention tiers + capture levels (`metadata`/`redacted`/`full`), tamper trail audit | Portkey/Prisma closest; TrueFoundry Enterprise-only |
| **Zero markup, true OSS** | AGPL-3.0, all features in the binary, no seat pricing | Most "OSS" gateways hold guardrails/SSO behind enterprise tiers |
| **Air-gap friendliness** | Single container + Postgres + Redis; license check-in fail-open; no cloud control plane | TrueFoundry air-gap is Enterprise; hosted options (Vercel, CF, OpenRouter) N/A |
| **Cost attribution** | Per-key/project monthly budgets with soft 80% warnings, `X-Budget-Status`, per-model pricing catalog, spend rollups | LiteLLM closest; others partial |
| **Resilience** | Circuit breaker per backend+URL (single half-open probe), multi-URL fallback, model-level fallback chains + aliases, configurable via env | Fallback common; breaker + half-open control less so |
| **Key security** | Backend API keys Fernet-encrypted at rest (`enc:v1:`), masked in APIs, one-call upgrade endpoint | Mostly plaintext-at-rest in OSS tier |

## Where DEZISS lags (honestly)

| Gap | Detail | Status |
| :--- | :--- | :--- |
| Token cost discounts | Respan resells up to 55% off list; DEZISS passes through your own contracts (which is often *cheaper* at scale, but requires those contracts) | By design |
| MCP gateway | Agent Router / TrueFoundry ship MCP registries with OAuth; DEZISS has agent-run correlation but no MCP server registry | Not started |
| Evals / guardrail marketplace | Respan ships evals; NeMo/presidio-style guardrail packs are ecosystem territory | Guardrail hooks exist, packs not bundled |
| Hosted SaaS option | DEZISS is self-host only — no zero-ops tier | By design |
| Managed compliance certifications | SOC 2 / HIPAA attestations are organizational; DEZISS is compliance-*ready* (audit, RBAC, encryption, capture tiers) but holds no certificates | Roadmap |
| Raw routing fan-out | Go routers publish higher rps on router-only benchmarks; DEZISS optimizes added-TTFT p95 instead | By design |
| Guardrails depth | **Input guardrails ship today**: regex injection detection (override/role-hijack/extraction/jailbreak categories), sensitivity low/medium/high, warn/block, enforced on both proxy planes (`ENABLE_GUARDRAILS`, off by default). Output phases (`post_call`, `tool_args`, `tool_output`) are defined but not yet wired into the response path; no inline streaming output inspection; no guardrail packs (NeMo/presidio) bundled | Partially shipped |
| Key rotation automation | Keys support revoke/disable; automatic scheduled rotation is manual (rotate + reissue) | Not started |

## Feature scorecard vs. the usual shortlist

| Capability | DEZISS | LiteLLM | Portkey | TrueFoundry | Kong AI GW | Bifrost |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Open source gateway | ✅ AGPL | ✅ | Partial | ❌ | Hybrid | ✅ Apache 2.0 |
| Sub-10 ms p95 overhead (published/measured) | ✅ measured | ❌ 4.34 ms median, poor at rps | ⚠️ unverified | ⚠️ vendor +3–7 ms | ⚠️ | ✅ |
| Per-key/model/project cost attribution | ✅ | ✅ | ✅ | ✅ | ⚠️ plugins | ⚠️ budgets |
| Budget soft-warnings before block | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ |
| Native alerting/incidents | ✅ | ❌ | ⚠️ Pro | ❌ (Prom export) | ⚠️ monitoring stack | ❌ |
| GPU/node telemetry + aware routing | ✅ | ❌ | ❌ | ⚠️ serving metrics | ❌ | ❌ |
| Agent-run tracing built in | ✅ | ⚠️ | ⚠️ OTel | ⚠️ OTel | ❌ | ❌ |
| Per-tenant policy profiles | ✅ | ⚠️ metadata/budget | ⚠️ config | ⚠️ workspace RBAC | ⚠️ scopes | ❌ |
| Compliance report endpoint | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| Capture-level tiers (metadata/redacted/full) | ✅ | ⚠️ log toggles | ⚠️ | ⚠️ | ❌ | ❌ |
| Backend key encryption at rest | ✅ | ⚠️ vault deps | ⚠️ | ✅ | ✅ secrets mgmt | ❌ |
| Model-level fallback chains | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Semantic caching | ✅ exact + cosine-similarity intent match (`ENABLE_SEMANTIC_CACHE`, `SEMANTIC_CACHE_THRESHOLD`, default 0.85) | ⚠️ | ✅ Pro | ⚠️ | ⚠️ plugins | ✅ |
| MCP support | ❌ | ✅ | ⚠️ | ✅ | ⚠️ | ❌ |
| Air-gap capable | ✅ | ✅ | ⚠️ VPC ent. | ✅ Enterprise | ⚠️ | ✅ |
| Zero markup on tokens | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Archestra (archestra-ai/archestra) — different category, worth the fine print

Archestra is not a gateway; it is an **enterprise AI platform** (TypeScript,
~4.5k stars, AGPL core + open-core enterprise license, $13.5M funded, CNCF
member) that *includes* an LLM gateway as one of ~12 products: internal Chat UI
with Slack/Teams/email front-ends, a developer portal (one token for Claude
Code/Codex/Cursor), MCP gateway with OAuth on-behalf-of, private MCP registry +
K8s orchestrator, A2A gateway, a hosted **agent runtime** (scheduled/webhook
triggers, sub-agents, sandboxed code execution), RAG knowledge base, mini-app
builder, deterministic guardrails ("Lethal Trifecta" data-leak prevention), SSO/
RBAC, per-environment egress + cost policies.

Key numbers (their published pages, Nov 2026): 1 worker ≈ 279–287 rps vs
LiteLLM's 86–87 (same hardware); **median added latency 169 ms / p95 258 ms at
50 concurrent requests** because it awaits database log writes in the request
path (their disclosed methodology) — their README's "31 ms at p95" is a
different, lighter scenario. Free for ≤30 users on the enterprise license;
RBAC/SSO/retention/encryption-at-rest/guardrails are **Enterprise tier**, not
the AGPL core.

| Dimension | DEZISS | Archestra |
| :--- | :--- | :--- |
| Scope | Gateway only — inference data plane | Whole AI platform: chat, agents, MCP, RAG, guardrails + gateway |
| Agent story | **Observes** agent traffic (run/step correlation, waterfalls) — agents run on your infra | **Runs** agents for you (K8s runtime, sandboxed execution, triggers) |
| MCP | Not started | Registry + gateway + OAuth OBO + K8s orchestrator (their core strength) |
| GPU/node awareness | ✅ KV-cache/queue/VRAM/XID telemetry, queue-aware routing | ❌ model-plane only |
| Native alerting/incidents | ✅ rule engine + ack + notification | ⚠️ OTel/Prometheus export (alert in your stack) |
| Added latency | ~**10 ms p95** governance plane on (low concurrency) | 169–258 ms median/p95 **at 50 concurrency** (awaits DB log writes) |
| RBAC/SSO | Local RBAC + API keys + JWT sessions (SSO not built in) | SSO/SAML/OIDC, team sync, resource ACLs — **Enterprise tier** |
| Guardrails | Input guardrails ship today (injection regex, warn/block); output-phase enforcement not wired yet; packs not bundled | Deterministic policy-as-code engine — **Enterprise tier** |
| Encryption at rest, retention, audit | ✅ in the free AGPL binary | Enterprise tier |
| Deployment | One container + Postgres + Redis; air-gap friendly | Docker/Helm/K8s multi-service; mounts docker.sock for sandboxes |
| License economics | AGPL-3.0, every feature in the binary | Open core; 30-user free enterprise; quote beyond |

**Rule of thumb:** adopting Archestra means migrating your teams onto *their*
platform (chat, portal, MCP, agents — the gateway is the entry point). Adopting
DEZISS means putting a governance + routing + GPU-aware gate in front of models
you already run, with nothing to migrate your workflows into.

## Bottom line

- **Pick DEZISS** when you self-host LLMs (vLLM/Ollama on your own GPUs), need the
  governance plane (audit, budgets, per-tenant policy, compliance exports) and
  infrastructure awareness (KV-cache/queue/GPU routing + alerting) in one AGPL
  binary with measured sub-10 ms p95 overhead — and you don't want seat pricing or
  a cloud control plane.
- **Pick Respan/OpenRouter** when you want discounted hosted tokens rather than
  your own provider contracts.
- **Pick LiteLLM/Bifrost** for the widest OSS Python/Go defaults when you don't
  need governance/alerting/GPU features and will bolt on the rest yourself.
- **Pick TrueFoundry/Kong** when you're already buying that platform (model
  serving with GPUs, or Kong API management estate) and want the gateway bundled.
