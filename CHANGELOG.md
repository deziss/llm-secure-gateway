## [Unreleased]

### 🔥 Phoenix color scheme

- Every design token now resolves to the Phoenix palette: a warm parchment canvas (`#FCFAF8`) with white panels and warm-charcoal ink in light mode, and a low-lightness warm charcoal (`#0D0B0A`) world in dark mode. Brand accent is amber (`#D97706` light / `#F59E0B` dark) with an ember-red secondary (`#DC2626` / `#EF4444`); `--phoenix-gradient` carries the amber→ember ramp.
- Status colours are semantic, not decorative: `ok`, `warn` and `down` tokens replace the ad-hoc emerald / amber / rose classes, each with an `*-ink` counterpart so text on a solid status colour keeps its contrast in both themes.
- Roughly 470 hard-coded Tailwind hue classes across 21 templates and 12 scripts were migrated to the semantic tokens (`text-ink-muted` instead of `text-slate-500 dark:text-slate-300`, and so on), which also removed almost every remaining per-element `dark:` pair.
- Charts, sparklines and canvas drawings draw from a warm ramp (`#D97706`, `#E8590C`, `#DC2626`, `#EAB308`, …) instead of the old blue/purple/pink set; categorical palettes that encode identity (embedding scatter points) stay multi-hue on purpose.
- Selection, caret and scrollbar colours now follow the accent, `meta[name=theme-color]` tracks the in-app theme switch, and markdown code blocks use a warm-dark chrome that matches the palette rather than GitHub's blue-black.

### 🧹 Dashboard badges and Alert Center toolbar

- Removed the always-on "Cluster Online" and uptime pills from the Operations Dashboard header; the timeframe control and "Updated …" timestamp remain. The "LIVE" indicator on the Connected Clients card is unchanged.
- Alert Center: Firing / Resolved 24h / Enabled rules are now compact chips on the toolbar row (left of Severity and Refresh) instead of three large cards; Refresh and Severity sit on the right and share one row with the stats.
- Live Traces and Agent Intelligence Refresh buttons dropped their remaining 44px minimum so they match the inputs beside them.

### 🎨 Action columns and table tooltips

- Pricing table actions now use the same edit / trash icon buttons as servers, users, projects, aliases and bots; tooltips (`title`) and screen-reader labels name the action and the model.
- GPU host and node lists in Observability use the same icon buttons instead of "Edit" / "Delete" text.
- Cells that can truncate (per-models Server/Model, GPU name, container name/id, spend model and owner) now show their full text in a native tooltip.
- Clear buttons on the Models, Groups and Live Traces toolbars match the height of the inputs beside them.

### 🐛 Pricing page CSRF fix

- Catalog sync, price upsert and price delete now send the `X-CSRF-Token` header (via `fetchWithCsrf`); previously every click returned 403 "Failed to sync catalog." for cookie-authenticated admins.
- Sync failures now surface the server's error detail instead of a generic message.

### 🎛 Observability & settings polish

- Toolbar controls (timeframe switch, Clear, Refresh) dropped from a 44px minimum to 36px, matching the height of the inputs beside them.
- The per-models table now pins Server and Model columns while the metric columns scroll horizontally; columns have fixed widths so the pinned cells align.
- Feature Laboratory toggles render three per row on wide screens (one on mobile, two on tablet).

### 🐛 Dashboard edge table

- Edge Connectivity rows now show the origin IP: `dashboard.js` filled only the "Last Activity" cell, leaving the "Origin IP" cell permanently blank. The empty-state message also targets a dedicated element instead of the cell's last child (the loading spinner).

### 🧪 Test and async correctness

- Fixed key rotation to await SQLAlchemy's async refresh calls.
- Removed legacy pytest event-loop fixtures, fixed mock AsyncSession result types, and resolved duplicate proxy operation IDs and test-only deprecation/security warnings.
- The development Compose service now starts Uvicorn explicitly while retaining the CI image's test tooling.

### 🖥 GPU agent in Compose

- Development and production Compose files include an optional `llm-gpu-agent` service (build context `./agent`, NVIDIA GPU reservations, `pid: host`) gated behind `--profile gpu`, mirroring the standalone `agent/docker-compose.yml`.

### 🧰 Redis

- Updated development, production, and deployment Compose files to the pinned `redis:8.12.0-alpine` image.
- Production gateway services explicitly build the Dockerfile's `runtime` target so Compose starts Uvicorn rather than the CI test command.
- Documented the safe dev-to-production Compose switch: remove the old containers and network without deleting named database volumes.

## [0.21.0] - 2026-11-28

### 📊 Operations dashboard

Refreshing the dashboard no longer blanks and re-loads everything. The page now paints immediately from a **versioned stale-while-revalidate cache in localStorage** and refreshes quietly in the background.

- **New `static/js/dash-cache.js`** (no dependencies): localStorage SWR cache keyed by URL with an index + byte-budget LRU eviction, per-user tagging (cache cleared on logout/401), ETag `If-None-Match` revalidation (server `304`s bump freshness without re-downloading), single-flight request de-duplication, cross-tab invalidation over `BroadcastChannel`, one visibility/offline-aware scheduler with full-jitter backoff for every poller, prefs store (`llmgw:prefs:v1`, kept separate from the data namespace), count-up number animation (first paint stays instant; respects `prefers-reduced-motion` and the per-user animations toggle), and delayed skeletons (~250 ms — fast loads never flash them).
- **Customise menu in the header**: show/hide and reorder every dashboard widget, Comfortable/Compact density, and chart/animation toggle — all persisted per browser and resettable. A segmented **time-range control (1h / 24h / 7d / 30d)** now scopes the summary and telemetry timeline, and a **Live cadence select (Off / 30s / 1m / 5m)** is the pause control required by WCAG 2.2.2 for auto-updating content.
- **Three new above-the-fold KPIs from a new backend `headline` block**: requests in the selected range (with % delta vs the previous equal window), error rate (with error/request counts, red at ≥5%), and p90 time-to-first-token (with ~p50/avg footnote). A gateway uptime chip and an "Updated HH:MM:SS / Showing saved data · updates failing" freshness label sit in the header.
- **Backend (`routers/dashboard.py`)**: `?range=` on `/admin/dashboard/summary` and `/traffic-24h`, bucketing that scales 1m/1h/1d instead of emitting hundreds of buckets for long ranges, TTFT p50/p90 roll-ups, and `ETag` + `Cache-Control: private, max-age=10, stale-while-revalidate=45` with real 304 responses. New `/admin/metrics/live` serves the SSE payload without backend probes for poll-mode clients.
- **Robustness & a11y**: the edge-IP table updates rows keyed by IP instead of re-rendering via `innerHTML` (no XSS surface, no focus/scroll loss), charts update with `update('none')` on background ticks (never destroyed/recreated), theme switches rebuild chart colours from tokens, exactly one `aria-live` region announces load completion (per-tile regions were removed), live SSE/poll traffic pauses with tab visibility *and* the user's pause preference, and the SSE fallback to polling now recovers automatically.
- Tests: `tests/test_dashboard_summary.py` covers the headline block, uptime, range bucketing and traffic shape (87 dashboard/offline-asset tests pass; full suite 925 passed).
- **Observability space cleanup**: the two full-width "Live / Average Tokens per sec (all models)" stat cards were removed and merged into compact inline chips (**Live … tok/s**, **Avg … tok/s**) that now sit beside the Timeframe selector row on the Models tab — one row of vertical space reclaimed, tooltips retained for context.
- **Observability GPU tab layout & popups**: each GPU host card is now two side-by-side boxes (GPU table | combined per-process table) with capped height, independent scrolling and sticky headers — the old stack of per-GPU `<details>` sections is gone. The "GPU hosts" form and the "Link a model server to GPUs" form moved out of the page into **Add server / Add link popup dialogs** built on the shared `AdminModal` (focus trap, Escape, click-outside close, scroll lock); Edit still opens the same popup prefilled.
- **Integrations cards responsive**: the tool-card grid now shows 3 cards per row by default (2 on tablet, 1 on mobile), and the card you open (Configure / How to connect) spans the full row so its form and instructions are never squeezed into a single column.

### 🪪 Licencia diagnostics

"Upgrade Failed — Could not validate the new license key" previously blamed the licensing server even when the gateway never contacted it. Investigation of a failed upgrade showed the real cause was a missing `LICENCIA_API_KEY` on the gateway (the provider was up and answering — not a `licencia.deziss.com` outage).

- `LicenseState.last_error` records **why** a validation/upgrade failed (missing API key → "the server was NOT contacted"; HTTP 4xx → key/credential rejected; HTTP 5xx / unreachable → server/network problem) and the `/admin/license/upgrade` 400 now leads with that specific reason. Gateway logs warn, not just info, when licensing is unconfigured.
- The "Hardware fingerprint for activation" shown in the Upgrade dialog was the literal string `localhost` in Community mode — operators could activate the wrong seat with it. It is now the real SHA-256 fingerprint (`hostname:MAC`) whenever no license is active.
- Default `LICENCIA_URL` for an unset env var changed from the LAN dev box `http://172.20.0.11:3000` to the production `https://licencia.deziss.com`; `.env.example` documents all four `LICENCIA_*` variables. (Changes take effect on next container restart.)
- Tests: 3 new regression tests in `tests/test_licencia_client.py` (fail-fast without API key proves no HTTP call is made; real fingerprint in Community mode; rejection reason recorded) — 37 licensing/settings tests pass.

### 🐳 Smaller deployment image

