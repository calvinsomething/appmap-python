import inspect
import threading
from functools import partial
from itertools import chain

from . import utils

class _EventIds:
    id = 1
    lock = threading.Lock()

    @classmethod
    def next_id(cls):
        with cls.lock:
            cls.id += 1
            return cls.id

    # The identifiers returned by threading.get_ident() aren't
    # guaranteed to be unique: they may be recycled after the thread
    # exits. We need a unique id, so we'll manage it ourselves.
    next_thread_id = 0
    next_thread_id_lock = threading.Lock()
    tls = threading.local()

    @classmethod
    def get_thread_id(cls):
        thread_id = getattr(cls.tls, 'thread_id', None)
        if thread_id is None:
            with cls.next_thread_id_lock:
                cls.next_thread_id += 1
                thread_id = cls.tls.thread_id = cls.next_thread_id
        return thread_id


class Event:
    __slots__ = ['id', 'event', 'thread_id']

    def __init__(self, event):
        self.id = _EventIds.next_id()
        self.event = event
        self.thread_id = _EventIds.get_thread_id()

    def to_dict(self):
        return {
            k: getattr(self, k, None)
            for k in chain.from_iterable(getattr(cls, '__slots__', [])
                                         for cls in type(self).__mro__)
        }



class CallEvent(Event):
    __slots__ = ['defined_class', 'method_id', 'path', 'lineno',
                 'static', 'receiver', 'parameters']

    @staticmethod
    def make(fn, isstatic):
        """
        Return a factory for creating new CallEvents based on
        introspecting the given function.
        """
        defined_class, method_id = utils.split_function_name(fn)
        path = inspect.getsourcefile(fn)
        __, lineno = inspect.getsourcelines(fn)
        return partial(CallEvent, defined_class,
                       method_id, path, lineno, isstatic)

    @staticmethod
    def make_receiver(fn, isstatic):
        """
        Create the receiver object that should be part of the call
        event for the given function.
        """
        defined_class, __ = utils.split_function_name(fn)
        if isstatic:
            cls = "class"
            value = defined_class
            object_id = id(defined_class)
        else:
            cls = defined_class
            value = None
            object_id = None

        def make(cls, value, object_id, *args):
            if not isstatic:
                object_id = id(args[0][0])
                slf = args[0][0]
                # Make a best-effort attempt to get a string value for
                # the receiver. If str() and repr() raise, formulate a
                # value from the class and id.
                try:
                    value = str(slf)
                except Exception:  # pylint: disable=broad-except
                    try:
                        value = repr(slf)
                    except Exception:  # pylint: disable=broad-except
                        value = f'<{defined_class} object at {object_id:#02x}>'
            return {
                "class": cls,
                "value": value,
                "object_id": object_id
            }
        return partial(make, cls, value, object_id)

    def __init__(self, defined_class, method_id, path, lineno,
                 static, receiver, parameters):
        super().__init__('call')
        self.defined_class = defined_class
        self.method_id = method_id
        self.path = path
        self.lineno = lineno
        self.static = static
        self.receiver = receiver
        self.parameters = parameters


class ReturnEvent(Event):
    __slots__ = ['parent_id']

    def __init__(self, parent_id):
        super().__init__('return')
        self.parent_id = parent_id


class ExceptionEvent(ReturnEvent):
    __slots__ = ['exceptions']

    def __init__(self, parent_id, exc_info):
        super().__init__(parent_id)
        class_, exc, __ = exc_info
        self.exceptions = [{
            'exceptions': {
                'class': f'{class_.__module__}.{class_.__qualname__}',
                'message': str(exc),
                'object_id': id(exc),
            }
        }]


def serialize_event(event):
    if isinstance(event, Event):
        return event.to_dict()
    raise TypeError