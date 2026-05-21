"""Cron helpers for plantgateway."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


BEGIN_MARKER = '# BEGIN plantgateway'
END_MARKER = '# END plantgateway'


def install_cron():
    """Install a plantgateway cron entry for the current user."""
    parser = argparse.ArgumentParser(description='Install plantgateway in the current user crontab.')
    parser.add_argument(
        '--interval',
        type=int,
        default=30,
        help='Run interval in minutes. Defaults to 30.',
    )
    parser.add_argument(
        '--command',
        default=_default_plantgateway_command(),
        help='Command to run from cron. Defaults to the installed plantgateway command.',
    )
    args = parser.parse_args()

    if args.interval < 1 or args.interval > 59:
        raise SystemExit('--interval must be between 1 and 59 minutes')

    current_crontab = _read_crontab()
    cron_line = f'*/{args.interval} * * * * {args.command}'
    updated_crontab = _without_managed_block(current_crontab)
    updated_crontab.extend([BEGIN_MARKER, cron_line, END_MARKER])
    _write_crontab(updated_crontab)
    print(f'Installed plantgateway cron entry: {cron_line}')


def remove_cron():
    """Remove the managed plantgateway cron entry from the current user."""
    current_crontab = _read_crontab()
    updated_crontab = _without_managed_block(current_crontab)
    if updated_crontab == current_crontab:
        print('No managed plantgateway cron entry found.')
        return
    _write_crontab(updated_crontab)
    print('Removed plantgateway cron entry.')


def _default_plantgateway_command():
    script_path = Path(sys.argv[0]).resolve().with_name('plantgateway')
    if script_path.exists():
        return str(script_path)
    command = shutil.which('plantgateway')
    if command:
        return command
    return 'plantgateway'


def _read_crontab():
    result = subprocess.run(
        ['crontab', '-l'],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.splitlines()
    if 'no crontab for' in result.stderr.lower():
        return []
    raise SystemExit(result.stderr.strip() or 'Could not read crontab')


def _write_crontab(lines):
    content = '\n'.join(lines).rstrip()
    if content:
        content += '\n'
    result = subprocess.run(
        ['crontab', '-'],
        input=content,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or 'Could not write crontab')


def _without_managed_block(lines):
    cleaned = []
    in_managed_block = False
    for line in lines:
        if line == BEGIN_MARKER:
            in_managed_block = True
            continue
        if line == END_MARKER:
            in_managed_block = False
            continue
        if not in_managed_block:
            cleaned.append(line)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return cleaned
