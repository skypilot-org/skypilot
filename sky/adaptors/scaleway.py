import functools

scaleway = None


def import_package(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global scaleway
        if scaleway is None:
            try:
                import scaleway as _scaleway  # type: ignore
                scaleway = _scaleway
            except ImportError:
                raise ImportError('Fail to import dependencies for Scaleway.'
                                  'Try pip install "skypilot[scaleway]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def instance():
    import scaleway
    client = scaleway.Client.from_config_file_and_env()
    from scaleway.instance.v1 import InstanceV1API
    return InstanceV1API(client=client)
