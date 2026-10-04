# The fleet agent (cloud/fleet/README.md). Build context: cloud/fleet.
#   docker build -t ghcr.io/tim4431/gamma-fleet:latest cloud/fleet
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY gammafleet/ ./gammafleet/
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "gammafleet.agent"]
