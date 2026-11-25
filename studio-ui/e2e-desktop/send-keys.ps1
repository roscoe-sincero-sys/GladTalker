# Sends real keystrokes (as a user would type them) to the top-level window of process -AppPid,
# for checks that only native input can reach, such as WebView2's Ctrl+Plus zoom. It brings that
# window to the front first and refuses to type anything unless that window is in front, so the
# keys can never go to another program. Used by e2e-desktop/desktop.spec.js.
param(
    [Parameter(Mandatory = $true)][int]$AppPid,
    [Parameter(Mandatory = $true)][string]$Keys,   # System.Windows.Forms.SendKeys syntax, e.g. '^{ADD}'
    [int]$Times = 1
)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Fg {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    public static uint ForegroundPid() { uint pid; GetWindowThreadProcessId(GetForegroundWindow(), out pid); return pid; }
}
'@

$A = [System.Windows.Automation.AutomationElement]
$condition = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $AppPid)
$window = $A::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $condition)
if ($null -eq $window) { Write-Error "no window for process $AppPid"; exit 2 }
$hwnd = [IntPtr]$window.Current.NativeWindowHandle

for ($try = 0; $try -lt 10 -and [Fg]::ForegroundPid() -ne $AppPid; $try++) {
    [void][Fg]::SetForegroundWindow($hwnd)
    try { $window.SetFocus() } catch { }
    Start-Sleep -Milliseconds 200
}
for ($i = 0; $i -lt $Times; $i++) {
    if ([Fg]::ForegroundPid() -ne $AppPid) { Write-Error 'the app window is not in front; no keys sent'; exit 3 }
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
    Start-Sleep -Milliseconds 250
}
'sent'
