# notify_toast.ps1 -- PA3 desktop toast (Windows PowerShell 5.1, WinRT, no module install).
#
# Called detached by pa/notify.py:
#   powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File notify_toast.ps1
# with PA_TOAST_TITLE / PA_TOAST_BODY / PA_TOAST_TAG in the environment (WSL forwards
# them through WSLENV).  Uses the built-in PowerShell AUMID so nothing has to be
# registered; Tag+Group make a newer toast replace the previous one of the same kind.
# Pure ASCII on purpose: PowerShell 5.1 reads a BOM-less file as ANSI, so all text
# arrives through the environment instead of living in this file.
# Exits 0 whatever happens; falls back to msg.exe when WinRT is unavailable.

$ErrorActionPreference = 'Stop'

$title = $env:PA_TOAST_TITLE
$body  = $env:PA_TOAST_BODY
$tag   = $env:PA_TOAST_TAG
if ([string]::IsNullOrEmpty($title)) { $title = 'Project Architect' }
if ($null -eq $body) { $body = '' }
if ([string]::IsNullOrEmpty($tag)) { $tag = 'pa3' }
if ($tag.Length -gt 60) { $tag = $tag.Substring(0, 60) }
if ($body.Length -gt 700) { $body = $body.Substring(0, 700) }

$AUMID = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

function Show-Fallback {
    try {
        $text = "$title`: $body"
        & msg.exe * /TIME:15 $text 2>$null | Out-Null
    } catch { }
}

try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

    $t = [System.Security.SecurityElement]::Escape($title)
    $b = [System.Security.SecurityElement]::Escape($body)

    $xml = @"
<toast activationType="foreground" scenario="default">
  <visual>
    <binding template="ToastGeneric">
      <text>$t</text>
      <text>$b</text>
    </binding>
  </visual>
  <audio src="ms-winsoundevent:Notification.Default" />
</toast>
"@

    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($xml)

    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    $toast.Tag = $tag
    $toast.Group = 'ProjectArchitect'

    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AUMID)
    $notifier.Show($toast)
} catch {
    Show-Fallback
}

exit 0
