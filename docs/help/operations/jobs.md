# Kilix 0.2.2: Starting a command in another pane

Use -- to separate launcher options from the command that the new pane or tab should execute. A pane launched with a short-lived command can disappear as soon as that command exits.

For held output, use `kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" launch --location=vsplit --hold --title results -- ./run-tests.sh`. The basic kilix new-pane wrapper does not expose --hold; use the authenticated kitten launch interface for that option.

Before typing a shell command into an existing pane, inspect its screen and foreground processes to establish that an idle shell owns the input. Keep a long-running job in the foreground of its visible pane when that pane is intended to monitor the job.

Use the pane's existing visible frontend for monitoring; a second read-write broker attachment can fail because the session is already attached. For a large script, write an explicit script file and send a short command to run it after verifying the target shell.
