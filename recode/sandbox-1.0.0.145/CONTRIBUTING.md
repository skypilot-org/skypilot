# Contributing to Agent Infrastructure SDK

Thank you for your interest in contributing to the Agent Infrastructure SDK! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Development Guidelines](#development-guidelines)
- [Testing](#testing)
- [Documentation](#documentation)
- [Release Process](#release-process)

## Code of Conduct

We are committed to providing a welcoming and inclusive environment for all contributors. Please be respectful and constructive in all interactions.

### Expected Behavior

- Use welcoming and inclusive language
- Be respectful of differing viewpoints and experiences
- Gracefully accept constructive criticism
- Focus on what is best for the community
- Show empathy towards other community members

## Getting Started

### Prerequisites

- Node.js 18+ and npm/yarn for JavaScript SDK
- Python 3.8+ for Python SDK
- Git for version control
- Docker (optional, for containerized development)

### Repository Structure

```
sdk/
├── javascript/          # JavaScript/TypeScript SDK
│   ├── src/            # Source code
│   ├── tests/          # Test files
│   └── examples/       # Usage examples
├── python/             # Python SDK
│   ├── src/            # Source code
│   ├── tests/          # Test files
│   └── examples/       # Usage examples
└── website/            # Documentation website
```

## Development Setup

### JavaScript SDK

```bash
cd javascript
npm install
npm run build
npm test
```

### Python SDK

```bash
cd python
pip install -e .
pip install -r requirements-dev.txt
pytest
```

## How to Contribute

### Reporting Issues

Before creating an issue, please check if a similar issue already exists. When creating a new issue:

1. Use a clear and descriptive title
2. Provide a detailed description of the problem
3. Include steps to reproduce the issue
4. Specify your environment (OS, SDK version, runtime version)
5. Include relevant code snippets or error messages

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When suggesting an enhancement:

1. Use a clear and descriptive title
2. Provide a detailed description of the proposed enhancement
3. Explain why this enhancement would be useful
4. Include code examples if applicable

### Contributing Code

1. Fork the repository
2. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. Make your changes following our coding standards
4. Write or update tests as needed
5. Ensure all tests pass
6. Commit your changes with a descriptive message
7. Push to your fork and create a pull request

## Pull Request Process

### Before Submitting

1. **Test your changes**: Run the full test suite
2. **Update documentation**: Update relevant documentation and examples
3. **Follow code style**: Run linters and formatters
4. **Write clear commits**: Use conventional commit format

### Pull Request Guidelines

1. **Title**: Use a clear, descriptive title following conventional commit format
   - `feat:` for new features
   - `fix:` for bug fixes
   - `docs:` for documentation changes
   - `refactor:` for code refactoring
   - `test:` for test additions/changes
   - `chore:` for maintenance tasks

2. **Description**: Include:
   - Summary of changes
   - Related issue numbers (fixes #123)
   - Breaking changes (if any)
   - Testing performed

3. **Size**: Keep PRs focused and reasonably sized
   - Split large changes into multiple PRs if possible
   - One feature/fix per PR

### Review Process

1. At least one maintainer review is required
2. All CI checks must pass
3. Resolve all review comments
4. Maintainers will merge using squash and merge

## Development Guidelines

### Code Style

#### JavaScript/TypeScript

- Use ESLint and Prettier configurations
- Follow TypeScript strict mode
- Use meaningful variable and function names
- Add JSDoc comments for public APIs

```typescript
/**
 * Creates a new sandbox instance
 * @param config - Sandbox configuration options
 * @returns Promise<Sandbox> - The initialized sandbox
 */
async function createSandbox(config: SandboxConfig): Promise<Sandbox> {
  // Implementation
}
```

#### Python

- Follow PEP 8 style guide
- Use type hints for function signatures
- Use meaningful variable and function names
- Add docstrings for all public functions

```python
def create_sandbox(config: SandboxConfig) -> Sandbox:
    """
    Creates a new sandbox instance.

    Args:
        config: Sandbox configuration options

    Returns:
        The initialized sandbox instance

    Raises:
        ValueError: If configuration is invalid
    """
    # Implementation
```

### Commit Messages

Follow conventional commit format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Examples:
```
feat(python): add sandbox lifecycle management
fix(javascript): resolve connection timeout issue
docs: update API reference for v2.0
```

### Testing

- Write unit tests for all new functionality
- Maintain or improve code coverage
- Include integration tests for API changes
- Test edge cases and error conditions

#### JavaScript Testing

```bash
npm test                 # Run all tests
npm run test:unit       # Run unit tests only
npm run test:integration # Run integration tests
npm run test:coverage   # Generate coverage report
```

#### Python Testing

```bash
pytest                   # Run all tests
pytest tests/unit       # Run unit tests only
pytest tests/integration # Run integration tests
pytest --cov=src        # Generate coverage report
```

## Documentation

### Code Documentation

- Document all public APIs
- Include examples in docstrings
- Explain complex algorithms or business logic
- Keep documentation up-to-date with code changes

### User Documentation

- Update README files as needed
- Add examples for new features
- Update API reference documentation
- Consider adding tutorials for complex features

### Documentation Website

The documentation website is built with Docusaurus:

```bash
cd website
npm install
npm start  # Start development server
npm build  # Build for production
```

## Release Process

### Version Numbering

We follow Semantic Versioning (SemVer):

- MAJOR version for incompatible API changes
- MINOR version for backwards-compatible functionality additions
- PATCH version for backwards-compatible bug fixes

### Release Checklist

1. Update version numbers in package files
2. Update CHANGELOG.md
3. Run full test suite
4. Build and verify packages
5. Create git tag
6. Publish to package repositories
7. Update documentation website
8. Create GitHub release with notes

### Publishing

#### JavaScript SDK

```bash
npm version <major|minor|patch>
npm run build
npm publish
```

#### Python SDK

```bash
cd python
./publish.sh  # Builds, publishes to PyPI, and creates git tag
```

## Questions and Support

If you have questions or need help:

1. Check the documentation
2. Search existing issues
3. Join our community discussions
4. Create a new issue if needed

## License

By contributing to this project, you agree that your contributions will be licensed under the same license as the project (see LICENSE file).

## Acknowledgments

Thank you to all contributors who help make this project better!