param(
    [ValidateSet('menu','install','configure','status','disable','test')][string]$Mode = 'menu',
    [string]$ExpectedSid
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$env:PYTHONUTF8 = '1'

function Test-Python {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        & $Path -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        foreach ($version in @('-3.14','-3.13','-3.12','-3.11','-3.10','-3')) {
            try {
                $path = & py.exe $version -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and (Test-Python $path)) { return $path }
            } catch { }
        }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\WindowsApps\*' -and (Test-Python $command.Source)) {
        return $command.Source
    }
    throw '请安装 Python 3.10 或以上版本（推荐 3.12），并包含 Python Launcher：https://www.python.org/downloads/windows/'
}

function New-CampusTaskDefinition {
    param([string]$ProjectRoot, [string]$Account)
    $pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
    $scriptPath = Join-Path $ProjectRoot 'campus_login.py'
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $scriptPath + '"') -WorkingDirectory $ProjectRoot
    $startup = New-ScheduledTaskTrigger -AtStartup
    $startup.Delay = 'PT30S'
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $Account
    $minute = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType Password -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    return New-ScheduledTask -Action $action -Trigger @($startup, $logon, $minute) -Principal $principal -Settings $settings -Description 'NWAFU campus authentication: startup, Windows sign-in and every minute, including before sign-in.'
}

function Register-CampusTask {
    param([string]$ProjectRoot, [string]$Account, [string]$TaskName, [PSCredential]$Credential)
    $definition = New-CampusTaskDefinition -ProjectRoot $ProjectRoot -Account $Account
    $password = $Credential.GetNetworkCredential().Password
    if ([string]::IsNullOrEmpty($password)) { throw '此任务需要非空 Windows 账户密码，PIN 不能替代账户密码。' }
    try {
        Register-ScheduledTask -TaskName $TaskName -InputObject $definition -User $Account -Password $password -Force | Out-Null
    } finally { $password = $null }
    $saved = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $kinds = @($saved.Triggers | ForEach-Object { $_.CimClass.CimClassName })
    $repeating = @($saved.Triggers | Where-Object { $_.Repetition.Interval -eq 'PT1M' })
    if ($saved.Principal.LogonType -ne 'Password' -or
        $kinds -notcontains 'MSFT_TaskBootTrigger' -or
        $kinds -notcontains 'MSFT_TaskLogonTrigger' -or $repeating.Count -ne 1) {
        throw '任务注册后的校验失败，请在任务计划程序中检查配置。'
    }
    Start-ScheduledTask -TaskName $TaskName
}

