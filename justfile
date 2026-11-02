build:
    docker build . -t special-bernacle
run:
    docker run -p 8080:8080 --env-file .env special-bernacle
