from urllib.parse import urlparse

from app.config import Settings

ENV = Settings()

TITLE = "YAC - Yet Another Configurator"
DESCRIPTION = f"""

## Authentication

We are using the OpenID Connect provider
[{urlparse(ENV.oidc_url).netloc}]({ENV.oidc_url})
for authentication. You need to send a valid `id_token` via `Authorization: Bearer`
header to all API endpoints that require authentication.

Only the following `client_id`s are accepted:
`{'` `'.join(ENV.oidc_client_ids.split(","))}`

For interactive user logins, `authentication_code with PKCE` flow (with a
dummy `nonce` parameter that won't be validated) is recommended. For
automated login in scripts/software, use the `password` flow instead (therefore
you will also need the `client_secret`).

## Source and Issues

Repository on [GitLab](https://gitlab.inf.ethz.ch/public-isg/yac-backend)
"""
CONTACT = {
    "name": "Manuel (isginf)",
    "email": "manuel.maestinger@inf.ethz.ch",
}
LICENSE = {
    "name": "GNU GPLv3",
    "url": "https://www.gnu.org/licenses/gpl-3.0.html",
}

# Must not allow / to avoid non-permitted file access!
NAME_PATTERN = r"^[a-zA-Z0-9_\-\.]{1,200}$"
TYPE_PATTERN = r"^[a-zA-Z0-9_\-\.]{1,200}$"
ACTION_PATTERN = r"^[a-zA-Z0-9_\-\.]{1,200}$"
SEARCH_PATTERN = r"^[a-zA-Z0-9_\-\. ]{0,200}$"
MSG_PATTERN = r"^[a-zäöüA-ZÄÖÜ0-9 @\(\)\"\.,_\/\-\r\n]{0,10000}$"

DESC_HASH = """
The hash represents the state of the data repository. So as long as this stays
the same, all the entity data did not change. (Note that this does not apply to
logs and might also not apply to type specifications, depending on whether they
are stored inside the data repository or not!)
"""

DESC_NAME_GENERATED = """
Defines if the entity name is generated on the server side:

  - `never` means every create request must contain a valid name
  - `optional` means requests can contain a name, if not (`null`),
    a name is generated instead
  - `enforced` means the name must be empty (`null`) on create requests
    and is always generated
"""

SVG_ACTION = (
    '<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 '
    '-960 960 960" width="24px" fill="#e8eaed"><path d="M42-120v-112q0-'
    "33 17-62t47-44q51-26 115-44t141-18q77 0 141 18t115 44q30 15 47 44t"
    "17 62v112H42Zm80-80h480v-32q0-11-5.5-20T582-266q-36-18-92.5-36T362"
    "-320q-71 0-127.5 18T142-266q-9 5-14.5 14t-5.5 20v32Zm240-240q-66 0"
    "-113-47t-47-113h-10q-9 0-14.5-5.5T172-620q0-9 5.5-14.5T192-640h10q"
    "0-45 22-81t58-57v38q0 9 5.5 14.5T302-720q9 0 14.5-5.5T322-740v-54q"
    "9-3 19-4.5t21-1.5q11 0 21 1.5t19 4.5v54q0 9 5.5 14.5T422-720q9 0 1"
    "4.5-5.5T442-740v-38q36 21 58 57t22 81h10q9 0 14.5 5.5T552-620q0 9-"
    "5.5 14.5T532-600h-10q0 66-47 113t-113 47Zm0-80q33 0 56.5-23.5T442-"
    "600H282q0 33 23.5 56.5T362-520Zm300 160-6-30q-6-2-11.5-4.5T634-402"
    "l-28 10-20-36 22-20v-24l-22-20 20-36 28 10q4-4 10-7t12-5l6-30h40l6"
    " 30q6 2 12 5t10 7l28-10 20 36-22 20v24l22 20-20 36-28-10q-5 5-10.5"
    " 7.5T708-390l-6 30h-40Zm20-70q12 0 21-9t9-21q0-12-9-21t-21-9q-12 0"
    "-21 9t-9 21q0 12 9 21t21 9Zm72-130-8-42q-9-3-16.5-7.5T716-620l-42 "
    "14-28-48 34-30q-2-5-2-8v-16q0-3 2-8l-34-30 28-48 42 14q6-6 13.5-10"
    ".5T746-798l8-42h56l8 42q9 3 16.5 7.5T848-780l42-14 28 48-34 30q2 5"
    " 2 8v16q0 3-2 8l34 30-28 48-42-14q-6 6-13.5 10.5T818-602l-8 42h-56"
    "Zm28-90q21 0 35.5-14.5T832-700q0-21-14.5-35.5T782-750q-21 0-35.5 1"
    '4.5T732-700q0 21 14.5 35.5T782-650ZM362-200Z"/></svg>'
)
