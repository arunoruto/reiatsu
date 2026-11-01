FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY reiatsu reiatsu
RUN pip install --no-cache-dir .
COPY sim sim
COPY examples examples

# Mount your floor plan at /config/plan.yaml; MQTT settings via REIATSU_MQTT_* env.
# The simulator still ships: docker run --rm IMAGE python -m sim.run
# Web UI (plan editor, live map) on 8099, no auth: keep it on the LAN or behind HA ingress.
ENV REIATSU_PLAN=/config/plan.yaml
EXPOSE 8099
CMD ["reiatsu"]
