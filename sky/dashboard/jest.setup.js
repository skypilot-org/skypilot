require('@testing-library/jest-dom');

// Mock fetch
global.fetch = jest.fn();

// Keep original console methods for testing
const originalConsole = { ...console };
global.console = {
  ...console,
  error: (...args) => {
    originalConsole.error(...args);
  },
  warn: (...args) => {
    originalConsole.warn(...args);
  },
  log: (...args) => {
    originalConsole.log(...args);
  },
  info: (...args) => {
    originalConsole.info(...args);
  },
  debug: (...args) => {
    originalConsole.debug(...args);
  },
};

// Add Jest globals
global.describe = describe;
global.test = test;
global.expect = expect;

// Add any global test setup here
