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

- **AGPL-3.0 License**: Officially relicensed under the GNU Affero General Public License v3.0 (`AGPL-3.0-or-later`), ensuring copyleft protections for network server environments and cloud-hosted LLM gateways. Added full [`LICENSE`](file:///home/anshukushwaha/Desktop/learn/llm-secure-gateway/LICENSE) text and updated project metadata.

### 🚀 Production vLLM Integration & Cluster Support

- **vLLM-42 Cluster Integration**: Added verified production vLLM cluster endpoint (`http://localhost:13313`) featuring 8 models:
  - `llama-3.1-8b` (Chat & Text Completions)
  - `qwen-2.5-14b` (Chat & Text Completions)
  - `llama-3.3-70b` (Large Reasoning & Coding)
  - `llama-4-scout` (High-efficiency Chat)
  - `llava-1.6-34b` (Multimodal Vision-Language)
  - `nemotron-3-nano-30b-a3b` (Chat with Thinking / Chain-of-Thought)
  - `bge-m3` (1024-dimension Dense Vector Embeddings)
  - `qwen-3-30b-a3b` (Chat with Reasoning)
- **Direct & Unified Routing**: Models accessible via both standard unified proxy (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) and direct cluster targeting (`/direct/vllm-42/...`).

### ⚔️ Side-by-Side Model Arena Enhancements

- **Backend Filter & Searchable Dropdown**: Enhanced the comparison arena (`/admin/view/playground/compare`) with backend-based filtering and instant search dropdowns, adopting developer-centric evaluation patterns from `inference-gateway`.
- **Live Comparison Execution**: Supports simultaneous prompt execution against two disparate models/backends with side-by-side latency (TTFT, total time), token speed, and full markdown rendering.

### 🎨 UI & Icon System Modernization

- **Fallback Chain Modal & Virtualization Fix**: Added the missing `#addChainModal` dialog and its corresponding JavaScript controller logic (`openAddChainModal`, `addChainTargetRow`, `submitCreateChain`) in the Model Virtualization view (`/admin/view/aliases`). Integrated CSRF protection (`window.fetchWithCsrf`) across all alias and fallback chain mutations, and replaced legacy icon classes with native Lucide SVGs.
- **100% Native Lucide SVGs**: Replaced all remaining legacy FontAwesome tags (`fas fa-*`) across Settings (`settings.html`), Spend (`spend.html`), and Chat Playground (`chat_playground.html`) with lightweight native Lucide SVG vectors.
- **Icon Adapter Expansion**: Expanded `faToLucideMap` in `icons.js` to cover all 96+ gateway icons (`palette`, `sun`, `moon`, `monitor`, `fingerprint`, `brain`, `shield-alert`, `flask-conical`, `clipboard-check`, `network`, `sparkles`, `coins`, `arrow-down`, `zap`, `image`), eliminating broken icon fallbacks across all views.
- **Responsive Top Metrics**: Streamlined top dashboard cards by removing the redundant system load card, establishing a balanced 3-column key performance layout.

### 👤 User Administration & Tenant Provisioning

- **Admin User Setup**: Provisioned administrator user `kushawahaanshu8858@gmail.com` with `ADMIN` role and superuser privileges.
- **Tenant & Permission Mapping**: Created dedicated Owner `anshu` mapped to Auth User ID, assigned wildcard permissions (`*`) to backend `vllm-42`, and generated active gateway API keys.

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
