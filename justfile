build:
    docker build . -t special-bernacle
run:
    docker run -p 8080:8080  --env-file .env -e PRIVATE_KEY="$(cat keys/id_rsa | base64)" special-bernacle