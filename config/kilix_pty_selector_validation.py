"""Semantic validation of selector data; no census, process or filesystem access."""
import json
import os

import kilix_pty as pty


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_id(value):
    return isinstance(value, str) and bool(pty.SESSION_ID.fullmatch(value)) and value not in (".", "..")


def caller(value):
    require(isinstance(value, dict) and value.get("source") == "environment"
            and value.get("authenticated") is False and value.get("status") in ("valid", "missing", "malformed"),
            "invalid captured caller")
    require(valid_id(value.get("id")) if value["status"] == "valid" else value.get("id") is None,
            "caller status and ID disagree")


def runtime(value):
    require(isinstance(value, dict) and set(value) == {"path", "directory_identity"}, "invalid runtime context")
    path, identity = value["path"], value["directory_identity"]
    require(isinstance(path, str) and path.startswith("/") and "\0" not in path and os.path.normpath(path) == path,
            "runtime path is not canonical")
    require(identity is None or (isinstance(identity, list) and len(identity) == 2
            and all(type(v) is int and v >= 0 for v in identity) and identity[1] > 0), "invalid runtime identity")


def selected_id(selector, ident):
    require(valid_id(ident), "invalid opaque session ID")
    require(("id" not in selector or ident == selector["id"])
            and ("id_prefix" not in selector or ident.startswith(selector["id_prefix"])), "ID contradicts selector")


def binding(value, selector):
    require(isinstance(value, dict), "invalid incarnation binding")
    selected_id(selector, value.get("id"))
    require(type(value.get("started_millis")) is int and 0 < value["started_millis"] < 2**64
            and type(value.get("attached")) is bool, "invalid incarnation fields")
    require("attached" not in selector or value["attached"] is selector["attached"], "attachment contradicts selector")
    for field in ("broker_pid", "child_pid", "start_ticks", "child_start_ticks"):
        if field in value:
            require(value[field] is None or (type(value[field]) is int and value[field] >= 0), "invalid process identity")
    if "boot_id" in value:
        require(value["boot_id"] is None or (isinstance(value["boot_id"], str) and 0 < len(value["boot_id"]) < 64
                and "\0" not in value["boot_id"]), "invalid boot identity")


def census(value, selector):
    require(isinstance(value, dict) and type(value.get("complete")) is bool, "invalid census")
    for field in ("census_count", "matches", "possible"):
        require(type(value.get(field)) is int and 0 <= value[field] <= 1024, "invalid census count")
    total = value["matches"] + value["possible"]
    rows = value.get("candidates")
    require(total <= value["census_count"] and isinstance(rows, list) and len(rows) == min(total, 32)
            and type(value.get("candidates_truncated")) is bool
            and value["candidates_truncated"] is (total > len(rows)), "invalid census display bounds")
    ids, possible = set(), 0
    for row in rows:
        require(isinstance(row, dict) and type(row.get("reachable")) is bool and type(row.get("possible")) is bool,
                "invalid candidate row")
        selected_id(selector, row.get("id"))
        require(row["id"] not in ids, "duplicate candidate ID")
        ids.add(row["id"]); possible += row["possible"]
    require(possible <= value["possible"] and len(rows) - possible <= value["matches"], "rows contradict census counts")


def unique(value, selected):
    require(value is not None and selected is not None and value["complete"] and value["matches"] == 1
            and value["possible"] == 0 and len(value["candidates"]) == 1, "missing conservative unique selection")
    row = value["candidates"][0]
    require(row["id"] == selected["id"] and row["reachable"] and not row["possible"], "selected candidate is not actionable")


