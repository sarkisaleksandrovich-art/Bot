# HANDOFF

Цель: бот автоматизации Android-игры в LDPlayer через ADB + OpenCV (шаблоны) + tkinter GUI. Навигация: база, карта локаций, иконка локации, кнопка фарма этажа, запуск данжа, автобой. Движение удержанием края джойстика (hold/release), мир статичен, персонажи ходят только влево-вправо.

## Файлы

- `main.py` — CLI, флаги `--check`, `--capture`, `--teach`, `--teach-only`, `--profile`, `--list-profiles`, `--reset-knowledge`, `--gui`, `--navigate`.
- `nav.py` — `nav_status`, `go_to_dungeon`, `verify_chain`, `take_screenshot`, `find_template`, `hold`, `release`, `approach_target`, `click_loot`, `attack_mob`, `handle_event`, `start_battle`, `find_known_objects`, `annotate_known`, `match_known`, `memory_summary`.
- `gui.py` — главное окно, вкладки Управление, Настройки, Память, Бот.
- `gui_base.py` — `SettingsTab`, `capture_region`, консоль, ползунок.
- `gui_farm.py` — `ControlTab`, алиас `FarmTab`, `Num1` старт, `Num2` стоп, `Num3` событие, `Num0` полная остановка.
- `gui_memory.py` — `MemoryTab`, удаление одной иконки с `.bak`.
- пакет `bot/` — `config`, `adb_controller`, `screenshot`, `vision`, `knowledge`, `teacher_ui`, `profiles`, `explorer`, `safety`, `logging_setup`.

## Контракт `data/base/nav.json`

Ключи:
- `base_screenshot`
- `base_to_locations`
- `locations_map`
- `locations` — элементы `slot`, `name`, `png`
- `farm_floor_button`
- `enter_dungeon`
- `autobattle_on`
- `joystick` — вложенные `left` и `right` с `x` и `y`
- `settings` — `click_radius_px`, `approach_timeout_s`, `hold_chunk_ms`, `frame_interval_ms`, `match_threshold`

## Контракт `data/base/actions.json`

Объект с ключом `objects`, элементы `name`, `png`, `action`.

`action` одно из: `loot`, `mob`, `event_choice`, `event_skip`, `skip`.

## Порог `settings.match_threshold`

- Диапазон: `0.05-0.99`.
- По умолчанию: `0.78`.
- Значение вне диапазона заменяется на `0.78` с записью в лог.
- Ползунок `1-10` соответствует порогам: `0.05`, `0.15`, `0.26`, `0.36`, `0.47`, `0.57`, `0.68`, `0.78`, `0.89`, `0.99`.
- По умолчанию уровень `8`.
- Консольные команды: `set match_level` и `set match_threshold` (`0.05-0.99` или `5-99` как проценты).
- Диалог совпадения с памятью: `Оставить`, `Заменить`, `Отмена`.

## Последние коммиты

- `nav.py` — `02ed95595d0e3c3346aed16668996b93cb81537a`.
- `gui_base.py` — `d843135e2753cb2804de4ec38eb2593f0d6dfff4`.
- `gui_farm.py` — `0dbc9e5904c6995f1b320b12ab7d3ff63f3560c0`, затем `8be858936baed96a20e5547d8d71deb4e7ad5d31`.
- Для `8be858936baed96a20e5547d8d71deb4e7ad5d31`: `match_known` как строка или `None`, защита от повторного обучения для Лут/Моб/Событие, надёжнее закрытие `Toplevel`.

## Статус

Статическая сверка стыков `main`, `nav`, `gui`, `gui_base`, `gui_farm`, `gui_memory` = PASS по тексту кода. НИЧЕГО не проверено запуском в LDPlayer.

## Известные риски, не проверено запуском

- `motionevent` на Android 9 и fallback через `swipe`.
- `approach_target` — проскок, упор в край.
- Распознавание и подбор порога.
- `tkinter` `Toplevel` и `wait_window` на Windows.
- Горячие Num-клавиши через `pynput`.
- Кодировка Windows.
- `main.py --navigate` принимает `location` только из `loc1..loc7`, а GUI передаёт имя локации — возможное расхождение, проверить.
- `gui_farm` не показывает `score` совпадения, потому что `nav.match_known` возвращает только строку действия.
- `nav.py` читает `selected_floor` из `nav.json`, а GUI его не пишет.

## Как продолжать

- Короткие циклы, `5-7` небольших задач на запуск, лимит запуска `1200` секунд.
- Сначала запуск у пользователя и логи (`logs/bot_live.log`, трейсбеки), потом правки по реальным ошибкам.
- Отчёты агентов сверять с коммитами.

Следующие шаги:
- тест ADB и скриншота (`main.py --check`)
- `verify_chain`
- `hold/release` на устройстве
- подбор порога
- тест сохранения памяти
