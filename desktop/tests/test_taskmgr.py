"""Task Manager: lists running windows; Switch To / End Task drive the WM."""
import harness as H
from apps.taskmgr import TaskManager
from apps.notepad import Notepad
from apps.calc import Calc


d = H.make_desk()
np = Notepad(d, None); d.wm.add(np)
ca = Calc(d, None); d.wm.add(ca)
tm = TaskManager(d); d.wm.add(tm)

# both other windows are listed; the task manager excludes itself
listed = [it[2] for it in tm.list.items]
assert np in listed and ca in listed, listed
assert tm not in listed

# a task count shows in the status line
assert len(tm.list.items) == 2, len(tm.list.items)

# Switch To activates the selected window
tm._sel = np
tm._switch()
assert d.wm.active is np, d.wm.active

# End Task closes the selection and the list shrinks on refresh
tm._sel = ca
n0 = len(tm.list.items)
tm._end()
assert ca not in d.wm.windows, "calc still open"
assert len(tm.list.items) == n0 - 1, (n0, len(tm.list.items))

# the live tick picks up a newly opened window without manual refresh
sol_before = len(tm.list.items)
op = Notepad(d, None); d.wm.add(op)
tm._tick(0.0)
assert len(tm.list.items) == sol_before + 1, tm.list.items

# renders clean
d.dirty = True
d.render()

print("ok")
