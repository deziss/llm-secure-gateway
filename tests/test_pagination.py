"""
Unit tests for the pagination utility module.

pagination_params uses FastAPI Query() defaults, so when called directly
(not via dependency injection) the raw Query objects are returned.
We test that the function signature is correct and returns a 2-tuple.
For actual validation (ge, le constraints), FastAPI handles that at
request time. Here we verify the contract.
"""
import pytest


class TestPaginationParams:
    def test_default_returns_tuple(self):
        from llm_gateway.pagination import pagination_params
        result = pagination_params()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_custom_values_passthrough(self):
        from llm_gateway.pagination import pagination_params
        skip, limit = pagination_params(skip=10, limit=25)
        assert skip == 10
        assert limit == 25

    def test_explicit_zero_skip(self):
        from llm_gateway.pagination import pagination_params
        skip, limit = pagination_params(skip=0, limit=50)
        assert skip == 0
        assert limit == 50

    def test_limit_one(self):
        from llm_gateway.pagination import pagination_params
        skip, limit = pagination_params(skip=0, limit=1)
        assert limit == 1

    def test_large_values(self):
        from llm_gateway.pagination import pagination_params
        skip, limit = pagination_params(skip=9999, limit=1000)
        assert skip == 9999
        assert limit == 1000
