# YO MANUAL
**Version:** v0.3.1  
**Status:** Local-first programmable environment

---

## 1. What is yo?

**yo** is a local-first system that exposes the physical and digital world as a **filesystem of resources**.

Instead of apps, dashboards, or cloud APIs, yo gives you:

- paths you can list
- state you can read
- actions you can write to
- events you can subscribe to

If you can use a shell, you can use yo.

---

## 2. Core idea

> **Everything in yo is a path.  
> Paths can be listed, read, written to, or streamed.**

This applies equally to:
- devices
- people
- chat rooms
- web pages
- automations
- permissions

---

## 3. The primitives

### Paths
Hierarchical names, like a filesystem.

Examples:
- `devices/light.kitchen`
- `chat/rooms/general`
- `web/pages/status`

### Listings (`ls`)
Discover what exists.

```bash
yo ls /
yo ls devices
yo ls chat/rooms
```

### State (`cat`)
Read current values.

```bash
yo cat devices/light.kitchen/state
yo cat chat/rooms/general/history
yo cat web/pages/status/content
```

### Actions (`write`)
Change something or invoke behavior.

```bash
yo write devices/light.kitchen/power on
yo write chat/rooms/general/post "hello"
yo write web/pages/status/content "# Updated"
```

### Events (`sub`)
Subscribe to real-time streams.

```bash
yo sub devices/motion.living_room/events
yo sub chat/rooms/general/events
yo sub web/pages/status/events
```

### Bindings (`bind`)
Declarative reactions: event → action.

```bash
yo bind "devices/motion.living_room/events:motion==true" \
        "devices/light.kitchen:set_power(on)"
```

### Identity
Lightweight identity and optional auth.
Reads are open by default; writes can be protected.

---

## 4. Filesystem map

```text
/
├─ devices
├─ bindings
├─ auth
├─ chat
└─ web
```

---

## 5. Devices

```bash
yo ls devices/light.kitchen
```

```text
state
actions
streams
power
brightness
```

---

## 6. Chat

```bash
yo ls chat/rooms
yo write chat/rooms/general/post "hello"
yo sub chat/rooms/general/events
yo cat chat/rooms/general/history
```

---

## 7. Web pages

```bash
yo ls web/pages
yo cat web/pages/status
yo write web/pages/status/content "# Hello"
yo sub web/pages/status/events
```

---

## 8. Scripting model

yo exposes reality.
The shell orchestrates it.

```bash
yo sub devices/motion.living_room/events \
| jq 'select(.data.motion==true)' \
| while read _; do
    yo write devices/light.kitchen/power on
  done
```

---

## 9. Naming philosophy

Verbs are path suffixes.
Meaning is defined by convention.

To change a verb, edit:
- `Hub.ls()`
- `Hub.cat()`
- `Hub.write_path()`

---

## 10. What yo is (and is not)

**yo is:**
- local-first
- inspectable
- scriptable
- composable

**yo is not:**
- a cloud platform
- a UI framework
- a rules engine

---

## Final note

If you can:
- list it
- read it
- write it
- observe it

It belongs in yo.
