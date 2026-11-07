"""Release selection and exact-input CI reuse without publishing side effects.

Publication commands remain owned by each product. A release graph records
independent source/tooling/artifact identities and exposes partial delivery.
"""

from __future__ import annotations

from typing import Any

from wsprrypi_qualification.lifecycle import LifecycleError, digest, validate
from wsprrypi_qualification.lifecycle_recipes import release_order


def ci_input_key(component: str, components: dict[str, Any]) -> str:
    keys = (
        "source",
        "tooling_source",
        "test_source",
        "embedded_source",
        "version",
        "tag",
        "artifact_sha256",
        "build_inputs_sha256",
        "canonical_ci",
        "required_jobs",
    )
    return digest(
        {
            "component": component,
            "pair": {name: {k: value[k] for k in keys} for name, value in components.items()},
        }
    )


def release_graph(request: dict[str, Any]) -> dict[str, Any]:
    """Validate independent selections; never contact GitHub or move a tag."""
    validate(request, "lifecycle-release-request")
    if set(request) != {"scope", "components", "pi_dependency_selections", "publication_selected"}:
        raise LifecycleError("release request fields differ")
    if (
        request["scope"] not in {"pi", "pico", "rp1", "system"}
        or type(request["publication_selected"]) is not bool
    ):
        raise LifecycleError("invalid release scope/publication selection")
    components = request["components"]
    if not isinstance(components, dict) or set(components) != {"pi", "pico", "rp1"}:
        raise LifecycleError("all selected/fixed companion identities are required")
    changed = []
    required = {
        "build_inputs",
        "changed",
        "source",
        "tooling_source",
        "test_source",
        "embedded_source",
        "version",
        "tag",
        "prior_remote_oid",
        "move_existing_tag",
        "artifact_sha256",
        "build_inputs_sha256",
        "verified_build_inputs_sha256",
        "published",
        "canonical_ci",
        "ci_input_sha256",
        "successful_ci_input_sha256",
        "ci_conclusion",
        "required_jobs",
        "successful_jobs",
    }
    operations = []
    for component, value in components.items():
        if set(value) != required:
            raise LifecycleError("release component identity fields differ: " + component)
        for name in ("source", "tooling_source", "test_source", "embedded_source"):
            text = value[name]
            if (
                not isinstance(text, str)
                or len(text) != 40
                or any(c not in "0123456789abcdef" for c in text)
            ):
                raise LifecycleError("full source identity required: " + component + "/" + name)
        for name in (
            "artifact_sha256",
            "build_inputs_sha256",
            "verified_build_inputs_sha256",
            "ci_input_sha256",
            "successful_ci_input_sha256",
        ):
            text = value[name]
            if (
                not isinstance(text, str)
                or len(text) != 64
                or any(c not in "0123456789abcdef" for c in text)
            ):
                raise LifecycleError("SHA256 input required: " + component + "/" + name)
        if any(type(value[n]) is not bool for n in ("changed", "move_existing_tag", "published")):
            raise LifecycleError("release decision fields must be booleans")
        if value["move_existing_tag"] and not value["prior_remote_oid"]:
            raise LifecycleError("tag movement requires expected prior remote object identity")
        if value["prior_remote_oid"] and not value["move_existing_tag"] and value["changed"]:
            raise LifecycleError("existing tag movement was not selected")
        if value["changed"]:
            changed.append(component)
        if (
            digest(value["build_inputs"]) != value["build_inputs_sha256"]
            or value["build_inputs"]["source"] != value["embedded_source"]
            or value["build_inputs"]["version"] != value["version"]
        ):
            raise LifecycleError("build-input identity mismatch: " + component)
        if value["ci_input_sha256"] != ci_input_key(component, components):
            raise LifecycleError(
                "CI key omits selected source/artifact/companion inputs: " + component
            )
        reuse = value["build_inputs_sha256"] == value["verified_build_inputs_sha256"]
        ci_reuse = (
            value["ci_conclusion"] == "success"
            and bool(value["required_jobs"])
            and value["ci_input_sha256"] == value["successful_ci_input_sha256"]
            and set(value["required_jobs"]).issubset(value["successful_jobs"])
        )
        operations.append(
            {
                "component": component,
                "artifact_action": "reuse" if reuse else "rebuild",
                "artifact_reason": "exact build inputs"
                if reuse
                else "version/source/board/toolchain/dependencies changed",
                "ci_action": "reuse" if ci_reuse else "canonical-validation-required",
                "canonical_ci": value["canonical_ci"],
                "published": value["published"],
            }
        )
    order = release_order(request["scope"], changed)
    selections = request["pi_dependency_selections"]
    if set(selections) != {"pico", "rp1"}:
        raise LifecycleError("Pi must fix both companion identities")
    for component in ("pico", "rp1"):
        if selections[component] != components[component]["artifact_sha256"]:
            raise LifecycleError("Pi dependency selection differs from exact companion bytes")
    phases = []
    for component in order:
        if components[component]["published"]:
            phases += [
                component + ":inspect-public-state",
                component + ":public-consumer-acceptance",
            ]
        else:
            phases += [
                component + ":validate",
                component + ":publish",
                component + ":public-consumer-acceptance",
            ]
    if "pi" in order and not components["pi"]["published"]:
        for dependency in ("pico", "rp1"):
            if dependency not in order and not components[dependency]["published"]:
                raise LifecycleError("fixed Pi dependency is not publicly available: " + dependency)
        position = phases.index("pi:validate")
        phases.insert(position, "pi:update-authoritative-dependency-selections")
    phases.append(
        "system:public-acceptance"
        if request["scope"] == "system"
        else request["scope"] + ":fixed-companion-acceptance"
    )
    result = {
        "kind": "lifecycle-release-plan",
        "schema_version": 1,
        "request": request,
        "publication_enabled": request["publication_selected"],
        "order": order,
        "phases": phases,
        "selection": operations,
        "partial_delivery": [c for c in order if components[c]["published"]],
        "recovery_policy": (
            "inspect public state; resume unfinished owning-component operations; "
            "never erase published history"
        ),
    }
    result["plan_sha256"] = digest(result)
    return result


def tag_push_arguments(
    remote: str,
    tag: str,
    new_oid: str,
    *,
    expected_old_oid: str | None = None,
    move_selected: bool = False,
) -> list[str]:
    """Construct a guarded Git update; never invoke it or implicitly force tags."""
    import re

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", remote) or not re.fullmatch(
        r"v[0-9][a-zA-Z0-9.+_-]*", tag
    ):
        raise LifecycleError("invalid remote or release tag")
    if not re.fullmatch(r"[0-9a-f]{40}", new_oid):
        raise LifecycleError("full new object ID required")
    ref = "refs/tags/" + tag
    if expected_old_oid is not None:
        if not move_selected or not re.fullmatch(r"[0-9a-f]{40}", expected_old_oid):
            raise LifecycleError(
                "existing tag update requires explicit movement and prior remote object ID"
            )
        return [
            "push",
            "--force-with-lease=" + ref + ":" + expected_old_oid,
            remote,
            new_oid + ":" + ref,
        ]
    if move_selected:
        raise LifecycleError("tag movement requires prior remote object ID")
    return ["push", remote, new_oid + ":" + ref]
