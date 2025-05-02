# File: test_slurm_core.py

import argparse
import pprint
import sys
import traceback

# Attempt to import necessary SkyPilot modules
try:
    from sky import core as sky_core
    from sky import exceptions as sky_exceptions
    from sky.utils import common_utils
    # Optional: Initialize SkyPilot logging if needed for deeper debugging
    # from sky import sky_logging
    # sky_logging.init_logger()
except ImportError as e:
    print(f"Error importing SkyPilot modules: {e}", file=sys.stderr)
    print("Please ensure SkyPilot is installed correctly and you are in the "
          "correct Python environment.", file=sys.stderr)
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description="Directly test the sky.core.slurm_gpu_availability function."
    )
    parser.add_argument(
        '--name-filter',
        type=str,
        default=None,
        help='Filter GPUs by name (e.g., "V100", "A100")'
    )
    parser.add_argument(
        '--quantity-filter',
        type=int,
        default=None,
        help='Filter nodes by the total number of GPUs (e.g., 8)'
    )
    # The core function itself doesn't directly use region/partition filter,
    # it's applied in the catalog layer before calling the core function.
    # However, we keep the argument here if you modify the core function
    # or want to simulate passing it down.
    parser.add_argument(
        '--partition-filter',
        type=str,
        default=None,
        help='Filter nodes by Slurm partition name.'
    )
    # Add other relevant arguments if the core function signature changes
    # parser.add_argument('--env', nargs='*', default=None, help='Environment variables KEY=VALUE')

    args = parser.parse_args()

    # env_vars_dict = None
    # if args.env:
    #     env_vars_dict = dict(item.split("=", 1) for item in args.env)

    print("--- Testing sky.core.slurm_gpu_availability ---")
    print(f"Filters:")
    print(f"  Name:      {args.name_filter}")
    print(f"  Quantity:  {args.quantity_filter}")
    print(f"  Partition: {args.partition_filter}") # Note: Partition filter applied in catalog layer
    # print(f"  Env vars:  {env_vars_dict}")
    print("-" * 30)

    try:
        # Call the core function directly
        # Note: The current core.slurm_gpu_availability doesn't accept partition_filter directly.
        # It relies on the service_catalog layer to handle it.
        # We pass name and quantity filters as they might be used internally.
        gpu_availability = sky_core.slurm_gpu_availability(
            name_filter=args.name_filter,
            quantity_filter=args.quantity_filter,
            # partition_filter=args.partition_filter # Pass if core function signature changes
            # env_vars=env_vars_dict # Pass if needed
        )

        print("\n✅ Successfully retrieved Slurm GPU availability:")
        if gpu_availability:
            pprint.pprint(gpu_availability)
        else:
            print("(No GPU information returned)")

    except ValueError as e:
        print(f"\n❌ Error: ValueError encountered.", file=sys.stderr)
        print(f"  Message: {e}", file=sys.stderr)
        # Provide more context for common ValueErrors from this function
        if "not found" in str(e).lower() or "no gpus found" in str(e).lower():
            print("  Hint: This likely means no GPUs matched your filters or Slurm reported no GPUs.", file=sys.stderr)
        else:
             print("\nFull Traceback:", file=sys.stderr)
             traceback.print_exc() # Print full traceback for unexpected ValueErrors

    except sky_exceptions.NotSupportedError as e:
        print(f"\n❌ Error: Slurm operation not supported.", file=sys.stderr)
        print(f"  Message: {e}", file=sys.stderr)
        print("  Hint: This might mean Slurm is not enabled ('sky check'), not configured, or backend commands failed.", file=sys.stderr)
        print("\nFull Traceback:", file=sys.stderr)
        traceback.print_exc()

    except Exception as e:
        print(f"\n❌ An unexpected error occurred:", file=sys.stderr)
        print(f"  Type: {type(e).__name__}", file=sys.stderr)
        print(f"  Formatted: {common_utils.format_exception(e, use_bracket=True)}", file=sys.stderr)
        print("\nFull Traceback:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    print("-" * 30)
    print("--- Test finished ---")

if __name__ == "__main__":
    main()
