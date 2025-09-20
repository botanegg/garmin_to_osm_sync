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

## Запуск в режиме загрузки истории

Если вы хотите загрузить все активности из Garmin Connect (а не только новые), установите в `.env`:

```env
MAX_ACTIVITIES=200  # или любое большое число
```

и запустите скрипт с опцией `--history`:

```bash
python main.py --history
```

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

## Идеи по контейнеризации (Docker)

Ниже — практический план подготовки этого проекта к запуску в контейнере (Docker). Пошаговые пункты можно выполнять по одному и проверять после каждого шага.

1) Dockerfile
   - Сделать многоступенчатый Dockerfile на базе официального `python:3.13-slim`. В первой стадии устанавливаем зависимости (`pip install -r requirements.txt`) и выполняем проверки; в финальной — копируем только необходимые артефакты.
   - Запуск приложения должен происходить от незарезервированного non-root пользователя.
   - Включить HEALTHCHECK (простая команда, например `python -c "import sys; sys.exit(0)"` или endpoint, если появится HTTP-сервер).

2) .dockerignore
   - Исключить ненужные файлы/папки: `.venv`, `.env`, `tokens.json`, `data.db`, `/downloads`, `.git`, `__pycache__`, `.pytest_cache`.

3) Переменные окружения и секреты
   - Использовать `.env` локально (включён в .gitignore). Для продакшна — секретный стор (Vault, GitHub Secrets, окружение CI/CD).
   - Явно документировать в README какие переменные обязательны (GARMIN_EMAIL, GARMIN_PASSWORD, OSM_CLIENT_ID/SECRET, REDIRECT_URI и т.д.).

4) Docker Compose для локальной разработки
   - Добавить `docker-compose.yml` с сервисом `app`. Примерно:
     - монтировать код в контейнер (bind mount) для быстрой разработки;
     - пробрасывать `.env` через `env_file`;

5) Миграции и стартовое поведение
   - На старте контейнера запускать минимальную инициализацию: проверка/создание таблиц (скрипт или Alembic), миграции и т.д. Это можно выполнить в entrypoint (или job в Compose).

6) Логи и мониторинг
   - Логировать в stdout/stderr (стандарт для контейнеров). Не писать логи только в файлы внутри образа.
   - (Опционально) добавить простой health endpoint или экспортировать метрики.

7) CI / автоматическая сборка образа
   - Добавить workflow (например GitHub Actions) который на push в ветку master:
     - собирает образ, запускает быстрые тесты/линтеры, запускает контейнер в smoke-test и удаляет артефакты.
   - Тегирование образов по SHA и по релизам.

8) Безопасность и оптимизации
   - Минимизировать размер образа (multi-stage, slim-базовый образ).
   - Не хранить секреты в образе и в репозитории.
   - Запуск от non-root пользователя, минимальные права на файловой системе.

9) Проверки и запуск
    - Локальная сборка: `docker build -t garmin_to_osm_sync:local .`
    - Локальный запуск: `docker run --rm --env-file .env -v $(pwd)/downloads:/app/downloads garmin_to_osm_sync:local`
    - Compose запуск: `docker-compose up --build`

10) Тестирование и отладка
    - Добавить unit/smoke тест, которые можно запускать в CI внутри контейнера.
    - Документировать debug-советы: как войти в контейнер (`docker run -it --entrypoint /bin/bash ...`), где искать логи и как временно монтировать `tokens.json`.

11) Документация в README
    - Включить в README краткие команды сборки/запуска, список обязательных переменных окружения и рекомендации по prod-деплою.

Если хотите, могу: создать пример `Dockerfile`, `.dockerignore` и `docker-compose.yml`, добавить пример GitHub Actions workflow и протестировать локальную сборку — скажите, какие из пунктов выполнить первые.
