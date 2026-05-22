# plantgateway
Bluetooth LE to mqtt gateway for Xiaomi Mi plant sensors. For more details see the [documentation overview](doc/overview.md).

# Use case
For many setups the Xiaomi Mi plant sensors are too far away from your 
home server to connect directly via Bluetooth LE. 
In such a scenario the plantgatway will poll the data from a list of 
Xiaomi Mi plant sensors via Bluetooth LE using 
[miflora](https://github.com/open-homeautomation/miflora).
The data is then published via mqtt to your home automation server.

The plantgateway is intended to be run on a small Linux machine (e.g. 
[Raspberry Pi](https://www.raspberrypi.org/)
or a [C.H.I.P](https://getchip.com/)) that has both Bluetooth LE and WiFi.

# installation & update
* install [python 3.8](https://www.python.org/) (or above)
and [pip](https://pip.pypa.io/en/stable/installing/)
```
sudo apt-get install python3-pip build-essential libglib2.0-dev libyaml-dev
```
* install the plant gateway from pypi:
```
sudo pip install --upgrade plantgateway
```
or if you have multiple python and pip installations:
```
sudo pip3 install --upgrade plantgateway
```
* To update your installation just run pip again. 

If you have problems with the PyYaml installation, update your pip version 
with `sudo pip3 install --upgrade pip` and try again.

# configuration
Copy the [plantgw.yaml](plantgw.yaml) (in this repository) to your home directory and
rename it to ".plantgw.yaml".
Then change this file to match your requirements.

# first setup
You can scan for nearby Xiaomi Flower Care sensors:

```
plantgateway-setup
```

To add discovered Flower Care sensors to your config:

```
plantgateway-setup --write
```

The setup helper only adds sensors that are not already in the config. If the
config file does not exist yet, it creates `~/.plantgw.yaml` with placeholder
MQTT settings that you still need to fill in.

# execution
After the installation with pip you can simply run the tool from the command line:
```
plantgateway
```
There are no command line parameters and there is no interaction required.
You probably want to add the script to your cron tab to be executed 
in regular intervals (e.q. every hour).

To install a cron entry for the current user:

```
plantgateway-install-cron --interval 30
```

The managed cron entry writes output to `~/plantgateway-cron.log` by default.
Check that file if the scheduled run does not seem to work:

```
tail -f ~/plantgateway-cron.log
```

To remove the managed cron entry again:

```
plantgateway-remove-cron
```

# bluetooth preflight
When `plantgateway` starts, it checks whether the configured Bluetooth adapter
exists and is powered before reading sensors. The configured adapter defaults to
`hci0`; set `interface: 1` in the configuration to use `hci1`.

Each sensor read is limited to 30 seconds by default, so a stuck Bluetooth
connection cannot block the whole run indefinitely. Set `sensor_timeout: 0` in
the configuration to disable this timeout.

Each sensor is attempted once by default. Set `sensor_retries: 2` or higher if
you want retries within the same run.

By default, timed-out `bluepy-helper` processes are not killed because that can
produce noisy `BrokenPipeError` messages. Set `kill_bluepy_on_timeout: true` to
force cleanup after each timeout.

# health monitoring
Every run publishes a retained MQTT health message to `<prefix>/health`, where
`<prefix>` is the `mqtt.prefix` value from your configuration.

The payload contains a `status` field:

- `running`: plantgateway has started and is currently reading sensors
- `ok`: the run completed without non-silent sensor failures
- `warning`: the run completed with fail-silent sensor failures only
- `error`: the run completed with failures or raised an exception
- `offline`: the MQTT connection was lost unexpectedly after startup, if
  `mqtt.last_will: true` is enabled

If plantgateway hangs while reading a sensor, the retained health message will
remain `running` with an old `timestamp`. In Home Assistant you can alert on
that stale timestamp.

For cron-based runs, `mqtt.last_will` is disabled by default to avoid false
`offline` states after short-lived runs. The stale health automation below is
the recommended way to detect a stuck plantgateway run.

## Home Assistant health automation
If your MQTT prefix is `homeassistant/plant`, add a health sensor like this:

```yaml
mqtt:
  sensor:
    - name: Plantgateway Health
      unique_id: plantgateway_health
      state_topic: "homeassistant/plant/health"
      value_template: "{{ value_json.status }}"
      json_attributes_topic: "homeassistant/plant/health"
```

Then add automations for bad health states and stale health updates. Replace
`notify.mobile_app_your_phone` with your own notification service.

```yaml
- alias: Plantgateway health problem
  description: ""
  triggers:
    - trigger: mqtt
      topic: "homeassistant/plant/health"
  conditions:
    - condition: template
      value_template: "{{ trigger.payload_json.status in ['warning', 'error', 'offline'] }}"
  actions:
    - action: notify.mobile_app_your_phone
      data:
        title: "Plantgateway health"
        message: |-
          Plantgateway status is {{ trigger.payload_json.status }}.
          Failed sensors: {{ trigger.payload_json.failed_count }}.
          {{ trigger.payload_json.message or '' }}
  mode: single

- alias: Plantgateway health stale
  description: ""
  triggers:
    - trigger: time_pattern
      minutes: "/10"
  conditions:
    - condition: template
      value_template: >-
        {% set state = states('sensor.plantgateway_health') %}
        {% if state in ['unknown', 'unavailable'] %}
          true
        {% else %}
          {{ (now() - states.sensor.plantgateway_health.last_updated).total_seconds() > 2700 }}
        {% endif %}
  actions:
    - action: notify.mobile_app_your_phone
      data:
        title: "Plantgateway health"
        message: "Plantgateway has not sent a health update for more than 45 minutes."
  mode: single
```

The stale check uses 45 minutes because the example cron interval is 30 minutes.
Increase or decrease `2700` seconds if you run plantgateway more or less often.

# integration in home automation

## HomeAssistant
If you enable the [MQTT discovery](https://www.home-assistant.io/docs/mqtt/discovery/) 
feature by setting the `discovery_prefix` parameter in
the config file, all configured sensors are automatically available in HomeAssistant.
To monitor the state of your plants, you can use the 
["plant" component](https://www.home-assistant.io/components/plant/).


## fhem
To check your plants in the home automation tool [fhem](http://fhem.de/), 
you can use the 
[gardener](https://github.com/ChristianKuehnel/fhem-gardener) module. 
The installation is explained on the github page of the module.

If you haven't done so, you need to configure your MQTT server in fhem with 
a [MQTT](http://fhem.de/commandref.html#MQTT) module.
For each sensor you have, set up a [MQTT_Device](http://fhem.de/commandref.html#MQTT_DEVICE) 
and make it auto subscribe to the topic 
you configured in the plantgateway:
```
define <plant_name> MQTT_Device
attr <plant_name> autoSubscribeReadings <prefix_in_config>/<plant alias>/+
```

After that configure the gardener to match your requirements

# Security
A remark on security:
Before running your MQTT server on the internet make sure that you enable
SSL/TLS encryption and client authentication.

# Problem analysis
In case you have any problem with plantgateway, please check:

- Is you configuration file a valid YAML file?
- Does your Bluetooth dongle support Bluetooh Low Energy? Check with `sudo hcitool lescan`, this should list all Low Energy devices.
- If you have connection issues, please try a system update `sudo apt update; sudo apt dist-upgrade`. This fixes these issues usually.

If all this does not help, please file a bug ticket in github.

# License
Unless stated otherwise all software in this repository is licensed under the Apache License 2.0
http://www.apache.org/licenses/LICENSE-2.0
