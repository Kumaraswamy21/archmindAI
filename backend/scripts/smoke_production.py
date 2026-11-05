"""Manual deployment smoke checks; test writes require an explicit flag."""

import argparse
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx


def _origin(name: str) -> str:
    value = os.environ.get(name, "").rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit(f"{name} must be an HTTP(S) origin.")
    if parsed.scheme != "https" and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit(f"{name} must use HTTPS for a nonlocal host.")
    return value


def _json(response: httpx.Response, label: str, expected: int = 200) -> dict:
    if response.status_code != expected:
        try:
            code = response.json().get("error", {}).get("code", "HTTP_ERROR")
        except ValueError:
            code = "HTTP_ERROR"
        raise RuntimeError(f"{label}: HTTP {response.status_code} ({code}).")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--functional",
        action="store_true",
        help="Run registration and indexing checks.",
    )
    parser.add_argument(
        "--allow-test-writes",
        action="store_true",
        help="Permit test repository registration and indexing.",
    )
    args = parser.parse_args()
    if args.functional and not args.allow_test_writes:
        raise SystemExit("Functional checks require --allow-test-writes.")
    api = _origin("ARCHMIND_SMOKE_API_URL")
    token = os.environ.get("ARCHMIND_SMOKE_BEARER_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        _json(client.get(f"{api}/api/v1/health"), "health")
        print("health: passed")
        _json(client.get(f"{api}/api/v1/ready"), "readiness")
        print("readiness: passed")
        anonymous = client.get(f"{api}/api/v1/repositories")
        if anonymous.status_code != 401:
            raise RuntimeError(
                "authentication: anonymous repository access must return HTTP 401."
            )
        if not token:
            raise SystemExit(
                "Set ARCHMIND_SMOKE_BEARER_TOKEN for authenticated checks."
            )
        _json(
            client.get(f"{api}/api/v1/repositories", headers=headers),
            "authenticated listing",
        )
        print("authentication: passed")
        if not args.functional:
            return

        alias = os.environ.get("ARCHMIND_SMOKE_TEST_ALIAS", "")
        question = os.environ.get("ARCHMIND_SMOKE_QUESTION", "")
        if not alias or not question:
            raise SystemExit(
                "Set ARCHMIND_SMOKE_TEST_ALIAS and ARCHMIND_SMOKE_QUESTION."
            )
        name = "smoke-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        registered = _json(
            client.post(
                f"{api}/api/v1/repositories",
                headers=headers,
                json={"name": name, "source_alias": alias},
            ),
            "registration",
            201,
        )
        repository_id = registered["repository_id"]
        print("registration: passed")
        queued = _json(
            client.post(
                f"{api}/api/v1/repositories/{repository_id}/index", headers=headers
            ),
            "index queue",
            202,
        )
        job_id = queued["job_id"]
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            job = _json(
                client.get(
                    f"{api}/api/v1/repositories/{repository_id}/index/jobs/{job_id}",
                    headers=headers,
                ),
                "index status",
            )
            if job["status"] == "COMPLETED":
                break
            if job["status"] in {"FAILED", "CANCELLED"}:
                raise RuntimeError(
                    f"indexing: {job['status']} ({job.get('error_code')})."
                )
            time.sleep(3)
        else:
            raise RuntimeError("indexing: timed out after 10 minutes.")
        print("indexing: passed")
        hybrid = _json(
            client.post(
                f"{api}/api/v1/search/hybrid",
                headers=headers,
                json={"repository_id": repository_id, "query": question, "top_k": 5},
            ),
            "hybrid retrieval",
        )
        if hybrid.get("repository_id") != repository_id or not hybrid.get("results"):
            raise RuntimeError("hybrid retrieval: no scoped results.")
        print("hybrid retrieval: passed")
        analysis = _json(
            client.post(
                f"{api}/api/v1/agents/analyze",
                headers=headers,
                json={"repository_id": repository_id, "question": question, "top_k": 5},
            ),
            "agent analysis",
        )
        citations = analysis.get("citations", [])
        if (
            analysis.get("repository_id") != repository_id
            or analysis.get("verification_status") != "CITATION_VERIFIED"
            or not citations
        ):
            raise RuntimeError("citation verification: no verified scoped citations.")
        for citation in citations:
            source = _json(
                client.get(
                    f"{api}/api/v1/repositories/{repository_id}/files/content",
                    headers=headers,
                    params={"path": citation["file_path"]},
                ),
                "citation source",
            )
            if (
                source.get("repository_id") != repository_id
                or citation["start_line"] < 1
            ):
                raise RuntimeError(
                    "citation verification: source scope or line is invalid."
                )
        print("agent analysis and citations: passed")
        frontend = _origin("ARCHMIND_SMOKE_FRONTEND_URL")
        cookie = os.environ.get("ARCHMIND_SMOKE_SESSION_COOKIE", "")
        browser_headers = {"Cookie": cookie} if cookie else headers
        project = _json(
            client.get(
                f"{frontend}/api/v1/repositories/{repository_id}",
                headers=browser_headers,
            ),
            "frontend API integration",
        )
        if project.get("repository_id") != repository_id:
            raise RuntimeError("frontend API integration: repository mismatch.")
        print("frontend API integration: passed")


if __name__ == "__main__":
    main()
