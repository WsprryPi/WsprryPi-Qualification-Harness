"""Thin campaign routing above the maintained production coordinators."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from copy import deepcopy
from enum import StrEnum
from pathlib import Path
from typing import Any

from wsprrypi_qualification.keyed_session_contracts import validate_resolved_keyed_plan
from wsprrypi_qualification.manifests import validate_manifest_name, write_manifest
from wsprrypi_qualification.offline import artifact, validate_document, write_json_new
from wsprrypi_qualification.real_session import validate_real_session_plan


class TurnkeyCampaignError(RuntimeError):
    """A campaign routing invariant failed closed."""


class CampaignMode(StrEnum):
    TONE = "TONE"
    WSPR = "WSPR"
    QRSS = "QRSS"
    FSKCW = "FSKCW"
    DFCW = "DFCW"


def canonical_sha256(document: dict[str, Any]) -> str:
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TurnkeyCampaignError(f"cannot load {path}: {error}") from error
    if not isinstance(value, dict):
        raise TurnkeyCampaignError(f"{path} must contain a JSON object")
    return value


def _route(mode: CampaignMode) -> str:
    return "real_session" if mode in {CampaignMode.TONE, CampaignMode.WSPR} else "live_keyed"


def _validate_mode_plan(mode: CampaignMode, document: dict[str, Any]) -> None:
    if mode in {CampaignMode.TONE, CampaignMode.WSPR}:
        validate_real_session_plan(document)
        kind = "cw_live_tone" if mode is CampaignMode.TONE else "wspr_qualification"
        if document.get("session_kind", "wspr_qualification") != kind or document["mode"] != mode:
            raise TurnkeyCampaignError("campaign mode differs from the real-session plan")
    else:
        validate_resolved_keyed_plan(document)
        if document["mode"] != mode:
            raise TurnkeyCampaignError("campaign mode differs from the keyed-session plan")


def compose_resolved_campaign_plan(
    request_path: Path, mode_plan_path: Path, output_path: Path | None = None
) -> dict[str, Any]:
    """Compose one route plan without external access or capability construction."""
    request = _load_object(request_path)
    validate_document(request, "turnkey-campaign-request.schema.json")
    mode = CampaignMode(request["mode"])
    mode_plan = _load_object(mode_plan_path)
    _validate_mode_plan(mode, mode_plan)
    document = {
        "schema_version": 1,
        "evidence_type": "resolved_turnkey_campaign_plan",
        "campaign_id": request["campaign_id"],
        "mode": mode.value,
        "execution_policy": request["execution_policy"],
        "request": {"artifact": artifact(request_path), "document": request},
        "mode_plan": {"artifact": artifact(mode_plan_path), "document": mode_plan},
        "production_route": _route(mode),
        "production_adapters_constructed": False,
        "qualification_claim": False,
    }
    validate_resolved_campaign_plan(document)
    if output_path is not None:
        write_json_new(
            output_path, document, schema_name="resolved-turnkey-campaign-plan.schema.json"
        )
    return document


def validate_resolved_campaign_plan(document: dict[str, Any]) -> dict[str, Any]:
    validate_document(document, "resolved-turnkey-campaign-plan.schema.json")
    mode = CampaignMode(document["mode"])
    if document["production_route"] != _route(mode):
        raise TurnkeyCampaignError("campaign route differs from its mode")
    _validate_mode_plan(mode, document["mode_plan"]["document"])
    for binding in (document["request"], document["mode_plan"]):
        source = Path(binding["artifact"]["path"])
        if source.is_symlink() or not source.is_file():
            raise TurnkeyCampaignError("bound campaign input is unavailable")
        current = artifact(source)
        if any(current[field] != binding["artifact"][field] for field in ("size_bytes", "sha256")):
            raise TurnkeyCampaignError("bound campaign input changed")
        if _load_object(source) != binding["document"]:
            raise TurnkeyCampaignError("bound campaign document differs from its source")
    request = document["request"]["document"]
    if request["campaign_id"] != document["campaign_id"] or request["mode"] != document["mode"]:
        raise TurnkeyCampaignError("campaign request differs from its resolved route")
    if request["execution_policy"] != document["execution_policy"]:
        raise TurnkeyCampaignError("campaign execution policy changed")
    return deepcopy(document)


def resolved_campaign_sha256(document: dict[str, Any]) -> str:
    return canonical_sha256(validate_resolved_campaign_plan(document))


def run_hardware_free_campaign(
    plan: dict[str, Any],
    output_parent: Path,
) -> dict[str, Any]:
    """Rehearse route selection without processes, hosts, devices, services, or RF."""
    resolved = validate_resolved_campaign_plan(plan)
    if resolved["execution_policy"] != "hardware_free":
        raise TurnkeyCampaignError("rehearsal requires a hardware_free campaign")
    campaign_id = validate_manifest_name(resolved["campaign_id"])
    parent = output_parent.resolve()
    final = parent / campaign_id
    temporary = parent / f".incomplete-{campaign_id}"
    if final.exists() or temporary.exists():
        raise TurnkeyCampaignError("campaign destination is not new")
    parent.mkdir(parents=True, exist_ok=True)
    temporary.mkdir()
    try:
        result = {
            "schema_version": 1,
            "evidence_type": "turnkey_campaign_result",
            "campaign_id": campaign_id,
            "mode": resolved["mode"],
            "plan_sha256": resolved_campaign_sha256(resolved),
            "production_route": resolved["production_route"],
            "routing_outcome": "verified",
            "final_status": "inconclusive",
            "qualification_claim": False,
        }
        validate_document(result, "turnkey-campaign-result.schema.json")
        write_json_new(
            temporary / "resolved-plan.json",
            resolved,
            schema_name="resolved-turnkey-campaign-plan.schema.json",
        )
        write_json_new(
            temporary / "result.json", result, schema_name="turnkey-campaign-result.schema.json"
        )
        write_manifest(temporary)
        temporary.replace(final)
        return {"bundle": str(final), "result": result}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def run_live_campaign(
    plan: dict[str, Any],
    output_parent: Path,
    *,
    operator: str,
    confirmed_plan_sha256: str,
    ssh_executable: Path,
    work_directory: Path,
    progress: Callable[[str, str, str, int | None, int | None], None] | None = None,
) -> dict[str, Any]:
    """Dispatch through an existing coordinator after exact campaign confirmation."""
    resolved = validate_resolved_campaign_plan(plan)
    if resolved["execution_policy"] != "live":
        raise TurnkeyCampaignError("live dispatch requires a live campaign")
    digest = resolved_campaign_sha256(resolved)
    if not operator.strip() or confirmed_plan_sha256 != digest:
        raise TurnkeyCampaignError("operator and exact campaign digest confirmation are required")
    child = resolved["mode_plan"]["document"]
    mode = CampaignMode(resolved["mode"])
    # Delayed imports are intentional: no production capability can be built
    # until the complete wrapper plan and exact operator confirmation pass.
    if mode in {CampaignMode.TONE, CampaignMode.WSPR}:
        from datetime import UTC, datetime

        from wsprrypi_qualification.live_adapters import build_production_adapters
        from wsprrypi_qualification.real_session import (
            RealQualificationSession,
            RealRuntimeAuthorization,
            ResolvedRealSessionPlan,
        )

        now = datetime.now(UTC)
        child_plan = ResolvedRealSessionPlan(child)
        child_digest = child_plan.sha256
        adapters = build_production_adapters(
            child,
            ssh_executable=ssh_executable.resolve(),
            work_directory=work_directory.resolve(),
        )
        session = RealQualificationSession(child_plan, adapters, now=now)
        authorizations = (
            RealRuntimeAuthorization("external_access", operator, now, child_digest, True),
            RealRuntimeAuthorization("rf", operator, now, child_digest, True),
            output_parent,
        )
        result = (
            session.run(*authorizations, progress=progress)
            if progress is not None
            else session.run(*authorizations)
        )
        authoritative_bundle = output_parent.resolve() / str(result["run_id"])
    else:
        from datetime import UTC, datetime

        from wsprrypi_qualification.keyed_session_contracts import (
            compose_keyed_runtime_authorization,
        )
        from wsprrypi_qualification.live_keyed import (
            build_production_keyed_adapter,
            run_live_keyed_session,
        )

        now = datetime.now(UTC)
        authorization = compose_keyed_runtime_authorization(
            child, operator=operator, authorized_utc=now.isoformat().replace("+00:00", "Z")
        )
        adapter = build_production_keyed_adapter(
            child,
            ssh_executable=ssh_executable.resolve(),
            work_directory=work_directory.resolve(),
        )
        result = (
            run_live_keyed_session(child, authorization, output_parent, adapter, progress=progress)
            if progress is not None
            else run_live_keyed_session(child, authorization, output_parent, adapter)
        )
        authoritative_bundle = Path(result["bundle"])
    return {
        "campaign_id": resolved["campaign_id"],
        "mode": mode.value,
        "campaign_plan_sha256": digest,
        "production_route": resolved["production_route"],
        "authoritative_bundle": str(authoritative_bundle),
        "underlying_result": result,
    }
