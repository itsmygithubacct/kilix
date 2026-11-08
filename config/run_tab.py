"""Open the already selected app command in one owned tab."""
import os
import sys

from launch_transport import literal_launch


def main() -> None:
    kitten, password, title, launcher, *arguments = sys.argv[1:]
    argv, environment, _ = literal_launch(
        [launcher, "run", *arguments],
        {"KILIX_IN_OVERLAY": "1", "KILIX_STREAM": os.environ.get("KILIX_STREAM", ""),
         "KILIX_RUN_READY_FILE": os.environ.get("KILIX_RUN_READY_FILE", "")})
    # Titles are display text; the target's path/argv remain exact in the envelope.
    title = title.encode("utf-8", "replace").decode("utf-8")
    command = [kitten, "@", "--password-file", password,
               "launch", "--type=tab", "--cwd=current", "--self", "--tab-title", title]
    for key, value in environment.items():
        command += ["--env", f"{key}={value}"]
    os.execv(kitten, [*command, "--", *argv])


if __name__ == "__main__":
    main()