def receipt(request, document, context=None):
    require(isinstance(document, dict) and document.get("schema") == "kilix.pty.selector/v1"
            and {"reason", "request_sent", "receiver_refused", "completion", "resolved", "census"} <= document.keys()
            and document.get("operation") == request["verb"] and document.get("selector") == request["selector"]
            and isinstance(document.get("runtime"), str)
            and len(json.dumps(document, ensure_ascii=True).encode()) <= 65536, "receipt does not bind request")
    require("attached" not in document["selector"] or type(document["selector"]["attached"]) is bool,
            "selector echo has invalid attachment type")
    caller(document.get("caller"))
    if document.get("runtime_context") is not None:
        runtime(document["runtime_context"])
    if context is not None:
        require(type(document.get("timeout_seconds")) in (int, float), "invalid receipt timeout")
        require(document["caller"] == context["caller"] and document.get("runtime_context") == context["runtime"]
                and document.get("timeout_seconds") == context["timeout_seconds"], "receipt does not bind captured context")
        require(document["runtime"].startswith("/") and "\0" not in document["runtime"]
                and len(json.dumps(document["runtime"], ensure_ascii=True).encode()) <= 8192, "invalid receipt runtime reference")
    sent, rejected = document.get("request_sent"), document.get("receiver_refused")
    require((sent is None or type(sent) is bool) and type(rejected) is bool, "invalid send flags")
    selected, observed = document.get("resolved"), document.get("census")
    if selected is not None:
        binding(selected, request["selector"])
    if observed is not None:
        census(observed, request["selector"])
    result, completion, reason = document.get("result"), document.get("completion"), document.get("reason")
    require(isinstance(result, str) and (reason is None or isinstance(reason, str)), "invalid result or reason")
    if request["verb"] == "resolve":
        require(result in ("unique", "ambiguous", "unresolved", "not_found", "error")
                and sent is False and not rejected and completion == "read", "invalid preview outcome")
        if result != "error":
            require(observed is not None and observed["complete"], "preview lacks complete census")
            total = observed["matches"] + observed["possible"]
            require((result == "unique" or selected is None)
                    and (result != "ambiguous" or total > 1)
                    and (result != "unresolved" or total == 1)
                    and (result != "not_found" or total == 0), "preview contradicts census")
    else:
        require(document.get("operation_id") == request["operation_id"] and type(document.get("duplicate")) is bool,
                "receipt does not bind operation key")
        require(result in ("verified_absent", "uncertain", "refused", "ambiguous"), "invalid end outcome")
        if result == "verified_absent":
            require(sent is True and not rejected and completion == "verified_absent" and reason is None,
                    "verified absence lacks accepted send")
            require(document["caller"]["status"] == "valid" and selected is not None
                    and document["caller"]["id"] != selected["id"], "success lacks valid non-own caller")
        elif result == "uncertain":
            require(sent is not False and not rejected and completion == "unknown", "uncertain history marked unsent")
        elif rejected:
            require(result == "refused" and sent is True and completion == "receiver_refused"
                    and reason in ("started_mismatch", "attachment_mismatch", "cannot_bind") and selected is not None,
                    "invalid receiver rejection")
            require(reason != "attachment_mismatch" or "attached" in request["selector"], "unsupplied attachment rejection")
            unique(observed, selected)
        else:
            require(sent is False and completion == "not_sent", "refusal has uncertain send history")
        if result == "ambiguous":
            require(observed is not None and observed["matches"] + observed["possible"] > 1, "ambiguity lacks alternatives")
    if result in ("unique", "verified_absent"):
        unique(observed, selected)


def record(value, operation_id, validate_request, fingerprint):
    require(isinstance(value, dict) and value.get("namespace") == "kilix.pty.selector/v1"
            and value.get("operation_id") == operation_id and valid_id(operation_id), "invalid operation record")
    require(isinstance(value.get("phase"), str) and value["phase"] in ("reserved", "resolved", "dispatch", "done")
            and "binding" in value and "receipt" in value, "invalid record phase")
    request, context = value.get("request"), value.get("context")
    validate_request(request)
    require(request["verb"] == "end" and request["operation_id"] == operation_id, "retained request contradicts key")
    require(isinstance(context, dict) and set(context) == {"caller", "runtime", "timeout_seconds"}, "invalid captured context")
    caller(context["caller"]); runtime(context["runtime"])
    timeout = context["timeout_seconds"]
    # Ordered finite bounds reject NaN/infinity and arbitrary-size integers
    # without first converting an unbounded integer to a C double.
    require(type(timeout) in (int, float) and 0.1 <= timeout <= 60, "invalid captured timeout")
    if "timeout_seconds" in request:
        require(timeout == float(format(request["timeout_seconds"], "g")), "captured timeout contradicts request")
    require(len(json.dumps(context, ensure_ascii=True).encode()) <= 8192
            and value.get("fingerprint") == fingerprint(request, context), "invalid retained fingerprint")
    if "updated_millis" in value:
        require(type(value["updated_millis"]) is int and value["updated_millis"] > 0, "invalid record timestamp")
    selected, outcome, phase = value["binding"], value["receipt"], value["phase"]
    if selected is not None:
        binding(selected, request["selector"])
    require((phase != "reserved" or selected is None) and (phase not in ("resolved", "dispatch") or selected is not None),
            "phase lacks original incarnation binding")
    if selected is not None:
        require(context["caller"]["status"] == "valid" and context["caller"]["id"] != selected["id"],
                "persisted selection lacks valid non-own caller")
    if phase != "done":
        require(outcome is None, "unfinished phase contains terminal receipt")
        return
    receipt(request, outcome, context)
    if selected is not None:
        require(outcome["resolved"] == selected, "receipt changed original incarnation binding")
    else:
        require(outcome["request_sent"] is False and not outcome["receiver_refused"], "send lacks persisted binding")
        require(outcome["resolved"] is None or (outcome["result"] == "refused" and outcome["reason"] == "own_session"
                and outcome["resolved"]["id"] == context["caller"]["id"]), "unbound receipt selected a target")


