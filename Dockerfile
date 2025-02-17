FROM python:3.12-slim-bullseye

ENV APP_DIR=/src
WORKDIR ${APP_DIR}

COPY pyproject.toml ${APP_DIR}/pyproject.toml

RUN apt-get update -y
RUN apt-get install -y --no-install-recommends curl vim git
RUN apt-get install -y --no-install-recommends curl vim git openssl ca-certificates
RUN git config --global http.sslBackend openssl
RUN git config --global http.sslVerify true
RUN pip install .

EXPOSE 8080

COPY src ${APP_DIR}

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
