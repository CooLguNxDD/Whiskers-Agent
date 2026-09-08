"""Unit tests for OperationCatalog (revision, atomic publish, scope filter)."""

from __future__ import annotations

import os

from core.route_registry.operation_catalog import (
    OperationCatalog,
    get_operation_catalog,
    _reset_operation_catalog_for_tests,
)
from core.route_registry.operation_descriptor import (
    AccessClass,
    Visibility,
    OperationDescriptor,
    UiContribution,
)
from core.route_registry.route_descriptor import RouteDescriptor
from core.route_registry import RouteRegistry


def _op(
    plugin_id: str,
    op_id: str,
    *,
    visibility: Visibility = Visibility.AUTHENTICATED,
    required_scopes: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> OperationDescriptor:
    return OperationDescriptor(
        plugin_id=plugin_id,
        operation_id=op_id,
        description=f"{plugin_id}/{op_id}",
        input_schema={"type": "object"},
        visibility=visibility,
        required_scopes=required_scopes,
        tags=tags,
        access=AccessClass.READ,
    )


def test_publish_owner_replaces_entire_set() -> None:
    cat = OperationCatalog()
    cat.publish_owner("p1", [_op("p1", "a"), _op("p1", "b")])
    assert len(cat) == 2
    cat.publish_owner("p1", [_op("p1", "c")])
    assert len(cat) == 1
    assert cat.get("p1", "a") is None
    assert cat.get("p1", "c") is not None


def test_revision_bumps_on_publish_and_remove() -> None:
    cat = OperationCatalog()
    assert cat.revision == 0
    cat.publish_owner("p1", [_op("p1", "a")])
    r1 = cat.revision
    assert r1 == 1
    etag1 = cat.etag
    cat.publish_owner("p1", [_op("p1", "a"), _op("p1", "b")])
    assert cat.revision == r1 + 1
    assert cat.etag != etag1
    cat.remove_owner("p1")
    assert cat.revision == r1 + 2


def test_failed_validate_leaves_prior_set() -> None:
    cat = OperationCatalog()
    cat.publish_owner("p1", [_op("p1", "a")])
    rev = cat.revision
    bad = OperationDescriptor(
        plugin_id="p1",
        operation_id="bad",
        ui=UiContribution(slot="s", renderer_kind="invalid"),
    )
    try:
        cat.publish_owner("p1", [bad])
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert cat.get("p1", "a") is not None
    assert cat.revision == rev


def test_filter_drops_hidden() -> None:
    cat = OperationCatalog()
    cat.publish_owner(
        "p1",
        [
            _op("p1", "vis"),
            _op("p1", "hid", visibility=Visibility.HIDDEN),
        ],
    )
    # unrestricted scopes (None)
    visible = cat.filter_for_caller(None)
    ids = {o.operation_id for o in visible}
    assert "vis" in ids
    assert "hid" not in ids
    with_hidden = cat.filter_for_caller(None, include_hidden=True)
    assert {o.operation_id for o in with_hidden} == {"vis", "hid"}


def test_filter_for_caller_scopes() -> None:
    # Ensure enforcement is on for this test
    prev = os.environ.get("SCOPE_ENFORCEMENT_OFF")
    os.environ.pop("SCOPE_ENFORCEMENT_OFF", None)
    try:
        cat = OperationCatalog()
        cat.publish_owner(
            "p1",
            [
                _op("p1", "need_plugin", tags=("p1",)),
                _op("p1", "explicit", required_scopes=("plugin:p1",)),
            ],
        )
        denied = cat.filter_for_caller([])
        assert denied == []
        allowed = cat.filter_for_caller(["plugin:p1"])
        assert {o.operation_id for o in allowed} == {"need_plugin", "explicit"}
        admin = cat.filter_for_caller(["admin"])
        assert len(admin) == 2
    finally:
        if prev is not None:
            os.environ["SCOPE_ENFORCEMENT_OFF"] = prev


def test_get_requires_plugin_id_pair() -> None:
    cat = OperationCatalog()
    cat.publish_owner("p1", [_op("p1", "op1")])
    assert cat.get("p1", "op1") is not None
    assert cat.get("", "op1") is None
    assert cat.get("p1", "") is None


def test_concurrent_publish_and_snapshot() -> None:
    """Publish while iterating must not raise RuntimeError (dictionary changed size)."""
    import threading
    import time

    cat = OperationCatalog()
    cat.publish_owner("p1", [_op("p1", f"op{i}") for i in range(20)])
    stop = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                _ = cat.all()
                _ = cat.ops_for_plugin("p1")
                _ = cat.filter_for_caller(None)
                _ = len(cat)
        except BaseException as exc:  # noqa: BLE001 — collect for assert
            errors.append(exc)

    def writer() -> None:
        try:
            n = 0
            while not stop.is_set():
                cat.publish_owner("p1", [_op("p1", f"w{n}"), _op("p1", f"w{n + 1}")])
                n += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(3)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    time.sleep(0.15)
    stop.set()
    for t in threads:
        t.join(timeout=2)
    assert errors == [], f"concurrent access raised: {errors}"


def test_route_registry_mirrors_to_catalog() -> None:
    _reset_operation_catalog_for_tests()
    reg = RouteRegistry()
    reg.contribute(
        [
            RouteDescriptor(
                plugin_id="p1",
                operation_id="p1__op1",
                description="d",
                method="CALL",
                is_fast_path=True,
                tags=("p1", "read"),
            )
        ]
    )
    cat = get_operation_catalog()
    op = cat.get("p1", "p1__op1")
    assert op is not None
    assert op.access == AccessClass.READ
    reg.remove_plugin("p1")
    assert cat.get("p1", "p1__op1") is None
