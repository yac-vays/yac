# YAC Backend

YAC (Yet Another Configurator) is a highly adjustable tool to modify YAML files
in a GIT repository via UI/API. It allows versatile permission definitions on
file and parameter/value level and generates a UI schema and a JSON schema to
display a form and validate the input.

For the full documentation, see: https://yac-vays.github.io

## Configuration

The bulk of YAC's configuration lives in the specs file (a static YAML
mounted into the container — by default at `/yac.yml`). It defines the
repository plugin and its connection details, entity types, roles and
schemas, and supports Jinja2 templating in values. Environment variables
cover only host-level settings (logging, OIDC, custom `env.*` values);
see `app/config.py` (`Settings`) for the full list.

You're free to set any `YAC_ENV__*` environment variable and access it
in the specs-file as `env.*` (e.g. `YAC_ENV__MY_VAR` -> `env.my_var`).

Because the specs file is loaded once at process startup, any change to
it (including the repo connection config) requires a pod/container
restart.

## Deployment

Prepare a minimal specs file:

    cat <<__EOF__ > /path/to/yac.yml
    ---
    repo:
      plugin: git_direct
      connection:
        url: https://user:pass@git.example.com/my/repo.git
        branch: main
      details:
        file: "files/{{ name }}.yml"
    auth:
      oidc:
        url: https://example.com/.well-known/openid-configuration
        client_ids: [my-client-id]
    types:
      - name: file
        title: File
        name_pattern: '^[a-z0-9]{1,10}$'
    roles:
      - file:all:all: "user.name == 'user1'"
    schema:
      type: object
      properties:
        my_message:
          title: My Message
          vays_category: General
          type: string
          pattern: '^[a-zA-Z0-9 ]{1,100}$'
        worked:
          title: Did it work?
          vays_category: General
          type: boolean
          default: false
    __EOF__

This minimal specs-file will allow a local user `user1` to add/change/delete
all YAML files in the repo's `files/` directory. The YAML files are allowed
to have the properties `my_message` (string) and `worked` (boolean).

With this specs-file mounted into the container, you can start YAC:

    docker run --rm --name yac -p 8080:80 \
        -v /path/to/yac.yml:/yac.yml:ro \
        yacvays/yac:latest

You should be able to access the API and the documentation at:
http://localhost:8080/

### Images/Tags

The container images are available with the following tag schema:
https://hub.docker.com/r/yacvays/yac

  - *latest*: The latest stable release
  - *v1*, *v2*, ...: A specific major release (stable API)
  - *v1.0*, *v2.1*, ...: A specific minor version
  - *testing*: The latest testing release
  - *v2rc*, *v7rc*, ...: A specific major testing release
  - *v2.0rc*, *v3.11rc*, ...: A specific minor testing release

## Development

### Upgrade Environment

- Check on https://hub.docker.com/r/alpine/helm for new versions and adjust the
  tag in `.gitlab-ci.yml`.

- Check on https://hub.docker.com/_/python for new versions and adjust the tag
  in the `FROM` instruction of `./Dockerfile`. (Use a most specific tag to allow
  reproducable builds.)

- Build container (and update the requirements file) with:

      docker run --rm -v "$(pwd)/requirements.in:/r.in:ro" --entrypoint sh yac:latest -c \
          "pip install pip-tools &>/dev/null; pip-compile -o - /r.in" > ./requirements.txt

      docker build --progress plain -t yac .
