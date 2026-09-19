# Contributing to LLM Secure Gateway

Thank you for your interest in contributing to LLM Secure Gateway! We welcome contributions from the community to make AI access control, performance, and security better for everyone.

---

## Code of Conduct

All contributors are expected to adhere to our [Code of Conduct](CODE_OF_CONDUCT.md). Please treat all participants with respect and professionalism.

---

## Development Setup

### 1. Prerequisites
- **Docker & Docker Compose** (Recommended)
- **Python 3.14+**
- **Git**

### 2. Local Environment
```bash
# Fork & clone the repository
git clone https://github.com/deziss/llm-secure-gateway.git
cd llm-secure-gateway

# Copy sample configuration
cp .env.example .env

# Start development stack
docker compose up -d
```

Access the Admin Dashboard at `http://localhost:6130/admin/dashboard` and API docs at `http://localhost:6130/docs`.

---

## Testing Standards

All pull requests must maintain or increase test coverage. We enforce zero-regression testing before merging.

```bash
# Run unit and integration tests inside Docker
docker run --rm \
  -e ENCRYPTION_KEY=dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI= \
  -e AUTH_SECRET=test-auth-secret-32-chars-long-0123456789 \
  -e DATABASE_URL="sqlite+aiosqlite:///:memory:" \
  -e REDIS_URL="" \
  llm-gateway:v0.9.0-py314 pytest tests/ -v
```

---

## Pull Request Guidelines

1. **Branching**: Create a feature branch off `main` (e.g. `feat/new-provider` or `fix/quarantine-timeout`).
2. **Commit Messages**: Follow [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat(...)`: A new feature
   - `fix(...)`: A bug fix
   - `docs(...)`: Documentation changes
   - `refactor(...)`: Code restructuring without behavioral changes
   - `test(...)`: Adding or updating test cases
3. **Documentation**: If adding or altering an API route or backend feature, update [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) and [`README.md`](README.md).
4. **Security**: Ensure no sensitive keys, credentials, or personal tokens are committed.
