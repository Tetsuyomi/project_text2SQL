# Text-to-SQL Generator

Это веб-приложение, которое преобразует запросы на естественном языке в SQL-запросы для базы данных PostgreSQL. Приложение использует LLM7o - проект пердоставляющий беплатные токены для различных моделей, чтобы генерировать SQL на основе загруженной схемы базы данных в формате JSON.

## Описание проекта

Text-to-SQL Generator позволяет пользователям:
- Загружать схему базы данных в формате JSON через веб-интерфейс.
- Вводить запросы на естественном языке (например, «Найти всех клиентов старше 25 лет»).
- Получать сгенерированный SQL-запрос, совместимый с PostgreSQL.

Приложение построено с использованием Flask и интегрируется с API LLM7 для обработки запросов.

## Требования

- Python 3.11
- Docker (для развертывания в контейнере)
- Poetry (для управления зависимостями)
- Веб-браузер (для доступа к интерфейсу)

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
Можно оставить "unused", или перейти на сайт проекта LLM7o и получить токен для более крупных запросов. Замените `"unused"` в `app/main.py` на ваш ключ при необходимости:
```python
client = OpenAI(
    base_url="https://api.llm7.io/v1",
    api_key="unused"  # Получите ключ на https://token.llm7.io/
)
```

### 4. Сборка и запуск с Docker
1. Соберите Docker-образ:
   ```bash
   docker build --no-cache -t text2sql .
   ```
2. Запустите контейнер:
   ```bash
   docker run -p 5000:5000 text2sql
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
       "customers": {
         "columns": {
           "id": "INTEGER PRIMARY KEY",
           "name": "TEXT",
           "age": "INTEGER"
         }
       }
     }
   }
   ```
3. Введите запрос на естественном языке, например:
   - «Найти всех клиентов старше 25 лет».
4. Нажмите «Generate SQL» и получите результат, например:
   ```sql
   SELECT * FROM customers WHERE age > 25;
   ```

## Структура проекта

```
project_text2SQL/
├── Dockerfile
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
- **pyproject.toml**, **poetry.lock**: Управление зависимостями через Poetry.
- **app/main.py**: Основной файл Flask-приложения.
- **app/templates/index.html**: HTML-шаблон для веб-интерфейса.
- **app/static/style.css**: Стили для интерфейса.
- **.venv/**: Виртуальная среда Poetry (игнорируется в Git).

## Генерация JSON-схемы

Чтобы создать JSON-схему для вашей базы данных SQLite:
1. Используйте следующий Python-скрипт (`generate_schema.py`):
   ```python
   import sqlite3
   import json

   def get_db_schema(db_path):
       conn = sqlite3.connect(db_path)
       cursor = conn.cursor()
       cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
       tables = [row[0] for row in cursor.fetchall()]
       schema = {"tables": {}}
       for table in tables:
           cursor.execute(f"PRAGMA table_info({table});")
           columns = cursor.fetchall()
           schema["tables"][table] = {
               "columns": {col[1]: col[2] for col in columns}
           }
       conn.close()
       return schema

   def save_schema_to_json(schema, output_file):
       with open(output_file, 'w', encoding='utf-8') as f:
           json.dump(schema, f, indent=2, ensure_ascii=False)

   if __name__ == "__main__":
       db_path = "path/to/your/database.db"
       output_file = "schema.json"
       schema = get_db_schema(db_path)
       save_schema_to_json(schema, output_file)
       print(f"Schema saved to {output_file}")
   ```
2. Укажите путь к вашей базе данных PostgreSQL и запустите:
   ```bash
   python generate_schema.py
   ```
3. Используйте сгенерированный `schema.json` в приложении.

## Устранение неполадок

- **Ошибка `ModuleNotFoundError: No module named 'flask'`**:
  Убедитесь, что зависимости установлены:
  ```bash
  poetry install
  ```
  Проверьте виртуальную среду в Docker:
  ```bash
  docker run -it text2sql /bin/bash
  /app/.venv/bin/pip list
  ```

- **Ошибка `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`**:
  Понизьте версию `openai` в `pyproject.toml`:
  ```toml
  openai = "^1.30.0"
  ```
  Или добавьте `http_client` в `main.py`:
  ```python
  client = OpenAI(
      base_url="https://api.llm7.io/v1",
      api_key="unused",
      http_client=httpx.Client()
  )
  ```

- **WSL-проблемы**:
  Убедитесь, что файлы имеют правильные права:
  ```bash
  chmod -R u+rw .
  ```

## Зависимости

- Flask (^3.0.3): Веб-фреймворк для приложения.
- OpenAI (^1.30.0): Клиент для API LLM7.
- httpx (^0.27.0): HTTP-клиент для OpenAI (при необходимости).
