# Text-to-SQL Generator

Это веб-приложение, которое преобразует запросы на естественном языке в SQL-запросы 
для базы данных PostgreSQL. Приложение использует проект LLM7, предоставляющий бесплатные 
токены для различных моделей, чтобы генерировать SQL на основе загруженной схемы базы данных
в формате JSON. Сгенерированные SQL-запросы автоматически отправляются на
API `http://yours-servece` для выполнения с до 10 попытками в случае ошибок.

## Описание проекта

Text-to-SQL Generator позволяет пользователям:
- Загружать схему базы данных в формате JSON через веб-интерфейс.
- Вводить запросы на естественном языке (например, «Найти количество аварий по районам»).
- Получать сгенерированный SQL-запрос, совместимый с PostgreSQL.
- Просматривать результат выполнения запроса через API в интерфейсе.

Приложение построено с использованием Flask и интегрируется с API LLM7 для генерации SQL-запросов.

## Требования

- Python 3.11
- Docker и Docker Compose (для развертывания в контейнере)
- Poetry (для управления зависимостями)
- Веб-браузер (для доступа к интерфейсу)
- Доступ к API `http://yours-servece`

## Установка

### 1. Клонирование репозитория
```bash
git clone <repository_url>
cd project_text2SQL
```

### 2. Установка зависимостей
Используйте Poetry для установки зависимостей:
```bash
poetry install
```

### 3. Настройка переменных окружения
Можно оставить `"unused"` для API-ключа LLM7 или получить токен на сайте проекта для более крупных запросов. Замените `"unused"` в `app/main.py` на ваш ключ при необходимости:
```python
client = OpenAI(
    base_url="https://api.llm7.io/v1",
    api_key="unused"  # Получите ключ на https://token.llm7.io/
)
```

### 4. Сборка и запуск с Docker Compose
1. Соберите и запустите сервис:
   ```bash
   docker-compose up --build -d
   ```
2. Проверьте логи:
   ```bash
   docker-compose logs
   ```

### 5. Локальный запуск (без Docker)
Если вы хотите запустить приложение локально:
```bash
poetry run python app/main.py
```

## Использование

1. Откройте веб-браузер и перейдите по адресу: `http://localhost:5000`.
2. Загрузите JSON-файл со схемой базы данных. Пример схемы:
   ```json
   {
     "tables": {
       "accidents": {
         "columns": {
           "accident_number": "TEXT",
           "died": "INTEGER",
           "wounded": "INTEGER",
           "children_died": "INTEGER",
           "children_wounded": "INTEGER",
           "district_name": "TEXT"
         }
       },
       "cars": {
         "columns": {
           "accident_number": "TEXT",
           "car_number": "TEXT",
           "brand": "TEXT",
           "color": "TEXT"
         }
       },
       "participants": {
         "columns": {
           "car_number": "TEXT",
           "gender": "TEXT",
           "participant_category": "TEXT",
           "driving_experience": "INTEGER",
           "child_restraint_type": "TEXT",
           "seatbelt_used": "TEXT"
         }
       }
     }
   }
   ```
3. Введите запрос на естественном языке, например:
   - «Найти количество аварий по районам».
4. Нажмите «Generate SQL» и получите результат:
   - Сгенерированный SQL-запрос, например:
     ```sql
     SELECT district_name, COUNT(accident_number) FROM accidents GROUP BY district_name;
     ```
   - Ответ от API, например:
     ```json
     {
       "status": "success",
       "api_response": {...}
     }
     ```
   Если API недоступен, приложение попытается отправить запрос до 10 раз с интервалом 2 секунды.

## Структура проекта

```
project_text2SQL/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── poetry.lock
├── .venv/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── templates/
│   │   └── index.html
│   └── static/
│       └── style.css
└── README.md
```

- **Dockerfile**: Конфигурация для сборки Docker-образа.
- **docker-compose.yml**: Конфигурация для запуска контейнера.
- **pyproject.toml**, **poetry.lock**: Управление зависимостями через Poetry.
- **app/main.py**: Основной файл Flask-приложения.
- **app/templates/index.html**: HTML-шаблон для веб-интерфейса.
- **app/static/style.css**: Стили для интерфейса.
- **.venv/**: Виртуальная среда Poetry (игнорируется в Git).

## Генерация JSON-схемы

Чтобы создать JSON-схему для вашей базы данных PostgreSQL:
1. Используйте следующий Python-скрипт (`generate_schema.py`):
   ```python
   import psycopg2
   import json

   def get_db_schema(db_params):
       conn = psycopg2.connect(**db_params)
       cursor = conn.cursor()
       cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
       tables = [row[0] for row in cursor.fetchall()]
       schema = {"tables": {}}
       for table in tables:
           cursor.execute("""
               SELECT column_name, data_type
               FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = %s;
           """, (table,))
           columns = cursor.fetchall()
           schema["tables"][table] = {
               "columns": {
                   col[0]: col[1].upper() for col in columns
               }
           }
       conn.close()
       return schema

   def save_schema_to_json(schema, output_file):
       with open(output_file, 'w', encoding='utf-8') as f:
           json.dump(schema, f, indent=2, ensure_ascii=False)

   if __name__ == "__main__":
       db_params = {
           "dbname": "your_database_name",
           "user": "your_username",
           "password": "your_password",
           "host": "localhost",
           "port": "5432"
       }
       output_file = "schema.json"
       schema = get_db_schema(db_params)
       save_schema_to_json(schema, output_file)
       print(f"Schema saved to {output_file}")
   ```
2. Установите `psycopg2`:
   ```bash
   pip install psycopg2-binary
   ```
3. Укажите параметры подключения к вашей базе данных PostgreSQL (`dbname`, `user`, `password`, `host`, `port`) и запустите:
   ```bash
   python generate_schema.py
   ```
4. Используйте сгенерированный `schema.json` в приложении.

## Устранение неполадок

- **Ошибка `ModuleNotFoundError: No module named 'flask'`**:
  Убедитесь, что зависимости установлены:
  ```bash
  poetry install
  ```
  Проверьте виртуальную среду в Docker:
  ```bash
  docker exec -it text2sql_app /app/.venv/bin/pip list
  ```

- **Ошибка `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`**:
  Используйте версию `openai==1.30.0` или добавьте `http_client` в `main.py`:
  ```python
  client = OpenAI(
      base_url="https://api.llm7.io/v1",
      api_key="unused",
      http_client=httpx.Client()
  )
  ```
  Добавьте зависимость `httpx`:
  ```toml
  httpx = "^0.27.0"
  ```

- **Ошибка подключения к API**:
  Проверьте доступность `http://yours-servece`:
  ```bash
  docker exec -it text2sql_app curl http://yours-servece
  ```
  Если API недоступен, добавьте в `docker-compose.yml`:
  ```yaml
  network_mode: host
  ```

- **WSL-проблемы**:
  Убедитесь, что файлы имеют правильные права:
  ```bash
  chmod -R u+rw .
  ```

## Зависимости

- Flask (^3.0.3): Веб-фреймворк для приложения.
- OpenAI (^1.30.0): Клиент для API LLM7.
- Requests (^2.32.0): Для отправки HTTP-запросов к API.
- Tenacity (^8.5.0): Для повторных попыток отправки запросов.
- httpx (^0.27.0): HTTP-клиент для OpenAI (при необходимости).
