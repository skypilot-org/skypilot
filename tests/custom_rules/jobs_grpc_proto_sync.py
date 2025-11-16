"""
Check if the ManagedJobRecord class in sky/schemas/api/responses.py has all the fields that are present in the ManagedJobInfo protobuf message.
If not, print the missing fields and suggest a diff to add the missing fields to sky/skylet/services.py.
"""

import difflib
from typing import Optional, Sequence

import libcst as cst
import libcst.matchers as m

from sky.schemas.generated import managed_jobsv1_pb2


class ManagedJobRecordFieldsVisitor(cst.CSTVisitor):

    def __init__(self):
        self.in_managed_job_record = False
        self.original_field_name: Optional[str] = None
        self.fields = set[str]()

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        if not m.matches(node, m.ClassDef(name=m.Name('ManagedJobRecord'))):
            return False
        self.in_managed_job_record = True

    def leave_ClassDef(self, node: cst.ClassDef) -> None:
        if m.matches(node, m.ClassDef(name=m.Name('ManagedJobRecord'))):
            self.in_managed_job_record = False

    def visit_AnnAssign(self, node: cst.AnnAssign) -> Optional[bool]:
        if not self.in_managed_job_record:
            return False

        cst.ensure_type(node.target, cst.Name)

        field_name = node.target.value
        self.fields.add(field_name)
        self.original_field_name = field_name

        if self._is_pydantic_field_call(node.value):
            self._apply_alias_from_field_call(node.value)

    def _is_pydantic_field_call(self, node: cst.Call) -> bool:
        return m.matches(
            node,
            m.Call(func=m.OneOf(
                m.Attribute(value=m.Name('pydantic'), attr=m.Name('Field')),
                m.Name('Field'),
            )),
        )

    def _apply_alias_from_field_call(self, node: cst.Call) -> None:
        if self.original_field_name is None:
            return
        for arg in node.args:
            if not m.matches(arg, m.Arg(keyword=m.Name('alias'))):
                continue
            cst.ensure_type(arg.value, cst.SimpleString)
            self.fields.remove(self.original_field_name)
            self.fields.add(arg.value.evaluated_value)
            self.original_field_name = None
            break


class ManagedJobInfoTransformer(cst.CSTTransformer):
    """Adds missing fields to ManagedJobInfo constructor calls."""

    def __init__(self, missing_fields: set[str], source_module: cst.Module):
        self.missing_fields = sorted(missing_fields)
        self.source_module = source_module

    def leave_Call(self, original_node: cst.Call,
                   updated_node: cst.Call) -> cst.Call:
        """Check if this is a ManagedJobInfo(...) call and add missing fields."""
        # Match call to managed_jobsv1_pb2.ManagedJobInfo(...)
        if not m.matches(
                updated_node,
                m.Call(func=m.Attribute(value=m.Name('managed_jobsv1_pb2'),
                                        attr=m.Name('ManagedJobInfo'))),
        ):
            return updated_node

        # Extract current argument names
        current_arg_names = set()
        for arg in updated_node.args:
            if arg.keyword is not None:
                current_arg_names.add(arg.keyword.value)

        # Create new args for missing fields
        new_args: list[cst.Arg] = []
        indent_str = ' ' * 4

        for i, field_name in enumerate(self.missing_fields):
            assert field_name not in current_arg_names, f"Field {field_name} already exists in ManagedJobInfo"

            # Last arg should have no comma
            is_last = (i == len(self.missing_fields) - 1)

            new_arg = cst.Arg(
                keyword=cst.Name(field_name),
                equal=cst.AssignEqual(
                    whitespace_before=cst.SimpleWhitespace(''),
                    whitespace_after=cst.SimpleWhitespace(''),
                ),
                value=cst.Call(
                    func=cst.Attribute(value=cst.Name('job'),
                                       attr=cst.Name('get')),
                    args=[cst.Arg(value=cst.SimpleString(f'\'{field_name}\''))],
                ),
                comma=cst.MaybeSentinel.DEFAULT if is_last else cst.Comma(
                    whitespace_after=cst.ParenthesizedWhitespace(
                        first_line=cst.TrailingWhitespace(
                            whitespace=cst.SimpleWhitespace(''),
                            newline=cst.Newline('\n'),
                        ),
                        empty_lines=[],
                        indent=True,
                        last_line=cst.SimpleWhitespace(indent_str),
                    ),),
            )
            new_args.append(new_arg)

        if not new_args:
            return updated_node

        # Update last existing arg to have proper newline in its comma
        all_args = list(updated_node.args)
        if all_args and all_args[-1].comma:
            last_arg = all_args[-1]
            all_args[-1] = last_arg.with_changes(comma=cst.Comma(
                whitespace_after=cst.ParenthesizedWhitespace(
                    first_line=cst.TrailingWhitespace(
                        whitespace=cst.SimpleWhitespace(''),
                        newline=cst.Newline('\n'),
                    ),
                    empty_lines=[],
                    indent=True,
                    last_line=cst.SimpleWhitespace(indent_str),
                ),),)

        # Append new args to existing args
        updated_args: Sequence[cst.Arg] = all_args + new_args
        return updated_node.with_changes(args=updated_args)


