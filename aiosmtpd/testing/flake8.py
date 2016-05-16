# Copyright (C) 2016 Barry Warsaw
#
# This project is licensed under the terms of the Apache 2.0 License.  See
# LICENSE.txt for details.

"""Flake8 extensions for Barry's coding style."""


from ast import NodeVisitor
from collections import namedtuple
from enum import Enum


class ImportType(Enum):
    non_from = 0
    from_import = 1


ImportRecord = namedtuple('ImportRecord', 'itype lineno colno, module, names')


NONFROM_FOLLOWS_FROM = 'B401 Non-from import follows from-import'
NONFROM_MULTIPLE_NAMES = 'B402 Multiple names on non-from import'
NONFROM_SHORTER_FOLLOWS = 'B403 Shorter non-from import follows longer'
NONFROM_ALPHA_UNSORTED = (
    'B404 Same-length non-from imports not sorted alphabetically')
NONFROM_EXTRA_BLANK_LINE = (
    'B405 Unexpected blank line since last non-from import')
NONFROM_DOTTED_UNSORTED = (
    'B406 Dotted non-from import not sorted alphabetically')

FROMIMPORT_MISSING_BLANK_LINE = (
    'B411 Expected one blank line since last non-from import')
FROMIMPORT_ALPHA_UNSORTED = 'B412 from-import not sorted alphabetically'
FROMIMPORT_MULTIPLE = 'B413 Multiple from-imports of same module'
FROMIMPORT_NAMES_UNSORTED = (
    'B414 from-imported names are not sorted alphabetically')


class ImportVisitor(NodeVisitor):
    def __init__(self):
        self.imports = []

    def visit_Import(self, node):
        if node.col_offset != 0:
            # Ignore nested imports.
            return
        names = [alias.name for alias in node.names]
        self.imports.append(
            ImportRecord(ImportType.non_from, node.lineno, node.col_offset,
                         None, names))

    def visit_ImportFrom(self, node):
        if node.col_offset != 0:
            # Ignore nested imports.
            return
        names = [alias.name for alias in node.names]
        self.imports.append(
            ImportRecord(ImportType.from_import, node.lineno, node.col_offset,
                         node.module, names))


class ImportOrder:
    name = 'flufl-import-order'
    version = '0.1'

    def __init__(self, tree, filename):
        self.tree = tree
        self.filename = filename

    def _error(self, record, error):
        code, space, text = error.partition(' ')
        return (record.lineno, record.colno,
                '{} {}'.format(code, text), ImportOrder)

    def run(self):
        visitor = ImportVisitor()
        visitor.visit(self.tree)
        last_import = None
        for record in visitor.imports:
            if last_import is None:
                last_import = record
                continue
            if record.itype is ImportType.non_from:
                if len(record.names) != 1:
                    yield self._error(record, NONFROM_MULTIPLE_NAMES)
                if last_import.itype is ImportType.from_import:
                    yield self._error(record, NONFROM_FOLLOWS_FROM)
                # Shorter imports should always precede longer import *except*
                # when they are dotted imports and everything but the last
                # path component are the same.  In that case, they should be
                # sorted alphabetically.
                last_name = last_import.names[0]
                this_name = record.names[0]
                if '.' in last_name and '.' in this_name:
                    last_parts = last_name.split('.')
                    this_parts = this_name.split('.')
                    if (last_parts[:-1] == this_parts[:-1] and
                            last_parts[-1] > this_parts[-1]):
                        yield self._error(record, NONFROM_DOTTED_UNSORTED)
                elif len(last_name) > len(this_name):
                    yield self._error(record, NONFROM_SHORTER_FOLLOWS)
                # It's also possible that the imports are the same length, in
                # which case they must be sorted alphabetically.
                if (len(last_import.names[0]) == len(record.names[0]) and
                        last_import.names[0] > record.names[0]):
                    yield self._error(record, NONFROM_ALPHA_UNSORTED)
                if last_import.lineno + 1 != record.lineno:
                    yield self._error(record, NONFROM_DOTTED_UNSORTED)
            else:
                assert record.itype is ImportType.from_import
                if (last_import.itype is ImportType.non_from and
                        record.lineno != last_import.lineno + 2):
                    yield self._error(record, FROMIMPORT_MISSING_BLANK_LINE)
                if last_import.itype is ImportType.non_from:
                    last_import = record
                    continue
                if last_import.module > record.module:
                    yield self._error(record, FROMIMPORT_ALPHA_UNSORTED)
                # All imports from the same module should show up in the same
                # multiline import.
                if last_import.module == record.module:
                    yield self._error(record, FROMIMPORT_MULTIPLE)
                # Check the sort order of the imported names.
                if sorted(record.names) != record.names:
                    yield self._error(record, FROMIMPORT_NAMES_UNSORTED)
                # How to check for no blank lines between from imports?
            # Update the last import.
            last_import = record
