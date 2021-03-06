# -*- coding: utf-8 -*-

"""The KwikModel class manages in-memory structures and KWIK file open/save."""

#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import os.path as op

import numpy as np

from ..ext import six
from .base_model import BaseModel
from ..cluster.manual.cluster_info import ClusterMetadata
from .h5 import open_h5, _check_hdf5_path
from ..waveform.loader import WaveformLoader
from ..waveform.filter import bandpass_filter, apply_filter
from ..electrode.mea import MEA, linear_positions
from ..utils.logging import debug
from ..utils.array import PartialArray


#------------------------------------------------------------------------------
# Kwik utility functions
#------------------------------------------------------------------------------

def _to_int_list(l):
    """Convert int strings to ints."""
    return [int(_) for _ in l]


def _list_int_children(group):
    """Return the list of int children of a HDF5 group."""
    return sorted(_to_int_list(group.keys()))


def _list_channel_groups(kwik):
    """Return the list of channel groups in a kwik file."""
    if 'channel_groups' in kwik:
        return _list_int_children(kwik['/channel_groups'])
    else:
        return []


def _list_recordings(kwik):
    """Return the list of recordings in a kwik file."""
    if '/recordings' in kwik:
        return _list_int_children(kwik['/recordings'])
    else:
        return []


def _list_channels(kwik, channel_group=None):
    """Return the list of channels in a kwik file."""
    assert isinstance(channel_group, six.integer_types)
    path = '/channel_groups/{0:d}/channels'.format(channel_group)
    if path in kwik:
        channels = _list_int_children(kwik[path])
        return channels
    else:
        return []


def _list_clusterings(kwik, channel_group=None):
    """Return the list of clusterings in a kwik file."""
    if channel_group is None:
        raise RuntimeError("channel_group must be specified when listing "
                           "the clusterings.")
    assert isinstance(channel_group, six.integer_types)
    path = '/channel_groups/{0:d}/clusters'.format(channel_group)
    clusterings = sorted(kwik[path].keys())
    # Ensure 'main' exists and is the first.
    assert 'main' in clusterings
    clusterings.remove('main')
    return ['main'] + clusterings


_COLOR_MAP = np.array([[1., 1., 1.],
                       [1., 0., 0.],
                       [0.5, 0.763, 1.],
                       [0.105, 1., 0.],
                       [1., 0.658, 0.5],
                       [0.421, 0., 1.],
                       [0.5, 1., 0.763],
                       [1., 0.947, 0.],
                       [1., 0.5, 0.974],
                       [0., 0.526, 1.],
                       [0.868, 1., 0.5],
                       [1., 0.316, 0.],
                       [0.553, 0.5, 1.],
                       [0., 1., 0.526],
                       [1., 0.816, 0.5],
                       [1., 0., 0.947],
                       [0.5, 1., 0.921],
                       [0.737, 1., 0.],
                       [1., 0.5, 0.5],
                       [0.105, 0., 1.],
                       [0.553, 1., 0.5],
                       [1., 0.632, 0.],
                       [0.711, 0.5, 1.],
                       [0., 1., 0.842],
                       [1., 0.974, 0.5],
                       [0.9, 0., 0.],
                       [0.45, 0.687, 0.9],
                       [0.095, 0.9, 0.],
                       [0.9, 0.592, 0.45],
                       [0.379, 0., 0.9],
                       [0.45, 0.9, 0.687],
                       [0.9, 0.853, 0.],
                       [0.9, 0.45, 0.876],
                       [0., 0.474, 0.9],
                       [0.782, 0.9, 0.45],
                       [0.9, 0.284, 0.],
                       [0.497, 0.45, 0.9],
                       [0., 0.9, 0.474],
                       [0.9, 0.734, 0.45],
                       [0.9, 0., 0.853],
                       [0.45, 0.9, 0.829],
                       [0.663, 0.9, 0.],
                       [0.9, 0.45, 0.45],
                       [0.095, 0., 0.9],
                       [0.497, 0.9, 0.45],
                       [0.9, 0.568, 0.],
                       [0.639, 0.45, 0.9],
                       [0., 0.9, 0.758],
                       [0.9, 0.876, 0.45]])


_KWIK_EXTENSIONS = ('kwik', 'kwx', 'raw.kwd')


def _kwik_filenames(filename):
    """Return the filenames of the different Kwik files for a given
    experiment."""
    basename, ext = op.splitext(filename)
    return {ext: '{basename}.{ext}'.format(basename=basename, ext=ext)
            for ext in _KWIK_EXTENSIONS}


