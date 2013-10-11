"""
The basic field info container resides here.  These classes, code specific and
universal, are the means by which we access fields across YT, both derived and
native.



"""

#-----------------------------------------------------------------------------
# Copyright (c) 2013, yt Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

import types
import inspect
import copy
import itertools

import numpy as np

from yt.funcs import *
from yt.utilities.units import Unit
from yt.data_objects.yt_array import YTArray

class FieldInfoContainer(dict): # Resistance has utility
    """
    This is a generic field container.  It contains a list of potential derived
    fields, all of which know how to act on a data object and return a value.
    This object handles converting units as well as validating the availability
    of a given field.

    """
    fallback = None

    def add_field(self, name, function=None, **kwargs):
        """
        Add a new field, along with supplemental metadata, to the list of
        available fields.  This respects a number of arguments, all of which
        are passed on to the constructor for
        :class:`~yt.data_objects.api.DerivedField`.

        """
        if function == None:
            def create_function(function):
                self[name] = DerivedField(name, function, **kwargs)
                return function
            return create_function
        self[name] = DerivedField(name, function, **kwargs)

    def add_grad(self, field, **kwargs):
        """
        Creates the partial derivative of a given field. This function will
        autogenerate the names of the gradient fields.

        """
        sl = slice(2,None,None)
        sr = slice(None,-2,None)

        def _gradx(f, data):
            grad = data[field][sl,1:-1,1:-1] - data[field][sr,1:-1,1:-1]
            grad /= 2.0*data["dx"].flat[0]
            g = np.zeros(data[field].shape, dtype='float64')
            g[1:-1,1:-1,1:-1] = grad
            return g

        def _grady(f, data):
            grad = data[field][1:-1,sl,1:-1] - data[field][1:-1,sr,1:-1]
            grad /= 2.0*data["dy"].flat[0]
            g = np.zeros(data[field].shape, dtype='float64')
            g[1:-1,1:-1,1:-1] = grad
            return g

        def _gradz(f, data):
            grad = data[field][1:-1,1:-1,sl] - data[field][1:-1,1:-1,sr]
            grad /= 2.0*data["dz"].flat[0]
            g = np.zeros(data[field].shape, dtype='float64')
            g[1:-1,1:-1,1:-1] = grad
            return g

        d_kwargs = kwargs.copy()
        if "display_name" in kwargs: del d_kwargs["display_name"]

        for ax in "xyz":
            if "display_name" in kwargs:
                disp_name = r"%s\_%s" % (kwargs["display_name"], ax)
            else:
                disp_name = r"\partial %s/\partial %s" % (field, ax)
            name = "Grad_%s_%s" % (field, ax)
            self[name] = DerivedField(name, function=eval('_grad%s' % ax),
                         take_log=False, validators=[ValidateSpatial(1,[field])],
                         display_name = disp_name, **d_kwargs)

        def _grad(f, data) :
            a = np.power(data["Grad_%s_x" % field],2)
            b = np.power(data["Grad_%s_y" % field],2)
            c = np.power(data["Grad_%s_z" % field],2)
            norm = np.sqrt(a+b+c)
            return norm

        if "display_name" in kwargs:
            disp_name = kwargs["display_name"]
        else:
            disp_name = r"\Vert\nabla %s\Vert" % (field)
        name = "Grad_%s" % field
        self[name] = DerivedField(name, function=_grad, take_log=False,
                                  display_name = disp_name, **d_kwargs)
        mylog.info("Added new fields: Grad_%s_x, Grad_%s_y, Grad_%s_z, Grad_%s" \
                   % (field, field, field, field))

    def has_key(self, key):
        # This gets used a lot
        if key in self: return True
        if self.fallback is None: return False
        return self.fallback.has_key(key)

    def __missing__(self, key):
        if self.fallback is None:
            raise KeyError("No field named %s" % (key,))
        return self.fallback[key]

    name = ""

    @classmethod
    def create_with_fallback(cls, fallback, name = ""):
        obj = cls()
        obj.fallback = fallback
        obj.name = name
        return obj

    def __contains__(self, key):
        if dict.__contains__(self, key): return True
        if self.fallback is None: return False
        return key in self.fallback

    def __iter__(self):
        for f in dict.__iter__(self):
            yield f
        if self.fallback is not None:
            for f in self.fallback: yield f

    def keys(self):
        keys = dict.keys(self)
        if self.fallback:
            keys += self.fallback.keys()
        return keys

def TranslationFunc(field_name):
    def _TranslationFunc(field, data):
        return data[field_name]
    return _TranslationFunc

def NullFunc(field, data):
    return

FieldInfo = FieldInfoContainer()
FieldInfo.name = id(FieldInfo)
add_field = FieldInfo.add_field
add_grad = FieldInfo.add_grad

