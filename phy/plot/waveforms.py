# -*- coding: utf-8 -*-

"""Plotting waveforms."""


#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import numpy as np

from vispy import gloo
from vispy.gloo import Texture2D
from vispy.visuals import Visual
from vispy.visuals.shaders import ModularProgram, Function, Variable
from vispy.visuals.glsl.color import HSV_TO_RGB, RGB_TO_HSV

from ._vispy_utils import PanZoomCanvas
from ..utils.array import _unique, _as_array, _index_of, _normalize
from ..utils.logging import debug
from ..utils._color import _random_color


#------------------------------------------------------------------------------
# Waveforms visual
#------------------------------------------------------------------------------

class Waveforms(Visual):
    # TODO: use ST instead of PanZoom
    # TODO: move GLSL code to .glsl files.
    VERT_SHADER = """
    // TODO: add depth
    attribute vec2 a_data;  // -1..1
    attribute float a_time;  // -1..1
    attribute vec2 a_box;  // 0..(n_clusters-1, n_channels-1)

    uniform float n_clusters;
    uniform float n_channels;
    uniform vec2 u_data_scale;
    uniform sampler2D u_channel_pos;
    uniform sampler2D u_cluster_color;

    varying vec4 v_color;
    varying vec2 v_box;

    // TODO: use VisPy transforms
    vec2 get_box_pos(vec2 box) {  // box = (cluster, channel)
        vec2 box_pos = texture2D(u_channel_pos,
                                 vec2(box.y / (n_channels - 1.), .5)).xy;
        box_pos = 2. * box_pos - 1.;
        // Spacing between cluster boxes.
        float h = 2.5 * u_data_scale.x;
        // TODO: add superposition
        box_pos.x += h * (box.x - .5 * (n_clusters - 1.));
        return box_pos;
    }

    vec3 get_color(float cluster) {
        return texture2D(u_cluster_color,
                         vec2(cluster / (n_clusters - 1.), .5)).xyz;
    }

    void main() {
        vec2 pos = u_data_scale * vec2(a_time, a_data.x);  // -1..1
        vec2 box_pos = get_box_pos(a_box);
        v_box = a_box;
        gl_Position = vec4($transform(pos + box_pos), 0., 1.);

        // Compute the waveform color as a function of the cluster color
        // and the mask.
        float mask = a_data.y;
        // TODO: store the colors in HSV in the texture?
        vec3 rgb = get_color(a_box.x);
        vec3 hsv = $rgb_to_hsv(rgb);
        // Change the saturation and value as a function of the mask.
        hsv.y = mask;
        hsv.z = .5 * (1. + mask);
        v_color.rgb = $hsv_to_rgb(hsv);
        v_color.a = .5;
    }
    """

    FRAG_SHADER = """
    varying vec4 v_color;
    varying vec2 v_box;

    void main() {
        if ((fract(v_box.x) > 0.) || (fract(v_box.y) > 0.))
            discard;
        gl_FragColor = v_color;
    }
    """

    def __init__(self, **kwargs):
        super(Waveforms, self).__init__(**kwargs)
        self.n_spikes, self.n_channels, self.n_samples = None, None, None
        self._spike_clusters = None
        self._waveforms = None
        self._spike_ids = None
        self._to_bake = []

        self.program = ModularProgram(self.VERT_SHADER, self.FRAG_SHADER)
        self.program.vert['rgb_to_hsv'] = Function(RGB_TO_HSV)
        self.program.vert['hsv_to_rgb'] = Function(HSV_TO_RGB)
        self.program['u_data_scale'] = (.05, .03)

        gloo.set_state(clear_color='black', blend=True,
                       blend_func=('src_alpha', 'one_minus_src_alpha'))

    # Data properties
    # -------------------------------------------------------------------------

    def _set_or_assert_n_spikes(self, arr):
        """If n_spikes is None, set it using the array's shape. Otherwise,
        check that the array has n_spikes rows."""
        if self.n_spikes is None:
            self.n_spikes = arr.shape[0]
        assert arr.shape[0] == self.n_spikes

    def set_to_bake(self, *bakes):
        for bake in bakes:
            if bake not in self._to_bake:
                self._to_bake.append(bake)

    @property
    def spike_clusters(self):
        """The clusters assigned to *all* spikes, not just the displayed
        spikes."""
        return self._spike_clusters

    @spike_clusters.setter
    def spike_clusters(self, value):
        """Set all spike clusters."""
        value = _as_array(value)
        self._spike_clusters = value
        self.set_to_bake('spikes_clusters')

    @property
    def waveforms(self):
        """Displayed waveforms."""
        return self._waveforms

    @waveforms.setter
    def waveforms(self, value):
        # WARNING: when setting new data, waveforms need to be set first.
        # n_spikes will be set as a function of waveforms.
        value = _as_array(value)
        # TODO: support sparse structures
        assert value.ndim == 3
        self.n_spikes, self.n_samples, self.n_channels = value.shape
        self._waveforms = value
        self.set_to_bake('spikes', 'spikes_clusters', 'color')

    @property
    def masks(self):
        """Masks of the displayed waveforms."""
        return self._masks

    @masks.setter
    def masks(self, value):
        value = _as_array(value)
        self._set_or_assert_n_spikes(value)
        # TODO: support sparse structures
        assert value.ndim == 2
        assert value.shape == (self.n_spikes, self.n_channels)
        self._masks = value
        self.set_to_bake('spikes')

    @property
    def spike_ids(self):
        """The list of spike ids to display, should correspond to the
        waveforms."""
        if self._spike_ids is None:
            self._spike_ids = np.arange(self.n_spikes).astype(np.int64)
        return self._spike_ids

    @spike_ids.setter
    def spike_ids(self, value):
        value = _as_array(value)
        self._set_or_assert_n_spikes(value)
        self._spike_ids = value
        self.set_to_bake('spikes')

    @property
    def channel_positions(self):
        """Array with the coordinates of all channels."""
        return self._channel_positions

    @channel_positions.setter
    def channel_positions(self, value):
        value = _as_array(value)
        self._channel_positions = value
        self.set_to_bake('channel_positions')

    @property
    def cluster_ids(self):
        """Clusters of the displayed spikes."""
        return _unique(self.spike_clusters[self.spike_ids])

    @property
    def n_clusters(self):
        return len(self.cluster_ids)

    @property
    def cluster_colors(self):
        """Colors of the displayed clusters."""
        return self._cluster_colors

    @cluster_colors.setter
    def cluster_colors(self, value):
        self._cluster_colors = _as_array(value)
        assert len(self._cluster_colors) == self.n_clusters
        self.set_to_bake('color')

    @property
    def box_scale(self):
        return tuple(self.program['u_data_scale'])

    @box_scale.setter
    def box_scale(self, value):
        assert isinstance(value, tuple) and len(value) == 2
        self.program['u_data_scale'] = value
        self.update()

    # Data baking
    # -------------------------------------------------------------------------

    def _bake_color(self):
        u_cluster_color = self.cluster_colors.reshape((1, self.n_clusters, -1))
        u_cluster_color = (u_cluster_color * 255).astype(np.uint8)
        # TODO: more efficient to update the data from an existing texture
        self.program['u_cluster_color'] = Texture2D(u_cluster_color)
        debug("bake color", u_cluster_color.shape)

    def _bake_channel_positions(self):
        # WARNING: channel_positions must be in [0,1] because we have a
        # texture.
        positions = self.channel_positions.astype(np.float32)
        positions = _normalize(positions, keep_ratio=True)
        positions = positions.reshape((1, self.n_channels, -1))
        # Rescale a bit and recenter.
        positions = .1 + .8 * positions
        u_channel_pos = np.dstack((positions,
                                  np.zeros((1, self.n_channels, 1))))
        u_channel_pos = (u_channel_pos * 255).astype(np.uint8)
        # TODO: more efficient to update the data from an existing texture
        self.program['u_channel_pos'] = Texture2D(u_channel_pos,
                                                  wrapping='clamp_to_edge')
        debug("bake channel pos", u_channel_pos.shape)

    def _bake_spikes(self):

        # Bake masks.
        # WARNING: swap channel/time axes in the waveforms array.
        waveforms = np.swapaxes(self._waveforms, 1, 2)
        masks = np.repeat(self._masks.ravel(), self.n_samples)
        data = np.c_[waveforms.ravel(), masks.ravel()].astype(np.float32)
        # TODO: more efficient to update the data from an existing VBO
        self.program['a_data'] = data
        debug("bake spikes", data.shape)

        # TODO: SparseCSR, this should just be 'channel'
        self._channels_per_spike = np.tile(np.arange(self.n_channels).
                                           astype(np.float32),
                                           self.n_spikes)

        # TODO: SparseCSR, this should be np.diff(spikes_ptr)
        self._n_channels_per_spike = self.n_channels * np.ones(self.n_spikes,
                                                               dtype=np.int32)

        self._n_waveforms = np.sum(self._n_channels_per_spike)

        # TODO: precompute this with a maximum number of waveforms?
        a_time = np.tile(np.linspace(-1., 1., self.n_samples),
                         self._n_waveforms).astype(np.float32)

        self.program['a_time'] = a_time
        self.program['n_clusters'] = self.n_clusters
        self.program['n_channels'] = self.n_channels

    def _bake_spikes_clusters(self):
        # WARNING: needs to be called *after* _bake_spikes().
        if not hasattr(self, '_n_channels_per_spike'):
            raise RuntimeError("'_bake_spikes()' needs to be called before "
                               "'bake_spikes_clusters().")
        # Get the spike cluster indices (between 0 and n_clusters-1).
        spike_clusters_idx = self.spike_clusters[self.spike_ids]
        spike_clusters_idx = _index_of(spike_clusters_idx, self.cluster_ids)
        # Generate the box attribute.
        a_cluster = np.repeat(spike_clusters_idx,
                              self._n_channels_per_spike * self.n_samples)
        a_channel = np.repeat(self._channels_per_spike, self.n_samples)
        a_box = np.c_[a_cluster, a_channel].astype(np.float32)
        # TODO: more efficient to update the data from an existing VBO
        self.program['a_box'] = a_box
        debug("bake spikes clusters", a_box.shape)

    def _bake(self):
        """Prepare and upload the data on the GPU.

        Return whether something has been baked or not.

        """
        if self.n_spikes is None or self.n_spikes == 0:
            return
        n_bake = len(self._to_bake)
        # Bake what needs to be baked.
        # WARNING: the bake functions are called in alphabetical order.
        # Tweak the names if there are dependencies between the functions.
        for bake in sorted(self._to_bake):
            # Name of the private baking method.
            name = '_bake_{0:s}'.format(bake)
            if hasattr(self, name):
                getattr(self, name)()
        self._to_bake = []
        return n_bake > 0

    def draw(self, event):
        """Draw the waveforms."""
        # Bake what needs to be baked at this point.
        self._bake()
        if self.n_spikes is not None and self.n_spikes > 0:
            self.program.draw('line_strip')


