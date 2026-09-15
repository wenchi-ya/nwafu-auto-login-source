# Tests task construction and registration with mocks; never registers a real task.
$ErrorActionPreference = 'Stop'
$source = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\manage.ps1'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$functions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($function in $functions) { . ([scriptblock]::Create($function.Extent.Text)) }

$script:registered = 0; $script:started = 0; $script:invalidSaved = $false
function New-ScheduledTaskAction {
    param($Execute,$Argument,$WorkingDirectory)
    if ($Execute -ne 'E:\test space\.venv\Scripts\pythonw.exe' -or $Argument -ne '"E:\test space\campus_login.py"') { throw 'Incorrect command quoting.' }
    return @{}
}
function New-ScheduledTaskTrigger {
    param([switch]$AtStartup,[switch]$AtLogOn,$User,[switch]$Once,$At,$RepetitionInterval)
    $class = if ($AtStartup) {'MSFT_TaskBootTrigger'} elseif ($AtLogOn) {'MSFT_TaskLogonTrigger'} else {'MSFT_TaskTimeTrigger'}
    if ($Once -and $RepetitionInterval.TotalMinutes -ne 1) { throw 'Incorrect repetition.' }
    return [pscustomobject]@{Delay='';CimClass=@{CimClassName=$class};Repetition=@{Interval=$(if ($Once) {'PT1M'} else {''})}}
}
function New-ScheduledTaskPrincipal {
    param($UserId,$LogonType,$RunLevel)
    if ($LogonType -ne 'Password' -or $RunLevel -ne 'Limited') { throw 'Incorrect principal.' }
    return [pscustomobject]@{LogonType=$LogonType}
}
function New-ScheduledTaskSettingsSet {
    param($MultipleInstances,$ExecutionTimeLimit,[switch]$StartWhenAvailable,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries)
    if ($MultipleInstances -ne 'IgnoreNew' -or -not $StartWhenAvailable -or -not $AllowStartIfOnBatteries -or -not $DontStopIfGoingOnBatteries) { throw 'Incorrect settings.' }
    return @{}
}
function New-ScheduledTask {
    param($Action,$Trigger,$Principal,$Settings,$Description)
    if ($Trigger.Count -ne 3 -or $Trigger[0].Delay -ne 'PT30S') { throw 'Incorrect triggers.' }
    return [pscustomobject]@{Principal=$Principal;Triggers=$Trigger}
}
function Register-ScheduledTask {
    param($TaskName,$InputObject,$User,$Password,[switch]$Force)
    if ($Password -ne 'mock-not-real' -or -not $Force) { throw 'Incorrect registration.' }
    $script:registered++; $script:saved=$InputObject
}
function Get-ScheduledTask {
    param($TaskName,$ErrorAction)
    if ($script:invalidSaved) { $script:saved.Principal.LogonType = 'Interactive' }
    return $script:saved
}
function Start-ScheduledTask { param($TaskName) $script:started++ }

$credential = [PSCredential]::new('TEST\user', (ConvertTo-SecureString 'mock-not-real' -AsPlainText -Force))
Register-CampusTask -ProjectRoot 'E:\test space' -Account 'TEST\user' -TaskName 'TEST-only' -Credential $credential
if ($script:registered -ne 1 -or $script:started -ne 1) { throw 'Registration/start did not run.' }
$script:invalidSaved=$true
$rejected=$false
try { Register-CampusTask -ProjectRoot 'E:\test space' -Account 'TEST\user' -TaskName 'TEST-only' -Credential $credential }
catch { $rejected=$true }
if (-not $rejected -or $script:started -ne 1) { throw 'Invalid saved task should not start.' }
$empty = [PSCredential]::new('TEST\user', (New-Object Security.SecureString))
$rejected=$false
try { Register-CampusTask -ProjectRoot 'E:\test space' -Account 'TEST\user' -TaskName 'TEST-only' -Credential $empty }
catch { $rejected=$true }
if (-not $rejected -or $script:registered -ne 2) { throw 'Empty password should not register.' }
Write-Output 'PASS: syntax, three triggers, password logon, limited privileges, quoting, registration, verification failure, empty password (all mocked).'
