# FHD_BIP v0.8.0 — контракт игровых команд

BIP пишет команды по одной строке JSON в:

```text
%LOCALAPPDATA%\FHD_BIP\bridge\commands.jsonl
```

Поддерживаются только четыре действия:

- `galaxy_map.open`;
- `galaxy_map.focus_system`;
- `autopilot.route_and_start`;
- `trade.search_and_mark`.

Пример маршрута:

```json
{"type":"command","schema":"fhd_bip.command.v1","command_id":"...","action":"autopilot.route_and_start","payload":{"system_id":23,"system_name":"Альтаир","integration":"FHD_Autopilot","open_map":true,"focus_system":true},"created_at":"2026-09-15T18:00:00.000+00:00"}
```

Пример поиска товара:

```json
{"type":"command","schema":"fhd_bip.command.v1","command_id":"...","action":"trade.search_and_mark","payload":{"good_id":4,"good_name":"Медицина","integration":"FHD_Trade","marker_owner":"FHD_Trade","preserve_foreign_markers":true},"created_at":"2026-09-15T18:00:00.000+00:00"}
```

Перед разрешением команд игровой мост передаёт точные каталоги:

```json
{"type":"capabilities","integrations":["FHD_Autopilot","FHD_Trade"],"known_systems":[{"id":23,"name":"Альтаир","open":true}],"trade_goods":[{"id":4,"name":"Медицина"}]}
```

После исполнения игровой потребитель пишет в `events.jsonl`:

```json
{"type":"command_result","command_id":"...","action":"autopilot.route_and_start","status":"completed","message":"Маршрут построен, автопилот включён"}
```

Допустимые статусы: `accepted`, `running`, `completed`, `failed`, `rejected`.
Только `completed` означает, что действие действительно выполнено. Неизвестный
`command_id`, неизвестное действие или имя, отсутствующее в точном каталоге,
должны быть отклонены. Метка `FHD_Trade` не должна менять состояния меток
`AMod_MapMarker`, `FHD_Stations` и `FHD_GalaxySearch`.
