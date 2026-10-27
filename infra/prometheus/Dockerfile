# Образ prometheus стенда (спека #40, 3.5): Prometheus и prometheus.yml.
# Контекст — корень репозитория: docker buildx build -f infra/prometheus/Dockerfile .
# Данные — /prometheus, на стенде это том prometheus_data. Хранение — в prometheus.yml.

FROM prom/prometheus:v3.15.0
LABEL org.opencontainers.image.source="https://github.com/f-normies/shtil" \
      org.opencontainers.image.description="Клинический компас: Prometheus стенда"

COPY infra/prometheus/prometheus.yml /etc/prometheus/prometheus.yml
# Ошибка в prometheus.yml останавливает сборку, до стенда она не доходит.
RUN ["/bin/promtool", "check", "config", "/etc/prometheus/prometheus.yml"]
