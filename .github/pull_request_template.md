<!-- Describe the changes in this PR -->



<!-- Describe the tests ran -->
<!-- Unit tests (tests/test_*.py) are part of GitHub CI; below are tests that launch on the cloud. -->

Tested (run the relevant ones):

- [ ] Code formatting: `bash format.sh`
  <!-- Or use pre-commit if installed -->

- [ ] Any manual/new tests:
  <!-- Specify below (CI command or local test) -->

- [ ] All smoke tests: `/smoke-test` (CI)
  <!-- Or run locally: `pytest tests/test_smoke.py` -->

- [ ] Relevant individual smoke tests: `/smoke-test --aws -k test_name` (CI)
  <!-- Or run locally: `pytest tests/test_smoke.py::test_fill_in_the_name` -->

- [ ] Backward compatibility: `/quicktest-core` (CI)
  <!-- Or run locally: `conda deactivate; bash -i tests/backward_compatibility_tests.sh` -->

<!-- CI commands (/-prefixed) can only be triggered by repo members -->
