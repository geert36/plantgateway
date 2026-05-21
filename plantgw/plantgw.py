"""Forward measurements from Xiaomi Mi plant sensor via MQTT.

See https://github.com/ChristianKuehnel/plantgateway for more details.
"""

##############################################
#
# This is open source software licensed under the Apache License 2.0
# http://www.apache.org/licenses/LICENSE-2.0
#
##############################################


from enum import Enum
import os
import logging
import json
import signal
import ssl
import subprocess
import time
from datetime import datetime
from typing import List, Optional
import yaml
import paho.mqtt.client as mqtt
from miflora.miflora_poller import MiFloraPoller, MI_BATTERY, MI_LIGHT, MI_CONDUCTIVITY, MI_MOISTURE, MI_TEMPERATURE
from btlewrap.bluepy import BluepyBackend

from plantgw import __version__


class MQTTAttributes(Enum):
    """Attributes sent in the json dict."""
    BATTERY = 'battery'
    TEMPERATURE = 'temperature'
    BRIGHTNESS = 'brightness'
    MOISTURE = 'moisture'
    CONDUCTIVITY = 'conductivity'
    TIMESTAMP = 'timestamp'


# unit of measurement for the different attributes
UNIT_OF_MEASUREMENT = {
    MQTTAttributes.BATTERY:      '%',
    MQTTAttributes.TEMPERATURE:  '°C',
    MQTTAttributes.BRIGHTNESS:   'lux',
    MQTTAttributes.MOISTURE:     '%',
    MQTTAttributes.CONDUCTIVITY: 'µS/cm',
    MQTTAttributes.TIMESTAMP:     's',
}


# home assistant device classes for the different attributes
DEVICE_CLASS = {
    MQTTAttributes.BATTERY:      'battery',
    MQTTAttributes.TEMPERATURE:  'temperature',
    MQTTAttributes.BRIGHTNESS:   'illuminance',
    MQTTAttributes.MOISTURE:     None,
    MQTTAttributes.CONDUCTIVITY: None,
    MQTTAttributes.TIMESTAMP:    'timestamp',
}


# pylint: disable-msg=too-many-instance-attributes
class Configuration:
    """Stores the program configuration."""

    def __init__(self, config_file_path):
        with open(config_file_path, 'r', encoding='utf-8') as config_file:
            config = yaml.load(config_file, Loader=yaml.FullLoader)

        self._configure_logging(config)

        self.interface = 0
        if 'interface' in config:
            self.interface = config['interface']

        self.sensor_timeout: int = 30
        self.kill_bluepy_on_timeout: bool = False
        self.mqtt_port: int = 8883
        self.mqtt_user: Optional[str] = None
        self.mqtt_password: Optional[str] = None
        self.mqtt_ca_cert: Optional[str] = None
        self.mqtt_client_id: Optional[str] = None
        self.mqtt_trailing_slash: bool = True
        self.mqtt_timestamp_format: Optional[str] = None
        self.mqtt_discovery_prefix: Optional[str] = None
        self.sensors: List[SensorConfig] = []

        if 'port' in config['mqtt']:
            self.mqtt_port = config['mqtt']['port']

        if 'user' in config['mqtt']:
            self.mqtt_user = config['mqtt']['user']

        if 'password' in config['mqtt']:
            self.mqtt_password = config['mqtt']['password']

        if 'ca_cert' in config['mqtt']:
            self.mqtt_ca_cert = config['mqtt']['ca_cert']

        if 'client_id' in config['mqtt']:
            self.mqtt_client_id = config['mqtt']['client_id']

        if 'trailing_slash' in config['mqtt'] and not config['mqtt']['trailing_slash']:
            self.mqtt_trailing_slash = False

        if 'timestamp_format' in config['mqtt']:
            self.mqtt_timestamp_format = config['mqtt']['timestamp_format']

        self.mqtt_server = config['mqtt']['server']
        self.mqtt_prefix = config['mqtt']['prefix']

        for sensor_config in config['sensors']:
            fail_silent = 'fail_silent' in sensor_config
            self.sensors.append(SensorConfig(sensor_config['mac'], sensor_config['alias'], fail_silent))

        if 'discovery_prefix' in config['mqtt']:
            self.mqtt_discovery_prefix = config['mqtt']['discovery_prefix']

        if 'sensor_timeout' in config:
            self.sensor_timeout = config['sensor_timeout']

        if 'kill_bluepy_on_timeout' in config:
            self.kill_bluepy_on_timeout = config['kill_bluepy_on_timeout']

    @staticmethod
    def _configure_logging(config):
        timeform = '%a, %d %b %Y %H:%M:%S'
        logform = '%(asctime)s %(levelname)-8s %(message)s'
        loglevel = logging.INFO
        if 'debug' in config:
            loglevel = logging.DEBUG

        if 'logfile' in config:
            logfile = os.path.abspath(os.path.expanduser(config['logfile']))
            logging.basicConfig(filename=logfile, level=loglevel, datefmt=timeform, format=logform)
        else:
            logging.basicConfig(level=loglevel, datefmt=timeform, format=logform)


