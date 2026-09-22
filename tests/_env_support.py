"""The environment a test hands to a child process.

A Kilix session exports upwards of sixty variables -- storage roots, state and
cache directories, chrome settings, Kitty control channels. A test that copies
``os.environ`` and overrides only the handful it cares about hands the child a
sandbox path for those and the operator's live session for everything else. The
product then refuses the mixture, correctly, and the suite reports a failure
that belongs to the machine rather than to the code.

So strip the whole family first and put back only what the test names.
"""

import os

#: Every variable this stack uses to locate itself. ``KILIX`` deliberately has
#: no trailing underscore: it must also catch ``KILIX95_*``. ``PLEB`` is here
#: because the five modules this helper replaced stripped ``PLEB_*`` inline,
#: and a shared helper that dropped it would have narrowed them: measured
#: against a live 120-variable session, the inline tuple stripped 51 names and
#: this tuple strips 69, missing none of the 51.
STACK_PREFIXES = ("KILIX", "GPU_TERMINAL", "KITTY", "PLEB")


def sandbox_env(**overrides):
    """The parent environment minus every stack variable, plus *overrides*.

    Overrides are applied last, so a test can name a variable whose prefix was
    just stripped -- which is the usual case, since the point is to point the
    child at a sandbox rather than at the session running the tests.
    """
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(STACK_PREFIXES)
    }
    env.update(overrides)
    return env
