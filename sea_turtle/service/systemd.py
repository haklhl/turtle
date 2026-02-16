"""systemd service management for Linux."""

import os
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "sea-turtle"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}.service"

UNIT_TEMPLATE = """\
[Unit]
Description=Sea Turtle AI Agent Daemon
After=network.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={work_dir}
ExecStart={python} -m sea_turtle start
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PATH={path}

[Install]
WantedBy=multi-user.target
"""


def _generate_unit() -> str:
    """Generate systemd unit file content."""
    user = os.environ.get("USER", "root")
    group = user
    work_dir = str(Path("~/.sea_turtle").expanduser())
    python = sys.executable
    path = os.environ.get("PATH", "/usr/bin:/usr/local/bin")

    return UNIT_TEMPLATE.format(
        user=user,
        group=group,
        work_dir=work_dir,
        python=python,
        path=path,
    )


def install_systemd_service() -> None:
    """Install Sea Turtle as a systemd service."""
    unit_content = _generate_unit()

    print(f"Installing systemd service: {SERVICE_NAME}")
    print(f"Service file: {SERVICE_FILE}")
    print()
    print("Unit file content:")
    print(unit_content)

    confirm = input("Proceed? (requires sudo) [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    # Write unit file
    tmp_file = Path("/tmp/sea-turtle.service")
    tmp_file.write_text(unit_content)

    try:
        subprocess.run(["sudo", "cp", str(tmp_file), SERVICE_FILE], check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", SERVICE_NAME], check=True)
        print(f"\n✅ Service installed and enabled.")
        print(f"  Start: sudo systemctl start {SERVICE_NAME}")
        print(f"  Status: sudo systemctl status {SERVICE_NAME}")
        print(f"  Logs: journalctl -u {SERVICE_NAME} -f")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed: {e}")
    finally:
        tmp_file.unlink(missing_ok=True)


def uninstall_systemd_service() -> None:
    """Remove Sea Turtle systemd service."""
    print(f"Removing systemd service: {SERVICE_NAME}")

    confirm = input("Proceed? (requires sudo) [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    try:
        subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME], check=False)
        subprocess.run(["sudo", "systemctl", "disable", SERVICE_NAME], check=False)
        if Path(SERVICE_FILE).exists():
            subprocess.run(["sudo", "rm", SERVICE_FILE], check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        print("✅ Service removed.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed: {e}")
