# Security Policy

## Reporting a Vulnerability

We take security seriously at SkyPilot. If you discover a security vulnerability, please report it responsibly following the guidelines below.

### How to Report

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report security vulnerabilities by emailing:

**security@skypilot.co**

### What to Include

Please include as much of the following information as possible to help us understand and address the issue:

- Type of vulnerability (e.g., remote code execution, privilege escalation, information disclosure)
- Full paths of source file(s) related to the vulnerability
- Location of the affected source code (tag/branch/commit or direct URL)
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue and how an attacker might exploit it

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within 48 hours.
- **Communication**: We will keep you informed of our progress as we work to address the issue.
- **Disclosure**: We will work with you to determine an appropriate disclosure timeline (typically 90 days).
- **Credit**: We will credit you for the discovery (unless you prefer to remain anonymous).

### Scope

This security policy applies to:

- The SkyPilot core library (`sky/`)
- SkyPilot CLI tools
- SkyPilot API server and dashboard
- Official SkyPilot container images
- Helm charts and deployment configurations

### Out of Scope

The following are generally out of scope:

- Vulnerabilities in third-party dependencies (please report these to the respective maintainers)
- Issues in user-provided task scripts or configurations
- Security issues in cloud provider services themselves

## Supported Versions

We provide security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| < 1.0.0 | :x:                |

We recommend always using the latest version of SkyPilot for the best security.

## Security Best Practices

When using SkyPilot, we recommend:

1. **Keep SkyPilot updated**: Always use the latest version for security patches.
2. **Use secure credentials**: Follow cloud provider best practices for credential management.
3. **Restrict network access**: Use security groups and firewalls to limit access to SkyPilot-managed resources.
4. **Review task configurations**: Audit task YAML files before execution, especially from untrusted sources.
5. **Enable logging**: Use `SKYPILOT_DEBUG=1` to enable detailed logging for audit purposes.

## Past Security Advisories

Security advisories will be published in the [GitHub Security Advisories](https://github.com/skypilot-org/skypilot/security/advisories) section.
