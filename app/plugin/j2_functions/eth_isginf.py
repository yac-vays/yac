"""
Some convenient functions specifically written for ETH D-INFK ISG use-cases.

Environment variables:

  YAC_ISGINF_LDAPSEARCH_URL:     Search base URL
                                 default: '' -> required!
                                 example: 'ldaps://ldap.example.com/'
  YAC_ISGINF_LDAPSEARCH_BIND_DN: Bind user
                                 default: '' -> required!
                                 example: 'cn=user,ou=admins,o=example,c=com'
  YAC_ISGINF_LDAPSEARCH_BIND_PW: Bind password
                                 default: '' -> required!
"""

import ldap
import logging
import os
import re

logger = logging.getLogger(__name__)


def isginf_ldap_search(
    dn: str,
    filterstr: str = "(cn=*)",
    attr: str = "cn",
    scope: str = "subtree",
    cert_check: bool = False,
) -> list[str]:
    # pylint: disable=no-member
    scopes = {
        "base": ldap.SCOPE_BASE,
        "onelevel": ldap.SCOPE_ONELEVEL,
        "subordinate": ldap.SCOPE_SUBORDINATE,
        "subtree": ldap.SCOPE_SUBTREE,
    }

    if not cert_check:
        ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)

    c = ldap.initialize(os.environ.get("YAC_ISGINF_LDAPSEARCH_URL"))
    c.simple_bind_s(
        os.environ.get("YAC_ISGINF_LDAPSEARCH_BIND_DN"),
        os.environ.get("YAC_ISGINF_LDAPSEARCH_BIND_PW"),
    )

    try:
        results = c.search_s(dn, scopes[scope], filterstr=filterstr, attrlist=[attr])
    except ldap.NO_SUCH_OBJECT:
        results = []
    except ldap.INVALID_DN_SYNTAX:
        logger.warning(f"Invalid DN syntax in isginf_ldap_search: {dn}")
        results = []

    if attr == "dn":
        return [result[0] for result in results]

    return [result[1][attr][0].decode("utf-8") for result in results]


def isginf_user_in_ou(user: str, ou: str) -> bool:
    return (
        len(
            isginf_ldap_search(
                f"ou=users,ou={ou},ou=inf,ou=auth,o=ethz,c=ch", f"(cn={user})"
            )
        )
        > 0
    )


def isginf_user_itc_in_ou(user: str, ou: str) -> bool:
    return (
        len(
            isginf_ldap_search(
                f"ou=users,ou={ou},ou=inf,ou=auth,o=ethz,c=ch",
                f"(&(description=ik=1)(cn={user}))",
            )
        )
        > 0
    )


def isginf_get_user_ous(user: str) -> list[str]:
    pattern = re.compile(f"^cn={user},ou=users,ou=([^,]+),ou=inf,ou=auth,o=ethz,c=ch$")
    ous = []
    for dn in isginf_ldap_search("ou=inf,ou=auth,o=ethz,c=ch", f"(cn={user})", "dn"):
        if pattern.match(dn):
            ous.append(pattern.sub(r"\1", dn))
    return sorted(ous)


def isginf_get_ou_users(ou: str, subou: str = "all") -> list[str]:
    if subou == "all":
        return isginf_ldap_search(f"ou=users,ou={ou},ou=inf,ou=auth,o=ethz,c=ch")
    return isginf_ldap_search(f"ou=users,ou={subou},ou={ou},ou=inf,ou=auth,o=ethz,c=ch")
