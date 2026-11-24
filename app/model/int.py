from pydantic import BaseModel

from app.model.out import Permission


class Entity(BaseModel):
    name: None | str = None
    exists: None | bool = None
    is_link: None | bool = None
    link: None | str = None
    # Whether another entity links to this one. Only looked up where it
    # matters (delete, rename), None otherwise.
    is_link_target: None | bool = None
    yaml: None | str = None
    data: None | dict = None
    # The permissions of the requesting user on THIS entity (see
    # `lib.repo.get_entities`): for `old`, evaluated as if the request only
    # referred to the stored entity (so `name` is its name, never the name of
    # a rename/copy/link target); for `new`, evaluated for the target name.
    # None where the entity plays no role in the request.
    perms: None | list[str] = None
