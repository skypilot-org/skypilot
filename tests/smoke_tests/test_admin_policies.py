"""Test admin policies for smoke testing."""
import sky


class EchoClientVersionPolicy(sky.AdminPolicy):
    """Test policy that echoes client version info in an error message.

    This policy always rejects requests with an error message containing
    the client version information. Useful for E2E testing that client
    version is correctly passed to admin policies.
    """

    @classmethod
    def validate_and_mutate(
            cls, user_request: sky.UserRequest) -> sky.MutatedUserRequest:
        """Reject with error containing client version info."""
        if not user_request.at_client_side:
            raise RuntimeError(
                f'ECHO_CLIENT_VERSION: '
                f'api_version={user_request.client_api_version}, '
                f'version={user_request.client_version}')
        return sky.MutatedUserRequest(
            task=user_request.task,
            skypilot_config=user_request.skypilot_config)
