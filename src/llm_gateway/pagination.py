from fastapi import Depends, Query


def pagination_params(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=1000, description="Max records to return"),
) -> tuple[int, int]:
    return skip, limit