The published image is now built by a multi-stage, [uv](https://docs.astral.sh/uv/)-based `Dockerfile` (`uv` -> `base` -> `builder` -> `test-builder` -> `runtime` -> `ci`) and is **312 MB** instead of 500 MB, with a full `--no-cache` cold build at ~10 s versus 45.7 s previously.

- **`arize-phoenix-evals` moved to a new optional `[evals]` extra.** It was the only reason `pandas` (83 MB) and `numpy` (43 MB + 23 MB of vendored libs) were in production: nothing in `src/`, `tests/` or `sdk/` imports `pandas`, `numpy` or `arize_phoenix_evals`. If you ran LLM-as-judge evals from the gateway container, install `llm-secure-gateway[evals]` (or `uv pip install arize-phoenix-evals`) — the base install no longer pulls them.
- **`requirements.base.txt` / `requirements.test.txt` are committed uv locks** (`uv pip compile`, generated *inside* `python:3.14.7-slim` — see each file header). Production installs `--no-deps -r requirements.base.txt`, CI layers `requirements.test.txt` on top, so CI exercises the exact production dependency set plus pytest instead of a looser `[test]` resolution.
- **`pip`, `setuptools` and `wheel` are gone** from the image (uv installs straight into `/opt/venv`), as are `tests/` and `sdk/` — both live only in the `ci` stage. Bytecode is precompiled (`UV_COMPILE_BYTECODE=1`) for faster startup.
- **`grpcio` is deliberately retained** (19 MB): `phoenix/otel/otel.py` imports the OTLP *gRPC* exporter at module scope, so `--no-emit-package grpcio` breaks `import phoenix.otel` and therefore `llm_gateway.main`, even though the gateway exports over HTTP.
- **`.dockerignore` written from scratch** (it was 14 bytes). The build context no longer carries `.git`, `node_modules`, `.env*`, browser/agent state, `docs/`, `grafana/`, `otel/` or compose files — dropping it from ~26 MB to ~12 MB and, more importantly, keeping `.env` and `.git` out of any image layer.
- **The image `CMD` no longer bakes in `--loop uvloop`** — uvicorn is started plain so `UVICORN_LOOP`/`UVICORN_WORKERS` actually work (uvicorn's click CLI uses `auto_envvar_prefix=UVICORN`, so an explicit flag silently overrides them). uvloop is still the loop you get: `--loop auto` picks it whenever it is installed. See [UPGRADE.md](./UPGRADE.md).
- **Image `HEALTHCHECK` is stdlib-only** (`python -c "import urllib.request; ..."` on `/health`). It previously relied on `curl`, which does not exist in `python:3.14.7-slim`.

### 🛡️ Deployment health checks

- **`docker-compose.prod.yml`: the gateway healthcheck could never pass.** It tested `curl`, which isn't in the slim base image, so the containers stayed `starting` forever and nginx — which now waits on `condition: service_healthy` — never started. Both healthchecks are the stdlib probe now, with `start_period: 20s` and `stop_grace_period: 120s` so in-flight LLM streams drain on redeploy. Also added: `nginx:1.29-alpine` (was untagged `latest`), `depends_on` health gating, `image:` names for rolling deploys, pinned `redis:7.4-alpine` and `edoburu/pgbouncer:v1.26.1-p0`, a fixed multi-line `DATABASE_URL` scalar, and removal of `UVICORN_LOOP=asyncio` so uvloop is used.
- **`nginx.conf` rewritten for real passive HA:** `least_conn` with `max_fails=3 fail_timeout=15s` on both upstreams, `keepalive 64` / `keepalive_requests 1000` to the backends, `proxy_next_upstream error timeout http_502 http_503 http_504` so a dying gateway is skipped instead of surfaced, `proxy_request_buffering off` for streaming, and `worker_connections 2048` / `backlog=4096`.
- **`deploy/docker-compose.yml` (GHCR pull-and-run) and `docker-compose.yml` / `docker-compose.fresh-test.yml` / `docker-compose.test.yml`** got the same health gating, a `redis-cli ping` healthcheck plus `maxmemory 128mb allkeys-lru` on Redis, explicit `build.target:` per service (`fresh-test` now builds `runtime`, i.e. what users actually run), and `up -d --wait` documentation. The dev stack's `gateway` targets `ci` so pytest/`sdk/` remain available in-place.
- **`k8s-manifest.yaml`** gained `readinessProbe`/`livenessProbe` on `/health` (a Deployment with no probes routes traffic to pods whose migrations haven't finished), a pinned `ghcr.io/deziss/llm-secure-gateway` image instead of `:latest`, `envFrom` a Secret, non-root/dropped-capabilities `securityContext`, a soft pod anti-affinity, and `terminationGracePeriodSeconds: 130`.
- **CI** (`ci.yml`) builds the explicit `target: ci`, fails if the published `runtime` image exceeds ~346 MB, and validates every compose file (`config --quiet`, so secrets aren't logged), `nginx.conf -t`, and the k8s manifests.

### 📝 Docs

- `UPGRADE.md`: corrected the stale "Event loop is now uvloop (`--loop uvloop` in the image CMD)" note for the new `CMD`.


### 📦 Alert rule import and export

- **`GET /admin/alerts/rules/export`** streams a portable JSON document (`format: "deziss-alert-rules"`, `format_version: 1`) of all alert rules with a `Content-Disposition` filename (`deziss-alert-rules-<timestamp>.json`); `?custom_only=true` limits it to user-created rules. Derived fields (`custom`, `target_kind`) are excluded so documents stay portable.
- **`POST /admin/alerts/rules/import`** applies such a document: rules are matched by `id` then by name; built-in rules are updated in place (never re-keyed) when `update_existing` is true (default) and skipped when false; new rules always get freshly generated custom ids so a document can never overwrite a built-in id. Per-rule results (`created` / `updated` / `skipped` / `errors`) are reported and one bad entry never aborts the batch. Guards: foreign `format` values and too-new `format_version` are rejected with 422, per-entry validation via the same `_validate_rule_fields` used by the create endpoint, max 200 rules per import.
- **UI**: Export / Import buttons on the Alert rules toolbar (file picker → confirm → import, toast summary, rules table reload). Toolbar row re-aligned: the *via* channel select now shares the 44px `min-h-touch` control height with its buttons, a divider groups test-alert vs rule-management controls, and the description line gets proper top/bottom padding (it previously sat cramped under the row).

## [0.20.1] - 2026-11-26

### 🔌 WAF-safe upstream serialization (opaque 502 on HTML-looking payloads)

Requests whose JSON body contains HTML/XSS-looking markup (`<script>alert(1)</script>`, `<svg onload=...>`, `<img src=x onerror=...>`, and even entity-encoded `&lt;script&gt;`) were being RST by an intermediary WAF / API edge in front of some backends — before any HTTP response was produced. httpx surfaced this as an empty `ReadError('')` retried twice, tripping the circuit breaker for that backend and returning an opaque `502 Upstream provider error`.

- **`serialize_json_body()` now escapes `<`, `>` and `&` as `\u003c` / `\u003e` / `\u0026`** (on top of the existing `ensure_ascii=True`). This is fully lossless — every JSON parser decodes them back to the original characters, so the model receives byte-identical content — but the wire bytes no longer match XSS signatures. Verified live end-to-end against a production vLLM endpoint behind such a WAF: the exact adversarial payload now returns `200` in ~0.4 s instead of a retried 502, on both non-stream and streaming paths. Applied on all proxy planes (`/v1/chat/completions`, `/v1/messages`, both `/v2` planes, audio, bots relay).
- **Actionable terminal error**: when every URL/attempt still dies before headers, the bare `ReadError('')` is replaced by `UpstreamConnectionReset` (subclass of `httpx.ReadError`, so all existing handlers/tests keep working) carrying the upstream URL and the likely causes (edge/WAF rejection, stale keep-alive, upstream restart) — visible in logs, traces and the client 502 message instead of an empty string.

## [0.20.0] - 2026-11-26

### 🛡 Streaming output guardrails

- **POST_CALL guardrails on responses**: assistant text is now scanned on both proxy planes — non-stream JSON bodies are checked (and replaced with a `[GUARDRAIL_NOTICE]` body) and **live SSE streams are checked incrementally** (per-line carry buffer, Anthropic and OpenAI event shapes). `GUARDRAIL_OUTPUT_ENABLED` defaults to `ENABLE_GUARDRAILS`; `GUARDRAIL_OUTPUT_ACTION` is `block` (replacement stream/body, audit `guardrail_violation` where=`stream_output`/`messages_output`) or `warn` (audit + forward). When disabled the stream is byte-identical to pre-v0.20 output.
- `helpers`: `output_guardrail_kwargs()` / `check_response_guardrail()` / `redacted_openai_body()` / `redacted_anthropic_body()` in `proxy_helpers`; violations set span attribute `llm.guardrail_output_block`.

### 🔑 Key rotation automation

- **Rotation policy on keys**: `rotate_interval_days`, `rotation_grace_days`, `rotated_at`, `rotated_from`, `rotation_notified_at` (migration 018).
- **Background sweep** (`services/rotation_service.py`, 6 h loop): rotation-due keys notify owners via webhooks (`key_rotation_due`), reminded at most once per day per key.
- **`POST /admin/keys/{prefix}/rotate`**: mints a replacement that inherits owner, scopes, quotas and rotation policy; the old key survives a configurable grace window (zero-downtime overlap), is marked rotated (double-rotation rejected), and both validation caches are invalidated so the switch applies immediately. Raw replacement key shown once; `api_key_rotated` audit event + `key_rotated` webhook.

### 🧠 Semantic prompt validation

- **Exemplar-similarity gate** (new `promptexemplar` table, migration 019): each request's prompt is embedded with the same sparse n-gram embedding used by the semantic cache and scored against active good/bad exemplars (in-process 60 s cache, invalidated on CRUD). Modes `denylist` (flag near known-bad), `allowlist` (require near-approved), `both`; `PROMPT_VALIDATION_THRESHOLD` (default 0.60); action `flag` (audit `semantic_prompt_validation` + proceed) or `block` (400 before any upstream call). Fails open on any internal error — a broken exemplar store never takes the gateway down.
- **Admin CRUD**: `GET/POST /admin/prompt-exemplars`, `DELETE /admin/prompt-exemplars/{id}`, and `POST /admin/prompt-exemplars/validate` (dry-run scorer returning nearest-exemplar similarities) — enable the gate without sending live traffic.
- Wired into `POST /v1/chat/completions`, `POST /v1/messages` and both `/v2` planes.

### 🕵 PII masking, both directions

- **Response-side masking**: `GUARDRAIL_OUTPUT_PII_MASKING` masks assistant output (streamed deltas masked field-by-field with a carry window so PII split across chunks is caught; whole JSON bodies masked atomically). Masked text is what on-complete hooks, response caches and spans see — raw PII never enters the cache. Span attribute `llm.pii_response_masked`, audit `pii_response_masked`.
- **PII scanner upgrades**: overlap-safe redaction (contiguous/enclosed spans merged), categories (`financial`, `identity`, `contact`, `credentials`) with per-request filtering via `PII_MASK_CATEGORIES`, JSON-configurable patterns via SystemSetting `PII_PATTERNS` (hot-swappable, validated, falls back to built-ins), and a new incremental `PIIScrubber` (feed/flush) purpose-built for token streams.
- Request-side masking now honours DB-custom patterns (`get_pii_scanner_async`), previously built-ins only.

### ✅ Testing & docs

- 909 tests passing (was 879): scrubber carry-window/PII-split-across-chunks unit tests, SSE masking (terminal `[DONE]` ordering, whole-body JSON, disabled passthrough, callback audit), output-guardrail block/warn/untouched streams, rotate-key service semantics (unknown prefix, revoked, double-rotate), exemplar compare modes (denylist/allowlist/both), fail-open, settings-default paths.
- README guardrails/PII/prompt-validation/key-rotation reference tables; `.env.example` guardrail & validation block; API_REFERENCE governance endpoints; comparison doc rows flipped to shipped.

## [0.19.0] - 2026-11-26

### 🛡 Governance: per-tenant policies, retention, compliance evidence

- **Per-tenant policy profiles** (`policy_profiles` table, migration 017): tri-state owner-scoped overrides — `allowed_models`, `denied_models` (prefix wildcards + `*` deny-all, deny wins) and informational `daily_budget_usd` — merged into every policy decision (denial code `model_not_allowed_by_policy_profile`). TTL-cached, admin CRUD at `/admin/policy-profiles`.
- **Observability capture levels**: `OBSERVABILITY_CAPTURE_LEVEL=metadata|redacted|full` (default `metadata` — content capture is opt-in per OTel GenAI semconv), `OBSERVABILITY_MAX_CONTENT_CHARS` truncation cap after redaction (redact-then-truncate at span finish).
- **Retention tiers**: `RETENTION_DAYS_AUDIT|TRACES|SPEND_METRICS` (`0` = keep forever) with a background sweep plus manual `POST /admin/retention/run` (migration 016).
- **Compliance report**: `GET /admin/audit/compliance-report?days=N` — denials by code, budget/governance events and spend per owner, for audit evidence export.
- **Structured denial codes**: governance rejections return `{"error": {"code", "message", "param"}}` (e.g. `model_not_allowed_by_policy_profile`, `budget_exceeded`) instead of prose.
- **Budget soft warnings**: at ≥80% of `monthly_budget_usd` responses carry `X-Budget-Status` and a one-time `budget_warning` audit event; the block itself stays `429 budget_exceeded`.

### 🔐 Security

- **`X-Spiffe-ID` fails closed**: the header is ignored unless `SPIFFE_TRUSTED_HEADER=true` and `SPIFFE_SIDECAR_TOKEN` matches (`X-Spiffe-Token`), i.e. only behind a trusted mesh sidecar; workload identities are additionally checked against a per-backend allowlist.
- **Role-escalation closed**: `UserManager.create` forces `Role.VIEWER`; `UserCreate.role` can no longer mint admins. New regression tests (`tests/test_auth_role_escalation.py`).
- **Backend API keys encrypted at rest**: `llmbackend.api_key` stored Fernet-encrypted (`enc:v1:`) under `GATEWAY_SECRET_KEY` (fallback chain `ENCRYPTION_KEY` → `SECRET_KEY` → `AUTH_SECRET` → ephemeral), masked in admin responses, one-call upgrade endpoint `POST /admin/backends/encrypt-keys`.
- **Circuit breaker hardening**: 5xx responses trip circuits even with retries disabled; half-open admits a single probe with stale-probe auto-release; thresholds via `CIRCUIT_BREAKER_FAILURE_THRESHOLD` / `CIRCUIT_BREAKER_RECOVERY_TIMEOUT`; `get_failure_counts()` exposed for alerting.

### ⚡ Latency (pure gateway hot path)

- **Pure-ASGI middleware stack**: all seven middlewares rewritten off `BaseHTTPMiddleware` — no per-request task-group/body buffering; streaming responses forward frames untouched. `BackpressureMiddleware` now holds its slot until a streamed body completes. +8 middleware tests.
- **uvloop** in the container CMD (`uvicorn --loop uvloop`).
- **Non-blocking Redis**: `RedisRateLimiter` and the prompt cache move to `redis.asyncio`; `apply_rate_limit`/`release_rate_limit_concurrency` are async end-to-end through both proxy planes (BackgroundTask callbacks await).
- Measured (pooled keep-alive, governance plane on): `/health` p50 3.4 / p95 5.1 ms; authenticated `/v1/models` p50 8.3 / p95 ~10 ms — down from p95 25.5 / 13.7 ms.

### 🧰 Misc

- `/api/version` now reports the real gateway version instead of a hard-coded `0.13.0`.
- New `docs/GATEWAY_COMPARISON.md`: DEZISS vs Respan, Bifrost, LiteLLM, Agent Router, Vercel, Cloudflare, OpenRouter, Portkey, TrueFoundry, Kong — pricing, speed, and a feature scorecard.
- Docs refresh: README governance/perf configuration tables, ARCHITECTURE middleware chain + 22-table data model, OBSERVABILITY capture/retention, API_REFERENCE governance endpoints, UPGRADE v0.19.0 notes. 15 new env vars documented in `.env.example`.

### ✅ Testing

- 879 tests passing (was 788): pure-ASGI streaming/state, role-escalation, policy-profile tenancy, retention sweeps, key crypto round-trips, breaker half-open/5xx behaviour, async-redis fail-open + slot release.

## [0.18.0] - 2026-11-26

### 🔧 Customizable Alert Rules

- **Target filters**: every rule takes glob patterns (`wardha*`, `vllm-42:llama3`, `*/gpu[0-3]`; a plain term like `wardha` also matches `wardha/gpu0` and `wardha:llama3`), so one metric rule can be scoped to a single host, server, or model. Changing a filter auto-resolves incidents whose target no longer matches.
- **Create/delete rules from the UI or API**: `POST /admin/alerts/rules` creates rules over any catalog metric; `DELETE` removes custom rules and closes their open incidents; built-in rules are edit/toggle-only (405 on delete). `GET /admin/alerts/metrics` returns the metric catalog for form dropdowns.
- **Rule testing**: `POST /admin/alerts/rules/{id}/test` dry-runs a rule against live telemetry (which targets currently violate + the live target list) and can send a real `[TEST]` notification without creating incidents; `POST /admin/alerts/test-alert` pings webhook/email/URL channels.
- **Per-rule webhook URLs**: channel lists now accept `https://…` endpoints (Slack/Discord/Teams incoming webhooks), delivered HMAC-signed like the global webhook.
- **Richer editing**: PATCH now covers name, comparator, target filter, description, cooldown and channels, with validating 422s that list the valid options.
- Alert Center UI: New-rule form with metric dropdown, per-row Edit / Test / Delete, target-filter and description columns, and a channel-aware "Send test alert".
- Migration `015_alert_rule_customise`: `alert_rules.target_filter` + `description`.

### 🚀 Release Automation

- **GitHub Releases are now automated for every tag**: `.github/workflows/release.yml` creates/refreshes the release via `scripts/release_notes.py`, which generates the release **title** and a highlighted **"What's inside"** summary from the CHANGELOG section (tag message wins for the title when present), followed by the full section and pull instructions. A tag pushed without a matching CHANGELOG section fails the workflow with a clear error.
- `scripts/release_notes.py` doubles as a local preview CLI: `python3 scripts/release_notes.py --latest --print`.

### 🐛 Fixes

- `sdk/` is now `COPY`'d into the gateway image (compose bind-mounted it, so only image builds/CI missed it, breaking `tests/test_deziss_sdk.py` collection).

### ✅ Testing

- 788 tests passing: alert customization (filters, engine scoping, filtered auto-resolve, id helpers), release-notes generator, SDK contract.

## [0.17.0] - 2026-11-25

### 🤖 Agent Intelligence (Phase 3)

- **Agent Run Correlation (`observability/agents.py`)**: gateway requests are stitched into *runs* — one agent workflow, many LLM calls — derived from `x-deziss-run-id`, `x-deziss-agent`, `x-deziss-parent-agent`, `x-deziss-session-id` headers, or from body session signals (`user`, `metadata.session_id`/`user_id`), which yield a deterministic `ses-…` pseudo-run with no client changes. Unattributed requests are intentionally not grouped (no noise).
- **Steps & Handoffs**: every dispatched request records an LLM step (kind, agent, model, server, status, duration, TTFT, tokens, error) via `RequestTracker.finish()` — fire-and-forget, so it can never break the request path. Agent identity changes mid-run are recorded as handoff edges; a run with ≥50% errors is marked failed, an idle run completed.
- **Storage & Flushing**: new `agent_runs` / `agent_steps` tables (migration 014). Steps batch in a bounded in-memory queue and are flushed by the observability poller tick, so the request path never blocks on the database.
- **Admin APIs**: `GET /admin/observability/agent-runs`, `GET /admin/observability/agent-runs/{run_id}` (run + step waterfall), `GET /admin/observability/agents/summary`.
- **Agent Intelligence tab** in Observability: runs table, expandable per-step waterfalls, per-agent summary cards.
- **`deziss-sdk` (`sdk/python`)**: zero-dependency Python SDK — `agent_run()` context manager, `run.handoff()`, `run.child()`, mapping duck-typing for OpenAI `extra_headers`, and `DEZISS_RUN_ID`/`DEZISS_SESSION_ID` env propagation across processes.

### 🖥 Infrastructure & GPU Intelligence (Phase 2)

- **Alert & Incident Engine (`alerts/`)**: declarative rules (metric, comparator, threshold, window, severity, cooldown, target kind) evaluated against live telemetry each poll tick; firing requires a sustained breach, recovery uses hysteresis; incidents dedupe per (rule, target) episode with notification cooldowns. Alert acknowledgement + incident history, and notification delivery over webhooks (HMAC-signed) and email. 10 default rules seeded (`backend_down`, KV-cache saturation, inference queue, GPU memory/temperature, TTFT degradation, gateway error rate, throughput collapse, …). New **Alert Center** UI tab; admin API `GET/PATCH /admin/alerts/rules`, `GET /admin/alerts/incidents`, `POST /admin/alerts/incidents/{id}/ack`.
- **Event-style alerting**: window-0 `>=` rules (e.g. GPU XID errors) fire on the instant sample and resolve when the value drops.
- **GPU extended telemetry**: DCGM parsers now collect XID errors, cumulative double-bit ECC errors, SM/memory clocks and power limits (clock/ECC fields also from the nvidia-smi exporter); XID present-and-non-zero maps to `xid_error=1`. Fields flow into the observability payload automatically. Rules `gpu_temp_high` and `gpu_xid_error` ship enabled, plus new `gpu_ecc_dbe_error`.
- **Host & container telemetry (`collect/hosts.py`)**: scrape Node Exporter / cAdvisor endpoints registered as node hosts (CRUD at `/admin/observability/node-hosts`); CPU per-core/load, RAM/swap, disk I/O & capacity, network throughput, TCP retransmits, container CPU/memory limits, restarts and OOM kills surface in the payload and feed alerts. New **Infrastructure** tab.

### 🧭 Routing & Observability

- **Cost-aware routing (`ROUTING_STRATEGY=cost_aware`)**: ranks candidate backends by the estimated dollar cost of the request from `model_pricing` (per model/provider with `*` wildcard; 30 s TTL cache, 250 ms hard DB cap so routing never stalls), with latency/queue tiebreakers, poller health penalties, and local engines (vLLM/Ollama/…) counted as marginal-cost free. Falls back to capacity scoring when nothing is priced.
- **GPU/infrastructure-aware routing (`ROUTING_STRATEGY=gpu_aware`)**: scores backends on KV-cache pressure, queue depth, free-VRAM headroom, GPU util/temperature, gateway-measured latency and health — with a human-readable rationale in the routing log line. Missing signals degrade to neutral instead of breaking selection.
- **Live throughput**: avg & live tokens/sec per model during streaming (counting vLLM `delta.reasoning` so reasoning models update), plus SSE delta (chunk) events-per-second per model.

### 🐛 Fixes

- Equal-jitter retry backoff and a keepalive race that surfaced as `httpx.ReadError` on pooled connections.
- Available-models popup on click in the models list, and a license plan upgrade flow in the license card.
- Timezone handling standardized to explicit UTC serialization with local rendering across the admin UI.

### ✅ Testing

- 775 tests passing (was ~720 at v0.16.2): new suites for alert rules/engine/incidents/notifications, GPU extended telemetry parsing, event-style alerts, cost-aware routing, GPU-aware routing, node telemetry, agent recorder correlation, and the SDK header contract.

## [0.16.2] - 2026-11-19

### 🌐 Unified Local Timezone Rendering & UTC Standardization

- **Global Client-Side Timezone Utilities (`globals.js`)**:
  - Implemented `window.parseIsoDate()`, `window.formatTime()`, `window.formatDate()`, `window.formatDateTime()`, and `window.formatRelTime()` across the admin application.
  - Resolved UTC vs local timezone display discrepancy where Live Traces previously sliced raw UTC strings (`t.timestamp.split("T")[1].substring(0, 8)`), showing UTC instead of the operator's local time (e.g. IST / UTC+05:30).
  - Protected against ECMAScript local date parsing hazards by automatically normalizing naive ISO strings without timezone offsets to UTC (`+ "Z"`), and parsing calendar date-only strings without cross-day timezone shifts.

- **Unified UI Component Localization**:
  - **Live Traces Table**: Formatted timestamp column to 24-hour local time (`HH:mm:ss`) with localized full date/time tooltips on hover.
  - **Trace Waterfall Drawer**: Displayed localized timestamps in drawer headers and error diagnostic summaries.
  - **Dashboard & Charts**: Localized 24-hour traffic and spend chart X-axis labels and last updated badges.
  - **Users, Audit, Owners & Settings**: Localized `Joined`, `Last Login`, audit events, API key dates, invite tokens, and lease expirations.

- **Explicit Backend UTC Serialization**:
  - Enforced explicit UTC serialization with `Z` or `+00:00` across `users.py` and `observability/api.py`.

### 📚 Documentation, Troubleshooting & Diagnostics

- **Comprehensive Upstream `httpx.ReadError` & Connection Reset Troubleshooting**:
  - Added dedicated troubleshooting guides across `USER_GUIDE.md`, `docs/VLLM_API_SUPPORT.md`, `docs/VLLM_INTEGRATION_ANALYSIS.md`, and `docs/OBSERVABILITY.md`.
  - Documented root causes for sub-50ms upstream socket termination: raw multi-byte UTF-8 typographic characters in coding agent prompts (VS Code Copilot SDK, Cline, Roo Code), replayed assistant thinking history crashing Jinja templates, and hop-by-hop header poisoning.
  - Clarified mitigation steps: pure ASCII JSON encoding (`\uXXXX` escaping), thinking field sanitization, and connection header normalization.

- **Admin UI Table Rendering & Cache Busting Troubleshooting**:
  - Documented browser cache invalidation procedures for empty data tables on Model Servers (`/admin/view/servers`) and Projects (`/admin/view/projects`).
  - Added verification steps for dynamic `?v={{ asset_v }}` asset tokens and browser hard refresh shortcuts (`Ctrl+Shift+R` / `Cmd+Shift+R`).

- **Observability Live Traces & Waterfall Diagnostics**:
  - Added diagnostic matrix in `TELEMETRY_GUIDE.md` and `docs/OBSERVABILITY.md` for interpreting `500 Upstream Err` vs `502 Bad Gateway` vs `429 Rate Limit` in Live Traces and Arize Phoenix traces.

## [0.16.1] - 2026-11-19

### 🐛 Upstream vLLM/Local Backend Stability & Coding Agent Reliability

- **ASCII-Safe JSON Serialization (`ensure_ascii=True`)**:
  - Implemented `serialize_json_body()` to force pure ASCII JSON encoding (`\uXXXX` escaping) across all upstream proxy dispatch paths (`try_backend_with_fallback`, Anthropic translation, and TTS endpoints).
  - Resolved critical `httpx.ReadError` / `Connection reset by peer` (Errno 104) where upstream vLLM and local C++ tokenizers/parsers abruptly terminated TCP sockets when processing large payloads containing raw multi-byte UTF-8 typographical characters (em-dashes `—`, en-dashes `–`, curved quotes `“` `”`, arrows `→`, and math symbols `≤`, `≥`) common in agent tool definitions and system prompts.
  - Enforced explicit `Content-Length` and `Content-Type: application/json` headers on serialized request bodies.

- **Agent Conversation History Sanitization**:
  - Automatically stripped non-standard thinking/reasoning history fields (`reasoning`, `reasoning_content`, `cot_id`, `cot_summary`) replayed by multi-turn coding agents (VS Code Copilot SDK, Cline, Roo Code) before dispatching to vLLM, Ollama, and llama.cpp backends.
  - Automatically deserialized stringified `tool_calls` into standard JSON lists and ensured valid `role` assignment across all conversation turns.
  - Fixed Jinja chat template rendering crashes and instant socket resets on vLLM backends.

- **RFC 7230/9110 Hop-by-Hop Header Stripping**:
  - Stripped all hop-by-hop headers (`connection`, `keep-alive`, `proxy-authenticate`, `proxy-authorization`, `te`, `trailers`, `transfer-encoding`, `upgrade`) from forwarded backend requests, preventing client `Connection: close` headers from poisoning persistent HTTPX connection pools.

- **Circuit Breaker & Fallback Exception Safety**:
  - Guaranteed valid `BaseException` instantiation in `try_backend_with_fallback` and fallback re-raise paths, resolving `TypeError: exceptions must derive from BaseException`.
  - Added half-open probe fallback to prevent deadlocks when all target URLs are in open circuit breaker states.

- **Admin UI Table Rendering & Asset Cache Invalidation**:
  - Restored data table rendering on `/admin/view/servers` and `/admin/view/projects` by adding comprehensive compatibility shims for `$.fn.dataTable`, `$.fn.DataTable`, `rows().invalidate()`, `.draw()`, and `.api()`.
  - Enforced dynamic per-render asset cache busting `?v={{ asset_v }}` to prevent stale browser disk caching.
  - Capped OpenTelemetry flattened attributes to prevent span buffer overflow drops.

## [0.16.0] - 2026-11-19

### 🚀 Enterprise UI Revamp & Licencia Licensing

- **Modernized Enterprise UI/UX**:
  - Full aesthetic and architectural overhaul of the Gateway admin interface using unslop design tokens, semantic borders, and cohesive dark-theme typography.
  - Zero-dependency client-side table system (`gateway-table.js`) supporting instant fuzzy search, multi-column sorting, and responsive pagination across Backends, Users, and Owners.
  - Redesigned authentication and recovery workflows (login, registration, forgot/reset password) with accessible form styling and responsive layouts.
  - Live Traces Waterfall Explorer on the Observability page featuring real-time span execution timelines, status inspection, and token cost avoidance visualization.

- **Licencia Enterprise Licensing Integration**:
  - Offline-first cryptographic lease caching (`LicenciaClient`) with persistent filesystem backup (`/tmp/gateway_license_lease.json`).
  - Multi-factor hardware fingerprinting (`generate_hardware_id`) using CPU model, motherboard UUID, machine-id, and MAC addresses with automated local subnet / loopback bypass.
  - Periodic background check-in loop (configurable interval, default 6 hours) with graceful degradation to cached offline leases.
  - Dynamic tier entitlement enforcement (`Community`, `Team`, `Enterprise`) for registered backend limits, API key quotas, and rate limiting ceilings.
  - Administrative License Portal under Settings for one-click key activation, status verification, lease renewal, and seat deactivation.

- **Dynamic Complexity-Based Routing**:
  - Multi-tier prompt complexity classification (`ComplexityTier.TIER_1_FAST`, `TIER_2_STANDARD`, `TIER_3_REASONING`) evaluating token lengths, syntax markers (code blocks, markdown, mathematical LaTeX), and cognitive indicators.
  - Transparent virtual model resolution for `auto`, `smart-route`, and `auto-router` requests to optimal downstream model backends.
  - Automated request payload rewriting ensuring upstream providers receive valid physical model identifiers.

- **Multi-Provider Fallback Cascade Ranking**:
  - Prioritized fallback matrix ranking (`rank_fallback_targets`) dynamically sorting cross-provider targets based on availability and configured tier weights.

- **OpenTelemetry GenAI Semantic Conventions (v1.30+)**:
  - Standardized tracing attributes strictly conforming to OTel GenAI SemConv v1.30+ (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`).
  - Standardized error taxonomy mapping upstream HTTP errors to OpenTelemetry exception events and canonical error classifications.
  - In-memory trace ring buffer (`TraceRingBuffer`) capturing full request dispatch metadata with authenticated `/traces` REST endpoint.

- **Security & Caching Enhancements**:
  - Pre-call prompt injection shield enforcement via `evaluate_guardrail` in standard and v2 proxy pipelines.
  - Automatic injection of Anthropic prompt caching headers (`anthropic-beta: prompt-caching-2024-07-31`) for Claude backends.

## [0.15.1] - 2026-11-18

### 🐛 Fixed & Resilient Networking

- **Upstream Connection Keep-Alive & ReadError Resilience**:
  - Proactive keep-alive connection expiration (`keepalive_expiry=15.0s`) on persistent HTTPX client pools, preventing socket reuse races where upstream vLLM/Uvicorn servers close idle TCP connections.
  - Comprehensive transport exception handling in `try_backend_with_fallback`: catches `httpx.ReadError`, `httpx.RemoteProtocolError`, `httpx.WriteError`, `httpx.WriteTimeout`, `httpx.PoolTimeout`, and all `httpx.TransportError` subclasses.
  - Automatic client pool eviction (`_reset_client`): immediately purges and closes dead pooled connections upon transport failure, guaranteeing subsequent attempts and retries establish a clean TCP handshake.
  - Contextual diagnostic logging on `httpx.ReadError` capturing target URL, HTTP method, backend name, attempt count, and elapsed duration before header failure.
  - Automatic transient recovery: allows at least 2 attempts by default (and up to 3 with exponential backoff when `ENABLE_RETRY_BACKOFF=true`), preventing transient keepalive resets from surfacing as 502 Bad Gateway to clients.
  - Configurable upstream request timeout (`UPSTREAM_TIMEOUT`, default 120.0s) to support long reasoning / thinking token generation.

- **Arize Phoenix & OpenTelemetry Model Tracing**:
  - Suppressed internal FastAPI framework HTTP route spans (`GET /v1/models`, `GET /admin/dashboard`) from cluttering the default Arize Phoenix project.
  - Captured full prompt messages in OpenInference span `input.value` and `input.mime_type = "application/json"`, restoring visibility of user inputs in the Phoenix UI.
  - Standardized OpenInference span names to `chat {model}` for clean multi-model trace observability.

- **GPU & Observability UI Fixes**:
  - Prevented GPU host polling timeouts from blocking cluster metrics by maintaining cached GPU states with exponential backoff on unreachable hosts.
  - Removed redundant Deep-Dive Telemetry column from the Observability models table, consolidating telemetry views into row click actions.
  - Added "Remember me" extended session option on the admin login page.

## [0.15.0] - 2026-11-05

### ✨ New

- **Historical Telemetry Aggregation Engine (1s, 1m, 1h, 6h, 12h, 24h)**:
  - Multi-scale historical aggregation across 1s (Live), 1m, 1h, 6h, 12h, and 24h horizons.
  - In-memory `_MinuteBucket` ring buffering with background PostgreSQL UPSERT aggregation (`model_telemetry_rollup`, database migration 010).
  - Adaptive timeseries downsampling and automatic 30-day retention pruning.
  - New timeseries endpoint: `GET /admin/observability/history` with server and model query filtering.
- **Interactive Multi-Scale Observability UI & Deep-Dive Modal**:
  - Timeframe selector toolbar `[ 1s (Live) | 1m | 1h | 6h | 12h | 24h ]` on the Observability page with dynamic header metrics and multi-hour SVG trend sparklines.
  - Model Telemetry Deep-Dive Modal with 4 interactive diagnostic charts: Traffic Volume & RPS, Latency Distribution (TTFT & ITL), Token Breakdown (Prompt vs. Completion), and Hardware/Cache Telemetry.
- **24-Hour Traffic Activity & Observability Dashboard**:
  - Executive 24-hour dual-axis bar and line timeline chart (`GET /admin/dashboard/traffic-24h`) displaying hourly request volume, RPS, peak load, P50 TTFT, and token consumption.
  - Responsive, compact full-width layout with balanced spacing, edge connectivity health monitoring, and live client telemetry.
- **Model Token Pricing & Cost Governance**:
  - Custom per-token and per-1M input/output pricing management (`/admin/spend/pricing`).
  - Provider cost auto-detection for OpenAI, Anthropic, Gemini, Groq, and DeepSeek backends.
  - Live cost calculation and per-tenant monthly spend tracking.
- **Full Native Anthropic `/v1/messages` Instrumentation**:
  - First-class support for Claude Code CLI, Cursor, Continue.dev, and Anthropic SDKs.
  - Bidirectional message translation to OpenAI and vLLM backends with OpenInference attributes (`openinference.span.kind = "LLM"`), TTFT, and token usage accounting.
- **Arize Phoenix Per-Tenant Project Isolation**:
  - Decoupled root spans (`PhoenixTraceManager.start_root_span`) automatically routing traces to dedicated per-tenant projects (`gateway-<tenant>-<provider>`).
- **Queue-Aware & Least-Busy Dynamic Load Balancing**:
  - Real-time traffic balancing across model backend replicas based on active in-flight requests from `LiveStats`, capacity weights, and rolling latency.
- **Multi-Dimensional Rate Limiting**:
  - Added Concurrency Semaphores (`max_concurrency`) and Tokens-Per-Minute (`rate_limit_tpm`) sliding window rate limiting alongside Requests-Per-Minute (`rate_limit_rpm`) with atomic Redis synchronization and local fallback (database migration 008).
- **One-Click Client Setup Snippets in Admin UI**:
  - Interactive tabbed modal in Projects / API keys to instantly copy tested configurations for Claude Code CLI, Cursor, Continue.dev, Python SDK (OpenAI & Anthropic), and cURL (`/api/client-snippets`).
- **Automatic Admin Password Synchronization**:
  - Automatically updates the default admin password hash in the database on boot whenever `DEFAULT_ADMIN_PASSWORD` changes in `.env`.

### 🐛 Fixed

- **Live Metrics Race Condition**: Resolved Temporal Dead Zone reference error in `dashboard.js` when `loadMetrics()` executed before chart initialization.
- **Same-Origin Relative URL Transport**: Replaced absolute `BASE_URL` fetches in dashboard scripts with relative paths to prevent cross-origin cookie omission when accessing via alternative hostnames or reverse proxies.
- **Target URL Path Deduplication**: Fixed URL normalization in proxy router to prevent duplicate segments during endpoint forwarding.

### 📜 License

- Explicitly maintained and confirmed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** across manifests and documentation.

---

## [0.14.2] - 2026-10-30

### ✨ New

- **Integrations tab is a control panel.** Pick a tool (Arize Phoenix / Arize AX, Langfuse, LangSmith, OpenObserve, SigNoz, Grafana, Datadog, Honeycomb, New Relic, Elastic, generic OTLP), read how to connect it, enter its endpoint and key, press **Test** (sends one real span and shows the backend's HTTP answer), then turn it on or off. Changes apply immediately, with no restart; other replicas follow within about 15 seconds. Credentials are stored Fernet-encrypted in the new `obs_integration` table (migration 007) and are never returned by the API. A Prometheus card shows a ready-made scrape config.
- **Per-tenant Phoenix projects from the UI.** "Separate project per gateway project and provider" sends traces to `gateway-<project>-<provider>`, as the environment setup does.
- **Filters and sorting.** Models: text, server, status. Groups: text, GPU data, traffic, sort. Click any column header to sort the Models table, the Groups list and the GPU tables.
- **Groups: compact cards and a List view** (the choice is remembered per browser).

### ✨ Changed

- **No double export.** When a tool is set both in `OBS_EXPORTERS` and in the UI, the UI settings win while enabled: the environment copy pauses (shown as "env (paused: set in UI)") and resumes when the UI integration is turned off or removed.
- **Models and Groups list every configured model**, not only those with traffic through the gateway, with server-side RPS, tok/s and ITL. vLLM production-stack routers are read per engine, and per-model series are read from plain vLLM.
- **GPU data is per model:** VRAM is the model's own process memory over its GPUs' capacity. A server on the same machine as a GPU host is linked automatically, and a model with no GPU process says why instead of showing the whole host.
- The gateway version is now 0.14.2 (GPU agent too).

### 🐛 Fixed

- Buttons inside dialogs did nothing (Revoke key, Delete permission, Delete model): the dialog panels swallowed the click.
- Editing a project never saved (the form sent `PUT`, the API only accepts `PATCH`), and its "block dangerous endpoints" checkbox was dropped.
- Revoked API keys still showed as active.
- A fallback chain with a single target could not be saved.
- GPU agent: `python -m <module>` was read as a model name (shown as "uvicorn").
- GPU table columns are aligned.

---

## [0.14.1] - 2026-10-29

### ✨ New

- **Show/hide password** button (eye icon) on the login, register and reset-password pages. It is keyboard-accessible and announces its state to screen readers.

### 🐛 Fixed

- **`agent/docker-compose.yml` is pull-only.** A failed GHCR pull used to fall back to `build: .` and fail with "open Dockerfile: no such file" on hosts that only had the compose file.

### ⬆️ Dependencies

- alembic >= 1.20.0, pydantic-settings >= 2.15.0, uvicorn[standard] >= 0.53.0, pyjwt >= 2.14.0, asyncpg >= 0.31.0.
- `docker/build-push-action` v5 → v7.
- These supersede dependabot PRs #12–#17.

---

## [0.14.0] - 2026-10-29

### ✨ New

- **Observability page** at `/admin/observability` (admins and managers). Tabs:
  - **Models:** per server and model, RPS over 10 s and 60 s, waiting and generating, tok/s (decode and throughput), TTFT p50, server queue and KV cache, with a sparkline.
  - **Groups:** one card per model group, with VRAM, GPU utilisation, and totals for RPS, tok/s and queue.
  - **GPUs:** per host, with utilisation, VRAM, temperature, power and per-process memory. Admins can manage GPU hosts and link servers to GPUs here.
  - **Gateway:** process RSS, USS, threads, tasks and CPU; per-thread CPU time; in-flight bytes per model; tracemalloc top 20 (admin toggle); admins can edit model group overrides.
  - **Integrations:** each telemetry sink's signals, last success, last error and counts.
  - Live updates over SSE every 3 s, falling back to polling. Both pause when the tab is hidden.
- **Prometheus `/metrics`** on the gateway, with an optional bearer token (`OBS_METRICS_TOKEN`). It exports per-model traffic, tokens, TTFT and tok/s, plus server-side and GPU gauges.
- **GPU agent** for GPU hosts (`--pid=host` and GPU device access). It reports per-process VRAM and maps it to the model server that owns it. DCGM, nvidia_gpu_exporter and AMD exporters are also supported as sources.
- **Export presets** via `OBS_EXPORTERS`: Phoenix (self-hosted, Phoenix Cloud and Arize AX), Langfuse, LangSmith, OpenObserve, SigNoz, Grafana, Datadog, Honeycomb, New Relic, Elastic, and generic OTLP.
- **Multi-replica view:** with `REDIS_URL` set, the admin page merges live stats across replicas (scope `cluster`).
- **Grafana dashboard and alert rules** rewritten to use only the emitted metric names, with group totals joined through `llm_model_group_info`. Prometheus config scrapes the gateway and GPU agents.
- **Docs:** new `docs/OBSERVABILITY.md` (architecture, presets, agent install, metric reference, known gaps). Updated `TELEMETRY_GUIDE.md` and `README.md`.

### ✨ Changed

- `ENABLE_TELEMETRY=true` still works as before. Other tools are selected with `OBS_EXPORTERS`.
- The gateway version is now 0.14.0.
- Release images on GHCR: `ghcr.io/deziss/llm-secure-gateway` and the new `ghcr.io/deziss/llm-secure-gateway-gpu-agent` (both linux/amd64 + arm64). `deploy/` holds a pull-and-run compose file and `.env.example`.
- The GPU agent now listens on port 9835, so it no longer clashes with dcgm-exporter on 9400.

---

## [0.13.0] - 2026-10-03

### 🔐 Security

- **Fixed a privilege escalation.** `PATCH /admin/users/{id}` required only MANAGER but wrote `role`, `is_superuser` and `is_active` unchecked, so a manager could make themselves admin. Now:
  - only admins change roles or the superuser flag, or edit admin, superuser or manager accounts;
  - managers may set DEVELOPER/VIEWER and toggle active on those accounts;
  - nobody can change their own role or state.
- **Viewers are read-only** on projects and API keys. The API used to accept their writes even though the page was hidden from them.

### ✨ Changed

- **"Backends" are now "Model servers".**
  - The page is `/admin/view/servers`; `/admin/view/backends` redirects there.
  - The API is `/admin/servers/*`; `/admin/backends/*` is deprecated but still works.
  - Responses add `server_type` next to `backend_type`, and requests accept either.
  - The Python class is `ModelServer`; `LLMBackend` remains as an alias.
  - **Database:** migration `005_model_server` renames table `llmbackend` to `model_server`. It is idempotent and drops an empty duplicate left by `create_all`. See UPGRADE.md.
  - **API tester:** the routing modes "Standard / Direct" are now "By type / Pick server", with an explanation; "Provider" selectors read "Server type" across the playgrounds.
- **"Owners" are now "Projects" everywhere in the UI, with the type options *Personal* and *Team or service*.
  - `/admin/projects/*` is the canonical API. `/admin/owners/*` still works and is marked deprecated in OpenAPI.
  - `/admin/view/owners` redirects to `/admin/view/projects`.
  - The Projects list shows each project's linked user (or "Service"), and the Users list shows each user's project count.
  - The README gains a Key Concepts section.
- **Manager access aligned with the UI.**
  - Managers get dashboard live metrics, Spend, the Bots tab, and backend edit, delete, health and sync.
  - Pulling and deleting models on an Ollama host stays admin-only, and its button is hidden for managers.
  - Developers and viewers see a playground workspace on the dashboard instead of empty cards.

---

## [0.12.0] - 2026-10-03

### ✨ New

- **Dashboard summary cards.** Spend this month (with change against the same point last month), tokens today, a 14-day spend chart, top models, a "Needs attention" list (budgets at 80% or more and keys expiring within 7 days), response-cache hits and tokens saved, denied and failed audit events in the last 24h, and a backend health strip built from live circuit-breaker state. Served by one new endpoint, `GET /admin/dashboard/summary` (admin/manager), which does no network I/O, so an unreachable backend cannot slow it down.
- **Audit history page** (`/admin/view/audit`, admin/manager). Filters by event type, decision, text and time window. Backed by `GET /admin/audit` and `GET /admin/audit/event-types`.
- **API tester:** request history (last 20, one-click restore or re-run, never stores keys); a timing bar with time to first byte, time to first token, total time, tokens in and out, and tokens/s; Body (text or highlighted JSON), Headers (gateway headers first, with explanations), Stream (every event with its timestamp) and Code tabs; copy-as-code for cURL, Python and JavaScript; Ctrl+Enter to send. Streamed chat requests now ask for `include_usage`.
- **Embedding Playground rebuilt.** PCA, t-SNE and UMAP layouts in a Web Worker, with animated transitions and explained variance; nearest/threshold similarity lines with optional scores; a detail panel with the top 10 neighbours; a similarity matrix with ordering and CSV export; meaning search and vector math (`dog - car + truck`); k-means clustering; cosine, dot and euclidean metrics; `text | label` input and .txt/.csv/.tsv upload; batched requests (64 texts per call); an IndexedDB embedding cache and saved session; JSON import and export; a demo provider that needs no backend. It now uses your sign-in when no API key is given. umap-js 1.4.0 (Apache-2.0) is vendored.
- **Chat:** a "thinking" panel that streams reasoning and collapses smoothly 3 seconds after it starts; token, context and speed details per message plus conversation totals; Markdown rendering with code copy buttons and safe links, shared with the Model Arena.
- **Model Arena:** an owner API key field, a dropdown that lists every model, and Raw/Rendered toggles.
- **`ALLOW_INSECURE_HTTP` dev mode** (off by default). Lets you sign in over plain `http://<lan-ip>` by dropping the `Secure` flag on the session and CSRF cookies and suppressing HSTS. Logs a startup warning and shows a top-bar badge. Without it, the login page now explains why sign-in will not stick over plain HTTP.

### 🎨 Admin panel

- Settings is split into tabs (General, Access, Routing, Caching, Compliance, Scope policy, Bots), and the selected tab is kept in the URL hash. The scope-policy add-rule form moved above the table with a scope dropdown.
- Bots moved into a Settings tab. `/admin/view/bots` now redirects to `/admin/view/settings#bots`.
- The sidebar has a "System" group (Users, Audit history, Settings).
- The theme switch moved to the top bar as a three-way toggle.
- Tables pin their name and action columns when they overflow; DataTables' black borders are gone.
- Modals are more compact; the "Created" column was added to API keys.
- The API tester and Embedding Playground settings panels are narrow fixed-width columns.
- Page headings and font sizes are consistent across pages.

### 🐛 Fixes

- **Embeddings returned corrupt JSON** with "normalize thinking" on: the stream rewriter split every network chunk on newlines and re-added them, putting a newline inside the JSON body and overrunning the upstream `Content-Length`. Plain JSON bodies now pass through unchanged, and SSE is line-buffered and UTF-8-safe. Upstream `Content-Length`/`Content-Encoding` are no longer copied onto rewritten responses.
- **`GET /admin/metrics` returned 500 (`DetachedInstanceError`).** Cached backend rows stayed bound to an old session and expired after a rollback. They are now detached before caching.
- **Direct-mode chat returned 500** (`NameError`: `fallback_chain_id`, `cache_key`).
- **Owner API keys got 403 "endpoint not allowed"** because of a `/v1` prefix mismatch. Endpoint matching is now canonical and segment-aware.
- **Audit log writes failed** with timezone-aware timestamps.
- **Model sync** handled base URLs ending in `/v1` incorrectly, and reported success when it found no models.
- **Admins could not edit settings.** Manager-only guards threw on admin-only fields.
- **Popups were slow and the close button did nothing**, because of an icon re-render loop and a backdrop blur.
- Chat send errors, the image-attach hit area, theme switching that only worked sometimes, and the "Back to API Tester" breadcrumbs were also fixed.

### 🧪 Tests

436 tests (up from 329), including real-SQLite dashboard aggregates, dev-mode cookie flags, stream rewriting with split chunks, endpoint whitelisting and a pyflakes undefined-name check.

---

## [0.11.1] - 2026-10-03

### 🐛 Fixes from a code review of the v0.11.0 redesign

- **Focus escaped stacked dialogs.** With the "New API Key Issued" dialog or the confirm dialog open on top of another modal, Tab moved focus back to the modal underneath, so keyboard users could not reach "Acknowledged" and could activate hidden controls. Focus now stays in the top-most dialog.
- **Escape closed every modal at once.** `backends.js`, `owners.js` and `users.js` each had a page-level Escape listener that closed all of that page's modals, including the one beneath an open dialog. Removed; `admin-modal.js` now closes only the top-most dialog, through its own Close button so page cleanup still runs.
- **Rotating a phone released a modal's scroll lock.** Crossing the 768px breakpoint ran the drawer's close routine even if the drawer had never opened, removing the page scroll lock an open modal relied on. The drawer now only undoes its own lock, and keeps it while a modal is open.
- **The closed mobile drawer stayed in the Tab order.** It was only translated off-screen, so ~14 hidden links preceded the page content and were announced by screen readers. It is now `inert` while closed below 768px.
- **Theme controls drifted apart.** The top-bar button and the Settings toggle now keep each other in sync.
- **Icons were re-rendered twice per theme click.** Now at most once, and not at all when the icon is unchanged.

---

## [0.11.0] - 2026-10-01

### 🎨 Admin panel redesign: responsive, themeable, accessible

The admin panel had one breakpoint (`md`), a top nav that overflowed and clipped "Logout" at 1238px, no design tokens, and tables that were clipped on phones.

- **New shell.** A sidebar replaces the top nav: full labels at ≥1024px, an icon rail at 768–1023px, and a slide-over drawer below 768px with a hamburger, backdrop, focus trap and Escape-to-close. Role gating is unchanged (admin/manager 12 links, developer 6, viewer 5). The Playground dropdown, which fought itself between CSS `:hover` and a JS toggle and was unusable on touch, is now a plain group of links. Adds a skip link and a `<main>` landmark.
- **Theme control in the top bar** for every role. It used to live only on Settings, which is admin/manager-only. `localStorage` access is now guarded (it threw in private windows).
- **Design tokens.** Colours are CSS variables surfaced through `tailwind.config.js` (`bg-surface-raised`, `text-ink`, `border-hairline`, `bg-accent`, `text-ok`/`warn`/`down`), so one class is correct in both themes. Also: two radii (`rounded-control`, `rounded-panel`), two elevations, named type steps replacing `text-[10px]`, and a 44px touch-target utility. The chat and embedding playgrounds, previously effectively dark-only, now render correctly in light mode.
- **Tables become cards below 768px.** Done once in CSS plus `admin-table.js`, so all seven tables and the tables inside modals are covered without touching any column renderer. DataTables writes an inline `width` and a `content-box` rule that both beat equal-specificity selectors; the card CSS overrides them with `!important` and a higher-specificity rule.
- **Every modal is now accessible.** `admin-modal.js` observes the `hidden` class on all ten dialogs and adds focus trap, scroll lock, Escape, focus restore, `role="dialog"` and an `aria-labelledby` taken from the heading, with no changes to the pages' own open/close code.
- **Responsive fixes per page:** KPI grids no longer jump from 1 to 3/4 columns at `md`; the chat and embedding sidebars collapse behind a toggle on phones and use `dvh` instead of `100vh`; compare-model panes no longer force 1120px of height; the toast fits a 320px screen; the Bots table no longer forces a 900px minimum width.

### 🔐 Security

- **Removed inline event handlers built from data.** Row actions used `onclick="fn('${id}')"` with a `replace(/'/g, "\'")` that was a no-op, and `escapeHtml` is not sufficient inside an inline handler because the browser HTML-decodes the attribute before JavaScript parses it. Buttons now carry `data-action`/`data-id` and are handled by delegated listeners. Covers Owners, Backends, Users, Bots, Aliases, Settings and both playgrounds.
- `escapeHtml` now also escapes `"` and `'`; it is used inside quoted attributes and previously did not.
- Settings: invite `code`/`used_by`/`created_by` were interpolated unescaped into HTML and an inline handler.
- The embedding playground inserted raw user text into `innerHTML`.

### ♿ Accessibility and polish

- 14 labels in `bots.html` and 6 in `aliases.html` had no `for`; all label/input pairs are now associated, and icon-only buttons have `aria-label`.
- Visible `:focus-visible` outline everywhere (previously used zero times); `prefers-reduced-motion` honoured globally (previously zero).
- Favicon reduced from 644 KB to 6 KB.
- Removed the hard-coded, incorrect "v2.4.0" from the login footer.
- Combobox ARIA on the Compare page (`role=combobox`/`listbox`/`option`, `aria-activedescendant`).

### ✅ Tests

- `tests/test_admin_ui.py` (new): sidebar role gating, one shell for every page, **no inline handlers interpolating data** in any JS or inline script, every dialog is named, `<label for>` targets exist, and the mobile table width override keeps its `!important`. **399 tests passing.**

---

## [0.10.2] - 2026-10-01

### 🔑 Fixed: login returned HTTP 500

Every password check succeeded and then died writing `last_login`:

```
sqlalchemy.exc.StatementError: (builtins.ValueError) Datetime values must have timezone information.
[SQL: UPDATE "user" SET last_login=$1::TIMESTAMP WITH TIME ZONE WHERE "user".id = $2::UUID]
```

All 23 timestamp columns in this schema are `TIMESTAMP WITHOUT TIME ZONE`, and all code writes naive UTC. sqlmodel 0.0.23+ changed a bare `datetime` annotation to map to `UTCDateTime(timezone=True)`, which rejects naive values outright. Because `pyproject.toml` pins only `sqlmodel>=0.0.22`, the change arrived silently through a dependency upgrade — the deployed container had drifted to 0.0.47.

- **Table fields now pin `sa_type=DateTime(timezone=False)`**, matching the existing schema. No migration is required and no stored data changes. `sa_type` is used rather than a `NaiveDatetime` annotation because it works across every sqlmodel version the project supports — `NaiveDatetime` does not exist in 0.0.37 and would have broken the declared floor.
- **Request/response schemas keep bare `datetime`**, so API clients may still send timezone-aware values.
- Replaced the deprecated `datetime.utcnow()` in the settings router with the shared `_utcnow()` helper.

### 🔇 Added: `ENABLE_TELEMETRY` on/off switch

Logs were flooded with OTLP retries against a collector that was never reachable:

```
Transient error HTTPConnectionPool(host='localhost', port=4318): ... [Errno 111] Connection refused ... retrying in 3.71s.
Failed to export metrics batch due to timeout, max retries or shutdown.
```

- **`ENABLE_TELEMETRY` (default `false`) is now the master switch** for all OpenTelemetry/Phoenix export, covering the global provider, `setup_phoenix_telemetry()` and the per-tenant `PhoenixTraceManager` tracers.
- **When off, exporter-free providers are installed explicitly.** Returning early was not sufficient: opentelemetry lazily auto-configures an SDK provider from `OTEL_EXPORTER_OTLP_ENDPOINT` the first time a meter is requested, which is what kept the exporter alive and retrying even with export nominally "off". The OTLP endpoint variables are also cleared and `OTEL_SDK_DISABLED=true` is set.
- **Fixed a wrong endpoint in `docker-compose.yml`**: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`, commented "Points to host". Inside a container `localhost` is the container itself, so nothing ever listened. It is now `host.docker.internal:4318` and overridable.
- Exporter retry chatter is capped at `ERROR` when telemetry *is* enabled, so a collector going away mid-run no longer logs a warning per retry.

### ✅ Tests

- Added `tests/test_datetime_and_telemetry.py`: fails if any table timestamp column becomes timezone-aware (the login-500 regression), if `_utcnow()` stops being naive, or if the telemetry switch stops defaulting to off / stops clearing the OTLP endpoint variables. **329 tests passing.**

### ⚠️ Note

Changing `ENABLE_TELEMETRY` in `.env` requires `docker compose up -d`. `docker restart` reuses the container's existing environment and will appear to ignore the change.

---

## [0.10.1] - 2026-09-21

### 🐌 Fixed: admin UI and connection pool stalling on unreachable backends

Symptom: the Backends page froze, and with it the rest of the admin UI, whenever backends were configured on a network that silently drops packets (firewalled host, dead VLAN).

Root cause: `GET /admin/backends/{name}/health` probed four candidate paths (`/api/tags`, `/v1/models`, `/health`, `/`) **sequentially at a 10s timeout each**. An unreachable host never sends a TCP reset, so every path burned its full timeout — a measured **40.1 seconds per backend**. Meanwhile the request held a pooled DB connection for that entire duration, and the browser held one of its ~6 per-origin sockets. The page fired one health check per backend with no concurrency limit, and the dashboard's SSE stream permanently occupied another socket, so the browser ran out of connections and could not issue *any* further request to the gateway.

- **Bounded the probe budget**: 2s connect timeout with a 6s deadline across all candidate paths combined. Measured worst case for an unreachable backend drops from **40.1s to 6.00s**; eight backends probed concurrently now settle in **6.06s**.
- **The DB session is released before network I/O.** Backend fields are copied off the ORM object and the pooled connection is returned before any probing, so a slow backend can no longer pin a connection from a pool sized `pool_size=10, max_overflow=20, pool_timeout=5`.
- **Client-side health checks are throttled** to 3 concurrent, each with an 8s `AbortController` deadline, so the browser's connection budget is never saturated. A timed-out probe now renders `TIMEOUT` rather than hanging.
- **The SSE metrics stream no longer pins a DB connection.** `GET /admin/metrics/stream` resolves its auth through `get_session`, and FastAPI does not tear down `yield` dependencies until the response completes — which for an SSE stream means never. The session is now closed explicitly before streaming begins, so each open dashboard tab no longer holds a connection for its lifetime.
- **The model federation poller** (every 30s) now uses an explicit 2s connect / 5s read timeout instead of httpx defaults.

### ✅ Tests

- Added `tests/test_backend_health_budget.py`, which fails the build if the probe budget regresses, if the connect timeout grows, or if the deadline stops bounding the candidate-path loop.

---

## [0.10.0] - 2026-09-20

### 🔌 Fully Offline Admin UI — No CDN Dependency

The dashboard previously fetched Tailwind, jQuery, DataTables, marked and highlight.js from public CDNs. On an air-gapped host, behind an egress proxy, or during a CDN outage the UI loaded unstyled and — because DataTables never initialised — was unusable. Every front-end dependency is now vendored under `src/llm_gateway/static/`.

- **Vendored all remaining libraries**: `marked@12.0.2` and `highlight.js@11.9.0` (plus its `github-dark` theme) are served from `/static`. The Tailwind Play CDN script has been removed entirely in favour of the local compiled bundle.
- **Removed `document.write()` CDN fallbacks** for jQuery and DataTables from `base.html`. They defeated the purpose of vendoring and blocked rendering on a slow network.
- **Removed a dead `umap-js` script tag** from the embedding playground. Its CDN URL had been returning **404** and no code referenced the library.
- **Tailwind now compiles locally with `darkMode: 'class'`**. The setting previously lived only in an inline `tailwind.config` consumed by the CDN script, so a local-only build would have silently fallen back to `prefers-color-scheme` and ignored the in-app theme switcher. Vendored `*.min.js` files are excluded from content scanning.
- **Content-Security-Policy tightened to `'self'`**, with `base-uri` and `form-action` added. All five CDN origins were dropped from the whitelist. This makes the fix self-enforcing: a re-added `<script src="https://…">` is now blocked by the browser during development rather than shipping unnoticed.
- **Regression tests** (`tests/test_offline_assets.py`) fail the build if any template references an external origin, uses a `document.write()` fallback, points at a missing `/static` asset, or if the CSP regains an external host.

### 🔐 Session Expiry Handling

Symptom: an admin page left open past the one-hour cookie lifetime raised a bare browser alert reading `DataTables warning: table id=backendsTable - Ajax error`.

- **Authenticated HTML is now sent with `Cache-Control: no-store`.** Without it the browser re-displayed a fully rendered admin page from cache after the session cookie had expired (or after logout); the stale page then fired XHRs the server correctly rejected with 401. This also stops authenticated pages from lingering in the browser cache after sign-out.
- **DataTables `errMode` is set to `none`** with a global `error.dt` handler. A 401/403 now redirects to the login page; any other failure renders an inline, escaped error row stating the actual HTTP status instead of an opaque alert dialog.
- **`fetchWithCsrf()` detects 401** and routes through the same single-shot redirect, so every page shares one session-expiry path.

### 🧹 Fixes

- **Chat playground syntax highlighting**: removed the `highlight` callback passed to `marked.setOptions()`. marked dropped that option in v5, so it had been silently doing nothing; highlighting is applied after rendering via `hljs.highlightElement()`.

---

## [0.9.0] - 2026-09-17

### 🦙 llama.cpp & Granular Model Resilience
- **First-Class llama.cpp Integration**: Native `BackendType.LLAMACPP` provider with automatic alias normalization (`llama.cpp`, `llamacpp`). Registered default `llamacpp` provider in `provider_registry.py`, updated Admin UI with modal options and teal badge styling (`bg-teal-500/10`).
- **Dynamic Model & Context Discovery**: Enhanced `federation_service.py` to probe llama.cpp `/v1/models` with automatic fallback to native `/props`, extracting model IDs and `n_ctx` context window bounds.
- **Granular Model Quarantining**: Adopted path-specific blacklisting from `ollama_proxy_server`. Isolates failing `(backend, model)` routes with tiered cooldown periods (401/403: 1 hour, 429: 5 minutes, 503: 1 minute, 500/502: 30 seconds) so healthy models on the same backend remain fully operational.
- **Ollama Protocol Compatibility**: Added native `/api/version` handshake route for Ollama CLI, OpenWebUI, and third-party tools.

### 🛡️ Model Virtualization & Resilience UI
- **Fallback Chain Modal & Virtualization Fix**: Added the missing `#addChainModal` dialog and its corresponding JavaScript controller logic (`openAddChainModal`, `addChainTargetRow`, `submitCreateChain`) in the Model Virtualization view (`/admin/view/aliases`). Integrated CSRF protection (`window.fetchWithCsrf`) across all alias and fallback chain mutations, and replaced legacy icon classes with native Lucide SVGs.

---

## [0.8.1] - 2026-09-16

### 📜 Licensing

- **AGPL-3.0 License**: Officially relicensed under the GNU Affero General Public License v3.0 (`AGPL-3.0-or-later`), ensuring copyleft protections for network server environments and cloud-hosted LLM gateways. Added full [`LICENSE`](LICENSE) text and updated project metadata.

### 🚀 Production vLLM Integration & Cluster Support

- **vLLM-node1 Cluster Integration**: Added verified production vLLM cluster endpoint (`http://localhost:8000`) featuring 8 models:
  - `llama-3.1-8b` (Chat & Text Completions)
  - `qwen-2.5-14b` (Chat & Text Completions)
  - `llama-3.3-70b` (Large Reasoning & Coding)
  - `llama-4-scout` (High-efficiency Chat)
  - `llava-1.6-34b` (Multimodal Vision-Language)
  - `nemotron-3-nano-30b-a3b` (Chat with Thinking / Chain-of-Thought)
  - `bge-m3` (1024-dimension Dense Vector Embeddings)
  - `qwen-3-30b-a3b` (Chat with Reasoning)
- **Direct & Unified Routing**: Models accessible via both standard unified proxy (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) and direct cluster targeting (`/direct/vllm-node1/...`).

### ⚔️ Side-by-Side Model Arena Enhancements

- **Backend Filter & Searchable Dropdown**: Enhanced the comparison arena (`/admin/view/playground/compare`) with backend-based filtering and instant search dropdowns, adopting developer-centric evaluation patterns from `inference-gateway`.
- **Live Comparison Execution**: Supports simultaneous prompt execution against two disparate models/backends with side-by-side latency (TTFT, total time), token speed, and full markdown rendering.

### 🎨 UI & Icon System Modernization

- **Fallback Chain Modal & Virtualization Fix**: Added the missing `#addChainModal` dialog and its corresponding JavaScript controller logic (`openAddChainModal`, `addChainTargetRow`, `submitCreateChain`) in the Model Virtualization view (`/admin/view/aliases`). Integrated CSRF protection (`window.fetchWithCsrf`) across all alias and fallback chain mutations, and replaced legacy icon classes with native Lucide SVGs.
- **100% Native Lucide SVGs**: Replaced all remaining legacy FontAwesome tags (`fas fa-*`) across Settings (`settings.html`), Spend (`spend.html`), and Chat Playground (`chat_playground.html`) with lightweight native Lucide SVG vectors.
- **Icon Adapter Expansion**: Expanded `faToLucideMap` in `icons.js` to cover all 96+ gateway icons (`palette`, `sun`, `moon`, `monitor`, `fingerprint`, `brain`, `shield-alert`, `flask-conical`, `clipboard-check`, `network`, `sparkles`, `coins`, `arrow-down`, `zap`, `image`), eliminating broken icon fallbacks across all views.
- **Responsive Top Metrics**: Streamlined top dashboard cards by removing the redundant system load card, establishing a balanced 3-column key performance layout.

### 👤 User Administration & Tenant Provisioning

- **Admin User Setup**: Provisioned administrator user `dev@example.com` with `ADMIN` role and superuser privileges.
- **Tenant & Permission Mapping**: Created dedicated Owner `anshu` mapped to Auth User ID, assigned wildcard permissions (`*`) to backend `vllm-node1`, and generated active gateway API keys.

---

## [0.8.0] - 2026-09-16

### 🛡️ Enterprise Security & Multi-Channel Bots

- **Data Protection & PII Masking**: Integrated `PIIService` for runtime detection and redaction of sensitive identifiers (credit cards, SSNs, phone numbers, email addresses, and API secret keys) before forwarding payloads to upstream providers.
- **Prompt Injection Guardrails**: Added `GuardrailsService` with regex-based heuristics and configurable sensitivity thresholds (low, medium, high) to intercept jailbreaks and adversarial inputs.
- **Discord Bot Platform**: Added Discord Interactions webhook handler supporting command dispatch, message verification, and proxy routing to any configured backend.
- **Slack Bot Platform**: Added Slack Events API handler supporting app mentions, direct messages, and team-isolated LLM proxy execution.
- **Side-by-Side Arena (Initial Release)**: Initial introduction of dual-model comparison arena.

---

## [0.7.0] - 2026-09-16

### ⚡ Performance & Semantic Caching

- **Exact Deterministic Caching**: Exact request/response caching powered by Redis with in-memory fallback for deterministic queries (temperature <= 0.1).
- **Semantic Caching Architecture**: Foundation for embedding-based semantic similarity caching utilizing PostgreSQL 18 with `pgvector` cosine similarity indexing.
- **Weighted Latency Balancing**: Added dynamic backend weight and rolling EWMA latency tracking to steer traffic to the fastest healthy providers.
- **Settings UI Expansion**: Added dedicated toggle cards for Performance, Response Caching, System Resilience, and Scope Policy Rules in `/admin/view/settings`.

---

## [0.6.0] - 2026-09-16

### 🚀 Resilience, Financial Governance & Performance

- **Cross-Provider Failover Chains**: Added `FallbackChain` and `try_with_cross_provider_fallback` to automatically reroute requests to secondary providers on 429 rate limit or 5xx outage errors.
- **Model Virtualization Aliases**: Added `ModelAlias` table and resolution service allowing clients to target virtual model aliases (e.g. `smart-model`, `fast-model`) that dynamically map to real backend targets.
- **Financial Governance & Spend Tracking**: Per-request cost metering via `SpendRecord` with built-in model pricing table (OpenAI, Anthropic, Gemini, Groq, local zero-cost). Supports monthly budget caps (`monthly_budget_usd`) on both Owner and API Key levels with HTTP 429 budget enforcement.
- **Spend Dashboard UI**: New interactive financial dashboard at `/admin/view/spend` displaying real-time monthly spend, token counts, and cost breakdowns by Owner and Model.
- **Aliases & Fallback Chains UI**: Dedicated management interface at `/admin/view/aliases` for managing virtual model aliases and target fallback sequences.
- **Response Caching (Redis & In-Memory)**: Deterministic exact response cache (`CachedResponse`) with latest Redis support (`redis:alpine`) and automatic in-memory TTL fallback for temperature <= 0.1 requests.
- **Weighted Load Balancing & Latency Tracking**: Added `LLMBackend.weight` column and rolling latency tracking to balance traffic across candidate backends proportionally to capacity.
- **Enterprise Guardrails & PII Redaction**: Built-in pattern-based prompt injection detection (with configurable sensitivity) and PII scanning/redaction (credit cards, SSNs, emails, API keys).
- **Multi-Channel Bot Support**: Extended bot platform to support Discord (Interactions API / slash commands) and Slack (Events API / app mentions).
- **Side-by-Side Model Arena**: New comparison playground at `/admin/view/playground/compare` allowing simultaneous evaluation of two models with identical prompts and latency/token comparisons.
- **Alembic Migration `004_roadmap_v060`**: Zero-downtime schema evolution adding `fallbackchain`, `modelalias`, `spendrecord`, `cachedresponse`, and budget/weight columns.

---

## [0.5.5] - 2026-09-16

### 🚀 Major Enhancements & Infrastructure

- **PostgreSQL 18 Upgrade**: Default database engine upgraded from PG15 to PostgreSQL 18 (`postgres:18-alpine`). Adapted storage directory layout to `/var/lib/postgresql` and added automated migration script `upgrade_pg_15_to_18.sh`.
- **Branch Convergence**: Consolidated features from `feat/vllm-0.5.1` and `v0.5.4`:
  - Native vLLM backend provider registration in `provider_registry.py`.
  - Non-blocking asynchronous background email dispatch with 5s timeout in `email_service.py`.
  - Path normalization (`clean_target_url`) preventing `/v1/v1` segment duplication.
  - Strict provider isolation in proxy routing preventing unintended fallback leakage.
  - Stateful Telegram bot platform with `/api/v1/bots` webhook routing and token verification.
  - Bidirectional OpenAI <-> Anthropic protocol translation and `<think>` reasoning block extraction.
  - Local fast-path synthetic responses.

### 🧹 Dependency Modernization & Cleanup

- Migrated deprecated `@app.on_event` handlers to modern FastAPI `lifespan` context manager.
- Pruned obsolete and incompatible packages: `passlib[bcrypt]` and `python-jose[cryptography]`.
- Explicitly pinned modern `bcrypt>=4.0.1`, `respx>=0.22.0`, `psycopg2-binary>=2.9.0`, and `aiosqlite>=0.20.0`.
- Updated frontend CDN libraries to latest stable releases with SHA-384 SRI hashes (jQuery 3.7.1, FontAwesome 6.7.2, DOMPurify 3.2.4, DataTables 1.13.11).

---

## [Unreleased] - 2026-04-03

### ✨ Features

- **Dark/Light Mode Theme Toggle**: Full theme support across all 13 UI templates. Users can choose Light, Dark, or System (OS preference) from a 3-way toggle on the Settings page.
  - Tailwind `darkMode: 'class'` configuration with FOUC prevention via inline `<head>` script.
  - Converted hardcoded dark classes (e.g. `bg-slate-900`, `text-white`, `border-slate-800`) to light defaults with `dark:` variants across all templates.
  - Light mode CSS overrides for inline-styled components (header nav, chat playground, embedding playground).
  - Theme preference persisted in `localStorage` with system-preference media query listener.
  - "User Preferences" card added to Settings page with Light / Dark / System toggle (accessible to all user roles).
  - Auth pages (login, register, forgot/reset password) updated with theme-aware gradient backgrounds.

---

## [0.5.0] - 2026-03-28

### 🔒 Security

- **XSS Hardening**: Applied `escapeHtml()` to all DataTable render functions across backends.js, owners.js, users.js, and dashboard.js. IP table now uses DOM API (`createElement`/`textContent`) instead of `innerHTML` interpolation.
- **Endpoint-aware scope checking**: Policy engine no longer hardcodes `"chat"` scope. Each proxy endpoint maps to its required scope (`chat`, `embeddings`, `read_only`). Admin and wildcard (`*`) scopes bypass checks.
- **EMBEDDINGS scope**: Added `APIKeyScope.EMBEDDINGS` to the enum — enables proper scope-based access control for embedding endpoints.

### ⚡ Performance

- **TTL caching layer**: New `cache.py` module. API key validation cached 60s, backend list 10s, system settings 30s. Reduces DB queries per proxy request from 7-8 to 2.
- **HTTPX connection pool**: Persistent `AsyncClient` per backend URL replaces per-request TCP connections. Shutdown hook closes all clients cleanly.
- **Database pool config**: Explicit `pool_size=10`, `max_overflow=20`, `pool_timeout=5`, `pool_pre_ping=True` on the SQLAlchemy engine.
- **Smart dashboard polling**: Metrics polling pauses when browser tab is hidden via `visibilitychange` API.

### 🏗️ Infrastructure

- **Optional Redis rate limiter**: Set `REDIS_URL` to enable cross-worker rate limiting. Falls back to in-process token bucket if Redis is unavailable or not configured.
- **Optional PgBouncer**: Set `PGBOUNCER_URL` for transaction-mode connection pooling via PgBouncer. Falls back to direct `DATABASE_URL` if not set.
- **Production docker-compose**: `docker-compose.prod.yml` with 2 gateway instances behind nginx load balancer. Redis and PgBouncer available via `--profile redis` / `--profile pgbouncer`.
- **Retry-After header**: 429 responses now include RFC 6585 `Retry-After` header with estimated seconds until next token.

### 🐛 Bug Fixes

- **False "PROTOCOL REJECTED" on login**: Tailwind CDN failure caused `.hidden` class to have no effect, making error div always visible. Added local Tailwind CSS build (43KB) as fallback.
- **`/health` intercepted by proxy**: Route ordering fixed — `/health` and `/` now registered before the catch-all `/{path:path}` proxy route.
- **`/favicon.ico` 401**: Added to auth + policy middleware skip lists.
- **PolicyMiddleware noise**: `/auth/*` and `/static/*` paths now skip policy evaluation entirely.

### ♿ Accessibility

- **Keyboard-accessible dropdown**: Playground nav dropdown converted from hover-only `<a>` to `<button>` with `aria-expanded`, `aria-haspopup`, `role="menu"/"menuitem"`, click toggle, and Escape dismiss.
- **Focus trap in modal**: Confirm modal now traps Tab between Cancel/Confirm, dismisses on Escape, and restores focus to trigger element on close.

### 🧪 Tests

- **102 tests passing** (up from ~80).
- New: `test_cache.py` (8 tests), `test_database.py` (3 tests).
- Updated: `test_policy.py` (+4 endpoint-aware scope tests), `test_rate_limit.py` (rewritten for `LocalRateLimiter` + factory), `test_middleware.py` (+5 path skip tests), `test_proxy_helpers.py` (+1 Retry-After header test).

### 🔧 Build

- **Local Tailwind CSS**: `build-css.sh` script + `tailwind.config.js` for building a static 43KB CSS bundle from templates/JS. CDN remains primary; local CSS is the fallback.

---

## [0.4.2] - 2026-03-20

### 🔒 Security

- **CSRF Protection**: Added double-submit cookie CSRF middleware for all cookie-authenticated mutations (`CSRFMiddleware`).
- **XSS Sanitization**: Added DOMPurify via `sanitizeHTML()` for AI-generated markdown content in chat playground.
- **Secret Leakage**: Replaced all `print()` calls with `logging`; removed tokens/URLs from log output.
- **Fail-fast AUTH_SECRET**: App raises `RuntimeError` on startup if `AUTH_SECRET` is missing (no more insecure default).
- **Junk File Removal**: Deleted `cookies.txt`, `*.sql` dumps, backup files, and misplaced test scripts from repository.
- **Error Sanitization**: All HTTP error responses now return generic messages; internal details logged server-side only.

### 🐛 Bug Fixes

- **Owner page modals broken**: Removed `sanitizeHTML()` (DOMPurify) wrapping from trusted app-generated HTML across 8 JS files. DOMPurify was stripping `onclick` handlers, breaking Manage API Keys table, Revoke buttons, Manage Permissions table, and other interactive elements. Kept sanitization only on `marked.parse()` output (AI responses) where XSS protection is needed.
- **Static files 401**: Added `/static` to `AuthMiddleware` skip list so JS/CSS assets load without authentication.
- **UI route type errors**: Removed invalid `Union[RedirectResponse, HTMLResponse]` return type annotations from all UI routes that caused FastAPI startup crash.
- **Admin login failure**: Fixed `is_verified=false` default for seed admin user preventing login.
- **Bare `except:` in telemetry**: Narrowed to specific exception types (`json.JSONDecodeError`, `KeyError`, `TypeError`).
- **Fragile exception check in backends**: Replaced string-based `"IntegrityError" in str(type(e))` with proper `isinstance` check.

### ✨ New Features

- **Pagination**: All list endpoints (`/admin/users`, `/admin/owners`, `/admin/backends`, `/admin/keys`, `/admin/models`, `/admin/invites`) now accept `?skip=0&limit=50` query parameters.
- **Accessibility (a11y)**: Added `aria-label`, `aria-hidden`, `sr-only` labels, `alt` text, and form labels across all 11 templates.
- **Static JS Assets**: Extracted ~3300 lines of inline JavaScript from templates into 10 static files under `static/js/`.

### 🔧 Refactor

- **Telemetry package split**: `telemetry.py` (438 lines) split into `telemetry/` package: `tracing.py`, `metrics.py`, `streaming.py`, `setup.py`.
- **Proxy helpers extracted**: Shared functions (`get_target_backend`, `try_backend_with_fallback`, `_get_all_urls`) moved to `proxy_helpers.py`.
- **Type hints**: Added return type annotations to all public functions across all Python modules.
- **Duplicate code removed**: Deleted duplicate `/admin/metrics` and `/admin/models` endpoints from `settings.py`.
- **Hardcoded template path fixed**: `ui.py` now uses `pathlib.Path` relative to module instead of `/app/src/...`.
- **Exception handlers narrowed**: Replaced `except Exception: pass` with specific catches across proxy, admin, and settings routers.
- **Console cleanup**: Removed `console.log`/`console.error` debug statements; replaced `alert()` with `showToast()` across all templates.
- **Test reorganization**: Moved non-pytest scripts to `tests/scripts/`; consolidated 5 duplicate `logging.Handler` subclasses in `test_audit.py`.
- **Print → logging**: All `print()` calls in `auth/manager.py`, `routers/backends.py`, `routers/owners.py`, `cli.py` replaced with `logger`.

### 🧪 Tests

- **73 new unit tests** (131 total): `test_middleware.py` (31), `test_pagination.py` (5), `test_provider_registry.py` (18), `test_email_service.py` (8), `test_federation_service.py` (13).
- Covers AuthMiddleware, CSRFMiddleware, PolicyMiddleware, pagination params, provider registry CRUD, email service, and federation service for all backend types.

### 📖 Documentation

- Updated `README.md`, `ARCHITECTURE.md`, `API_REFERENCE.md`, `TESTING.md` to reflect all v0.4.2 changes.

---

## [0.3.2] - 2026-03-19

### 🧪 Test Suite

- **57 unit tests passing** in Docker (`python -m pytest tests/ -v --tb=short`).
- **New test files**:
  - `tests/test_audit.py` — AuditLogger identity resolution (API key, SPIFFE, unknown), metadata defaults, singleton
  - `tests/test_proxy_helpers.py` — `get_owner_id_from_request` (6 identity cases), `apply_rate_limit` (allow/429/no-user), `check_owner_permissions` (wildcard, model-restricted, endpoint-blocked, empty whitelist, specific model)
- **Fixed `tests/test_tracking.py`** — rewritten to use `_active_ips`/`_last_cleanup` private state after refactor; added middleware dispatch and stale-IP cleanup tests.

### 🏗️ Infrastructure

- **`pyproject.toml`**: added `[test]` optional-dependencies group (`pytest`, `pytest-asyncio`, `aiosqlite`).
- **`Dockerfile`**: installs `.[test]` so `pytest` is available inside the image.
- **`docker-compose.test.yml`**: new isolated test-only compose service.
- **`docs/TESTING.md`**: new testing guide — how to run, coverage table, fixture reference, adding tests, CI snippet.

---

## [0.3.2-rc1] - 2026-03-19 (refactor / bug-fix batch)

### 🔧 Refactor & Code Quality

- **Shared Proxy Helpers**: Extracted `proxy_helpers.py` with `get_owner_id_from_request`, `apply_rate_limit`, `check_owner_permissions`, and `build_backend_auth_headers` — eliminated ~300 lines of duplication across `proxy.py` and `v2_proxy.py`.
- **Service Modularity**: Split `services/__init__.py` (3 unrelated classes, 185 lines) into focused modules: `config_service.py`, `auth_service.py`, `owner_service.py`. All existing imports unchanged via re-exports.
- **Session Management**: Promoted `sessionmaker` to a single module-level `_async_session` in `database.py`; `middleware.py` now imports it instead of creating its own copy on every request.
- **SQL Logging**: `echo=True` replaced with `SQL_ECHO` env var (defaults to `false`).

### 🔒 Security Fixes

- **Removed Hardcoded Credentials**: Admin email and password removed from `main.py`. Now read from `DEFAULT_ADMIN_EMAIL` and `DEFAULT_ADMIN_PASSWORD` env vars; skips creation with a warning if unset.

### 🐛 Bug Fixes

- **`OwnerService` crash on missing key**: `ENCRYPTION_KEY=None` previously caused a silent `pass` then an `AttributeError` on first use. Now raises `RuntimeError` with a clear message and key-generation instructions at startup.
- **Email login URL bug**: `send_welcome_email` was building login URLs from `EMAILS_FROM_EMAIL` (the sender address). Now uses `APP_BASE_URL` env var.
- **`isinstance(user, object)` always True**: `policy.py` scope check was bypassed because every Python object is an instance of `object`. Fixed to `hasattr(user, "scopes")`.
- **Mutable default argument**: `audit.py` `log()` used `metadata={}` — a classic Python bug where the dict is shared across calls. Fixed to `metadata: dict | None = None`.
- **Duplicate endpoint whitelist check**: `v2_proxy.py` `direct_proxy` checked `allowed_endpoints` twice. Removed the redundant second check.
- **Version mismatch**: Root endpoint returned `"version": "0.3.0"` while the FastAPI app declared `version="0.3.1"`. Both now read from a single `_VERSION` constant.

### ⚠️ Deprecation Fixes

- **`datetime.utcnow()`**: Replaced all uses in `audit.py`, `auth/models.py`, and `services/auth_service.py` with timezone-aware `datetime.now(timezone.utc)`.

### 🏗️ Infrastructure

- **`APP_BASE_URL`** env var added to `config.py` (default: `http://localhost:8000`).
- **`APP_VERSION`** env var read by `telemetry.py` for `service.version` resource attribute (was hardcoded `"0.3.0"`).
- **IP Tracking**: `tracking.py` module-level globals encapsulated; `get_active_ips()` function preserved for router compatibility.

### 📦 New Files

```
src/llm_gateway/
├── proxy_helpers.py             # Shared proxy utilities
└── services/
    ├── config_service.py        # ConfigService (extracted)
    ├── auth_service.py          # AuthService (extracted)
    └── owner_service.py         # OwnerService (extracted)
```

### 🔑 New Environment Variables

| Variable                 | Default                 | Purpose                                         |
| ------------------------ | ----------------------- | ----------------------------------------------- |
| `DEFAULT_ADMIN_EMAIL`    | —                       | Email for default admin user created on startup |
| `DEFAULT_ADMIN_PASSWORD` | —                       | Password for default admin user                 |
| `APP_BASE_URL`           | `http://localhost:8000` | Base URL used in email templates                |
| `APP_VERSION`            | `0.3.1`                 | Service version reported in telemetry           |
| `SQL_ECHO`               | `false`                 | Enable SQLAlchemy query logging                 |

---

## [0.3.1] - 2026-03-16

### ✨ New Features

- **Premium UI Components**: Integrated a global, glassmorphism-style **Confirmation Modal** and **Toast Notification** system across all administrative pages.
- **Improved Feedback**: Replaced standard browser `confirm()` and `alert()` calls with the new premium components for a more integrated user experience.

### 🔧 Improvements

- **Transaction Consistency**: Consolidated database transaction management (`session.commit()` and `session.refresh()`) into the router layer.
- **Service Decoupling**: Removed internal session commits from `ConfigService` and `OwnerService` to ensure reliable per-request transaction atomicity.

### 🐛 Bug Fixes

- **Backend Deletion Persistence**: Fixed a critical issue where backend and owner deletions were not persisting in the database due to missing transaction commits in the router layer.
- **Backends Page Fixes**: Resolved broken delete buttons and inconsistent UI state updates on the backends management page.

---

## [0.3.0] - 2026-03-09

### 🎉 Major Release: LLM Gateway v0.3.0

This is a complete rewrite and modernization of the original `llm-secure-gateway` project, adding enterprise-grade multi-tenancy, admin UI, enhanced observability, and improved routing.

---

### ⬆️ Breaking Changes

| Change                      | Migration                                           |
| --------------------------- | --------------------------------------------------- |
| Database schema redesigned  | Run new migrations, see UPGRADE.md                  |
| Config from env → database  | Register backends via Admin API                     |
| Auth moved to FastAPI-Users | New login endpoints at `/auth/jwt/...`              |
| V1 proxy deprecated         | Use V2 routes: `/direct/{backend}` or `/{provider}` |

---

### ✨ New Features

#### Multi-Tenancy & Ownership

- **Owner Model** - Create owners (users/projects) to group API keys
- **Provider Keys** - Store per-owner LLM provider API keys (encrypted)
- **API Key Scoping** - Keys scoped to specific owners with cascade delete
- **Owner Types** - `user` and `project` types for different use cases

#### Admin UI Dashboard

- **Web Interface** - Full admin panel at `/ui/`
- **Backend Management** - Add, edit, delete LLM backends
- **Owner Management** - Create owners, view API keys
- **Key Generation** - Create and revoke API keys
- **Real-time Metrics** - View active IPs, request stats

#### V2 Routing System

- **Direct Routing** - `/direct/{backend_name}/{path}` for explicit backend
- **Dynamic Routing** - `/{provider}/{path}` for provider-based routing
- **Endpoint Whitelisting** - Restrict allowed paths per backend
- **Improved Streaming** - Better SSE handling with telemetry

#### Enhanced Observability

- **Phoenix Integration** - Native Arize Phoenix OTEL support
- **OpenInference** - LLM-specific semantic conventions
- **Multi-tenant Tracing** - Per-owner project names in Phoenix
- **Token Tracking** - Capture prompt/completion token counts
- **Message Capture** - Input/output messages in traces (JSON array format)

#### Security Enhancements

- **Encrypted Provider Keys** - Fernet encryption for stored API keys
- **Cookie + JWT Auth** - Dual authentication backends
- **Password Reset** - Built-in password reset flow
- **Admin Roles** - Role-based access control

#### Infrastructure

- **IP Tracking** - Real-time active IP monitoring
- **Email Notifications** - Rate limit alerts, password reset
- **CLI Tool** - `cli.py` for admin operations
- **Performance Tests** - `perf.sh` and `stress_test.sh`

---

### 🔧 Improvements

#### Code Architecture

- **Services Package** - Refactored from single `services.py` to package
- **Modular Routers** - Split into `admin`, `proxy`, `v2_proxy`, `ui`, `auth_aux`
- **Type Safety** - Improved type hints throughout
- **Response Models** - Separate `OwnerResponse` for clean serialization

#### Database

- **New Tables** - `owner`, `providerkey` added
- **Relationships** - Proper FK relationships with cascades
- **Indexes** - Added indexes on frequently queried fields
- **Constraints** - Unique constraints for owner+provider keys

#### Telemetry

- **Reduced 401 Errors** - Fixed duplicate OTLP exporter without auth
- **Graceful Fallbacks** - Console exporter if OTLP endpoint unreachable
- **Dynamic Projects** - Tenant-specific Phoenix project names

---

### 🗑️ Removed

- Legacy middleware directory structure (consolidated)
- Old auth router (replaced with FastAPI-Users)
- Hardcoded endpoint configurations

---

### 📊 Comparison: V0.1.0 vs V0.3.0

| Feature            | V0.1.0 (llm-secure-gateway) | V0.3.0 (llm-gateway-v3)    |
| ------------------ | --------------------------- | -------------------------- |
| Multi-tenancy      | ❌                          | ✅ Owners + Provider Keys  |
| Admin UI           | ❌                          | ✅ Full Dashboard          |
| Routing            | Single v1/chat              | Direct + Dynamic           |
| Telemetry          | Basic OTEL                  | Phoenix + OpenInference    |
| Provider Keys      | ❌                          | ✅ Encrypted per-owner     |
| Endpoint Whitelist | ❌                          | ✅ Per-backend             |
| IP Tracking        | ❌                          | ✅ Real-time               |
| Auth               | Basic JWT                   | FastAPI-Users (Cookie+JWT) |
| Version            | 0.1.0                       | 0.3.0                      |

---

### 📁 File Changes Summary

#### New Files

```
src/llm_gateway/
├── cli.py               # Admin CLI tool
├── config.py            # Configuration management
├── provider_registry.py # Provider registration
├── tracking.py          # IP tracking middleware
├── templates/           # 15 HTML templates for Admin UI
├── services/            # Refactored services package
│   └── __init__.py
│   └── email_service.py
└── routers/
    ├── v2_proxy.py      # V2 routing (direct/dynamic)
    ├── ui.py            # Admin UI routes
    └── auth_aux.py      # Auth helper routes
```

#### Modified Files

```
main.py          - Version 0.3.0, new routers, telemetry setup
models.py        - Added Owner, OwnerType, ProviderKey, OwnerResponse
telemetry.py     - Phoenix + OpenInference integration
middleware.py    - Consolidated from directory
database.py      - Updated for new models
```

---

### 🧪 Testing

New test infrastructure:

- `tests/test_phoenix.py` - Telemetry tests
- `tests/verify_gateway.py` - E2E verification
- `tests/perf.sh` - Performance benchmarks
- `tests/stress_test.sh` - Load testing

---

## [0.1.0] - 2026-01-01

### Initial Release (llm-secure-gateway)

- Basic API key authentication
- Single backend proxy
- SPIFFE/mTLS support (SPIRE integration)
- Policy engine foundation
- Basic OTEL telemetry
