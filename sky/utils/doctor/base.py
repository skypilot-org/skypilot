"""Base classes for sky doctor accelerator health check plugins."""
import abc
import dataclasses
import enum
from typing import Callable, List, Optional, Tuple


class CheckStatus(enum.Enum):
    """Status of a single diagnostic check."""
    OK = 'OK'
    WARNING = 'WARNING'
    FAIL = 'FAIL'
    SKIPPED = 'SKIPPED'


@dataclasses.dataclass
class CheckResult:
    """Result of a single diagnostic check."""
    name: str
    status: CheckStatus
    message: str
    # Optional detail lines shown when --verbose is used.
    details: Optional[str] = None

    @classmethod
    def ok(cls,
           name: str,
           message: str,
           details: Optional[str] = None) -> 'CheckResult':
        return cls(name, CheckStatus.OK, message, details)

    @classmethod
    def warn(cls,
             name: str,
             message: str,
             details: Optional[str] = None) -> 'CheckResult':
        return cls(name, CheckStatus.WARNING, message, details)

    @classmethod
    def fail(cls,
             name: str,
             message: str,
             details: Optional[str] = None) -> 'CheckResult':
        return cls(name, CheckStatus.FAIL, message, details)

    @classmethod
    def skipped(cls, name: str, message: str) -> 'CheckResult':
        return cls(name, CheckStatus.SKIPPED, message)


# Type alias: a callable that runs a shell command on the remote cluster.
# Signature: run_cmd(cmd) -> (returncode, stdout, stderr)
RemoteRunner = Callable[[str], Tuple[int, str, str]]


class AcceleratorDoctorPlugin(abc.ABC):
    """Abstract base class for accelerator-specific diagnostic plugins.

    Concrete implementations should override ``checks()`` to return the
    list of individual checks to run and ``detect()`` to signal whether
    this plugin is applicable for the cluster being diagnosed.
    """

    # Human-readable name for this plugin (e.g. "NVIDIA GPU").
    NAME: str = ''

    # Short identifier used in CLI output (e.g. "nvidia").
    ACCELERATOR_TYPE: str = ''

    def __init__(self, run_cmd: RemoteRunner) -> None:
        """Initialise the plugin.

        Args:
            run_cmd: A callable that executes a shell command string on the
                remote cluster head node and returns
                (returncode, stdout, stderr).
        """
        self._run = run_cmd

    @classmethod
    @abc.abstractmethod
    def detect(cls, run_cmd: RemoteRunner) -> bool:
        """Return True if this plugin is applicable for the cluster.

        For example, the NVIDIA plugin returns True when ``nvidia-smi`` is
        available on the remote host.
        """

    @abc.abstractmethod
    def checks(self) -> List[Callable[[], CheckResult]]:
        """Return an ordered list of zero-argument callables, each running
        one diagnostic check and returning a CheckResult."""
