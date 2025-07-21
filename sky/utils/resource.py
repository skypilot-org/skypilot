try:
    from resource import *
except (ModuleNotFoundError, ImportError) as exc_info:
    # resource.py (dummy module for Windows)

    """
    A dummy replacement for the Unix-specific 'resource' module for use on
    Windows.
    
    This module provides no-op or plausible default implementations of the
    functions and constants from the 'resource' module. It is intended to
    allow libraries that have a non-critical dependency on 'resource' to be
    imported and run on Windows.
    """

    # Define constants that are used by the library.
    # You might need to add more depending on what the library uses.
    RLIMIT_NOFILE = 7

    # A common constant for "unlimited"
    RLIM_INFINITY = -1


    def getrlimit(res):
        """
        Dummy getrlimit for Windows.
        Returns a tuple of (soft_limit, hard_limit).
        -1 is often used to indicate an unlimited value.
        """
        # The library in your context uses this for file descriptors.
        # Returning a reasonably high number or -1 (unlimited) is a safe bet.
        if res == RLIMIT_NOFILE:
            # Return a plausible but fake value for open file limits.
            # (soft_limit, hard_limit)
            return (2048, 2048)
        # Default for other resource types
        return (RLIM_INFINITY, RLIM_INFINITY)


    def setrlimit(res, limits):
        """
        Dummy setrlimit for Windows. This is a no-op.
        """
        # Since we can't set resource limits on Windows this way, we do nothing.
        pass


    def getrusage(who):
        """
        Dummy getrusage for Windows. Returns a dummy struct_rusage object.
        """

        # This is a mock object that mimics the structure of struct_rusage
        class struct_rusage:
            def __init__(self):
                self.ru_utime = 0.0  # user time used
                self.ru_stime = 0.0  # system time used
                self.ru_maxrss = 0  # maximum resident set size
                self.ru_ixrss = 0  # integral shared memory size
                self.ru_idrss = 0  # integral unshared data size
                self.ru_isrss = 0  # integral unshared stack size
                self.ru_minflt = 0  # page reclaims (soft page faults)
                self.ru_majflt = 0  # page faults (hard page faults)
                self.ru_nswap = 0  # swaps
                self.ru_inblock = 0  # block input operations
                self.ru_oublock = 0  # block output operations
                self.ru_msgsnd = 0  # IPC messages sent
                self.ru_msgrcv = 0  # IPC messages received
                self.ru_nsignals = 0  # signals received
                self.ru_nvcsw = 0  # voluntary context switches
                self.ru_nivcsw = 0  # involuntary context switches

        return struct_rusage()
