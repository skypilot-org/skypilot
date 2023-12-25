from datetime import datetime
import functools
from logging import Logger


def debug_enabled(logger: Logger):

    def decorate(f):

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.debug(f"{dt_str} Enter {f}, {args}, {kwargs}")
            try:
                return f(*args, **kwargs)
            finally:
                logger.debug(f"{dt_str} Exit {f}")

        return wrapper

    return decorate
