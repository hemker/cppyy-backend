from __future__ import print_function
""" cppyy_backend loader
"""

__all__ = [
    'load_cpp_backend',           # load libcppyy_backend
    'set_cling_compile_options',  # set EXTRA_CLING_ARGS envar
    'ensure_precompiled_header'   # build precompiled header as necessary
]

import os, sys, ctypes, subprocess, sysconfig, warnings

if 'win32' in sys.platform:
    soext = '.dll'
else:
    soext = '.so'

soabi = sysconfig.get_config_var("SOABI")
soext2 = sysconfig.get_config_var("EXT_SUFFIX")
if not soext2:
    soext2 = sysconfig.get_config_var("SO")


def _load_helper(bkname):
    errors = set()

 # normal load, allowing for user overrides of LD_LIBRARY_PATH
    try:
        return ctypes.CDLL(bkname, ctypes.RTLD_GLOBAL)
    except OSError as e:
        errors.add(str(e))

 # failed ... load dependencies explicitly
    try:
        pkgpath = os.path.dirname(bkname)
        if not pkgpath:
            pkgpath = os.path.dirname(__file__)
        elif os.path.basename(pkgpath) in ['lib', 'bin']:
            pkgpath = os.path.dirname(pkgpath)
        for dep in ['libCoreLegacy', 'libThreadLegacy', 'libRIOLegacy', 'libCling']:
            for loc in ['lib', 'bin']:
                fpath = os.path.join(pkgpath, loc, dep+soext)
                if os.path.exists(fpath):
                    ldtype = ctypes.RTLD_GLOBAL
                    if dep == 'libCling': ldtype = ctypes.RTLD_LOCAL
                    ctypes.CDLL(fpath, ldtype)
                    break
        return ctypes.CDLL(os.path.join(pkgpath, 'lib', bkname), ctypes.RTLD_GLOBAL), errors
    except OSError as e:
        errors.add(str(e))

    return None, errors


_precompiled_header_ensured = False
def load_cpp_backend():
    set_cling_compile_options()

    if not _precompiled_header_ensured:
     # the precompiled header of standard and system headers is not part of the
     # distribution as there are too many varieties; create it now if needed
        ensure_precompiled_header()

    names = list()
    try:
        bkname = os.environ['CPPYY_BACKEND_LIBRARY']
        if bkname.rfind(soext) < 0:
            bkname += soext
        names.append(bkname)
    except KeyError:
        names.append('libcppyy_backend'+soext)
        if soabi:
            names.append('libcppyy_backend.'+soabi+soext)
        if soext2:
            names.append('libcppyy_backend'+soext2)

    err = set()
    for name in names:
        c, err2 = _load_helper(name)
        if c:
            break
        err = err.union(err2)

    if not c:
        raise RuntimeError("could not load cppyy_backend library, details:\n%s" %
            '\n'.join(['  '+x for x in err]))

    return c


def set_cling_compile_options(add_defaults = False):
 # extra optimization flags for Cling
    if not 'EXTRA_CLING_ARGS' in os.environ:
        CURRENT_ARGS = ''
        add_defaults = True
    else:
        CURRENT_ARGS = os.environ['EXTRA_CLING_ARGS']

    if add_defaults:
        has_avx = False
        try:
            with open('/proc/cpuinfo', 'r') as ci:
                for line in ci:
                    if 'avx' in line:
                        has_avx = True
                        break
        except Exception:
            try:
                cli_arg = subprocess.check_output(['sysctl', 'machdep.cpu.features'])
                has_avx = 'avx' in cli_arg.decode("utf-8").strip().lower()
            except Exception:
                pass
        CURRENT_ARGS += ' -O2'
        if has_avx: CURRENT_ARGS += ' -mavx'

      # py2.7 uses the register storage class, which is no longer allowed with C++17
        if sys.hexversion < 0x3000000:
            CURRENT_ARGS += ' -Wno-register'

        os.putenv('EXTRA_CLING_ARGS', CURRENT_ARGS)
        os.environ['EXTRA_CLING_ARGS'] = CURRENT_ARGS


def _disable_pch():
    os.putenv('CLING_STANDARD_PCH', 'none')

def _warn_no_pch(msg, pchname=None):
    if pchname is None or not os.path.exists(pchname):
        _disable_pch()
        warnings.warn('No precompiled header available (%s); this may impact performance.' % msg)
    else:
        warnings.warn('Precompiled header may be out of date (%s).' % msg)

def _is_uptodate(pchname, incpath):
  # test whether the pch is older than the include directory as a crude way
  # to find if it's up to date; if however, this is a frozen bundle, then
  # it's unlikely there's a C++ compiler available, so accept the PCH if any
    if getattr(sys, 'frozen', False):
      # okay if PCH is available, no point to rebuild if include dir is not
        return os.path.exists(pchname) or not os.path.exists(incpath)

    try:
        return os.stat(pchname).st_mtime >= os.stat(incpath).st_mtime
    except Exception:
        if not os.path.exists(incpath):
            return True     # no point in updating as it will fail
    return False

def ensure_precompiled_header(pchdir = '', pchname = ''):
  # the precompiled header of standard and system headers is not part of the
  # distribution as there are too many varieties; create it now if needed
     global _precompiled_header_ensured
     _precompiled_header_ensured = True     # only ever call once, even if failed

     olddir = os.getcwd()
     try:
         pkgpath = os.path.abspath(os.path.dirname(__file__))
         if 'CLING_STANDARD_PCH' in os.environ:
             pchname = os.environ['CLING_STANDARD_PCH']
             if pchname.lower() == 'none':  # magic keyword to disable pch
                 _disable_pch()
                 os.chdir(olddir)
                 return                     # quiet
             pchdir = os.path.dirname(pchname)
         else:
             if not pchdir:
                 pchdir = os.path.join(pkgpath, 'etc')
             if not pchname:
                 from ._version import __version__
                 pchname = 'allDict.cxx.pch.'+str(__version__)
             pchname = os.path.join(pchdir, pchname)
             os.environ['CLING_STANDARD_PCH'] = pchname

         os.chdir(pkgpath)
         incpath = os.path.join(pkgpath, 'include')
         pch_exists = os.path.exists(pchname)
         if not pch_exists or not _is_uptodate(pchname, incpath):
             if os.access(pchdir, os.R_OK|os.W_OK):
                 print('(Re-)building pre-compiled headers (options:%s); this may take a minute ...' % os.environ.get('EXTRA_CLING_ARGS', ' none'))
                 makepch = os.path.join(pkgpath, 'etc', 'dictpch', 'makepch.py')
                 pyexe = sys.executable
                 if getattr(sys, 'frozen', False) or not ('python' in pyexe.lower() or 'pypy' in pyexe.lower()):
                   # either frozen, or a high chance of being embedded; and the actual version
                   # of python used doesn't matter per se, as long as it is functional
                     pyexe = 'python'
                 if subprocess.call([pyexe, makepch, pchname, '-I'+incpath]) != 0:
                     _warn_no_pch('failed to build', pchname)
             elif not pch_exists:
               # accept that the file may be out of date; since the location is not writable,
               # the most likely cause is that it is managed by some packager, which in that
               # case is responsible for the PCH, so only warn if it doesn't exist
                 _warn_no_pch('%s not writable, set CLING_STANDARD_PCH' % pchdir, pchname)

     except Exception as e:
         _warn_no_pch(str(e))
     finally:
         os.chdir(olddir)
