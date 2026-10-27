# Образ grafana стенда (спека #40, 3.5): Grafana, источник данных Prometheus и дашборд
# «Штиль: быстродействие». Контекст — корень репозитория:
#   docker buildx build -f infra/grafana/Dockerfile .
# Данные (пользователи, сессии) — /var/lib/grafana, на стенде это том grafana_data.
# Настройка и дашборд лежат вне тома: откат образа откатывает и их.

FROM grafana/grafana:13.2.3
LABEL org.opencontainers.image.source="https://github.com/f-normies/shtil" \
      org.opencontainers.image.description="Клинический компас: Grafana стенда"

COPY infra/grafana/grafana.ini /etc/grafana/grafana.ini
COPY infra/grafana/provisioning/datasources/prometheus.yaml /etc/grafana/provisioning/datasources/
COPY infra/grafana/provisioning/dashboards/compass.yaml /etc/grafana/provisioning/dashboards/
COPY infra/grafana/dashboards /etc/grafana/dashboards
