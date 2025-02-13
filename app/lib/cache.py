import pickle
import hashlib
from functools import wraps
from async_lru import alru_cache


# TODO use in more strategic good places!
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
