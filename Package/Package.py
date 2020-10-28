#!/usr/bin/env python3
#
# Package.py
#
# This package represents a "Package" in Jamf (a macOS installer package bundle that's been uploaded to Jamf)


class Package:
    """A package. This exists merely to carry the variables"""

    # the application part of the package name matching the test policy
    package = ""
    patch = ""  # name of the patch definition
    name = ""  # full name of the package '<package>-<version>.pkg'
    version = ""  # the version of our package
    idn = ""  # id of the package in our JP server
    test_weekdays = ""  # allowed weekdays for deployment to test
    test_not_before = ""  # earliest time for deployment to test
    test_not_after = ""  # latest time for deployment to test
    min_days_until_prod = ""  # minimum days before move to production
    prod_weekdays = ""  # allowed weekdays for move to production
    prod_not_before = ""  # earliest time for move to production
    prod_not_after = ""  # latest time for move to production
