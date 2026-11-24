#
# Base Image
#

FROM python:3.15.2-alpine3.24 AS build

WORKDIR /code

COPY ./requirements.* ./

RUN apk add --update-cache curl git libldap openssh-client && \
    apk add --virtual build-deps build-base cmake openldap-dev python3-dev libffi-dev yaml-dev && \
    pip install --no-cache-dir --upgrade --compile -r /code/requirements.txt && \
    apk del build-deps && \
    rm -rf /var/cache/apk/*

COPY ./app /code/app

# Swagger UI for the API docs page, served by YAC itself instead of from a
# public CDN (see app/lib/docs.py). Pinned and verified: to update, set the
# version and the sha512 of its npm tarball
# (`npm view swagger-ui-dist@<version> dist.integrity`, base64 -> hex, or
# `sha512sum` of the downloaded .tgz).
ARG SWAGGER_UI_VERSION=5.36.0
ARG SWAGGER_UI_SHA512=2edebf29bd205e62dd89a6cb86fb28e157493fd57ba89581d33874833797515dfe08c393ccc9afa494525cdec40237f08b1c26a22f5ecc36cb1e27b10bb940f4
RUN curl -fsSL -o /tmp/swagger-ui.tgz \
        "https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-${SWAGGER_UI_VERSION}.tgz" && \
    echo "${SWAGGER_UI_SHA512}  /tmp/swagger-ui.tgz" | sha512sum -c - && \
    mkdir -p /code/static/swagger-ui && \
    tar -xzf /tmp/swagger-ui.tgz -C /code/static/swagger-ui --strip-components=1 \
        package/swagger-ui-bundle.js package/swagger-ui.css \
        package/favicon-32x32.png package/LICENSE package/NOTICE && \
    rm /tmp/swagger-ui.tgz

#
# Linting/Testing
#

FROM build AS test

RUN pip install --no-cache-dir --upgrade --compile pylint pytest pytest-asyncio
RUN pylint --rcfile /code/app/.pylintrc --exit-zero /code/app /code/app/plugin/*/*.py
COPY ./tests /code/tests
RUN PYTHONPATH=/code python -m pytest /code/tests
RUN touch /tmp/tested

#
# Final Image
#

FROM build AS production

# Enforce test run
COPY --from=test /tmp/tested /dev/null

ARG version=v0.0
RUN echo 'VERSION = "'${version#v}'"' > /code/app/version.py

# Create non-root user (UID/GID 1000); the home dir holds the default SSH
# key/known_hosts location and is the only writable path inside the image
# (besides the /repo and /tmp volumes the user provides at runtime).
RUN addgroup -g 1000 yac && \
    adduser -D -u 1000 -G yac -h /home/yac yac && \
    mkdir -p /home/yac/.ssh && \
    chown -R yac:yac /home/yac /code

USER 1000

EXPOSE 8080
ENTRYPOINT ["uvicorn", "--log-config", "app/uvicorn.yml", "--host", "0.0.0.0", "--port", "8080", "app.main:yac"]
