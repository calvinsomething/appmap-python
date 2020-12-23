"""Capture recordings of python code"""

import inspect
import logging
import sys

from abc import ABC, abstractmethod
from functools import wraps

from . import env
from . import utils

logger = logging.getLogger(__name__)


class Recording:
    def __init__(self):
        self.events = []

    def start(self):

        if not env.enabled():
            return

        Recorder().start_recording()

    def stop(self):
        if not env.enabled():
            return False

        self.events = Recorder().stop_recording()

    def __enter__(self):
        self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False


class Filter(ABC):
    def __init__(self, next_filter):
        self.next_filter = next_filter

    @abstractmethod
    def filter(self, class_):
        """
        Determine whether the given class should have its methods
        instrumented.  Return True if it should be, False if it should
        not be, or call the next filter if this filter can't decide.
        """

    @abstractmethod
    def wrap(self, fn, isstatic):
        """
        Determine whether the given method should be instrumented.  If
        so, return a new function that wraps the old.  If not, return
        the original function.  isstatic is True if fn is a static or
        class method, False otherwise.
        """


class NullFilter(Filter):
    def filter(self, class_):
        return False

    def wrap(self, fn, isstatic):
        return fn


def get_classes(module):
    return [v for __, v in module.__dict__.items() if inspect.isclass(v)]


def get_members(class_):
    """
    Get the function members of the given class.  Unlike
    inspect.getmembers, this function only calls getattr on functions,
    to avoid potential side effects.

    Returns a list of tuples of the form (key, dict_value, attr_value):
      * key is the attribute name
      * dict_value is class_.__dict__[key]
      * and attr_value is getattr(class_, key)
    """
    def is_member_func(m):
        return (inspect.isfunction(m)
                or inspect.ismethod(m)
                or utils.is_staticmethod(m)
                or utils.is_classmethod(m))

    ret = []
    for key in dir(class_):
        if key.startswith('__'):
            continue
        value = inspect.getattr_static(class_, key)
        if not is_member_func(value):
            continue
        ret.append((key, value))
    return ret


class Recorder:
    """ Singleton Recorder class """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            logger.debug('Creating the Recorder object')
            cls._instance = super(Recorder, cls).__new__(cls)

            # Put any __init__ here.
            cls._instance._initialized = False

        return cls._instance

    def __init__(self):
        if self.__getattribute__('_initialized'):  # keep pylint happy
            return
        self._initialized = True
        self.enabled = False
        self.filter_stack = [NullFilter]
        self.filter_chain = []
        self._events = []

    @classmethod
    def initialize(cls):
        cls._instance = None

    def use_filter(self, filter_class):
        self.filter_stack.append(filter_class)

    def start_recording(self):
        logger.info('AppMap recording started')
        self.enabled = True

    def stop_recording(self):
        logger.info('AppMap recording stopped')
        self.enabled = False
        return self._events

    def add_event(self, event):
        """
        Add an event to the current recording
        """
        self._events.append(event)

    def events(self):
        """
        Get the events from the current recording
        """
        return self._events

    def do_import(self, *args, **kwargs):
        mod = args[0]
        logger.debug('do_import, args %s kwargs %s', args, kwargs)
        if not self.filter_chain:
            logger.debug('  filter_stack %s', self.filter_stack)
            self.filter_chain = self.filter_stack[0](None)
            for filter_ in self.filter_stack[1:]:
                self.filter_chain = filter_(self.filter_chain)
                logger.debug('  self.filter chain: %s', self.filter_chain)

        if mod.__name__.startswith('appmap'):
            return

        classes = get_classes(mod)
        logger.debug(('  classes %s'
                      ' inspect.getmembers(mod, inspect.isclass) %s'),
                     classes,
                     inspect.getmembers(mod, inspect.isclass))
        for class_ in classes:
            if not self.filter_chain.filter(class_):
                continue

            logger.debug('  looking for members')
            functions = get_members(class_)
            logger.debug('  functions %s', functions)
            for fn_name, fn in functions:
                isstatic = utils.is_staticmethod(fn)
                isclass = utils.is_classmethod(fn)

                if isstatic or isclass:
                    new_fn = self.filter_chain.wrap(fn.__func__, isstatic=True)
                else:
                    new_fn = self.filter_chain.wrap(fn, isstatic=False)

                if new_fn != fn:
                    if isstatic:
                        new_fn = staticmethod(new_fn)
                    elif isclass:
                        new_fn = classmethod(new_fn)
                    setattr(class_, fn_name, new_fn)


def wrap_exec_module(exec_module):
    @wraps(exec_module)
    def wrapped_exec_module(*args, **kwargs):
        logger.debug(('exec_module %s'
                      ' exec_module.__name__ %s'
                      ' args %s'
                      ' kwargs %s'),
                     exec_module,
                     exec_module.__name__,
                     args,
                     kwargs)
        exec_module(*args, **kwargs)
        Recorder().do_import(*args, **kwargs)
    return wrapped_exec_module


def wrap_find_spec(find_spec):
    @wraps(find_spec)
    def wrapped_find_spec(*args, **kwargs):
        spec = find_spec(*args, **kwargs)
        if spec is not None:
            if getattr(spec.loader, "exec_module", None) is not None:
                loader = spec.loader
                logger.debug("wrap_find_spec, before loader.exec_module %s",
                             loader.exec_module)
                loader.exec_module = wrap_exec_module(loader.exec_module)
                logger.debug("  after loader.exec_module %s",
                             loader.exec_module)
            else:
                logger.warning("%s doesn't have exec_module", spec.loader)
        return spec
    return wrapped_find_spec


def initialize():
    Recorder.initialize()
    if env.enabled():
        for h in sys.meta_path:
            if getattr(h, 'find_spec', None) is not None:
                logger.debug('  h.find_spec %s',  h.find_spec)
                h.find_spec = wrap_find_spec(h.find_spec)