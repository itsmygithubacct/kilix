"""Terminal and optional Tk catalog choosers over the same management API."""
import os
import queue
import shlex
import subprocess
import sys
import threading

from catalog_store import entries


def capability_details(reported):
    if isinstance(reported, dict):
        return '\n'.join(name + ': ' + (('available' if value else 'unavailable')
                         if isinstance(value, bool) else str(value))
                         for name, value in reported.items())
    return '\n'.join(item['name'] + ': ' + item['reason'] for item in reported)


def split_arguments(value):
    if os.name != 'nt':
        return shlex.split(value)
    # Windows paths contain backslashes. Parse them by Windows argv rules,
    # without sending the text to PowerShell or cmd.
    import ctypes
    count = ctypes.c_int()
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    result = shell.CommandLineToArgvW('kilix ' + value, ctypes.byref(count))
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [result[index] for index in range(1, count.value)]
    finally:
        kernel.LocalFree(result)


def launch(catalog, spec, arguments, execute):
    argv = catalog.argv(spec) + arguments
    return execute(argv) if execute else subprocess.call(argv, env=dict(os.environ, KITTYFB_TRANSPORT='inline'))


def terminal(catalog, kind, execute=None):
    if not sys.stdin.isatty():
        raise ValueError('The chooser needs an interactive terminal; use list --json for scripts')
    specs = entries(kind)
    while True:
        print('\nKilix ' + kind + ' — ' + catalog.runtime)
        for number, spec in enumerate(specs, 1):
            status = catalog.status(spec)
            print(f"{number:2}. {spec['title']:<27} {status['status']}")
        print('l NUMBER [ARGS] launch | i NUMBER install | u NUMBER update | '
              'e/d NUMBER enable/disable | ? NUMBER details | q quit')
        try:
            words = split_arguments(input('> '))
            if not words:
                continue
            if words == ['q']:
                return 0
            if len(words) < 2 or not words[1].isdigit() or not 1 <= int(words[1]) <= len(specs):
                raise ValueError('Choose a displayed number')
            action, spec = words[0], specs[int(words[1]) - 1]
            if action == 'l':
                launch(catalog, spec, words[2:], execute)
            elif action in ('i', 'u') and len(words) == 2:
                catalog.install_item(spec)
            elif action in ('e', 'd') and len(words) == 2:
                catalog.set_enabled(spec, action == 'e')
            elif action == '?' and len(words) == 2:
                import json
                print(json.dumps(catalog.status(spec), indent=2))
            else:
                raise ValueError('Choose l, i, u, e, d, ?, or q')
        except (EOFError, KeyboardInterrupt):
            return 0
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            print('kilix: ' + str(error), file=sys.stderr)


def graphical(catalog, kind, execute=None):
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
        window = tk.Tk()
    except (ImportError, RuntimeError) as error:
        raise ValueError('Graphical chooser requires tkinter and a display; use choose without --gui') from error
    except Exception as error:
        # TclError also covers a machine without a display server.
        raise ValueError('Cannot open graphical chooser; use choose without --gui: ' + str(error)) from error
    window.title('Kilix ' + kind + ' — ' + catalog.runtime)
    window.geometry('850x570')
    specs = entries(kind)
    frame = ttk.Frame(window)
    frame.pack(fill='both', expand=True)
    table = ttk.Treeview(frame, columns=('runtime', 'state'), selectmode='browse')
    scroll = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    table.heading('#0', text='Application' if kind == 'apps' else 'Game')
    table.heading('runtime', text='Runtime')
    table.heading('state', text='Readiness')
    scroll.pack(side='right', fill='y')
    table.pack(side='left', fill='both', expand=True)
    detail = tk.StringVar(value='Select an entry to see prerequisites and support details.')
    ttk.Label(window, textvariable=detail, wraplength=810).pack(fill='x', padx=12, pady=10)
    ttk.Label(window, text='Launch arguments (quote file paths containing spaces):').pack(anchor='w', padx=12)
    arguments = ttk.Entry(window)
    arguments.pack(fill='x', padx=12, pady=4)
    controls = ttk.Frame(window)
    controls.pack(fill='x', padx=12, pady=10)
    messages = queue.Queue()
    busy = False

    def refresh():
        for index, spec in enumerate(specs):
            status = catalog.status(spec)
            if table.exists(str(index)):
                table.item(str(index), values=(catalog.runtime, status['status']))
            else:
                table.insert('', 'end', iid=str(index), text=spec['title'], values=(catalog.runtime, status['status']))

    def selected():
        selected_rows = table.selection()
        if not selected_rows:
            raise ValueError('Select an entry first')
        return specs[int(selected_rows[0])]

    def selection(event=None):
        try:
            spec = selected()
            status = catalog.status(spec)
            capabilities = capability_details(status.get('capabilities', []))
            detail.set(status['reason'] + '\n' + spec.get('repository', '') +
                       ('\nBuild packages: ' + ', '.join(spec['packages']) if spec.get('packages') else '') +
                       ('\n' + capabilities if capabilities else ''))
        except ValueError:
            pass

    def task(action):
        nonlocal busy
        if busy:
            return
        try:
            spec = selected()
            argv = split_arguments(arguments.get()) if action == 'Launch' else []
        except ValueError as error:
            messagebox.showerror('Kilix', str(error), parent=window)
            return
        busy = True
        detail.set(action + ': ' + spec['name'] + '. Build output appears in the launching terminal.')
        for button in controls.winfo_children():
            button.configure(state='disabled')

        def worker():
            try:
                if action in ('Install', 'Update'):
                    catalog.install_item(spec)
                elif action in ('Enable', 'Disable'):
                    catalog.set_enabled(spec, action == 'Enable')
                else:
                    launch(catalog, spec, argv, execute)
                messages.put(None)
            except Exception as error:
                messages.put(str(error))
        threading.Thread(target=worker, daemon=False).start()

    def poll():
        nonlocal busy
        try:
            error = messages.get_nowait()
        except queue.Empty:
            pass
        else:
            busy = False
            refresh()
            selection()
            for button in controls.winfo_children():
                button.configure(state='normal')
            if error:
                messagebox.showerror('Kilix', error, parent=window)
        window.after(100, poll)

    def close():
        if busy:
            messagebox.showinfo('Kilix', 'Wait for the active install or application to finish.', parent=window)
        else:
            window.destroy()

    for label in ('Launch', 'Install', 'Update', 'Enable', 'Disable'):
        ttk.Button(controls, text=label, command=lambda action=label: task(action)).pack(side='left', padx=4)
    table.bind('<<TreeviewSelect>>', selection)
    window.protocol('WM_DELETE_WINDOW', close)
    refresh()
    poll()
    window.mainloop()
    return 0
