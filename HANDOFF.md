# HANDOFF

Цель: бот Android-игры в LDPlayer через ADB, OpenCV по шаблонам и tkinter GUI. Движение делается удержанием края джойстика.

## Файлы
- `main.py` — CLI, флаги `--check`, `--capture`, `--teach`, `--teach-only`, `--list-profiles`, `--reset-knowledge`, `--gui`, `--navigate`, `--location`, `--floor`, `--profile`, `--floor-from`, `--floor-to`.
- `nav.py` — `nav_status`, `go_to_dungeon`, `verify_chain`, `take_screenshot`, `find_template`, `hold`, `release`, `approach_target`, `click_loot`, `attack_mob`, `handle_event`, `start_battle`, `find_known_objects`, `annotate_known`, `match_known`, `memory_summary`.
- `gui.py` — главное окно, вкладки `Управление`, `Настройки`, `Память`, `Бот`.
- `gui_base.py` — `SettingsTab`, захват области, консоль, ползунок.
- `gui_farm.py` — `ControlTab`, alias `FarmTab`, горячие клавиши `Num1` старт, `Num2` стоп, `Num3` событие, `Num0` полная остановка.
- `gui_memory.py` — `MemoryTab`, удаление одной иконки с `.bak`.
- пакет `bot/` — `config`, `adb_controller`, `screenshot`, `vision`, `knowledge`, `teacher_ui`, `profiles`, `explorer`, `safety`, `logging_setup`.

## Контракт `data/base/nav.json`
Ключи:
- `base_screenshot`
- `base_to_locations`
- `locations_map`
- `locations` — элементы с `slot`, `name`, `png`
- `farm_floor_button`
- `enter_dungeon`
- `autobattle_on`
- `joystick` — вложенные `left` и `right` с `x` и `y`
- `settings` — `click_radius_px`, `approach_timeout_s`, `hold_chunk_ms`, `frame_interval_ms`, `match_threshold`

## Контракт `data/base/actions.json`
Объект с ключом `objects`, элементы содержат:
- `name`
- `png`
- `action`

`action` может быть только из: `loot`, `mob`, `event_choice`, `event_skip`, `skip`.

## Порог `match_threshold`
- Допустимый диапазон: `0.05-0.99`.
- По умолчанию: `0.78`.
- Ползунок `1-10` соответствует значениям: `0.05`, `0.15`, `0.26`, `0.36`, `0.47`, `0.57`, `0.68`, `0.78`, `0.89`, `0.99`.
- Команды консоли: `set match_level` и `set match_threshold`.

## Последние SHA коммитов
Из задания и прошлых отчётов:
- `nav.py` — `02ed95595d0e3c3346aed16668996b93cb81537a`
- `gui_base.py` — `d843135e2753cb2804de4ec38eb2593f0d6dfff4`
- `gui_farm.py` — `8be858936baed96a20e5547d8d71deb4e7ad5d31`
- `main.py` — `ff43ea8f7019152d4b3217d06c9d53fb6d677acd`
- `nav.py` — `d8757c2f26e68fb18bccd51a9be66138c94e2358`
- `gui_farm.py` — `e934e8ece1a69f8a9a5eec2008c77f87f0914bef`

## Что исправлено в этом запуске
- Создана новая версия `HANDOFF.md` в корне репозитория.
- В `PROJECT_LOG.md` добавлена запись за `2026-11-02`.
- Новых правок в коде бота в этом запуске не было.

## Вердикт ревью
По прошлому отчёту:
- `main.py` — PASS
- `nav.py` — PASS
- `gui.py` — PASS
- `gui_base.py` — PASS
- `gui_memory.py` — PASS
- `gui_farm.py` — PASS после коммита `e934e8ece1a69f8a9a5eec2008c77f87f0914bef`

## Риски, не проверено запуском
- `motionevent` на Android 9 и fallback через `swipe` — не проверено запуском.
- `approach_target`: проскок, упор в край — не проверено запуском.
- Подбор порога — не проверено запуском.
- `tkinter` `Toplevel` и `wait_window` на Windows — не проверено запуском.
- Глобальные клавиши `pynput` — не проверено запуском.
- Кодировка Windows — не проверено запуском.
- В `gui_farm` show score не отображается, потому что `nav.match_known` возвращает только строку действия — не проверено запуском.
- `main.py --navigate` принимает `location` строкой вместо `loc1..loc7`, а GUI передаёт имя локации — не проверено запуском.
- `nav.py` читает `selected_floor` из `nav.json`, а GUI его не пишет — не проверено запуском.

## Как продолжать
- Работать короткими циклами по `5-7` задач, лимит запуска `1200` секунд.
- Сначала собирать логи реального запуска: `logs/bot_live.log`, трейсбеки.
- Потом делать правки по реальным ошибкам.

Следующие шаги:
- тест ADB и скриншота (`main.py --check`)
- `verify_chain`
- `hold` и `release` на устройстве
- подбор порога
- тест сохранения памяти
