#!/usr/bin/env python3
"""
Migration Helper Tool

Hilft bei der Migration von Python-Code zu rust_fallback imports.

Verwendung:
    python tools/migration_helper.py <file.py>                    # Analysiere Datei
    python tools/migration_helper.py <file.py> --migrate         # Migriere automatisch
    python tools/migration_helper.py <file.py> --check           # Pr?fe Migration
"""

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Available Rust-accelerated functions
RUST_FUNCTIONS = {
    # I/O utilities
    'read_last_n_lines',
    'hash_file',
    'find_free_port',
    
    # String utilities
    'base36_encode',
    'format_float',
    'truncate_long_string',
    
    # System utilities
    'get_cpu_count',
    'get_mem_size_gb',
    
    # Process utilities
    'get_parallel_threads',
    'is_process_alive',
    'get_max_workers_for_file_mounts',
    'estimate_fd_for_directory',
}

# Module mappings
OLD_MODULES = [
    'sky.utils.common_utils',
    'sky.utils.subprocess_utils',
]

NEW_MODULE = 'sky.utils.rust_fallback'


class MigrationAnalyzer(ast.NodeVisitor):
    """AST visitor to analyze Python code for migration opportunities."""
    
    def __init__(self):
        self.imports: List[Tuple[int, str, str]] = []  # (line, module, names)
        self.function_calls: List[Tuple[int, str]] = []  # (line, function)
        self.migratable_functions: Set[str] = set()
    
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            if any(old_mod in alias.name for old_mod in OLD_MODULES):
                self.imports.append((node.lineno, 'import', alias.name))
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module and any(old_mod in node.module for old_mod in OLD_MODULES):
            names = [alias.name for alias in node.names]
            self.imports.append((node.lineno, 'from', f"{node.module} import {', '.join(names)}"))
            
            # Check if any imported functions are migratable
            for name in names:
                if name in RUST_FUNCTIONS:
                    self.migratable_functions.add(name)
        
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in RUST_FUNCTIONS:
                self.function_calls.append((node.lineno, func_name))
        
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in RUST_FUNCTIONS:
                self.function_calls.append((node.lineno, attr_name))
        
        self.generic_visit(node)


def analyze_file(filepath: Path) -> Dict:
    """Analyze a Python file for migration opportunities."""
    
    try:
        content = filepath.read_text()
    except Exception as e:
        return {'error': str(e)}
    
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return {'error': f'Syntax error: {e}'}
    
    analyzer = MigrationAnalyzer()
    analyzer.visit(tree)
    
    return {
        'imports': analyzer.imports,
        'function_calls': analyzer.function_calls,
        'migratable_functions': analyzer.migratable_functions,
        'total_functions': len(analyzer.function_calls),
    }


def migrate_file(filepath: Path, dry_run: bool = True) -> bool:
    """Migrate a file to use rust_fallback."""
    
    try:
        content = filepath.read_text()
        original_content = content
    except Exception as e:
        print(f"Error reading file: {e}")
        return False
    
    # Replace imports
    changes_made = False
    
    for old_module in OLD_MODULES:
        # Pattern: from sky.utils.common_utils import ...
        pattern = rf'from {re.escape(old_module)} import ([^\n]+)'
        
        def replace_import(match):
            nonlocal changes_made
            imported_names = match.group(1)
            
            # Parse imported names
            names = [n.strip() for n in imported_names.split(',')]
            
            # Filter to only migratable functions
            migratable = [n for n in names if n in RUST_FUNCTIONS]
            non_migratable = [n for n in names if n not in RUST_FUNCTIONS]
            
            if migratable:
                changes_made = True
                result = f'from {NEW_MODULE} import {", ".join(migratable)}'
                
                if non_migratable:
                    # Keep old import for non-migratable functions
                    result += f'\nfrom {old_module} import {", ".join(non_migratable)}'
                
                return result
            else:
                return match.group(0)
        
        content = re.sub(pattern, replace_import, content)
    
    if changes_made:
        if dry_run:
            print("\n--- Proposed changes ---")
            print("OLD:")
            print(original_content[:500])
            print("\nNEW:")
            print(content[:500])
            print("\n--- End of changes ---")
            return True
        else:
            filepath.write_text(content)
            print(f"? Migrated: {filepath}")
            return True
    else:
        print(f"? No changes needed: {filepath}")
        return False


