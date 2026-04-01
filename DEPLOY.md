# Запуск бота в продакшене

Бот на **aiogram** с **long polling** (постоянное подключение к Telegram). Для продакшена нужен процесс, который работает 24/7 и перезапускается при сбоях. База **SQLite** (`data.db`) должна жить на постоянном диске (не в «эфемерной» файловой системе без тома).

Ниже — несколько рабочих схем; для вашего случая с грантом в **Yandex Cloud** чаще всего берут **виртуальную машину (Compute Cloud)**.

---

## Что подготовить заранее

1. **Токен бота** от [@BotFather](https://t.me/BotFather): переменная окружения `BOT_TOKEN`.
2. **Python 3.10+** на сервере (на VM или локально при сборке образа).
3. Репозиторий с кодом на GitHub/GitLab/Bitbucket или архив — чтобы склонировать на сервер.

Локальный тест перед выкладкой:

```bash
cd clan_march_bot
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
set BOT_TOKEN=ваш_токен   # Linux/macOS: export BOT_TOKEN=...
python bot.py
```

---

## Вариант 1: Yandex Cloud — виртуальная машина (рекомендуется для этого бота)

Подходит для **long polling** без переделки кода. Грант (в т.ч. ваши 10 000 ₽/мес.) можно тратить на **Compute Cloud**, диск и трафик — смотрите актуальные цены и лимиты в [документации Yandex Cloud](https://cloud.yandex.ru/docs/).

### 1.1 Создать ВМ

1. Консоль Yandex Cloud → **Compute Cloud** → **Виртуальные машины** → **Создать ВМ**.
2. Образ: **Ubuntu 22.04 LTS** (или другой актуальный LTS).
3. Платформа: минимально достаточная vCPU/RAM для одного Python-процесса (например, 2 vCPU / 2 GB RAM с запасом).
4. Диск: **SSD** или **HDD**, объём по желанию; данные `data.db` хранятся на этом диске.
5. Сеть: публичный IP (удобно для SSH). **Группы безопасности**: разрешить **входящий SSH (TCP 22)** с вашего IP; входящий трафик для работы бота не обязателен (исходящие HTTPS к `api.telegram.org` нужны — по умолчанию обычно разрешён).

### 1.2 Залить код и зависимости

По SSH:

```bash
sudo apt update && sudo apt install -y git python3 python3-venv
git clone <URL_вашего_репозитория> clan_march_bot
cd clan_march_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1.3 Токен и systemd (автозапуск и перезапуск)

Создайте файл с секретом (права только root):

```bash
sudo nano /etc/clan_march_bot.env
```

Содержимое:

```
BOT_TOKEN=ваш_токен_от_BotFather
```

```bash
sudo chmod 600 /etc/clan_march_bot.env
```

Юнит systemd `sudo nano /etc/systemd/system/clan-march-bot.service`:

```ini
[Unit]
Description=Clan March Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ваш_пользователь
WorkingDirectory=/home/ваш_пользователь/clan_march_bot
EnvironmentFile=/etc/clan_march_bot.env
ExecStart=/home/ваш_пользователь/clan_march_bot/.venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Замените `ваш_пользователь` и путь к каталогу на реальные. Затем:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now clan-march-bot.service
sudo systemctl status clan-march-bot.service
journalctl -u clan-march-bot.service -f
```

Обновление кода: `git pull`, при необходимости `pip install -r requirements.txt`, затем `sudo systemctl restart clan-march-bot.service`.

---

## Вариант 2: Yandex Cloud — Docker на той же ВМ

Если удобнее воспроизводимая среда: на ВМ ставите **Docker** и **Docker Compose**, кладёте рядом `Dockerfile` и при необходимости `docker-compose.yml`, монтируете **volume** для `data.db`, чтобы база не терялась при пересоздании контейнера.

Минимальная идея `Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

Сборка и запуск (пример):

```bash
docker build -t clan-march-bot .
docker run -d --name clan-bot --restart unless-stopped \
  -e BOT_TOKEN=ваш_токен \
  -v /путь/на/хосте/data:/app \
  clan-march-bot
```

Каталог на хосте `/путь/на/хосте/data` должен содержать или получать `data.db` (можно смонтировать только файл, если аккуратно с правами).

На Yandex это всё равно **та же ВМ**, просто другой способ упаковки процесса.

---

## Вариант 3: Другие облака и PaaS (кратко)

| Подход | Комментарий |
|--------|-------------|
| **Любой VPS** (Timeweb, Selectel, DigitalOcean и т.д.) | Тот же сценарий, что ВМ в Yandex: systemd + venv или Docker. |
| **Railway / Render / Fly.io** | Часто удобен деплой из Git; проверьте, что **диск персистентный** для SQLite или подключите внешнее хранилище. |
| **Домашний сервер / Raspberry Pi** | systemd как в варианте 1; нужен стабильный интернет; при динамическом IP — DDNS или туннель. |

---

## Про Yandex Cloud Serverless (функции / контейнеры) и этот бот

Текущий код использует **polling** (`start_polling`). **Cloud Functions** и короткоживущие контейнеры без постоянного процесса для такого режима не подходят.

Чтобы жить в «серверлесс» Yandex, нужно перейти на **webhook**: бот принимает HTTPS-запросы от Telegram на ваш URL (API Gateway + Cloud Function или контейнер за балансировщиком). Это отдельная доработка кода и настройка сертификата/домена. Для вашего бота проще и дешевле по сопровождению — **ВМ + systemd** (вариант 1).

---

## Чеклист продакшена

- [ ] `BOT_TOKEN` не в Git; на сервере — через `EnvironmentFile` или секреты облака.
- [ ] Сервис с **Restart=always** или политика restart в Docker.
- [ ] Регулярные **бэкапы** `data.db` (копия файла в Object Storage или другой бакет).
- [ ] При смене IP/серверов — перенос каталога с `data.db`.
- [ ] Следить за логами: `journalctl` или `docker logs`.

Если нужно, можно отдельно добавить в репозиторий примеры `Dockerfile` и `docker-compose.yml` под ваш путь деплоя.
