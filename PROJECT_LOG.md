# Журнал проекта

## Цель и контекст

- Бот для Android: съёмка экрана через scrcpyAI Studio.
- Анализ изображения: OpenCV, LDPplayer.
- Координаты хранятся в `floor_state.json`, профили `loc1`–`loc3`.
- `py main.py --check`: ADB на Android 9, разрешение 1600x900.
- GUI: разрешение 1200, LDPplayer.

## Запуск 2026-11-01

### Агент навигации
- Проверен `nav.py`, CLI, `NavigationController` в `main.py`.
- `nav.py`, SHA `b2d435e3fb66cb2ee1cc07c1f7c0f88de251b4`, и `main.py`, SHA `be4d3cc0c86a0bc09370835c9576e83b611625ae`, прошли проверку API: PASS.

### Агент GUI
- Проверены `gui.py`, SHA `3163c5453ca955bbb981ae3c967d655f7c58cd8`, и `main.py`, SHA `769dbd4ca273ac5c7911280450f99168a9fb8e2d0`. Проверка запуска subprocess и связей прошла: PASS для Windows, вывод не проверен.
- Проверен `gui_base.py`, SHA `438c651c176a514cb53c21ba7a3d7cb00f0cf97a`, и `gui_memory.py`, SHA `205cb9b03beab739f0bbdadf8ae8ebc71c38ae9b`. Проверка PNG/JSON: PASS.
- Проверен `gui_farm.py`, SHA `85db28cf18ac897d12f29eca1561e7e7136aae81`, callback: PASS.
- `start_gui.bat`, SHA `7d4d4ffdec9dbebabbd58de8906b1718f610f283e`, и `GUI_README.md`, SHA `9b05a884f2a973b01120dfcde98b4e8a48cecac0`, проверка Windows: PASS.
- Проверен стык `NavigationController`; SHA `d734780674b8b2c4ecdc44176564b511768504c8`: PASS.

### Проверка
- Число кругов: 1.
- Вердикт: FAIL: `data/base/nav.json` отсутствует в `main`; формат JSON и полная навигационная цепочка не подтверждены.
- Не проверено: PNG, Windows/LDPplayer, ADB, callback Tkinter, реальные файлы профилей.

### Открытые задачи
- Ручная проверка GUI на ПК после `git pull`.
- Обучение на вкладке «Навигация».
- Шаблоны loc1.
- Пороги motion.
- Перенос на loc2–loc7.
- Передеплой.

## Запуск 2026-11-02

- Прочитан полный текущий `PROJECT_LOG.md` в ветке `main`; исходный SHA файла: `b86658394cebea18b1791e85aa7f7d71787f492a`; размер: 5817 байт.
- Получено содержимое корня репозитория `sarkisaleksandrovich-art/Bot` в `main`. Факты о файлах и SHA: `RUN_REPORT.md` — `df4305085a7589e75260f7e25a3927d807fc5890` (10035 байт), `main.py` — `be4d3c0c86a0bc09370835c9576e83b611625aea`, `nav.py` — `42493c2d64766c4a42273fa699ea1c336592e2bd`, `gui.py` — `df117335494dadf167f252698ff314877aa82e83`, `gui_base.py` — `34bbbfcb323a8b300ece91fdb93ae7698aa00c09`, `gui_memory.py` — `46e23ca3d072d5950316ddc462a4f711bc0f211c`, `gui_farm.py` — `8bed17a08558f98641ae676b65bf14027e489c9e`, `start_gui.bat` — `7d4d4ffdec9dbeabbd58de8906b1718f610f283e`, `GUI_README.md` — `9b05a884f2a973b01120dfcde98b4e8a48cecac0`.
- По доступным отчётам задачи: заявлены проверки API для `nav.py` и `main.py` — PASS; GUI и связи subprocess — PASS с оговоркой, что вывод не проверен; PNG/JSON — PASS; callback — PASS; Windows-проверка — PASS; стык `NavigationController` — PASS. Итоговый вердикт отчёта — FAIL из-за отсутствия `data/base/nav.json`; полная навигационная цепочка и формат JSON не подтверждены.
- Число кругов: 1 по имеющейся записи. Новых проверок кода, запуска GUI, Windows, Tkinter, ADB, PNG и реальных профилей в этом запуске не выполнялось; не проверено.
- Ошибки: в отчёте указано отсутствие `data/base/nav.json`; это не исправлялось.
- Открытые задачи по фактам: ручная проверка GUI на ПК после `git pull`; обучение на вкладке «Навигация»; шаблоны loc1; пороги motion; перенос на loc2–loc7; передеплой.
- Размер новой версии: 7600 байт; старый текст сохранён без изменения и присутствует полностью.