class WaveformView(PanZoomCanvas):
    def __init__(self, **kwargs):
        super(WaveformView, self).__init__(**kwargs)
        self.visual = Waveforms()

    def on_key_press(self, event):
        # TODO: more interactivity
        # TODO: keyboard shortcut manager
        super(WaveformView, self).on_key_press(event)
        u, v = self.visual.box_scale
        coeff = 1.1
        if event.key == '+':
            if 'Control' in event.modifiers:
                self.visual.box_scale = (u*coeff, v)
            else:
                self.visual.box_scale = (u, v*coeff)
        if event.key == '-':
            if 'Control' in event.modifiers:
                self.visual.box_scale = (u/coeff, v)
            else:
                self.visual.box_scale = (u, v/coeff)


def add_waveform_view(session, backend=None):
    """Add a waveform view in a session.

    This function binds the session events to the created waveform view.

    The caller needs to show the waveform view explicitly.

    """
    if backend in ('pyqt4', None):
        kwargs = {'always_on_top': True}
    else:
        kwargs = {}
    view = WaveformView(**kwargs)

    @session.connect
    def on_open():
        if session.model is None:
            return
        view.visual.spike_clusters = session.clustering.spike_clusters
        view.visual.channel_positions = session.model.probe.positions
        view.update()

    @session.connect
    def on_cluster(up=None):
        pass
        # TODO: select the merged cluster
        # session.select(merged)

    @session.connect
    def on_select(selector):
        spikes = selector.selected_spikes
        if len(spikes) == 0:
            return
        if view.visual.spike_clusters is None:
            on_open()
        view.visual.waveforms = session.model.waveforms[spikes]
        view.visual.masks = session.model.masks[spikes]
        view.visual.spike_ids = spikes
        # TODO: how to choose cluster colors?
        view.visual.cluster_colors = [_random_color()
                                      for _ in selector.selected_clusters]
        view.update()

    # Unregister the callbacks when the view is closed.
    @view.connect
    def on_close(event):
        session.unconnect(on_open, on_cluster, on_select)

    # TODO: first_draw() event in VisPy view that is emitted when the view
    # is first rendered (first paint event).
    @view.connect
    def on_draw(event):
        if view.visual.spike_clusters is None:
            on_open()
            on_select(session.selector)

    return view