class SensorConfig:
    """Stores the configuration of a sensor."""

    def __init__(self, mac: str, alias: str = None, fail_silent: bool = False):
        if mac is None:
            msg = 'mac of sensor must not be None'
            logging.error(msg)
            raise ValueError('mac of sensor must not be None')
        self.mac = mac
        self.alias = alias
        self.fail_silent = fail_silent

    def get_topic(self) -> str:
        """Get the topic name for the sensor."""
        if self.alias is not None:
            return self.alias
        return self.mac

    def __str__(self) -> str:
        if self.alias:
            result = self.alias
        else:
            result = self.mac
        if self.fail_silent:
            result += ' (fail silent)'
        return result

    @property
    def short_mac(self):
        """Get the sensor mac without ':' in it."""
        return self.mac.replace(':', '')

    @staticmethod
    def get_name_string(sensor_list) -> str:
        """Convert a list of sensor objects to a nice string."""
        return ', '.join([str(sensor) for sensor in sensor_list])


class PlantGateway:
    """Main class of the module."""

    def __init__(self, config_file_path: str = '~/.plantgw.yaml'):
        config_file_path = os.path.abspath(os.path.expanduser(config_file_path))
        self.config = Configuration(config_file_path)  # type: Configuration
        logging.info('PlantGateway version %s', __version__)
        logging.info('loaded config file from %s', config_file_path)
        self.mqtt_client = None
        self.connected: bool = False

    def start_client(self):
        """Start the mqtt client."""
        if not self.connected:
            self._start_client()

    def stop_client(self):
        """Stop the mqtt client."""
        if self.mqtt_client is None:
            return
        if self.connected:
            self.mqtt_client.disconnect()
            self.connected = False
        self.mqtt_client.loop_stop()
        logging.info('Disconnected MQTT connection')

    def _start_client(self):
        self.mqtt_client = self._create_mqtt_client(self.config.mqtt_client_id)
        if self.config.mqtt_user is not None:
            self.mqtt_client.username_pw_set(self.config.mqtt_user, self.config.mqtt_password)
        if self.config.mqtt_ca_cert is not None:
            self.mqtt_client.tls_set(self.config.mqtt_ca_cert, cert_reqs=ssl.CERT_REQUIRED)
        self.mqtt_client.will_set(
            self._get_health_topic(),
            json.dumps(self._build_health_payload('offline')),
            qos=1,
            retain=True,
        )

        def _on_connect(client, _, flags, return_code, properties=None):
            self.connected = True
            try:
                result = mqtt.connack_string(return_code)
            except TypeError:
                result = str(return_code)
            logging.info("MQTT connection returned result: %s", result)
        self.mqtt_client.on_connect = _on_connect

        self.mqtt_client.connect(self.config.mqtt_server, self.config.mqtt_port, 60)
        self.mqtt_client.loop_start()

    @staticmethod
    def _create_mqtt_client(client_id):
        """Create a Paho MQTT client across the 1.x and 2.x constructor APIs."""
        if hasattr(mqtt, 'CallbackAPIVersion'):
            return mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        return mqtt.Client(client_id)

    def publish_health(self, status: str, failed_sensors: Optional[List[SensorConfig]] = None, message: str = None):
        """Publish a retained health message for monitoring."""
        self.start_client()
        payload = self._build_health_payload(status, failed_sensors, message)
        publish_info = self.mqtt_client.publish(self._get_health_topic(), json.dumps(payload), qos=1, retain=True)
        publish_info.wait_for_publish()
        logging.info('sent health status %s to topic %s', status, self._get_health_topic())

    def _build_health_payload(
            self,
            status: str,
            failed_sensors: Optional[List[SensorConfig]] = None,
            message: str = None):
        failed_sensors = failed_sensors or []
        return {
            'status': status,
            'timestamp': datetime.now().isoformat(),
            'version': __version__,
            'failed_count': len(failed_sensors),
            'failed_sensors': [sensor.get_topic() for sensor in failed_sensors],
            'message': message,
        }

    def _get_health_topic(self) -> str:
        return f'{self.config.mqtt_prefix}/health'

    def check_bluetooth(self):
        """Verify that the configured Bluetooth adapter exists and is powered."""
        adapter = f'hci{self.config.interface}'
        adapter_path = f'/sys/class/bluetooth/{adapter}'
        if os.name != 'posix':
            logging.warning('Skipping Bluetooth check on non-POSIX platform')
            return
        if not os.path.isdir('/sys/class/bluetooth'):
            raise RuntimeError('Bluetooth is not available: /sys/class/bluetooth does not exist')
        if not os.path.exists(adapter_path):
            raise RuntimeError(f'Bluetooth adapter {adapter} was not found')

        checked = self._check_hciconfig(adapter)
        if not checked:
            checked = self._check_bluetoothctl()
        if not checked:
            logging.warning('Bluetooth adapter %s exists, but no status command was available', adapter)
            return
        logging.info('Bluetooth adapter %s is available', adapter)

    @staticmethod
    def _check_hciconfig(adapter: str) -> bool:
        try:
            result = subprocess.run(
                ['hciconfig', adapter],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except FileNotFoundError:
            return False
        except subprocess.TimeoutExpired as exception:
            raise RuntimeError(f'Bluetooth check timed out while running hciconfig {adapter}') from exception

        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise RuntimeError(f'Bluetooth adapter {adapter} could not be checked: {output.strip()}')
        if 'UP' not in output:
            raise RuntimeError(f'Bluetooth adapter {adapter} is present but not UP')
        return True

    @staticmethod
    def _check_bluetoothctl() -> bool:
        try:
            result = subprocess.run(
                ['bluetoothctl', 'show'],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except FileNotFoundError:
            return False
        except subprocess.TimeoutExpired as exception:
            raise RuntimeError('Bluetooth check timed out while running bluetoothctl show') from exception

        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise RuntimeError(f'Bluetooth controller could not be checked: {output.strip()}')
        if 'Powered: no' in output:
            raise RuntimeError('Bluetooth controller is present but powered off')
        if 'Powered: yes' not in output:
            logging.warning('Bluetooth controller status did not report a powered state')
        return True

    def _publish(self, sensor_config: SensorConfig, poller: MiFloraPoller):
        self.start_client()
        state_topic = self._get_state_topic(sensor_config)

        data = {
            MQTTAttributes.BATTERY.value:      poller.parameter_value(MI_BATTERY),
            MQTTAttributes.TEMPERATURE.value:  f'{poller.parameter_value(MI_TEMPERATURE):.1f}',
            MQTTAttributes.BRIGHTNESS.value:   poller.parameter_value(MI_LIGHT),
            MQTTAttributes.MOISTURE.value:     poller.parameter_value(MI_MOISTURE),
            MQTTAttributes.CONDUCTIVITY.value: poller.parameter_value(MI_CONDUCTIVITY),
            MQTTAttributes.TIMESTAMP.value:    datetime.now().isoformat(),
        }
        for key, value in data.items():
            logging.debug("%s: %s", key, value)
        if self.config.mqtt_timestamp_format is not None:
            data['timestamp'] = datetime.now().strftime(self.config.mqtt_timestamp_format)
        json_payload = json.dumps(data)
        self.mqtt_client.publish(state_topic, json_payload, qos=1, retain=True)
        logging.info('sent data to topic %s', state_topic)

    def _get_state_topic(self, sensor_config: SensorConfig) -> str:
        prefix_fmt = '{}/{}'
        if self.config.mqtt_trailing_slash:
            prefix_fmt += '/'
        prefix = prefix_fmt.format(self.config.mqtt_prefix,
                                   sensor_config.get_topic())
        return prefix

    def process_mac(self, sensor_config: SensorConfig):
        """Get data from one Sensor."""
        logging.info('Getting data from sensor %s', sensor_config.get_topic())
        poller = MiFloraPoller(sensor_config.mac, BluepyBackend)
        self.announce_sensor(sensor_config)
        self._publish(sensor_config, poller)

    def process_mac_with_timeout(self, sensor_config: SensorConfig):
        """Get data from one Sensor, bounded by the configured timeout."""
        # pylint: disable=not-callable
        alarm_signal = getattr(signal, 'SIGALRM', None)
        timer_real = getattr(signal, 'ITIMER_REAL', None)
        setitimer = getattr(signal, 'setitimer', None)
        if self.config.sensor_timeout <= 0 or alarm_signal is None or timer_real is None or setitimer is None:
            self.process_mac(sensor_config)
            return

        previous_handler = signal.getsignal(alarm_signal)

        def _timeout_handler(signum, frame):
            raise TimeoutError(
                f'timed out after {self.config.sensor_timeout} seconds '
                f'while reading sensor {sensor_config.get_topic()}'
            )

        signal.signal(alarm_signal, _timeout_handler)
        setitimer(timer_real, self.config.sensor_timeout)
        try:
            self.process_mac(sensor_config)
        except TimeoutError:
            if self.config.kill_bluepy_on_timeout:
                self._stop_bluepy_helpers()
            raise
        finally:
            setitimer(timer_real, 0)
            signal.signal(alarm_signal, previous_handler)

    @staticmethod
    def _stop_bluepy_helpers():
        try:
            subprocess.run(
                ['pkill', '-f', 'bluepy-helper'],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logging.warning('Could not stop lingering bluepy-helper processes')

    def process_all(self):
        """Get data from all sensors."""
        next_list = self.config.sensors
        timeout = 1  # initial timeout in seconds
        max_retry = 6  # number of retries
        retry_count = 0

        while retry_count < max_retry and next_list:
            # if this is not the first try: wait some time before trying again
            if retry_count > 0:
                logging.info('try %d of %d: could not process sensor(s) %s. Waiting %d sec for next try',
                             retry_count, max_retry, SensorConfig.get_name_string(next_list), timeout)
                time.sleep(timeout)
                timeout *= 2  # exponential backoff-time

            current_list = next_list
            retry_count += 1
            next_list = []
            for sensor in current_list:
                try:
                    self.process_mac_with_timeout(sensor)
                # pylint: disable=bare-except, broad-except
                except Exception as exception:
                    next_list.append(sensor)  # if it failed, we'll try again in the next round
                    reason = str(exception) or exception.__class__.__name__
                    msg = f"could not read data from {sensor.mac} ({sensor.alias}) with reason: {reason}"
                    if sensor.fail_silent:
                        logging.error(msg)
                        logging.warning('fail_silent is set for sensor %s, so not raising an exception.', sensor.alias)
                    else:
                        logging.exception(msg)
                        print(msg)

        # return sensors that could not be processed after max_retry
        return next_list

    def announce_sensor(self, sensor_config: SensorConfig):
        """Announce the sensor via Home Assistant MQTT Discovery.

           see https://www.home-assistant.io/docs/mqtt/discovery/
        """
        if self.config.mqtt_discovery_prefix is None:
            return
        self.start_client()
        device_name = f'plant_{sensor_config.short_mac}'
        for attribute in MQTTAttributes:
            topic = f'{self.config.mqtt_discovery_prefix}/sensor/{device_name}_{attribute.value}/config'
            payload = {
                'state_topic':         self._get_state_topic(sensor_config),
                'unit_of_measurement': UNIT_OF_MEASUREMENT[attribute],
                'value_template':      '{{value_json.'+attribute.value+'}}',
            }
            if sensor_config.alias is not None:
                payload['name'] = f'{sensor_config.alias}_{attribute.value}'

            if DEVICE_CLASS[attribute] is not None:
                payload['device_class'] = DEVICE_CLASS[attribute]

            json_payload = json.dumps(payload)
            self.mqtt_client.publish(topic, json_payload, qos=1, retain=False)
            logging.info('sent sensor config to topic %s', topic)
