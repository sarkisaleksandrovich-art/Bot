# Лог работы ИИ-ассистента над проектом Bot

Репозиторий: `https://github.com/sarkisaleksandrovich-art/Bot`, ветка `feature/new-branch`.
Все даты — 2026-11-03/04. Все правки минимальные, формат кода и кириллические строки сохранены.

## 1. Аудит кода (ШАГ 1, только чтение)
- Прочитаны `nav.py`, `main.py`, `data/base/nav.json`.
- Задокументированы: сигнатуры `_log`, `take_screenshot`, `find_template`, `_click_center`, `start_battle`; формат ключей nav.json (`{"png": ...}`, rect/threshold вычисляются в рантайме); обработка результата `start_battle` вызывающим кодом `(ok, reason)`.
- `git status`: чисто (не отслеживались только data/, logs/, screenshots/, config.json).

## 2. Кнопка «Старт» — скриншот через ADB (gui_base.py)
- Добавлена кнопка (позже переименована: описание «Кнопка старт», кнопка «Сделать скриншот») в блок «Навигация» после «Кнопка Фарм текущего этажа».
- Метод `_screenshot_button_clicked()` → через `capture_region()`: снимок ADB + окно выделения области → сохранение в `logs/shot_crop_<timestamp>.png`, путь в консоль GUI.
- Добавлена галочка ✓/✗ (✓ — если есть `logs/shot_crop_*.png`; ставится сразу после сохранения).

## 3. Ползунок прокрутки вкладки «Настройки» (gui_base.py)
- Контент обёрнут в `tk.Canvas` + вертикальный `ttk.Scrollbar`.
- Внутренний фрейм растягивается по ширине, `scrollregion` обновляется на `<Configure>`.
- Колесо мыши работает только когда курсор внутри вкладки (`bind_all` на Enter/Leave).

## 4. FIX-A…FIX-G (коммит `01b06c8`)
- **FIX-A** (`main.py`, `nav.py`): `--floor` доходит до навигации — `go_to_dungeon(..., floor_number=...)`; в `_go_path` этаж из аргумента приоритетнее `selected_floor` из nav.json. Удалены дублирующие print `[1/8]`/`[2/8]` в `_run_check`.
- **FIX-B** (`gui_farm.py`): запись `actions.json` переведена на `os.fdopen(fd, ...)` — временный файл закрывается, на Windows `replace` больше не падает с PermissionError.
- **FIX-C** (`gui_farm.py`): ручные действия Лут/Моб/Событие передают в nav **путь к сохранённому png**, а не numpy-массив; `_save_action_memory()` возвращает имя сохранённого шаблона.
- **FIX-D** (`main.py`): проверка «только один режим» — до резолва профиля; `--list-profiles`/`--gui`/`--navigate` не падают на отсутствующем профиле.
- **FIX-E** (`nav.py`): `base_screenshot` и `locations_map` читаются через `_extract_png` (формат `{"png": ...}`) в `_nav_chain_templates` и `nav_status` — шаблон базы находится, `_go_home` работает.
- **FIX-F** (`nav.py`): `match_known()` возвращает `(action, name, png, score)` вместо строки — GUI знает, какой объект совпал.
- **FIX-G** (`gui_farm.py`): флаг `_last_match_decision` (`none|keep|replace|cancel`); Keep/Отмена завершают диалог (меню действий не открывается); Replace открывает меню с `replace_known=True` и заменяет именно совпавший объект (имя+png из FIX-F).

## 5. Регрессия FIX-D (коммит `a711b5e`, main.py)
- `needs_profile = not (args.navigate or args.gui or args.list_profiles)` — профиль резолвится во всех режимах, включая обычный запуск фарма `python main.py --profile <имя>`.

## 6. GUI: полный запуск без консоли (gui_farm.py, start_gui.bat)
- `_run_main_process()`: `subprocess.Popen(..., creationflags=subprocess.CREATE_NO_WINDOW)` — окно консоли не вспыхивает при старте фарма.
- `bot/adb_controller.py::_run()`: то же для **всех** вызовов adb.exe (скриншоты/тапы не мелькают окнами при запуске через `pyw`).
- `start_gui.bat`: приоритет `pyw.exe` (оконный интерпретатор), fallback на `py`.

## 7. Иконка приложения (gui.py, icon.ico)
- `icon.ico` (череп демона) скопирован в корень проекта.
- В `GuiApp.__init__()`: `SetCurrentProcessExplicitAppUserModelID("LDPlayerBot.GUI.1")` **до** создания `Tk`, затем `iconbitmap` + `iconphoto` (через Pillow) — иконка в заголовке, панели задач и Alt+Tab держится между запусками.

