# Architecture

`homey-esphomedriver` is the shared layer between ESPHome devices and Homey. The generic `[esphome-homey](https://github.com/Doekse/esphome-homey)` app (`io.esphome`) uses it to support any ESPHome node. Brand apps use the exact same core and only define which of their products should show up in their Homey app.

Brands don't need to reimplement discovery, encryption, entity mapping, reconnect logic or BLE Wi-Fi setup. They basically define their products, add the store assets and ship the app.

```text
ESPHome firmware                    Homey
─────────────────                   ─────
project.name = Brand.AQ-1           Brand app  (thin drivers + compose `esphome`)
        │                                    │
        │  Native API (TCP :6053)            │  pythonPackages
        └────────────►  homey-esphomedriver  ◄┘
                        discovery, pairing,
                        mapping, reconnect,
                        Improv BLE
```



## What this does for ESPHome devices

Homey talks directly to the device using ESPHome's Native API. Everything stays local, without a cloud in between.

The same connection and mapping logic is used whether it's a DIY ESPHome node paired through `io.esphome`, or a branded product paired through a manufacturer's own Homey app.


| Device need                    | What core provides                                                                                               |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| Find the node on the LAN       | mDNS discovery, plus add-by-IP when discovery doesn't find it                                                    |
| First-time Wi-Fi               | BLE Improv provisioning. Once connected, the device appears through mDNS like any other ESPHome node             |
| Secure API                     | Noise encryption key during pairing or repair. Reconnect stops when the key is wrong instead of endlessly retrying |
| Lights, sensors, climate, etc. | ESPHome entities mapped to Homey capabilities, including UI, Flows and Insights                                  |
| Stay connected                 | Reconnect after connection loss, follow mDNS address changes and keep deep-sleep devices available while offline |


Diagnostic and configuration entities are hidden from the Homey device by default. This includes things like Wi-Fi/version text sensors, `disabled_by_default` entities and ESPHome diagnostic/config categories.

Brands can hide additional entities such as status LEDs or factory-reset buttons through their profile, without having to change the mapping code.

Supported domains include light, switch, sensor, binary sensor, cover, climate, fan, lock, button, number, select, media player, valve, siren, event, alarm panel and water heater.

## What this does for brands

A dedicated Homey app gives users the app they would actually expect to search for in the Homey App Store. For example, `Apollo Automation` or `Everything Presence` instead of `ESPHome`.

Without this package, a brand app would basically have to fork the entire ESPHome integration. With it, the brand app is mostly a thin product profile plus the things needed for its own Homey App Store listing.

Typical setup:

1. Create a Homey Python app: `homey app create`
2. Add the packages: `homey app dependencies add aioesphomeapi homey-esphomedriver`
3. Copy Compose templates: `esphome-homey sync`
4. Add one Homey driver per product: `homey app driver create`
5. Set `$extends: ["esphome-defaults"]` and `esphome.projects` in `driver.compose.json` to the firmware `project.name`
6. Add the product names, icons and PNG store images
7. Publish using the Homey CLI

Homey CLI does not copy these templates. `esphome-homey sync` writes them into `.homeycompose/`; commit that output so `homey app run` works on a stock CLI. Re-run `sync` after a core upgrade.

The generic `io.esphome` app installs this package from PyPI the same way (`homey app dependencies add homey-esphomedriver`).

### Brand profile (compose `esphome`)

Normally there is one Homey driver per product SKU. Product filters go on that driver's `driver.compose.json`, same place Zigbee puts `manufacturerName` / `productId`. Homey's driver schema allows extra keys, so `esphome` survives `homey app validate` and is available at runtime as `self.manifest["esphome"]`.


