# -*- coding: utf-8 -*-

"""Common interacts."""


#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import math

import numpy as np
from vispy.gloo import Texture2D

from .base import BaseInteract
from .transform import Scale, Range, Subplot, Clip, NDC
from .utils import _get_texture, _get_boxes, _get_box_pos_size


#------------------------------------------------------------------------------
# Grid interact
#------------------------------------------------------------------------------

class Grid(BaseInteract):
    """Grid interact.

    NOTE: to be used in a grid, a visual must define `a_box_index`
    (by default) or another GLSL variable specified in `box_var`.

    Parameters
    ----------

    shape : tuple or str
        Number of rows, cols in the grid.
    box_var : str
        Name of the GLSL variable with the box index.

    """

    def __init__(self, shape, box_var=None):
        self._zoom = 1.

        # Name of the variable with the box index.
        self.box_var = box_var or 'a_box_index'

        self.shape = shape
        if isinstance(self.shape, tuple):
            assert len(self.shape) == 2
            assert self.shape[0] >= 1
            assert self.shape[1] >= 1

    def attach(self, canvas):
        super(Grid, self).attach(canvas)
        m = 1. - .05  # Margin.
        canvas.transforms.add_on_gpu([Scale('u_grid_zoom'),
                                      Scale((m, m)),
                                      Clip([-m, -m, m, m]),
                                      Subplot(self.shape, 'a_box_index'),
                                      ])
        canvas.inserter.insert_vert("""
                                    attribute vec2 a_box_index;
                                    //uniform float u_grid_shape;
                                    uniform float u_grid_zoom;
                                    """, 'header')
        canvas.connect(self.on_key_press)

    def update_program(self, program):
        # program['u_grid_shape'] = self.shape
        program['u_grid_zoom'] = self._zoom
        # Only set the default box index if necessary.
        try:
            program['a_box_index']
        except KeyError:
            program['a_box_index'] = (0, 0)

    @property
    def zoom(self):
        """Zoom level."""
        return self._zoom

    @zoom.setter
    def zoom(self, value):
        """Zoom level."""
        self._zoom = value
        self.update()

    def on_key_press(self, event):
        """Pan and zoom with the keyboard."""
        key = event.key

        # Zoom.
        if key in ('-', '+') and event.modifiers == ('Control',):
            k = .05 if key == '+' else -.05
            self.zoom *= math.exp(1.5 * k)
            self.update()

        # Reset with 'R'.
        if key == 'R':
            self.zoom = 1.
            self.update()


#------------------------------------------------------------------------------
# Boxed interact
#------------------------------------------------------------------------------