## 8. Скорость запуска GUI (gui_farm.py)
- `refresh_ready_state()` перенесена в фоновый поток (`nav.nav_status` с ADB-скриншотом больше не блокирует открытие окна); результат применяется через `self.after(0, ...)` (`_apply_ready_state`).

## 9. ШАГ 2 — кнопка «Подтвердить старт» (nav.json, nav.py, gui_base.py)
- nav.json: ключ `"confirm_start": {"png": ...}` (формат `enter_dungeon`); пользователь привязал шаблон через GUI.
- GUI: строка «Кнопка Подтвердить старт» с кнопкой и галочкой из nav.json.
- `nav.py::_wait_and_click_confirm_start()`: после клика «Войти в данж» вместо `time.sleep(1.0)` — опрос экрана (таймаут `approach_timeout_s`), клик по центру rect, проверка исчезновения кнопки (экран сменился); повторного клика по исчезнувшей кнопке нет; ошибки логируются; неуспех — `(False, reason)`; при пустом шаблоне — fallback на `time.sleep(1.0)` с логом.

## 10. «Бот больше не сдаётся» (bot/explorer.py)
- `_ensure_safe_known_frame()`: вместо `ExplorerError` после `max_unknown_stable_screens` — бот **всегда** открывает окно разметки с пояснением («Стабильный экран не распознан (неизвестных подряд: N)»); выход только по `q`/закрытию окна (осознанное действие).

## 11. Кнопка «Локация» (gui_farm.py)
- Новая кнопка рядом с «Лут»/«Моб»: снимает текущий экран, открывает окно разметки TeacherUI с подсказкой («выделите устойчивый участок и нажмите r — экран комнаты»), разметка сохраняется в память выбранного профиля (`KnowledgeBase` + `TeacherUI.show_and_label`).

## 12. Определение «я уже внутри локации» (nav.json, nav.py, gui_base.py)
- nav.json: ключ `"inside_dungeon": {"png": ...}` (опциональный).
- GUI: строка «Якорь: внутри локации» (после «Кнопка Подтвердить старт»).
- `_go_path()`: перед `_go_home` ищется якорь; если найден — возврат на базу и цепочка пропускаются, сразу `_run_known_objects` и фарм (лог: «уже внутри локации; навигация пропущена»); не найден — прежнее поведение. Ключ не обязателен (исключён из проверки `template missing`).

## 13. Фикс списка профилей (gui_memory.py)
- Из списка профилей GUI исключены служебные каталоги `base`, `knowledge`, `common` — раньше выбор «knowledge» валил фарм (`Unknown profile 'knowledge'`, принимает только loc1…loc7).

## 14. Прочее
- Кнопка «Полная остановка» перекрашена из красной в светло-голубую (`#add8e6`/`#87ceeb`, чёрный текст).
- Выполнены фикс-консультации: структура фарма (`DungeonExplorer` + память профиля), настройки `motion` (`stable_diff_threshold`, `stable_frames`) против вопросов из-за анимации.

## Коммиты на GitHub
| Коммит | Сообщение |
|---|---|
| `33a38f6` | GUI настроек: ползунок прокрутки вкладки и кнопка скриншота через ADB |
| `01b06c8` | FIX A-G: --floor в навигацию, запись actions.json на Windows, ручные действия по пути шаблона, резолв профиля по режимам, чтение base_screenshot/locations_map через _extract_png, match_known возвращает объект, Replace/Keep в диалоге |
| `a711b5e` | FIX-D регрессия: профиль резолвится во всех режимах кроме --navigate/--gui/--list-profiles |

## НЕ закоммичено (актуально на момент записи лога)
`nav.py` (confirm_start/inside_dungeon), `gui_base.py` (строки confirm_start/inside_dungeon, кнопка старт), `gui_farm.py` (кнопка «Локация», CREATE_NO_WINDOW, фоновая проверка готовности), `gui.py` (иконка), `bot/explorer.py` (не сдаётся), `bot/adb_controller.py` (CREATE_NO_WINDOW), `gui_memory.py` (фикс списка профилей), `start_gui.bat` (pyw), `data/base/nav.json` (confirm_start/inside_dungeon), `icon.ico`.

## Незакрытые вопросы
1. Перенести разметку из `data/profiles/knowledge/` в `loc1/` (пользователь размечал с выбранным knowledge).
2. Проверить фарм «Старт» внутри локации после выбора loc1.
3. При остановках FloorExplorer (лимиты комнат) — применить тот же принцип «спрашивать, а не сдаваться».
4. git push накопленных изменений (п. «НЕ закоммичено»).
