# Нагрузочный тест

Locust гоняет врачей и пациентов против локального стенда и пишет, как растёт время ответа с числом пользователей. Модели для теста не нужны: сценарии бьют по API, базе и WebSocket.

## Что делают пользователи

- Пациент: главный экран, дневник настроения, PHQ-9 и GAD-7, чат.
- Врач: список пациентов, алерты, карточка пациента (14 запросов), ответ в чате.
- Каждый держит WebSocket. Врачей и пациентов 1 к 20. Пауза между действиями 5–15 секунд.
- Отдельный замер алерта: раз в 10 секунд пациент отправляет PHQ-9 с пунктом 9 = 1, и мы меряем время до события `alert.created` у врача. В отчёте это строка `WS alert.created`.

Учётные записи — в `loadtest/accounts.py`: 50 врачей `lt-doctor-NN`, у каждого 20 пациентов `lt-pNN-MM`, плюс пара для замера алерта. Пароль у всех один — `LOADTEST_PASSWORD`.

## Подготовка

Стенд уже поднят (`make stand-up`). Команды — из корня репозитория.

```bash
export LOADTEST_PASSWORD=$(openssl rand -hex 12)
docker build -t shtil-locust loadtest
```

Врачи. API для создания врача нет, поэтому скрипт выполняется внутри api:

```bash
docker compose exec -T -e LOADTEST_PASSWORD api python - < loadtest/doctors.py
```

Пациенты и пара для замера, через API:

```bash
docker run --rm --network host -e LOADTEST_PASSWORD -v $PWD/loadtest:/mnt/locust -w /mnt/locust \
  --entrypoint python shtil-locust prepare.py --host http://localhost:8080
```

Окно дневника у врача — 30 дней. Свежие пациенты в него не попадают, и API отвечает 422. Поэтому сдвигаем начало наблюдения на 60 дней назад:

```bash
docker compose exec -T postgres psql -U compass -d compass -c "update patients.patients p
  set created_at = created_at - interval '60 days' from identity.users u
  where u.id = p.user_id and u.email like 'lt-%'"
```

## Прогон

Все виртуальные пользователи приходят с одного IP. Неудачные входы с него быстро упираются в лимиты, поэтому перед каждым прогоном счётчики сбрасываются:

```bash
docker compose exec -T postgres psql -U compass -d compass -c "truncate identity.throttle_events"
loadtest/run.sh step 100:90,200:90,300:90,400:90,500:90,600:90,800:90,1000:90
```

Аргументы: имя прогона, ступени «пользователи:секунды», число процессов Locust (по умолчанию 2). Если генератор и стенд на одной машине, отдайте Locust отдельные ядра, например `LOADTEST_CPUSET=6,7`.

Результаты — в `loadtest/results/<имя>/`:

- `locust_stats_history.csv` — посекундно по каждому запросу;
- `stats.csv` — CPU и память контейнеров;
- `db.csv` — соединения Postgres и длина очереди;
- `alerts.csv` — время доставки каждого алерта замера.

Таблица по ступеням и график:

```bash
uv run --with matplotlib python loadtest/report.py --out loadtest/results/report \
  --run "1 реплика=loadtest/results/step=100:90,200:90,300:90,400:90,500:90,600:90,800:90,1000:90"
```

## Масштабирование

api и worker не хранят состояние, их можно размножить:

```bash
docker compose up -d --scale api=2 --scale worker=2
```

Prometheus сам находит все реплики. В Grafana видно время ответа по процентилям и нагрузку по репликам.

## Уборка

Учётные записи `lt-*` остаются в базе. Чтобы начать с чистого листа, удалите тома: `docker compose down -v`.
