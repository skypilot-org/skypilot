"""ID Generator
"""

import random
import string
import uuid


def generate_random_uuid():
    return str(uuid.uuid4())


def rand(value):
    return value + generate_random_string(5)


def generate_random_string(length):
    return ''.join(random.choice(string.ascii_uppercase) for _ in range(length))
