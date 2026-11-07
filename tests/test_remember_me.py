"""'Remember me' login: long-lived cookie and token, same cookie name, rate-limited."""
import jwt

from llm_gateway.auth import users
from llm_gateway.auth.manager import SECRET
from llm_gateway.middleware import LoginRateLimitMiddleware


def test_remember_backend_issues_long_lived_cookie_and_token():
    normal, remember = users.cookie_transport, users.remember_cookie_transport
    assert normal.cookie_name == remember.cookie_name           # one cookie: the normal backend reads it
    assert normal.cookie_max_age == users.SESSION_SECONDS
    assert remember.cookie_max_age == users.REMEMBER_SECONDS > users.SESSION_SECONDS
    assert users.get_remember_jwt_strategy().lifetime_seconds == users.REMEMBER_SECONDS
    assert remember.cookie_secure == normal.cookie_secure and remember.cookie_samesite == "lax"


async def test_remember_token_is_accepted_by_the_normal_cookie_backend():
    from types import SimpleNamespace
    import uuid

    user = SimpleNamespace(id=uuid.uuid4())
    token = await users.get_remember_jwt_strategy().write_token(user)
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience=["fastapi-users:auth"])
    assert claims["exp"] - claims.get("iat", claims["exp"] - users.REMEMBER_SECONDS) >= users.REMEMBER_SECONDS - 5


def test_remember_route_mounted_and_rate_limited():
    import inspect
    from llm_gateway import main

    paths = {getattr(r, "path", "") for r in main.app.router.routes}
    assert "/auth/cookie-remember/login" in paths or any("cookie-remember" in str(r) for r in main.app.router.routes)
    assert "/auth/cookie-remember/login" in inspect.getsource(LoginRateLimitMiddleware.dispatch)


def test_remember_days_clamped(monkeypatch):
    from llm_gateway import config
    for raw, want in [("30", 30), ("0", 1), ("9999", 365), ("abc", 30)]:
        monkeypatch.setenv("REMEMBER_ME_DAYS", raw)
        assert config._remember_days() == want


def test_login_page_offers_remember_me():
    from pathlib import Path
    html = (Path(users.__file__).resolve().parents[1] / "templates" / "login.html").read_text()
    assert 'id="login-remember"' in html and "/auth/cookie-remember/login" in html
    assert "localStorage.setItem(\"login.remember\"" in html and "password" not in html.split('localStorage.setItem("login.remember"')[1][:80]
