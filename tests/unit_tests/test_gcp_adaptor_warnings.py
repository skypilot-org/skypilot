"""Tests for the warning filters installed by sky.adaptors.gcp."""
import warnings

import pytest

from sky.adaptors import gcp


def _shown_future_warnings(message: str, module: str) -> list:
    """Emits a FutureWarning as if raised from `module` with only the GCP
    adaptor's filters active, and returns the warnings that were shown."""
    with warnings.catch_warnings(record=True) as caught:
        # Start from Python's defaults so the result does not depend on
        # PYTHONWARNINGS or on filters installed by pytest.
        warnings.resetwarnings()
        warnings.simplefilter('always')
        gcp._suppress_google_deprecation_warnings()  # pylint: disable=protected-access
        warnings.warn_explicit(message,
                               FutureWarning,
                               filename=module.replace('.', '/') + '.py',
                               lineno=1,
                               module=module)
    return [w for w in caught if issubclass(w.category, FutureWarning)]


@pytest.mark.parametrize(
    'message,module',
    [
        # Older google-api-core wording (issue #7886).
        ('You are using a Python version (3.10.0) which Google will stop '
         'supporting in new releases of google.api_core once it reaches its '
         'end of life (2026-10-04).', 'google.api_core._python_version_support'
        ),
        # Newer google-api-core wording once the Python version is past EOL.
        ('You are using a non-supported Python version (3.10.22). Google will '
         'not post any further updates to google.api_core supporting this '
         'Python version.', 'google.api_core._python_version_support'),
        # google-api-core dependency deprecation (grpcio < 1.83.0, no PQC).
        ('Package google.api_core depends on grpcio, currently installed at '
         'version 1.63.0. grpcio < 1.83.0 does not support Post-Quantum '
         'Cryptography (PQC). Support for non-PQC environments is '
         'deprecated.', 'google.api_core._python_package_support'),
        # google-auth grpc transport deprecation (grpcio < 1.83.0, no PQC).
        ('grpcio < 1.83.0 does not support Post-Quantum Cryptography (PQC). '
         'Support for non-PQC environments is deprecated. In April 2027, '
         'google-auth will raise its minimum requirements to enforce '
         'grpcio >= 1.83.0.', 'google.auth.transport.grpc'),
    ])
def test_google_deprecation_future_warnings_suppressed(message, module):
    assert _shown_future_warnings(message, module) == []


def test_unrelated_future_warning_not_suppressed():
    shown = _shown_future_warnings('some other deprecation',
                                   'google.api_core.retry')
    assert len(shown) == 1