def derived_field(**kwargs):
    def inner_decorator(function):
        if 'name' not in kwargs:
            kwargs['name'] = function.func_name
        kwargs['function'] = function
        add_field(**kwargs)
        return function
    return inner_decorator

class ValidationException(Exception):
    pass

class NeedsGridType(ValidationException):
    def __init__(self, ghost_zones = 0, fields=None):
        self.ghost_zones = ghost_zones
        self.fields = fields
    def __str__(self):
        return "(%s, %s)" % (self.ghost_zones, self.fields)

class NeedsOriginalGrid(NeedsGridType):
    def __init__(self):
        self.ghost_zones = 0

class NeedsDataField(ValidationException):
    def __init__(self, missing_fields):
        self.missing_fields = missing_fields
    def __str__(self):
        return "(%s)" % (self.missing_fields)

class NeedsProperty(ValidationException):
    def __init__(self, missing_properties):
        self.missing_properties = missing_properties
    def __str__(self):
        return "(%s)" % (self.missing_properties)

class NeedsParameter(ValidationException):
    def __init__(self, missing_parameters):
        self.missing_parameters = missing_parameters
    def __str__(self):
        return "(%s)" % (self.missing_parameters)

class FieldDetector(defaultdict):
    Level = 1
    NumberOfParticles = 1
    _read_exception = None
    _id_offset = 0

    def __init__(self, nd = 16, pf = None, flat = False):
        self.nd = nd
        self.flat = flat
        self._spatial = not flat
        self.ActiveDimensions = [nd,nd,nd]
        self.shape = tuple(self.ActiveDimensions)
        self.size = np.prod(self.ActiveDimensions)
        self.LeftEdge = [0.0, 0.0, 0.0]
        self.RightEdge = [1.0, 1.0, 1.0]
        self.dds = np.ones(3, "float64")
        class fake_parameter_file(defaultdict):
            pass

        if pf is None:
            # required attrs
            pf = fake_parameter_file(lambda: 1)
            pf["Massarr"] = np.ones(6)
            pf.current_redshift = pf.omega_lambda = pf.omega_matter = \
                pf.cosmological_simulation = 0.0
            pf.hubble_constant = 0.7
            pf.domain_left_edge = np.zeros(3, 'float64')
            pf.domain_right_edge = np.ones(3, 'float64')
            pf.dimensionality = 3
            pf.periodicity = (True, True, True)
        self.pf = pf

        class fake_hierarchy(object):
            class fake_io(object):
                def _read_data_set(io_self, data, field):
                    return self._read_data(field)
                _read_exception = RuntimeError
            io = fake_io()
            def get_smallest_dx(self):
                return 1.0

        self.hierarchy = fake_hierarchy()
        self.requested = []
        self.requested_parameters = []
        if not self.flat:
            defaultdict.__init__(self,
                lambda: np.ones((nd, nd, nd), dtype='float64')
                + 1e-4*np.random.random((nd, nd, nd)))
        else:
            defaultdict.__init__(self,
                lambda: np.ones((nd * nd * nd), dtype='float64')
                + 1e-4*np.random.random((nd * nd * nd)))

    def _reshape_vals(self, arr):
        if not self._spatial: return arr
        if len(arr.shape) == 3: return arr
        return arr.reshape(self.ActiveDimensions, order="C")

    def __missing__(self, item):
        if hasattr(self.pf, "field_info"):
            if not isinstance(item, tuple):
                field = ("unknown", item)
                finfo = self.pf._get_field_info(*field)
                #mylog.debug("Guessing field %s is %s", item, finfo.name)
            else:
                field = item
            finfo = self.pf._get_field_info(*field)
            # For those cases where we are guessing the field type, we will
            # need to re-update -- otherwise, our item will always not have the
            # field type.  This can lead to, for instance, "unknown" particle
            # types not getting correctly identified.
            # Note that the *only* way this works is if we also fix our field
            # dependencies during checking.  Bug #627 talks about this.
            item = self.pf._last_freq
        else:
            FI = getattr(self.pf, "field_info", FieldInfo)
            if item in FI:
                finfo = FI[item]
            else:
                finfo = None
        if finfo is not None and finfo._function.func_name != 'NullFunc':
            try:
                vv = finfo(self)
            except NeedsGridType as exc:
                ngz = exc.ghost_zones
                nfd = FieldDetector(self.nd + ngz * 2, pf = self.pf)
                nfd._num_ghost_zones = ngz
                vv = finfo(nfd)
                if ngz > 0: vv = vv[ngz:-ngz, ngz:-ngz, ngz:-ngz]
                for i in nfd.requested:
                    if i not in self.requested: self.requested.append(i)
                for i in nfd.requested_parameters:
                    if i not in self.requested_parameters:
                        self.requested_parameters.append(i)
            if vv is not None:
                if not self.flat: self[item] = vv
                else: self[item] = vv.ravel()
                return self[item]
        elif finfo is not None and finfo.particle_type:
            if item == "Coordinates" or item[1] == "Coordinates" or \
               item == "Velocities" or item[1] == "Velocities" or \
               item == "Velocity" or item[1] == "Velocity":
                # A vector
                self[item] = \
                  YTArray(np.ones((self.NumberOfParticles, 3)),
                          finfo.units, registry=self.pf.unit_registry)
            else:
                # Not a vector
                self[item] = \
                  YTArray(np.ones(self.NumberOfParticles),
                          finfo.units, registry=self.pf.unit_registry)
            self.requested.append(item)
            return self[item]
        self.requested.append(item)
        if item not in self:
            self[item] = self._read_data(item)
        return self[item]

    def deposit(self, *args, **kwargs):
        return np.random.random((self.nd, self.nd, self.nd))

    def _read_data(self, field_name):
        self.requested.append(field_name)
        if hasattr(self.pf, "field_info"):
            finfo = self.pf._get_field_info(*field_name)
        else:
            finfo = FieldInfo[field_name]
        if finfo.particle_type:
            self.requested.append(field_name)
            return np.ones(self.NumberOfParticles)
        return YTArray(defaultdict.__missing__(self, field_name),
                       input_units=finfo.units,
                       registry=self.pf.unit_registry)

    fp_units = {
        'bulk_velocity' : 'cm/s',
        'center' : 'cm',
        'normal' : ''
        }

    def get_field_parameter(self, param, default = None):
        self.requested_parameters.append(param)
        if param in ['bulk_velocity', 'center', 'normal']:
            return YTArray(np.random.random(3) * 1e-2, self.fp_units[param])
        elif param in ['axis']:
            return 0
        else:
            return 0.0

    _num_ghost_zones = 0
    id = 1

    def has_field_parameter(self, param):
        return True

    @property
    def fcoords(self):
        fc = np.array(np.mgrid[0:1:self.nd*1j,
                               0:1:self.nd*1j,
                               0:1:self.nd*1j])
        if self.flat:
            fc.shape = (self.nd*self.nd*self.nd, 3)
        else:
            fc = fc.transpose()
        return fc

    @property
    def icoords(self):
        ic = np.mgrid[0:self.nd-1:self.nd*1j,
                      0:self.nd-1:self.nd*1j,
                      0:self.nd-1:self.nd*1j]
        if self.flat:
            ic.shape = (self.nd*self.nd*self.nd, 3)
        else:
            ic = ic.transpose()
        return ic

    @property
    def ires(self):
        ir = np.ones(self.nd**3, dtype="int64")
        if not self.flat:
            ir.shape = (self.nd, self.nd, self.nd)
        return ir

    @property
    def fwidth(self):
        fw = np.ones(self.nd**3, dtype="float64") / self.nd
        if not self.flat:
            fw.shape = (self.nd, self.nd, self.nd)
        return fw

