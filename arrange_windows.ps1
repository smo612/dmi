$titles = @(
    "2330dmi API",
    "2330dmi ngrok",
    "2330dmi Shell",
    "2330dmi Watcher"
)

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class WinApi {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
}
"@

$found = @{}
[WinApi]::EnumWindows({
    param($hWnd, $lParam)
    if (-not [WinApi]::IsWindowVisible($hWnd)) { return $true }
    $sb = New-Object System.Text.StringBuilder 512
    [void][WinApi]::GetWindowText($hWnd, $sb, $sb.Capacity)
    $title = $sb.ToString()
    foreach ($wanted in $titles) {
        if ($title -like "$wanted*") {
            $script:found[$wanted] = $hWnd
        }
    }
    return $true
}, [IntPtr]::Zero) | Out-Null

$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$halfW = [int]($screen.Width / 2)
$halfH = [int]($screen.Height / 2)
$x = $screen.X
$y = $screen.Y

$layout = @{
    "2330dmi API"     = @($x,           $y,           $halfW, $halfH)
    "2330dmi ngrok"   = @($x + $halfW,  $y,           $halfW, $halfH)
    "2330dmi Shell"   = @($x,           $y + $halfH,  $halfW, $halfH)
    "2330dmi Watcher" = @($x + $halfW,  $y + $halfH,  $halfW, $halfH)
}

foreach ($name in $layout.Keys) {
    if ($found.ContainsKey($name)) {
        $rect = $layout[$name]
        [void][WinApi]::MoveWindow($found[$name], $rect[0], $rect[1], $rect[2], $rect[3], $true)
    }
}
