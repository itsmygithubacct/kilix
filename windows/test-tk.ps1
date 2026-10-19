#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ApplicationDirectory)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
$ApplicationDirectory = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
$savedPath = $env:PATH
$savedPythonHome = $env:PYTHONHOME
$savedPythonPath = $env:PYTHONPATH
$savedTcl = $env:TCL_LIBRARY
$savedTk = $env:TK_LIBRARY
try {
    $runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $ApplicationDirectory 'tk-runtime.json') | ConvertFrom-Json
    $env:TCL_LIBRARY = Join-Path $ApplicationDirectory $runtime.tcl
    $env:TK_LIBRARY = Join-Path $ApplicationDirectory $runtime.tk
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    $code = "import tkinter as tk; from tkinter import ttk; root=tk.Tk(); root.withdraw(); tree=ttk.Treeview(root); tree.insert('', 'end', text='Kilix'); root.update_idletasks(); root.destroy(); print('KILIX_TK_OK')"
    $result = Invoke-KilixProcess (Join-Path $ApplicationDirectory 'kitty/launcher/kitty-console.exe') @('+runpy', $code)
    if ($result.exitCode -ne 0 -or !$result.stdout.Contains('KILIX_TK_OK')) { throw "Packaged Tcl/Tk failed with a clean PATH: $($result.stderr)" }
    Write-Host 'PASS: packaged tkinter, Tcl/Tk data, DLL closure and ttk widget creation with clean PATH'
} finally {
    $env:PATH = $savedPath
    $env:PYTHONHOME = $savedPythonHome
    $env:PYTHONPATH = $savedPythonPath
    $env:TCL_LIBRARY = $savedTcl
    $env:TK_LIBRARY = $savedTk
}
