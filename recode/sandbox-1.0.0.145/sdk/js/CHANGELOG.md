# Changelog

All notable changes to the JS SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.6] - 2025-09-15

### Changed

- change returncode to exit_code in shell exec response
- optimize logs for request info

## [0.0.5] - 2025-09-08

### Changed

- Improved README.md to make it more readful.

## [0.0.4] - 2025-09-08

### Added

- **Browser API Module**: New browser-related API functionality
  - Browser information retrieval
  - Browser action automation support
- **Modular Architecture**: Split codebase into specialized modules
  - Browser module (`src/modules/browser.ts`)
  - File operations module (`src/modules/file.ts`)
  - Shell operations module (`src/modules/shell.ts`)
  - Shared types module (`src/modules/types.ts`)

## [0.0.3] - 2025-08-29

### Added

- Enhanced error messages with additional body content for better debugging

### Changed

- Improved error handling and response formatting

## [0.0.2] - 2025-08-26

### Added

- **BaseClient Architecture**: Split base logic code into reusable BaseClient class
- **Request Options**: Added requestOption parameter for all API methods
- **Logging System**: Implemented configurable logLevel with fetch cost calculation
- **Timeout Handling**: Added comprehensive timeout support with test suites
- **New Examples**:
  - Benchmark example (`examples/bench.ts`)
  - Jupyter notebook example (`examples/jupeter.ts`)
  - Shell execution example (`examples/shell.ts`)

### Changed

- **Major Refactoring**: Refactored AIO client to extend BaseClient for better code organization
- **API Enhancement**: All APIs now support custom request options
- **Performance**: Added fetch cost tracking and performance monitoring
- **Type System**: Enhanced TypeScript types with additional interfaces

### Removed

- Removed old AIO example (`examples/aio.ts`) in favor of more specific examples

### Testing

- Added comprehensive timeout test suites
- Enhanced basic test coverage with 200+ additional test cases

## [0.0.1] - 2025-08-14

### Added

- **Initial Release**: Complete JS SDK for AIO Sandbox integration
- **Core Features**:
  - AIO client with full API support
  - TypeScript support with comprehensive type definitions
  - Built-in testing framework with Vitest
  - Example usage code
- **API Support**:
  - Sandbox lifecycle management (create, delete, status)
  - Shell command execution with polling
  - Jupyter notebook operations
  - File system operations
  - Health check endpoint
- **Development Tools**:
  - TypeScript compilation with watch mode
  - Test coverage reporting
  - UI testing interface
  - Build and clean scripts
- **Documentation**: Comprehensive README with usage examples and API reference

### Technical Details

- Node.js 18+ support
- TypeScript 5+ compatibility
- Integrated logger dependency
- Comprehensive test suite (480+ test cases)
- Example implementations included

---

## Version History Summary

- **v0.0.3**: Error message improvements
- **v0.0.2**: Major architecture refactoring with BaseClient, timeout handling, and request options
- **v0.0.1**: Initial release with complete SDK functionality
