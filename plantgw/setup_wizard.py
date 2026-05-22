"""First setup helper for discovering Flower Care sensors."""
import argparse
import os
import re
import subprocess
import time
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = '~/.plantgw.yaml'
FLOWER_CARE_NAME = 'flower care'
MAC_PATTERN = re.compile(r'\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b')


def main():
    """Run the plantgateway first setup helper."""
    parser = argparse.ArgumentParser(description='Scan for Flower Care sensors and update plantgateway config.')
    parser.add_argument(
        '--duration',
        type=int,
        default=30,
        help='Bluetooth scan duration in seconds. Defaults to 30.',
    )
    parser.add_argument(
        '--config',
        default=DEFAULT_CONFIG_PATH,
        help='Path to the plantgateway config file. Defaults to ~/.plantgw.yaml.',
    )
    parser.add_argument(
        '--write',
        action='store_true',
        help='Add discovered Flower Care sensors to the config file.',
    )
    args = parser.parse_args()

    sensors = scan_flower_care(args.duration)
    if not sensors:
        print('No Flower Care sensors found.')
        return 1

    print('Found Flower Care sensors:')
    for mac in sensors:
        print(f'- {mac}')

    if args.write:
        added = update_config(args.config, sensors)
        if added:
            print(f'Added {len(added)} sensor(s) to {Path(args.config).expanduser()}:')
            for mac in added:
                print(f'- {mac}')
        else:
            print(f'No new sensors added; all discovered sensors are already in {Path(args.config).expanduser()}.')
    else:
        print('Run again with --write to add these sensors to your config.')
    return 0


def scan_flower_care(duration):
    """Scan for BLE advertisements with a Flower Care name."""
    if duration < 1:
        raise SystemExit('--duration must be at least 1 second')

    output = _scan_with_bluetoothctl(duration)
    if output is None:
        output = _scan_with_hcitool(duration)
    if output is None:
        raise SystemExit('Could not scan: bluetoothctl or hcitool is required')
    return _parse_flower_care_macs(output)


def _scan_with_bluetoothctl(duration):
    try:
        process_context = subprocess.Popen(
            ['bluetoothctl'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        return None

    with process_context as process:
        try:
            process.stdin.write('scan on\n')
            process.stdin.flush()
            time.sleep(duration)
            process.stdin.write('scan off\nquit\n')
            process.stdin.flush()
            output, _ = process.communicate(timeout=10)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            process.kill()
            output, _ = process.communicate()
    return output


def _scan_with_hcitool(duration):
    try:
        result = subprocess.run(
            ['timeout', str(duration), 'hcitool', 'lescan'],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        return None
    return result.stdout + result.stderr


def _parse_flower_care_macs(output):
    sensors = []
    seen = set()
    for line in output.splitlines():
        if FLOWER_CARE_NAME not in line.lower():
            continue
        match = MAC_PATTERN.search(line)
        if not match:
            continue
        mac = match.group(1).upper()
        if mac not in seen:
            sensors.append(mac)
            seen.add(mac)
    return sensors


def update_config(config_path, sensors):
    """Add discovered sensors to a plantgateway YAML config."""
    path = Path(os.path.abspath(os.path.expanduser(config_path)))
    if path.exists():
        existing_content = path.read_text(encoding='utf-8')
        existing_macs = _load_existing_macs(existing_content)
        new_sensors = [mac for mac in sensors if mac not in existing_macs]
        if not new_sensors:
            return []
        path.write_text(_add_sensors_to_config(existing_content, new_sensors), encoding='utf-8')
        return new_sensors

    path.write_text(_new_config_content(sensors), encoding='utf-8')
    return sensors


def _load_existing_macs(content):
    try:
        config = yaml.load(content, Loader=yaml.FullLoader) or {}
    except yaml.YAMLError as exception:
        raise SystemExit(f'Could not parse existing config: {exception}') from exception
    existing_macs = set()
    for sensor in config.get('sensors', []):
        mac = sensor.get('mac')
        if mac:
            existing_macs.add(mac.upper())
    return existing_macs


def _add_sensors_to_config(content, sensors):
    lines = content.splitlines()
    sensors_index = _find_sensors_section(lines)
    sensor_lines = _format_sensor_lines(sensors)
    if sensors_index is None:
        if lines and lines[-1].strip():
            lines.append('')
        lines.append('sensors:')
        lines.extend(sensor_lines)
        return '\n'.join(lines) + '\n'

    insert_index = _find_sensors_insert_index(lines, sensors_index)
    updated = lines[:insert_index]
    if updated and updated[-1].strip():
        updated.append('')
    updated.extend(sensor_lines)
    if insert_index < len(lines) and lines[insert_index].strip():
        updated.append('')
    updated.extend(lines[insert_index:])
    return '\n'.join(updated) + '\n'


def _find_sensors_section(lines):
    for index, line in enumerate(lines):
        if line.strip() == 'sensors:' and not line.startswith((' ', '\t')):
            return index
    return None


def _find_sensors_insert_index(lines, sensors_index):
    insert_index = len(lines)
    for index in range(sensors_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.startswith((' ', '\t', '#')):
            insert_index = index
            break
    while insert_index > sensors_index + 1 and not lines[insert_index - 1].strip():
        insert_index -= 1
    return insert_index


def _format_sensor_lines(sensors):
    lines = []
    for mac in sensors:
        lines.extend([
            f'    - mac: {mac}',
            f'      alias: flower_care_{mac[-5:].replace(":", "").lower()}',
            '      fail_silent:',
        ])
    return lines


def _new_config_content(sensors):
    sensor_lines = '\n'.join(_format_sensor_lines(sensors))
    return (
        '# Generated by plantgateway-setup. Fill in your MQTT settings before running plantgateway.\n'
        'mqtt:\n'
        '    server: my-mqtt-server\n'
        '    prefix: homeassistant/plant\n'
        '\n'
        'sensor_timeout: 15\n'
        'sensor_retries: 1\n'
        'kill_bluepy_on_timeout: true\n'
        '\n'
        'sensors:\n'
        f'{sensor_lines}\n'
    )


if __name__ == '__main__':
    raise SystemExit(main())