class FieldUnitsError(Exception):
    pass

class DerivedField(object):
    """
    This is the base class used to describe a cell-by-cell derived field.

    Parameters
    ----------

    name : str
       is the name of the field.
    function : callable
       A function handle that defines the field.  Should accept
       arguments (field, data)
    units : str
       A plain text string encoding the unit.  Powers must be in
       python syntax (** instead of ^).
    take_log : bool
       Describes whether the field should be logged
    validators : list
       A list of :class:`FieldValidator` objects
    particle_type : bool
       Is this a particle (1D) field?
    vector_field : bool
       Describes the dimensionality of the field.  Currently unused.
    display_field : bool
       Governs its appearance in the dropdowns in Reason
    not_in_all : bool
       Used for baryon fields from the data that are not in all the grids
    display_name : str
       A name used in the plots
    projection_conversion : unit
       which unit should we multiply by in a projection?
    """
    def __init__(self, name, function, units=None,
                 take_log=True, validators=None,
                 particle_type=False, vector_field=False, display_field=True,
                 not_in_all=False, display_name=None,
                 projection_conversion="cm"):
        self.name = name
        self.take_log = take_log
        self.display_name = display_name
        self.not_in_all = not_in_all
        self.display_field = display_field
        self.particle_type = particle_type
        self.vector_field = vector_field

        self._function = function

        if validators:
            self.validators = ensure_list(validators)
        else:
            self.validators = []

        # handle units
        if units is None:
            self.units = ""
        elif isinstance(units, str):
            self.units = units
        elif isinstance(units, Unit):
            self.units = str(units)
        else:
            raise FieldUnitsError("Cannot handle units '%s' (type %s)." \
                                  "Please provide a string or Unit " \
                                  "object." % (units, type(units)) )

    def _copy_def(self):
        dd = {}
        dd['name'] = self.name
        dd['units'] = self.units
        dd['take_log'] = self.take_log
        dd['validators'] = list(self.validators)
        dd['particle_type'] = self.particle_type
        dd['vector_field'] = self.vector_field
        dd['display_field'] = True
        dd['not_in_all'] = self.not_in_all
        dd['display_name'] = self.display_name
        dd['projection_conversion'] = self.projection_conversion
        return dd

    def get_units(self):
        return "unknown"

    def get_projected_units(self):
        return "unknown"

    def check_available(self, data):
        """
        This raises an exception of the appropriate type if the set of
        validation mechanisms are not met, and otherwise returns True.
        """
        for validator in self.validators:
            validator(data)
        # If we don't get an exception, we're good to go
        return True

    def get_dependencies(self, *args, **kwargs):
        """
        This returns a list of names of fields that this field depends on.
        """
        e = FieldDetector(*args, **kwargs)
        if self._function.func_name == '<lambda>':
            e.requested.append(self.name)
        else:
            e[self.name]
        return e

    def __call__(self, data):
        """ Return the value of the field in a given *data* object. """
        ii = self.check_available(data)
        original_fields = data.keys() # Copy
        if self._function is not NullFunc:
            dd = self._function(self, data)
        else:
            raise RuntimeError(
                "Something has gone terribly wrong, _function is NullFunc")
        for field_name in data.keys():
            if field_name not in original_fields:
                del data[field_name]
        return dd

    def get_source(self):
        """
        Return a string containing the source of the function (if possible.)
        """
        return inspect.getsource(self._function)

    def get_label(self, projected=False):
        """
        Return a data label for the given field, inluding units.
        """
        name = self.name
        if self.display_name is not None:
            name = self.display_name

        # Start with the field name
        data_label = r"$\rm{%s}" % name

        # Grab the correct units
        if projected:
            raise NotImplementedError
        else:
            units = self.units
        # Add unit label
        if not units.is_dimensionless:
            data_label += r"\/\/ (%s)" % (units)

        data_label += r"$"
        return data_label

