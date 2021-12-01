from sky import resources

Resources = resources.Resources


class ResourcesUnavailableError(Exception):

    def __init__(self, to_provision: Resources):
        super().__init__(f'Unable to provision {to_provision}')
        self.to_provision = to_provision
