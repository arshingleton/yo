# yo ✌️

yo is a local-first, shell-native system that lets you explore, control, and automate the physical and digital world as a filesystem.

If you can `ls`, `cat`, and `write`, you can use yo. Devices, data, chat, web pages, and automation all appear as paths you can inspect, modify, and subscribe to — with no cloud, no UI, and no configuration required to get started.


Highlights:
- Filesystem-native control: `yo ls`, `yo cat`, **`yo write`**
- Auto-connect: `yo discover` caches hub in `~/.yo/config.json`
- Persistent hub state: bindings + rules saved at `~/.yo/hub/<node_id>/state.json`
- Safety: `--dry-run` on `write`, `do`, `bind`, `grant`
- Bindings can be enabled/disabled: `yo enable 0`, `yo disable 0`

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run hub

```bash
yod run
```

Raspberry Pi GPIO LED (optional):

```bash
yod run --gpio-led-pin 17 --gpio-led-node devices/led.living_room
# then:
yo write devices/led.living_room/power on
```

## Try it

```bash
yo discover
yo ls /
yo ls devices
yo ls devices/light.kitchen
yo cat devices/light.kitchen/state
yo write devices/light.kitchen/power on
yo write devices/light.kitchen/brightness 80
yo sub devices/light.kitchen/state
```

Bindings (persisted):

```bash
yo bind "devices/motion.living_room/events:motion==true" "devices/light.kitchen:set_power(on)"
yo ls bindings
yo cat bindings/0
yo disable 0
yo enable 0
```

Safety:

```bash
yo write --dry-run devices/light.kitchen/power on
yo do --dry-run devices/light.kitchen set_brightness brightness=25
yo bind --dry-run "devices/motion.living_room/events:motion==true" "devices/light.kitchen:set_power(on)"
```

Auth (optional):

```bash
yod run --auth
export YO_TOKEN="PASTE_TOKEN"
yo grant person.guest write devices/light.kitchen*
```

Chat:
```bash
yo ls chat/rooms
yo write chat/rooms/general/post '{"from":"person.andrew","text":"hello"}'
yo sub chat/rooms/general/events
yo cat chat/rooms/general/history
```

Web pages:
```bash
yo ls web/pages
yo cat web/pages/status
yo write web/pages/status/content '# Status\n\nUpdated from yo.'
yo sub web/pages/status/events
```
