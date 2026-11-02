"""Assertions for the shared public API error envelope."""

from uuid import UUID


def legacy_error(response):
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "request_id", "details"}
    assert str(UUID(error["request_id"])) == error["request_id"]
    assert response.headers["X-Request-ID"] == error["request_id"]
    return {"error": {"code": error["code"], "message": error["message"]}}
