#
# Base Image
#

FROM python:3.14.7-alpine3.24 AS build

WORKDIR /code

COPY ./requirements.* ./

RUN apk add --update-cache curl git libldap openssh-client && \
    apk add --virtual build-deps build-base openldap-dev python3-dev libffi-dev yaml-dev && \
    pip install --no-cache-dir --upgrade --compile -r /code/requirements.txt && \
    apk del build-deps && \
    rm -rf /var/cache/apk/*

COPY ./app /code/app

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
