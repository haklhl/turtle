"""launchd service management for macOS."""

import os
import subprocess
import sys
from pathlib import Path

SERVICE_LABEL = "com.haklhl.sea-turtle"
PLIST_PATH = Path("~/Library/LaunchAgents").expanduser() / f"{SERVICE_LABEL}.plist"

PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>sea_turtle</string>
        <string>start</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{work_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_dir}/daemon_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/daemon_stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path}</string>
    </dict>
</dict>
</plist>
"""


def _generate_plist() -> str:
    """Generate launchd plist content."""
    work_dir = str(Path("~/.sea_turtle").expanduser())
    log_dir = str(Path("~/.sea_turtle/logs").expanduser())
    python = sys.executable
    path = os.environ.get("PATH", "/usr/bin:/usr/local/bin")

    return PLIST_TEMPLATE.format(
        label=SERVICE_LABEL,
        python=python,
        work_dir=work_dir,
        log_dir=log_dir,
        path=path,
    )


def install_launchd_service() -> None:
    """Install Sea Turtle as a launchd service (macOS)."""
    plist_content = _generate_plist()

    print(f"Installing launchd service: {SERVICE_LABEL}")
    print(f"Plist file: {PLIST_PATH}")
    print()
    print("Plist content:")
    print(plist_content)

    confirm = input("Proceed? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    # Ensure directories exist
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_dir = Path("~/.sea_turtle/logs").expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        PLIST_PATH.write_text(plist_content)
        subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)
        print(f"\n✅ Service installed and loaded.")
        print(f"  Start: launchctl start {SERVICE_LABEL}")
        print(f"  Stop: launchctl stop {SERVICE_LABEL}")
        print(f"  Status: launchctl list | grep sea-turtle")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed: {e}")


def uninstall_launchd_service() -> None:
    """Remove Sea Turtle launchd service (macOS)."""
    print(f"Removing launchd service: {SERVICE_LABEL}")

    confirm = input("Proceed? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    try:
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=False)
        if PLIST_PATH.exists():
            PLIST_PATH.unlink()
        print("✅ Service removed.")
    except Exception as e:
        print(f"❌ Failed: {e}")
