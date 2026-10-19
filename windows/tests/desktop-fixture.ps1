# Owned WinForms fixture for desktop-integration.py; closes without prompts.
param([Parameter(Mandatory=$true)][string]$StatePath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form=New-Object Windows.Forms.Form
$form.Text='Kilix desktop automation fixture'
$form.StartPosition='Manual';$form.Location=New-Object Drawing.Point(160,140)
$form.ClientSize=New-Object Drawing.Size(640,420)
$script:counter=0;$script:wheel=0
$script:keys=New-Object 'Collections.Generic.List[string]'
$label=New-Object Windows.Forms.Label
$label.Text='Owned desktop test';$label.Location=New-Object Drawing.Point(20,15);$label.Size=New-Object Drawing.Size(500,25)
$form.Controls.Add($label)
$edit=New-Object Windows.Forms.TextBox
$edit.Name='sampleText';$edit.Multiline=$false;$edit.Location=New-Object Drawing.Point(20,50);$edit.Size=New-Object Drawing.Size(580,35)
$form.Controls.Add($edit)
$button=New-Object Windows.Forms.Button
$button.Name='actionButton';$button.Text='Invoke me';$button.Location=New-Object Drawing.Point(20,175);$button.Size=New-Object Drawing.Size(180,45)
$form.Controls.Add($button)
$password=New-Object Windows.Forms.TextBox
$password.Name='passwordText';$password.UseSystemPasswordChar=$true;$password.Text='fixture-password-do-not-expose';$password.Location=New-Object Drawing.Point(20,250);$password.Size=New-Object Drawing.Size(300,30)
$form.Controls.Add($password)
function Save-State {
 param([bool]$Closed=$false)
 if(![IO.Directory]::Exists([IO.Path]::GetDirectoryName($StatePath))){return}
 $data=@{pid=$PID;text=$edit.Text;counter=$script:counter;wheel=$script:wheel;closed=$Closed;keys=@($script:keys.ToArray());selection=$edit.SelectionLength}
 [IO.File]::WriteAllText($StatePath,($data|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)))
}
$edit.Add_TextChanged({Save-State})
$edit.Add_KeyDown({
 param($sender,$event)
 if($script:keys.Count -lt 128){$script:keys.Add([string]$event.KeyCode+':'+[string]$event.Modifiers)}
 # Make the fixture's shortcut behavior explicit across .NET Framework builds.
 if($event.Control -and $event.KeyCode -eq [Windows.Forms.Keys]::A){$edit.SelectAll();$event.SuppressKeyPress=$true}
 Save-State
})
$button.Add_Click({$script:counter++;$label.Text='Invoked '+$script:counter;Save-State})
$edit.Add_MouseWheel({param($sender,$e);$script:wheel+=$e.Delta;Save-State})
$form.Add_Shown({$edit.Focus();Save-State})
$form.Add_FormClosed({Save-State -Closed $true})
[Windows.Forms.Application]::Run($form)
