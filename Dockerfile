FROM python:3.11-slim

WORKDIR /app

# Установка curl и poetry
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir poetry==1.8.3

# Копирование файлов конфигурации Poetry
COPY pyproject.toml .
COPY poetry.lock .

# Установка зависимостей в виртуальную среду
RUN poetry config virtualenvs.in-project true && \
    poetry install --no-root --no-interaction --no-ansi --no-cache

# Копирование приложения
COPY app ./app

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5000/ || exit 1

# Запуск приложения
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000"]