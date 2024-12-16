#
# Base Image
#

FROM python:3.12.6-alpine3.20 AS build

WORKDIR /code

COPY ./requirements.* ./

RUN apk add --update-cache curl git libldap openssh-client && \
    apk add --virtual build-deps build-base openldap-dev python3-dev libffi-dev && \
    pip install --no-cache-dir --upgrade --compile -r /code/requirements.txt && \
    apk del build-deps && \
    rm -rf /var/cache/apk/*

COPY ./app /code/app

#
# Linting/Testing
#

FROM build AS test

# TODO enable pylint and enforce by removing --exit-zero
RUN pip install --no-cache-dir --upgrade --compile pylint
RUN pylint --rcfile /code/app/.pylintrc --exit-zero /code/app /code/app/plugin/*/*.py
COPY ./tests /code/tests
RUN PYTHONPATH=/code python /code/tests/main.py
RUN touch /tmp/tested

#
# Final Image
#

FROM build AS production

# Enforce test run
COPY --from=test /tmp/tested /dev/null

ARG version=v0
RUN echo 'VERSION = "'${version#v}'"' > /code/app/version.py

EXPOSE 80
ENTRYPOINT ["uvicorn", "--log-config", "app/uvicorn.yml", "--host", "0.0.0.0", "--port", "80", "app.main:app"]
