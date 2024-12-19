from pydantic import BaseModel

from app.model.out import Permission


class Entity(BaseModel):
    name: None | str = None
    exists: None | bool = None
    is_link: None | bool = None
    link: None | str = None
    yaml: None | str = None
    data: None | dict = None
    perms: None | list[Permission | str] = None
