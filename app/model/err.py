class YACError(Exception):
    code = 500
    title = "Internal Server Error"
    default_message = (
        "Please try again later or contact your administrator if the error does not"
        " disappear."
    )


#
# Specs
#


class SpecsError(YACError):
    code = 500
    title = "Error in Specification"


class ActionSpecsError(SpecsError):
    pass  # Error in Action Specs


class LogSpecsError(SpecsError):
    pass  # Error in Log Specs


class RepoSpecsError(SpecsError):
    pass  # Error in Repo Specs


class SchemaSpecsError(SpecsError):
    pass  # Error in Schema Specs


#
# Plugins
#


class PluginError(YACError):
    code = 500
    title = "Error in Plugin"


class ActionError(YACError):
    code = 500
    title = "Action could not be executed"


class LogError(YACError):
    code = 500
    title = "Logs could not be loaded"


class ActionClientError(ActionError):
    code = 400


class RequestError(YACError):
    code = 400
    title = "Not Allowed"


class RequestConflict(RequestError):
    code = 409
    title = "Conflict"


class RequestForbidden(RequestError):
    code = 403
    title = "Forbidden"


class RequestNotFound(RequestError):
    code = 404
    title = "Not Found"


class RepoError(YACError):
    code = 500
    title = "Accessing Data Repository failed"


class RepoTimeoutError(RepoError):
    title = "Data Repository did not answer timely"


class RepoUnavailable(RepoError):
    """
    The remote data repository cannot be reached (network/DNS/connection
    failure or an HTTP 5xx from the git server) and no local copy can stand
    in. Reads normally survive such outages by serving the last known state
    (see the repo plugins); this is raised when that is impossible or when
    the operation needs the remote (writes, first clone).
    """

    code = 503
    title = "Data Repository Unavailable"
    default_message = (
        "The data repository cannot be reached at the moment. Please try again"
        " later."
    )


class RepoMaintenance(RepoUnavailable):
    """
    The git server answered HTTP 503, which it does while in maintenance.
    """

    title = "Data Repository in Maintenance"
    default_message = (
        "The data repository is in maintenance. Please try again later."
    )


class RepoClientError(RepoError):
    code = 400
    title = "Not Allowed"


class RepoConflict(RepoClientError):
    code = 409
    title = "Conflict"


class RepoForbidden(RepoClientError):
    code = 403
    title = "Forbidden"


class RepoNotFound(RepoClientError):
    code = 404
    title = "Not Found"


#
# Others
#


class ServerError(YACError):
    code = 500
    title = "Server Error"


class AuthError(YACError):
    code = 401
    title = "Login Failed"


def http_responses() -> dict:
    result = {}
    for c in [400, 401, 403, 404, 409, 500, 503]:
        result.update(
            {
                c: {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "message": {"type": "string"},
                                },
                            }
                        }
                    }
                }
            }
        )
    return result
