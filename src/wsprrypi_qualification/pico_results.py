"""Read-only Pico result, identity, independent-count, and analysis validation."""

from __future__ import annotations

import wave
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from wsprrypi_qualification.audio import AudioParameters, render_slot_pcm
from wsprrypi_qualification.capture_metadata import load_capture_metadata
from wsprrypi_qualification.carrier import _capture_reference
from wsprrypi_qualification.cw_contracts import (
    _validate_events,
    _validate_gate,
    _validate_observations,
)
from wsprrypi_qualification.decoder import (
    _classify_expected_decodes,
    malformed_wsprd_lines,
    parse_wsprd_output,
)
from wsprrypi_qualification.models import WsprIdentity
from wsprrypi_qualification.offline import artifact, validate_document
from wsprrypi_qualification.pico_audit import validate_wire_audit
from wsprrypi_qualification.pico_campaign import (
    MODE_ORDER,
    check_admission,
    check_prepared_correction,
    cw_analysis_plan,
    plan_digest,
    validate_measurement_binding,
    validate_pico_plan,
)
from wsprrypi_qualification.pico_wire import PicoWireError, loads
from wsprrypi_qualification.timing import sample_index_at_utc
from wsprrypi_qualification.wtp_control import validate_transaction


def _read(path: Path, schema: str | None = None) -> dict[str, Any]:
    value = loads(path.read_bytes())
    if schema is not None:
        validate_document(value, schema)
    return value


def _within(path: str, directory: Path) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.resolve(strict=True).is_relative_to(
        directory.resolve()
    ):
        raise PicoWireError("Measurement reference escapes its private observation")
    return candidate


def settings_match(initial: dict[str, Any], final: dict[str, Any]) -> bool:
    return (
        final["config"] == initial["config"]
        and final["revision"] == initial["revision"]
        and all(
            final[k]["saved"] == initial[k]["saved"]
            and final[k]["boot_active"] == initial[k]["boot_active"]
            for k in ("gps", "si5351")
        )
        and all(
            final["hardware"][k] == initial["hardware"][k]
            for k in ("saved", "active", "active_revision", "execution_engine")
        )
        and final["target"] == initial["target"]
        and final["message-schedule"]["saved"] == initial["message-schedule"]["saved"]
        and final["message-schedule"]["pause_pending"] is False
    )


def _decoder_bindings(directory: Path, document: dict[str, Any], audio: dict[str, Any]) -> None:
    wav = _within(document["wav"]["path"], directory)
    metadata = directory / "audio.json"
    tools = _read(directory.parent / "controller.json")["tools"]
    if (
        artifact(wav) != document["wav"]
        or audio["output"] != document["wav"]
        or document["audio_evidence"] != artifact(metadata)
        or document["tool"]["path"] != tools["wsprd"]["path"]
        or document["tool"]["sha256"] != tools["wsprd"]["sha256"]
        or document["tool"]["version_query"]["arguments"] != [tools["wsprd"]["path"], "--version"]
    ):
        raise PicoWireError("Decoder tool, WAV, or audio artifact binding differs")
    data = Path(document["decoder_data_directory"])
    if data.is_symlink() or data.resolve() != directory.resolve() / "wsprd-data":
        raise PicoWireError("Decoder data directory differs")
    if document["arguments"] != [
        tools["wsprd"]["path"],
        "-a",
        str(data.resolve()),
        str(wav.resolve()),
    ]:
        raise PicoWireError("Decoder arguments differ from retained artifacts")
    indexed = []
    for record in document["decoder_data_artifacts"]:
        path = _within(record["path"], data)
        if artifact(path) != record or path in indexed:
            raise PicoWireError("Decoder data artifact differs or is duplicated")
        indexed.append(path)
    if set(indexed) != set(data.iterdir()):
        raise PicoWireError("Decoder data inventory is incomplete")


