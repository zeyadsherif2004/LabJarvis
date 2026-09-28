"""An ordinary protocol travels upload -> flagged draft -> review -> approval over HTTP.

The importer may leave questions for the reviewer, but the server - not the
browser - is what guarantees an unanswered question or a placeholder never
reaches a running session.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.settings import Settings

PROTOCOL = """# Buffer exchange

Written for the teaching lab.

## Materials
- PBS
- Spin column

## Procedure
1. Add 1-2 mL PBS to the column.
2. Centrifuge for 2 min.
3. Discard the flow-through.
"""


def _client(tmp_path) -> TestClient:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'import-review.db'}",
        retain_transcripts=False,
        application_commit="test-commit",
    )
    return TestClient(create_app(settings, create_schema=True))


def _acknowledged(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**step, "flags": [{**flag, "acknowledged": True} for flag in step["flags"]]}
        for step in steps
    ]


def test_markdown_upload_is_flagged_preserved_and_gated_until_reviewed(tmp_path) -> None:
    with _client(tmp_path) as client:
        uploaded = client.post(
            "/api/v1/protocols/upload",
            files={"file": ("buffer-exchange.md", PROTOCOL.encode(), "text/markdown")},
        )
        assert uploaded.status_code == 201, uploaded.text
        version = uploaded.json()["version"]
        version_id = version["id"]
        steps = version["steps"]
        assert [step["id"] for step in steps] == ["step-1", "step-2", "step-3"]
        assert steps[0]["requirements"][0]["name"] == "pbs"
        assert steps[0]["requirements"][0]["quantity"] is None
        assert [flag["code"] for flag in steps[0]["flags"]] == ["QUANTITY_RANGE"]
        assert steps[0]["flags"][0]["acknowledged"] is False
        assert steps[1]["duration_seconds"] == 120

        source = client.get(f"/api/v1/protocol-versions/{version_id}/source").json()
        assert "1. Add 1-2 mL PBS to the column." in source["text"]
        assert {(item["reason"], item["section"]) for item in source["unassigned_text"]} == {
            ("OUTSIDE_STEPS", "Buffer exchange"),
            ("NON_PROCEDURE_SECTION", "Materials"),
        }

        blocked = client.post(
            f"/api/v1/protocol-versions/{version_id}/approve",
            headers={"If-Match": '"0"'},
        )
        assert blocked.status_code == 422
        assert blocked.json()["code"] == "PROTOCOL_FLAGS_UNRESOLVED"

        # A reviewer-added step that still holds placeholder text is also refused,
        # even with every flag acknowledged.
        placeholder = {
            **steps[2],
            "id": "review-added",
            "number": 4,
            "title": "Operator-added step",
            "instruction": "Review and replace this instruction before approval.",
            "flags": [],
        }
        with_placeholder = client.post(
            f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
            headers={"If-Match": '"0"', "Idempotency-Key": "placeholder-attempt"},
            json={"steps": [*_acknowledged(steps), placeholder]},
        )
        assert with_placeholder.status_code == 422
        assert with_placeholder.json()["code"] == "PROTOCOL_PLACEHOLDER_UNRESOLVED"

        # Nothing was persisted by the refused attempt.
        unchanged = client.get(f"/api/v1/protocol-versions/{version_id}").json()
        assert unchanged["revision"] == 0
        assert unchanged["status"] == "DRAFT"

        reviewed = _acknowledged(steps)
        reviewed[0]["requirements"][0] = {
            **reviewed[0]["requirements"][0],
            "quantity": "1.5",
            "unit": "mL",
        }
        started = client.post(
            f"/api/v1/protocol-versions/{version_id}/review-approve-and-start",
            headers={"If-Match": '"0"', "Idempotency-Key": "reviewed"},
            json={"steps": reviewed},
        )
        assert started.status_code == 201, started.text
        approved = started.json()["version"]
        assert approved["status"] == "APPROVED"
        assert approved["steps"][0]["flags"][0]["acknowledged"] is True
        assert approved["steps"][0]["requirements"][0]["quantity"] == "1.5"
        assert started.json()["session"]["state"] == "READY"

        reloaded = client.get(f"/api/v1/protocol-versions/{version_id}").json()
        assert reloaded["steps"][0]["flags"] == approved["steps"][0]["flags"]


def test_plain_text_protocol_upload_is_accepted(tmp_path) -> None:
    with _client(tmp_path) as client:
        uploaded = client.post(
            "/api/v1/protocols/upload",
            files={
                "file": ("wash.txt", b"Procedure\n1. Wash the plate.\n2. Dry it.", "text/plain")
            },
        )

    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["title"] == "wash"
    assert [step["instruction"] for step in uploaded.json()["version"]["steps"]] == [
        "Wash the plate.",
        "Dry it.",
    ]


def test_a_text_upload_that_is_not_text_is_unsupported(tmp_path) -> None:
    with _client(tmp_path) as client:
        rejected = client.post(
            "/api/v1/protocols/upload",
            files={"file": ("renamed.txt", b"%PDF-1.7 binary", "text/plain")},
        )

    assert rejected.status_code == 415
    assert rejected.json()["code"] == "UNSUPPORTED_DOCUMENT"
