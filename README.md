# Homey ESPHomeDriver

[![PyPI version](https://img.shields.io/pypi/v/homey-esphomedriver)](https://pypi.org/project/homey-esphomedriver/)
[![PyPI downloads](https://img.shields.io/pypi/dm/homey-esphomedriver)](https://pypi.org/project/homey-esphomedriver/)
[![Python](https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white)](https://pypi.org/project/homey-esphomedriver/)
[![CI](https://img.shields.io/github/actions/workflow/status/Doekse/homey-esphomedriver/ci.yml?branch=main)](https://github.com/Doekse/homey-esphomedriver/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)
[![Homey](https://img.shields.io/badge/Homey-Python_SDK_v3-00C9FF.svg)](https://apps.developer.homey.app/)

This module can be used to make the development of ESPHome apps for Homey easier.

It is essentially a map-tool from Homey-capabilities to ESPHome entities.

This module requires Homey Apps SDK v3.

## Related Modules

- [node-homey-zigbeedriver](https://athombv.github.io/node-homey-zigbeedriver) — Module for Zigbee drivers
- [node-homey-zwavedriver](https://athombv.github.io/node-homey-zwavedriver) — Module for Z-Wave drivers
- [node-homey-rfdriver](https://athombv.github.io/node-homey-rfdriver) — Module for RF drivers
- [node-homey-oauth2app](https://athombv.github.io/node-homey-oauth2app) — Module for OAuth2 apps



## Installation

```bash
$ pip install homey-esphomedriver
```

Homey apps declare it next to `aioesphomeapi` in `app.json` / `.homeycompose/app.json`:

```json
"pythonPackages": ["aioesphomeapi", "homey-esphomedriver"]
```

Also checkout `[aioesphomeapi](https://github.com/esphome/aioesphomeapi)` if you want to talk to the ESPHome Native API directly, without `homey-esphomedriver`.

```bash
$ pip install aioesphomeapi
```



## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Requirements

This module requires Homey Apps SDK v3 (Python 3.14 runtime).

## Usage

Your device should extend `EspHomeDevice`. This is the class you most likely want to extend from. Your driver should extend `EspHomeDriver`.

Product identity lives on the Homey driver like Zigbee `productId`: an `esphome` object in `driver.compose.json`. `EspHomeDriver` reads it from `self.manifest`. `driver.py` and `device.py` can re-export the stock classes when you do not need extra logic.

### Driver compose

```json
{
  "name": { "en": "AQ-1" },
  "class": "sensor",
  "$extends": ["esphome-defaults"],
  "esphome": {
    "clientInfo": "Homey EverythingSmart",
    "projects": ["EverythingSmart.AQ-1"],
    "hiddenEntities": ["status_led"],
    "deviceEntities": { "voc": "measure_tvoc" },
    "deviceClassOverrides": { "aq_1": "sensor" }
  }
}
```


| Compose key            | Python `BrandProfile` field | Purpose                                          |
| ---------------------- | --------------------------- | ------------------------------------------------ |
| `projects`             | `projects`                  | Exact ESPHome `project.name` this driver accepts |
| `projectPrefix`        | `project_prefix`            | Prefix match such as `Brand.`                    |
| `clientInfo`           | `client_info`               | Name shown on the node for this Homey client     |
| `hiddenEntities`       | `hidden_entities`           | Hide extra entities (status LED, OTA helpers)    |
| `deviceEntities`       | `device_entities`           | Remap an entity to a Homey capability            |
| `deviceClassOverrides` | `device_class_overrides`    | Force Homey class from an entity id              |
| `settingEntities`      | `setting_entities`          | Back a settings field with an entity             |


Omit `projects` / `projectPrefix` to accept every project (`io.esphome`). If both are set, either match is enough.

A class-level `brand_profile = BrandProfile(...)` on the driver still overrides compose. Use that for `after_map`.

### Driver

```python
# drivers/aq-1/driver.py
from homey_esphomedriver import EspHomeDriver

homey_export = EspHomeDriver
```



### Device

```python
# drivers/aq-1/device.py
from homey_esphomedriver import EspHomeDevice

homey_export = EspHomeDevice
```



### Lifecycle hooks

Core owns `on_init` and `on_uninit` on `EspHomeDriver` and `EspHomeDevice`. Subclass the hooks below instead — same pattern as `homey-oauth2app`'s `onOAuth2Init`. Brand `app.py` exports Homey's `App`.


| Class           | Override                                                          | When                                                                 |
| --------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------- |
| `EspHomeDriver` | `on_esphome_init` / `on_esphome_uninit`                           | After Flow listeners / before teardown                               |
| `EspHomeDevice` | `on_esphome_init(client)` / `on_esphome_uninit`                   | After capability wiring; `client` is `None` when host is unknown     |
| `EspHomeDevice` | `on_esphome_connected(client)`                                    | After each login + `set_available`; session not READY yet — no cmds |


On a paired device, use `self.client` for the live `EspHomeClient` (may exist before the API handshake). Capability commands still require a READY session via `_require_client()` / `available` — including from `on_esphome_connected`.

Debug logging uses `env.json` `DEBUG`; there is no separate class flag.

```python
# drivers/aq-1/device.py
from homey_esphomedriver import EspHomeClient, EspHomeDevice as BaseDevice


class EspHomeDevice(BaseDevice):
    async def on_esphome_init(self, client: EspHomeClient | None) -> None:
        await super().on_esphome_init(client)
        if client is not None:
            self.log(f"API session started for {client.host}")

    async def on_esphome_connected(self, client: EspHomeClient) -> None:
        await super().on_esphome_connected(client)
        self.log(f"Logged in to {client.host}")


homey_export = EspHomeDevice
```



### Homey Compose files

Homey Compose runs before Python, so `$extends` / `$template` only see files already in the app. When `homey-esphomedriver` is listed in `pythonPackages`, Homey CLI copies this package's templates into `.homeycompose/` during preprocess (`homey app run`, `build`, `validate`), before compose. That is what makes `"$extends": ["esphome-defaults"]` and pair `"$template": "enter_key"` resolve.

Until that CLI is released, or when the package is unpublished, run the same copy by hand:

```bash
$ esphome-homey sync
```

`-p` / `--path` selects an app directory other than the current one. `sync` leaves `app.json`, `app.py`, `driver.compose.json`, and existing driver code alone unless `--force`.

A brand app is a normal Homey Python app:

```bash
$ homey app create
$ homey app dependencies add aioesphomeapi homey-esphomedriver
$ homey app driver create
```

Then set `$extends` and the `esphome` block on each driver's `driver.compose.json`, add store images, and publish with Homey CLI.

## Documentation

See [ARCHITECTURE.md](ARCHITECTURE.md) for what devices get from Homey and how a brand listing is an `esphome` compose block on top of this core.

- `[esphome-homey](https://github.com/Doekse/esphome-homey)` — generic Homey app (`io.esphome`; any ESPHome node)



## License

MIT. See [LICENSE](LICENSE).