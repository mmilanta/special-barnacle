FROM python:3.12-slim-bullseye

ENV APP_DIR=/src
WORKDIR ${APP_DIR}

COPY requirements.txt ${APP_DIR}/requirements.txt

RUN apt-get update -y
RUN apt-get install -y --no-install-recommends nginx curl vim git
RUN pip install -r requirements.txt

COPY src ${APP_DIR}

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