| Compose key              | What a brand uses it for                                                                                          |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `projects`               | Only allow specific firmware on this driver, for example `Brand.AQ-1`                                             |
| `projectPrefix`          | Match all projects using a prefix such as `Brand.`. Less common, but useful when several products share a driver  |
| `clientInfo`             | Name shown on the ESPHome device for the connected client, for example `Homey Brand`                              |
| `hiddenEntities`         | Hide things like status LEDs, OTA helpers or duplicate sensors                                                    |
| `deviceEntities`         | Map VOC, NOx, PM and similar entities to proper Homey capabilities instead of generic gauges                      |
| `deviceClassOverrides`   | Force a Homey device class such as socket, sensor or speaker                                                      |
| `settingEntities`        | Map a settings-page field onto an ESPHome entity, so writing the setting writes the entity                        |
| `displaySlots`           | Naming for the Homey display-slot convention; defaults match the documented convention                            |


`settingEntities` exists because Homey device settings are declared statically in `driver.compose.json` — there is no API to add a field per device at pair time. A driver written for a known product can therefore declare the field itself and name the entity it configures, and core writes that entity whenever the setting changes. Configuration entities (`entity_category: config` on the node) are the motivating case: a calibration offset belongs beside Host and Port rather than on the device tile. The generic `io.esphome` driver cannot use this, since it does not know in advance what it will pair with.

If both `projects` and `projectPrefix` are configured, a match on either is enough.

If neither is configured, every ESPHome project is accepted. This is how `io.esphome` works.

`after_map` cannot live in JSON. Set a class-level `brand_profile` on the driver when a Python hook is needed; that overrides compose.

```json
{
  "name": { "en": "AQ-1" },
  "class": "sensor",
  "$extends": ["esphome-defaults"],
  "esphome": {
    "projects": ["Brand.AQ-1"]
  }
}
```

Everything else, including pairing, mapping and reconnect logic, stays inside the package.

### Lifecycle hooks

`EspHomeDriver` and `EspHomeDevice` seal `on_init` / `on_uninit`. Brand apps extend `on_esphome_init`, `on_esphome_connected`, and `on_esphome_uninit` instead (mirrors `homey-oauth2app`). Brand `app.py` exports Homey's `App`.

On `EspHomeDevice`, `on_esphome_init(client)` runs after capability listeners are registered and `_ensure_client_started()` has run. `client` is `None` when the node has no host yet (waiting on mDNS). Use the public `client` property to read the session later; command paths still use `_require_client()` when a live connection is required.

`on_esphome_connected(client)` runs at the end of each successful login (including reconnects), after Homey `set_available()`. The session is still not READY when the hook runs — `_mark_ready()` happens after `_on_connected` returns — so brand code must not issue commands until `client.available` / `_require_client()`.

`on_esphome_init` fires once at device init and so cannot re-register per-connection state; use `on_esphome_connected` for anything the node discards on a drop. The user-defined action list is the motivating case: `EspHomeClient` rebuilds its action cache on each connect, and a node reflashed while paired may declare a different set.

### What the brand owns vs what core owns


| Brand app                                       | This package                                                                        |
| ----------------------------------------------- | ----------------------------------------------------------------------------------- |
| Store listing, app ID, product names and images | Native API client and reconnect logic                                               |
| One driver folder per SKU                       | ESPHome entity to Homey capability mapping                                          |
| `esphome` block in `driver.compose.json`        | Improv BLE, encryption and mDNS/IP pairing                                          |
|                                                 | Compose defaults, pairing UI, Flow cards, locales and default SVGs (`esphome-homey sync`) |


`esphome-homey sync` refreshes the Homey files owned by the package after a core upgrade.

It does not overwrite `app.json`, `app.py` or existing `driver.py` / `device.py` files unless `--force` is explicitly used.

## Two app shapes, one core


|               | Generic `io.esphome`                    | Brand `{brand}-homey`                    |
| ------------- | --------------------------------------- | ---------------------------------------- |
| Who it is for | Any ESPHome node                        | Products from that manufacturer          |
| Drivers       | One catch-all driver                    | One driver per product SKU               |
| Filter        | None                                    | Exact `project.name` matching per driver |
| Store         | “ESPHome”                               | The brand name users already know        |


Firmware should set ESPHome `project.name`, for example `Brand.AQ-1`.

That value is what the brand driver uses to recognize its own hardware. It also allows the generic ESPHome app to point users towards the dedicated Homey app when one is available.