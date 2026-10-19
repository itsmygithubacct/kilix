#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$StatePath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$form=New-Object Windows.Forms.Form
$form.Text='Kilix condition wait fixture'
$form.Width=540; $form.Height=300
$script:count=0
function Save-State([bool]$Closed=$false) {
    $state=@{pid=$PID;enabled=$target.Enabled;closed=$Closed;count=$script:count}
    [IO.File]::WriteAllText($StatePath,($state|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
}
function Button([string]$Name,[string]$Text,[int]$Y) {
    $b=New-Object Windows.Forms.Button
    $b.Name=$Name; $b.Text=$Text; $b.Left=20; $b.Top=$Y; $b.Width=200
    $form.Controls.Add($b)
    return $b
}
$target=Button 'delayedTarget' 'Delayed target' 20
$target.Enabled=$false
$enable=Button 'enableLater' 'Enable later' 60
$close=Button 'closeLater' 'Close later' 100
$one=Button 'duplicateOne' 'Duplicate' 140
$two=Button 'duplicateTwo' 'Duplicate' 180
$enableTimer=New-Object Windows.Forms.Timer
$enableTimer.Interval=6000
$enableTimer.Add_Tick({$enableTimer.Stop();$target.Enabled=$true;Save-State})
$closeTimer=New-Object Windows.Forms.Timer
$closeTimer.Interval=6000
$closeTimer.Add_Tick({$closeTimer.Stop();$form.Close()})
$enable.Add_Click({$enableTimer.Start()})
$close.Add_Click({$closeTimer.Start()})
$one.Add_Click({$script:count++;Save-State})
$two.Add_Click({$script:count++;Save-State})
$form.Add_Shown({Save-State})
$form.Add_FormClosed({Save-State -Closed $true})
try {[Windows.Forms.Application]::Run($form)} finally {$enableTimer.Dispose();$closeTimer.Dispose();$form.Dispose()}
