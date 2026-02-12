"""Remote function decorator for Sky Batch.

This module provides the @remote_function decorator that marks functions
for execution on remote workers.
"""
import ast
import builtins
import inspect
from typing import Callable, Set, TypeVar

F = TypeVar('F', bound=Callable)


def remote_function(fn: F) -> F:
    """Decorator for functions that execute on remote workers.

    The decorated function will be serialized and sent to workers for
    execution. All imports must be inside the function body since the
    function runs in a fresh Python environment on each worker.

    The pool's setup command must install all required packages before
    the function can run.

    Example:
        @sky.batch.remote_function
        def process_data():
            import pandas as pd  # Import inside function

            for batch in sky.batch.load():
                df = pd.DataFrame(batch)
                results = df.to_dict('records')
                sky.batch.save_results(results)

    Args:
        fn: The function to decorate.

    Returns:
        The decorated function with metadata marking it as a remote function.

    Raises:
        ValueError: If the function references external variables (closures).
    """
    # Validate that the function doesn't have closures
    _validate_no_closures(fn)

    fn._is_remote_function = True  # type: ignore[attr-defined]  # pylint: disable=protected-access
    return fn


def _validate_no_closures(fn: Callable) -> None:
    """Validate that a function doesn't reference external variables.

    Remote functions are serialized as source code and executed in a clean
    namespace on workers. Any references to external variables (closures)
    will fail at runtime, so we detect and reject them early.

    Args:
        fn: The function to validate.

    Raises:
        ValueError: If the function has closures or references external
                    variables that won't be available on the worker.
    """
    # Check for closure variables (free variables)
    if fn.__code__.co_freevars:
        closure_vars = ', '.join(fn.__code__.co_freevars)
        raise ValueError(
            f'Remote function {fn.__name__!r} references external variables: '
            f'{closure_vars}\n\n'
            f'Remote functions are executed in a clean namespace on workers '
            f'and cannot access variables from the enclosing scope.\n\n'
            f'To fix this:\n'
            f'1. Move the variable definition inside the function\n'
            f'2. Load resources inside the function (runs once per worker):\n\n'
            f'   @sky.batch.remote_function\n'
            f'   def {fn.__name__}():\n'
            f'       # Load model/data inside function\n'
            f'       model = load_model()\n'
            f'       \n'
            f'       for batch in sky.batch.load():\n'
            f'           results = [model.predict(item) for item in batch]\n'
            f'           sky.batch.save_results(results)\n')

    # Check for nonlocal variables (Python 3 nonlocal keyword)
    # These are indicated by cells in the closure
    if fn.__closure__ is not None:
        # Get the names of closure variables
        closure_var_names = list(fn.__code__.co_freevars)
        if closure_var_names:
            closure_info = []
            for var_name, cell in zip(closure_var_names, fn.__closure__):
                try:
                    value = cell.cell_contents
                    value_type = type(value).__name__
                    closure_info.append(f'{var_name} ({value_type})')
                except ValueError:
                    closure_info.append(f'{var_name} (unbound)')

            closure_str = ', '.join(closure_info)
            raise ValueError(
                f'Remote function {fn.__name__!r} has closures over: '
                f'{closure_str}\n\n'
                f'Remote functions cannot access external variables. '
                f'Move all logic inside the function body.')

    # Check for global references using AST
    _validate_no_global_references(fn)


def _validate_no_global_references(fn: Callable) -> None:
    """Validate that function doesn't reference module-level globals.

    Uses AST to detect references to names that are defined at module level
    but won't be available in the worker's clean namespace.

    Args:
        fn: The function to validate.

    Raises:
        ValueError: If the function references global variables that won't
                    be available on workers.
    """
    try:
        source = inspect.getsource(fn)
    except (TypeError, OSError):
        # Can't get source - skip validation
        # This happens for functions defined interactively (e.g., in python -c)
        return

    # Parse the function source
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Can't parse - skip validation
        return

    # Find the function definition
    func_def = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn.__name__:
            func_def = node
            break

    if func_def is None:
        return

    # Collect names defined locally in the function
    local_names = _collect_local_names(func_def)

    # Collect names referenced but not defined locally
    referenced_names = _collect_referenced_names(func_def)

    # Get builtin names
    builtin_names = set(dir(builtins))

    # Names that are safe to use (will be available on worker)
    safe_names = builtin_names | {
        'sky',  # Made available in worker namespace
        '__name__',
        '__file__',
        '__builtins__',
    }

    # Find problematic global references
    global_refs = referenced_names - local_names - safe_names

    if global_refs:
        # Check if these are actually defined in the function's globals
        # (to avoid false positives)
        fn_globals = fn.__globals__
        actual_globals = {name for name in global_refs if name in fn_globals}

        if actual_globals:
            globals_str = ', '.join(sorted(actual_globals))
            raise ValueError(
                f'Remote function {fn.__name__!r} references module-level '
                f'globals: {globals_str}\n\n'
                f'Remote functions are executed in a clean namespace on workers'
                f' and cannot access module-level imports or variables.\n\n'
                f'To fix this, move all imports inside the function:\n\n'
                f'   # ❌ BAD - import at module level\n'
                f'   import vllm\n'
                f'   \n'
                f'   @sky.batch.remote_function\n'
                f'   def {fn.__name__}():\n'
                f'       vllm.LLM(...)  # Won\'t work!\n'
                f'   \n'
                f'   # ✅ GOOD - import inside function\n'
                f'   @sky.batch.remote_function\n'
                f'   def {fn.__name__}():\n'
                f'       import vllm\n'
                f'       vllm.LLM(...)  # Works!\n')


def _collect_local_names(func_def: ast.FunctionDef) -> Set[str]:
    """Collect names defined locally within a function.

    This includes:
    - Function parameters
    - Variables assigned in the function
    - Names in for loops
    - Names in with statements
    - Imported names
    - Nested function/class definitions

    Args:
        func_def: AST node for the function definition.

    Returns:
        Set of locally defined names.
    """
    local_names = set()

    # Add function parameters
    for arg in func_def.args.args:
        local_names.add(arg.arg)

    # Walk the function body
    for node in ast.walk(func_def):
        # Assignment targets
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    local_names.add(target.id)
        # For loop variables
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                local_names.add(node.target.id)
        # With statement variables
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars,
                                                     ast.Name):
                    local_names.add(item.optional_vars.id)
        # Import statements
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                local_names.add(name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                local_names.add(name)
        # Nested functions and classes
        elif isinstance(node,
                        (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node != func_def:  # Don't add the function itself
                local_names.add(node.name)
        # Exception handlers
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                local_names.add(node.name)

    return local_names


def _collect_referenced_names(func_def: ast.FunctionDef) -> Set[str]:
    """Collect names referenced (loaded) within a function.

    Args:
        func_def: AST node for the function definition.

    Returns:
        Set of referenced names.
    """
    referenced = set()

    for node in ast.walk(func_def):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            referenced.add(node.id)

    return referenced


def is_remote_function(fn: Callable) -> bool:
    """Check if a function is decorated with @remote_function.

    Args:
        fn: The function to check.

    Returns:
        True if the function is a remote function, False otherwise.
    """
    return getattr(fn, '_is_remote_function', False)
