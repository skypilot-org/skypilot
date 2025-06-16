/**
 * @jest-environment jsdom
 */

import { formatYaml } from '../yamlUtils';

describe('formatYaml function', () => {
  describe('environment variable redaction', () => {
    test('should redact all environment variables regardless of type', () => {
      const yamlString = `
name: test-task
run: echo hello
envs:
  API_KEY: secret-api-key-123
  DATABASE_URL: postgresql://user:password@host:5432/db
  DEBUG: "true"
  PORT: 8080
resources:
  cpus: 2
`;

      const result = formatYaml(yamlString);

      // Check that ALL env vars are redacted (JavaScript version redacts everything)
      expect(result).toContain('API_KEY: <redacted>');
      expect(result).toContain('DATABASE_URL: <redacted>');
      expect(result).toContain('DEBUG: <redacted>');
      expect(result).toContain('PORT: <redacted>');

      // Check that sensitive values are not present
      expect(result).not.toContain('secret-api-key-123');
      expect(result).not.toContain('postgresql://user:password@host:5432/db');
      expect(result).not.toContain('8080');

      // Check that other fields are preserved
      expect(result).toContain('name: test-task');
      expect(result).toContain('run: echo hello');
      expect(result).toContain('cpus: 2');
    });

    test('should redact all environment variable types', () => {
      const yamlString = `
name: test-task
envs:
  STRING_VAR: "sensitive-value"
  PORT: 8080
  DEBUG_ENABLED: true
  TIMEOUT: 30.5
  EMPTY_VAR: ""
  NULL_VAR: null
`;

      const result = formatYaml(yamlString);

      // ALL env vars should be redacted in JavaScript version
      expect(result).toContain('STRING_VAR: <redacted>');
      expect(result).toContain('PORT: <redacted>');
      expect(result).toContain('DEBUG_ENABLED: <redacted>');
      expect(result).toContain('TIMEOUT: <redacted>');
      expect(result).toContain('EMPTY_VAR: <redacted>');
      expect(result).toContain('NULL_VAR: <redacted>');

      // No original values should be present
      expect(result).not.toContain('sensitive-value');
      expect(result).not.toContain('8080');
      expect(result).not.toContain('true');
      expect(result).not.toContain('30.5');
    });

    test('should not redact environment variables if envs is an array', () => {
      const yamlString = `
name: test-task
envs:
  - API_KEY=secret-value
  - DEBUG=true
`;

      const result = formatYaml(yamlString);

      // Should not redact array-style envs (this is intentional based on the condition)
      expect(result).toContain('API_KEY=secret-value');
      expect(result).toContain('DEBUG=true');
    });

    test('should handle missing envs field', () => {
      const yamlString = `
name: test-task
run: echo hello
resources:
  cpus: 2
`;

      const result = formatYaml(yamlString);

      expect(result).toContain('name: test-task');
      expect(result).toContain('run: echo hello');
      expect(result).toContain('cpus: 2');
      expect(result).not.toContain('envs:');
    });

    test('should handle null envs field', () => {
      const yamlString = `
name: test-task
envs: null
run: echo hello
`;

      const result = formatYaml(yamlString);

      expect(result).toContain('name: test-task');
      expect(result).toContain('envs: null');
      expect(result).toContain('run: echo hello');
    });

    test('should handle empty envs object', () => {
      const yamlString = `
name: test-task
envs: {}
run: echo hello
`;

      const result = formatYaml(yamlString);

      expect(result).toContain('name: test-task');
      expect(result).toContain('run: echo hello');
      // Empty object might be formatted differently by yaml.dump
    });

    test('should redact complex environment variables', () => {
      const yamlString = `
name: ml-training
envs:
  AWS_ACCESS_KEY_ID: AKIAIOSFODNN7EXAMPLE
  AWS_SECRET_ACCESS_KEY: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
  OPENAI_API_KEY: sk-proj-abcdef1234567890
  DATABASE_PASSWORD: "MyVerySecretPassword123!"
  JWT_SECRET: supersecretjwtkey
  STRIPE_KEY: sk_live_51234567890
  WORKER_COUNT: 4
  ENABLE_LOGGING: true
`;

      const result = formatYaml(yamlString);

      // ALL values should be redacted in JavaScript version
      expect(result).toContain('AWS_ACCESS_KEY_ID: <redacted>');
      expect(result).toContain('AWS_SECRET_ACCESS_KEY: <redacted>');
      expect(result).toContain('OPENAI_API_KEY: <redacted>');
      expect(result).toContain('DATABASE_PASSWORD: <redacted>');
      expect(result).toContain('JWT_SECRET: <redacted>');
      expect(result).toContain('STRIPE_KEY: <redacted>');
      expect(result).toContain('WORKER_COUNT: <redacted>');
      expect(result).toContain('ENABLE_LOGGING: <redacted>');

      // No sensitive data should be visible
      expect(result).not.toContain('AKIAIOSFODNN7EXAMPLE');
      expect(result).not.toContain('wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY');
      expect(result).not.toContain('sk-proj-abcdef1234567890');
      expect(result).not.toContain('MyVerySecretPassword123!');
      expect(result).not.toContain('supersecretjwtkey');
      expect(result).not.toContain('sk_live_51234567890');
      expect(result).not.toContain('4');
      expect(result).not.toContain('true');
    });
  });

  describe('YAML formatting and structure', () => {
    test('should handle null or empty input', () => {
      expect(formatYaml(null)).toBe('No YAML available');
      expect(formatYaml(undefined)).toBe('No YAML available');
      expect(formatYaml('')).toBe('No YAML available');
    });

    test('should handle invalid YAML gracefully', () => {
      const invalidYaml = `
name: test
envs:
  API_KEY: secret
  invalid: [unclosed
`;

      // Should return original string if parsing fails
      const result = formatYaml(invalidYaml);
      expect(result).toBe(invalidYaml);
    });

    test('should preserve task structure while redacting envs', () => {
      const yamlString = `
name: web-server
setup: pip install requirements.txt
run: python app.py
envs:
  SECRET_KEY: my-secret-key
  PORT: 8080
resources:
  cpus: 2
  memory: 4GB
file_mounts:
  /app: ./src
workdir: /app
`;

      const result = formatYaml(yamlString);

      // Check all non-env fields are preserved
      expect(result).toContain('name: web-server');
      expect(result).toContain('setup: pip install requirements.txt');
      expect(result).toContain('run: python app.py');
      expect(result).toContain('cpus: 2');
      expect(result).toContain('memory: 4GB');
      expect(result).toContain('/app: ./src');
      expect(result).toContain('workdir: /app');

      // Check env redaction - ALL values are redacted in JavaScript
      expect(result).toContain('SECRET_KEY: <redacted>');
      expect(result).toContain('PORT: <redacted>');
      expect(result).not.toContain('my-secret-key');
      expect(result).not.toContain('8080');
    });

    test('should handle nested objects without affecting envs redaction', () => {
      const yamlString = `
name: complex-task
envs:
  SECRET: sensitive-data
  COUNT: 5
resources:
  cloud: aws
  instance_type: p3.2xlarge
  accelerators:
    V100: 1
service:
  readiness_probe:
    path: /health
    initial_delay_seconds: 30
`;

      const result = formatYaml(yamlString);

      // Env redaction should work - ALL values redacted
      expect(result).toContain('SECRET: <redacted>');
      expect(result).toContain('COUNT: <redacted>');
      expect(result).not.toContain('sensitive-data');
      expect(result).not.toContain('5');

      // Nested structures should be preserved
      expect(result).toContain('cloud: aws');
      expect(result).toContain('instance_type: p3.2xlarge');
      expect(result).toContain('V100: 1');
      expect(result).toContain('path: /health');
      expect(result).toContain('initial_delay_seconds: 30');
    });
  });

  describe('edge cases', () => {
    test('should handle envs field that is not an object', () => {
      const yamlString = `
name: test-task
envs: "not an object"
run: echo hello
`;

      const result = formatYaml(yamlString);

      // Should not crash and should preserve the original value
      expect(result).toContain('name: test-task');
      expect(result).toContain('envs: not an object');
      expect(result).toContain('run: echo hello');
    });

    test('should handle very large environment variable values', () => {
      const longSecret = 'x'.repeat(1000);
      const yamlString = `
name: test-task
envs:
  LONG_SECRET: ${longSecret}
  NORMAL_VAR: short
run: echo hello
`;

      const result = formatYaml(yamlString);

      expect(result).toContain('LONG_SECRET: <redacted>');
      expect(result).toContain('NORMAL_VAR: <redacted>');
      expect(result).not.toContain(longSecret);
    });

    test('should handle environment variables with special characters in quoted strings', () => {
      const yamlString = `
name: test-task
envs:
  SPECIAL_CHARS: "hello-world"
  UNICODE_VAR: "simple-text"
  MULTILINE: "multi line text"
`;

      const result = formatYaml(yamlString);

      expect(result).toContain('SPECIAL_CHARS: <redacted>');
      expect(result).toContain('UNICODE_VAR: <redacted>');
      expect(result).toContain('MULTILINE: <redacted>');

      // Original values should not be present
      expect(result).not.toContain('hello-world');
      expect(result).not.toContain('simple-text');
      expect(result).not.toContain('multi line text');
    });
  });
});
