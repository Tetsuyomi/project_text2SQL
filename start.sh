#!/bin/bash
# Остановить и удалить существующие контейнеры
docker-compose down

# Запустить сборку и запуск новых контейнеров
docker-compose up --build -d