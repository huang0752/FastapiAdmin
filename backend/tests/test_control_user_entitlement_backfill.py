from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.scripts import backfill_user_entitlements as backfill_module
from app.scripts.backfill_user_entitlements import (
    build_parser,
    control_preview_record,
    eligible_for_mode,
    needs_backfill,
    page_coordinates,
    require_central_membership,
    validate_args,
)


def test_backfill_defaults_to_read_only_preview() -> None:
    args = build_parser().parse_args([])

    validate_args(args)

    assert args.preview is True
    assert args.enqueue is False
    assert args.retry_failed is False
    assert args.status is False
    assert args.batch_size == 100
    assert args.after_id == 0


@pytest.mark.parametrize("flag", ["--enqueue", "--retry-failed"])
def test_mutating_backfill_modes_must_be_explicit(flag: str) -> None:
    args = build_parser().parse_args([flag, "--batch-size", "23", "--after-id", "41"])

    validate_args(args)

    assert args.preview is False
    assert args.batch_size == 23
    assert args.after_id == 41


def test_backfill_modes_are_mutually_exclusive() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--enqueue", "--retry-failed"])


@pytest.mark.parametrize(
    "argv",
    [
        ["--batch-size", "0"],
        ["--after-id", "-1"],
    ],
)
def test_backfill_rejects_unsafe_batch_boundaries(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)

    with pytest.raises(ValueError):
        validate_args(args)


def test_only_uninitialized_grants_need_backfill_so_repeat_runs_are_idempotent() -> None:
    legacy = SimpleNamespace(
        sync_version=0,
        last_event_id=None,
        sync_status="pending",
        desired_state="active",
        status=0,
    )
    migrated_active = SimpleNamespace(
        sync_version=1,
        last_event_id=None,
        sync_status="pending",
        desired_state="active",
        status=0,
    )
    migrated_inactive = SimpleNamespace(
        sync_version=1,
        last_event_id=None,
        sync_status="succeeded",
        desired_state="inactive",
        status=1,
    )
    fresh_inactive = SimpleNamespace(
        sync_version=0,
        last_event_id=None,
        sync_status="succeeded",
        desired_state="inactive",
        status=1,
    )
    already_prepared = SimpleNamespace(sync_version=1, last_event_id="evt-1", sync_status="pending")
    failed = SimpleNamespace(sync_version=2, last_event_id="evt-2", sync_status="failed")

    assert needs_backfill(legacy) is True
    assert needs_backfill(migrated_active) is True
    assert needs_backfill(migrated_inactive) is False
    assert needs_backfill(fresh_inactive) is False
    assert needs_backfill(already_prepared) is False
    assert needs_backfill(failed) is False
    assert eligible_for_mode(legacy, retry_failed=False) is True
    assert eligible_for_mode(already_prepared, retry_failed=False) is False
    assert eligible_for_mode(failed, retry_failed=True) is True
    assert eligible_for_mode(already_prepared, retry_failed=True) is False


def test_every_mode_can_report_processed_ids_and_safe_next_cursor() -> None:
    rows = [SimpleNamespace(grant=SimpleNamespace(id=42)), SimpleNamespace(grant=SimpleNamespace(id=51))]

    assert page_coordinates(rows, processed_grant_ids=[42]) == {
        "processed_grant_ids": [42],
        "next_after_id": 51,
    }
    assert page_coordinates([], processed_grant_ids=[]) == {
        "processed_grant_ids": [],
        "next_after_id": None,
    }


def test_control_preview_uses_target_tenant_identity_shared_with_products() -> None:
    record = control_preview_record(
        grant=SimpleNamespace(
            id=7,
            desired_state="active",
            sync_status="succeeded",
            sync_version=3,
            last_event_id="evt",
        ),
        opening=SimpleNamespace(target_tenant_code="product-tenant-a"),
        application=SimpleNamespace(code="trace"),
        user=SimpleNamespace(uuid="central-user-1"),
        membership=SimpleNamespace(role="member"),
    )

    assert record["tenant_code"] == "product-tenant-a"
    assert record["application_code"] == "trace"
    assert record["central_user_uuid"] == "central-user-1"
    assert record["central_membership_role"] == "member"
    assert record["principal_role"] == "member"


def test_missing_central_membership_stays_visible_but_enqueue_fails_closed() -> None:
    record = control_preview_record(
        grant=SimpleNamespace(
            id=8,
            desired_state="active",
            sync_status="pending",
            sync_version=1,
            last_event_id=None,
            status=0,
        ),
        opening=SimpleNamespace(target_tenant_code="product-tenant-b"),
        application=SimpleNamespace(code="trace"),
        user=SimpleNamespace(uuid="central-user-2"),
        membership=None,
    )

    assert record["central_membership_role"] is None
    assert record["principal_role"] is None
    with pytest.raises(RuntimeError, match="中央租户成员关系"):
        require_central_membership(SimpleNamespace(grant=SimpleNamespace(id=8), membership=None))
    with pytest.raises(RuntimeError, match="中央租户成员关系"):
        require_central_membership(
            SimpleNamespace(
                grant=SimpleNamespace(id=8),
                membership=SimpleNamespace(role="auditor"),
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("argv", "expected_mode"),
    [
        ([], "preview"),
        (["--status"], "status"),
        (["--enqueue"], "enqueue"),
        (["--retry-failed"], "retry-failed"),
    ],
)
async def test_every_backfill_mode_returns_pagination_metadata(
    argv: list[str],
    expected_mode: str,
    monkeypatch,
) -> None:
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    row = SimpleNamespace(
        grant=SimpleNamespace(id=42, sync_status="failed"),
        export=lambda: {"grant_id": 42},
    )

    async def load_rows(*_args, **_kwargs):
        return [row]

    async def prepare(*_args, **_kwargs):
        return [101], [42]

    async def publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_module, "async_db_session", FakeSession)
    monkeypatch.setattr(backfill_module, "_load_rows", load_rows)
    monkeypatch.setattr(backfill_module, "_prepare", prepare)
    monkeypatch.setattr(backfill_module, "publish_entitlement_tasks", publish)

    result = await backfill_module.run(build_parser().parse_args(argv))

    assert result["mode"] == expected_mode
    assert result["processed_grant_ids"] == [42]
    assert result["next_after_id"] == 42
