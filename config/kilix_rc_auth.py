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

    return False
