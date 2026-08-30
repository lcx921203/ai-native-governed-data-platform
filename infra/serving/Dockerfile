FROM python:3.12-slim

WORKDIR /opt/project

COPY requirements-serving.txt /tmp/requirements-serving.txt
RUN pip install --no-cache-dir -r /tmp/requirements-serving.txt

COPY serving /opt/project/serving

ENV PYTHONUNBUFFERED=1
EXPOSE 8081

CMD ["uvicorn", "serving.api.main:app", "--host", "0.0.0.0", "--port", "8081"]
