import logging
import inspect
from functools import wraps

from framework import sentry

logger = logging.getLogger(__name__)

# statuses
FAILED = 'failed'
CREATED = 'created'
STARTED = 'started'
COMPLETED = 'completed'


# Use _index here as to not clutter the namespace for kwargs
def dispatch(_event, status, _index=None, **kwargs):
    if _index:
        _event = f'{_event}.{_index}'

    logger.debug(f'[{_event}][{status}]{kwargs!r}')


def logged(event, index=None):
    def _logged(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            context = extract_context(func, *args, **kwargs)
            dispatch(event, STARTED, _index=index, **context)
            try:
                res = func(*args, **kwargs)
            except Exception as e:
                sentry.log_exception(e)
                dispatch(event, FAILED, _index=index, exception=e, **context)
                raise
            else:
                dispatch(event, COMPLETED, _index=index, **context)
            return res
        return wrapped
    return _logged


def extract_context(func, *args, **kwargs):
    arginfo = inspect.getfullargspec(func)
    arg_names = arginfo.args
    defaults = {
        arg_names.pop(-1): kwarg
        for kwarg in (arginfo.defaults or [])
    }

    computed_args = zip(arg_names, args)
    if arginfo.varargs:
        computed_args.append(('args', list(args[len(arg_names):])))

    if kwargs:
        defaults['kwargs'] = kwargs

    return dict(computed_args, **defaults)