class FieldValidator(object):
    pass

class ValidateParameter(FieldValidator):
    def __init__(self, parameters):
        """
        This validator ensures that the parameter file has a given parameter.
        """
        FieldValidator.__init__(self)
        self.parameters = ensure_list(parameters)
    def __call__(self, data):
        doesnt_have = []
        for p in self.parameters:
            if not data.has_field_parameter(p):
                doesnt_have.append(p)
        if len(doesnt_have) > 0:
            raise NeedsParameter(doesnt_have)
        return True

class ValidateDataField(FieldValidator):
    def __init__(self, field):
        """
        This validator ensures that the output file has a given data field stored
        in it.
        """
        FieldValidator.__init__(self)
        self.fields = ensure_list(field)
    def __call__(self, data):
        doesnt_have = []
        if isinstance(data, FieldDetector): return True
        for f in self.fields:
            if f not in data.hierarchy.field_list:
                doesnt_have.append(f)
        if len(doesnt_have) > 0:
            raise NeedsDataField(doesnt_have)
        return True

class ValidateProperty(FieldValidator):
    def __init__(self, prop):
        """
        This validator ensures that the data object has a given python attribute.
        """
        FieldValidator.__init__(self)
        self.prop = ensure_list(prop)
    def __call__(self, data):
        doesnt_have = []
        for p in self.prop:
            if not hasattr(data,p):
                doesnt_have.append(p)
        if len(doesnt_have) > 0:
            raise NeedsProperty(doesnt_have)
        return True

class ValidateSpatial(FieldValidator):
    def __init__(self, ghost_zones = 0, fields=None):
        """
        This validator ensures that the data handed to the field is of spatial
        nature -- that is to say, 3-D.
        """
        FieldValidator.__init__(self)
        self.ghost_zones = ghost_zones
        self.fields = fields
    def __call__(self, data):
        # When we say spatial information, we really mean
        # that it has a three-dimensional data structure
        #if isinstance(data, FieldDetector): return True
        if not getattr(data, '_spatial', False):
            raise NeedsGridType(self.ghost_zones,self.fields)
        if self.ghost_zones <= data._num_ghost_zones:
            return True
        raise NeedsGridType(self.ghost_zones,self.fields)

class ValidateGridType(FieldValidator):
    def __init__(self):
        """
        This validator ensures that the data handed to the field is an actual
        grid patch, not a covering grid of any kind.
        """
        FieldValidator.__init__(self)
    def __call__(self, data):
        # We need to make sure that it's an actual AMR grid
        if isinstance(data, FieldDetector): return True
        if getattr(data, "_type_name", None) == 'grid': return True
        raise NeedsOriginalGrid()