function Invoke-CampusPython {
    param([string]$Python, [string]$Option)
    if (-not (Test-Python $Python)) { throw 'Python 虚拟环境不可用，请通过菜单 1 安装/更新。' }
    if ($Option -eq '--test') {
        & $Python (Join-Path $Root 'campus_login.py') --login --visible
    } else {
        & $Python (Join-Path $Root 'campus_login.py') $Option
    }
    $result = $LASTEXITCODE
    if ($result -eq 2) { throw '后台仍在运行，本次操作未执行，请稍后重试。' }
    if ($result -ne 0) { throw '操作未完成，请查看以上提示及 %LOCALAPPDATA%\NWAFUAutoLogin\activity.log。' }
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = $identity.User.Value
    $account = $identity.Name
    if ($ExpectedSid -and $ExpectedSid -ne $sid) {
        throw '请使用保存校园网凭据的同一 Windows 账户；不能换成其他管理员账户运行。'
    }
    if ($Mode -eq 'menu') {
        Write-Host 'NWAFU 校园网自动认证'
        Write-Host '1. 安装/更新：开机 + 登录 + 每分钟自动检查'
        Write-Host '2. 修改校园网账号密码 / 解除失败暂停'
        Write-Host '3. 查看自动任务状态'
        Write-Host '4. 停用自动任务'
        Write-Host '5. 手动可视测试'
        Write-Host '0. 退出'
        $selection = Read-Host '请选择（首次使用选 1）'
        $choices = @{'1'='install';'2'='configure';'3'='status';'4'='disable';'5'='test'}
        if ($selection -eq '0') { exit 0 }
        if (-not $choices.ContainsKey($selection)) { throw '无效选项，请重新运行 setup.cmd。' }
        $Mode = $choices[$selection]
    }
    if ($Mode -in @('install','status','disable')) {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Mode ' + $Mode + ' -ExpectedSid "' + $sid + '"'
            # Visible window is intentional: the installer needs local password input.
            $child = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
            exit $child.ExitCode
        }
    }
    $taskName = 'NWAFUAutoLogin-' + $sid
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    $dataRoot = Join-Path $env:LOCALAPPDATA 'NWAFUAutoLogin'
    switch ($Mode) {
        'install' {
            if (-not (Test-Python $python)) {
                $basePython = Find-Python
                & $basePython -m venv (Join-Path $Root '.venv')
                if ($LASTEXITCODE -ne 0 -or -not (Test-Python $python)) { throw '无法创建或修复 Python 虚拟环境。' }
            }
            & $python -m pip install -r (Join-Path $Root 'requirements.txt')
            if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请先手动联网再重试。' }
            $hasConfig = (Test-Path -LiteralPath (Join-Path $dataRoot 'credentials.bin')) -and (Test-Path -LiteralPath (Join-Path $dataRoot 'config.json'))
            if ($hasConfig) {
                $keep = Read-Host '已有校园网配置，是否保留？[Y/n]'
                if ($keep -match '^(n|no)$') { Invoke-CampusPython $python '--configure' }
            } else { Invoke-CampusPython $python '--configure' }
            Write-Host ('下一步需要 Windows 账户 ' + $account + ' 的密码，不是 PIN，也不是校园网密码。')
            $credential = Get-Credential -UserName $account -Message '请输入 Windows 账户密码（不是 PIN / 校园网密码）'
            if ($null -eq $credential) { throw '已取消，未提交自动任务注册。' }
            $enteredAccount = New-Object Security.Principal.NTAccount($credential.UserName)
            if ($enteredAccount.Translate([Security.Principal.SecurityIdentifier]).Value -ne $sid) { throw '输入的 Windows 账户必须与当前账户一致。' }
            try { Register-CampusTask -ProjectRoot $Root -Account $account -TaskName $taskName -Credential $credential }
            finally { $credential = $null }
            Write-Host 'SUCCESS：已统一启用开机、登录和每分钟检查；未登录时也能运行。' -ForegroundColor Green
            Write-Host '已请求一次后台检查。任务配置成功不代表真实校园网认证已经成功。'
        }
        'configure' { Invoke-CampusPython $python '--configure' }
        'test' { Invoke-CampusPython $python '--test' }
        'status' {
            $task = Get-ScheduledTask | Where-Object { $_.TaskName -eq $taskName -and $_.TaskPath -eq '\' }
            if ($null -eq $task) { Write-Host '未安装自动任务，请通过菜单 1 安装。' }
            else {
                $task | Select-Object TaskName,State,@{N='LogonType';E={$_.Principal.LogonType}},@{N='RunAs';E={$_.Principal.UserId}} | Format-List
                Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime,LastTaskResult,NextRunTime | Format-List
                Write-Host 'LogonType 应为 Password，才支持未登录时运行。LastTaskResult=0 表示正常退出，也可能是无需认证或跳过。'
            }
            $statePath = Join-Path $dataRoot 'state.json'
            if (Test-Path -LiteralPath $statePath) {
                $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
                Write-Host ('认证状态：连续失败次数={0}；暂停={1}' -f $state.failures,$state.paused)
            }
        }
        'disable' {
            $task = Get-ScheduledTask | Where-Object { $_.TaskName -eq $taskName -and $_.TaskPath -eq '\' }
            if ($null -ne $task) {
                Stop-ScheduledTask -TaskName $taskName
                Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            }
            Write-Host '已停用当前账户的自动任务。本机校园网凭据和日志仍保留。'
        }
    }
    Write-Host ('日志目录：' + $dataRoot)
    Read-Host '按 Enter 关闭' | Out-Null
    exit 0
} catch {
    Write-Host ('未完成：' + $_.Exception.Message) -ForegroundColor Red
    Read-Host '按 Enter 关闭' | Out-Null
    exit 1
}
