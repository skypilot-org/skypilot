"""Regression tests for thin-client installs that lack heavy deps.

See https://github.com/skypilot-org/skypilot/pull/9373.
"""
import subprocess
import sys
import textwrap


def test_client_validate_does_not_import_pandas():
    """Client-side `sdk.validate(dag)` must not import pandas.

    Thin-client installs (e.g. a CLI bundled with a stripped standalone
    Python that ships only the deps needed to POST to a remote API server)
    may not have pandas. The server owns the instance-type catalog and
    re-derives any instance-type-dependent fields it needs.

    We enforce this invariant in a subprocess with a `sys.meta_path`
    finder that raises `ImportError` for any `pandas*` import. Any client
    path that reaches pandas (directly or via a LazyImport first access)
    causes the subprocess to exit nonzero. Subprocess isolation avoids
    pollution from other tests that may have already imported pandas
    into the parent process's `sys.modules`.

    The test mocks only the HTTP transport and the server health check so
    that `sdk.validate()` can run end-to-end without a live server --
    everything else about the call is real: decorators, user-specified
    yaml handling, DAG serialization, admin-policy plumbing, version
    negotiation, etc.
    """
    script = textwrap.dedent('''
        import sys

        class _PandasBlocker:
            """Finder that refuses every pandas-related import."""

            def find_spec(self, name, path=None, target=None):
                if name == "pandas" or name.startswith("pandas."):
                    raise ImportError(
                        f"pandas import blocked: {name!r}. Client code "
                        "must not import pandas. "
                        "See skypilot-org/skypilot#9373."
                    )
                return None

        sys.meta_path.insert(0, _PandasBlocker())

        # `import sky` itself must not import pandas. If it does, this
        # raises here and the test surfaces the failing import.
        import sky
        import sky.client.sdk as sdk
        from unittest import mock

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {}

        task = sky.Task(run="echo hi")
        task.set_resources(
            sky.Resources(cloud=sky.AWS(), instance_type="m6a.4xlarge"))
        dag = sky.Dag()
        dag.add(task)

        # Mock the HTTP + server-health primitives only. Everything
        # between the client-facing API and the wire is real code.
        with mock.patch(
                "sky.server.common.make_authenticated_request",
                return_value=_FakeResponse(),
        ), mock.patch(
                "sky.server.common.check_server_healthy_or_start_fn",
                return_value=None,
        ):
            sdk.validate(dag)

        leaked = [
            m for m in sys.modules
            if m == "pandas" or m.startswith("pandas.")
        ]
        assert not leaked, f"pandas leaked into sys.modules: {leaked}"
        print("OK")
    ''')

    result = subprocess.run([sys.executable, '-c', script],
                            capture_output=True,
                            text=True,
                            timeout=60)
    # The script prints `OK` as its final line after all assertions pass.
    # Skypilot may emit debug logs earlier in stdout (depending on log
    # level), so check the last non-empty line rather than equality.
    last_line = next(
        (line for line in reversed(result.stdout.splitlines()) if line.strip()),
        '')
    assert result.returncode == 0 and last_line.strip() == 'OK', (
        'Subprocess failed. Client-side code appears to import pandas.\n'
        f'returncode: {result.returncode}\n'
        f'stdout:\n{result.stdout}\n'
        f'stderr:\n{result.stderr}\n')