def extract_managed_job_record_fields() -> set[str]:
    """Extract fields from ManagedJobRecord class."""
    with open('sky/schemas/api/responses.py', 'r') as f:
        source_tree = cst.parse_module(f.read())

    visitor = ManagedJobRecordFieldsVisitor()
    source_tree.visit(visitor)
    return visitor.fields


def extract_grpc_managed_job_info_fields() -> set[str]:
    """Extract fields from ManagedJobInfo protobuf message."""
    return {
        field.name
        for field in managed_jobsv1_pb2.ManagedJobInfo.DESCRIPTOR.fields
    }


def main():
    api_fields = extract_managed_job_record_fields()
    grpc_fields = extract_grpc_managed_job_info_fields()

    missing_from_api = grpc_fields - api_fields
    assert not missing_from_api, f'Fields only in ManagedJobInfo: {sorted(missing_from_api)}'
    missing_from_grpc = api_fields - grpc_fields

    if not missing_from_api and not missing_from_grpc:
        print('✓ Field sets are identical!')
        exit(0)

    print(
        f'The following fields are missing from ManagedJobInfo gRPC proto: {sorted(missing_from_grpc)}'
    )
    print()
    print('===== To fix this issue =====')
    print()
    print('1. Add the missing fields to sky/schemas/proto/managed_jobsv1.proto')
    print('2. Regenerate the Python protobuf files by running:')
    print('python -m grpc_tools.protoc \\')
    print('  --proto_path=sky/schemas/generated=sky/schemas/proto \\')
    print('  --python_out=. \\')
    print('  --grpc_python_out=. \\')
    print('  --pyi_out=. \\')
    print('  sky/schemas/proto/*.proto')
    print('3. Update GetJobTable in sky/skylet/services.py:')

    # Apply transformer to services.py if there are missing fields
    if missing_from_grpc:
        with open('sky/skylet/services.py', 'r') as f:
            source_code = f.read()
            source_tree = cst.parse_module(source_code)

        transformer = ManagedJobInfoTransformer(missing_from_grpc, source_tree)
        modified_tree = source_tree.visit(transformer)

        # Generate diff
        diff_lines = difflib.unified_diff(
            source_code.splitlines(keepends=True),
            modified_tree.code.splitlines(keepends=True),
            fromfile='sky/skylet/services.py',
            tofile='sky/skylet/services.py',
            lineterm='',
        )
        diff_output = ''.join(diff_lines)
        assert diff_output, 'No changes needed.'
        print(diff_output)
        exit(1)


if __name__ == '__main__':
    main()
