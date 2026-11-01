FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY reiatsu reiatsu
RUN pip install --no-cache-dir .
COPY sim sim
COPY examples examples

# Mount your floor plan at /config/plan.yaml; MQTT settings via REIATSU_MQTT_* env.
# The simulator still ships: docker run --rm IMAGE python -m sim.run
ENV REIATSU_PLAN=/config/plan.yaml
CMD ["reiatsu"]
