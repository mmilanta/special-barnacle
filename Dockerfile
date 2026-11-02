FROM python:3.12-slim-bullseye

ENV APP_DIR=/src
WORKDIR ${APP_DIR}

COPY pyproject.toml ${APP_DIR}/pyproject.toml

RUN pip install .

EXPOSE 8080

COPY src ${APP_DIR}

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
