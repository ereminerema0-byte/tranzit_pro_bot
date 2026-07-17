# Настройка переменных окружения

## Обязательные переменные

- `BOT_TOKEN` — токен вашего Telegram-бота

## Рекомендуемые переменные

- `CONTACT_MODE` — режим контакта в канале: `user` | `hub` | `hybrid` (по умолчанию `hybrid`)
  - `user` — контакт автора, `CONTACT_USERNAME` только как fallback
  - `hub` — всегда контакт биржи с пометкой «биржа»
  - `hybrid` — контакт автора + контакт биржи
- `CONTACT_USERNAME` — контакт биржи/хаба (для `hub` и `hybrid`)
- `ADMIN_ID` — ваш Telegram ID
- `CHANNEL_ID` — ID канала для публикации (начинается на -100...)
- `DATABASE_PATH` — путь к базе данных (по умолчанию `bot.db`)

## Рекомендуемые значения

CONTACT_MODE=hybrid
CONTACT_USERNAME=@your_hub_username
DATABASE_PATH=bot.db

**Как настроить:**
1. Скопируйте `.env.example` → `.env`
2. Заполните значения в `.env`
3. Добавьте переменные в Railway (Variables)
4. **Никогда** не коммитьте `.env` в git
