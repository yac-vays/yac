# Section `types`

A type defines everything related to the different YAML file types that YAC
should manage. Only manage similar types in one YAC instance. If the schemas
are too different, better run a second instance.

## Defaults

    - name: # mandatory
      title: # mandatory
      details: {} # repo_plugin specific configuration
        # for git_repo:
        file: # mandatory, path relative to the repo dir, with `name` as format-string
      name_pattern: '^[a-zA-Z0-9_-\.]+$'
      name_example: ''
      name_generator: # jinja2 string to generate the name on create
      name_generated: never # or optional or enforced
      description: '' # Used in UI (with markdown formatting)
      create: true # enable create functions for entities of this type
      delete: true # enable delete functions for entities of this type
      options: [] # definition of values displayed in the entity list (GET /entity/{type})
        - name: # mandatory
          title: ''
          default: null
          aliases: {} # key is the source-value and value is the alias for it (with markdown formatting)
      favorites: [] # actions or operations displayed in the UI
        - name: '' # either a custom defined action or one of: create_copy, create_link, change, delete
          action: false # if it is a custom action or one of the standard operations
      logs: []
        - name: # mandatory
          title: # mandatory
          progress: false # contains progress information
          problem: false # contains status information (if there is problem or not)
          plugin: # mandatory, one of: elastic, file
          details: {} # log_plugin specific configuration
            # for elastic:
            url: # mandatory, with `name` and any var as format-string
            ssl_verify: true
            query: # mandatory, with `name` and any var as format-string
            message: '' # jinja2 string with `log` as additional var containing
                        # all data from the elastic server for each entry
            time: 'log["@timestamp"]' # jinja2 string with `log` var
            progress: 0 # jinja2 int with `log` var
            problem: false # jinja2 bool with `log` var

            # for file:
            path: # mandatory, with `name` and any var as format-string
            line_format: # mandatory, format-string where you define the vars 
            time: '' # jinja2 string with `log` containing your vars
            message: '' # jinja2 string with `log` containing your vars
            progress: 0 # jinja2 int with `log` containing your vars
            problem: false # jinja2 bool with `log` containing your vars
      actions: []
        - name: # mandatory
          title: # mandatory
          description: '' # Used in UI if dangerous (with markdown formatting)
          icon: '' # an SVG button icon
          dangerous: false # display a confirmation box with description
          perms: [act] # required user perms on entity to run this action
          force: false # run automatically & silently on hooked operations, otherwise
                       # it's optional
          hooks: [] # any of: arbitrary, create:before, create:after, change:before,
                    # change:after, delete:before, delete:after
          plugin: # mandatory, one of: http, shell
          details: {} # log_plugin specific configuration
            # for http
            method: GET
            url: # mandatory
            body: ''
            headers: {}
            success: [200, 201, 202, 203, 204, 205, 206, 207, 208, 226]
            error: [] # list of HTTP status codes to return the response body
                      # to the user as error message (instead of just 500)

            # for shell
            command: # mandatory
            success: [0] # return code
            error: [] # to return the stdout/stderr to the user as error message

## Example

    types:
      - name: animal
        title: Animal
        details:
          file: animals/{name}.yml
        options:
          - name: color
            title: Color
            default: grey
            aliases:
              grey: Grey
              brown: Brown
              red: Red
        favorites:
          - name: flush
            action: true
          - name: delete
        logs:
          - name: camera-trap
            title: Camera Trap
            progress: true
            plugin: elastic
            details:
              url: https://user:{{ env.elastic_pass }}@elastic.example.com:9200/camera-trap
              query: 'any where animal.name == "{name}"'
              message: '"![image](%s)" | format(animal.foto_url)'
              progress: 'animal.count * 100 / animal.total'
          - name: camera-status
            title: Camera Status
            problem: true
            plugin: file
            details:
              path: "/logs/camera/{name}.log"
              line_format: "[{time}] {message}"
              time: "log.time"
              message: "log.message"
              problem: "log.message is regex_match('.*failed.*')"
        actions:
          - name: flush
            title: Flush Fotos
            description: Flush all photos from the [Gallery]({{ env.photo_gallery_url }})
            dangerous: true
            perms: [act, admin]
            force: false
            hooks:
              - arbitrary
              - delete:after
            plugin: http
            details:
              method: POST
              url: "https://photos.example.com/api/{name}/?secret={{ env.photo_secret }}"
              body: '{"action": "flush", "animal": "{{ name }}"}'
              headers:
                Content-Type: application/json
              error: [409]
          - name: hello
            title: Say Hello
            icon: <svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#e8eaed"><path d="M320-120v-80H160q-33 0-56.5-23.5T80-280v-480q0-33 23.5-56.5T160-840h320v80H160v480h640v-120h80v120q0 33-23.5 56.5T800-200H640v80H320Zm360-280L480-600l56-56 104 103v-287h80v287l104-103 56 56-200 200Z"/></svg>
            hooks:
              - create:before
            plugin: shell
            details:
              command: 'echo "Hello $YAC__USER__FULL_NAME"'
