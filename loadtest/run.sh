#!/usr/bin/env bash
# Прогон Locust против локального стенда. Запуск из корня репозитория:
#   loadtest/run.sh <имя> <ступени> [процессы]
# Ступени — «пользователи:секунды» через запятую. Итоги — loadtest/results/<имя>/: CSV Locust,
# docker stats контейнеров стенда (stats.csv), соединения Postgres и очередь (db.csv).
# Нужны LOADTEST_PASSWORD в окружении и образ shtil-locust (docs/loadtest.md).
set -euo pipefail

name=${1:?имя прогона}
stages=${2:?ступени, например 100:120,200:120}
processes=${3:-2}
host=${LOADTEST_HOST:-http://localhost:${WEB_PORT:-8080}}
: "${LOADTEST_PASSWORD:?задайте LOADTEST_PASSWORD}"
root=$(pwd)
out=loadtest/results/$name
mkdir -p "$out"

# Ресурсы контейнеров стенда и генератора раз в ~3 с.
(
  echo "ts,name,cpu,mem"
  while true; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' \
      | grep -E '^shtil-' | sed "s/^/$(date +%s),/"
  done
) >"$out/stats.csv" 2>/dev/null &
stats_pid=$!

# Соединения Postgres и задачи очереди раз в 5 с.
(
  echo "ts,connections,active,queued"
  while true; do
    row=$(docker compose --env-file .env exec -T postgres psql -U compass -d compass -Atc \
      "select (select count(*) from pg_stat_activity where datname='compass'),
              (select count(*) from pg_stat_activity where datname='compass' and state='active'),
              (select count(*) from pgq.pgqueuer)" </dev/null | tr '|' ',')
    echo "$(date +%s),$row"
    sleep 5
  done
) >"$out/db.csv" 2>/dev/null &
db_pid=$!
trap 'kill $stats_pid $db_pid 2>/dev/null || true' EXIT

# LOADTEST_CPUSET — ядра генератора, например 6,7: так Locust не отнимает процессор у стенда.
docker run --rm --name shtil-locust --network host ${LOADTEST_CPUSET:+--cpuset-cpus "$LOADTEST_CPUSET"} \
  --user "$(id -u):$(id -g)" \
  -e LOADTEST_PASSWORD -e LOADTEST_STAGES="$stages" -e LOADTEST_PROCESSES="$processes" \
  -e LOADTEST_SPAWN_RATE="${LOADTEST_SPAWN_RATE:-10}" -e LOADTEST_ALERTS_CSV=/mnt/out/alerts.csv \
  -v "$root/loadtest:/mnt/locust" -v "$root/$out:/mnt/out" \
  -w /mnt/locust --entrypoint bash shtil-locust -c "
    # Locust 2.43.3 с --processes передаёт дочерним процессам --csv-full-history без --csv,
    # и они падают. Поэтому workers и master запускаются отдельно.
    locust -f locustfile.py --worker --processes $processes &
    locust -f locustfile.py --master --headless --expect-workers $processes --host $host \
      --csv /mnt/out/locust --csv-full-history --only-summary
    wait" </dev/null 2>&1 | grep -v -i warning | tail -60 | tee "$out/summary.txt"
