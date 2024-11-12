from typing import Optional


class User:
    """A class representing a sky user in the system.

    Attributes:
        id: user hash
        name: display name of the user
    """

    def __init__(self, user_id: str, user_name: Optional[str] = None):
        self.id = user_id
        self.name = user_name
