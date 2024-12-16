# YAC Backend

YAC (Yet Another Configurator) is a highly adjustable tool to modify YAML files
in a GIT repository via UI/API. It allows versatile permission definitions on
file and parameter/value level and generates a UI schema and a JSON schema to
display a form and validate the input.

## Configuration

Some basics like the repository are configured through environment variables.
All available environment variables are defined in `app/config.py` in
`Settings` (like `log_level` which maps to the env variable `YAC_LOG_LEVEL`).

In addition, you're free to set any `YAC_ENV__*` environment variable and
access it in the specs-file as `env.*` (e.g. `YAC_ENV__MY_VAR` -> `env.my_var`).

Everything else is configured through the specs-file (see `docs/specs/general.md`).
The specs-file is a YAML file where you can use jinja2-templating in values.

## Deployment

Prepare a minimal specs file and commit it into a git repository (you can also
mount it into the container as a file and point the env var `YAC_SPECS_FILE` to
it):

    cat <<__EOF__ > /path/to/repo/yac.yml
    ---
    types:
      - name: file
        title: File
        details:
          file: files/{name}.yml
        name_pattern: '^[a-z0-9]{1,10}$'
    roles:
      - all:file:all: "user.name == 'user1'"
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

This minimal specs-file will allow a local user `user1`  to add/change/delete
all YAML files in the repo in `files/`. The YAML files are allowed to have the
properties `my_message` (string) and `worked` (boolean).

With this specs-file, write access to a git repository and an Open ID Connect
auth provider, you can start the YAC container:
    
    docker run --rm --name yac -p 8080:80 \
        --env YAC_REPO__URL="https://user@pass:git.example.com/my/repo.git" \
        --env YAC_OIDC_URL="https://example.com/.well-known/openid-configuration" \
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
