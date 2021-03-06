# -*- coding: utf-8 -*-

"""Cluster metadata structure."""

#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

from collections import defaultdict, OrderedDict, MutableMapping
from copy import deepcopy

from ...utils._color import _random_color
from ...utils._misc import _as_dict, _fun_arg_count, _as_list, _is_list
from ...ext.six import iterkeys, itervalues, iteritems
from ._utils import _unique, _spikes_in_clusters
from ._update_info import UpdateInfo
from ._history import History


#------------------------------------------------------------------------------
# ClusterMetadata class
#------------------------------------------------------------------------------

class ClusterMetadata(object):
    def __init__(self, data=None):
        self._fields = {}
        self._data = defaultdict(dict)
        # Fill the existing values.
        if data is not None:
            self._data.update(data)
        # Keep a deep copy of the original structure for the undo stack.
        self._data_base = deepcopy(self._data)
        # The stack contains (clusters, field, value, update_info) tuples.
        self._undo_stack = History((None, None, None, None))

    def _get_one(self, cluster, field):
        """Return the field value for a cluster, or the default value if it
        doesn't exist."""
        if cluster in self._data:
            if field in self._data[cluster]:
                return self._data[cluster][field]
            elif field in self._fields:
                # Call the default field function.
                return self._fields[field](cluster)
            else:
                return None
        else:
            if field in self._fields:
                return self._fields[field](cluster)
            else:
                return None

    def _get(self, clusters, field):
        if _is_list(clusters):
            return [self._get_one(cluster, field)
                    for cluster in _as_list(clusters)]
        else:
            return self._get_one(clusters, field)

    def _set_one(self, cluster, field, value):
        """Set a field value for a cluster."""
        self._data[cluster][field] = value

    def _set(self, clusters, field, value, add_to_stack=True):
        clusters = _as_list(clusters)
        for cluster in clusters:
            self._set_one(cluster, field, value)
        info = UpdateInfo(description='metadata_' + field,
                          metadata_changed=clusters)
        if add_to_stack:
            self._undo_stack.add((clusters, field, value, info))
        return info

    def default(self, func):
        field = func.__name__
        # Register the decorated function as the default field function.
        self._fields[field] = func
        # Create self.<field>(clusters).
        setattr(self, field, lambda clusters: self._get(clusters, field))
        # Create self.set_<field>(clusters, value).
        setattr(self, 'set_{0:s}'.format(field),
                lambda clusters, value: self._set(clusters, field, value))
        return func

    def undo(self):
        """Undo the last metadata change."""
        args = self._undo_stack.back()
        if args is None:
            return
        self._data = deepcopy(self._data_base)
        for clusters, field, value, _ in self._undo_stack:
            if clusters is not None:
                self._set(clusters, field, value, add_to_stack=False)
        # Return the UpdateInfo instance of the undo action.
        info = args[-1]
        return info

    def redo(self):
        """Redo the next metadata change."""
        args = self._undo_stack.forward()
        if args is None:
            return
        clusters, field, value, info = args
        self._set(clusters, field, value, add_to_stack=False)
        # Return the UpdateInfo instance of the redo action.
        return info
