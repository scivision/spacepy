#!/usr/bin/env python

"""Support for ISTP-compliant CDFs

The `ISTP metadata standard  <https://spdf.gsfc.nasa.gov/sp_use_of_cdf.html>`_
specifies the interpretation of the attributes in a CDF to describe
relationships between the variables and their physical interpretation.

This module supports that subset of CDFs.

.. rubric:: Classes

.. autosummary::
    :toctree: autosummary  
    :template: clean_class.rst

    FileChecks
    VariableChecks

.. rubric:: Functions

.. autosummary::
    :toctree: autosummary  

    fillval
    format
    get_max
    get_min

"""

import datetime
import math
import os.path
import re
import sys

import numpy
import spacepy.pycdf.const
import spacepy.datamodel


class VariableChecks(object):
    """Tests of a single variable

    All tests return a list of errors (empty if none). :meth:`all`
    will check all tests and concatenate all errors. :meth:`all`
    should be updated with the list of all tests so that
    helper functions aren't called, subtests aren't called twice, etc.
    """

    @classmethod
    def all(cls, v):
        """Call all test functions in this class

        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        callme = (cls.depends, cls.depsize, cls.fieldnam, cls.recordcount,
                  cls.validrange, cls.validscale, cls.validplottype)
        errors = []
        for f in callme:
            errors.extend(f(v))
        return errors

    @classmethod
    def depends(cls, v):
        """Checks that DEPEND and LABL_PTR variables actually exist

        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        return ['{} variable {} missing'.format(a, v.attrs[a])
                for a in v.attrs
                if a.startswith(('DEPEND_', 'LABL_PTR_')) and
                not v.attrs[a] in v.cdf_file]

    @classmethod
    def depsize(cls, v):
        """Checks that DEPEND has same shape as that dim

        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        rv = int(v.rv()) #RV is a leading dimension
        errs = []
        # Check that don't have invalid DEPEND_1
        if v.shape == (0,):
            if 'DEPEND_1' in v.attrs or 'DEPEND_2' in v.attrs:
                errs.append('Do not expect DEPEND_1 or DEPEND_2 in 1 dimensional variable.')
        for i in range(rv, len(v.shape)): #This is index on shape (of var)
            depidx = i + 1 - rv #This is x in  DEPEND_x
            target = v.shape[i]
            if not 'DEPEND_{}'.format(depidx) in v.attrs:
                continue
            d = v.attrs['DEPEND_{}'.format(depidx)]
            if d in v.cdf_file:
                dv = v.cdf_file[d]
            else:
                continue #this is a different error
            #We hope the only weirdness is whether the dependency
            #is constant, or dependent on record. If it's dependent
            #on another dependency, this gets really weird really fast
            # If the dependency is dependent, remove the lower level
            # dependency size from consideration
            # eg. if counts [80,48], depends on energy [80,48],
            # depends on look [80], remove 80 from the view of energy
            # so that we accurately check 48==48.
            # NB: This assumes max of two layers of dependency
            if 'DEPEND_2' in dv.attrs:
                errs.append('Do not expect three layers of dependency.')
                continue
            elif 'DEPEND_1' in dv.attrs:
                dd = dv.attrs['DEPEND_1']
                if dd in v.cdf_file:
                    ddv = v.cdf_file[dd]
                else:
                    continue #this is a different error
                actual = list(dv.shape)
                for ii in actual:
                    if ii in ddv.shape:
                        actual.remove(ii)
                if 'DEPEND_0' in dv.attrs:
                    # record varying
                    dd = dv.attrs['DEPEND_0']
                    if dd[:5] != 'Epoch':
                        errs.append('Expect DEPEND_0 to be Epoch')
                        continue
                    if dd in v.cdf_file:
                        ddv = v.cdf_file[dd]
                    else:
                        continue #this is a different error
                    for ii in actual:
                        if ii in ddv.shape:
                            actual.remove(ii)
                    
                if len(actual) != 1:
                    errs.append('More complicated double dependency than taken into account.')
                    continue
                else:
                    actual = actual[0]
            else:
                actual = dv.shape[int(dv.rv())]
            if target != actual:
                errs.append('Dim {} sized {} but DEPEND_{} {} sized {}'.format(
                    i, target, depidx, d, actual))

        return errs

    @classmethod
    def recordcount(cls, v):
        """Check that the DEPEND_0 has same record count as variable

        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        if not v.rv() or not 'DEPEND_0' in v.attrs:
            return []
        dep0 = v.attrs['DEPEND_0']
        if not dep0 in v.cdf_file: #This is a DIFFERENT error
            return []
        if len(v) != len(v.cdf_file[dep0]):
            return ['{} records; DEPEND_0 {} has {}'.format(
                len(v), dep0, len(v.cdf_file[dep0]))]
        return []

    @classmethod
    def validrange(cls, v):
        """Check that all values are within VALIDMIN/VALIDMAX, or FILLVAL

        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        errs = []
        raw_v = v.cdf_file.raw_var(v.name())
        data = raw_v[...]
        if 'VALIDMIN' in raw_v.attrs:
            idx = data < raw_v.attrs['VALIDMIN']
            if ('FILLVAL' in raw_v.attrs) and (idx.size != 0):
                is_fill = numpy.isclose(data, raw_v.attrs['FILLVAL'])
                idx = numpy.logical_and(idx, numpy.logical_not(is_fill))
            if idx.any():
                errs.append('Value {} at index {} under VALIDMIN {}'.format(
                    ', '.join(str(d) for d in v[...][idx]),
                    ', '.join(str(d) for d in numpy.nonzero(idx)[0]),
                    v.attrs['VALIDMIN']))
            if (raw_v.attrs['VALIDMIN'] < get_min(raw_v.type())) or \
               (raw_v.attrs['VALIDMIN'] > get_max(raw_v.type())):
                errs.append('VALIDMIN ({}) outside data range ({},{}) for {}.'.format(
                    raw_v.attrs['VALIDMIN'], get_min(raw_v.type()), get_max(raw_v.type()),
                    raw_v.name()))
        if 'VALIDMAX' in raw_v.attrs:
            idx = data > raw_v.attrs['VALIDMAX']
            if 'FILLVAL' in raw_v.attrs:
                is_fill = numpy.isclose(data, raw_v.attrs['FILLVAL'])
                idx = numpy.logical_and(idx, numpy.logical_not(is_fill))
            if idx.any():
                errs.append('Value {} at index {} over VALIDMAX {}'.format(
                    ', '.join(str(d) for d in v[...][idx]),
                    ', '.join(str(d) for d in numpy.nonzero(idx)[0]),
                    v.attrs['VALIDMAX']))
            if (raw_v.attrs['VALIDMIN'] < get_min(raw_v.type())) or \
               (raw_v.attrs['VALIDMAX'] > get_max(raw_v.type())):
                errs.append('VALIDMAX ({}) outside data range ({},{}) for {}.'.format(
                    raw_v.attrs['VALIDMAX'], get_min(raw_v.type()), get_max(raw_v.type()),
                    raw_v.name()))
        if ('VALIDMIN' in raw_v.attrs) and ('VALIDMAX' in raw_v.attrs):
            if raw_v.attrs['VALIDMIN'] > raw_v.attrs['VALIDMAX']:
                errs.append('VALIDMIM > VALIDMAX for {}'.format(v.name()))
        return errs

    
    @classmethod
    def validscale(cls, v):
        """Check that SCALEMIN<=SCALEMAX, and neither goes out 
        of range for CDF datatype.
        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        errs = []
        raw_v = v.cdf_file.raw_var(v.name())
        data = raw_v[...]
        if 'SCALEMIN' in raw_v.attrs:
            if (raw_v.attrs['SCALEMIN'] < get_min(raw_v.type())) or \
               (raw_v.attrs['SCALEMIN'] > get_max(raw_v.type())):
                errs.append('SCALEMIN ({}) outside data range ({},{}) for {}.'.format(
                    raw_v.attrs['SCALEMIN'], get_min(raw_v.type()), get_max(raw_v.type()),
                    raw_v.name()))
        if 'SCALEMAX' in raw_v.attrs:
            if (raw_v.attrs['SCALEMAX'] < get_min(raw_v.type())) or \
               (raw_v.attrs['SCALEMAX'] > get_max(raw_v.type())):
                errs.append('SCALEMAX ({}) outside data range ({},{}) for {}.'.format(
                    raw_v.attrs['SCALEMAX'], get_min(raw_v.type()), get_max(raw_v.type()),
                    raw_v.name()))
        if ('SCALEMIN' in raw_v.attrs) and ('SCALEMAX' in raw_v.attrs):
            if raw_v.attrs['SCALEMIN'] > raw_v.attrs['SCALEMAX']:
                errs.append('SCALEMIN > SCALEMAX for {}'.format(v.name()))
        return errs


    @classmethod
    def validplottype(cls, v):
        """Check that plottype matches dimensions.
        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        if sys.version_info >= (3,0):
            time_st = b'time_series'
            spec_st = b'spectrogram'
        else:
            time_st = 'time_series'
            spec_st = 'spectrogram'
        errs = []
        raw_v = v.cdf_file.raw_var(v.name())
        data = raw_v[...]
        if 'DISPLAY_TYPE' in raw_v.attrs:
            if (len(raw_v.shape) == 1) and (raw_v.attrs['DISPLAY_TYPE'] != time_st):
                errs.append('{}: 1 dim variable with {} display type.'.format(
                    raw_v.name(), raw_v.attrs['DISPLAY_TYPE']))
            elif (len(raw_v.shape) > 1) and (raw_v.attrs['DISPLAY_TYPE'] != spec_st):
                errs.append('{}: multi dim variable with {} display type.'.format(
                    raw_v.name(), raw_v.attrs['DISPLAY_TYPE']))
        return errs

    @classmethod
    def fieldnam(cls, v):
        """Check that FIELDNAM attribute matches variable name.

        :param v: Variable to check
        :type v: :class:`~spacepy.pycdf.Var`
        :returns: All error messages
        :rtype: list of str
        """
        errs = []
        vname = v.name()
        if 'FIELDNAM' not in v.attrs:
            errs.append('{}: no FIELDNAM attribute.'.format(vname))
        elif v.attrs['FIELDNAM'] != vname:
            errs.append('{}: FIELDNAM attribute {} does not match var name.'
                        .format(vname, v.attrs['FIELDNAM']))
        return errs


class FileChecks(object):
    """Tests of a file

    All tests return a list of errors (empty if none). :meth:`all`
    will check all tests and concatenate all errors. :meth:`all`
    should be updated with the list of all tests so that
    helper functions aren't called, subtests aren't called twice, etc.
    """

    @classmethod
    def all(cls, f):
        """Call all test functions, AND all test functions on all variables

        :param f: File to check
        :type f: :class:`~spacepy.pycdf.CDF`
        :returns: All error messages
        :rtype: list of str
        """
        callme = (cls.filename, cls.time_monoton, cls.times,)
        errors = []
        for func in callme:
            errors.extend(func(f))
        for v in f:
            errors.extend(('{}: {}'.format(v, e)
                           for e in VariableChecks.all(f[v])))
        return errors
                
    @classmethod
    def filename(cls, f):
        """Compare filename to global attributes

        Check that the logical_file_id matches the actual filename,
        and logical_source also matches.

        :param f: File to check
        :type f: :class:`~spacepy.pycdf.CDF`
        :returns: All error messages
        :rtype: list of str
        """
        errs = []
        for a in ('Logical_source', 'Logical_file_id'):
            if not a in f.attrs:
                errs.append('No {} in global attrs'.format(a))
        if errs:
            return errs
        fname = os.path.basename(f.pathname)
        if not bytes is str:
            fname = fname.decode('ascii')
        if not fname.startswith(f.attrs['Logical_source'][0]):
            errs.append("Logical_source {} doesn't match filename {}".format(
                f.attrs['Logical_source'][0], fname))
        if fname[:-4] != f.attrs['Logical_file_id'][0]:
            errs.append("Logical_file_id {} doesn't match filename {}".format(
                f.attrs['Logical_file_id'][0], fname))
        return errs

    @classmethod
    def time_monoton(cls, f):
        """Checks that times are monotonic

        Check that all Epoch variables are monotonically increasing.

        :param f: File to check
        :type f: :class:`~spacepy.pycdf.CDF`
        :returns: All error messages
        :rtype: list of str
        """
        errs = []
        for v in f:
            if not f[v].type() in (spacepy.pycdf.const.CDF_EPOCH.value,
                                   spacepy.pycdf.const.CDF_EPOCH16.value,
                                   spacepy.pycdf.const.CDF_TIME_TT2000.value):
                continue
            data = f[v][...]
            idx = numpy.where(numpy.diff(data) < datetime.timedelta(0))[0]
            if not any(idx):
                continue
            errs.append('{}: nonmonotonic time at record {}'.format(
                v, ', '.join((str(i) for i in (idx + 1)))))
        return errs

    @classmethod
    def times(cls, f):
        """Compare filename to times

        Check that all Epoch variables only contain times matching filename

        :param f: File to check
        :type f: :class:`~spacepy.pycdf.CDF`
        :returns: All error messages
        :rtype: list of str
        """
        errs = []
        fname = os.path.basename(f.pathname)
        if not bytes is str:
            fname = fname.decode('ascii')
        m = re.search('\d{8}', fname)
        if not m:
            return ['Cannot parse date from filename {}'.format(fname)]
        datestr = m.group(0)
        for v in f:
            if f[v].type() in (spacepy.pycdf.const.CDF_EPOCH.value,
                               spacepy.pycdf.const.CDF_EPOCH16.value,
                               spacepy.pycdf.const.CDF_TIME_TT2000.value):
                datestrs = list(set((d.strftime('%Y%m%d') for d in f[v][...])))
                if len(datestrs) == 0:
                    continue
                elif len(datestrs) > 1:
                    errs.append('{}: multiple days {}'.format(
                        v, ', '.join(sorted(datestrs))))
                elif datestrs[0] != datestr:
                    errs.append('{}: date {} doesn\'t match file {}'.format(
                        v, datestrs[0], fname))
        return errs


def fillval(v): 
    """Automatically set ISTP-compliant FILLVAL on a variable

    :param v: Variable to update
    :type v: :class:`~spacepy.pycdf.Var`
    """
    #Fill value, indexed by the CDF type (numeric)
    fillvals = {}
    #Integers
    for i in (1, 2, 4, 8):
        fillvals[getattr(spacepy.pycdf.const, 'CDF_INT{}'.format(i)).value] = \
            - 2 ** (8*i - 1)
        if i == 8:
            continue
        fillvals[getattr(spacepy.pycdf.const, 'CDF_UINT{}'.format(i)).value] = \
            2 ** (8*i) - 1
    fillvals[spacepy.pycdf.const.CDF_EPOCH16.value] = (-1e31, -1e31)
    fillvals[spacepy.pycdf.const.CDF_REAL8.value] = -1e31
    fillvals[spacepy.pycdf.const.CDF_REAL4.value] = -1e31
    fillvals[spacepy.pycdf.const.CDF_CHAR.value] = ' '
    fillvals[spacepy.pycdf.const.CDF_UCHAR.value] = ' '
    #Equivalent pairs
    for cdf_t, equiv in (
            (spacepy.pycdf.const.CDF_TIME_TT2000, spacepy.pycdf.const.CDF_INT8),
            (spacepy.pycdf.const.CDF_EPOCH, spacepy.pycdf.const.CDF_REAL8),
            (spacepy.pycdf.const.CDF_BYTE, spacepy.pycdf.const.CDF_INT1),
            (spacepy.pycdf.const.CDF_FLOAT, spacepy.pycdf.const.CDF_REAL4),
            (spacepy.pycdf.const.CDF_DOUBLE, spacepy.pycdf.const.CDF_REAL8),
    ):
        fillvals[cdf_t.value] = fillvals[equiv.value]
    if 'FILLVAL' in v.attrs:
        del v.attrs['FILLVAL']
    v.attrs.new('FILLVAL', data=fillvals[v.type()], type=v.type())


def format(v, use_scaleminmax=False, dryrun=False):
    """Automatically set ISTP-compliant FORMAT on a variable

    :param v: Variable to update
    :type v: :class:`~spacepy.pycdf.Var`
    :param bool use_scaleminmax: Use SCALEMIN/MAX instead of VALIDMIN/MAX.
                                 Note: istpchecks may complain about result.
    :param bool dryrun: Print the decided format to stdout instead of modifying
                        the CDF.  (For use in command-line debugging.)
    """
    if use_scaleminmax:
        minn = 'SCALEMIN'
        maxx = 'SCALEMAX'
    else:
        minn = 'VALIDMIN'
        maxx = 'VALIDMAX'
    cdftype = v.type()
    if cdftype in (spacepy.pycdf.const.CDF_INT1.value,
                   spacepy.pycdf.const.CDF_INT2.value,
                   spacepy.pycdf.const.CDF_INT4.value,
                   spacepy.pycdf.const.CDF_INT8.value,
                   spacepy.pycdf.const.CDF_UINT1.value,
                   spacepy.pycdf.const.CDF_UINT2.value,
                   spacepy.pycdf.const.CDF_UINT4.value,
                   spacepy.pycdf.const.CDF_BYTE.value):
        if minn in v.attrs: #Just use validmin or scalemin
            minval = v.attrs[minn]
        elif cdftype in (spacepy.pycdf.const.CDF_UINT1.value,
                         spacepy.pycdf.const.CDF_UINT2.value,
                         spacepy.pycdf.const.CDF_UINT4.value): #unsigned, easy
            minval = 0
        elif cdftype == spacepy.pycdf.const.CDF_BYTE.value:
            minval = - 2 ** 7
        else: #Signed, harder
            size = next((i for i in (1, 2, 4, 8) if getattr(
                spacepy.pycdf.const, 'CDF_INT{}'.format(i)).value == cdftype))
            minval = - 2 ** (8*size  - 1)
        if maxx in v.attrs: #Just use max
            maxval = v.attrs[maxx]
        elif cdftype == spacepy.pycdf.const.CDF_BYTE.value:
            maxval = 2 ** 7 - 1
        else:
            size = next((8 * i for i in (1, 2, 4) if getattr(
                spacepy.pycdf.const, 'CDF_UINT{}'.format(i)).value == cdftype),
                        None)
            if size is None:
                size = next((8 * i for i in (1, 2, 4, 8) if getattr(
                    spacepy.pycdf.const, 'CDF_INT{}'.format(i)).value ==
                             cdftype)) - 1
            maxval = 2 ** size - 1
        #Two tricks:
        #-Truncate and add 1 rather than ceil so get
        #powers of 10 (log10(10) = 1 but needs two digits)
        #-Make sure not taking log of zero
        if minval < 0: #Need an extra space for the negative sign
            fmt = 'I{}'.format(int(math.log10(max(
                abs(maxval), abs(minval), 1))) + 2)
        else:
            fmt = 'I{}'.format(int(
                math.log10(maxval) if maxval != 0 else 1) + 1)
    elif cdftype == spacepy.pycdf.const.CDF_TIME_TT2000.value:
        fmt = 'A{}'.format(len('9999-12-31T23:59:59.999999999'))
    elif cdftype == spacepy.pycdf.const.CDF_EPOCH16.value:
        fmt = 'A{}'.format(len('31-Dec-9999 23:59:59.999.999.000.000'))
    elif cdftype == spacepy.pycdf.const.CDF_EPOCH.value:
        fmt = 'A{}'.format(len('31-Dec-9999 23:59:59.999'))
    elif cdftype in (spacepy.pycdf.const.CDF_REAL8.value,
                     spacepy.pycdf.const.CDF_REAL4.value,
                     spacepy.pycdf.const.CDF_FLOAT.value,
                     spacepy.pycdf.const.CDF_DOUBLE.value):
        # Prioritize SCALEMIN/MAX to find the number of decimals to include
        if 'SCALEMIN' in v.attrs and 'SCALEMAX' in v.attrs:
            range = v.attrs['SCALEMAX'] - v.attrs['SCALEMIN']
        # If not, use VALIDMIN/MAX
        elif 'VALIDMIN' in v.attrs and 'VALIDMAX' in v.attrs:
            range = v.attrs['VALIDMAX'] - v.attrs['VALIDMIN']
        # If not, just use nothing.
        else:
            range = None
        # Find how many spaces we need for the 'integer' part of the number
        # (Use maxx-minn for this...effectively uses VALIDMIN/MAX for most
        # cases.)
        if range and (minn in v.attrs and maxx in v.attrs):
            if len(str(int(v.attrs[maxx]))) >=\
               len(str(int(v.attrs[minn]))):
                ln = str(int(v.attrs[maxx]))
            else:
                ln = str(int(v.attrs[minn]))
        if range and ln and range < 0: # Cover all our bases:
            # raise ValueError('Range ({} - {}) cannot be negative:'
                # '\nVarname: {}\nRange: {}'.format(maxx, minn, v, range))
            ### Instead of throwing an error, just use None
            # There are old cases that for some reason have negative ranges, so
            # this is really more of a compatibility choice than a good
            # decision.
            range = None
        # All of the lengths below (the +4, +3, +2, etc...) should be EXACTLY
        # enough.  Consider adding 1, (4+1=5, 3+1=4, etc...) to possibly make
        # this easier.
        # elif range and ln and range <= 11: # If range <= 11, we want 2 decimal places:
        if range and ln and range <= 11: # If range <= 11, we want 2 decimal places:
            # Need extra for '.', and 3 decimal places (4 extra)
            fmt = 'F{}.3'.format(len([i for i in ln]) + 4)
        elif range and ln and 11 < range <= 101:
            # Need extra for '.' (1 extra)
            fmt = 'F{}.2'.format(len([i for i in ln]) + 3)
        elif range and ln and 101 < range <= 1000:
            # Need extra for '.' (1 extra)
            fmt = 'F{}.1'.format(len([i for i in ln]) + 2)
        else:
            # No range, must not be populated, copied from REAL4/8(s) above
            # OR we don't care because it's a 'big' number:
            fmt = 'G10.2E3'
    elif cdftype in (spacepy.pycdf.const.CDF_CHAR.value,
                     spacepy.pycdf.const.CDF_UCHAR.value):
        #This is a bit weird but pycdf consider nelems private. Should change.
        fmt = 'A{}'.format(v._nelems())
    else:
        raise ValueError("Couldn't find FORMAT for {} of type {}".format(
            v.name(),
            spacepy.pycdf.lib.cdftypenames.get(cdftype, 'UNKNOWN')))
    if dryrun:
        print(fmt)
    else:
        if 'FORMAT' in v.attrs:
            del v.attrs['FORMAT']
        v.attrs.new('FORMAT', data=fmt, type=spacepy.pycdf.const.CDF_CHAR)


def get_max(type): 
    """Find maximum possible value based on datatype.

    :param int type: Type number
    :return: Maximum valid number
    :rtype: int
    """

    if(type == spacepy.pycdf.const.CDF_BYTE.value) or \
      (type == spacepy.pycdf.const.CDF_INT1.value) or \
      (type == spacepy.pycdf.const.CDF_CHAR.value):
        return 127
    elif(type == spacepy.pycdf.const.CDF_UINT1.value) or \
        (type == spacepy.pycdf.const.CDF_UCHAR.value):
        return 255
    elif(type == spacepy.pycdf.const.CDF_INT2.value):
        return 32767
    elif(type == spacepy.pycdf.const.CDF_UINT2.value):
        return 65535
    elif(type == spacepy.pycdf.const.CDF_INT4.value):
        return 2147483647
    elif(type == spacepy.pycdf.const.CDF_UINT4.value):
        return 4294967295
    elif(type == spacepy.pycdf.const.CDF_INT8.value):
        return 9223372036854775807
    elif(type == spacepy.pycdf.const.CDF_REAL4.value) or \
        (type == spacepy.pycdf.const.CDF_FLOAT.value): # from https://www.ias.ac.in/article/fulltext/reso/021/01/0011-0030
        return 3.4E38
    elif(type == spacepy.pycdf.const.CDF_REAL8.value) or \
        (type == spacepy.pycdf.const.CDF_DOUBLE.value) or \
        (type == spacepy.pycdf.const.CDF_EPOCH.value):
        return 1E4932
    elif(type == spacepy.pycdf.const.CDF_TIME_TT2000.value):
        return 9223372036854775807
    else:
        raise ValueError('Unknown data type: {}'.format(type))

    
def get_min(type): 
    """Find minimum possible value based on datatype.

    :param int type: Type number
    :return: Minimum valid number
    :rtype: int
    """

    if(type == spacepy.pycdf.const.CDF_BYTE.value) or \
      (type == spacepy.pycdf.const.CDF_INT1.value) or \
      (type == spacepy.pycdf.const.CDF_CHAR.value):
        return -128
    elif(type == spacepy.pycdf.const.CDF_UINT1.value) or \
        (type == spacepy.pycdf.const.CDF_UINT2.value) or \
        (type == spacepy.pycdf.const.CDF_UINT4.value) or \
        (type == spacepy.pycdf.const.CDF_UCHAR.value):
        return 0
    elif(type == spacepy.pycdf.const.CDF_INT2.value):
        return -32768
    elif(type == spacepy.pycdf.const.CDF_INT4.value):
        return -2147483648
    elif(type == spacepy.pycdf.const.CDF_INT8.value):
        return -9223372036854775808
    elif(type == spacepy.pycdf.const.CDF_REAL4.value) or \
        (type == spacepy.pycdf.const.CDF_FLOAT.value): # from https://www.ias.ac.in/article/fulltext/reso/021/01/0011-0030
        return -3.4E38
    elif(type == spacepy.pycdf.const.CDF_REAL8.value) or \
        (type == spacepy.pycdf.const.CDF_DOUBLE.value) or \
        (type == spacepy.pycdf.const.CDF_EPOCH.value):        
        return -1E4932
    elif(type == spacepy.pycdf.const.CDF_TIME_TT2000.value):
        return -9223372036854775808
    else:
        raise ValueError('Unknown data type: {}'.format(type))    