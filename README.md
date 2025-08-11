# Сервис синхронизации треков из Garmin Connect в OpenStreetMap

Этот проект позволяет автоматически синхронизировать треки из Garmin Connect в OpenStreetMap (OSM) с использованием OAuth2 для аутентификации.

## Подготовка

1. Скопируйте шаблон `env_example` в файл `.env` в корне проекта и заполните переменные (см. комментарии в файле):

   ```bash
   cp env_example .env
   # затем отредактируйте .env
   ```

2. Убедитесь, что в приложении OpenStreetMap (<https://www.openstreetmap.org/oauth2/clients>) вы зарегистрировали приложение и указали `REDIRECT_URI` точно так же, как в `.env`.
3. Если у вас был старый `processed_ids.txt`, можно позже выполнить миграцию в SQLite (см. раздел «Миграция»).

## Установка (локально)

1. Создайте виртуальное окружение и активируйте его:

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # macOS / Linux
   .\.venv\Scripts\activate  # Windows (PowerShell)
   ```

2. Установите зависимости:

   ```bash
   pip install -r requirements.txt
   # если нет requirements.txt:
   pip install garminconnect requests python-dotenv requests-oauthlib
   ```

## Запуск

1. Запустите скрипт:

   ```bash
   python main.py
   ```

2. При первом запуске скрипт откроет браузер для авторизации в OpenStreetMap. После подтверждения разрешений (scopes: `read_gpx write_gpx`) скрипт получит код и обменяет его на `access_token` и `refresh_token`, которые будут сохранены в `tokens.json`.
3. Повторный запуск будет использовать `tokens.json`; если `access_token` истечёт, скрипт автоматически попытается обновить токен через `refresh_token`.

## Что делает скрипт

- логинится в Garmin Connect (использует `GARMIN_EMAIL` и `GARMIN_PASSWORD` из `.env`);
- скачивает последние активности (количество регулируется через `MAX_ACTIVITIES`);
- сохраняет GPX временно в `DOWNLOAD_DIR` и загружает его в OSM через OAuth2 `Bearer` токен;
- помечает активности в SQLite (файл `data.db` по умолчанию) как обработанные, чтобы не загружать их повторно.

## Миграция processed_ids.txt → SQLite

Если раньше вы хранили обработанные id в `processed_ids.txt`, выполните миграцию:

1. В репозитории есть `migrate_txt_to_db.py` — запустите его один раз:

   ```bash
   python migrate_txt_to_db.py
   ```

2. Проверьте результат:

   ```bash
   sqlite3 data.db "SELECT COUNT(*) FROM processed_activities;"
   sqlite3 data.db "SELECT activity_id, uploaded_at, status FROM processed_activities ORDER BY uploaded_at DESC LIMIT 10;"
   ```

## Отладка — частые проблемы

- **401 Couldn't authenticate you**
  - Убедитесь, что `tokens.json` существует и что `refresh_token` актуален. При необходимости запустите `python main.py` и пройдите авторизацию вручную.
  - Проверьте, что зарегистрированное приложение OSM имеет scope `read_gpx write_gpx`.
  - Проверьте точное совпадение `REDIRECT_URI` в настройках OSM и в `.env`.

- **Проблемы с Garmin авторизацией**
  - Если `garminconnect` требует 2FA или блокирует логин, посмотрите вывод в консоли — библиотека обычно описывает, что нужно сделать.
  - В некоторых случаях помогает смена `GARMIN_USER_AGENT` (настраивается в коде) или интерактивный вход в браузере.

- **Файл tokens.json**
  - Файл содержит чувствительные токены — не коммитьте его в репозиторий. Добавьте в `.gitignore`: `tokens.json`, `.env`, `data.db`, `/state`.

## Команды быстрого контроля

- Показать 5 последних обработанных активности:

  ```bash
  sqlite3 data.db "SELECT activity_id, uploaded_at, status FROM processed_activities ORDER BY uploaded_at DESC LIMIT 5;"
  ```

- Запустить миграцию вручную (если нужно):

  ```bash
  python migrate_txt_to_db.py
  ```

## Безопасность

- Храните `.env`, `tokens.json`, `data.db` в защищённом месте. Для production используйте секретный стор.
- Если вы случайно опубликовали OSM client secret, Garmin пароль или `tokens.json`, немедленно отозвите/перегенерируйте секреты и смените пароль Garmin.

## Что можно сделать дальше

- Автоматизировать запуск через cron / systemd timer (я могу добавить пример unit-файла).
- Перенести `tokens.json` в таблицу SQLite (`tokens`) для более централизованного хранения.
- Добавить CLI-команды для отмены пометки активности / повторной загрузки конкретной активности.