def broker(request, document, verb, code, expected_caller, original=None):
    require(isinstance(document, dict) and document.get("schema") == "kitty-pty-selector/v1"
            and "resolved" in document
            and document.get("operation") == verb and document.get("selector") == request["selector"]
            and document.get("caller") == expected_caller and type(code) is int, "broker receipt does not bind request")
    require("attached" not in document["selector"] or type(document["selector"]["attached"]) is bool,
            "broker selector echo has invalid attachment type")
    caller(document["caller"])
    for flag in ("actionable", "request_sent", "receiver_refused"):
        require(type(document.get(flag)) is bool, "invalid broker flag")
    census(document, request["selector"])
    selected = document.get("resolved")
    if selected is not None:
        binding(selected, request["selector"])
    require(document["actionable"] is (selected is not None), "actionable and resolved disagree")
    if selected is not None:
        unique(document, selected)
    result, reason = document.get("result"), document.get("reason")
    require(isinstance(result, str) and isinstance(reason, str) and reason in (
        "none", "invalid_selector", "caller_guard", "ambiguous", "unresolved", "started_mismatch",
        "attachment_mismatch", "cannot_bind", "not_sent", "terminate_timed_out", "read_timed_out",
        "transport_error", "census_incomplete"), "invalid broker outcome")
    sent, rejected = document["request_sent"], document["receiver_refused"]
    if verb == "resolve":
        require(not sent and not rejected and result in ("unique", "ambiguous", "unresolved", "not_found", "error"),
                "invalid broker preview")
        total = document["matches"] + document["possible"]
        require(code == (1 if result == "error" else 0), "broker preview exit disagrees")
        if result == "error":
            require(not document["complete"] and selected is None and reason != "none", "invalid census error")
        else:
            require(document["complete"] and reason == "none"
                    and (result == "unique") is document["actionable"]
                    and (result != "ambiguous" or total > 1)
                    and (result != "unresolved" or total == 1)
                    and (result != "not_found" or total == 0), "broker preview contradicts census")
        return
    require(verb == "end" and original is not None, "end lacks original binding")
    binding(original, request["selector"])
    if sent:
        unique(document, selected)
        require(selected["id"] == original["id"] and selected["started_millis"] == original["started_millis"],
                "sent request changed original binding")
        require(document["caller"]["status"] == "valid" and document["caller"]["id"] != selected["id"], "sent own/unknown caller")
    if rejected:
        require(sent and result == "refused" and code == 3
                and reason in ("started_mismatch", "attachment_mismatch", "cannot_bind"), "invalid negative ACK")
        require(reason != "attachment_mismatch" or "attached" in request["selector"], "unsupplied attachment rejection")
    elif result == "accepted":
        require(sent and code == 0 and reason == "none", "invalid accepted termination")
    elif result == "uncertain":
        require(sent and code == 1 and reason in ("terminate_timed_out", "transport_error"), "invalid uncertain termination")
    else:
        require(result == "refused" and not sent and code == 3 and reason != "none", "invalid unsent termination refusal")
        if reason == "ambiguous":
            require(document["complete"] and document["matches"] + document["possible"] > 1
                    and not document["actionable"], "ambiguous refusal lacks alternatives")
        if reason == "unresolved":
            require(document["complete"], "unresolved refusal lacks complete census")
            if document["actionable"]:
                # The broker retains its unique census when the later sole
                # candidate recheck fails before sending (selector.inc).
                require(selected["id"] == original["id"] and selected["started_millis"] == original["started_millis"],
                        "unresolved recheck changed original binding")
                require(document["caller"]["status"] == "valid" and document["caller"]["id"] != selected["id"],
                        "unresolved recheck lacks valid non-own caller")
            else:
                require(document["matches"] + document["possible"] <= 1, "unresolved refusal contradicts census")
        if reason == "caller_guard":
            require(document["caller"]["status"] != "valid" or (selected is not None
                    and selected["id"] == document["caller"]["id"]), "caller guard contradicts caller echo")
