FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY reiatsu reiatsu
RUN pip install --no-cache-dir .
COPY sim sim
COPY examples examples

# Phase 1 has no service yet: the image runs the simulator.
CMD ["python", "-m", "sim.run", "--scenario", "default"]