def print_analysis(filepath: Path, analysis: Dict):
    """Print analysis results."""
    
    print(f"\n{'='*70}")
    print(f"Migration Analysis: {filepath.name}")
    print(f"{'='*70}")
    
    if 'error' in analysis:
        print(f"\n? Error: {analysis['error']}")
        return
    
    # Imports
    print(f"\n?? Imports found: {len(analysis['imports'])}")
    for line, import_type, import_str in analysis['imports']:
        print(f"  Line {line}: {import_str}")
    
    # Function calls
    print(f"\n?? Function calls: {analysis['total_functions']}")
    function_counts = {}
    for line, func in analysis['function_calls']:
        function_counts[func] = function_counts.get(func, 0) + 1
    
    for func, count in sorted(function_counts.items()):
        emoji = "?" if func in RUST_FUNCTIONS else "  "
        print(f"  {emoji} {func}: {count} calls")
    
    # Migratable functions
    migratable = analysis['migratable_functions']
    print(f"\n? Migratable functions: {len(migratable)}")
    if migratable:
        for func in sorted(migratable):
            print(f"  ? {func}")
    else:
        print("  (none found)")
    
    # Summary
    print(f"\n?? Migration Potential")
    if migratable:
        print(f"  ? Can migrate {len(migratable)} functions to Rust")
        print(f"  ? Expected speedup: 2-25x per function")
        print(f"  ?? Expected memory reduction: 15-40%")
    else:
        print(f"  ??  No migratable functions found in imports")
        print(f"  Note: File may still benefit from rust_fallback if it uses these functions indirectly")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze and migrate Python code to use Rust-accelerated functions"
    )
    parser.add_argument('file', type=Path, help='Python file to analyze/migrate')
    parser.add_argument('--migrate', action='store_true', help='Perform migration')
    parser.add_argument('--dry-run', action='store_true', help='Show changes without applying')
    parser.add_argument('--check', action='store_true', help='Check if migration is needed')
    
    args = parser.parse_args()
    
    if not args.file.exists():
        print(f"? File not found: {args.file}")
        return 1
    
    if not args.file.suffix == '.py':
        print(f"? Not a Python file: {args.file}")
        return 1
    
    # Analyze
    analysis = analyze_file(args.file)
    
    if args.check:
        # Just check if migration is possible
        if analysis.get('migratable_functions'):
            print(f"? {len(analysis['migratable_functions'])} functions can be migrated")
            return 0
        else:
            print("? No functions to migrate")
            return 1
    
    # Print analysis
    print_analysis(args.file, analysis)
    
    # Migrate if requested
    if args.migrate or args.dry_run:
        if analysis.get('migratable_functions'):
            print(f"\n{'='*70}")
            print("Migration")
            print(f"{'='*70}")
            
            migrate_file(args.file, dry_run=args.dry_run or not args.migrate)
            
            if args.dry_run:
                print("\n?? Run with --migrate (without --dry-run) to apply changes")
        else:
            print("\n??  No migratable functions found, skipping migration")
    
    print("\n" + "="*70)
    print("Available Rust-accelerated functions:")
    print("="*70)
    for func in sorted(RUST_FUNCTIONS):
        print(f"  ? {func}")
    
    print("\n?? Documentation:")
    print("  ? INTEGRATION_GUIDE.md - How to integrate")
    print("  ? RUST_MIGRATION.md - Complete guide")
    print("  ? examples/rust_integration_example.py - Examples")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
