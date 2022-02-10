"""Utilities shared by skylet and sky."""
import prettytable


def create_table(field_names):
    """Creates table with default style."""
    table = prettytable.PrettyTable()
    table.field_names = field_names
    table.border = False
    table.left_padding_width = 0
    table.right_padding_width = 2
    table.align = 'l'
    return table
