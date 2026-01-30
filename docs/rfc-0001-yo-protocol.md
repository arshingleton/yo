# RFC-0001: yo Protocol v0.1.0

**Status:** Draft  
**Updated:** 2026-01-29

## Transport
- TCP + JSON Lines (UTF-8)
- Default port: 7331

## Discovery
- mDNS: `_yo._tcp.local.`
- TXT props: `node_id`, `proto`

## Envelope
Required:
- `t`, `v`, `id`, `ts`, `from`

Optional:
- `token` (v0 auth)

## Types
- `hello` (server→client): `node_id`, `auth`
- `ls` / `cat`
- `write` (filesystem-native control)
- `sub` / `event`
- `command` / `result`
- `bind` / `unbind`
- `grant`

## write
Request:
- `path` (string)
- `value` (any JSON)
- `dry_run` (bool, optional)

Response:
- `ok` (bool)
- `data` (object; MAY include `dry_run`, `would_set`)

Writable paths in v0.3 hub:
- `devices/<id>/power`
- `devices/<id>/brightness`
- `bindings/<i>/enabled`

## Persistence (hub)
Default: `~/.yo/hub/<node_id>/state.json`
Persists:
- bindings: expr/target/enabled/min_interval_ms
- rules (future expansion)

## Auth (v0)
If hub starts with `--auth`, non-read operations require an admin `token`.


## v0.1.0 resources (non-normative)
- chat rooms as paths: `chat/rooms/<room>/post`, `chat/rooms/<room>/history`, stream `chat/rooms/<room>/events`
- web pages as paths: `web/pages/<page>/content`, `web/pages/<page>` and stream `web/pages/<page>/events`