class Boxed(BaseInteract):
    """Boxed interact.

    NOTE: to be used in a boxed, a visual must define `a_box_index`
    (by default) or another GLSL variable specified in `box_var`.

    Parameters
    ----------

    box_bounds : array-like
        A (n, 4) array where each row contains the `(xmin, ymin, xmax, ymax)`
        bounds of every box, in normalized device coordinates.

        NOTE: the box bounds need to be contained within [-1, 1] at all times,
        otherwise an error will be raised. This is to prevent silent clipping
        of the values when they are passed to a gloo Texture2D.

    box_var : str
        Name of the GLSL variable with the box index.

    """
    def __init__(self,
                 box_bounds=None,
                 box_pos=None,
                 box_size=None,
                 box_var=None):
        self._key_pressed = None

        # Name of the variable with the box index.
        self.box_var = box_var or 'a_box_index'

        # Find the box bounds if only the box positions are passed.
        if box_bounds is None:
            assert box_pos is not None
            # This will find a good box size automatically if it is not
            # specified.
            box_bounds = _get_boxes(box_pos, size=box_size)

        self._box_bounds = np.atleast_2d(box_bounds)
        assert self._box_bounds.shape[1] == 4

        self.n_boxes = len(self._box_bounds)

    def attach(self, canvas):
        super(Boxed, self).attach(canvas)
        canvas.transforms.add_on_gpu([Range(NDC, 'box_bounds')])
        canvas.inserter.insert_vert("""
            #include "utils.glsl"
            attribute float {};
            uniform sampler2D u_box_bounds;
            uniform float n_boxes;""".format(self.box_var), 'header')
        canvas.inserter.insert_vert("""
            // Fetch the box bounds for the current box (`box_var`).
            vec4 box_bounds = fetch_texture({},
                                            u_box_bounds,
                                            n_boxes);
            box_bounds = (2 * box_bounds - 1);  // See hack in Python.
            """.format(self.box_var), 'before_transforms')
        canvas.connect(self.on_key_press)
        canvas.connect(self.on_key_release)

    def update_program(self, program):
        # Signal bounds (positions).
        box_bounds = _get_texture(self._box_bounds, NDC, self.n_boxes, [-1, 1])
        program['u_box_bounds'] = Texture2D(box_bounds,
                                            internalformat='rgba32f')
        program['n_boxes'] = self.n_boxes

    # Change the box bounds, positions, or size
    #--------------------------------------------------------------------------

    @property
    def box_bounds(self):
        return self._box_bounds

    @box_bounds.setter
    def box_bounds(self, val):
        assert val.shape == (self.n_boxes, 4)
        self._box_bounds = val
        self.update()

    @property
    def box_pos(self):
        box_pos, _ = _get_box_pos_size(self._box_bounds)
        return box_pos

    @box_pos.setter
    def box_pos(self, val):
        assert val.shape == (self.n_boxes, 2)
        self.box_bounds = _get_boxes(val, size=self.box_size)

    @property
    def box_size(self):
        _, box_size = _get_box_pos_size(self._box_bounds)
        return box_size

    @box_size.setter
    def box_size(self, val):
        assert len(val) == 2
        self.box_bounds = _get_boxes(self.box_pos, size=val)

    # Interaction event callbacks
    #--------------------------------------------------------------------------

    _arrows = ('Left', 'Right', 'Up', 'Down')
    _pm = ('+', '-')

    def on_key_press(self, event):
        """Handle key press events."""
        key = event.key

        self._key_pressed = key

        ctrl = 'Control' in event.modifiers
        shift = 'Shift' in event.modifiers

        # Box scale.
        if ctrl and key in self._arrows + self._pm:
            coeff = 1.1
            box_size = np.array(self.box_size)
            if key == 'Left':
                box_size[0] /= coeff
            elif key == 'Right':
                box_size[0] *= coeff
            elif key in ('Down', '-'):
                box_size[1] /= coeff
            elif key in ('Up', '+'):
                box_size[1] *= coeff
            self.box_size = box_size

        # Probe scale.
        if shift and key in self._arrows:
            coeff = 1.1
            box_pos = self.box_pos
            if key == 'Left':
                box_pos[:, 0] /= coeff
            elif key == 'Right':
                box_pos[:, 0] *= coeff
            elif key == 'Down':
                box_pos[:, 1] /= coeff
            elif key == 'Up':
                box_pos[:, 1] *= coeff
            self.box_pos = box_pos

    def on_key_release(self, event):
        self._key_pressed = None  # pragma: no cover


class Stacked(Boxed):
    """Stacked interact.

    NOTE: to be used in a stacked, a visual must define `a_box_index`
    (by default) or another GLSL variable specified in `box_var`.

    Parameters
    ----------

    n_boxes : int
        Number of boxes to stack vertically.
    margin : int (0 by default)
        The margin between the stacked subplots. Can be negative. Must be
        between -1 and 1. The unit is relative to each box's size.
    box_var : str
        Name of the GLSL variable with the box index.

    """
    def __init__(self, n_boxes, margin=0, box_var=None):

        # The margin must be in [-1, 1]
        margin = np.clip(margin, -1, 1)
        # Normalize the margin.
        margin = 2. * margin / float(n_boxes)

        # Signal bounds.
        b = np.zeros((n_boxes, 4))
        b[:, 0] = -1
        b[:, 1] = np.linspace(-1, 1 - 2. / n_boxes + margin, n_boxes)
        b[:, 2] = 1
        b[:, 3] = np.linspace(-1 + 2. / n_boxes - margin, 1., n_boxes)
        b = b[::-1, :]

        super(Stacked, self).__init__(b, box_var=box_var)