def _validate_carrier_binding(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    carrier = _read(directory / "carrier.json", "carrier-analysis.schema.json")
    validate_measurement_binding(carrier["contract"]["profiles"], plan)
    if carrier["contract"]["evidence_scope"] != "acquired":
        raise PicoWireError("Pico carrier is not acquired evidence")
    references = carrier["contract"]["capture_metadata_validation"]
    if references["outcome"] != "passed":
        raise PicoWireError("Carrier capture metadata is unverified")
    for kind, filename in (("rf_off", "rf-off-plan.json"), ("rf_on", "rf-on-plan.json")):
        capture = _read(directory / filename)
        iq = _within(capture["output_path"], directory)
        actual = artifact(iq)
        if any(
            carrier["inputs"][kind][key] != actual[key] for key in ("path", "size_bytes", "sha256")
        ):
            raise PicoWireError("Carrier input differs from its independent Pico capture")
        metadata_path = _within(capture["metadata_path"], directory)
        metadata = load_capture_metadata(metadata_path)
        if references[kind] != _capture_reference(metadata_path, metadata):
            raise PicoWireError("Carrier native capture metadata binding differs")
    expected_reference = {
        "alignment_policy": "independent_tone_duration_v1",
        "plan": artifact(directory / "mode-plan.json"),
        "expected_events": artifact(directory / "expected.json"),
    }
    if carrier["contract"]["temporal_cw_reference"] != expected_reference:
        raise PicoWireError("Carrier timing reference differs from the Pico observation")
    return carrier


def _validate_wspr(directory: Path, plan: dict[str, Any], start_ns: int) -> str:
    config = plan["configuration"]
    p = config["protocol"]
    document = _read(directory / "decode.json", "decoder-evidence.schema.json")
    audio = _read(directory / "audio.json", "audio-conversion.schema.json")
    _decoder_bindings(directory, document, audio)
    validate_measurement_binding(document["profiles"], plan)
    validate_measurement_binding(audio["profiles"], plan)
    slot = datetime.fromisoformat(document["slot_utc"].replace("Z", "+00:00"))
    if (
        start_ns != int(slot.timestamp()) * 1_000_000_000 + 2_000_000_000
        or int(slot.timestamp()) % 120
    ):
        raise PicoWireError("WSPR decoder slot differs from the armed UTC boundary")
    identity = WsprIdentity(p["callsign"], p["locator"][:4], p["power_dbm"])
    decodes = parse_wsprd_output(document["stdout"])
    found, intended = _classify_expected_decodes(decodes, identity, slot, 1500, 100)
    outcome = (
        "passed"
        if intended and document["return_code"] == 0 and not document["timed_out"]
        else "blocked"
        if document["timed_out"] or document["return_code"] not in (0, None)
        else "failed"
    )
    if (
        document["expected_identity"]
        != dict(callsign=identity.callsign, grid=identity.grid, power_dbm=identity.power_dbm)
        or document["decodes"] != decodes
        or document["expected_identity_found"] != found
        or document["expected_intended_signal_found"] != intended
        or document["malformed_candidate_lines"] != malformed_wsprd_lines(document["stdout"])
        or document["gate_outcome"] != outcome
        or audio["slot_utc"] != document["slot_utc"]
    ):
        raise PicoWireError("WSPR decode claims contradict retained stdout/identity/slot")
    c = audio["contract"]
    parameters = AudioParameters(**{name: c[name] for name in AudioParameters.__dataclass_fields__})
    carrier = _validate_carrier_binding(directory / "carrier", plan)
    acquired = carrier["metrics"]["strongest_transmitter_added_frequency_hz"]
    expected_parameters = AudioParameters(
        config["receiver"]["sample_rate_hz"], config["receiver"]["center_frequency_hz"], acquired
    )
    capture_plan = _read(directory / "rf-on-plan.json")
    metadata = load_capture_metadata(_within(capture_plan["metadata_path"], directory))
    if metadata.retained_capture_start_utc is None:
        raise PicoWireError("WSPR capture lacks observed UTC")
    if (
        asdict(parameters) != asdict(expected_parameters)
        or c["input_start_sample"]
        != sample_index_at_utc(metadata.retained_capture_start_utc, slot, parameters.sample_rate_hz)
        or c["input_sample_count"] != parameters.sample_rate_hz * 120
        or Path(audio["input"]["path"]) != Path(capture_plan["output_path"])
    ):
        raise PicoWireError("WSPR audio tuning, capture, or slot geometry differs")
    pcm, scale = render_slot_pcm(
        _within(audio["input"]["path"], directory),
        c["input_start_sample"],
        c["input_sample_count"],
        parameters,
    )
    with wave.open(str(_within(document["wav"]["path"], directory)), "rb") as wav:
        if wav.readframes(wav.getnframes()) != pcm or scale != c["normalization_scale"]:
            raise PicoWireError("WSPR WAV differs from retained IQ and conversion contract")
    return outcome


def _validate_analysis_identity(
    plan: dict[str, Any], expected: dict[str, Any], measured: dict[str, Any]
) -> None:
    validate_document(expected, "cw-expected-events.schema.json")
    validate_document(measured, "cw-generated-observations.schema.json")
    revision = plan["analyzer_source"]["revision"]
    if (
        expected["generator"]["source_revision"] != revision
        or measured["analyzer"]["source_revision"] != revision
        or measured["capture"]["synthetic"] is not False
    ):
        raise PicoWireError("Pico live analysis has synthetic or mismatched source evidence")


def _validate_frame(
    directory: Path,
    frame: dict[str, Any],
    plan: dict[str, Any],
    expected_job: dict[str, Any],
    job_ids: set[str],
    capture_ids: set[str],
    *,
    purpose: str = "qualification",
) -> None:
    mode = expected_job["mode"]
    transaction = _read(directory / "transaction.json")
    valid = validate_transaction(transaction)
    actual_job = dict(transaction["job"])
    actual_job["job_id"] = expected_job["job_id"]
    if actual_job != expected_job:
        raise PicoWireError("Submitted Pico job differs from its compiled plan")
    if transaction["job"]["job_id"] in job_ids or transaction["job"]["mode"] != mode:
        raise PicoWireError("Pico transaction job identity is reused or mismatched")
    job_ids.add(transaction["job"]["job_id"])
    before = _read(directory / "before.json")
    prepared = _read(directory / "prepared.json")
    check_admission(plan, before, purpose=purpose)
    check_admission(plan, prepared, owned=True, purpose=purpose)
    gps_observations = [before["gps"], prepared["gps"]]
    load_bracket: list[dict[str, Any]] = []
    if plan["configuration"]["pico"]["automatic_correction"]:
        load_bracket.extend(
            _read(directory / name) for name in ("load-gps-before.json", "load-gps-after.json")
        )
    check_prepared_correction(
        plan,
        prepared,
        gps_observations,
        job_id=transaction["job"]["job_id"],
        load_bracket=load_bracket,
        purpose=purpose,
        owner_id=transaction.get("owner_id"),
    )
    if (
        before["target"] != prepared["target"]
        or transaction["hello"]["boot_id"] != before["target"]["boot_id"]
        or transaction["hello"]["device_id"] != before["target"]["device_id"]
    ):
        raise PicoWireError("Pico job/boot/management binding differs")
    capture_plan = _read(directory / "rf-on-plan.json")
    metadata = load_capture_metadata(_within(capture_plan["metadata_path"], directory))
    iq = _within(capture_plan["output_path"], directory)
    if (
        metadata.capture_id in capture_ids
        or metadata.retained_sample_count != capture_plan["sample_count"]
        or metadata.cleanup_outcome != "verified"
        or metadata.output.sha256 != artifact(iq)["sha256"]
    ):
        raise PicoWireError("Pico capture identity/count/hash/cleanup differs")
    receiver = plan["configuration"]["receiver"]
    for name in (
        "driver",
        "serial",
        "channel",
        "sample_rate_hz",
        "bandwidth_hz",
        "center_frequency_hz",
        "gain_db",
    ):
        if capture_plan[name] != receiver[name]:
            raise PicoWireError("Capture settings differ from the selected receiver")
    if (
        metadata.resolved_device != dict(driver=receiver["driver"], serial=receiver["serial"])
        or metadata.actual_settings is None
        or any(
            metadata.actual_settings[name] != receiver[name]
            for name in (
                "channel",
                "sample_rate_hz",
                "bandwidth_hz",
                "center_frequency_hz",
                "gain_db",
            )
        )
        or metadata.overflow_count
        or metadata.timeout_count
        or metadata.clipped_samples
        or metadata.retained_capture_start_utc is None
    ):
        raise PicoWireError("Capture device, continuity, clipping, or UTC differs")
    pre_quiet = transaction["start_utc_ns"] / 1e9 - metadata.retained_capture_start_utc.timestamp()
    if (
        pre_quiet < 2
        or metadata.retained_sample_count / receiver["sample_rate_hz"]
        - pre_quiet
        - int(expected_job["total_duration_ns"]) / 1e9
        < 2
    ):
        raise PicoWireError("Capture lacks complete job and quiet margins")
    readiness = _read(directory / "readiness.json")
    prefix = _within(readiness["prefix"]["path"], directory)
    if artifact(prefix) != readiness["prefix"] or readiness["output_active"] is not False:
        raise PicoWireError("Pico readiness evidence differs")
    with iq.open("rb") as handle:
        if prefix.read_bytes() != handle.read(2 * receiver["sample_rate_hz"] * 8):
            raise PicoWireError("Readiness prefix differs from the retained capture")
    released = transaction.get("released_status", {})
    if (
        released.get("owner_id") is not None
        or released.get("output_active") is not False
        or released.get("boot_id") != before["target"]["boot_id"]
    ):
        raise PicoWireError("Owned job release is unverified")
    capture_ids.add(metadata.capture_id)
    if mode == "wspr":
        carrier_job = next(row["carrier_job"] for row in plan["modes"] if row["mode"] == "wspr")
        if carrier_job is None:
            raise PicoWireError("WSPR carrier gate is absent from its compiled plan")
        carrier_directory = directory / "carrier"
        _validate_frame(
            carrier_directory, dict(outcome="passed"), plan, carrier_job, job_ids, capture_ids
        )
        outcome = _validate_wspr(directory, plan, transaction["start_utc_ns"])
    else:
        cw = _read(directory / "mode-plan.json", "cw-mode-plan.schema.json")
        reference = cw_analysis_plan(plan, mode, cw["run_id"], pre_quiet)
        reference["capture_contract"]["sample_count"] = capture_plan["sample_count"]
        for name in (
            "source",
            "backend",
            "protocol",
            "receiver",
            "rf_path",
            "thresholds",
            "capture_contract",
        ):
            if cw[name] != reference[name]:
                raise PicoWireError("CW analysis plan differs from its Pico campaign")
        expected = _read(directory / "expected.json", "cw-expected-events.schema.json")
        measured = _read(directory / "observations.json", "cw-generated-observations.schema.json")
        gate = _read(directory / "gate.json", "cw-mode-gate.schema.json")
        if (
            cw["source"]["target_kind"] != "pico"
            or cw["source"]["source_revision"] != plan["source_revision"]
            or cw["source"]["compiler_sha256"] != plan["compiler"]["sha256"]
        ):
            raise PicoWireError("Pico analyzer source binding differs")
        _validate_analysis_identity(plan, expected, measured)
        _validate_events(cw, expected)
        if _within(measured["capture"]["path"], directory) != iq:
            raise PicoWireError("CW observations use another capture")
        _validate_observations(cw, expected, measured, directory / "observations.json")
        _validate_gate(mode, measured, gate)
        outcome = measured["analysis_outcome"]
        if mode == "tone":
            carrier = _validate_carrier_binding(directory, plan)
            if carrier["contract"]["evidence_scope"] != "acquired":
                raise PicoWireError("Pico live carrier is not acquired evidence")
            if carrier["gate_outcome"] != "passed":
                outcome = carrier["gate_outcome"]
    if frame["outcome"] != outcome or (
        frame["outcome"] == "passed" and not (valid["completed"] and valid["cleanup_verified"])
    ):
        raise PicoWireError("Pico frame outcome contradicts transaction or measurements")


def _validate_pico_bundle(
    bundle: Path, *, result_document: dict[str, Any] | None = None
) -> dict[str, Any]:
    if bundle.is_symlink():
        raise PicoWireError("Pico bundle cannot be a symlink")
    root = bundle.resolve(strict=True)
    result = result_document if result_document is not None else _read(root / "result.json")
    validate_document(result, "pico-campaign-result.schema.json")
    plan = _read(root / "plan.json", "pico-campaign-plan.schema.json")
    validate_pico_plan(plan)
    if (
        result["plan_sha256"] != plan_digest(plan)
        or Path(result["output_directory"]).resolve() != root
    ):
        raise PicoWireError("Pico result/plan/root binding differs")
    indexed: set[Path] = set()
    for record in result["artifacts"]:
        path = Path(record["path"])
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or path in indexed
            or artifact(path) != record
        ):
            raise PicoWireError(
                "Pico artifact is duplicate, changed, missing, or outside its bundle"
            )
        indexed.add(path)
    actual = {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path != root / "result.json"
    }
    if indexed != actual:
        raise PicoWireError("Pico artifact inventory is incomplete")
    selected = [row["mode"] for row in plan["modes"]]
    if selected != [mode for mode in MODE_ORDER if mode in selected] or len(set(selected)) != len(
        selected
    ):
        raise PicoWireError("Pico plan mode order/identity differs")
    attempted = [row["mode"] for row in result["modes"]]
    if attempted != selected[: len(attempted)]:
        raise PicoWireError("Pico result contains duplicate, out-of-order, or unselected modes")
    if result["rehearsal"]:
        if (
            result["qualification_claim"]
            or result["outcome"] != "rehearsed"
            or attempted != selected
            or any(
                row["observations"] or row["outcome"] != "unattempted" for row in result["modes"]
            )
        ):
            raise PicoWireError("Pico rehearsal cannot claim hardware observations")
        return result
    job_ids: set[str] = set()
    capture_ids: set[str] = set()
    for row in result["modes"]:
        if [o["number"] for o in row["observations"]] != list(
            range(1, len(row["observations"]) + 1)
        ):
            raise PicoWireError("Independent observation numbers have gaps or duplicates")
        for observation in row["observations"]:
            for index, frame in enumerate(observation["frames"], 1):
                directory = root / f"{row['mode']}-{observation['number']}-{index}"
                if Path(frame["directory"]).resolve() != directory or not Path(
                    frame["analysis"]["path"]
                ).resolve().is_relative_to(directory):
                    raise PicoWireError("Pico frame belongs to another observation")
                expected_jobs = next(
                    item["jobs"] for item in plan["modes"] if item["mode"] == row["mode"]
                )
                if index > len(expected_jobs):
                    raise PicoWireError("Pico observation has extra frames")
                _validate_frame(
                    directory, frame, plan, expected_jobs[index - 1], job_ids, capture_ids
                )
            if observation["outcome"] == "passed" and (
                len(observation["frames"])
                != len(next(item["jobs"] for item in plan["modes"] if item["mode"] == row["mode"]))
                or any(f["outcome"] != "passed" for f in observation["frames"])
            ):
                raise PicoWireError("Pico passing observation lacks complete measurements")
        if row["outcome"] == "passed" and (
            len(row["observations"]) != 3
            or any(o["outcome"] != "passed" for o in row["observations"])
        ):
            raise PicoWireError("Pico passing mode requires three independent transactions")
    if result["outcome"] == "passed":
        if (
            attempted != selected
            or result["errors"]
            or not result["cleanup_verified"]
            or not result["settings_unchanged"]
            or not all(row["outcome"] == "passed" for row in result["modes"])
            or not result["transmitter_transaction_attempted"]
        ):
            raise PicoWireError("Pico passing aggregate has incomplete/failed coverage or cleanup")
        initial, final = (_read(root / name) for name in ("initial.json", "final.json"))
        validate_wire_audit(root / "usb-io.jsonl", job_ids, initial["target"])
        if (
            not settings_match(initial, final)
            or final["status"]["job"]["owner_id"] is not None
            or final["status"]["job"]["output_active"]
        ):
            raise PicoWireError(
                "Pico original settings, ownership release, or final RF inactivity differs"
            )
    if result["qualification_claim"] != (
        result["outcome"] == "passed" and result["cleanup_verified"]
    ):
        raise PicoWireError("Pico qualification claim contradicts aggregate outcome")
    return result


def validate_pico_bundle(
    bundle: Path, *, result_document: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Normalize malformed cross-document evidence into a closed validation refusal."""
    try:
        return _validate_pico_bundle(bundle, result_document=result_document)
    except (
        KeyError,
        TypeError,
        IndexError,
        OverflowError,
        UnicodeError,
        wave.Error,
        EOFError,
    ) as error:
        raise PicoWireError("Malformed Pico result evidence") from error