class SpikeLoader(object):
    """Translate selection with spike ids into selection with
    absolute times."""
    def __init__(self, waveforms, spike_times):
        self._spike_times = spike_times
        self._waveforms = waveforms

    def __getitem__(self, item):
        times = self._spike_times[item]
        return self._waveforms[times]


#------------------------------------------------------------------------------
# KwikModel class
#------------------------------------------------------------------------------

class KwikModel(BaseModel):
    """Holds data contained in a kwik file."""
    def __init__(self, filename=None,
                 channel_group=None,
                 recording=None,
                 clustering=None):
        super(KwikModel, self).__init__()

        # Initialize fields.
        self._spike_times = None
        self._spike_clusters = None
        self._metadata = None
        self._clustering = 'main'
        self._probe = None
        self._channels = []
        self._features = None
        self._masks = None
        self._waveforms = None
        self._cluster_metadata = None
        self._traces = None
        self._waveform_loader = None

        if filename is None:
            raise ValueError("No filename specified.")

        # Open the file.
        self.name = op.splitext(op.basename(filename))[0]
        self._kwik = open_h5(filename)
        if not self._kwik.is_open():
            raise ValueError("File {0} failed to open.".format(filename))

        # This class only works with kwik version 2 for now.
        kwik_version = self._kwik.read_attr('/', 'kwik_version')
        if kwik_version != 2:
            raise IOError("The kwik version is {v} != 2.".format(kwik_version))

        # Open the Kwx file if it exists.
        filenames = _kwik_filenames(filename)
        if op.exists(filenames['kwx']):
            self._kwx = open_h5(filenames['kwx'])
        else:
            self._kwx = None

        # Open the Kwd file if it exists.
        if op.exists(filenames['raw.kwd']):
            self._kwd = open_h5(filenames['raw.kwd'])
        else:
            self._kwd = None

        # Load global information about the file.
        self._load_meta()

        # List channel groups and recordings.
        self._channel_groups = _list_channel_groups(self._kwik.h5py_file)
        self._recordings = _list_recordings(self._kwik.h5py_file)

        # Choose the default channel group if not specified.
        if channel_group is None and self.channel_groups:
            channel_group = self.channel_groups[0]
        # Load the channel group.
        self.channel_group = channel_group

        # Choose the default recording if not specified.
        if recording is None and self.recordings:
            recording = self.recordings[0]
        # Load the recording.
        self.recording = recording

        # Once the channel group is loaded, list the clusterings.
        self._clusterings = _list_clusterings(self._kwik.h5py_file,
                                              self.channel_group)
        # Choose the first clustering (should always be 'main').
        if clustering is None and self.clusterings:
            clustering = self.clusterings[0]
        # Load the specified clustering.
        self.clustering = clustering

    # Internal properties and methods
    # -------------------------------------------------------------------------

    @property
    def _channel_groups_path(self):
        return '/channel_groups/{0:d}'.format(self._channel_group)

    @property
    def _spikes_path(self):
        return '{0:s}/spikes'.format(self._channel_groups_path)

    @property
    def _channels_path(self):
        return '{0:s}/channels'.format(self._channel_groups_path)

    @property
    def _clusters_path(self):
        return '{0:s}/clusters'.format(self._channel_groups_path)

    @property
    def _clustering_path(self):
        return '{0:s}/{1:s}'.format(self._clusters_path, self._clustering)

    def _load_meta(self):
        """Load metadata from kwik file."""
        metadata = {}
        # Automatically load all metadata from spikedetekt group.
        path = '/application_data/spikedetekt/'
        metadata_fields = self._kwik.attrs(path)
        for field in metadata_fields:
            if field.islower():
                try:
                    metadata[field] = self._kwik.read_attr(path, field)
                except TypeError:
                    debug("Unable to load metadata field {0:s}".format(field))
        self._metadata = metadata

    # Channel group
    # -------------------------------------------------------------------------

    @property
    def channel_groups(self):
        return self._channel_groups

    def _channel_group_changed(self, value):
        """Called when the channel group changes."""
        if value not in self.channel_groups:
            raise ValueError("The channel group {0} is invalid.".format(value))
        self._channel_group = value

        # Load channels.
        self._channels = _list_channels(self._kwik.h5py_file,
                                        self._channel_group)

        # Load spike times.
        path = '{0:s}/time_samples'.format(self._spikes_path)
        self._spike_times = self._kwik.read(path)[:]

        # Load features masks.
        path = '{0:s}/features_masks'.format(self._channel_groups_path)

        if self._kwx is not None:
            fm = self._kwx.read(path)
            self._features = PartialArray(fm, 0)

            # TODO: sparse, memory mapped, memcache, etc.
            k = self._metadata['nfeatures_per_channel']
            # This partial array simulates a (n_spikes, n_channels) array.
            self._masks = PartialArray(fm,
                                       (slice(0, k * self.n_channels, k), 1))
            assert self._masks.shape == (self.n_spikes, self.n_channels)

        self._cluster_metadata = ClusterMetadata()

        @self._cluster_metadata.default
        def group(cluster):
            return 3

        # Load probe.
        positions = self._load_channel_positions()

        # TODO: support multiple channel groups.
        self._probe = MEA(positions=positions,
                          n_channels=self.n_channels)

        self._create_waveform_loader()

    def _load_channel_positions(self):
        """Load the channel positions from the kwik file."""
        positions = []
        for channel in self.channels:
            path = '{0:s}/{1:d}'.format(self._channels_path, channel)
            position = self._kwik.read_attr(path, 'position')
            positions.append(position)
        return np.array(positions)

    def _create_waveform_loader(self):
        """Create a waveform loader."""
        n_samples = (self._metadata['extract_s_before'],
                     self._metadata['extract_s_after'])
        order = self._metadata['filter_butter_order']
        b_filter = bandpass_filter(rate=self._metadata['sample_rate'],
                                   low=self._metadata['filter_low'],
                                   high=self._metadata['filter_high'],
                                   order=order)

        def filter(x):
            return apply_filter(x, b_filter)

        self._waveform_loader = WaveformLoader(n_samples=n_samples,
                                               channels=self._channels,
                                               filter=filter,
                                               filter_margin=order * 3,
                                               scale_factor=.01)

    @property
    def channels(self):
        """List of channels in the current channel group."""
        return self._channels

    @property
    def n_channels(self):
        """Number of channels in the current channel group."""
        return len(self._channels)

    @property
    def recordings(self):
        return self._recordings

    def _recording_changed(self, value):
        """Called when the recording number changes."""
        if value not in self.recordings:
            raise ValueError("The recording {0} is invalid.".format(value))
        self._recording = value
        # Traces.
        if self._kwd is not None:
            path = '/recordings/{0:d}/data'.format(self._recording)
            self._traces = self._kwd.read(path)
            # Create a new WaveformLoader if needed.
            if self._waveform_loader is None:
                self._create_waveform_loader()
            self._waveform_loader.traces = self._traces

    @property
    def clusterings(self):
        return self._clusterings

    def _clustering_changed(self, value):
        """Called when the clustering changes."""
        if value not in self.clusterings:
            raise ValueError("The clustering {0} is invalid.".format(value))
        self._clustering = value
        # NOTE: we are ensured here that self._channel_group is valid.
        path = '{0:s}/clusters/{1:s}'.format(self._spikes_path,
                                             self._clustering)
        self._spike_clusters = self._kwik.read(path)[:]
        # TODO: cluster metadata

    # Data
    # -------------------------------------------------------------------------

    @property
    def _clusters(self):
        """List of clusters in the Kwik file."""
        clusters = self._kwik.groups(self._clustering_path)
        clusters = [int(cluster) for cluster in clusters]
        return sorted(clusters)

    @property
    def metadata(self):
        """A dictionary holding metadata about the experiment."""
        return self._metadata

    @property
    def probe(self):
        """A Probe instance."""
        return self._probe

    @property
    def traces(self):
        """Traces from the current recording (may be memory-mapped)."""
        return self._traces

    @property
    def spike_times(self):
        """Spike times from the current channel_group."""
        return self._spike_times

    @property
    def n_spikes(self):
        """Return the number of spikes."""
        return len(self._spike_times)

    @property
    def features(self):
        """Features from the current channel_group (may be memory-mapped)."""
        return self._features

    @property
    def masks(self):
        """Masks from the current channel_group (may be memory-mapped)."""
        return self._masks

    @property
    def waveforms(self):
        """Waveforms from the current channel_group (may be memory-mapped)."""
        return SpikeLoader(self._waveform_loader, self.spike_times)

    @property
    def spike_clusters(self):
        """Spike clusters from the current channel_group."""
        return self._spike_clusters

    @property
    def cluster_metadata(self):
        """ClusterMetadata instance holding information about the clusters."""
        # TODO
        return self._cluster_metadata

    def save(self):
        """Commits all in-memory changes to disk."""
        raise NotImplementedError()

    def close(self):
        """Close all opened files."""
        if self._kwx is not None:
            self._kwx.close()
        if self._kwd is not None:
            self._kwd.close()
        self._kwik.close()
