import pickle
import hashlib
import contextvars
import functools
import inspect

from functools import wraps
from async_lru import alru_cache


def pickled_alru_cache(**alru_kwargs):
    def decorator(func):
        """
        A decorator that uses alru_cache under the hood but serializes
        both the function arguments and return values via pickle.
        This allows caching calls to async functions that accept or return
        non-hashable objects.
        """

        @alru_cache(**alru_kwargs)
        async def internal(key, pickled_args):
            # Deserialize the original args, kwargs
            args, kwargs = pickle.loads(pickled_args)
            # Call the original function
            result = await func(*args, **kwargs)
            # Return the pickled result so that it can be properly cached
            return pickle.dumps(result)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Pickle the arguments (positional + keyword)
            pickled_args = pickle.dumps((args, kwargs))

            # Create a hash key from the pickled arguments
            # This string key ensures it is hashable for the cache dictionary
            key = hashlib.md5(pickled_args).hexdigest()

            # Fetch or compute the pickled result from the internal cached function
            pickled_result = await internal(key, pickled_args)
            # Unpickle the actual return value before returning
            return pickle.loads(pickled_result)

        return wrapper

    return decorator


# A context variable for storing bound arguments during each function call.
# Because each asyncio Task has its own context, simultaneous calls
# can safely store different bound arguments without interfering.
_current_bound_args = contextvars.ContextVar("_current_bound_args")


def partial_alru_cache(*argument_names, **alru_kwargs):
    """
    Decorator factory that returns a decorator which caches the result of
    the decorated async function using only the values of the specified
    argument_names as the cache key—ignoring all other (possibly unhashable) arguments.
    """

    def decorator(func):
        signature = inspect.signature(func)

        @alru_cache(**alru_kwargs)
        async def internal(keys_tuple):
            """
            Internal function seen by alru_cache, receiving only a tuple of key values.
            It retrieves the full set of bound arguments from the ContextVar for
            this coroutine call, then calls the actual 'func'.
            """
            del keys_tuple
            bound_args = _current_bound_args.get()
            return await func(*bound_args.args, **bound_args.kwargs)

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            bound_args = signature.bind(*args, **kwargs)
            bound_args.apply_defaults()

            key_tuple = tuple(
                bound_args.arguments[arg_name] for arg_name in argument_names
            )

            token = _current_bound_args.set(bound_args)
            try:
                return await internal(key_tuple)
            finally:
                _current_bound_args.reset(token)

        return wrapper

    return decorator
