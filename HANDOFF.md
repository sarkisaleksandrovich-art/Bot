# HANDOFF

Цель: бот Android-игры в LDPlayer через ADB, OpenCV по шаблонам и tkinter GUI. Движение делается удержанием края джойстика.

## Файлы
- `main.py` — CLI, флаги `--check`, `--capture`, `--teach`, `--teach-only`, `--list-profiles`, `--reset-knowledge`, `--gui`, `--navigate`, `--location`, `--floor`, `--profile`, `--floor-from`, `--floor-to`.
- `nav.py` — `nav_status`, `go_to_dungeon`, `verify_chain`, `take_screenshot`, `find_template`, `hold`, `release`, `approach_target`, `click_loot`, `attack_mob`, `handle_event`, `start_battle`, `find_known_objects`, `annotate_known`, `match_known`, `memory_summary`, `wait_battle_end`, `_battle_state`, `_wait_and_click_confirm_start`, `_run_known_objects`, `_go_path`, `_go_home`.
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
- `confirm_start` — кнопка подтверждения старта данжа (после «Войти в данж»)
- `inside_dungeon` — якорь «бот уже внутри локации» (опциональный; пропускает возврат на базу)
- `autobattle_on` — кнопка ВЫКЛЮЧЕННОГО автобоя; найдена — бот в бою и нажимает её
- `autobattle_active` — кнопка ВКЛЮЧЁННОГО автобоя; найдена — бот в бою и ничего не нажимает
- `selected_floor` — опциональный; GUI его не пишет; приоритет: `--floor`, затем `selected_floor`, затем 1
- `joystick` — вложенные `left` и `right` с `x` и `y`
- `settings` — `click_radius_px`, `approach_timeout_s`, `hold_chunk_ms`, `frame_interval_ms`, `match_threshold`
- Видна любая из кнопок `autobattle_on`/`autobattle_active` — бой идёт. Вне боя они не видны.

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

## Константы боя (`nav.py`)
- `DEFAULT_BATTLE_TIMEOUT_S = 120.0` — общий лимит боя в `wait_battle_end`.
- `DEFAULT_STABLE_TIMEOUT_S = 3.0` — пауза «кнопок автобоя не видно», после которой бой считается оконченным.
- Таймаут входа в бой после подхода — `settings.approach_timeout_s`.

## Последние SHA коммитов
- `3c72a05` — бой по кнопкам автобоя (`_battle_state`/`wait_battle_end`/`attack_mob` без слепых тапов), ключ `autobattle_active`, `confirm_start`/`inside_dungeon`, кнопка «Локация»
- `a711b5e` — FIX-D регрессия (профиль резолвится во всех режимах кроме --navigate/--gui/--list-profiles)
- `01b06c8` — FIX A-G
- `33a38f6` — GUI: ползунок прокрутки и кнопка скриншота ADB
- `9e33c08` — docs: project log 2026-11-02
- `5d53415` — docs: HANDOFF 2026-11-02

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
- Возможное ложное `battle finished` в `nav.wait_battle_end`, если кнопки автобоя пропадают с экрана на анимациях дольше `DEFAULT_STABLE_TIMEOUT_S` (3 c) — не проверено запуском.
- В `bot/floor.py` и `bot/explorer.py` нет `stop_event`: остановка фарма только через terminate процесса из GUI или Space в OpenCV-окне разметки — не проверено запуском.
- `nav.py` читает `selected_floor` из `nav.json`, а GUI его не пишет (приоритет: `--floor` → `selected_floor` → 1) — не проверено запуском.
- `motionevent` на Android 9 и fallback через `swipe` — не проверено запуском.
- `approach_target`: проскок, упор в край — не проверено запуском.
- Подбор порога — не проверено запуском.
- `tkinter` `Toplevel` и `wait_window` на Windows — не проверено запуском.
- Глобальные клавиши `pynput` — не проверено запуском.
- Кодировка Windows — не проверено запуском.

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
