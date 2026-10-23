"""Trusted supervisor API, never a CLI or an RPC issuance method.

The supervisor owns this object, broker/state access and the direct-user event
source. Give an untrusted actor only dispatch(), never select() or Python access.
See docs/pty-controller.md for the required isolation and entrypoint policy.
"""
from __future__ import annotations

import copy
import math
import threading
import time

import kilix_pty_selector as selector


class Controller:
    def __init__(self, broker, state, *, clock=time.monotonic, pane_lookup=None):
        self._broker = broker
        self._state = state
        self._clock = clock
        self._lock = threading.RLock()
        self._grant = None
        self._pane_lookup = pane_lookup

    def revoke(self):
        """Linearizes before dispatch or after its outcome; cannot undo a send."""
        with self._lock:
            self._grant = None

    def select(self, direct_user_request, *, lifetime_seconds=30, expected_binding=None):
        """Called ONLY by trusted direct-user selection code, before actor ingress.

        A new selection revokes the previous selection, including on refusal.
        No opaque token, environment field or model text is accepted as provenance.
        """
        with self._lock:
            self._grant = None
            if (type(lifetime_seconds) not in (int, float) or not math.isfinite(lifetime_seconds)
                    or not 0 < lifetime_seconds <= 60):
                raise ValueError("selection lifetime must be finite, positive and at most 60 seconds")
            request = copy.deepcopy(selector.validate(direct_user_request))
            if request["verb"] != "end":
                raise ValueError("only an explicit end selection can issue authority")
            deadline = self._clock() + lifetime_seconds
            if "timeout_seconds" in request:
                self._broker.timeout = format(request["timeout_seconds"], "g")
            context = selector.capture(self._broker)
            if context["caller"]["status"] != "valid" or context["runtime"]["directory_identity"] is None:
                return {"issued": False, "reason": "context_unavailable"}
            doc, code, _ = selector.broker_document(self._broker, request, "resolve")
            if (doc is None or code or not doc["complete"] or not doc["actionable"]
                    or doc["resolved"]["id"] == context["caller"]["id"]):
                return {"issued": False, "reason": "selection_not_actionable"}
            if expected_binding is not None and doc["resolved"] != expected_binding:
                return {"issued": False, "reason": "selection_changed"}
            if self._clock() >= deadline:
                return {"issued": False, "reason": "authority_expired"}
            self._grant = {"request": request, "context": copy.deepcopy(context),
                           "binding": copy.deepcopy(doc["resolved"]), "deadline": deadline,
                           "consumed": False, "result": None}
            return {"issued": True, "operation_id": request["operation_id"],
                    "selector": copy.deepcopy(request["selector"]),
                    "resolved": copy.deepcopy(doc["resolved"]), "lifetime_seconds": lifetime_seconds,
                    "issued_monotonic": deadline - lifetime_seconds, "expires_monotonic": deadline}

    def read(self, actor_request):
        """Validated bounded data reads; optional owned-pane data lookup only."""
        import json
        import kilix_pty_request as legacy
        request = legacy.validate(json.dumps(actor_request, ensure_ascii=True).encode())
        if request["verb"] not in {"list", "status", "observe", "reaped", "journals", "pane"}:
            raise ValueError("unsupported constrained read")
        with self._lock:
            previous_timeout = self._broker.timeout
            try:
                if "timeout_seconds" in request:
                    self._broker.timeout = format(request["timeout_seconds"], "g")
                if "pane_id" in request.get("args", {}):
                    pane = request["args"]["pane_id"]
                    if self._pane_lookup is None:
                        return 3, self._broker.envelope(result="refused", reason="pane_lookup_unavailable", pane_id=pane)
                    code, mapped = self._pane_lookup.lookup(pane, self._broker)
                    if code or request["verb"] == "pane":
                        return code, mapped
                    request = copy.deepcopy(request)
                    request["args"] = {"id": mapped["id"]}
                return legacy.execute(self._broker, request, False, self._state)
            finally:
                self._broker.timeout = previous_timeout

    def read_cli(self, argv):
        import kilix_pty_reads
        with self._lock:
            return kilix_pty_reads.execute(self, argv)

    def dispatch(self, actor_request):
        """Only actor-facing entrypoint. Exact request match; no issuance or fallback."""
        with self._lock:
            request = copy.deepcopy(selector.validate(actor_request))
            if request["verb"] == "resolve":
                return selector.execute(self._broker, request, False, self._state)
            context = selector.capture(self._broker)
            grant = self._grant

            def refuse(reason):
                return 3, selector.receipt(self._broker, request, context, "refused", reason=reason)

            if grant is None:
                return refuse("authority_required")
            if request != grant["request"] or context != grant["context"]:
                self._grant = None
                return refuse("authority_mismatch")
            if self._clock() >= grant["deadline"]:
                self._grant = None
                return refuse("authority_expired")
            if grant["consumed"]:
                if grant["result"] is None:
                    return refuse("authority_consumed")
                code, result = copy.deepcopy(grant["result"])
                result["duplicate"] = True
                return code, result
            # Consume before any store admission or broker operation. Exceptions,
            # cancellation and uncertain outcomes never restore dispatch authority.
            grant["consumed"] = True

            def check(current_request, current_context, binding):
                if self._clock() >= grant["deadline"]:
                    return "authority_expired"
                if (current_request != grant["request"] or current_context != grant["context"]
                        or binding != grant["binding"]):
                    return "authority_mismatch"
                return None

            result = selector.execute(self._broker, request, True, self._state, before_dispatch=check)
            grant["result"] = copy.deepcopy(result)
            return result
