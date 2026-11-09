"""Inhibited connected fixture for the one-job runner; no physical constructors."""

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support import pico_connected_capture as connected
from tests.support import pico_governance as base
from wsprrypi_qualification import pico_live, pico_smoke, pico_wire
from wsprrypi_qualification.offline import artifact, write_json_new
from wsprrypi_qualification.pico_audit import AuditedStream, WireAudit
from wsprrypi_qualification.pico_campaign import compose_pico_campaign
from wsprrypi_qualification.pico_wire import ManagementClient, WtpClient, encode, loads


class ManagementStream:
    """Fixture resource server behind the production UMP client and byte audit."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.pending = bytearray()
        self.snapshot = base.make_snapshot(
            gps=adapter.fixture_scenario == "legacy-auto",
            automatic=adapter.fixture_scenario == "legacy-auto",
        )
        if adapter.fixture_scenario == "busy":
            self.snapshot["status"]["job"]["owner_id"] = "f" * 32

    def write(self, data, deadline):
        request = loads(data[8:])
        operation = request["operation"]
        if operation == "hello":
            body = dict(
                schema="usb-management-discovery/1",
                product="WsprryPico",
                firmware="governance-fixture",
                max_request_bytes=32768,
                max_response_bytes=65536,
            )
        elif operation == "ack":
            body = dict(acknowledged=True)
        else:
            resource = request["path"].rsplit("/", 1)[1]
            if resource == "status":
                self.snapshot = (
                    base.make_snapshot()
                    if self.adapter._wtp is None
                    else base.Adapter.snapshot(self.adapter)
                )
            if self.adapter.fixture_scenario == "busy":
                self.snapshot["status"]["job"]["owner_id"] = "f" * 32
            if self.adapter.fixture_scenario in {"legacy-manual", "legacy-auto"}:
                status = self.snapshot["status"]["job"]
                if self.adapter.fixture_scenario == "legacy-auto":
                    self.snapshot = base.make_snapshot(gps=True, automatic=True)
                    self.snapshot["status"]["job"] = status
                    if self.adapter._wtp is not None:
                        self.snapshot["capabilities"]["wtp"] = self.adapter.wtp.request("CAPS", {})
                    if status["state"] == "loaded":
                        self.snapshot["rf-calibration"]["selection"].update(
                            prepared_source="gps", prepared_generation="7", prepared_ppb=25
                        )
                self.snapshot["rf-calibration"]["selection"].pop("prepared_input", None)
                self.snapshot["rf-calibration"]["selection"]["future_telemetry"] = {"value": 1}
                self.snapshot["ntp-observations"].pop("network", None)
            body = self.snapshot[resource]
        reply = dict(
            schema="usb-management/1",
            id=request["id"],
            status=200,
            session="c" * 32,
            target=self.snapshot["target"],
            body=json.dumps(body),
            etag=self.snapshot["revision"],
            delivery_required=operation == "request",
        )
        payload = encode(reply)
        self.pending.extend(b"UMP1" + len(payload).to_bytes(4, "big") + payload)

    def read(self, maximum, deadline):
        data = bytes(self.pending[:maximum])
        del self.pending[:maximum]
        return data

    def close(self):
        pass


class Adapter(connected.Adapter, pico_smoke.SmokeAdapter):
    def start_wtp(self):
        self._wtp = WtpClient(AuditedStream(self.fixture_stream, self.audit, "wtp"))
        self.wtp.request("HELLO", dict(versions=["WTP/1"], client_name="smoke", client_version="1"))

    def snapshot(self, *, initial=False):
        return pico_live.PicoLiveAdapter.snapshot(self, initial=initial)

    def close(self):
        try:
            super().close()
        finally:
            write_json_new(self.fixture_output / "requests.json", self.fixture_stream.requests)
            write_json_new(
                self.fixture_output / "executions.json",
                self.fixture_stream.command(dict(action="inspect"))["executions"],
            )
            self.fixture_stream.close()


def run_case(source: Path, native: Path, wsprd: Path, output: Path, scenario: str):
    clock = connected.Clock()
    cancel = base.Cancellation(clock)
    clock.cancel = cancel
    created = []
    with pytest.MonkeyPatch.context() as patch:
        for name in ("PicoUsb", "SoapyCaptureCapability", "LocalTransportLauncher"):
            patch.setattr(pico_live, name, base.deny)
        patch.setattr(pico_live, "time", clock)
        patch.setattr(pico_wire, "time", clock)
        config = base.configuration(
            gps=scenario == "legacy-auto", automatic=scenario == "legacy-auto"
        )
        config["pico_source"] = str(source)
        config["protocol"].update(message="ET", dot_seconds=0.7)
        config["receiver"].update(
            capture_helper=str(base.binary_path(native, "wspq-capture-connected")), wsprd=str(wsprd)
        )
        plan = compose_pico_campaign(
            config, base.binary_path(native, "wspq-pico-compile"), modes=("qrss",)
        )

        def factory(plan, output, reporter, operation_cancel):
            stream = connected.EndpointStream(
                base.binary_path(native, "wspq-pico-governance"), output, clock, False
            )
            adapter = None
            try:
                identity = stream.command(dict(action="inspect"))
                assert identity["source_revision"] == plan["source_revision"]
                assert identity["source_files"] == [
                    dict(
                        path=Path(p["path"]).relative_to(source / "src").as_posix(),
                        sha256=p["sha256"],
                    )
                    for p in plan["encoder_sources"]
                ]
                adapter = object.__new__(Adapter)
                adapter.plan, adapter.cancel, adapter.reporter = plan, cancel, reporter
                adapter.audit = WireAudit(output / "usb-io.jsonl")
                adapter.fixture_stream, adapter.fixture_output = stream, output
                adapter._wtp = None
                adapter.fixture_scenario = scenario
                adapter.management = ManagementClient(
                    AuditedStream(ManagementStream(adapter), adapter.audit, "management"),
                    plan["configuration"]["pico"]["device_id"],
                    purpose=adapter.validation_purpose,
                )
                adapter.discovery = adapter.management.connect()
                adapter.usb = SimpleNamespace(
                    identity=dict(fixture=True),
                    begin_operations=lambda _: None,
                    begin_cleanup=lambda: None,
                    close=lambda: None,
                )
                adapter.tools = {
                    "capture_helper": artifact(Path(config["receiver"]["capture_helper"]))
                }
                write_json_new(output / "controller.json", dict(tools=adapter.tools))
                write_json_new(
                    output / "fixture.json",
                    dict(
                        identity=identity,
                        substitutions=base.SUBSTITUTIONS,
                        qualification_claim=False,
                    ),
                )
                adapter.jobs_attempted, adapter.receiver_cleanup_failed, adapter.futures = (
                    False,
                    False,
                    [],
                )
                connected.configure(
                    adapter, clock, "keyed" if scenario.startswith("legacy-") else scenario
                )
                created.append(adapter)
                return adapter
            except BaseException:
                stream.close()
                raise

        patch.setattr(pico_smoke, "SmokeAdapter", factory)
        try:
            result = pico_smoke.run(
                plan, output, "offline-fixture", pico_smoke.plan_digest(plan), enable_rf=True
            )
        finally:
            for adapter in created:
                cancel.set()
                adapter.pool.shutdown(wait=True, cancel_futures=True)
                if not adapter.audit.file.closed:
                    with contextlib.suppress(Exception):
                        adapter.close()
    return result
