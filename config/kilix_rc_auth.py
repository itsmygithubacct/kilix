"""Allow only the remote-control operations used by Kilix itself."""


def is_cmd_allowed(pcmd, window, from_socket, extra_data):
    command = pcmd.get("cmd")
    payload = pcmd.get("payload")
    if not isinstance(payload, dict):
        return False

    if command == "resize-os-window":
        return (
            not from_socket
            and window is not None
            and payload.get("self") is True
            and not payload.get("match")
            and payload.get("action") == "toggle-fullscreen"
        )

    if command == "action":
        return (
            payload.get("action") == "load_config_file"
            and not payload.get("match_window")
        )

    if command == "set-font-size":
        return True

    if command == "send-text":
        # send-text types into a window, so this is a keystroke channel. It is
        # the UNAUTHENTICATED path -- kilix's own tooling reaches send-text with
        # the launcher's private runtime credential and is unaffected by any of
        # this. Permitted within one OS window: a pane may type into itself or a
        # sibling it shares a window with, and may not reach another window.
        if window is None or payload.get("all"):
            return False

        match = payload.get("match") or ""
        if not match:
            return True                      # self-targeted

        try:
            from kitty.fast_data_types import get_boss

            targets = list(get_boss().match_windows(match, self_window=window))
        except Exception:
            return False                     # cannot prove it -> refuse

        # all() over an empty sequence is True, so a match resolving to nothing
        # would otherwise be permitted by vacuous truth. Require real targets.
        if not targets:
            return False

        return all(
            getattr(t, "os_window_id", None) == window.os_window_id
            for t in targets
        )

    return False
