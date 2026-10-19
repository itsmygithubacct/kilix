#requires -Version 5.1
<# Offline backend checks. No valid desktop operation is executed.
   On Windows, compiles with the same .NET Framework assemblies as the worker.
   -BackendAssembly accepts a separately compiled assembly for non-Windows CI.
#>
param([string]$BackendAssembly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$script:checks = 0
$worker = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../desktop.ps1'))
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../desktop-native.cs'))
$onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT

if ($BackendAssembly) {
    [Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($BackendAssembly)) | Out-Null
} else {
    if (!$onWindows) { throw 'On non-Windows hosts supply -BackendAssembly compiled against .NET Framework references.' }
    $names = @('System', 'System.Core', 'System.Windows.Forms', 'System.Drawing', 'UIAutomationClient', 'UIAutomationTypes', 'WindowsBase')
    $references = foreach ($name in $names) {
        Add-Type -AssemblyName $name
        ([AppDomain]::CurrentDomain.GetAssemblies() | Where-Object { $_.GetName().Name -eq $name } | Select-Object -First 1).Location
    }
    Add-Type -LiteralPath $source -ReferencedAssemblies $references
}

function Assert($Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
    $script:checks++
}
function Expect-Code([scriptblock]$Body, [string]$Code = 'invalid_request') {
    $caught = $null
    try { & $Body | Out-Null } catch {
        $caught = $_.Exception
        while ($caught.InnerException) { $caught = $caught.InnerException }
    }
    Assert ($null -ne $caught) "Expected error $Code."
    Assert ($caught.GetType().FullName -eq 'KilixDesktopException') "Unexpected exception type: $($caught.GetType().FullName)"
    Assert ($caught.Code -eq $Code) "Expected $Code; received $($caught.Code)."
}
function New-Request([string]$Operation, [hashtable]$Parameters = @{}, [bool]$Targeted = $false) {
    $request = @{ schema = 'kilix.desktop/v1'; operation = $Operation; params = $Parameters }
    if ($Targeted) { $request.target = @{ hwnd = '123456'; pid = 123; started = '133700000000000000' } }
    return $request
}
function Valid($Request) {
    # Round-trip through the actual strict parser, not PowerShell's object conversion.
    $json = ConvertTo-Json -InputObject $Request -Depth 16 -Compress
    [KilixDesktop]::Validate([KilixDesktop]::ReadRequest($json))
    $script:checks++
}
function Invalid($Request) {
    $json = ConvertTo-Json -InputObject $Request -Depth 16 -Compress
    Expect-Code { [KilixDesktop]::Validate([KilixDesktop]::ReadRequest($json)) }
}

Valid (New-Request 'status')
Valid (New-Request 'windows' @{ title = ''; pid = 1 })
Valid (New-Request 'windows' @{ pid = 4294967295 })
Valid (New-Request 'inspect' @{ depth = 0; limit = 1 } $true)
Valid (New-Request 'inspect' @{ depth = 8; limit = 500 } $true)
Valid (New-Request 'screenshot' @{ path = 'C:\captures\one.png'; overwrite = $false })
Valid (New-Request 'screenshot' @{ path = '\\server\share\one.PNG'; overwrite = $true } $true)
Valid (New-Request 'capture')
Valid (New-Request 'capture' @{} $true)
Invalid (New-Request 'capture' @{path='C:\captures\one.png'})
Invalid (New-Request 'capture' @{overwrite=$true})
# Exercise the actual bounded native PNG stream without accessing a desktop.
Assert ([KilixDesktop]::MaxCaptureBytes -eq 33554432) 'PNG limit must be 32 MiB.'
$streamType = [KilixDesktop].GetNestedType('BoundedPngStream', [Reflection.BindingFlags]::NonPublic)
$stream = [Activator]::CreateInstance($streamType, $true)
try {
    $stream.Position = [KilixDesktop]::MaxCaptureBytes - 1
    $stream.WriteByte(7)
    Assert ($stream.Length -eq [KilixDesktop]::MaxCaptureBytes) 'Exact PNG limit should fit.'
    Expect-Code { $stream.WriteByte(8) } 'capture_limit'
    Assert ($stream.Exceeded) 'Stream must retain the limit failure for GDI+ exception wrapping.'
    Expect-Code { $stream.SetLength([KilixDesktop]::MaxCaptureBytes + 1) } 'capture_limit'
    $stream.Position = [KilixDesktop]::MaxCaptureBytes - 1
    Expect-Code { $stream.Write([byte[]]@(1, 2), 0, 2) } 'capture_limit'
} finally { $stream.Dispose() }
foreach ($operation in @('focus', 'minimize', 'maximize', 'restore', 'close')) { Valid (New-Request $operation @{} $true) }
Valid (New-Request 'move' @{ x = -2147483648; y = -200; width = 16384; height = 1 } $true)
$unicode = [char]0x03bb + [char]::ConvertFromUtf32(0x1F642)
Valid (New-Request 'launch' @{ argv = @('example.exe', '', 'a b', 'a"b', 'C:\trailing\', 'literal & | > %PATH% $text', $unicode); cwd = 'C:\working dir' })
Valid (New-Request 'launch' @{ argv = @('example.exe'); cwd = '\\server\share' })
Valid (New-Request 'type' @{ text = $unicode + "`r`n`t" } $true)
Valid (New-Request 'type' @{ text = ('x' * 16384) } $true)
Valid (New-Request 'type' @{ text = ([char]::ConvertFromUtf32(0x1F642) * 8192) } $true)
Valid (New-Request 'key' @{ keys = @('CTRL', 'A') } $true)
foreach ($key in @('F1', 'F12', 'SHIFT', 'ALT', 'WIN', 'TAB', 'ESC', 'BACKSPACE', 'DELETE', 'HOME', 'END', 'PAGEUP', 'PAGEDOWN', 'LEFT', 'RIGHT', 'UP', 'DOWN', 'ENTER', '9')) {
    Valid (New-Request 'key' @{ keys = @($key) } $true)
}
Valid (New-Request 'click' @{ x = 0; y = 1; button = 'middle'; count = 2 } $true)
foreach ($delta in @(-1200, 0, 1200)) { Valid (New-Request 'scroll' @{ x = 1; y = 2; delta = $delta } $true) }
Valid (New-Request 'invoke' @{ element = '42.-2147483648.0.2147483647' } $true)
Valid (New-Request 'set-value' @{ element = '42.1'; text = $unicode } $true)

# Selector fields and condition budgets are checked before any OS/provider access.
foreach ($app in @('Editor.exe', 'My Editor.EXE', 'C:\Apps\editor.exe', 'C:/Apps/editor.exe', '\\server\apps\editor.exe')) {
    Valid (New-Request 'windows' @{app=$app;title='notes';pid=123})
    Valid (New-Request 'find' @{app=$app;include_hidden=$true})
}
Valid (New-Request 'find')
Valid (New-Request 'find' @{selector=@{name='Save';automation_id='save';control_type='Button'}} $true)
Valid (New-Request 'find' @{app='editor.exe';selector=@{name='';automation_id=''};depth=64;limit=20000})
Valid (New-Request 'invoke' @{selector=@{name='Save';control_type='Button'};depth=0;limit=1} $true)
Valid (New-Request 'set-value' @{selector=@{automation_id='edit'};text=$unicode} $true)
foreach ($state in @('present','visible','enabled','closed')) {
    Valid (New-Request 'wait' @{state=$state;app='editor.exe';title='notes';pid=123;wait_ms=0;poll_ms=20})
    Valid (New-Request 'wait' @{state=$state;selector=@{name='Save';control_type='Button'};wait_ms=19000;poll_ms=1000} $true)
    Valid (New-Request 'wait' @{state=$state;element='42.1'} $true)
}
$request = New-Request 'wait' @{state='closed';wait_ms=59000} $true; $request.timeout=60; Valid $request
$request = New-Request 'wait' @{state='closed'} $true; $request.timeout=1; Valid $request
foreach ($app in @('', '*.exe', 'editor?', 'app|other', 'C:app.exe', 'relative\app.exe', 'C:\Apps\', 'C:\Apps\.', 'C:\Apps\..', '\\.\app.exe')) {
    Invalid (New-Request 'find' @{app=$app})
}
foreach ($selector in @(@{}, @{unknown='Save'}, @{name=$null}, @{automation_id=1}, @{control_type='ControlType.Button'}, @{control_type="Button`n"}, @{control_type=''})) {
    Invalid (New-Request 'find' @{selector=$selector} $true)
    Invalid (New-Request 'invoke' @{selector=$selector} $true)
}
Invalid (New-Request 'find' @{} $true)
Invalid (New-Request 'find' @{selector=@{name='Save'}})
Invalid (New-Request 'find' @{selector=@{name='Save'};app='editor.exe'} $true)
Invalid (New-Request 'find' @{selector=@{name='Save'};include_hidden=$false} $true)
Invalid (New-Request 'find' @{include_hidden=1})
Invalid (New-Request 'find' @{depth=3})
Invalid (New-Request 'invoke' @{element='42.1';selector=@{name='Save'}} $true)
foreach ($depth in @(-1,65,$true)) { Invalid (New-Request 'find' @{selector=@{name='Save'};depth=$depth} $true) }
foreach ($limit in @(0,20001,$true)) { Invalid (New-Request 'invoke' @{selector=@{name='Save'};limit=$limit} $true) }
Invalid (New-Request 'wait' @{state='visible'})
Invalid (New-Request 'wait' @{state='hidden'} $true)
Invalid (New-Request 'wait' @{state='closed';element='42.1';app='editor.exe'})
Invalid (New-Request 'wait' @{state='closed';element='42.1';selector=@{name='Save'}} $true)
Invalid (New-Request 'wait' @{state='visible';depth=3} $true)
foreach ($duration in @(-1,19001,$true,1.5)) { Invalid (New-Request 'wait' @{state='visible';wait_ms=$duration} $true) }
foreach ($interval in @(0,19,1001,$true)) { Invalid (New-Request 'wait' @{state='visible';poll_ms=$interval} $true) }
$request = New-Request 'wait' @{state='visible';wait_ms=1} $true; $request.timeout=1; Invalid $request

foreach ($json in @('', 'null', '[]', '[{}]', '{', '{} trailing', '{"x":1,}', '{"x":[1,]}', '{"x":NaN}', '{"x":01}', '{"x":1.}', '{"x":1e}', '{"x":+1}', '{"x":1e999}', '{"x":"\q"}', '{"x":"\uD800"}', '{"x":"\uDC00"}', '{"x":"\u0000"}', '{"x":1,"x":2}', '{/* comment */}', ('{"x":"a' + "`n" + 'b"}'))) {
    Expect-Code { [KilixDesktop]::ReadRequest($json) }
}
Expect-Code { [KilixDesktop]::ReadRequest(('[' * 34) + '0' + (']' * 34)) }
Expect-Code { [KilixDesktop]::ReadRequest('{"schema":"kilix.desktop/v1","schema":"bad","operation":"status","params":{}}') }
Expect-Code { [KilixDesktop]::Validate([KilixDesktop]::ReadRequest('{"schema":"kilix.desktop/v1","Schema":"bad","operation":"status","params":{}}')) }
foreach ($json in @('{"schema":"kilix.desktop/v1","operation":"status","params":{},"timeout":1.0}', '{"schema":"kilix.desktop/v1","operation":"status","params":{},"timeout":1e0}')) {
    Expect-Code { [KilixDesktop]::Validate([KilixDesktop]::ReadRequest($json)) }
}
foreach ($bad in @($null, $false, @(), 'object')) { Expect-Code { [KilixDesktop]::Validate(@{schema='kilix.desktop/v1'; operation='status'; params=$bad}) } }
foreach ($schema in @($null, 'kilix.desktop/v2', 1)) { $request = New-Request 'status'; $request.schema = $schema; Invalid $request }
foreach ($operation in @('', 'STATUS', 'shell', 'terminal')) { Invalid (New-Request $operation) }
$request = New-Request 'status'; $request.Remove('params'); Invalid $request
$request = New-Request 'status'; $request.extra = $true; Invalid $request
Invalid (New-Request 'status' @{ extra = 1 })
Invalid (New-Request 'status' @{} $true)
foreach ($timeout in @($null, $true, '20', 0, 61, 1.5)) { $request = New-Request 'status'; $request.timeout = $timeout; Invalid $request }
foreach ($timeout in @(1, 60)) { $request = New-Request 'status'; $request.timeout = $timeout; Valid $request }
foreach ($operation in @('inspect','focus','move','minimize','maximize','restore','close','type','key','click','scroll','invoke','set-value')) { Invalid (New-Request $operation) }
foreach ($field in @('hwnd','pid','started')) { $request = New-Request 'focus' @{} $true; $request.target.Remove($field); Invalid $request }
$request = New-Request 'focus' @{} $true; $request.target.extra = 1; Invalid $request
foreach ($hwnd in @($null, 123, '0', '-1', '0x12', '18446744073709551616', '12.3')) { $request = New-Request 'focus' @{} $true; $request.target.hwnd = $hwnd; Invalid $request }
foreach ($pidValue in @($null, $true, '123', 0, -1, 4294967296, 1.5)) { $request = New-Request 'focus' @{} $true; $request.target.pid = $pidValue; Invalid $request }
foreach ($start in @($null, 123, '-1', '0', 'x')) { $request = New-Request 'focus' @{} $true; $request.target.started = $start; Invalid $request }
foreach ($depth in @(-1, 9, $true, 2.5)) { Invalid (New-Request 'inspect' @{depth=$depth} $true) }
foreach ($limit in @(0, 501, '20')) { Invalid (New-Request 'inspect' @{limit=$limit} $true) }
foreach ($path in @('one.png', 'C:one.png', '\one.png', 'C:\one.jpg', 'C:\file:stream.png', '\\.\device.png', '\\?\C:\file.png', 'C:\wild*.png')) { Invalid (New-Request 'screenshot' @{path=$path}) }
Invalid (New-Request 'screenshot' @{path='C:\one.png'; overwrite=1})
Invalid (New-Request 'move' @{x=2147483647; y=0; width=1; height=1} $true)
Invalid (New-Request 'move' @{x=0; y=0; width=0; height=1} $true)
Invalid (New-Request 'move' @{x=0; y=0; width=1; height=16385} $true)
foreach ($argv in @(@(), @(''), @('script.cmd'), @('script.bat'), @(' example.exe'), @('"example.exe"'), @('example.exe', $true), @('example.exe', ('x' * 32767)))) { Invalid (New-Request 'launch' @{argv=$argv}) }
Invalid (New-Request 'launch' @{argv=@('example.exe'); cwd='relative'})
Invalid (New-Request 'launch' @{argv=('example.exe' * 1), ('x' * 32760)})
Invalid (New-Request 'launch' @{argv=(@('example.exe') + (@('a') * 64))})
Invalid (New-Request 'type' @{text=('x' * 16385)} $true)
Invalid (New-Request 'type' @{text=([char]::ConvertFromUtf32(0x1F642) * 8193)} $true)
Invalid (New-Request 'type' @{text=$true} $true)
foreach ($keys in @(@(), @('F13'), @('F01'), @('CTRL','CTRL'), @('CTRL','ctrl'), @('CTRL+A'), @('a b'), @($true))) { Invalid (New-Request 'key' @{keys=$keys} $true) }
Invalid (New-Request 'click' @{x=-1;y=0} $true)
Invalid (New-Request 'click' @{x=0;y=0;button='other'} $true)
Invalid (New-Request 'click' @{x=0;y=0;count=3} $true)
Invalid (New-Request 'scroll' @{x=0;y=0;delta=1201} $true)
Invalid (New-Request 'scroll' @{x=0;y=0;delta=0.5} $true)
Invalid (New-Request 'scroll' @{delta=120} $true)
foreach ($id in @('', 'button', '42..1', '42.+1', '42.01', '42.2147483648')) { Invalid (New-Request 'invoke' @{element=$id} $true) }
Invalid (New-Request 'set-value' @{element='42.1';text=('x' * 16385)} $true)

# Exact quoting fixtures exercise data that must not become shell syntax.
foreach ($pair in @(
    @('', '""'), @('plain', '"plain"'), @('a b', '"a b"'), @('a"b', '"a\"b"'),
    @('C:\x\', '"C:\x\\"'), @('a\"b', '"a\\\"b"'), @('& | %PATH% $(value)', '"& | %PATH% $(value)"'),
    @($unicode, ('"' + $unicode + '"'))
)) { Assert ([KilixDesktop]::QuoteArgument($pair[0]) -ceq $pair[1]) 'Literal Windows argument quoting changed.' }

# These layout assertions prevent a silent 32-bit INPUT layout on x64.
$nativeType = [KilixDesktop].GetNestedType('Native', [Reflection.BindingFlags]::NonPublic)
$inputType = $nativeType.GetNestedType('INPUT', [Reflection.BindingFlags]::Public)
$unionType = $nativeType.GetNestedType('INPUTUNION', [Reflection.BindingFlags]::Public)
Assert ([Runtime.InteropServices.Marshal]::SizeOf([Activator]::CreateInstance($inputType)) -eq 40) 'x64 INPUT must be 40 bytes.'
Assert ([Runtime.InteropServices.Marshal]::OffsetOf($inputType, 'Data').ToInt32() -eq 8) 'x64 INPUT union must start at offset 8.'
Assert ([Runtime.InteropServices.Marshal]::SizeOf([Activator]::CreateInstance($unionType)) -eq 32) 'x64 INPUT union must be 32 bytes.'

# Pure matching predicates run on either platform, without loading any UIA provider.
$appMatch = [KilixDesktop].GetMethod('MatchesApp', [Reflection.BindingFlags]'NonPublic, Static')
foreach ($pair in @(@('EDITOR.EXE','C:\Apps\editor.exe'), @('C:/Apps/Editor.exe','c:\apps\editor.exe'))) {
    Assert ($appMatch.Invoke($null, [object[]]$pair)) 'Exact app matching must ignore case and path separators.'
}
foreach ($pair in @(@('edit','C:\Apps\editor.exe'), @('editor.exe','C:\Apps\other-editor.exe'), @('C:\Other\editor.exe','C:\Apps\editor.exe'))) {
    Assert (!$appMatch.Invoke($null, [object[]]$pair)) 'App matching must not accept prefixes, substrings or another path.'
}
$elementMatch = [KilixDesktop].GetMethod('MatchesElementFields', [Reflection.BindingFlags]'NonPublic, Static')
$selector = New-Object 'Collections.Generic.Dictionary[string,object]'
$selector.Add('name','Save'); $selector.Add('automation_id','save'); $selector.Add('control_type','Button')
Assert ($elementMatch.Invoke($null, [object[]]@($selector.PSObject.BaseObject,'Save','save','Button'))) 'Exact conjunctive selector should match.'
foreach ($fields in @(@('save','save','Button'), @('Save','other','Button'), @('Save','save','button'), @('Save As','save','Button'), @($null,'save','Button'))) {
    Assert (!$elementMatch.Invoke($null, [object[]]@($selector.PSObject.BaseObject,$fields[0],$fields[1],$fields[2]))) 'UIA selector fields must all match exactly; a password name cannot match.'
}

# The password guard is tested without loading UIA or calling any application provider.
$valueMethod = [KilixDesktop].GetMethod('ReadInspectableValue', [Reflection.BindingFlags]'NonPublic, Static')
$guardArgs = [object[]]@([Func[bool]]{ $true }, [Func[string]]{ throw 'Password getter must not run.' }, $false, $false)
$value = $valueMethod.Invoke($null, $guardArgs)
Assert ($null -eq $value -and $guardArgs[2] -eq $true -and $guardArgs[3] -eq $false) 'Password values must never be read.'
$guardArgs = [object[]]@([Func[bool]]{ $false }, [Func[string]]{ 'ordinary edit text' }, $false, $false)
$value = $valueMethod.Invoke($null, $guardArgs)
Assert ($value -ceq 'ordinary edit text' -and !$guardArgs[2] -and !$guardArgs[3]) 'Normal ValuePattern text must remain inspectable.'
$script:passwordChecks = 0
$guardArgs = [object[]]@([Func[bool]]{ $script:passwordChecks++; $script:passwordChecks -ge 2 }, [Func[string]]{ 'changed while reading' }, $false, $false)
$value = $valueMethod.Invoke($null, $guardArgs)
Assert ($null -eq $value -and $guardArgs[2] -eq $true) 'Password state must be rechecked after reading a normal value.'
$guardArgs = [object[]]@([Func[bool]]{ $false }, [Func[string]]{ ('x' * 4095) + [char]::ConvertFromUtf32(0x1F642) }, $false, $false)
$value = $valueMethod.Invoke($null, $guardArgs)
Assert ($value.Length -eq 4095 -and $guardArgs[3] -eq $true) 'Value truncation must not split a Unicode surrogate pair.'

# An invalid operation cannot reach native calls, including on non-Windows hosts.
Expect-Code { [KilixDesktop]::Execute((New-Request 'no-such-operation')) }
if (!$onWindows) { Expect-Code { [KilixDesktop]::Execute((New-Request 'status')) } 'unsupported_platform' }

$temp = Join-Path ([IO.Path]::GetTempPath()) ('kilix-desktop-offline-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temp) | Out-Null
try {
    $requestPath = Join-Path $temp ('request with spaces ' + [char]0x03bb + '.json')
    $responsePath = Join-Path $temp 'response.json'
    $shellName = if ($PSVersionTable.PSEdition -eq 'Desktop') { 'powershell.exe' } elseif ($onWindows) { 'pwsh.exe' } else { 'pwsh' }
    $shellPath = Join-Path $PSHOME $shellName
    & $shellPath -NoLogo -NoProfile -NonInteractive -File $worker -Help -RequestPath $requestPath -ResponsePath $responsePath | Out-Null
    Assert ($LASTEXITCODE -eq 0) 'Offline help failed.'
    Assert (!(Test-Path -LiteralPath $responsePath)) 'Help wrote a response or attempted desktop work.'
    $utf8 = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($requestPath, '{"schema":"kilix.desktop/v1","operation":"no-such-operation","params":{}}', $utf8)
    & $shellPath -NoLogo -NoProfile -NonInteractive -File $worker -RequestPath $requestPath -ResponsePath $responsePath
    Assert ($LASTEXITCODE -ne 0) 'Invalid request must exit nonzero.'
    $responseBytes = [IO.File]::ReadAllBytes($responsePath)
    Assert ($responseBytes[0] -eq 123) 'Response must be UTF-8 JSON without BOM.'
    $response = [Text.UTF8Encoding]::new($false, $true).GetString($responseBytes) | ConvertFrom-Json
    Assert ($response.schema -eq 'kilix.desktop/v1' -and $response.ok -eq $false) 'Error envelope is malformed.'
    Assert ($response.operation -eq 'no-such-operation') 'Error envelope lost the request operation.'
    $expected = if ($onWindows) { 'invalid_request' } else { 'unsupported_platform' }
    Assert ($response.error.code -eq $expected) "Expected $expected from worker, got $($response.error.code)."
    Assert (@(Get-ChildItem -LiteralPath $temp -Filter '.kilix-response-*').Count -eq 0) 'Atomic response temporary file leaked.'
    # UTF-16 is not accepted as UTF-8 even when it has a BOM; replace the existing response atomically.
    [IO.File]::WriteAllText($requestPath, '{"schema":"kilix.desktop/v1","operation":"status","params":{}}', [Text.Encoding]::Unicode)
    & $shellPath -NoLogo -NoProfile -NonInteractive -File $worker -RequestPath $requestPath -ResponsePath $responsePath
    Assert ($LASTEXITCODE -ne 0) 'UTF-16 request must be rejected.'
    $response = [IO.File]::ReadAllText($responsePath, $utf8) | ConvertFrom-Json
    Assert ($response.error.code -eq 'invalid_request') 'UTF-16 request reached the backend.'
} finally { [IO.Directory]::Delete($temp, $true) }
Write-Output "PASS: $script:checks offline desktop backend checks; no valid desktop operation executed."
# The negative subprocess checks deliberately leave a nonzero LASTEXITCODE.
# GitHub's PowerShell wrapper propagates it unless successful completion resets it.
$global:LASTEXITCODE = 0
