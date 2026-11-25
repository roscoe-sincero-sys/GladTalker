# Prints, as JSON, what Windows UI Automation (the interface Narrator, NVDA, and JAWS use) exposes
# for the top-level window of process -AppPid: every element's control type, localized type
# (Chromium reports ARIA roles and landmarks here, e.g. "heading", "main"), name, and whether it
# takes keyboard focus. Used by e2e-desktop/desktop.spec.js.
param([Parameter(Mandatory = $true)][int]$AppPid)

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$condition = New-Object System.Windows.Automation.PropertyCondition($A::ProcessIdProperty, $AppPid)
$window = $A::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $condition)
if ($null -eq $window) { '[]'; exit 0 }

$out = New-Object System.Collections.Generic.List[object]
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
function Visit($element, $depth) {
    if ($depth -gt 60 -or $out.Count -gt 3000) { return }
    try {
        $c = $element.Current
        $out.Add([pscustomobject]@{
            type      = ($c.ControlType.ProgrammaticName -replace '^ControlType\.', '')
            localized = $c.LocalizedControlType
            name      = $c.Name
            focusable = $c.IsKeyboardFocusable
            offscreen = $c.IsOffscreen
            enabled   = $c.IsEnabled
        })
    } catch { return }
    $child = $walker.GetFirstChild($element)
    while ($null -ne $child) {
        Visit $child ($depth + 1)
        $child = $walker.GetNextSibling($child)
    }
}
Visit $window 0
$out | ConvertTo-Json -Depth 3 -Compress
